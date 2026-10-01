"""Fetch and paragraph-hash a plain web page, for services with no Open Terms
Archive declaration (checked first; this is the fallback).

Generalizes muse.py's pattern for new direct-fetch sources added 2026-09-26
(Vercel, Netlify, Supabase, DeepL, Otter — see watchlist.json "source":
"direct"). Differs from muse.py in two ways, both required by the operator's
scope for this batch of sources and NOT applied retroactively to the existing
Muse snapshots (that would change their hashes and break comparability with
the 2026-09-26 snapshot already checked in):

1. Paragraphs shorter than MIN_PARAGRAPH_LEN are dropped. Marketing sites
   without a plain <main>-wrapped document (unlike Meta's help page) mix nav
   / mega-menu text into the same block tags as the real policy text; a
   length floor is a cheap, imperfect filter for that. Verified by hand
   2026-09-26 for all five URLs below — it keeps real policy paragraphs and
   drops most (not all) nav noise. Netlify's mega-menu in particular has a
   few long nav strings that still get through; this is a known, reported
   limitation, not a silent gap.
2. Stored excerpts are truncated to EXCERPT_MAX_CHARS characters (operator's
   scope limit: never store full policy text from a direct fetch). The hash
   is still computed on the untruncated paragraph text, so real edits below
   the truncation point still change the hash and get caught.
"""
from __future__ import annotations

import json
import pathlib

from .muse import BROWSER_HEADERS, extract_paragraphs, fetch_html  # noqa: F401 (re-exported)
from .extract import para_hash

MIN_PARAGRAPH_LEN = 60
EXCERPT_MAX_CHARS = 300


def snapshot(url: str) -> list[dict]:
    """Fetch url, return [{sha256, text}], text truncated to EXCERPT_MAX_CHARS.

    The hash is computed over the full (untruncated) paragraph so a real
    edit past the truncation point still changes the hash.
    """
    paras = [p for p in extract_paragraphs(fetch_html(url)) if len(p) >= MIN_PARAGRAPH_LEN]
    return [
        {"sha256": para_hash(p), "text": p[:EXCERPT_MAX_CHARS]}
        for p in paras
    ]


def load_snapshot(path: pathlib.Path) -> list[dict]:
    return json.loads(path.read_text())


def latest_snapshot(snapshots_dir: pathlib.Path, vendor_slug: str) -> tuple[str, list[dict]] | None:
    files = sorted(snapshots_dir.glob(f"{vendor_slug}-*.json"))
    if not files:
        return None
    latest = files[-1]
    date_str = latest.stem[len(vendor_slug) + 1 :]
    return date_str, load_snapshot(latest)
