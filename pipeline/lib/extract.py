"""Paragraph-hash diffing between consecutive versions of an OTA-tracked document.

Ported from the 2026-09-26 session spike (tos_extract2.py). Same normalization
and hashing so results match the spike's validated behaviour; generalized to
take an arbitrary git repo path, document path, and revision list instead of
hard-coded values, and to walk the FULL history of a document rather than the
last N versions (the pipeline backfills everything OTA has).
"""
from __future__ import annotations

import difflib
import hashlib
import re
import subprocess
from dataclasses import dataclass, field


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True
    ).stdout


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def normalize(p: str) -> str:
    p = re.sub(r"\]\([^)]*\)", "]", p)  # drop markdown link targets
    p = re.sub(r"\\\[\d+\\\]", "", p)  # drop footnote markers like \[48\]
    p = re.sub(r"https?://\S+", "", p)
    p = re.sub(r"[-=]{3,}", "", p)
    p = re.sub(r"[^\w%$]+", " ", p).lower()
    return " ".join(p.split())


def para_hash(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()[:16]


@dataclass
class Revision:
    sha: str
    date: str  # YYYY-MM-DD


def revisions(repo: str, doc_path: str) -> list[Revision]:
    """Every commit that touched doc_path, oldest first."""
    out = _git(
        repo, "log", "--format=%H %ad", "--date=short", "--reverse", "--", doc_path
    )
    revs = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, date = line.split(" ", 1)
        revs.append(Revision(sha=sha, date=date))
    return revs


def blob(repo: str, sha: str, doc_path: str) -> str:
    return _git(repo, "show", f"{sha}:{doc_path}")


def commit_url(repo_slug: str, sha: str) -> str:
    return f"https://github.com/{repo_slug}/commit/{sha}"


@dataclass
class ChangeBlock:
    vendor: str
    doc: str
    old_date: str
    new_date: str
    new_rev: str
    removed: list[str] = field(default_factory=list)  # normalized removed paragraphs
    added: list[str] = field(default_factory=list)  # normalized added paragraphs
    removed_raw: list[str] = field(default_factory=list)
    added_raw: list[str] = field(default_factory=list)


def diff_versions(
    repo: str, repo_slug: str, doc_path: str, doc_label: str, vendor: str
) -> list[ChangeBlock]:
    """Full-history paragraph-level diff of one document.

    Returns one ChangeBlock per contiguous run of genuinely added/removed
    paragraphs between each pair of consecutive versions. Paragraphs that only
    moved (same hash present on both sides) are not changes.
    """
    revs = revisions(repo, doc_path)
    blocks: list[ChangeBlock] = []
    prev_sha = prev_date = None
    prev_paras: list[str] | None = None
    for rev in revs:
        raw = blob(repo, rev.sha, doc_path)
        paras = paragraphs(raw)
        if prev_paras is not None:
            ha = [para_hash(normalize(p)) for p in prev_paras]
            hb = [para_hash(normalize(p)) for p in paras]
            sa, sb = set(ha), set(hb)
            sm = difflib.SequenceMatcher(None, ha, hb, autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    continue
                ra = [k for k in range(i1, i2) if ha[k] not in sb and normalize(prev_paras[k])]
                rb = [k for k in range(j1, j2) if hb[k] not in sa and normalize(paras[k])]
                if not ra and not rb:
                    continue  # moved or pure-cosmetic reorder only
                blocks.append(
                    ChangeBlock(
                        vendor=vendor,
                        doc=doc_label,
                        old_date=prev_date,
                        new_date=rev.date,
                        new_rev=rev.sha,
                        removed=[normalize(prev_paras[k]) for k in ra],
                        added=[normalize(paras[k]) for k in rb],
                        removed_raw=[prev_paras[k] for k in ra],
                        added_raw=[paras[k] for k in rb],
                    )
                )
        prev_sha, prev_date, prev_paras = rev.sha, rev.date, paras
    return blocks
