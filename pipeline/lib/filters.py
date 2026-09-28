"""Two deterministic filters applied after diffing, before Jev scoring.

Both are described (not code, so re-implemented here to spec) in the
2026-09-26 session spike write-up:

- Flap filter: OTA's scraper occasionally drops a chunk of a document and
  restores it a few captures later (anti-bot noise, not a real policy
  change). A paragraph that is removed in one block and the same paragraph
  added back in another block of the *same document* within 3 months (either
  order) is a scraper flap; drop it from both blocks.
- Duplicate collapse: the same textual change (same vendor, same normalized
  removed+added paragraphs) recorded more than once (e.g. a rename applied
  once but re-captured across several noisy re-fetches) collapses to a single
  occurrence, kept at its earliest date.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

from .extract import ChangeBlock, para_hash

FLAP_WINDOW = timedelta(days=90)


def _to_date(s: str) -> date:
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


def drop_scraper_flaps(blocks: list[ChangeBlock]) -> list[ChangeBlock]:
    """Remove flapping paragraphs; drop any block left with no real change."""
    by_doc: dict[str, list[ChangeBlock]] = {}
    for b in blocks:
        by_doc.setdefault(b.doc, []).append(b)

    out: list[ChangeBlock] = []
    for doc, doc_blocks in by_doc.items():
        # index: hash -> list of (date, kind, block_index) within this doc
        removed_events: dict[str, list[tuple[date, int]]] = {}
        added_events: dict[str, list[tuple[date, int]]] = {}
        for i, b in enumerate(doc_blocks):
            d = _to_date(b.new_date)
            for p in b.removed:
                removed_events.setdefault(para_hash(p), []).append((d, i))
            for p in b.added:
                added_events.setdefault(para_hash(p), []).append((d, i))

        flap_hashes: set[str] = set()
        for h, r_events in removed_events.items():
            a_events = added_events.get(h)
            if not a_events:
                continue
            for rd, _ in r_events:
                for ad, _ in a_events:
                    if abs((ad - rd).days) <= FLAP_WINDOW.days:
                        flap_hashes.add(h)
                        break

        for b in doc_blocks:
            keep_r_idx = [i for i, p in enumerate(b.removed) if para_hash(p) not in flap_hashes]
            keep_a_idx = [i for i, p in enumerate(b.added) if para_hash(p) not in flap_hashes]
            if not keep_r_idx and not keep_a_idx:
                continue  # entire block was scraper flap
            b.removed = [b.removed[i] for i in keep_r_idx]
            b.removed_raw = [b.removed_raw[i] for i in keep_r_idx]
            b.added = [b.added[i] for i in keep_a_idx]
            b.added_raw = [b.added_raw[i] for i in keep_a_idx]
            out.append(b)
    return out


def _delta_signature(b: ChangeBlock) -> str:
    h = hashlib.sha256()
    for p in sorted(b.removed):
        h.update(b"R")
        h.update(p.encode())
    for p in sorted(b.added):
        h.update(b"A")
        h.update(p.encode())
    return h.hexdigest()


def collapse_duplicates(blocks: list[ChangeBlock]) -> list[ChangeBlock]:
    """Keep only the earliest occurrence of each (vendor, normalized delta)."""
    ordered = sorted(blocks, key=lambda b: (b.new_date, b.doc))
    seen: set[tuple[str, str]] = set()
    out: list[ChangeBlock] = []
    for b in ordered:
        key = (b.vendor, _delta_signature(b))
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out
