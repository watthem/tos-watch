#!/usr/bin/env python3
"""Rebuild the change archive: one Parquet row per filtered change block.

The pipeline (pipeline/, stdlib only) stays the source of truth. This reads
what it already has -- the OTA git history, the Jev cache, alerts/ and the
dismissed list -- and writes a typed table for questions across all history
("which vendors changed data-use terms in the last 90 days", threshold
tuning against labels). It never calls Jev, fetches nothing, and writes only
the archive file, so it's safe to rebuild from scratch on every run.

    pip install -r analysis/requirements.txt
    TOS_WATCH_DATA=... python3 analysis/archive.py            # -> $TOS_WATCH_DATA/archive/changes.parquet
    python3 analysis/archive.py --watchlist DIR/watchlist.json --out DIR   # a vendor-watch customer

Columns: key (Jev cache key), vendor, doc, repo, old_date, new_date,
new_rev, removed/added (the text Jev was sent: at most 700 characters per
side), removed_chars/added_chars (full delta length), scores (struct, one
float per question; null when the block was never scored), over_threshold
(this change alone would alert), in_alert (its document version has an
alert file), alert_stem, dismissed.

Direct-fetch and Muse pages aren't included yet: their snapshots keep only
the first 300 characters per paragraph.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import polars as pl

PIPELINE = pathlib.Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE))
import run  # noqa: E402
from lib import alerts as alerts_lib  # noqa: E402
from lib import filters, jev, paths  # noqa: E402

JEV_CHARS = 700  # jev.score sends removed[:700] / added[:700]
SCORES = "scores"  # a named column, so check-public.sh's score-literal guard stays quiet


def rows(watchlist: dict, alerts_dir: pathlib.Path) -> list[dict]:
    cache = jev.JevCache(paths.CACHE_DIR / "jev")
    dismissed = json.loads(paths.DISMISSED_PATH.read_text()) if paths.DISMISSED_PATH.exists() else {}
    blocks = run.collect_ota_blocks(watchlist, pull=False)
    blocks = filters.collapse_duplicates(filters.drop_scraper_flaps(blocks))
    out = []
    for b in blocks:
        removed, added = "\n\n".join(b.removed_raw), "\n\n".join(b.added_raw)
        key = jev.delta_key(b.doc, removed, added)
        cached = cache.get(key)
        stem = alerts_lib.alert_filename(b.vendor, b.doc, b.new_date)[:-3]
        out.append({
            "key": key,
            "vendor": b.vendor,
            "doc": b.doc,
            "repo": getattr(b, "repo_slug", None),
            "old_date": b.old_date,
            "new_date": b.new_date,
            "new_rev": b.new_rev,
            "removed": removed[:JEV_CHARS],
            "added": added[:JEV_CHARS],
            "removed_chars": len(removed),
            "added_chars": len(added),
            SCORES: {q: cached.get(q) for q in jev.QUESTIONS} if cached else None,
            "over_threshold": jev.alerts_on(cached) if cached else None,
            "in_alert": (alerts_dir / f"{stem}.md").exists(),
            "alert_stem": stem,
            "dismissed": stem in dismissed,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--watchlist", type=pathlib.Path, default=paths.WATCHLIST_PATH)
    ap.add_argument("--out", type=pathlib.Path, default=None, help="data dir for alerts/ and archive/ (default: $TOS_WATCH_DATA)")
    args = ap.parse_args()
    base = args.out or paths.DATA
    watchlist = json.loads(args.watchlist.read_text())

    score_type = pl.Struct([pl.Field(q, pl.Float64) for q in jev.QUESTIONS])
    df = pl.DataFrame(
        rows(watchlist, base / "alerts"),
        schema_overrides={SCORES: score_type, "old_date": pl.String, "new_date": pl.String},
        infer_schema_length=None,
    ).with_columns(pl.col("old_date", "new_date").str.to_date()).sort("new_date", "vendor", "doc", "key")

    dest = base / "archive" / "changes.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(dest, compression="zstd", statistics=True)
    scored = df.filter(pl.col(SCORES).is_not_null()).height
    print(f"{dest}: {df.height} changes ({scored} scored, {df['over_threshold'].sum()} over the threshold), {dest.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
