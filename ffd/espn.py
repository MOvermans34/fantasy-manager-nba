"""ESPN Fantasy Basketball (onofficiële API): league, selecties, stats, schema en live scores."""
import datetime as dt
import json
import urllib.error
import urllib.request

API = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date}"
NEWS = "https://site.api.espn.com/apis/fantasy/v2/games/fba/news/players?limit={limit}&playerId={pid}"
HEADSHOT = "https://a.espncdn.com/i/headshots/nba/players/full/{pid}.png"

POS = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C"}
SLOT = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 7: "SG/SF", 8: "G/F",
        9: "PF/C", 10: "F/C", 11: "UT", 12: "Bank", 13: "IR"}
BENCH_SLOT_IDS = {12, 13}
PLAYER_SLOT_IDS = [0, 1, 2, 3, 4]  # filterSlotIds voor de spelerspool

# statId -> (korte naam, Nederlandse omschrijving)
STATS = {
    "0": ("PTS", "Punten"), "1": ("BLK", "Blocks"), "2": ("STL", "Steals"), "3": ("AST", "Assists"),
    "4": ("OREB", "Aanvallende rebounds"), "5": ("DREB", "Verdedigende rebounds"), "6": ("REB", "Rebounds"),
    "9": ("PF", "Persoonlijke fouten"), "11": ("TO", "Turnovers"), "13": ("FGM", "Velddoelpunten"),
    "14": ("FGA", "Veldpogingen"), "15": ("FTM", "Vrije worpen raak"), "16": ("FTA", "Vrije worpen"),
    "17": ("3PM", "Driepunters"), "18": ("3PA", "Driepuntpogingen"), "19": ("FG%", "Velddoelpercentage"),
    "20": ("FT%", "Vrijeworppercentage"), "21": ("3P%", "Driepuntpercentage"), "22": ("AFG%", "Effectief FG%"),
    "35": ("A/TO", "Assists per turnover"), "37": ("DD", "Double-doubles"), "38": ("TD", "Triple-doubles"),
    "40": ("MIN", "Minuten"), "41": ("GS", "Basisplaatsen"), "42": ("GP", "Wedstrijden"),
}
# Ratio-categorieën: statId -> (teller, noemer[, extra teller-component met gewicht])
RATIO = {"19": ("13", "14"), "20": ("15", "16"), "21": ("17", "18"), "35": ("3", "11")}
DEFAULT_CATS = [("0", False), ("6", False), ("3", False), ("2", False), ("1", False),
                ("17", False), ("19", False), ("20", False), ("11", True)]  # standaard 9-cat
# Ruwe stats die we per wedstrijd/gemiddelde bewaren
RAW = ["0", "1", "2", "3", "4", "5", "6", "9", "11", "13", "14", "15", "16", "17", "18", "37", "38", "40", "41"]

# stat-id's van ESPN: "00{jaar}" seizoen, "01/02/03{jaar}" laatste 7/15/30 dagen, "10{jaar}" projectie
WINDOWS = {"season": "00", "l7": "01", "l15": "02", "l30": "03", "proj": "10"}


class AuthError(Exception):
    pass


def et_date(ms):
    """ESPN-tijdstempel (ms, UTC) -> speeldatum in de VS (wedstrijden beginnen na 12:00 ET)."""
    return (dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc) - dt.timedelta(hours=6)).date()


class Espn:
    def __init__(self, season, espn_s2=None, swid=None):
        self.season = season
        self.base = API.format(season=season)
        self.cookie = f"espn_s2={espn_s2}; SWID={swid}" if espn_s2 and swid else None

    def get(self, url, fantasy_filter=None, auth=True, timeout=60):
        headers = {"User-Agent": "fantasy-manager-nba/1.0", "Accept": "application/json"}
        if fantasy_filter is not None:
            headers["x-fantasy-filter"] = json.dumps(fantasy_filter)
        if auth and self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AuthError("ESPN weigert toegang (401): controleer ESPN_S2 en ESPN_SWID")
            raise

    # ------------------------------------------------------------------
    def current_period(self):
        """Huidige scoring period (= speeldag)."""
        return self.get(self.base, auth=False)["currentScoringPeriod"]["id"]

    def pro_teams(self):
        """NBA-teams met hun wedstrijden per speeldag (scoring period)."""
        data = self.get(self.base + "?view=proTeamSchedules_wl", auth=False)
        teams = {}
        for t in data["settings"]["proTeams"]:
            if not t["id"]:
                continue
            games = {}
            for sp, gl in (t.get("proGamesByScoringPeriod") or {}).items():
                g = gl[0]
                home = g["homeProTeamId"] == t["id"]
                games[int(sp)] = {"opp": g["awayProTeamId"] if home else g["homeProTeamId"],
                                  "home": home, "date": g["date"], "gameId": g["id"]}
            teams[t["id"]] = {"id": t["id"], "abbrev": t["abbrev"],
                              "name": f'{t["location"]} {t["name"]}'.strip(), "games": games}
        return teams

    def league(self, league_id, sp):
        url = (f"{self.base}/segments/0/leagues/{league_id}?view=mTeam&view=mRoster&view=mSettings"
               f"&view=mMatchupScore&view=mScoreboard&view=mStatus&scoringPeriodId={sp}")
        return self.get(url)

    def league_matchups(self, league_id, sp, mp):
        """Alleen de matchups van één matchup-periode (live categoriestanden)."""
        url = f"{self.base}/segments/0/leagues/{league_id}?view=mMatchupScore&view=mScoreboard&scoringPeriodId={sp}"
        return self.get(url, {"schedule": {"filterMatchupPeriodIds": {"value": [mp]}}}, timeout=30)

    def _filter(self, recent=1, ids=None, slots=None, limit=None, periods=None):
        y = self.season
        f = {}
        if periods:
            f["filterStatsForCurrentSeasonScoringPeriodId"] = {"value": list(periods)}
        else:
            f["filterStatsForTopScoringPeriodIds"] = {
                "value": max(1, recent),
                "additionalValue": [f"{w}{y}" for w in WINDOWS.values()] + [f"00{y - 1}"]}
        if ids is not None:
            f["filterIds"] = {"value": list(ids)}
        if slots is not None:
            f["filterSlotIds"] = {"value": slots}
        if limit:
            f["limit"] = limit
            f["sortPercOwned"] = {"sortAsc": False, "sortPriority": 1}
        return {"players": f}

    def players_url(self, league_id):
        if league_id:
            return f"{self.base}/segments/0/leagues/{league_id}"
        return f"{self.base}/segments/0/leaguedefaults/3"

    def pool(self, league_id, sp, limit=400):
        """Spelerspool (meest gekozen eerst) met seizoen/L7/L15/L30/projectie."""
        u = f"{self.players_url(league_id)}?view=kona_player_info&scoringPeriodId={sp}"
        return self.get(u, self._filter(1, slots=PLAYER_SLOT_IDS, limit=limit))["players"]

    def players_by_id(self, league_id, sp, ids, recent=1, periods=None, timeout=40):
        u = f"{self.players_url(league_id)}?view=kona_player_info&scoringPeriodId={sp}"
        out = []
        ids = list(ids)
        for i in range(0, len(ids), 150):
            out += self.get(u, self._filter(recent, ids=ids[i:i + 150], periods=periods), timeout=timeout)["players"]
        return out

    def scoreboard(self, date):
        return self.get(SCOREBOARD.format(date=date.strftime("%Y%m%d")), auth=False, timeout=20)

    def news(self, pid, limit=8):
        if pid < 0:
            return []
        data = self.get(NEWS.format(limit=limit, pid=pid), auth=False, timeout=20)
        out = []
        for a in data.get("feed", []):
            link = ((a.get("links") or {}).get("web") or {}).get("href")
            typ = a.get("type")
            out.append({"id": "espn-" + str(a.get("id")),
                        "source": "Rotowire" if typ == "Rotowire" else "ESPN",
                        "via": "ESPN" if typ == "Rotowire" else None,
                        "kind": {"Rotowire": "Update", "Story": "Analyse", "Media": "Video",
                                 "HeadlineNews": "Nieuws"}.get(typ, typ),
                        "author": a.get("byline"),
                        "headline": a.get("headline", ""), "description": a.get("description", ""),
                        "published": a.get("published"), "type": typ, "link": link})
        return out


# ----------------------------------------------------------------------
# Verwerken
# ----------------------------------------------------------------------
def r2(x):
    return None if x is None else round(x, 2)


def categories(settings):
    """Categorieën van de league: [(statId, omgekeerd)] en het scoringstype."""
    sc = (settings or {}).get("scoringSettings") or {}
    items = sc.get("scoringItems") or []
    stype = sc.get("scoringType") or "H2H_CATEGORY"
    cats = [(str(i["statId"]), bool(i.get("isReverseItem"))) for i in items if str(i.get("statId")) in STATS]
    if not cats or stype == "H2H_POINTS":
        return DEFAULT_CATS, stype
    return cats, stype


def per_game(stats, gp):
    """Totaalregel -> gemiddelden per wedstrijd voor de ruwe stats."""
    if not stats or not gp:
        return None
    return {k: stats.get(k, 0.0) / gp for k in RAW}


def build_player(entry, season, pro, sp):
    p = entry["player"]
    pos = POS.get(p.get("defaultPositionId"))
    team = pro.get(p.get("proTeamId"), {})
    windows, games = {}, []
    for s in p.get("stats", []):
        sid = s.get("id", "")
        st = s.get("stats") or {}
        if s.get("statSplitTypeId") == 5 and s.get("seasonId") == season:
            if st.get("40", 0) or st.get("42", 0):
                games.append(game_row(s, team, pro))
            continue
        for name, code in WINDOWS.items():
            if sid == f"{code}{season}":
                gp = st.get("42", 0)
                windows[name] = {"gp": int(gp), "pg": per_game(st, gp)}
        if sid == f"00{season - 1}":
            gp = st.get("42", 0)
            windows["last"] = {"gp": int(gp), "pg": per_game(st, gp)}
    games.sort(key=lambda g: g["sp"])
    own = p.get("ownership") or {}
    slots = [SLOT[s] for s in p.get("eligibleSlots", []) if s in SLOT and s not in BENCH_SLOT_IDS]
    return {
        "id": p["id"], "name": p.get("fullName"), "pos": pos,
        "elig": [s for s in slots if s in ("PG", "SG", "SF", "PF", "C")] or ([pos] if pos else []),
        "eligSlots": p.get("eligibleSlots", []),
        "proTeamId": p.get("proTeamId"), "team": team.get("abbrev", "FA"),
        "injury": p.get("injuryStatus") or ("ACTIVE" if not p.get("injured") else "INJURED"),
        "onTeamId": entry.get("onTeamId", 0), "status": entry.get("status"),
        "owned": r2(own.get("percentOwned")), "ownedChange": r2(own.get("percentChange")),
        "started": r2(own.get("percentStarted")),
        "windows": windows, "games": games,
        "outlook": p.get("seasonOutlook"),
        "lastNews": p.get("lastNewsDate"),
        "headshot": HEADSHOT.format(pid=p["id"]),
    }


def game_row(s, team, pro):
    st = s.get("stats") or {}
    sp = s.get("scoringPeriodId")
    g = (team.get("games") or {}).get(sp) or {}
    row = {"sp": sp, "gameId": s.get("externalId"),
           "opp": pro.get(g.get("opp"), {}).get("abbrev") if g else None,
           "home": g.get("home"), "date": g.get("date")}
    row.update({k: st.get(k, 0.0) for k in RAW})
    return row


def parse_scoreboard(sb):
    """ESPN NBA-scoreboard -> per ESPN game-id de wedstrijdstatus."""
    games = {}
    for ev in sb.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        st = comp.get("status") or ev.get("status") or {}
        stype = st.get("type", {})
        info = {
            "id": str(ev.get("id")), "state": stype.get("state"),  # pre / in / post
            "detail": stype.get("shortDetail"), "period": st.get("period"),
            "clock": st.get("displayClock"), "date": ev.get("date"), "teams": [],
        }
        for c in comp.get("competitors", []):
            t = c.get("team", {})
            info["teams"].append({"abbrev": t.get("abbreviation"), "score": c.get("score"),
                                  "home": c.get("homeAway") == "home"})
        if info["state"] == "post":
            frac = 1.0
        elif info["state"] == "in":
            per = info["period"] or 1
            try:
                m, s = (info["clock"] or "12:00").split(":")
                left = int(m) * 60 + float(s)
            except ValueError:
                left = 720
            frac = 1.0 if per > 4 else min(0.99, ((per - 1) * 720 + (720 - left)) / 2880)
        else:
            frac = 0.0
        info["frac"] = round(frac, 3)
        games[info["id"]] = info
    return games
