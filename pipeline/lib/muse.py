"""Fetch and paragraph-hash a plain web page (the Muse privacy help page).

Unlike the OTA documents, this page has no git history of its own, so the
pipeline keeps its own snapshots: one JSON file per fetch date under
`pipeline/muse_snapshots/`, each a list of {sha256, text} paragraphs (short
excerpts only, never the full page). The very first snapshot
(2026-09-26) was captured by hand in a chat session and is checked in as-is;
this module reproduces the same hash scheme (plain sha256 of the raw
paragraph text, truncated to 16 hex chars, no normalization) so later
snapshots compare cleanly against it.
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import pathlib
import urllib.request
from html.parser import HTMLParser

BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4"}
# Meta's help center returns an empty JS-shell page (HTTP 400) to a plain
# stdlib request with no cookies; a cookie jar plus browser-shaped headers
# gets the real server-rendered content (verified 2026-09-26).
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


class _ParagraphExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_main_depth = 0
        self.saw_main = False
        self.block_depth = 0
        self.buf: list[str] = []
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.saw_main = True
            self.in_main_depth += 1
        elif self.saw_main:
            self.in_main_depth += 1
        if tag in BLOCK_TAGS:
            self.block_depth += 1

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS and self.block_depth:
            self.block_depth -= 1
            if self.block_depth == 0:
                text = " ".join("".join(self.buf).split())
                if text:
                    self.out.append(text)
                self.buf = []
        if self.saw_main and self.in_main_depth:
            self.in_main_depth -= 1

    def handle_data(self, data):
        if self.block_depth and (not self.saw_main or self.in_main_depth):
            self.buf.append(data)


def extract_paragraphs(raw_html: str) -> list[str]:
    parser = _ParagraphExtractor()
    parser.feed(raw_html)
    # If <main> was never found, fall back to scanning the whole document.
    if not parser.saw_main:
        parser2 = _ParagraphExtractor()
        parser2.saw_main = True  # treat whole doc as "in main"
        parser2.feed(raw_html)
        return parser2.out
    return parser.out


def fetch_html(url: str) -> str:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with opener.open(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def para_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def snapshot(url: str) -> list[dict]:
    paras = extract_paragraphs(fetch_html(url))
    return [{"sha256": para_hash(p), "text": p} for p in paras]


def load_snapshot(path: pathlib.Path) -> list[dict]:
    return json.loads(path.read_text())


def latest_snapshot(snapshots_dir: pathlib.Path) -> tuple[str, list[dict]] | None:
    files = sorted(snapshots_dir.glob("*.json"))
    if not files:
        return None
    latest = files[-1]
    return latest.stem, load_snapshot(latest)
