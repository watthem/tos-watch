#!/usr/bin/env python3
"""Build the static site from alerts/*.md and pipeline/watchlist.json.

Stdlib only. Parses the flat frontmatter written by
pipeline/lib/alerts.py (no YAML dependency), renders:

- site/index.html              (landing page: signup, example diff, search, latest)
- site/alerts/index.html       (every change, newest first)
- site/alerts/<alert>.html     (one page per change, with its diffs)
- site/services.html           (every tracked service, by track)
- site/s/<slug>.html           (one page per tracked service)
- site/self-hosting.html
- site/feed.xml                (RSS 2.0, newest 50)
- site/404.html, robots.txt, sitemap.xml, favicon.svg

Each changed passage is written as a plain removed/added block inside
<div class="diff" data-old=… data-new=…>. build/render-diffs.mjs then
replaces those blocks with @pierre/diffs word diffs; without Node the
plain blocks stay and still read correctly.

Run directly (`python3 site/build.py`) or via pipeline/run.py after a
scoring pass.
"""
from __future__ import annotations

import datetime
import difflib
import html
import json
import pathlib
import re
import shutil
import subprocess

SITE_DIR = pathlib.Path(__file__).parent
ROOT_DIR = SITE_DIR.parent
WATCHLIST_PATH = ROOT_DIR / "pipeline" / "watchlist.json"
API_BASE = "https://api.tos.watch"
BASE_URL = "https://tos.watch"
FEATURED_ALERT = "2025-11-04-linkedin-privacy-policy"
TOPIC_THRESHOLD = 0.5
TOPICS = [
    ("ai", "AI"),
    ("data_use", "Data use"),
    ("money", "Ads & money"),
    ("rights", "Your rights"),
    ("telemetry", "Telemetry"),
    ("usage_pricing", "Pricing"),
]
ATTRIBUTION_TEXT = (
    "Policy text and history come from Open Terms Archive (opentermsarchive.org), "
    "under ODC-By 1.0 (opendatacommons.org/licenses/by/1-0/)."
)
ATTRIBUTION_HTML = (
    'Policy text and history come from <a href="https://opentermsarchive.org/en/">Open Terms Archive</a>: '
    'the <a href="https://www.platformgovernancearchive.org/">Platform Governance Archive</a> '
    "(ZeMKI, University of Bremen), the GenAI Governance Archive and other collections, "
    'under <a href="https://opendatacommons.org/licenses/by/1-0/">ODC-By 1.0</a>.'
)


# ---------------------------------------------------------------- parsing

def parse_alert(path: pathlib.Path) -> dict:
    text = path.read_text()
    parts = text.split("---\n")
    # text = "---\n<frontmatter>\n---\n\n<body>"
    fm_text, body = parts[1], "---\n".join(parts[2:])
    fields: dict = {}
    for line in fm_text.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    scores = {}
    for kv in fields.get("scores", "").split():
        k, _, v = kv.partition("=")
        if k:
            scores[k] = float(v)
    fields["scores"] = scores
    fields["changes"] = parse_changes(body)
    fields["stem"] = path.stem
    fields["_path"] = path
    return fields


def parse_changes(body: str) -> list[dict]:
    """The alert body's **Removed:** / **Added:** blockquotes, paired: a
    Removed directly followed by an Added is one edited passage."""
    blocks = re.findall(r"^\*\*(Removed|Added):\*\*\n> (.*)$", body, re.M)
    changes = []
    i = 0
    while i < len(blocks):
        kind, text = blocks[i]
        if kind == "Removed" and i + 1 < len(blocks) and blocks[i + 1][0] == "Added":
            changes.append({"old": text, "new": blocks[i + 1][1]})
            i += 2
            continue
        changes.append({"old": text, "new": ""} if kind == "Removed" else {"old": "", "new": text})
        i += 1
    return changes


def plain_policy_text(md: str) -> str:
    """Excerpt Markdown (as OTA stores it) -> readable plain text, one
    bullet per line so the diff aligns list items."""
    s = md.replace("⁠", "")
    s = re.sub(r"\[((?:[^\[\]\\]|\\.)*)\]\([^)]*(\)|…$)", lambda m: m.group(1) + ("…" if m.group(2) == "…" else ""), s)
    s = re.sub(r"\\(.)", r"\1", s)
    s = s.replace("**", "")
    s = re.sub(r"(?<!\w)_|_(?!\w)", "", s)
    s = re.sub(r"#{2,6} ", "", s)
    # Markdown tables: drop the |---| rules, one cell per line.
    s = re.sub(r"\|?(\s*:?-{3,}:?\s*\|)+", "\n", s)
    s = re.sub(r"\s*\|\s*", "\n", s)
    s = re.sub(r"\[(?![^\[\]]*\])", "", s)
    s = re.sub(r"(^|\s)\* ", lambda m: ("" if not m.group(1) else "\n") + "• ", s)
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


def load_alerts(alerts_dir: pathlib.Path) -> list[dict]:
    entries = [parse_alert(p) for p in sorted(alerts_dir.glob("*.md"))]
    entries.sort(key=lambda e: (e.get("date", ""), e["stem"]), reverse=True)
    return entries


def slugify(name: str) -> str:
    """Same algorithm as datasets/build_services_catalog.py and
    worker/src/index.js's slugify(), so a vendor name always maps to the
    same site/s/<slug>.html path and subscribe `source` tag everywhere."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-+", "-", s)


def load_watchlist() -> dict:
    if not WATCHLIST_PATH.exists():
        return {"tracks": [], "documents": [], "web_pages": [], "direct_pages": []}
    return json.loads(WATCHLIST_PATH.read_text())


def tracked_vendors(watchlist: dict) -> dict[str, list[str]]:
    """vendor name -> sorted list of track ids, from every tracked source
    (OTA documents, the direct Muse page, other direct-fetch pages)."""
    by_vendor: dict[str, set[str]] = {}
    for key in ("documents", "web_pages", "direct_pages"):
        for d in watchlist.get(key, []):
            by_vendor.setdefault(d["vendor"], set()).add(d.get("track") or "")
    return {v: sorted(t for t in tracks if t) for v, tracks in by_vendor.items()}


# ---------------------------------------------------------------- pieces

esc = html.escape


def nice_date(iso: str) -> str:
    try:
        d = datetime.date.fromisoformat(iso)
    except ValueError:
        return esc(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def topics_of(entry: dict) -> list[str]:
    sc = entry["scores"]
    return [label for key, label in TOPICS if sc.get(key, 0) >= TOPIC_THRESHOLD]


def topic_tags(entry: dict) -> str:
    tags = topics_of(entry)
    if not tags:
        return ""
    return '<ul class="tags">' + "".join(f"<li>{esc(t)}</li>" for t in tags) + "</ul>"


def similarity(change: dict) -> float:
    a, b = change["old"].split(), change["new"].split()
    """Share of the shorter side that survives unchanged: an insertion
    into a sentence scores high, a rewritten paragraph scores low."""
    if not a or not b:
        return 0.0
    same = sum(m.size for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return same / min(len(a), len(b))


def diff_block(change: dict) -> str:
    old, new = plain_policy_text(change["old"]), plain_policy_text(change["new"])
    # Word highlights help when a passage was edited; when it was rewritten
    # outright they turn into confetti, so show whole lines instead.
    words = "word-alt" if similarity(change) >= 0.5 else "none"
    fallback = ""
    if old:
        fallback += f'<p class="diff-del"><span class="visually-hidden">Removed: </span>{esc(old)}</p>'
    if new:
        fallback += f'<p class="diff-ins"><span class="visually-hidden">Added: </span>{esc(new)}</p>'
    # email_off: Cloudflare Email Obfuscation would rewrite addresses in the
    # policy text to "[email protected]", and its decoder can't reach the
    # declarative shadow DOM render-diffs.mjs puts them in.
    return (
        "<!--email_off-->"
        f'<div class="diff" data-old="{esc(old)}" data-new="{esc(new)}" data-words="{words}">'
        f'<div class="diff-fallback">{fallback}</div></div><!--/diff-->'
        "<!--/email_off-->"
    )


def change_rows(entries: list[dict], root: str, show_vendor: bool = True) -> str:
    """A ledger of changes: service, document, topics, date. Reads as a
    table on wide screens and as stacked rows on phones."""
    if not entries:
        return '<p class="empty">No changes yet. We check again with every run.</p>'
    cls = "ledger" if show_vendor else "ledger ledger-one-service"
    head = (
        '<li class="ledger-head" aria-hidden="true">'
        + ("<span>Service</span>" if show_vendor else "")
        + "<span>Document</span><span>Flagged for</span><span>Date</span></li>"
    )
    rows = []
    for e in entries:
        vendor = f'<span class="row-service">{esc(e.get("vendor", ""))}</span>' if show_vendor else ""
        rows.append(
            f'<li><a class="row" href="{root}alerts/{e["stem"]}.html">{vendor}'
            f'<span class="row-doc">{esc(e.get("doc", ""))}</span>'
            f'<span class="row-tags">{topic_tags(e)}</span>'
            f'<time class="row-date" datetime="{esc(e.get("date", ""))}">{nice_date(e.get("date", ""))}</time></a></li>'
        )
    return f'<ul class="{cls}">' + head + "\n".join(rows) + "</ul>"


def coverage_bento(watchlist: dict, by_vendor: dict[str, list[str]], entries: list[dict]) -> str:
    """The one bento on the site: a search tile plus a tile per track."""
    tiles = []
    for t in watchlist.get("tracks", []):
        vendors = sorted((v for v, ts in by_vendor.items() if t["id"] in ts), key=str.lower)
        changes = sum(1 for e in entries if e.get("track") == t["id"])
        names = ", ".join(vendors[:3]) + (f" and {len(vendors) - 3} more" if len(vendors) > 3 else "")
        tiles.append(
            f'<a class="tile" href="services.html#{esc(t["id"])}">'
            f'<span class="tile-title">{esc(t["label"])}</span>'
            f'<span class="tile-stats">{len(vendors)} services, {changes} changes</span></a>'
        )
    return f"""
<section class="coverage">
  <div class="wrap">
    <div class="bento">
      <div class="tile tile-search">
        <div>
          <h2 id="search-heading">Is your app watched?</h2>
          <p class="section-lede">{len(by_vendor)} services today. Missing one? Ask us to add it.</p>
        </div>
        <form id="search-form" class="search-form" role="search" aria-labelledby="search-heading" novalidate>
          <label for="search-input" class="visually-hidden">Search for a service or paste a link to its terms</label>
          <input id="search-input" name="q" type="search" placeholder="Grammarly, Tinder…" autocomplete="off">
          <button class="button button-secondary" type="submit">Search</button>
        </form>
        <div id="search-results" class="search-results" aria-live="polite"></div>
      </div>
      {"".join(tiles)}
      <a class="tile tile-total" href="alerts/index.html">
        <span class="tile-title">{len(entries)} changes since {min((e.get("date", "") for e in entries), default="")[:4]}</span>
        <span class="tile-stats">Open the full ledger</span>
      </a>
    </div>
  </div>
</section>"""


WORDMARK = (
    '<svg class="mark" viewBox="0 0 20 20" aria-hidden="true" focusable="false">'
    '<rect x="2.5" y="1.5" width="15" height="17" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.5"/>'
    '<rect x="5.5" y="5" width="9" height="1.6" rx=".8" fill="currentColor" opacity=".45"/>'
    '<rect x="5.5" y="8.6" width="9" height="2.4" rx="1" class="mark-add"/>'
    '<rect x="5.5" y="13.2" width="6" height="1.6" rx=".8" fill="currentColor" opacity=".45"/>'
    "</svg>"
)


# The header mark as a standalone icon: concrete colours instead of
# currentColor, and the dark-scheme palette from style.css.
FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">
<style>.s{stroke:#3d5a98}.f{fill:#3d5a98}.a{fill:#2c7656}@media (prefers-color-scheme:dark){.s{stroke:#8ea8e8}.f{fill:#8ea8e8}.a{fill:#79cfa6}}</style>
<rect x="2.5" y="1.5" width="15" height="17" rx="2.5" fill="none" class="s" stroke-width="1.5"/>
<rect x="5.5" y="5" width="9" height="1.6" rx=".8" class="f" opacity=".45"/>
<rect x="5.5" y="8.6" width="9" height="2.4" rx="1" class="a"/>
<rect x="5.5" y="13.2" width="6" height="1.6" rx=".8" class="f" opacity=".45"/>
</svg>
"""
DEFAULT_DESCRIPTION = "A free email newsletter that tells you when a company changes what it does with your data."


def page_url(path: str) -> str:
    """Public URL for a site-relative output path. Pages 308-redirects
    `x.html` to `x` and `dir/index.html` to `dir/`, so canonical URLs,
    og:url and feed links use the extensionless form."""
    if path == "index.html":
        return BASE_URL + "/"
    if path.endswith("/index.html"):
        return f"{BASE_URL}/{path[: -len('index.html')]}"
    return f"{BASE_URL}/{path.removesuffix('.html')}"


def layout(title: str, body: str, root: str = "", description: str = "", scripts: str = "",
           path: str | None = None, og_type: str = "website") -> str:
    """path: the page's site-relative output path, for canonical and og:url.
    None (the 404 page) omits both and asks crawlers not to index it."""
    desc = description or DEFAULT_DESCRIPTION
    if path is None:
        meta = '<meta name="robots" content="noindex">'
    else:
        url = esc(page_url(path))
        meta = f'<link rel="canonical" href="{url}">\n<meta property="og:url" content="{url}">'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
{meta}
<meta property="og:site_name" content="tos.watch">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta name="twitter:card" content="summary">
<link rel="icon" href="{root}favicon.svg" type="image/svg+xml">
<link rel="preload" href="{root}fonts/public-sans.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{root}style.css">
<link rel="alternate" type="application/rss+xml" title="tos.watch changes" href="{root}feed.xml">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="wordmark" href="{root}index.html">{WORDMARK}<span>tos.watch</span></a>
    <nav class="nav" aria-label="Main">
      <a href="{root}alerts/index.html">Changes</a>
      <a href="{root}services.html">Services</a>
      <a class="button button-quiet" href="{root}index.html#subscribe">Subscribe</a>
    </nav>
  </div>
</header>
<main id="main">
{body}
</main>
<footer class="site-footer">
  <div class="wrap footer-inner">
    <p class="footer-links"><span class="footer-brand">{WORDMARK}<span>tos.watch</span></span><a href="{root}feed.xml">RSS</a><a href="{root}self-hosting.html">Self-hosting</a><a href="https://github.com/watthem/tos-watch">Source on GitHub</a><span>Not affiliated with any company we track.</span></p>
    <p class="footer-fine">{ATTRIBUTION_HTML} Diffs by <a href="https://diffs.com">@pierre/diffs</a> (Apache-2.0).</p>
  </div>
</footer>
<script>window.TOS_WATCH_API_BASE = "{API_BASE}";</script>
{scripts}
</body>
</html>
"""


def track_blurb(track: dict) -> str:
    return re.sub(r"\s*--\s*", ": ", track.get("description", ""))


def track_checkboxes(tracks: list[dict]) -> str:
    return "\n".join(
        f'<label class="check"><input type="checkbox" name="tracks" value="{esc(t["id"])}" checked> '
        f'<span><strong>{esc(t["label"])}</strong> {esc(track_blurb(t))}</span></label>'
        for t in tracks
    )


# ---------------------------------------------------------------- pages

def render_home(entries: list[dict], watchlist: dict, by_vendor: dict[str, list[str]]) -> str:
    featured = next((e for e in entries if e["stem"] == FEATURED_ALERT), entries[0] if entries else None)
    example = ""
    if featured:
        # Show one edited passage, preferring the one about AI training.
        pairs = [c for c in featured["changes"] if c["old"] and c["new"]] or featured["changes"]
        best = max(pairs, key=similarity) if pairs else None
        example = f"""
    <figure class="example">
      <figcaption class="example-head">
        <span class="example-kicker">Example alert</span>
        <span class="example-title">{esc(featured["vendor"])} {esc(featured["doc"])}</span>
        <time datetime="{esc(featured["date"])}">{nice_date(featured["date"])}</time>
      </figcaption>
      {diff_block(best) if best else ""}
      <div class="example-foot">{topic_tags(featured)}<a href="alerts/{featured["stem"]}.html">Read the whole change</a></div>
    </figure>"""

    body = f"""
<section class="hero">
  <div class="wrap hero-inner">
    <div class="hero-copy">
      <h1>Know when the fine print changes.</h1>
      <p class="lede">When an app changes what it does with your data, we email you the exact words that changed.</p>
      <form id="subscribe" class="signup-form" data-source="landing-page" novalidate>
        <div class="field-row">
          <label for="email" class="visually-hidden">Email address</label>
          <input id="email" name="email" type="email" placeholder="you@example.com" required autocomplete="email">
          <button class="button" type="submit">Subscribe</button>
        </div>
        <details class="topics">
          <summary>Choose topics</summary>
          <div class="topic-list">
{track_checkboxes(watchlist.get("tracks", []))}
          </div>
        </details>
      </form>
      <p class="form-note" id="form-note">Free. One email per real change. <a href="https://github.com/watthem/tos-watch">Open source</a>.</p>
    </div>
    {example}
  </div>
</section>

{coverage_bento(watchlist, by_vendor, entries)}

<section>
  <div class="wrap">
    <div class="section-head">
      <h2>Latest changes</h2>
      <a href="alerts/index.html">See all {len(entries)}</a>
    </div>
    {change_rows(entries[:5], "")}
  </div>
</section>

<section class="how">
  <div class="wrap">
    <h2>How it works</h2>
    <ol class="steps">
      <li><h3>We keep every version</h3><p>Each new version of a policy is compared with the last.</p></li>
      <li><h3>We skip the noise</h3><p>Typos and reformatting are dropped; changes to your data, AI, money or rights are kept.</p></li>
      <li><h3>You get the words</h3><p>What was removed, what was added, and a link to the source.</p></li>
    </ol>
  </div>
</section>
"""
    return layout(
        "tos.watch: know when the fine print changes",
        body,
        scripts='<script src="subscribe.js"></script>\n<script src="search.js"></script>',
        path="index.html",
    )


def render_alert_page(e: dict, others: list[dict]) -> str:
    vendor, doc, slug = e.get("vendor", ""), e.get("doc", ""), slugify(e.get("vendor", ""))
    n = len(e["changes"])
    diffs = "\n".join(diff_block(c) for c in e["changes"]) or '<p class="empty">This version was flagged, but its changed passages were too long to excerpt. The source links below show the full text.</p>'
    sources = []
    if e.get("ota_commit_url"):
        sources.append(f'<li><a href="{esc(e["ota_commit_url"])}">The full diff on Open Terms Archive</a></li>')
    if e.get("source_url"):
        sources.append(f'<li><a href="{esc(e["source_url"])}">The current {esc(doc)} on {esc(vendor)}’s site</a></li>')
    more = ""
    if others:
        more = f'<h2 class="h3">Earlier {esc(vendor)} changes</h2>' + change_rows(others[:5], "../", show_vendor=False)
    body = f"""
<div class="wrap narrow page">
  <p class="crumbs"><a href="index.html">Changes</a> <span aria-hidden="true">/</span> <a href="../s/{slug}.html">{esc(vendor)}</a></p>
  <h1>{esc(vendor)} changed its {esc(doc)}</h1>
  <div class="page-meta"><time datetime="{esc(e.get("date", ""))}">{nice_date(e.get("date", ""))}</time>{topic_tags(e)}</div>
  <p class="section-lede">{n} passage{"s" if n != 1 else ""} changed. Red lines were removed and green lines were added; within an edited sentence, the exact words are highlighted, and removed words are struck through. These are excerpts, cut to the part that changed.</p>
  <div class="diffs">
{diffs}
  </div>
  <h2 class="h3">Sources</h2>
  <ul class="plain-list">{"".join(sources)}</ul>
  {service_signup(vendor, slug, e.get("track", ""), "../")}
  {more}
</div>
"""
    topics = topics_of(e)
    flagged = f", flagged for {', '.join(topics)}" if topics else ""
    return layout(
        f"{vendor} {doc} change, {nice_date(e.get('date', ''))}: tos.watch",
        body,
        root="../",
        description=(
            f"{vendor} changed its {doc} on {nice_date(e.get('date', ''))}: "
            f"{n} passage{'s' if n != 1 else ''} changed{flagged}. The exact words removed and added."
        ),
        scripts='<script src="../subscribe.js"></script>',
        path=f"alerts/{e['stem']}.html",
        og_type="article",
    )


def service_signup(vendor: str, slug: str, tracks: str, root: str) -> str:
    return f"""
  <div class="signup-box">
    <h2 class="h3">Get the next {esc(vendor)} change by email</h2>
    <form class="signup-form" data-source="service:{esc(slug)}" data-tracks="{esc(tracks)}" novalidate>
      <div class="field-row">
        <label for="email-{esc(slug)}" class="visually-hidden">Email address</label>
        <input id="email-{esc(slug)}" name="email" type="email" placeholder="you@example.com" required autocomplete="email">
        <button class="button" type="submit">Subscribe</button>
      </div>
    </form>
    <p class="form-note">Free. One email per real change. Unsubscribe with one click.</p>
  </div>"""


def render_archive(entries: list[dict]) -> str:
    by_year: dict[str, list[dict]] = {}
    for e in entries:
        by_year.setdefault(e.get("date", "")[:4] or "Undated", []).append(e)
    sections = "\n".join(
        f'<h2 class="year">{esc(year)}</h2>\n{change_rows(items, "../")}' for year, items in by_year.items()
    )
    body = f"""
<div class="wrap page">
  <h1>All changes</h1>
  <p class="section-lede">Every change we have flagged, newest first. Each one opens the exact words that were removed and added.</p>
  {sections}
</div>
"""
    return layout(
        "All changes: tos.watch",
        body,
        root="../",
        description=f"Every privacy policy and terms change tos.watch has flagged, newest first: {len(entries)} so far.",
        path="alerts/index.html",
    )


def render_services(watchlist: dict, by_vendor: dict[str, list[str]], counts: dict[str, int]) -> str:
    groups = []
    for t in watchlist.get("tracks", []):
        vendors = sorted((v for v, ts in by_vendor.items() if t["id"] in ts), key=str.lower)
        if not vendors:
            continue
        items = "".join(
            f'<li><a href="s/{slugify(v)}.html">{esc(v)}</a> <span class="muted">{counts.get(v, 0)} change{"s" if counts.get(v, 0) != 1 else ""}</span></li>'
            for v in vendors
        )
        groups.append(
            f'<section class="service-group" id="{esc(t["id"])}"><h2 class="h3">{esc(t["label"])}</h2>'
            f'<p class="muted">{esc(track_blurb(t))}</p><ul class="service-list">{items}</ul></section>'
        )
    body = f"""
<div class="wrap page">
  <div class="narrow-left">
    <h1>Services we watch</h1>
    <p class="section-lede">{len(by_vendor)} services, grouped by topic. Missing one? <a href="index.html#search-heading">Search for it</a> and ask us to add it.</p>
  </div>
  <div class="service-groups">{"".join(groups)}</div>
</div>
"""
    return layout(
        "Services we watch: tos.watch",
        body,
        description=f"The {len(by_vendor)} apps and services whose privacy policies and terms tos.watch watches, grouped by topic.",
        path="services.html",
    )


def render_service_page(vendor: str, tracks: list[str], track_labels: dict[str, str], entries: list[dict]) -> str:
    slug = slugify(vendor)
    labels = [track_labels.get(t, t) for t in tracks]
    body = f"""
<div class="wrap narrow page">
  <p class="crumbs"><a href="../services.html">Services</a></p>
  <h1>{esc(vendor)}</h1>
  <p class="section-lede">Listed under {esc(" and ".join(labels) or "no topic yet")}. {len(entries)} change{"s" if len(entries) != 1 else ""} flagged so far.</p>
  {service_signup(vendor, slug, ",".join(tracks), "../")}
  <h2 class="h3">Changes</h2>
  {change_rows(entries, "../", show_vendor=False)}
  <p class="muted small"><a href="../self-hosting.html">Want to run your own copy instead?</a></p>
</div>
"""
    n = len(entries)
    return layout(
        f"{vendor}: tos.watch",
        body,
        root="../",
        description=(
            f"Get an email when {vendor} changes its privacy policy or terms, with the exact words that changed. "
            f"{n} change{'s' if n != 1 else ''} flagged so far."
        ),
        scripts='<script src="../subscribe.js"></script>',
        path=f"s/{slug}.html",
    )


def render_self_hosting() -> str:
    body = """
<div class="wrap narrow page">
  <h1>Self-hosting</h1>
  <p class="section-lede">The tos.watch code is open source under Apache-2.0. You can run your own copy for the documents you care about, with your own alert archive and RSS feed.</p>
  <p>Start with the <a href="https://github.com/watthem/tos-watch/blob/main/SELF_HOSTING.md">self-hosting guide</a> in the <a href="https://github.com/watthem/tos-watch">repository</a>. You need Python 3 and an <a href="https://openrouter.ai">OpenRouter</a> key for the classifier. The public repo ships a generic question set; tos.watch's tuned questions and alert history stay private.</p>
  <p>Would rather not run it yourself? <a href="index.html#subscribe">Subscribe</a>, or search for a service on the <a href="index.html#search-heading">home page</a> and request it.</p>
</div>
"""
    return layout(
        "Self-hosting: tos.watch",
        body,
        description="How to run your own copy of tos.watch, the Apache-2.0 policy change watcher.",
        path="self-hosting.html",
    )


def render_404() -> str:
    """Served by Pages for any missing path, so every link is root-absolute."""
    body = """
<div class="wrap narrow page">
  <h1>Page not found</h1>
  <p class="section-lede">There's nothing at this address. The change or service you were after may have moved.</p>
  <ul class="plain-list">
    <li><a href="/alerts/">Every change we have flagged</a></li>
    <li><a href="/services">Every service we watch</a></li>
    <li><a href="/#search-heading">Search for a service</a></li>
  </ul>
</div>
"""
    return layout("Page not found: tos.watch", body, root="/")


def render_robots() -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n"


def render_sitemap(paths: list[tuple[str, str]]) -> str:
    """paths: (site-relative output path, lastmod ISO date or "")."""
    urls = []
    for path, lastmod in paths:
        mod = f"<lastmod>{esc(lastmod)}</lastmod>" if lastmod else ""
        urls.append(f"<url><loc>{esc(page_url(path))}</loc>{mod}</url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )


HEADING = re.compile(r"(?:^|\s)#{2,6} +")
SENTENCE_OPENERS = set(
    "A An The This That These Those We Our You Your If When Where What Which Who Why How "
    "In For To As At By On Some Any Each Please Unless Depending Learn Note It Its "
    "Can Do Does Is Are Will Should May".split()
)


def split_heading(text: str) -> tuple[str, str]:
    """Excerpts are whitespace-collapsed, so "Agentic calling What
    happens…" (text after a ### marker) has lost the break after its
    heading. Cut where a common English sentence opener follows a
    lower-case word within the first few words (sentence-case headings).
    Anything else (a Title Case heading, German capitalised nouns) stays
    unsplit: ("", text), which reads as before."""
    words = list(re.finditer(r"\S+", text))[:10]
    for prev, cur in zip(words, words[1:]):
        if prev.group()[0].islower() and cur.group() in SENTENCE_OPENERS:
            return text[: cur.start()].strip(), text[cur.start():]
    return "", text


def feed_description(md: str) -> str:
    """Item description as HTML (escaped once more by the caller's XML):
    headings bold and on their own, one paragraph per line."""
    out = []
    for i, part in enumerate(HEADING.split(md)):
        if i > 0:
            head, part = split_heading(part)
            if head:
                out.append(f"<p><strong>{esc(plain_policy_text(head))}</strong></p>")
        out += [f"<p>{esc(line)}</p>" for line in plain_policy_text(part).splitlines()]
    return esc("".join(out))


def render_feed(entries: list[dict], base_url: str = BASE_URL) -> str:
    items = []
    for e in entries[:50]:
        title = esc(f"{e.get('vendor')} {e.get('doc')} changed, {nice_date(e.get('date', ''))}")
        link = f"{base_url}/alerts/{e['stem']}"
        first = e["changes"][0] if e["changes"] else {"old": "", "new": ""}
        desc = feed_description(first["new"] or first["old"])
        items.append(
            f"<item><title>{title}</title><link>{link}</link>"
            f"<guid isPermaLink=\"false\">{esc(e['_path'].name)}</guid>"
            f"<pubDate>{rfc822(e.get('date', ''))}</pubDate><description>{desc}</description></item>"
        )
    items_xml = "\n".join(items)
    # Newest alert date, not wall-clock time, so a rebuild with no new alert
    # leaves feed.xml unchanged.
    built = rfc822(max((e.get("date", "") for e in entries), default=""))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>\n'
        "<title>tos.watch changes</title>\n"
        f"<link>{base_url}/</link>\n"
        f'<atom:link href="{base_url}/feed.xml" rel="self" type="application/rss+xml"/>\n'
        f"<description>When a company changes what it does with your data. {esc(ATTRIBUTION_TEXT)}</description>\n"
        f"<copyright>Policy excerpts: Open Terms Archive, ODC-By 1.0</copyright>\n"
        "<language>en</language>\n"
        f"<lastBuildDate>{built}</lastBuildDate>\n"
        f"{items_xml}\n"
        "</channel></rss>\n"
    )


def rfc822(iso: str) -> str:
    try:
        d = datetime.date.fromisoformat(iso)
    except ValueError:
        return esc(iso)
    return d.strftime("%a, %d %b %Y 00:00:00 +0000")


# ---------------------------------------------------------------- build

def copy_fonts(site_dir: pathlib.Path) -> None:
    """Self-hosted fonts (OFL-1.1): no third-party font request on a privacy
    site. The OFL has to travel with the font files, so the licences are
    copied alongside them."""
    src = ROOT_DIR / "build" / "node_modules" / "@fontsource-variable"
    wanted = {
        "public-sans.woff2": src / "public-sans" / "files" / "public-sans-latin-wght-normal.woff2",
        "source-serif-4.woff2": src / "source-serif-4" / "files" / "source-serif-4-latin-wght-normal.woff2",
        "OFL-public-sans.txt": src / "public-sans" / "LICENSE",
        "OFL-source-serif-4.txt": src / "source-serif-4" / "LICENSE",
    }
    out = site_dir / "fonts"
    out.mkdir(exist_ok=True)
    for name, path in wanted.items():
        if path.exists():
            shutil.copyfile(path, out / name)


def render_diffs(root: pathlib.Path) -> str:
    script = root / "build" / "render-diffs.mjs"
    if not shutil.which("node") or not (root / "build" / "node_modules").exists():
        return "diffs: node or build/node_modules missing, plain fallback kept"
    res = subprocess.run(["node", str(script), str(root / "site")], capture_output=True, text=True)
    return (res.stdout or res.stderr).strip()


def build(root: pathlib.Path) -> None:
    site_dir = root / "site"
    entries = load_alerts(root / "alerts")
    watchlist = load_watchlist()
    by_vendor = tracked_vendors(watchlist)
    track_labels = {t["id"]: t["label"] for t in watchlist.get("tracks", [])}
    per_vendor: dict[str, list[dict]] = {}
    for e in entries:
        per_vendor.setdefault(e.get("vendor", ""), []).append(e)

    alerts_out = site_dir / "alerts"
    alerts_out.mkdir(parents=True, exist_ok=True)
    for old in alerts_out.glob("*.html"):
        old.unlink()
    (alerts_out / "index.html").write_text(render_archive(entries))
    for e in entries:
        others = [o for o in per_vendor.get(e.get("vendor", ""), []) if o is not e]
        (alerts_out / f"{e['stem']}.html").write_text(render_alert_page(e, others))

    (site_dir / "s").mkdir(parents=True, exist_ok=True)
    for vendor, tracks in by_vendor.items():
        page = render_service_page(vendor, tracks, track_labels, per_vendor.get(vendor, []))
        (site_dir / "s" / f"{slugify(vendor)}.html").write_text(page)

    counts = {v: len(es) for v, es in per_vendor.items()}
    (site_dir / "index.html").write_text(render_home(entries, watchlist, by_vendor))
    (site_dir / "services.html").write_text(render_services(watchlist, by_vendor, counts))
    (site_dir / "self-hosting.html").write_text(render_self_hosting())
    (site_dir / "feed.xml").write_text(render_feed(entries))
    (site_dir / "404.html").write_text(render_404())
    (site_dir / "favicon.svg").write_text(FAVICON_SVG)
    (site_dir / "robots.txt").write_text(render_robots())
    pages = [("index.html", ""), ("alerts/index.html", entries[0].get("date", "") if entries else "")]
    pages += [(f"alerts/{e['stem']}.html", e.get("date", "")) for e in entries]
    pages += [("services.html", ""), ("self-hosting.html", "")]
    pages += [(f"s/{slugify(v)}.html", "") for v in sorted(by_vendor, key=str.lower)]
    (site_dir / "sitemap.xml").write_text(render_sitemap(pages))
    copy_fonts(site_dir)
    print(f"site build: {len(entries)} alerts, {len(by_vendor)} service pages")
    print(render_diffs(root))


if __name__ == "__main__":
    build(pathlib.Path(__file__).parent.parent)
