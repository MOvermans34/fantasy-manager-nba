"""
Categoriemodel voor H2H-categorieën.

- z-scores per categorie (de klassieke "9-cat-waarde"), per periode (seizoen, L7, L15, L30, projectie)
- attributen 1-20 per categorie (Football Manager-stijl) en een kwaliteitscijfer
- projectie per wedstrijd (mix van seizoen, recente vorm en ESPN-projectie)
- matchup-projectie: huidige stand + resterende wedstrijden met een dagelijkse opstelling,
  winkans per categorie (normale benadering) en in totaal (Monte Carlo)
"""
import math
import random

from .espn import RATIO, STATS

PCT = {"19", "20", "21"}  # binomiale percentages
INACTIVE_INJ = {"OUT", "INJURY_RESERVE", "SUSPENSION"}


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def cat_meta(cats):
    return [{"id": c, "abbr": STATS[c][0], "name": STATS[c][1], "reverse": rev,
             "ratio": list(RATIO[c]) if c in RATIO else None} for c, rev in cats]


def _val(pg, c):
    if c in RATIO:
        n, d = RATIO[c]
        den = pg.get(d, 0)
        return pg.get(n, 0) / den if den else None
    return pg.get(c, 0)


# ----------------------------------------------------------------------
# Waardemodel (z-scores)
# ----------------------------------------------------------------------
class ValueModel:
    def __init__(self, cats, players, n_pool=150):
        """players: {id: speler met windows}. Normaliseert per periode op de top-n_pool."""
        self.cats = cats
        self.n_pool = n_pool
        self.norm = {}   # window -> {cat: (mean, sd, ratio)}
        for w in ("season", "l7", "l15", "l30", "proj", "last"):
            rows = [(pid, p["windows"][w]["pg"]) for pid, p in players.items()
                    if w in p["windows"] and p["windows"][w]["pg"] and p["windows"][w]["gp"] >= (1 if w == "l7" else 3)
                    and p["windows"][w]["pg"].get("40", 0) >= 10]
            if len(rows) < 30:
                continue
            norm = self._fit([r[1] for r in rows])
            # tweede ronde: alleen de top-n_pool (realistische "rosterbare" spelers)
            scored = sorted(rows, key=lambda r: -self._total(r[1], norm))[: self.n_pool]
            self.norm[w] = self._fit([r[1] for r in scored])

    def _fit(self, pgs):
        out = {}
        for c, _ in self.cats:
            if c in RATIO:
                n, d = RATIO[c]
                sn, sd_ = sum(p.get(n, 0) for p in pgs), sum(p.get(d, 0) for p in pgs)
                ratio = sn / sd_ if sd_ else 0
                xs = [p.get(n, 0) - ratio * p.get(d, 0) for p in pgs]
            else:
                ratio = None
                xs = [p.get(c, 0) for p in pgs]
            m = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) or 1.0
            out[c] = (m, sd, ratio)
        return out

    def z(self, pg, norm):
        out = {}
        for c, rev in self.cats:
            m, sd, ratio = norm[c]
            if c in RATIO:
                n, d = RATIO[c]
                x = pg.get(n, 0) - ratio * pg.get(d, 0)
            else:
                x = pg.get(c, 0)
            v = (x - m) / sd
            out[c] = round(-v if rev else v, 2)
        return out

    def _total(self, pg, norm):
        return sum(self.z(pg, norm).values())

    def score(self, p):
        """Voegt aan de speler toe: z per periode, totaalwaarde, attributen, projectie per wedstrijd."""
        p["z"], p["value"] = {}, {}
        for w, norm in self.norm.items():
            win = p["windows"].get(w)
            if win and win["pg"] and win["gp"]:
                zz = self.z(win["pg"], norm)
                p["z"][w] = zz
                p["value"][w] = round(sum(zz.values()), 2)
        p["ppg"] = projection(p)
        base = self.norm.get("season") or self.norm.get("proj") or self.norm.get("last")
        if p["ppg"] and base:
            p["z"]["ros"] = self.z(p["ppg"], base)
            p["value"]["ros"] = round(sum(p["z"]["ros"].values()), 2)
        zr = p["z"].get("ros") or {}
        p["attrs"] = {c: attr_score(v) for c, v in zr.items()}
        p["attrs"]["MIN"] = attr_score(((p["ppg"] or {}).get("40", 0) - 24) / 7) if p["ppg"] else None

    def game_value(self, g):
        base = self.norm.get("season") or self.norm.get("proj") or self.norm.get("last")
        if not base:
            return None
        return round(sum(self.z(g, base).values()), 1)


def attr_score(z):
    if z is None:
        return None
    return int(max(1, min(20, round(10.5 + 3.3 * z))))


def projection(p):
    """Verwachting per wedstrijd: seizoen, L15 en ESPN-projectie, gewogen naar steekproefgrootte."""
    w = p["windows"]
    parts = []
    s = w.get("season")
    if s and s["gp"] and s["pg"]:
        parts.append((min(1.0, s["gp"] / 15) * 0.5, s["pg"]))
    r = w.get("l15")
    if r and r["gp"] >= 3 and r["pg"]:
        parts.append((0.3, r["pg"]))
    pj = w.get("proj")
    if pj and pj["gp"] and pj["pg"]:
        parts.append((0.25, pj["pg"]))
    last = w.get("last")
    if not parts and last and last["gp"] and last["pg"]:
        parts.append((1.0, last["pg"]))
    if not parts:
        return None
    tot = sum(x[0] for x in parts)
    keys = set().union(*[x[1].keys() for x in parts])
    return {k: sum(wt * pg.get(k, 0) for wt, pg in parts) / tot for k in keys}


# ----------------------------------------------------------------------
# Dagelijkse opstelling (maximale waarde binnen de slots)
# ----------------------------------------------------------------------
def best_lineup(cands, slot_counts):
    """cands: [(waarde, speler-id, {slot-id's})]; slot_counts: {slot-id: aantal}.
    Kiest via augmenting paths zoveel mogelijk spelers, in volgorde van waarde."""
    slots = [s for s, n in slot_counts.items() for _ in range(n)]
    owner = [None] * len(slots)
    chosen = []

    def place(i, seen, elig):
        for j, s in enumerate(slots):
            if s in elig[i] and j not in seen:
                seen.add(j)
                if owner[j] is None or place(owner[j], seen, elig):
                    owner[j] = i
                    return True
        return False

    order = sorted(range(len(cands)), key=lambda i: -cands[i][0])
    elig = {i: cands[i][2] for i in order}
    for i in order:
        if place(i, set(), elig):
            chosen.append(cands[i][1])
    return set(chosen)


# ----------------------------------------------------------------------
# Matchup-projectie
# ----------------------------------------------------------------------
def components(cats):
    """Ruwe stats die we moeten optellen voor de categorieën."""
    need = set()
    for c, _ in cats:
        if c in RATIO:
            need |= set(RATIO[c])
        else:
            need.add(c)
    return sorted(need)


def side_totals(totals, cats):
    """Ruwe sommen -> categoriewaarden."""
    return {c: (_val(totals, c) if c in RATIO else totals.get(c, 0)) for c, _ in cats}


class Projector:
    def __init__(self, cats, players, pro, slot_counts, sp, sps, live_games, factor=None):
        """sps: alle speeldagen van de huidige matchup; live_games: game-id -> status van vandaag.
        factor(speler, wedstrijd) -> {stat-id: vermenigvuldiger} voor de tegenstander (of None)."""
        self.factor = factor
        self.cats, self.players, self.pro = cats, players, pro
        self.slot_counts = slot_counts
        self.sp, self.sps = sp, sps
        self.live = live_games
        self.need = components(cats)

    def game_on(self, p, sp):
        return (self.pro.get(p.get("proTeamId"), {}).get("games") or {}).get(sp)

    def _cands(self, roster, sp):
        out = []
        for r in roster:
            if r["slot"] == "IR":
                continue
            p = self.players.get(r["id"])
            if not p or not p.get("ppg") or p.get("injury") in INACTIVE_INJ or not self.game_on(p, sp):
                continue
            out.append(((p.get("value") or {}).get("ros", 0) + 10, r["id"], set(p.get("eligSlots") or [])))
        return out

    def side(self, roster, cur=None):
        """Huidige ruwe sommen (cur) + verwachting resterende wedstrijden.
        Geeft (huidig, verwacht-rest, variantie-rest, wedstrijden gespeeld/resterend)."""
        rem = {k: 0.0 for k in self.need}
        var = {k: 0.0 for k in self.need}
        played = 0
        remaining = 0
        # zelf berekenen uit de wedstrijdlogs van deze matchup-periode (ook als ESPN de stand geeft:
        # dan tellen we alleen de gespeelde wedstrijden)
        log_tot = {k: 0.0 for k in self.need}
        for sp in [s for s in self.sps if s <= self.sp]:
            for pid in self._starters(roster, sp, past=True):
                g = next((x for x in self.players[pid]["games"] if x["sp"] == sp), None)
                if g:
                    played += 1
                    for k in self.need:
                        log_tot[k] += g.get(k, 0)
        cur_tot = dict(cur) if cur is not None else log_tot
        for sp in [s for s in self.sps if s >= self.sp]:
            starters = self._starters(roster, sp)
            for pid in starters:
                p = self.players[pid]
                g = self.game_on(p, sp)
                frac = 0.0
                if sp == self.sp:
                    st = self.live.get(str(g["gameId"])) if g else None
                    frac = st["frac"] if st else 0.0
                left = 1.0 - frac
                if left <= 0:
                    continue
                if p.get("injury") == "DAY_TO_DAY":
                    left *= 0.8
                remaining += left
                fac = (self.factor(p, g) if self.factor and g else None) or {}
                for k in self.need:
                    m = p["ppg"].get(k, 0) * left * fac.get(k, 1.0)
                    rem[k] += m
                    var[k] += max(m, 0.15 * left) * 1.5
        return cur_tot, rem, var, played, round(remaining, 1)

    def _starters(self, roster, sp, past=False):
        if sp == self.sp and not past:
            # vandaag: de echte opstelling (geen bank/IR)
            return {r["id"] for r in roster if r["slot"] not in ("Bank", "IR")
                    and r["id"] in self.players and self.players[r["id"]].get("ppg")
                    and self.game_on(self.players[r["id"]], sp)}
        if past:
            cands = []
            for r in roster:
                p = self.players.get(r["id"])
                if r["slot"] == "IR" or not p:
                    continue
                g = next((x for x in p["games"] if x["sp"] == sp), None)
                if g:
                    cands.append(((p.get("value") or {}).get("ros", 0) + 10, r["id"], set(p.get("eligSlots") or [])))
            return best_lineup(cands, self.slot_counts)
        return best_lineup(self._cands(roster, sp), self.slot_counts)

    def matchup(self, a_roster, b_roster, a_cur=None, b_cur=None, sims=3000, seed=7):
        A = self.side(a_roster, a_cur)
        B = self.side(b_roster, b_cur)
        rows = []
        mus, sds = [], []
        for c, rev in self.cats:
            ra = self._cat(c, A)
            rb = self._cat(c, B)
            diff = ra["final"] - rb["final"] if ra["final"] is not None and rb["final"] is not None else 0
            sd = math.sqrt(ra["var"] + rb["var"])
            if rev:
                diff = -diff
            if sd < 1e-9:
                p = 1.0 if diff > 1e-9 else 0.0 if diff < -1e-9 else 0.5
            else:
                p = phi(diff / sd)
            mus.append(diff)
            sds.append(sd)
            rows.append({"cat": c, "rev": rev, "a": ra, "b": rb, "pA": round(p, 3)})
        # Monte Carlo voor het totaal
        rng = random.Random(seed)
        win = tie = 0
        exp_a = exp_b = 0.0
        for _ in range(sims):
            wa = wb = 0
            for m, s in zip(mus, sds):
                x = m + (rng.gauss(0, s) if s > 1e-9 else 0)
                if x > 1e-9:
                    wa += 1
                elif x < -1e-9:
                    wb += 1
            exp_a += wa
            exp_b += wb
            if wa > wb:
                win += 1
            elif wa == wb:
                tie += 1
        return {"cats": rows, "winProb": round((win + 0.5 * tie) / sims, 3),
                "expA": round(exp_a / sims, 1), "expB": round(exp_b / sims, 1),
                "gamesA": {"played": A[3], "left": A[4]}, "gamesB": {"played": B[3], "left": B[4]}}

    def _cat(self, c, side):
        cur, rem, var = side[0], side[1], side[2]
        if c in RATIO:
            n, d = RATIO[c]
            cn, cd = cur.get(n, 0), cur.get(d, 0)
            rn, rd = rem.get(n, 0), rem.get(d, 0)
            now = cn / cd if cd else None
            tot_d = cd + rd
            final = (cn + rn) / tot_d if tot_d else None
            if c in PCT and final is not None and rd > 0:
                p = rn / rd if rd else final
                v = rd * p * (1 - p) * 1.3 / (tot_d ** 2)
            elif final is not None and rd > 0:
                v = (var.get(n, 0) + (final ** 2) * var.get(d, 0)) / (tot_d ** 2)
            else:
                v = 0.0
            return {"now": now, "final": final, "var": v, "num": cn + rn, "den": tot_d, "nowNum": cn, "nowDen": cd}
        now = cur.get(c, 0)
        return {"now": now, "final": now + rem.get(c, 0), "var": var.get(c, 0)}


def espn_current(team_side, cats, est):
    """Huidige ruwe sommen uit ESPN (scoreByStat). Voor ratio's gebruiken we ESPN's
    percentage met de noemer uit onze eigen schatting (of uit ESPN als die er is)."""
    sbs = ((team_side or {}).get("cumulativeScoreLive") or (team_side or {}).get("cumulativeScore") or {}).get("scoreByStat") or {}
    if not sbs:
        return None, None
    cur = dict(est)
    res = {}
    for c, _ in cats:
        s = sbs.get(c)
        if not s or s.get("score") is None:
            continue
        res[c] = s.get("result")
        if c in RATIO:
            n, d = RATIO[c]
            den = sbs.get(d, {}).get("score") if d in sbs else est.get(d, 0)
            if den:
                cur[d] = den
                cur[n] = s["score"] * den
        else:
            cur[c] = s["score"]
    for k, s in sbs.items():  # componenten die ESPN zelf meestuurt
        if k not in dict(cats) and s.get("score") is not None and k in est:
            cur[k] = s["score"]
    return cur, res
