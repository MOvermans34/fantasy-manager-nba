"""
Engine: haalt alle league-data op, combineert die en ververst op de achtergrond.

Eén engine bedient de hele league; elk API-verzoek kiest een team (team_id).
"""
import datetime as dt
import threading
import time
import traceback

from .bbref import BBRef, opponent_factors
from .cats import INACTIVE_INJ, Projector, ValueModel, best_lineup, cat_meta, espn_current, side_totals
from .espn import (BENCH_SLOT_IDS, RAW, SLOT, STATS, AuthError, Espn, build_player, categories, et_date,
                   game_row, parse_scoreboard)
from .news import NewsStore, fetch_rotowire, match_rotowire, norm_name

DEFAULT_SLOTS = {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 11: 3, 12: 3, 13: 1}
WINDOW_KEYS = ("season", "l7", "l15", "l30", "proj", "last")
DAYS_NL = ["ma", "di", "wo", "do", "vr", "za", "zo"]
LOG_STATS = ("40", "0", "6", "3", "2", "1", "17", "18", "13", "14", "15", "16", "11", "4", "9")


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def rnd(d, n=2):
    return {k: round(v, n) for k, v in d.items()} if d else d


class Engine:
    def __init__(self, cfg, demo=False, period=None, data_dir="data", log=print):
        self.cfg = cfg
        self.demo = demo
        self.period_override = period
        self.log = log
        self.season = int(cfg["season"])
        self.league_id = None if demo else int(cfg["league_id"])
        self.default_team = int(cfg["team_id"]) if cfg.get("team_id") and not demo else 1
        self.espn = Espn(self.season, cfg.get("espn_s2"), cfg.get("swid"))
        json_path = f"{data_dir}/news.json"
        if cfg.get("database_url"):
            from .etl_news import PostgresNewsStore  # Polars/psycopg alleen laden als nodig
            self.news = PostgresNewsStore(cfg["database_url"], log=log, legacy_json=json_path)
        else:
            import os
            os.makedirs(data_dir, exist_ok=True)
            self.news = NewsStore(json_path)
        self.bbref = BBRef(self.season, data_dir, log=log)
        self._fac = {}
        self.lock = threading.RLock()
        self.heavy = threading.Lock()
        self.players = {}
        self.rosters = {}        # team_id -> [{id, slot}]
        self.pairs = {}          # team_id -> tegenstander
        self.matchup_ids = {}    # team_id -> (home, away) sleutel
        self.espn_scores = {}    # team_id -> ESPN-zijde van de matchup (cumulativeScore)
        self.fantasy_teams = {}
        self.meta = {}
        self.cats = []
        self.games = {}
        self.proj = {}           # frozenset({a,b}) -> projectie
        self.live_updated = None
        self.any_live = False
        self.events = []
        self._last_lines = {}
        self._news_seen = {}
        self.errors = []
        self.model = None

    # ------------------------------------------------------------------
    def _err(self, where, e, level="error"):
        msg = f"{where}: {e}"
        self.log(("FOUT " if level == "error" else "LET OP ") + msg)
        if level == "error":
            self.errors = (self.errors + [{"at": now_iso(), "msg": msg}])[-10:]

    def team_ok(self, tid):
        return tid in self.fantasy_teams

    def ctx(self, tid):
        opp = self.pairs.get(tid)
        return self.rosters.get(tid, []), self.rosters.get(opp, []) if opp else [], opp

    # ------------------------------------------------------------------
    # Speeldagen en matchup-periodes
    # ------------------------------------------------------------------
    def _sp_dates(self, pro):
        """scoring period -> ET-datum (één speeldag per scoring period)."""
        known = {}
        for t in pro.values():
            for sp, g in t["games"].items():
                known.setdefault(sp, et_date(g["date"]))
        if not known:
            return lambda sp: None
        sp0 = min(known)
        base = known[sp0] - dt.timedelta(days=sp0 - 1)
        return lambda sp: base + dt.timedelta(days=sp - 1)

    def _period_sps(self, sp, ld, date_of):
        """Speeldagen van de huidige matchup-periode (en de volgende)."""
        status = (ld or {}).get("status") or {}
        mp = status.get("currentMatchupPeriod")
        settings = (ld or {}).get("settings") or {}
        mps = ((settings.get("scheduleSettings") or {}).get("matchupPeriods")) or {}
        seen = {}
        for m in (ld or {}).get("schedule", []):
            keys = set()
            for side in ("home", "away"):
                keys |= {int(k) for k in ((m.get(side) or {}).get("pointsByScoringPeriod") or {})}
            if keys:
                seen.setdefault(m.get("matchupPeriodId"), set()).update(keys)
        start = None
        if mp and seen.get(mp):
            start = min(seen[mp])
        elif mp and seen.get(mp - 1):
            start = max(seen[mp - 1]) + 1
        if start is None or start > sp:
            # zonder league-info: week van maandag t/m zondag
            d = date_of(sp)
            start = sp - (d.weekday() if d else 0)
            start = max(1, start)
        weeks = len(mps.get(str(mp), [mp])) if mp else 1

        def end_from(s, n_weeks):
            e = s
            while True:
                d = date_of(e)
                if d is None or (d.weekday() == 6 and e - s >= 7 * (n_weeks - 1)):
                    return e
                e += 1
                if e - s > 7 * n_weeks + 7:
                    return e
        end = end_from(start, weeks)
        nxt_weeks = len(mps.get(str(mp + 1), [0])) if mp else 1
        nxt_end = end_from(end + 1, nxt_weeks)
        return mp, list(range(start, end + 1)), list(range(end + 1, nxt_end + 1))

    # ------------------------------------------------------------------
    # Fantasy (ESPN)
    # ------------------------------------------------------------------
    def refresh_fantasy(self):
        with self.heavy:
            self._refresh_fantasy()

    def _refresh_fantasy(self):
        self.log("ESPN: league, selecties en stats ophalen…")
        sp = self.period_override or self.espn.current_period()
        pro = self.espn.pro_teams()
        date_of = self._sp_dates(pro)
        meta = {"season": self.season, "sp": sp, "demo": self.demo,
                "date": str(date_of(sp)) if date_of(sp) else None}
        fantasy_teams, rosters, pairs, espn_scores = {}, {}, {}, {}
        ld = None
        slot_counts = dict(DEFAULT_SLOTS)
        team_stats = {}
        if self.demo:
            cats, stype = categories(None)
            raw = self.espn.pool(None, sp, limit=400)
        else:
            ld = self.espn.league(self.league_id, sp)
            settings = ld.get("settings") or {}
            meta["leagueName"] = settings.get("name") or f"League {self.league_id}"
            cats, stype = categories(settings)
            lsc = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}
            if lsc:
                slot_counts = {int(k): v for k, v in lsc.items() if v and int(k) in SLOT}
            members = {m.get("id"): m for m in ld.get("members", [])}
            for t in ld.get("teams", []):
                name = t.get("name") or f'{t.get("location", "")} {t.get("nickname", "")}'.strip()
                rec = (t.get("record") or {}).get("overall") or {}
                owners = [members.get(o, {}) for o in (t.get("owners") or [])]
                owner = ", ".join(filter(None, [
                    (o.get("firstName", "") + " " + o.get("lastName", "")).strip() or o.get("displayName", "")
                    for o in owners]))
                fantasy_teams[t["id"]] = {
                    "id": t["id"], "name": name, "abbrev": t.get("abbrev", str(t["id"])), "owner": owner,
                    "logo": t.get("logo"), "wins": rec.get("wins", 0), "losses": rec.get("losses", 0),
                    "ties": rec.get("ties", 0), "pct": rec.get("percentage"),
                    "seed": t.get("playoffSeed"), "rank": t.get("rankCalculatedFinal") or t.get("currentProjectedRank"),
                    "streak": f'{(rec.get("streakType") or "")[:1]}{rec.get("streakLength") or ""}',
                    "moves": (t.get("transactionCounter") or {}).get("matchupAcquisitionTotals"),
                }
                vbs = t.get("valuesByStat") or {}
                if vbs:
                    team_stats[t["id"]] = {str(k): v for k, v in vbs.items()}
                rosters[t["id"]] = [{"id": e["playerId"], "slot": SLOT.get(e["lineupSlotId"], "Bank")}
                                    for e in (t.get("roster") or {}).get("entries", [])]
            mp = (ld.get("status") or {}).get("currentMatchupPeriod")
            for m in ld.get("schedule", []):
                if m.get("matchupPeriodId") != mp:
                    continue
                h, a = (m.get("home") or {}).get("teamId"), (m.get("away") or {}).get("teamId")
                if h and a:
                    pairs[h], pairs[a] = a, h
                    espn_scores[h], espn_scores[a] = m.get("home"), m.get("away")
            ids = [r["id"] for ro in rosters.values() for r in ro]
            raw = self.espn.pool(self.league_id, sp, limit=400)
            have = {e["player"]["id"] for e in raw}
            missing = [i for i in ids if i not in have]
            if missing:
                raw += self.espn.players_by_id(self.league_id, sp, missing)

        players = {}
        for e in raw:
            p = build_player(e, self.season, pro, sp)
            if p["pos"]:
                players[p["id"]] = p

        mp, sps, next_sps = self._period_sps(sp, ld, date_of)
        meta.update({"matchupPeriod": mp, "sps": sps, "nextSps": next_sps, "scoringType": stype,
                     "days": {s: str(date_of(s)) for s in sps + next_sps if date_of(s)}})
        meta["cats"] = cat_meta(cats)
        meta["slots"] = {SLOT[k]: v for k, v in slot_counts.items()}

        model = ValueModel(cats, players, n_pool=max(100, 13 * max(8, len(fantasy_teams) or 10)))
        for p in players.values():
            model.score(p)

        if self.demo:
            fantasy_teams, rosters, pairs = self._demo_league(players, slot_counts)
            meta["leagueName"] = "Demo-league (openbare ESPN-data, 9-cat)"

        # wedstrijdlogs (laatste 12) voor alle spelers in een selectie
        rostered = sorted({r["id"] for ro in rosters.values() for r in ro})
        try:
            for e in self.espn.players_by_id(self.league_id, sp, rostered, recent=12):
                p = players.get(e["player"]["id"])
                if p:
                    p["games"] = build_player(e, self.season, pro, sp)["games"]
        except Exception as e:
            self._err("Wedstrijdlogs", e, "warn")

        owner_of = {r["id"]: tid for tid, ro in rosters.items() for r in ro}
        for pid, p in players.items():
            p["onTeamId"] = owner_of.get(pid, 0)
        # rang op waarde (rest seizoen) per positie en overall
        ranked = sorted(players.values(), key=lambda x: -((x.get("value") or {}).get("ros", -99)))
        for i, p in enumerate(ranked):
            p["rank"] = i + 1

        with self.lock:
            self.pro, self.date_of = pro, date_of
            self.players = players
            self.rosters, self.pairs = rosters, pairs
            self.espn_scores = espn_scores
            self.fantasy_teams = fantasy_teams
            self.meta = meta
            self.sp = sp
            self.cats = cats
            self.slot_counts = {k: v for k, v in slot_counts.items() if k not in BENCH_SLOT_IDS}
            self.model = model
            self.team_stats = team_stats
        self.log(f"ESPN: speeldag {sp} (matchup {mp}: dag {sps[0]}-{sps[-1]}), {len(players)} spelers, "
                 f"{len(fantasy_teams)} teams, {len(cats)} categorieën.")

    def _demo_league(self, players, slot_counts):
        """Snake-draft van 10 demoteams uit de openbare spelerspool."""
        names = ["Demoteam", "Splash Brothers", "Paint Protectors", "Fast Break FC", "Triple Double",
                 "Pick & Roll", "Buzzer Beaters", "Glass Cleaners", "And-One", "Full Court Press"]
        n_roster = sum(v for k, v in slot_counts.items() if k != 13)
        avail = sorted([p for p in players.values() if p.get("ppg")],
                       key=lambda x: -((x.get("value") or {}).get("ros", -99)))
        rosters = {i + 1: [] for i in range(len(names))}
        order = list(rosters)
        it = iter(avail)
        for rnd_ in range(n_roster):
            for tid in (order if rnd_ % 2 == 0 else order[::-1]):
                p = next(it, None)
                if p:
                    rosters[tid].append(p["id"])
        active = {k: v for k, v in slot_counts.items() if k not in BENCH_SLOT_IDS}
        out = {}
        for tid, ids in rosters.items():
            cands = [((players[i].get("value") or {}).get("ros", 0) + 10, i, set(players[i]["eligSlots"])) for i in ids]
            start = best_lineup(cands, active)
            # slots toewijzen aan de gekozen basisspelers
            free = [s for s, n in active.items() for _ in range(n)]
            ro = []
            for i in sorted(start, key=lambda i: len(players[i]["eligSlots"])):
                s = next((s for s in free if s in players[i]["eligSlots"]), None)
                if s is not None:
                    free.remove(s)
                ro.append({"id": i, "slot": SLOT.get(s, "UT")})
            ro += [{"id": i, "slot": "Bank"} for i in ids if i not in start]
            out[tid] = ro
        teams = {tid: {"id": tid, "name": names[tid - 1], "abbrev": names[tid - 1][:4].upper(),
                       "owner": f"Speler {tid}", "wins": 0, "losses": 0, "ties": 0} for tid in out}
        pairs = {}
        for a in range(1, len(names), 2):
            pairs[a], pairs[a + 1] = a + 1, a
        return teams, out, pairs

    # ------------------------------------------------------------------
    # Live: wedstrijden van deze matchup-periode en de categoriestand
    # ------------------------------------------------------------------
    def refresh_live(self):
        with self.lock:
            sp, sps = self.sp, list(self.meta.get("sps") or [])
            ids = sorted({r["id"] for ro in self.rosters.values() for r in ro})
            players, pro = self.players, self.pro
            date_of = self.date_of
        if not ids:
            return False
        past = [s for s in sps if s <= sp]
        fresh = {}
        if past:
            for e in self.espn.players_by_id(self.league_id, sp, ids, periods=past, timeout=30):
                pl = e["player"]
                team = pro.get(pl.get("proTeamId"), {})
                fresh[pl["id"]] = [game_row(s, team, pro) for s in pl.get("stats", [])
                                   if s.get("statSplitTypeId") == 5 and s.get("seasonId") == self.season
                                   and ((s.get("stats") or {}).get("40") or (s.get("stats") or {}).get("42"))]
        try:
            games = parse_scoreboard(self.espn.scoreboard(date_of(sp))) if date_of(sp) else {}
        except Exception as e:
            self._err("Scoreboard", e, "warn")
            games = {}
        espn_scores = None
        if not self.demo and self.meta.get("matchupPeriod"):
            try:
                ld = self.espn.league_matchups(self.league_id, sp, self.meta["matchupPeriod"])
                espn_scores = {}
                for m in ld.get("schedule", []):
                    if m.get("matchupPeriodId") != self.meta["matchupPeriod"]:
                        continue
                    for side in ("home", "away"):
                        s = m.get(side) or {}
                        if s.get("teamId"):
                            espn_scores[s["teamId"]] = s
            except Exception as e:
                self._err("Matchups", e, "warn")
        stamp = now_iso()
        events = []
        with self.lock:
            for pid, gl in fresh.items():
                p = players.get(pid)
                if not p:
                    continue
                by_sp = {g["sp"]: g for g in p["games"]}
                for g in gl:
                    by_sp[g["sp"]] = g
                p["games"] = sorted(by_sp.values(), key=lambda g: g["sp"])
                today = by_sp.get(sp)
                if today:
                    prev = self._last_lines.get(pid)
                    cur = {k: today.get(k, 0) for k in ("0", "6", "3", "2", "1", "17", "11")}
                    if prev is not None and cur != prev:
                        delta = {k: cur[k] - prev.get(k, 0) for k in cur if cur[k] != prev.get(k, 0)}
                        events.append({"at": stamp, "id": pid, "name": p["name"],
                                       "delta": {STATS[k][0]: round(v) for k, v in delta.items()},
                                       "line": stat_line(today)})
                    self._last_lines[pid] = cur
            if espn_scores is not None:
                self.espn_scores.update(espn_scores)
            self.games = games
            self.events = (events + self.events)[:400]
            self.live_updated = stamp
            self.any_live = any(g["state"] == "in" for g in games.values())
            self._project_all()
        return self.any_live

    def _projector(self):
        return Projector(self.cats, self.players, self.pro, self.slot_counts, self.sp,
                         self.meta.get("sps") or [self.sp], self.games, factor=self._factor)

    # ------------------------------------------------------------------
    # Basketball Reference
    # ------------------------------------------------------------------
    def _abbr(self, pro_id):
        return self.pro.get(pro_id, {}).get("abbrev")

    def _factor_teams(self, own, opp):
        key = (own, opp)
        if key not in self._fac:
            self._fac[key] = opponent_factors(self.bbref.teams, own, opp) if self.bbref.teams else None
        return self._fac[key]

    def _factor(self, p, g):
        return self._factor_teams(p.get("team"), self._abbr(g["opp"]))

    def refresh_bbref(self, force=False):
        if self.bbref.refresh(force=force) or not self._fac:
            with self.lock:
                self._fac = {}
                if self.players and self.live_updated:
                    self._project_all()

    def _adv(self, p):
        b = self.bbref.player(p.get("name") or "") if self.bbref.players else None
        if not b:
            return None
        return {k: b.get(k) for k in ("per", "ts_pct", "usg_pct", "ast_pct", "trb_pct", "stl_pct", "blk_pct",
                                      "tov_pct", "obpm", "dbpm", "bpm", "vorp", "ws_per_48", "fg3a_per_fga_pct",
                                      "fta_per_fga_pct", "bbrefId")}

    def _project_all(self):
        pj = self._projector()
        out = {}
        done = set()
        for a, b in self.pairs.items():
            if a in done:
                continue
            done |= {a, b}
            ra, rb = self.rosters.get(a, []), self.rosters.get(b, [])
            est_a = pj.side(ra)[0]
            est_b = pj.side(rb)[0]
            ca, res_a = espn_current(self.espn_scores.get(a), self.cats, est_a)
            cb, res_b = espn_current(self.espn_scores.get(b), self.cats, est_b)
            m = pj.matchup(ra, rb, ca if ca else est_a, cb if cb else est_b)
            m["source"] = "espn" if ca else "berekend"
            m["a"], m["b"] = a, b
            m["resA"] = res_a
            out[frozenset((a, b))] = m
        self.proj = out

    def _proj_for(self, tid):
        opp = self.pairs.get(tid)
        if not opp:
            return None
        m = self.proj.get(frozenset((tid, opp)))
        if not m:
            return None
        if m["a"] == tid:
            return m
        # spiegelen
        return {**m, "a": tid, "b": opp, "winProb": round(1 - m["winProb"], 3), "expA": m["expB"], "expB": m["expA"],
                "gamesA": m["gamesB"], "gamesB": m["gamesA"], "resA": None,
                "cats": [{"cat": r["cat"], "rev": r["rev"], "a": r["b"], "b": r["a"], "pA": round(1 - r["pA"], 3)} for r in m["cats"]]}

    # ------------------------------------------------------------------
    # Nieuws
    # ------------------------------------------------------------------
    def refresh_news(self):
        with self.lock:
            rostered = {r["id"] for ro in self.rosters.values() for r in ro}
            players = dict(self.players)
        todo = sorted([pid for pid in rostered if pid > 0 and players.get(pid)
                       and players[pid].get("lastNews") != self._news_seen.get(pid)],
                      key=lambda pid: -(players[pid].get("lastNews") or 0))
        if time.time() < getattr(self, "_news_backoff_until", 0):
            todo = []
        batch = int(self.cfg.get("news_batch", 20))
        limit = int(self.cfg.get("news_per_player", 8))
        items, done, seen, warns = [], 0, {}, []
        for pid in todo[:batch]:
            try:
                res = self.espn.news(pid, limit)
            except Exception as e:
                code = getattr(e, "code", None)
                if code in (403, 429):
                    self._news_backoff = min(3600, getattr(self, "_news_backoff", 300) * 2)
                    self._news_backoff_until = time.time() + self._news_backoff
                    warns.append(f"ESPN limiet bereikt ({code}); opnieuw over {self._news_backoff // 60} min")
                    self._err("ESPN-nieuws", warns[-1], "warn")
                    break
                warns.append(f"ESPN {pid}: {e}")
                self._err("ESPN-nieuws", e, "warn")
                continue
            for it in res:
                it["player_ids"] = [pid]
                items.append(it)
            seen[pid] = players[pid].get("lastNews")
            done += 1
            time.sleep(1.0)
        else:
            if done:
                self._news_backoff = 300
        name_to_ids = {}
        for p in players.values():
            name_to_ids.setdefault(norm_name(p["name"]), []).append(p["id"])
        try:
            items += match_rotowire(fetch_rotowire(), name_to_ids)
        except Exception as e:
            warns.append(f"Rotowire: {e}")
            self._err("Rotowire", e, "warn")
        new = self.news.ingest(items, players=players, extracted=len(items), message="; ".join(warns) or None)
        self._news_seen.update(seen)
        self.log(f"Nieuws: {done} spelers bijgewerkt ({max(0, len(todo) - done)} in de wachtrij), {new} nieuwe items.")

    # ------------------------------------------------------------------
    # API-output
    # ------------------------------------------------------------------
    def _games_left(self, p, sps=None):
        sps = sps if sps is not None else [s for s in self.meta.get("sps", []) if s >= self.sp]
        out = []
        team = self.pro.get(p.get("proTeamId"), {})
        for s in sps:
            g = team.get("games", {}).get(s)
            if not g:
                continue
            if s == self.sp:
                st = self.games.get(str(g["gameId"]))
                if st and st["state"] == "post":
                    continue
            out.append(s)
        return out

    def _today(self, p):
        g = (self.pro.get(p.get("proTeamId"), {}).get("games") or {}).get(self.sp)
        if not g:
            return None
        st = self.games.get(str(g["gameId"])) or {}
        return {"opp": self.pro.get(g["opp"], {}).get("abbrev"), "home": g["home"], "date": g["date"],
                "gameId": str(g["gameId"]), "state": st.get("state"), "detail": st.get("detail"), "frac": st.get("frac", 0)}

    def _player_out(self, p, games=5):
        need = set(RAW)
        out = {k: p.get(k) for k in ("id", "name", "pos", "elig", "team", "injury", "onTeamId", "status",
                                     "owned", "ownedChange", "started", "rank", "headshot", "proTeamId")}
        out["pg"] = {w: {"gp": v["gp"], "s": {k: round(x, 2) for k, x in v["pg"].items() if k in need}}
                     for w, v in p["windows"].items() if v.get("pg")}
        out["z"], out["value"], out["attrs"] = p.get("z"), p.get("value"), p.get("attrs")
        out["ppg"] = rnd(p.get("ppg"))
        recent = p["games"][-games:] if games else p["games"]
        out["form"] = [{"sp": g["sp"], "opp": g["opp"], "date": g["date"],
                        "v": self.model.game_value(g) if self.model else None,
                        "line": stat_line(g), "pts": g.get("0"), "min": g.get("40"),
                        "s": {k: g.get(k, 0) for k in LOG_STATS}} for g in recent]
        out["left"] = self._games_left(p)
        out["next"] = len(self._games_left(p, self.meta.get("nextSps") or []))
        out["today"] = self._today(p)
        out["eligSlots"] = [SLOT[s] for s in p.get("eligSlots", []) if s in SLOT and s not in BENCH_SLOT_IDS]
        out["adv"] = self._adv(p)
        return out

    def teams_out(self):
        with self.lock:
            return {"leagueName": self.meta.get("leagueName"), "default": self.default_team, "demo": self.demo,
                    "teams": [{k: t.get(k) for k in ("id", "name", "abbrev", "owner", "wins", "losses", "ties", "logo")}
                              for t in sorted(self.fantasy_teams.values(), key=lambda t: t["id"])]}

    def state_out(self, tid):
        with self.lock:
            if not self.players or not self.model:
                return {"status": "laden", "errors": self.errors}
            roster, opp_roster, opp_id = self.ctx(tid)
            mine = {r["id"]: r["slot"] for r in roster}
            theirs = {r["id"]: r["slot"] for r in opp_roster}
            depth = int(self.cfg.get("pool_depth", 300))
            ranked = sorted(self.players.values(), key=lambda x: x.get("rank") or 9999)
            pool = ranked[:depth] + [p for p in ranked[depth:] if p["onTeamId"]]
            out_players = {}
            for p in pool:
                q = self._player_out(p)
                if p["id"] in mine:
                    q["mine"], q["slot"] = True, mine[p["id"]]
                if p["id"] in theirs:
                    q["oppTeam"], q["oppSlot"] = True, theirs[p["id"]]
                out_players[p["id"]] = q
            meta = dict(self.meta, bbref=self.bbref.updated, team=self.fantasy_teams.get(tid),
                        opponent=self.fantasy_teams.get(opp_id) if opp_id else None, teamId=tid)
            return {"status": "ok", "generated": now_iso(), "meta": meta,
                    "roster": [r["id"] for r in roster], "oppRoster": [r["id"] for r in opp_roster],
                    "fantasyTeams": self.fantasy_teams, "players": out_players,
                    "matchup": self._proj_for(tid), "errors": self.errors[-3:]}

    def live_out(self, tid):
        with self.lock:
            if not self.live_updated:
                return {"status": "nog niet geladen"}
            roster, opp, opp_id = self.ctx(tid)

            def rows(ro):
                out = []
                for r in ro:
                    p = self.players.get(r["id"])
                    if not p:
                        continue
                    today = self._today(p)
                    g = next((x for x in p["games"] if x["sp"] == self.sp), None)
                    out.append({"id": p["id"], "name": p["name"], "pos": p["pos"], "team": p["team"],
                                "slot": r["slot"], "injury": p["injury"], "today": today,
                                "stats": {k: g.get(k, 0) for k in RAW} if g else None,
                                "line": stat_line(g) if g else "", "left": len(self._games_left(p))})
                return out
            ids = {r["id"] for r in roster} | {r["id"] for r in opp}
            mine_ids = {r["id"] for r in roster}
            events = [dict(e, mine=e["id"] in mine_ids) for e in self.events if e["id"] in ids][:50]
            gids = set()
            for i in ids:
                t = self._today(self.players[i]) if i in self.players else None
                if t:
                    gids.add(t["gameId"])
            games = [self.games[g] for g in gids if g in self.games]
            return {"updated": self.live_updated, "sp": self.sp, "anyLive": any(g["state"] == "in" for g in games),
                    "me": rows(roster), "opp": rows(opp) if opp else None, "matchup": self._proj_for(tid),
                    "events": events, "games": sorted(games, key=lambda g: g["date"] or "")}

    def league_out(self):
        with self.lock:
            teams = sorted(self.fantasy_teams.values(),
                           key=lambda t: (-(t["wins"] + 0.5 * t["ties"]) / max(1, t["wins"] + t["losses"] + t["ties"]),
                                          -t["wins"]))
            matchups = []
            for key, m in self.proj.items():
                matchups.append({"a": m["a"], "b": m["b"], "winProb": m["winProb"], "expA": m["expA"], "expB": m["expB"],
                                 "now": score_now(m), "source": m["source"],
                                 "cats": [{"cat": r["cat"], "rev": r["rev"], "a": r["a"]["now"], "b": r["b"]["now"],
                                           "fa": r["a"]["final"], "fb": r["b"]["final"], "pA": r["pA"]} for r in m["cats"]]})
            # categoriesterkte per team: seizoensstats van ESPN of projectie per wedstrijd van de basis
            strength = self._strength()
            return {"standings": teams, "matchups": matchups, "anyLive": self.any_live, "updated": self.live_updated,
                    "matchupPeriod": self.meta.get("matchupPeriod"), "strength": strength}

    def _strength(self):
        cats = self.cats
        out = {}
        for tid, ro in self.rosters.items():
            ps = [self.players[r["id"]] for r in ro if r["id"] in self.players and r["slot"] != "IR"
                  and self.players[r["id"]].get("ppg")]
            ps.sort(key=lambda p: -((p.get("value") or {}).get("ros", -99)))
            n = sum(self.slot_counts.values())
            tot = {}
            for p in ps[:n]:
                for k, v in p["ppg"].items():
                    tot[k] = tot.get(k, 0) + v
            out[tid] = {"proj": side_totals(tot, cats)}
            if tid in getattr(self, "team_stats", {}):
                ts = self.team_stats[tid]
                out[tid]["season"] = {c: ts.get(c) for c, _ in cats}
        # rangen per categorie
        for src in ("proj", "season"):
            for c, rev in cats:
                vals = [(tid, d[src].get(c)) for tid, d in out.items() if src in d and d[src].get(c) is not None]
                vals.sort(key=lambda x: x[1], reverse=not rev)
                for i, (tid, _) in enumerate(vals):
                    out[tid].setdefault(src + "Rank", {})[c] = i + 1
        return out

    def player_out(self, pid, tid):
        with self.lock:
            p = self.players.get(pid)
            if not p:
                return None
        # volledige wedstrijdlog (op aanvraag)
        try:
            e = self.espn.players_by_id(self.league_id, self.sp, [pid], recent=82, timeout=20)
            if e:
                with self.lock:
                    games = build_player(e[0], self.season, self.pro, self.sp)["games"]
                    if games:
                        by = {g["sp"]: g for g in games}
                        by.update({g["sp"]: g for g in p["games"]})
                        p["games"] = sorted(by.values(), key=lambda g: g["sp"])
        except Exception as e:
            self._err("Spelerlog", e, "warn")
        with self.lock:
            roster, opp, _ = self.ctx(tid)
            out = self._player_out(p, games=0)
            slot = {r["id"]: r["slot"] for r in roster}.get(pid)
            if slot:
                out["mine"], out["slot"] = True, slot
            if pid in {r["id"] for r in opp}:
                out["oppTeam"] = True
            out["outlook"] = p.get("outlook")
            out["news"] = self.news.for_players([pid], 40)
            team = self.pro.get(p.get("proTeamId"), {})
            upcoming = []
            for s in sorted(k for k in team.get("games", {}) if k >= self.sp)[:12]:
                g = team["games"][s]
                opp = self._abbr(g["opp"])
                fac = self._factor_teams(p.get("team"), opp) or {}
                bt = self.bbref.teams.get(opp) or {}
                upcoming.append({"sp": s, "date": g["date"], "opp": opp,
                                 "home": g["home"], "inMatchup": s in (self.meta.get("sps") or []),
                                 "ease": fac.get("_ease"), "pace": bt.get("pace"), "drtg": bt.get("drtg"),
                                 "f": {k: v for k, v in fac.items() if not k.startswith("_")} or None})
            out["upcoming"] = upcoming
            # directe concurrenten (zelfde positie, rond dezelfde waarde)
            grp = sorted([x for x in self.players.values() if x["pos"] == p["pos"]],
                         key=lambda x: x.get("rank") or 9999)
            i = next((k for k, x in enumerate(grp) if x["id"] == pid), 0)
            lo = max(0, i - 4)
            out["competitors"] = [self._player_out(x, games=0) for x in grp[lo:i + 5]]
            for c in out["competitors"]:
                c["form"] = c["form"][-5:]
            return out

    def schedule_out(self):
        with self.lock:
            sps, nxt = self.meta.get("sps") or [], self.meta.get("nextSps") or []
            days = []
            for s in sps + nxt:
                d = self.date_of(s)
                n = sum(1 for t in self.pro.values() if s in t["games"]) // 2
                days.append({"sp": s, "date": str(d) if d else None, "dow": DAYS_NL[d.weekday()] if d else "",
                             "games": n, "next": s in nxt, "past": s < self.sp})
            teams = []
            for t in self.pro.values():
                row = {"abbrev": t["abbrev"], "name": t["name"], "games": {}}
                for s in sps + nxt:
                    g = t["games"].get(s)
                    if g:
                        opp = self._abbr(g["opp"])
                        fac = self._factor_teams(t["abbrev"], opp) or {}
                        row["games"][s] = {"opp": opp, "home": g["home"], "ease": fac.get("_ease")}
                row["left"] = sum(1 for s in sps if s >= self.sp and s in t["games"])
                row["total"] = sum(1 for s in sps if s in t["games"])
                row["next"] = sum(1 for s in nxt if s in t["games"])
                bt = self.bbref.teams.get(t["abbrev"]) or {}
                row.update({"pace": bt.get("pace"), "ortg": bt.get("ortg"), "drtg": bt.get("drtg"),
                            "record": f'{int(bt["wins"])}-{int(bt["losses"])}' if bt.get("wins") is not None else None})
                eases = [g["ease"] for s, g in row["games"].items() if s in sps and s >= self.sp and g.get("ease")]
                row["ease"] = round(sum(eases) / len(eases), 3) if eases else None
                teams.append(row)
            return {"days": days, "teams": sorted(teams, key=lambda r: (-r["left"], -r["next"], r["abbrev"])),
                    "sp": self.sp, "bbref": self.bbref.updated}

    def news_out(self, tid):
        with self.lock:
            roster, opp, _ = self.ctx(tid)
        return {"mine": self.news.for_players([r["id"] for r in roster], 200),
                "opp": self.news.for_players([r["id"] for r in opp], 60),
                "latest": self.news.latest(80), "etl": self._etl_status()}

    def _etl_status(self):
        try:
            return self.news.etl_status(3)
        except Exception as e:
            return {"backend": "onbekend", "error": str(e)}

    # ------------------------------------------------------------------
    # Achtergrondlus
    # ------------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _safe(self, name, fn):
        try:
            return fn()
        except AuthError as e:
            self._err(name, e)
        except Exception as e:
            self._err(name, e)
            traceback.print_exc()
        return None

    def _news_async(self):
        if getattr(self, "_news_thread", None) and self._news_thread.is_alive():
            return
        self._news_thread = threading.Thread(target=lambda: self._safe("Nieuws", self.refresh_news), daemon=True)
        self._news_thread.start()

    def _bbref_async(self):
        if self.cfg.get("bbref", True) in (False, "0", "false"):
            return
        if getattr(self, "_bbref_thread", None) and self._bbref_thread.is_alive():
            return
        def run():
            try:
                self.refresh_bbref()
            except Exception as e:  # optionele bron: alleen in de log, de app werkt zonder
                self._err("Basketball Reference", e, "warn")
        self._bbref_thread = threading.Thread(target=run, daemon=True)
        self._bbref_thread.start()

    def _loop(self):
        while not self.players:
            self._safe("ESPN", self.refresh_fantasy)
            if not self.players:
                time.sleep(30)
        self._safe("Live", self.refresh_live)
        self._news_async()
        self._bbref_async()
        iv = self.cfg.get("intervals", {})
        t = {k: time.time() for k in ("fantasy", "news", "live", "bbref")}
        live_fast = self.any_live
        while True:
            time.sleep(5)
            now = time.time()
            if now - t["live"] >= (iv.get("live_seconds", 30) if live_fast else iv.get("idle_seconds", 300)):
                t["live"] = now
                live_fast = bool(self._safe("Live", self.refresh_live))
            if now - t["news"] >= iv.get("news_seconds", 300):
                t["news"] = now
                self._news_async()
            if now - t["bbref"] >= 3600:  # de cache zelf ververst hooguit eens per 12 uur
                t["bbref"] = now
                self._bbref_async()
            if now - t["fantasy"] >= iv.get("fantasy_seconds", 900):
                t["fantasy"] = now
                self._safe("ESPN", self.refresh_fantasy)
                self._safe("Live", self.refresh_live)

    def force_refresh(self):
        if time.time() - getattr(self, "_last_force", 0) < 60:
            return False
        self._last_force = time.time()

        def run():
            self._safe("ESPN", self.refresh_fantasy)
            self._safe("Live", self.refresh_live)
            self._safe("Nieuws", self.refresh_news)
        threading.Thread(target=run, daemon=True).start()
        return True


def stat_line(g):
    if not g:
        return ""
    parts = [f'{int(g.get("40", 0))} min', f'{int(g.get("0", 0))} pts', f'{int(g.get("6", 0))} reb',
             f'{int(g.get("3", 0))} ast']
    for k, lab in (("2", "stl"), ("1", "blk"), ("17", "3pm"), ("11", "to")):
        if g.get(k):
            parts.append(f"{int(g[k])} {lab}")
    if g.get("14"):
        parts.append(f'{int(g.get("13", 0))}/{int(g["14"])} FG')
    if g.get("16"):
        parts.append(f'{int(g.get("15", 0))}/{int(g["16"])} FT')
    return ", ".join(parts)


def score_now(m):
    """Huidige categoriestand (W-V-G) vanuit de huidige waarden."""
    w = l = t = 0
    for r in m["cats"]:
        a, b = r["a"]["now"], r["b"]["now"]
        if a is None or b is None:
            continue
        rev = r.get("rev", False)
        if abs(a - b) < 1e-9:
            t += 1
        elif (a > b) != rev:
            w += 1
        else:
            l += 1
    return [w, l, t]
