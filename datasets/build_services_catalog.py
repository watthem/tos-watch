#!/usr/bin/env python3
"""Build site/services.json: every service tos.watch tracks, plus every
service Open Terms Archive has ever declared in the collections checked for
this batch (2026-09-26) -- even ones we don't track -- so the site's search
box can say "archived, but not in the newsletter yet" instead of a flat
"not found".

This is a maintainer-run script, not part of the regular pipeline run:
- The "tracked" half comes from pipeline/watchlist.json, which any
  self-hoster has (see SELF_HOSTING.md); site/build.py regenerates that half
  on every normal pipeline run.
- The "archived" half needs the much bigger set of OTA declarations cloned
  under datasets/cache/ (pga, genai-eu, genai-contrib, dating, contrib,
  vlopses-us -- see datasets/candidates.py for why these five/six and not
  OTA's other collections). That cache is gitignored and multiple hundred
  MB, so this script is a manual refresh step for the tos.watch maintainer,
  not something a fresh self-hosted clone needs to run.

Usage:
    python3 datasets/build_services_catalog.py    # needs datasets/cache/*
    site/build.py                                  # tracked-half refresh only, always safe

Output: site/services.json (client-side search + /check's bundled lookup)
and a copy at worker/src/services.json (Workers bundle JSON at build time;
the Worker never fetches this at request time -- see worker/src/index.js
handleCheck).
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent.parent
# The OTA clones are big and private-side; look in the data directory first.
CACHE = pathlib.Path(os.environ.get("TOS_WATCH_DATA") or ROOT).expanduser() / "datasets/cache"
WATCHLIST = json.loads((ROOT / "pipeline/watchlist.json").read_text())

# (declarations-dir-name-or-None, versions-repo-slug)
COLLECTIONS = [
    ("pga-declarations", "OpenTermsArchive/pga-versions"),
    ("genai-eu-declarations", "OpenTermsArchive/genai-eu-versions"),
    ("genai-contrib-declarations", "OpenTermsArchive/genai-contrib-versions"),
    ("dating-declarations", "OpenTermsArchive/dating-versions"),
    ("contrib-declarations", "OpenTermsArchive/contrib-versions"),
]
# vlopses-us has no declarations repo cloned locally; list from the versions
# repo's top-level folders instead (service names, not documents).
VLOPSES_VERSIONS_DIR = CACHE / "vlopses-us-versions"
VLOPSES_REPO_SLUG = "OpenTermsArchive/vlopses-us-versions"

SKIP_NAMES = {"README.md", "LICENSE"}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-+", "-", s)


def match_key(name: str) -> str:
    """Loose key for fuzzy matching a searched name/URL fragment to a
    service name: lowercase, alphanumeric only, no spaces or punctuation."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def tracked_services() -> dict[str, dict]:
    by_vendor: dict[str, dict] = {}

    def add(vendor: str, track: str | None, source: str | None):
        entry = by_vendor.setdefault(
            vendor, {"name": vendor, "status": "tracked", "tracks": set(), "sources": set()}
        )
        if track:
            entry["tracks"].add(track)
        if source:
            entry["sources"].add(source)

    for d in WATCHLIST.get("documents", []):
        add(d["vendor"], d.get("track"), d.get("source"))
    for wp in WATCHLIST.get("web_pages", []):
        add(wp["vendor"], wp.get("track"), wp.get("source"))
    for dp in WATCHLIST.get("direct_pages", []):
        add(dp["vendor"], dp.get("track"), dp.get("source"))

    out = {}
    for vendor, entry in by_vendor.items():
        key = match_key(vendor)
        out[key] = {
            "name": vendor,
            "slug": slugify(vendor),
            "status": "tracked",
            "tracks": sorted(entry["tracks"]),
        }
    return out


def archived_services(already: set[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for decl_dir, repo_slug in COLLECTIONS:
        d = CACHE / decl_dir / "declarations"
        if not d.exists():
            print(f"warning: {d} not present locally, skipping this collection", file=sys.stderr)
            continue
        for f in d.iterdir():
            name = f.stem
            if f.name in SKIP_NAMES or ".history" in f.name or ".filters" in f.name:
                continue
            key = match_key(name)
            if key in already or key in out:
                continue
            out[key] = {
                "name": name,
                "slug": slugify(name),
                "status": "archived",
                "collection_repo": repo_slug,
                "ota_history_url": f"https://github.com/{repo_slug}/tree/main/{name.replace(' ', '%20')}",
            }
    if VLOPSES_VERSIONS_DIR.exists():
        for f in VLOPSES_VERSIONS_DIR.iterdir():
            if not f.is_dir() or f.name in SKIP_NAMES or f.name.startswith("."):
                continue
            name = f.name
            key = match_key(name)
            if key in already or key in out:
                continue
            out[key] = {
                "name": name,
                "slug": slugify(name),
                "status": "archived",
                "collection_repo": VLOPSES_REPO_SLUG,
                "ota_history_url": f"https://github.com/{VLOPSES_REPO_SLUG}/tree/main/{name.replace(' ', '%20')}",
            }
    else:
        print(f"warning: {VLOPSES_VERSIONS_DIR} not present locally, skipping", file=sys.stderr)
    return out


def main() -> None:
    tracked = tracked_services()
    archived = archived_services(set(tracked))
    services = sorted(tracked.values(), key=lambda s: s["name"]) + sorted(
        archived.values(), key=lambda s: s["name"]
    )
    payload = {
        "generated": "2026-09-26",
        "note": (
            "tracked: alerted on tos.watch. archived: has Open Terms Archive "
            "history but is not on tos.watch yet -- request it. Anything else "
            "is unknown to both."
        ),
        "services": services,
    }
    text = json.dumps(payload, indent=1) + "\n"
    (ROOT / "site/services.json").write_text(text)
    (ROOT / "worker/src/services.json").write_text(text)
    print(f"tracked: {len(tracked)}, archived: {len(archived)}, total: {len(services)}")


if __name__ == "__main__":
    main()
