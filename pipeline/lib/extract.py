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


_LINK = re.compile(r"\]\([^)]*\)")  # markdown link targets
_FOOTNOTE = re.compile(r"\\\[\d+\\\]")  # footnote markers like \[48\]
_URL = re.compile(r"https?://\S+")
_RULE = re.compile(r"[-=]{3,}")
_NONWORD = re.compile(r"[^\w%$]+")


def normalize(p: str) -> str:
    p = _LINK.sub("]", p)
    p = _FOOTNOTE.sub("", p)
    p = _URL.sub("", p)
    p = _RULE.sub("", p)
    p = _NONWORD.sub(" ", p).lower()
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


def blobs(repo: str, shas: list[str], doc_path: str) -> list[str]:
    """Every version of doc_path in one `git cat-file --batch` call, instead
    of one `git show` process per version (profiled 2026-09-29: a third of
    the extraction time was process startup)."""
    out = subprocess.run(
        ["git", "-C", repo, "cat-file", "--batch"],
        input="".join(f"{sha}:{doc_path}\n" for sha in shas).encode(),
        capture_output=True, check=True,
    ).stdout
    texts, at = [], 0
    for _ in shas:
        nl = out.index(b"\n", at)
        header = out[at:nl].decode()
        parts = header.split()
        if len(parts) != 3:
            raise RuntimeError(f"git cat-file: {header}")
        size = int(parts[2])
        texts.append(out[nl + 1 : nl + 1 + size].decode("utf-8", errors="replace"))
        at = nl + 1 + size + 1
    return texts


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
    prev_date = None
    prev_paras: list[str] | None = None
    prev_norm: list[str] = []
    prev_hash: list[str] = []
    # Each distinct paragraph is normalized and hashed once per document:
    # most paragraphs repeat unchanged across versions (profiled 2026-09-29:
    # normalize ran four times per paragraph per version and was over half
    # the extraction time).
    seen: dict[str, tuple[str, str]] = {}
    for rev, raw in zip(revs, blobs(repo, [r.sha for r in revs], doc_path)):
        paras = paragraphs(raw)
        for p in paras:
            if p not in seen:
                n = normalize(p)
                seen[p] = (n, para_hash(n))
        norm = [seen[p][0] for p in paras]
        hb = [seen[p][1] for p in paras]
        if prev_paras is not None:
            ha = prev_hash
            sa, sb = set(ha), set(hb)
            sm = difflib.SequenceMatcher(None, ha, hb, autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    continue
                ra = [k for k in range(i1, i2) if ha[k] not in sb and prev_norm[k]]
                rb = [k for k in range(j1, j2) if hb[k] not in sa and norm[k]]
                if not ra and not rb:
                    continue  # moved or pure-cosmetic reorder only
                blocks.append(
                    ChangeBlock(
                        vendor=vendor,
                        doc=doc_label,
                        old_date=prev_date,
                        new_date=rev.date,
                        new_rev=rev.sha,
                        removed=[prev_norm[k] for k in ra],
                        added=[norm[k] for k in rb],
                        removed_raw=[prev_paras[k] for k in ra],
                        added_raw=[paras[k] for k in rb],
                    )
                )
        prev_date, prev_paras, prev_norm, prev_hash = rev.date, paras, norm, hb
    return blocks
