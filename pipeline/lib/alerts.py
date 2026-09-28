"""Render one alert Markdown file per (vendor, doc, document version).

Frontmatter is flat key:value pairs (no YAML dependency, stdlib only) so
site/build.py can parse it with a few string splits.
"""
from __future__ import annotations
import difflib

import pathlib
import re

EXCERPT_LIMIT = 300
MAX_PASSAGES = 3
# ODC-By 1.0 requires attribution wherever the excerpts travel; site/build.py
# carries the same credit in HTML (ATTRIBUTION_HTML) and in feed.xml.
ATTRIBUTION_MD = (
    "Policy text and history from [Open Terms Archive](https://opentermsarchive.org/en/), "
    "under [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/)."
)


def slugify(*parts: str) -> str:
    s = "-".join(parts).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"-+", "-", s)


def excerpt(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= EXCERPT_LIMIT:
        return text
    return text[: EXCERPT_LIMIT - 1].rstrip() + "…"


def paired_excerpts(removed: str, added: str) -> tuple[str, str]:
    """Window both sides around the words that differ, so a change deep in a
    long paragraph is visible instead of being cut off at the limit.

    Both sides start at the same word and are cut at the same shared word,
    so a diff of the two excerpts shows only the real edit, never two
    different truncation points."""
    r, a = removed.split(), added.split()
    if not r or not a:
        return excerpt(removed), excerpt(added)
    k = 0
    while k < min(len(r), len(a)) and r[k] == a[k]:
        k += 1
    start = max(0, k - 8)
    lead = ["…"] if start > 0 else []
    r, a = r[start:], a[start:]
    limit = EXCERPT_LIMIT - 4  # room for the leading and trailing "…"
    if len(" ".join(r)) <= limit and len(" ".join(a)) <= limit:
        return excerpt(" ".join(lead + r)), excerpt(" ".join(lead + a))
    # Cut both sides just after the latest word they share, within the limit.
    best = None
    for block in difflib.SequenceMatcher(None, r, a, autojunk=False).get_matching_blocks():
        for n in range(1, block.size + 1):
            i, j = block.a + n, block.b + n
            if len(" ".join(r[:i])) <= limit and len(" ".join(a[:j])) <= limit:
                best = (i, j)
    if best is None:
        return excerpt(" ".join(lead + r)), excerpt(" ".join(lead + a))
    i, j = best
    r_out = lead + r[:i] + (["…"] if i < len(r) else [])
    a_out = lead + a[:j] + (["…"] if j < len(a) else [])
    return excerpt(" ".join(r_out)), excerpt(" ".join(a_out))


def alert_filename(vendor: str, doc: str, date_str: str) -> str:
    return f"{date_str}-{slugify(vendor, doc)}.md"


def render(
    *,
    vendor: str,
    doc: str,
    date_str: str,
    source_url: str,
    commit_url: str | None,
    scores: dict,
    passages: list[dict],
    track: str | None = None,
) -> str:
    """passages: list of {"removed": str, "added": str} raw excerpts, longest
    first (already truncated by the caller if desired; this also enforces
    the 300-char / 3-passage limits defensively). track (added 2026-09-26) is
    the watchlist track id (e.g. "dev-tools"), used by site/build.py to group
    alerts per track; omitted from frontmatter when not given, so alerts
    written before tracks existed remain valid and unchanged."""
    score_line = " ".join(f"{k}={v:.2f}" for k, v in scores.items())
    fm = [
        "---",
        f"vendor: {vendor}",
        f"doc: {doc}",
        f"date: {date_str}",
        f"source_url: {source_url}",
    ]
    if track:
        fm.append(f"track: {track}")
    if commit_url:
        fm.append(f"ota_commit_url: {commit_url}")
    fm.append(f"scores: {score_line}")
    fm.append("---")

    body = [f"# {vendor} {doc} changed — {date_str}", ""]
    for p in passages[:MAX_PASSAGES]:
        removed, added = paired_excerpts(p.get("removed", ""), p.get("added", ""))
        if removed:
            body.append("**Removed:**")
            body.append(f"> {removed}")
            body.append("")
        if added:
            body.append("**Added:**")
            body.append(f"> {added}")
            body.append("")
    if commit_url:
        body.append(f"[See this change on GitHub]({commit_url}).")
    body.append("")
    body.append(f"Source: [{source_url}]({source_url})")
    if commit_url:  # only OTA-sourced alerts have a commit URL
        body.append("")
        body.append(ATTRIBUTION_MD)
    return "\n".join(fm) + "\n\n" + "\n".join(body) + "\n"


def write(alerts_dir: pathlib.Path, **kwargs) -> pathlib.Path:
    alerts_dir.mkdir(parents=True, exist_ok=True)
    path = alerts_dir / alert_filename(kwargs["vendor"], kwargs["doc"], kwargs["date_str"])
    path.write_text(render(**kwargs))
    return path
