"""Nieuws van ESPN (per speler) en Rotowire (RSS), lokaal opgeslagen."""
import email.utils
import json
import os
import re
import threading
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET

ROTOWIRE_RSS = "https://www.rotowire.com/rss/news.php?sport=NBA"
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)


def norm_name(n):
    n = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode()
    n = SUFFIX.sub("", n.lower())
    return re.sub(r"[^a-z]", "", n)


def _iso(pubdate):
    try:
        return email.utils.parsedate_to_datetime(pubdate).astimezone(
            tz=__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def fetch_rotowire():
    req = urllib.request.Request(ROTOWIRE_RSS, headers={"User-Agent": "fantasy-manager-nba/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        desc = re.sub(r"\s*Visit RotoWire\.com for more analysis on this update\.\s*$", "", desc).strip()
        player = title.split(":", 1)[0].strip() if ":" in title else None
        headline = title.split(":", 1)[1].strip() if ":" in title else title
        items.append({
            "id": "rw-" + (it.findtext("guid") or title),
            "source": "Rotowire", "player_name": player, "headline": headline,
            "description": desc, "link": (it.findtext("link") or "").replace(".com//", ".com/"),
            "published": _iso(it.findtext("pubDate")), "type": "Update", "kind": "Update",
        })
    return items


class NewsStore:
    """Bewaart nieuwsitems op schijf; Rotowire-feed toont alleen de laatste items."""

    def __init__(self, path, keep=4000):
        self.path = path
        self.keep = keep
        self.lock = threading.Lock()
        self.items = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.items = {i["id"]: i for i in json.load(f)}
            except Exception:
                self.items = {}

    def add(self, items):
        new = 0
        with self.lock:
            for it in items:
                if it["id"] not in self.items:
                    new += 1
                    self.items[it["id"]] = it
                else:  # spelerkoppelingen samenvoegen
                    old = self.items[it["id"]]
                    old["player_ids"] = sorted(set(old.get("player_ids", [])) | set(it.get("player_ids", [])))
        return new

    def save(self):
        with self.lock:
            lst = sorted(self.items.values(), key=lambda i: i.get("published") or "", reverse=True)[: self.keep]
            self.items = {i["id"]: i for i in lst}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lst, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def ingest(self, items, players=None, extracted=None, message=None, job="news"):
        """Zelfde interface als PostgresNewsStore: laden + opslaan."""
        new = self.add(items)
        self.save()
        self.last_run = {"job": job, "status": "ok", "extracted": extracted if extracted is not None else len(items),
                         "loaded_items": new, "message": message}
        return new

    def etl_status(self, n=5):
        with self.lock:
            total = len(self.items)
        run = getattr(self, "last_run", None)
        return {"backend": "json", "runs": [run] if run else [], "totals": {"items": total}}

    def for_players(self, ids, limit=150):
        ids = set(ids)
        with self.lock:
            lst = [i for i in self.items.values() if ids & set(i.get("player_ids", []))]
        lst.sort(key=lambda i: i.get("published") or "", reverse=True)
        return lst[:limit]

    def latest(self, limit=100):
        with self.lock:
            lst = sorted(self.items.values(), key=lambda i: i.get("published") or "", reverse=True)
        return lst[:limit]


def match_rotowire(items, name_to_ids):
    """Koppel Rotowire-items aan ESPN-speler-id's op (genormaliseerde) naam."""
    for it in items:
        key = norm_name(it.get("player_name"))
        it["player_ids"] = sorted(name_to_ids.get(key, []))
    return items
