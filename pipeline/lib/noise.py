"""Is a quoted passage page furniture instead of policy text?

Open Terms Archive documents are scraped web pages, so a "changed passage"
is sometimes a table of contents, a menu or half a Markdown link. Such a
passage is not evidence of a change. Used by pipeline/verify_alerts.py (an
alert needs at least one real passage) and site/build.py (noise passages are
not shown).
"""
from __future__ import annotations

import re

LINK = re.compile(r"\[((?:[^\[\]\\]|\\.)*)\]\([^)]*(?:\)|…$)")
STRAY_LINK_END = re.compile(r"\]\([^)]*(?:\)|…$)")
BULLET = re.compile(r"(?:^|\s)[\*•] ")
MIN_WORDS = 6


def prose(quote: str) -> str:
    """The quote with Markdown links reduced to their text and markup removed."""
    s = quote.replace("…", " ").replace("⁠", "")
    s = LINK.sub(lambda m: m.group(1), s)
    s = STRAY_LINK_END.sub("", s)  # a link cut at the start of the excerpt: "app](https://…)"
    s = re.sub(r"[*_#|]|:?-{3,}:?", " ", s)
    return " ".join(s.split())


def is_furniture(quote: str) -> bool:
    """A table of contents or a run of in-page anchors: never evidence, even
    next to a real sentence."""
    if quote.count("](#") >= 3:
        return True
    items = [i for i in BULLET.split(quote) if i.strip()]
    return len(items) >= 4 and max(len(prose(i).split()) for i in items) < 12


def is_noise(quote: str) -> bool:
    """Furniture, or too few real words once the markup is gone."""
    return is_furniture(quote) or len(prose(quote).split()) < MIN_WORDS


# "Last Updated: October 27, 2026 ([previous version](https://…))": the date line
# at the top of a policy, with the archive's own link to the older copy.
HEADER = re.compile(
    r"^\s*[_*]*\s*(?:last\s+)?(?:updated|effective|revised)[^_*(\[]{0,60}?\d{4}\s*"
    r"(?:\(\s*\[[^\]]*\]\([^)]*\)\s*\))?\s*[_*]*\s*", re.I)
PREVIOUS = re.compile(r"\s*\(\s*\[previous version\]\([^)]*\)\s*\)")


def has_header(quote: str) -> bool:
    return HEADER.match(quote) is not None


def same_text(old: str, new: str) -> bool:
    """Old and new differ only in markup (a link or an invisible character)."""
    return bool(old and new) and prose(old) == prose(new)


def drop_change(old: str, new: str) -> bool:
    """Should a changed passage (old and/or new quote) be left off the page?
    Yes when either side is furniture, or when every side that exists is noise."""
    sides = [q for q in (old, new) if q]
    if same_text(old, new):
        return True
    return any(is_furniture(q) for q in sides) or all(is_noise(q) for q in sides)
