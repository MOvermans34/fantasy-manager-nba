"""
Basketball Reference: advanced stats per speler en tempo/defensie per team.

We halen per verversing maar twee pagina's op (ruim binnen de limieten van de site:
robots.txt vraagt 3 s tussen verzoeken, Sports Reference blokkeert boven ~20 per minuut):
  /leagues/NBA_{jaar}_advanced.html  -> PER, TS%, USG%, AST%, REB%, STL%, BLK%, TOV%, BPM, VORP, WS/48
  /leagues/NBA_{jaar}.html           -> per team: tempo, ORtg/DRtg en wat tegenstanders per wedstrijd scoren
Het resultaat wordt op schijf bewaard, zodat een herstart geen nieuwe verzoeken doet.
"""
import json
import os
import re
import time
import urllib.request
from html.parser import HTMLParser

from .news import norm_name

BASE = "https://www.basketball-reference.com/leagues/"
# Terugval: snapshot die een GitHub Action twee keer per dag ververst (zie .github/workflows/bbref.yml)
SNAPSHOT_URL = "https://raw.githubusercontent.com/MOvermans34/fantasy-manager-nba/main/snapshots/bbref_{season}.json"
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "snapshots")
UA = "fantasy-manager-nba/1.0 (prive league-app; 2 verzoeken per verversing)"

# BBRef-afkortingen -> ESPN-afkortingen (fantasy-API)
TEAM_MAP = {"BRK": "BKN", "CHO": "CHA", "GSW": "GS", "NOP": "NO", "NYK": "NY", "PHO": "PHX", "SAS": "SA",
            "UTA": "UTAH", "WAS": "WSH"}
TEAM_NAMES = {  # voor de teamtabellen (die gebruiken volledige namen)
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GS", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM", "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NO", "New York Knicks": "NY",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SA", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTAH", "Washington Wizards": "WSH",
}
PLAYER_STATS = ["per", "ts_pct", "usg_pct", "ast_pct", "trb_pct", "stl_pct", "blk_pct", "tov_pct",
                "obpm", "dbpm", "bpm", "vorp", "ws_per_48", "fg3a_per_fga_pct", "fta_per_fga_pct", "mp", "games"]
# opponent per game -> ESPN-stat-id
OPP_MAP = {"opp_pts": "0", "opp_trb": "6", "opp_ast": "3", "opp_stl": "2", "opp_blk": "1", "opp_fg3": "17",
           "opp_tov": "11", "opp_fg": "13", "opp_fga": "14", "opp_ft": "15", "opp_fta": "16"}


class _Tables(HTMLParser):
    """Leest <table id=...> met cellen op hun data-stat-naam."""

    def __init__(self, want):
        super().__init__()
        self.want, self.tables = set(want), {}
        self.cur = self.row = self.cell = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and a.get("id") in self.want:
            self.cur = self.tables.setdefault(a["id"], [])
        elif self.cur is not None and tag == "tr":
            self.row = {} if "thead" not in (a.get("class") or "") else None
        elif self.row is not None and tag in ("td", "th") and a.get("data-stat"):
            self.cell, self.text = a["data-stat"], []
            if a.get("data-append-csv"):
                self.row["_id"] = a["data-append-csv"]

    def handle_data(self, data):
        if self.cell:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell and self.row is not None:
            self.row[self.cell] = "".join(self.text).strip()
            self.cell = None
        elif tag == "tr" and self.row:
            self.cur.append(self.row)
            self.row = None
        elif tag == "table" and self.cur is not None:
            self.cur = None


def tables(html, ids):
    p = _Tables(ids)
    p.feed(html.replace("<!--", "").replace("-->", ""))  # veel tabellen staan in HTML-commentaar
    return p.tables


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_players(html):
    rows = tables(html, ["advanced"]).get("advanced", [])
    out = {}
    for r in rows:
        name = r.get("name_display") or r.get("player")
        if not name or name == "Player":
            continue
        team = r.get("team_name_abbr") or r.get("team_id") or ""
        key = norm_name(name)
        rec = {k: fnum(r.get(k)) for k in PLAYER_STATS}
        rec.update({"name": name, "team": TEAM_MAP.get(team, team), "bbrefId": r.get("_id"),
                    "pos": r.get("pos")})
        # spelers die gewisseld zijn: eerst een totaalregel (2TM/3TM), die heeft voorrang
        if key in out and out[key]["team"] in ("2TM", "3TM", "4TM", "TOT"):
            continue
        out[key] = rec
    return out


def parse_teams(html):
    t = tables(html, ["advanced-team", "per_game-opponent"])
    teams = {}
    for r in t.get("advanced-team", []):
        abbr = TEAM_NAMES.get(re.sub(r"\*$", "", r.get("team", "")).strip())
        if abbr:
            teams[abbr] = {"pace": fnum(r.get("pace")), "ortg": fnum(r.get("off_rtg")), "drtg": fnum(r.get("def_rtg")),
                           "nrtg": fnum(r.get("net_rtg")), "wins": fnum(r.get("wins")), "losses": fnum(r.get("losses"))}
    for r in t.get("per_game-opponent", []):
        abbr = TEAM_NAMES.get(re.sub(r"\*$", "", r.get("team", "")).strip())
        if abbr:
            teams.setdefault(abbr, {})["opp"] = {sid: fnum(r.get(k)) for k, sid in OPP_MAP.items()}
            teams[abbr]["g"] = fnum(r.get("g"))
    return teams


def scrape(season):
    players = parse_players(fetch(f"{BASE}NBA_{season}_advanced.html"))
    time.sleep(6)  # ruim boven de crawl-delay van 3 s
    teams = parse_teams(fetch(f"{BASE}NBA_{season}.html"))
    return {"players": players, "teams": teams, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


class BBRef:
    def __init__(self, season, data_dir, log=print, max_age=12 * 3600):
        self.season, self.log, self.max_age = season, log, max_age
        self.path = os.path.join(data_dir, f"bbref_{season}.json")
        self.players, self.teams, self.updated = {}, {}, None
        self.source, self.error = None, None
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.players, self.teams, self.updated = d["players"], d["teams"], d.get("updated")
            self.source = "cache"
        except Exception:
            pass

    def age(self):
        try:
            return time.time() - os.path.getmtime(self.path)
        except OSError:
            return float("inf")

    def refresh(self, force=False):
        """Vers ophalen; lukt dat niet (sommige cloudservers worden door de site geblokkeerd),
        dan de snapshot uit de GitHub-repo, en anders de snapshot die in het image zit."""
        if not force and self.age() < self.max_age and self.players:
            return False
        errors = []
        for source, load in (("basketball-reference.com", self._direct), ("snapshot (GitHub)", self._remote),
                             ("snapshot (image)", self._bundled)):
            try:
                d = load()
                if not d:
                    continue
                players, teams = d["players"], d["teams"]
                if len(players) < 100 or len(teams) < 30:
                    raise RuntimeError(f"onvolledige data ({len(players)} spelers, {len(teams)} teams)")
            except Exception as e:
                errors.append(f"{source}: {e}")
                self.log(f"LET OP Basketball Reference via {source} mislukt: {e}")
                continue
            self.players, self.teams = players, teams
            self.updated = d.get("updated") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.source, self.error = source, "; ".join(errors) or None
            self._save()
            self.log(f"Basketball Reference via {source}: {len(players)} spelers, {len(teams)} teams "
                     f"(stand {self.updated}).")
            return True
        self.error = "; ".join(errors) or "geen bron beschikbaar"
        raise RuntimeError(self.error)

    def _direct(self):
        return scrape(self.season)

    def _remote(self):
        url = os.environ.get("BBREF_SNAPSHOT_URL", SNAPSHOT_URL).format(season=self.season)
        if not url:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def _bundled(self):
        path = os.path.join(SNAPSHOT_DIR, f"bbref_{self.season}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"players": self.players, "teams": self.teams, "updated": self.updated}, f)
        os.replace(tmp, self.path)

    def status(self):
        return {"updated": self.updated, "source": self.source, "error": self.error,
                "players": len(self.players), "teams": len(self.teams)}

    def player(self, name, team=None):
        return self.players.get(norm_name(name))

    def league_avg(self):
        """Gemiddelde tempo en toegestane stats per wedstrijd over alle teams."""
        ts = [t for t in self.teams.values() if t.get("pace") and t.get("opp")]
        if not ts:
            return None
        avg = {"pace": sum(t["pace"] for t in ts) / len(ts), "drtg": sum(t["drtg"] for t in ts) / len(ts)}
        avg["opp"] = {sid: sum(t["opp"][sid] or 0 for t in ts) / len(ts) for sid in OPP_MAP.values()}
        return avg


# ----------------------------------------------------------------------
# Tegenstanderfactoren
# ----------------------------------------------------------------------
SHRINK = 0.5          # vroege seizoensdata is ruisig: halveer de afwijking t.o.v. de league
CLAMP = (0.85, 1.15)


def _clamp(x):
    return max(CLAMP[0], min(CLAMP[1], x))


def opponent_factors(teams, own, opp):
    """Vermenigvuldigers per ESPN-stat-id voor een wedstrijd van een speler van `own` tegen `opp`.

    - tempo: wedstrijdtempo ~ gemiddelde van beide teams; de eigen stats van een speler zijn
      gespeeld op (eigen tempo + gemiddelde tegenstander) / 2.
    - defensie: wat de tegenstander per wedstrijd weggeeft t.o.v. het league-gemiddelde
      (zit tempo al in, dus die combineren we niet nog eens voor tellende stats).
    """
    ts = [t for t in teams.values() if t.get("pace") and t.get("opp")]
    o, s = teams.get(opp), teams.get(own)
    if not ts or not o or not o.get("opp") or not o.get("pace"):
        return None
    lg_pace = sum(t["pace"] for t in ts) / len(ts)
    lg = {k: sum((t["opp"].get(k) or 0) for t in ts) / len(ts) for k in OPP_MAP.values()}
    own_pace = (s or {}).get("pace") or lg_pace
    pace = (own_pace + o["pace"]) / (own_pace + lg_pace)

    def rel(k):
        v, m = o["opp"].get(k), lg.get(k)
        return 1 + SHRINK * (v / m - 1) if v and m else 1.0

    f = {k: _clamp(rel(k)) for k in ("0", "6", "3", "2", "1", "17", "11", "14", "16", "15")}
    # FGM: pogingen x afwijking in toegestaan FG%
    fg_opp = o["opp"]["13"] / o["opp"]["14"] if o["opp"].get("14") else None
    fg_lg = lg["13"] / lg["14"] if lg.get("14") else None
    f["13"] = _clamp(f["14"] * (1 + SHRINK * (fg_opp / fg_lg - 1))) if fg_opp and fg_lg else f["14"]
    f["15"] = f["16"]  # FT% is van de schutter zelf
    f["_pace"] = round(pace, 3)
    # samenvattend: gemiddelde factor over de tellende categorieën (TO telt omgekeerd)
    f["_ease"] = round(sum(f[k] for k in ("0", "6", "3", "2", "1", "17")) / 6, 3)
    return f


if __name__ == "__main__":
    # Snapshot maken (GitHub Action): python -m ffd.bbref [seizoen] [uitvoerbestand]
    import datetime as _dt
    import sys
    today = _dt.date.today()
    season = int(sys.argv[1]) if len(sys.argv) > 1 else (today.year + 1 if today.month >= 9 else today.year)
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(SNAPSHOT_DIR, f"bbref_{season}.json")
    d = scrape(season)
    if len(d["players"]) < 100 or len(d["teams"]) < 30:
        sys.exit(f"onvolledige data: {len(d['players'])} spelers, {len(d['teams'])} teams")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print(f"{out}: {len(d['players'])} spelers, {len(d['teams'])} teams")
