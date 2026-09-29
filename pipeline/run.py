#!/usr/bin/env python3
"""tos.watch pipeline: fetch, diff, score, filter, alert.

For each watched document (OTA-tracked policy pages, plus the Muse privacy
page fetched directly):

1. Pull the latest data (git pull for OTA repos, HTTP fetch for web pages).
2. Diff every consecutive pair of versions at the paragraph level (full
   history — this both backfills and keeps up to date; git operations and
   Jev calls are cheap and cached, so reprocessing history each run is
   cheap after the first run).
3. Drop scraper flaps and collapse duplicate edits (pipeline/lib/filters.py).
4. Score each remaining change with Jev (pipeline/lib/jev.py, cached on
   disk by delta hash so a rerun costs nothing new).
5. For every document version where any topic score >= 0.5, write
   alerts/YYYY-MM-DD-<vendor>-<doc>.md (skipped if it already exists —
   this is what makes reruns idempotent) and note it as new.
6. Regenerate the static site's alert archive and RSS feed.

state.json records, per document, the last version date the pipeline has
seen, purely for the human-readable run report; the alert filename itself
(one per document version) is what makes writing alerts idempotent.

Usage:
    pipeline/run.py                 # normal run: pull + full reprocess
    pipeline/run.py --no-pull       # use whatever is already cloned
    pipeline/run.py --workers 6     # Jev scoring concurrency (default 6)
    pipeline/run.py --dry-run       # extract + filter, skip Jev + writing
    pipeline/run.py --watchlist W --out DIR
                                    # a separate watchlist (e.g. one made by
                                    # pipeline/vendors.py match): alerts and
                                    # state go to DIR, the site is not rebuilt,
                                    # and the Jev cache is shared

Requires OPENROUTER_API_KEY in the environment, unless --dry-run.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from lib import alerts as alerts_lib
from lib import direct, extract, filters, jev, muse, ota, paths

# Everything a run reads or writes, apart from the watchlist, lives in the
# data directory ($TOS_WATCH_DATA, default: this repo). See lib/paths.py.
ROOT = paths.CODE
CACHE_DIR = paths.CACHE_DIR
STATE_PATH = paths.STATE_PATH
MUSE_SNAPSHOTS_DIR = paths.MUSE_SNAPSHOTS_DIR
ALERTS_DIR = paths.ALERTS_DIR
# Alerts the owner rejected as false positives: {stem: reason}. A listed stem
# is never written again, and the list doubles as negative labels for tuning
# Jev.
DISMISSED_PATH = paths.DISMISSED_PATH
WATCHLIST_PATH = paths.WATCHLIST_PATH
DIRECT_SNAPSHOTS_DIR = paths.DIRECT_SNAPSHOTS_DIR


def load_watchlist() -> dict:
    return json.loads(WATCHLIST_PATH.read_text())


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"documents": {}, "last_run": None}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def collect_ota_blocks(watchlist: dict, pull: bool) -> list[extract.ChangeBlock]:
    repo_dirs: dict[str, tuple[str, str]] = {}
    for name, cfg in watchlist["ota_repos"].items():
        if pull:
            path = ota.ensure_repo(CACHE_DIR, name, cfg["clone_url"])
        else:
            path = str(CACHE_DIR / name)
        repo_dirs[name] = (path, cfg["slug"])

    blocks: list[extract.ChangeBlock] = []
    for d in watchlist["documents"]:
        repo_path, repo_slug = repo_dirs[d["repo"]]
        doc_blocks = extract.diff_versions(
            repo_path, repo_slug, d["path"], doc_label=d["doc"], vendor=d["vendor"]
        )
        for b in doc_blocks:
            b.source_url = d["source_url"]  # type: ignore[attr-defined]
            b.repo_slug = repo_slug  # type: ignore[attr-defined]
            b.track = d.get("track")  # type: ignore[attr-defined]
        blocks.extend(doc_blocks)
    return blocks


def collect_muse_blocks(watchlist: dict) -> list[extract.ChangeBlock]:
    """Compare the latest fetch of the Muse page to the most recent snapshot."""
    blocks: list[extract.ChangeBlock] = []
    for page in watchlist.get("web_pages", []):
        prev = muse.latest_snapshot(MUSE_SNAPSHOTS_DIR)
        try:
            current = muse.snapshot(page["url"])
        except Exception as err:  # noqa: BLE001 - network fetch, report and skip
            print(f"warning: could not fetch {page['url']}: {err}", file=sys.stderr)
            continue
        today = time.strftime("%Y-%m-%d")
        MUSE_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snap_path = MUSE_SNAPSHOTS_DIR / f"{today}.json"
        if not snap_path.exists():
            snap_path.write_text(json.dumps(current, indent=1))
        if prev is None:
            continue  # first snapshot ever; nothing to diff against
        prev_date, prev_paras = prev
        if prev_date == today:
            continue  # already have today's snapshot as the "previous" one
        prev_texts = [p["text"] for p in prev_paras]
        cur_texts = [p["text"] for p in current]
        prev_hashes = {p["sha256"] for p in prev_paras}
        cur_hashes = {p["sha256"] for p in current}
        import difflib

        sm = difflib.SequenceMatcher(
            None,
            [p["sha256"] for p in prev_paras],
            [p["sha256"] for p in current],
            autojunk=False,
        )
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            ra = [k for k in range(i1, i2) if prev_paras[k]["sha256"] not in cur_hashes]
            rb = [k for k in range(j1, j2) if current[k]["sha256"] not in prev_hashes]
            if not ra and not rb:
                continue
            b = extract.ChangeBlock(
                vendor=page["vendor"],
                doc=page["doc"],
                old_date=prev_date,
                new_date=today,
                new_rev="",
                removed=[prev_paras[k]["text"] for k in ra],
                added=[current[k]["text"] for k in rb],
                removed_raw=[prev_paras[k]["text"] for k in ra],
                added_raw=[current[k]["text"] for k in rb],
            )
            b.source_url = page["url"]  # type: ignore[attr-defined]
            b.repo_slug = None  # type: ignore[attr-defined]
            b.track = page.get("track")  # type: ignore[attr-defined]
            blocks.append(b)
    return blocks


def collect_direct_blocks(watchlist: dict) -> list[extract.ChangeBlock]:
    """Same idea as collect_muse_blocks, generalized (pipeline/lib/direct.py)
    for the 2026-09-26 dev-tools/typing direct-fetch sources (Vercel,
    Netlify, Supabase, DeepL, Otter): no OTA declaration exists for these, so
    tos.watch keeps its own dated, per-vendor snapshots and diffs them.
    """
    blocks: list[extract.ChangeBlock] = []
    for page in watchlist.get("direct_pages", []):
        slug = page["slug"]
        prev = direct.latest_snapshot(DIRECT_SNAPSHOTS_DIR, slug)
        try:
            current = direct.snapshot(page["url"])
        except Exception as err:  # noqa: BLE001 - network fetch, report and skip
            print(f"warning: could not fetch {page['url']}: {err}", file=sys.stderr)
            continue
        today = time.strftime("%Y-%m-%d")
        DIRECT_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snap_path = DIRECT_SNAPSHOTS_DIR / f"{slug}-{today}.json"
        if not snap_path.exists():
            snap_path.write_text(json.dumps(current, indent=1))
        if prev is None:
            continue  # first snapshot ever; nothing to diff against
        prev_date, prev_paras = prev
        if prev_date == today:
            continue
        prev_hashes = {p["sha256"] for p in prev_paras}
        cur_hashes = {p["sha256"] for p in current}
        import difflib

        sm = difflib.SequenceMatcher(
            None,
            [p["sha256"] for p in prev_paras],
            [p["sha256"] for p in current],
            autojunk=False,
        )
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            ra = [k for k in range(i1, i2) if prev_paras[k]["sha256"] not in cur_hashes]
            rb = [k for k in range(j1, j2) if current[k]["sha256"] not in prev_hashes]
            if not ra and not rb:
                continue
            b = extract.ChangeBlock(
                vendor=page["vendor"],
                doc=page["doc"],
                old_date=prev_date,
                new_date=today,
                new_rev="",
                removed=[prev_paras[k]["text"] for k in ra],
                added=[current[k]["text"] for k in rb],
                removed_raw=[prev_paras[k]["text"] for k in ra],
                added_raw=[current[k]["text"] for k in rb],
            )
            b.source_url = page["url"]  # type: ignore[attr-defined]
            b.repo_slug = None  # type: ignore[attr-defined]
            b.track = page.get("track")  # type: ignore[attr-defined]
            blocks.append(b)
    return blocks


def score_blocks(blocks: list[extract.ChangeBlock], workers: int) -> dict[int, dict]:
    cache = jev.JevCache(CACHE_DIR / "jev")
    scores: dict[int, dict] = {}

    def work(item):
        i, b = item
        removed = "\n\n".join(b.removed_raw)
        added = "\n\n".join(b.added_raw)
        return i, jev.score(b.doc, b.new_date, removed, added, cache)

    with cf.ThreadPoolExecutor(workers) as ex:
        for i, result in ex.map(work, enumerate(blocks)):
            scores[i] = result
    return scores


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--no-pull", action="store_true", help="skip git pull / re-fetch")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true", help="extract + filter only, no Jev, no writes")
    ap.add_argument(
        "--since",
        default=None,
        help=(
            "YYYY-MM-DD: only score/alert change blocks at or after this date. "
            "A Jev-call-budget control for backfilling a large new watchlist "
            "addition (added 2026-09-26 for the tracks expansion), not a "
            "behavior change for normal runs. Safe to combine with existing "
            "history: already-written alert files are untouched either way "
            "(alerting is keyed by document version, checked before scoring)."
        ),
    )
    ap.add_argument(
        "--rerender",
        action="store_true",
        help=(
            "Rewrite existing alert files (for example after an excerpt "
            "change) and write no new ones. Only blocks behind an existing "
            "alert are scored, so every Jev answer comes from the cache."
        ),
    )
    ap.add_argument("--watchlist", type=pathlib.Path, default=None, help="watchlist JSON (default: pipeline/watchlist.json)")
    ap.add_argument(
        "--out",
        type=pathlib.Path,
        default=None,
        help=(
            "write alerts to OUT/alerts and state to OUT/state.json instead of "
            "the data directory's, and skip the site rebuild. For private "
            "watchlists (e.g. a team's vendors) whose alerts must never reach "
            "the public site or the newsletter."
        ),
    )
    args = ap.parse_args()

    global WATCHLIST_PATH, ALERTS_DIR, STATE_PATH
    if args.watchlist:
        WATCHLIST_PATH = args.watchlist
    if args.out:
        ALERTS_DIR = args.out / "alerts"
        STATE_PATH = args.out / "state.json"
        ALERTS_DIR.mkdir(parents=True, exist_ok=True)

    watchlist = load_watchlist()
    state = load_state()
    dismissed = json.loads(DISMISSED_PATH.read_text()) if DISMISSED_PATH.exists() else {}

    print("Extracting OTA document history...")
    blocks = collect_ota_blocks(watchlist, pull=not args.no_pull)
    print(f"  {len(blocks)} raw change blocks across {len(watchlist['documents'])} OTA documents")

    print("Fetching the Muse page...")
    blocks += collect_muse_blocks(watchlist)

    print("Fetching direct-fetch sources (no OTA declaration)...")
    blocks += collect_direct_blocks(watchlist)

    print("Applying scraper-flap filter and duplicate collapse...")
    blocks = filters.drop_scraper_flaps(blocks)
    blocks = filters.collapse_duplicates(blocks)
    print(f"  {len(blocks)} change blocks remain")

    if args.since:
        before = len(blocks)
        blocks = [b for b in blocks if b.new_date >= args.since]
        print(f"  --since {args.since}: {before - len(blocks)} older block(s) skipped, {len(blocks)} remain")

    if args.rerender:
        before = len(blocks)
        blocks = [
            b for b in blocks
            if (ALERTS_DIR / alerts_lib.alert_filename(b.vendor, b.doc, b.new_date)).exists()
        ]
        print(f"  --rerender: {len(blocks)} of {before} blocks sit behind an existing alert")

    if args.dry_run:
        print("--dry-run: skipping Jev scoring and alert writing")
        return

    print(f"Scoring with Jev ({args.workers} workers, cached by delta hash)...")
    t0 = time.time()
    scores = score_blocks(blocks, args.workers)
    print(f"  scored in {time.time() - t0:.1f}s")

    # Group alerting blocks by (vendor, doc, new_date) -> one alert each.
    groups: dict[tuple, list[int]] = {}
    for i, b in enumerate(blocks):
        if jev.alerts_on(scores[i]):
            groups.setdefault((b.vendor, b.doc, b.new_date), []).append(i)

    new_count = 0
    for (vendor, doc, date_str), idxs in sorted(groups.items(), key=lambda kv: kv[0][2]):
        fname = alerts_lib.alert_filename(vendor, doc, date_str)
        path = ALERTS_DIR / fname
        if path.exists() and not args.rerender:
            continue  # already alerted this document version
        if path.stem in dismissed:
            continue  # the owner rejected this one as a false positive
        if args.rerender and not path.exists():
            continue
        # .get(q, 0.0): a block may have been Jev-scored and cached before
        # telemetry/usage_pricing existed (older QUESTIONS set); such a
        # cached score dict simply lacks those keys. Defaulting to 0.0 keeps
        # old, already-alerted history from ever reaching this line at all
        # (path.exists() above short-circuits it) and only affects blocks
        # that are new enough to alert for the first time today.
        idxs.sort(key=lambda i: max(scores[i].get(q, 0.0) for q in jev.TOPIC_QUESTIONS), reverse=True)
        agg_scores = {q: max(scores[i].get(q, 0.0) for i in idxs) for q in jev.QUESTIONS}
        b0 = blocks[idxs[0]]
        commit_url = (
            extract.commit_url(b0.repo_slug, b0.new_rev)
            if getattr(b0, "repo_slug", None) and b0.new_rev
            else None
        )
        passages = [
            {"removed": "\n".join(blocks[i].removed_raw), "added": "\n".join(blocks[i].added_raw)}
            for i in idxs
        ]
        alerts_lib.write(
            ALERTS_DIR,
            vendor=vendor,
            doc=doc,
            date_str=date_str,
            source_url=getattr(b0, "source_url", ""),
            commit_url=commit_url,
            scores=agg_scores,
            passages=passages,
            track=getattr(b0, "track", None),
        )
        new_count += 1
        state.setdefault("documents", {})[f"{vendor}/{doc}"] = date_str

    state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(state)

    print(f"{new_count} new alert(s) written; {len(groups)} alerting document version(s) total seen this run")

    if args.out:
        print("Done (--out: site not rebuilt).")
        return

    print("Rebuilding site archive and RSS feed...")
    sys.path.insert(0, str(ROOT / "site"))
    import build as site_build  # noqa: E402

    site_build.build(paths.CODE, paths.DATA)
    print("Done.")


if __name__ == "__main__":
    main()
