"""
Nieuws-ETL: extract (ESPN + Rotowire) -> transform (Polars) -> load (Postgres).

Tabellen (worden automatisch aangemaakt):
    news_item    één rij per artikel/update (id = bron-id, idempotent)
    news_player  koppeling artikel <-> speler (met naam/positie/team op dat moment)
    etl_run      logboek per run: opgehaald, nieuw geladen, status, foutmelding

De webapp leest het nieuws met Polars (pl.read_database) terug.

Losse run (bijv. als cron job, zonder webserver):
    python -m ffd.etl_news            # league uit config.json / omgevingsvariabelen
    python -m ffd.etl_news --demo     # demoleague
"""
import json
import os
import threading
import time

import polars as pl
import psycopg

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_item (
    id            text PRIMARY KEY,
    source        text NOT NULL,
    via           text,
    kind          text,
    author        text,
    headline      text NOT NULL,
    description   text,
    link          text,
    raw_type      text,
    published_at  timestamptz,
    first_seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS news_item_published_idx ON news_item (published_at DESC);

CREATE TABLE IF NOT EXISTS news_player (
    news_id     text   NOT NULL REFERENCES news_item(id) ON DELETE CASCADE,
    player_id   bigint NOT NULL,
    player_name text,
    pos         text,
    pro_team    text,
    PRIMARY KEY (news_id, player_id)
);
CREATE INDEX IF NOT EXISTS news_player_player_idx ON news_player (player_id);

CREATE TABLE IF NOT EXISTS etl_run (
    id           bigserial PRIMARY KEY,
    job          text NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text NOT NULL DEFAULT 'running',
    extracted    integer,
    loaded_items integer,
    loaded_links integer,
    message      text
);
"""

ITEM_COLS = ["id", "source", "via", "kind", "author", "headline", "description", "link",
             "raw_type", "published_at"]
LINK_COLS = ["news_id", "player_id", "player_name", "pos", "pro_team"]

ITEM_SCHEMA = {
    "id": pl.Utf8, "source": pl.Utf8, "via": pl.Utf8, "kind": pl.Utf8, "author": pl.Utf8,
    "headline": pl.Utf8, "description": pl.Utf8, "link": pl.Utf8, "type": pl.Utf8,
    "published": pl.Utf8, "player_ids": pl.List(pl.Int64),
}

# Kolommen zoals de frontend ze verwacht (published als ISO-tekst in UTC)
SELECT_ITEM = """
    i.id, i.source, i.via, i.kind, i.author, i.headline, i.description, i.link,
    i.raw_type AS type,
    to_char(i.published_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS published
"""


# ----------------------------------------------------------------------
# Transform (Polars)
# ----------------------------------------------------------------------
def transform(items, players):
    """Ruwe items (dicts) -> (news_item-frame, news_player-frame).

    - lege/kapotte items eruit, witruimte opschonen
    - beschrijving leeg als die gelijk is aan de kop
    - publicatietijd naar UTC-timestamp
    - ontdubbelen op id (spelerkoppelingen van dubbele items samengevoegd)
    - koppelingen verrijkt met spelernaam, positie en NBA-team
    """
    rows = [{k: it.get(k) for k in ITEM_SCHEMA} | {"player_ids": [int(x) for x in it.get("player_ids") or []]}
            for it in items if it.get("id") and it.get("headline")]
    if not rows:
        return pl.DataFrame(schema={c: pl.Utf8 for c in ITEM_COLS}), pl.DataFrame(schema={c: pl.Utf8 for c in LINK_COLS})
    df = pl.DataFrame(rows, schema=ITEM_SCHEMA)
    df = (
        df.with_columns(
            pl.col("headline").str.strip_chars(),
            pl.col("description").str.strip_chars(),
            pl.col("link").str.replace(r"\.com//", ".com/"),
            pl.col("published").str.to_datetime(time_zone="UTC", strict=False).alias("published_at"),
        )
        .with_columns(
            pl.when(pl.col("description").is_in(["", None]) | (pl.col("description") == pl.col("headline")))
            .then(None).otherwise(pl.col("description")).alias("description"),
        )
    )
    links = (
        df.select(pl.col("id").alias("news_id"), pl.col("player_ids").alias("player_id"))
        .explode("player_id").drop_nulls("player_id").unique()
    )
    if players:
        dim = pl.DataFrame([{"player_id": int(p["id"]), "player_name": p.get("name"), "pos": p.get("pos"),
                             "pro_team": p.get("team")} for p in players.values()],
                           schema={"player_id": pl.Int64, "player_name": pl.Utf8, "pos": pl.Utf8, "pro_team": pl.Utf8})
        links = links.join(dim, on="player_id", how="left")
    else:
        links = links.with_columns(player_name=pl.lit(None, pl.Utf8), pos=pl.lit(None, pl.Utf8), pro_team=pl.lit(None, pl.Utf8))
    items_df = (df.rename({"type": "raw_type"})
                .sort("published_at", descending=True, nulls_last=True)
                .unique("id", keep="first", maintain_order=True)
                .select(ITEM_COLS))
    return items_df, links.select(LINK_COLS)


# ----------------------------------------------------------------------
# Load + lezen (Postgres)
# ----------------------------------------------------------------------
class PostgresNewsStore:
    """Zelfde interface als NewsStore (JSON), maar met Postgres + Polars."""

    def __init__(self, url, log=print, legacy_json=None):
        self.url = url
        self.log = log
        self._cache = {}
        self._lock = threading.Lock()
        self._schema_ok = False
        self._legacy = legacy_json
        try:
            self._ensure_schema()
        except Exception as e:  # database (nog) niet bereikbaar: app start toch
            self.log(f"Nieuws: Postgres nog niet bereikbaar ({e}); nieuwe poging bij de volgende ETL-run.")

    def _ensure_schema(self):
        if self._schema_ok:
            return
        with self._conn() as conn:
            conn.execute(SCHEMA)
        self._schema_ok = True
        self.log("Nieuws: Postgres-opslag actief.")
        if self._legacy and os.path.exists(self._legacy):
            self._import_legacy(self._legacy)

    def _conn(self, autocommit=False):
        return psycopg.connect(self.url, autocommit=autocommit, connect_timeout=10)

    # -- load ------------------------------------------------------------
    def ingest(self, items, players=None, extracted=None, message=None, job="news"):
        """Transform + load in één transactie, met een etl_run-logregel."""
        t0 = time.time()
        self._ensure_schema()
        with self._conn(autocommit=True) as conn:
            run_id = conn.execute("INSERT INTO etl_run (job, extracted) VALUES (%s, %s) RETURNING id",
                                  (job, extracted if extracted is not None else len(items))).fetchone()[0]
        try:
            items_df, links_df = transform(items, players)
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute("CREATE TEMP TABLE stg_item (LIKE news_item INCLUDING DEFAULTS) ON COMMIT DROP")
                cur.execute("CREATE TEMP TABLE stg_link (LIKE news_player) ON COMMIT DROP")
                with cur.copy(f"COPY stg_item ({', '.join(ITEM_COLS)}) FROM STDIN") as cp:
                    for row in items_df.iter_rows():
                        cp.write_row(row)
                with cur.copy(f"COPY stg_link ({', '.join(LINK_COLS)}) FROM STDIN") as cp:
                    for row in links_df.iter_rows():
                        cp.write_row(row)
                cur.execute(f"""INSERT INTO news_item ({', '.join(ITEM_COLS)})
                                SELECT {', '.join(ITEM_COLS)} FROM stg_item
                                ON CONFLICT (id) DO NOTHING""")
                new_items = cur.rowcount
                cur.execute(f"""INSERT INTO news_player ({', '.join(LINK_COLS)})
                                SELECT DISTINCT ON (news_id, player_id) {', '.join(LINK_COLS)} FROM stg_link
                                WHERE news_id IN (SELECT id FROM news_item)
                                ON CONFLICT (news_id, player_id) DO NOTHING""")
                new_links = cur.rowcount
                # Verrijken: koppelingen zonder spelergegevens (bijv. uit een import)
                # aanvullen vanuit de actuele spelerslijst.
                if players:
                    cur.execute("CREATE TEMP TABLE stg_dim (player_id bigint, player_name text, pos text, "
                                "pro_team text) ON COMMIT DROP")
                    with cur.copy("COPY stg_dim FROM STDIN") as cp:
                        for p in players.values():
                            cp.write_row((int(p["id"]), p.get("name"), p.get("pos"), p.get("team")))
                    cur.execute("""UPDATE news_player np
                                   SET player_name = d.player_name, pos = d.pos, pro_team = d.pro_team
                                   FROM stg_dim d
                                   WHERE np.player_id = d.player_id AND np.player_name IS NULL""")
                    enriched = cur.rowcount
                    if enriched:
                        self.log(f"ETL nieuws: {enriched} spelerkoppelingen verrijkt.")
            status = "ok" if not message else "ok_met_waarschuwing"
            self._finish(run_id, status, new_items, new_links, message)
            with self._lock:
                self._cache.clear()
            self.log(f"ETL nieuws: {len(items_df)} items getransformeerd, {new_items} nieuw, "
                     f"{new_links} nieuwe spelerkoppelingen ({time.time() - t0:.1f}s).")
            return new_items
        except Exception as e:
            self._finish(run_id, "fout", None, None, f"{type(e).__name__}: {e}")
            raise

    def _finish(self, run_id, status, items, links, message):
        try:
            with self._conn(autocommit=True) as conn:
                conn.execute("""UPDATE etl_run SET finished_at = now(), status = %s, loaded_items = %s,
                                loaded_links = %s, message = %s WHERE id = %s""",
                             (status, items, links, (message or "")[:1000] or None, run_id))
        except Exception as e:
            self.log(f"ETL-log bijwerken mislukt: {e}")

    def _import_legacy(self, path):
        with self._conn() as conn:
            if conn.execute("SELECT EXISTS (SELECT 1 FROM news_item)").fetchone()[0]:
                return
        try:
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            return
        self.log(f"Nieuws: {len(items)} items uit {os.path.basename(path)} importeren…")
        self.ingest(items, extracted=len(items), job="import_json")

    # -- lezen met Polars ------------------------------------------------
    def _read(self, key, query, params):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < 30:
                return hit[1]
        try:
            self._ensure_schema()
            with self._conn(autocommit=True) as conn:
                df = pl.read_database(query, connection=conn, execute_options={"params": params})
        except Exception as e:  # lezen mag de app nooit laten crashen
            self.log(f"Nieuws lezen uit Postgres mislukt: {e}")
            return hit[1] if hit else []
        out = df.to_dicts()
        with self._lock:
            self._cache[key] = (time.time(), out)
        return out

    def for_players(self, ids, limit=150):
        ids = sorted({int(i) for i in ids})
        if not ids:
            return []
        q = f"""
            WITH hits AS (
                SELECT DISTINCT news_id FROM news_player WHERE player_id = ANY(%(ids)s)
            )
            SELECT {SELECT_ITEM},
                   array_agg(p.player_id ORDER BY p.player_id) AS player_ids
            FROM news_item i
            JOIN hits h ON h.news_id = i.id
            JOIN news_player p ON p.news_id = i.id
            GROUP BY i.id
            ORDER BY i.published_at DESC NULLS LAST
            LIMIT %(limit)s"""
        return self._read(("players", tuple(ids), limit), q, {"ids": ids, "limit": limit})

    def latest(self, limit=100):
        q = f"""
            SELECT {SELECT_ITEM},
                   coalesce(array_agg(p.player_id) FILTER (WHERE p.player_id IS NOT NULL), '{{}}') AS player_ids
            FROM news_item i
            LEFT JOIN news_player p ON p.news_id = i.id
            GROUP BY i.id
            ORDER BY i.published_at DESC NULLS LAST
            LIMIT %(limit)s"""
        return self._read(("latest", limit), q, {"limit": limit})

    def etl_status(self, n=5):
        q = """SELECT id, job, status, extracted, loaded_items, loaded_links, message,
                      to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS started,
                      round(extract(epoch FROM finished_at - started_at)::numeric, 1)::float AS seconds
               FROM etl_run ORDER BY id DESC LIMIT %(n)s"""
        runs = self._read(("etl", n), q, {"n": n})
        totals = self._read(("totals",), """SELECT (SELECT count(*) FROM news_item) AS items,
                                                   (SELECT count(*) FROM news_player) AS links""", {})
        if not self._schema_ok:
            return {"backend": "postgres", "error": "database niet bereikbaar", "runs": [], "totals": {}}
        return {"backend": "postgres", "runs": runs, "totals": totals[0] if totals else {}}


def main():
    import argparse
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from server import load_config  # noqa: E402
    from ffd.engine import Engine  # noqa: E402

    ap = argparse.ArgumentParser(description="Nieuws-ETL: ESPN/Rotowire -> Postgres")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--batch", type=int, help="max. aantal spelers per run (standaard 20)")
    args = ap.parse_args()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = load_config(os.path.join(here, "config.json"))
    if not cfg.get("database_url"):
        sys.exit("Zet DATABASE_URL (of database_url in config.json).")
    if args.batch:
        cfg["news_batch"] = args.batch
    eng = Engine(cfg, demo=args.demo, data_dir=os.environ.get("DATA_DIR", os.path.join(here, "data")))
    eng.refresh_fantasy()
    eng.refresh_news()
    print(json.dumps(eng.news.etl_status(1)["runs"], indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()

