#!/usr/bin/env python3
"""
Fantasy Manager NBA: web-app voor je hele ESPN fantasy basketball league (H2H-categorieën).

Lokaal:
    python3 server.py              # league uit config.json
    python3 server.py --demo       # demoleague, geen cookies nodig

Online (server/hosting): configureer via omgevingsvariabelen, zie README.md:
    LEAGUE_ID, ESPN_S2, ESPN_SWID, ACCESS_CODE, (SEASON, TEAM_ID, PORT, HOST, DATA_DIR)
"""
import argparse
import datetime as dt
import gzip
import hmac
import json
import mimetypes
import os
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ffd.engine import Engine  # noqa: E402

WEB = os.path.join(HERE, "web")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")

ENV_MAP = {  # omgevingsvariabele -> config-sleutel
    "LEAGUE_ID": "league_id", "TEAM_ID": "team_id", "SEASON": "season",
    "ESPN_S2": "espn_s2", "ESPN_SWID": "swid", "ACCESS_CODE": "access_code",
    "DATABASE_URL": "database_url",
}


def load_config(path):
    today = dt.date.today()
    # ESPN telt een NBA-seizoen naar het jaar waarin het eindigt (2026-27 = 2027)
    cfg = {"season": today.year + 1 if today.month >= 9 else today.year, "news_per_player": 8}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    for env, key in ENV_MAP.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def make_handler(engine, access_code):
    code_b = (access_code or "").encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = "FantasyManagerNBA/1.0"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype, cache="no-store", enc=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if enc:
                self.send_header("Content-Encoding", enc)
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
            if len(body) > 2048 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
                body = gzip.compress(body, 5)
                self._send(code, body, "application/json; charset=utf-8", enc="gzip")
            else:
                self._send(code, body, "application/json; charset=utf-8")

        def _static(self, rel):
            path = os.path.normpath(os.path.join(WEB, rel))
            if not path.startswith(WEB + os.sep) or not os.path.isfile(path):
                path = os.path.join(WEB, "index.html")  # SPA-fallback
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "svg+xml")):
                ctype += "; charset=utf-8"
            with open(path, "rb") as f:
                self._send(200, f.read(), ctype, "no-cache")

        def _authed(self):
            if not code_b:
                return True
            given = (self.headers.get("X-Access-Code") or "").encode()
            return hmac.compare_digest(given, code_b)

        def _team(self, q):
            try:
                tid = int(q.get("team", [engine.default_team])[0])
            except (TypeError, ValueError):
                tid = engine.default_team
            return tid if engine.team_ok(tid) else engine.default_team

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            parts = [unquote(x) for x in u.path.strip("/").split("/") if x]
            try:
                if not parts or parts[0] != "api":
                    return self._static("/".join(parts) or "index.html")
                what = parts[1] if len(parts) > 1 else ""
                if what == "config":  # openbaar: weet de frontend of er een code nodig is
                    return self._json({"needsCode": bool(code_b), "leagueName": engine.meta.get("leagueName")})
                if not self._authed():
                    return self._json({"error": "toegangscode onjuist"}, 401)
                if what == "teams":
                    return self._json(engine.teams_out())
                tid = self._team(q)
                if what == "state":
                    return self._json(engine.state_out(tid))
                if what == "live":
                    return self._json(engine.live_out(tid))
                if what == "league":
                    return self._json(engine.league_out())
                if what == "news":
                    return self._json(engine.news_out(tid))
                if what == "player" and len(parts) > 2:
                    out = engine.player_out(int(parts[2]), tid)
                    return self._json(out or {"error": "speler niet gevonden"}, 200 if out else 404)
                if what == "schedule":
                    return self._json(engine.schedule_out())
                return self._json({"error": "onbekend"}, 404)
            except Exception as e:
                return self._json({"error": str(e)}, 500)

        def do_POST(self):
            if not self._authed():
                return self._json({"error": "toegangscode onjuist"}, 401)
            if self.path.startswith("/api/refresh"):
                ok = engine.force_refresh()
                return self._json({"ok": ok, "msg": None if ok else "Net ververst; probeer over een minuut opnieuw."})
            return self._json({"error": "onbekend"}, 404)

    return Handler


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="Fantasy Manager NBA: ESPN fantasy basketball league-app")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--demo", action="store_true", help="demoleague, geen cookies nodig")
    ap.add_argument("--period", type=int, help="speeldag (scoring period) forceren (standaard: vandaag)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default=os.environ.get("HOST"), help="bind-adres (standaard 127.0.0.1; online 0.0.0.0)")
    ap.add_argument("--lan", action="store_true", help="bereikbaar op je lokale netwerk (= --host 0.0.0.0)")
    ap.add_argument("--no-open", action="store_true", help="browser niet automatisch openen")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if not args.demo:
        if not cfg.get("league_id"):
            sys.exit("Geen league_id: zet LEAGUE_ID (of league_id in config.json), of start met --demo.")
        if not (cfg.get("espn_s2") and cfg.get("swid")):
            print("Let op: geen ESPN-cookies gevonden (ESPN_S2/ESPN_SWID). Een privé-league is dan niet leesbaar.")
    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    code = cfg.get("access_code")
    if host not in ("127.0.0.1", "localhost") and not code and not args.demo:
        print("WAARSCHUWING: de app is bereikbaar vanaf andere apparaten maar heeft geen ACCESS_CODE. "
              "Iedereen met het adres kan dan je league zien.")

    data_dir = os.environ.get("DATA_DIR", os.path.join(HERE, "data"))
    engine = Engine(cfg, demo=args.demo, period=args.period, data_dir=data_dir)
    engine.start()

    srv = ThreadingHTTPServer((host, args.port), make_handler(engine, code))
    url = f"http://localhost:{args.port}"
    print(f"\nFantasy Manager NBA draait op {url} (bind {host}){' · toegangscode actief' if code else ''}")
    if args.lan and lan_ip():
        print(f"In je netwerk: http://{lan_ip()}:{args.port}")
    print("Stoppen: Ctrl+C\n")
    if not args.no_open and host in ("127.0.0.1", "localhost"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nGestopt.")


if __name__ == "__main__":
    main()
