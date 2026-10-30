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
- site/privacy.html, site/terms.html, site/support.html
- site/feed.xml                (RSS 2.0, newest 50)
- site/404.html, robots.txt, sitemap.xml, favicon.svg

Each changed passage is written as a plain removed/added block inside
<div class="diff" data-old=… data-new=…>. build/render-diffs.mjs then
replaces those blocks with @pierre/diffs word diffs; without Node the
plain blocks stay and still read correctly.

The pages are written to site/ in the data directory ($TOS_WATCH_DATA,
default: this repo; see pipeline/lib/paths.py). When that is somewhere else,
the static files (CSS, JS, fonts, services.json) are copied in next to them,
so the data directory's site/ is the whole deployable site.

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
import sys

SITE_DIR = pathlib.Path(__file__).parent
ROOT_DIR = SITE_DIR.parent
WATCHLIST_PATH = ROOT_DIR / "pipeline" / "watchlist.json"
sys.path.insert(0, str(ROOT_DIR / "pipeline"))
from lib.noise import drop_change  # noqa: E402
API_BASE = "https://api.tos.watch"
BASE_URL = "https://tos.watch"
# Stripe Payment Link, "tos.watch founding supporter": customer picks the amount, default $5.
SUPPORT_URL = "https://buy.stripe.com/8x2bJ21z9gJn8ir7f6bQY0b"
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
    # Navigation and table-of-contents passages are not evidence: left off the page.
    fields["changes"] = [c for c in parse_changes(body) if not drop_change(c["old"], c["new"])]
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
    # A link cut at the start of the excerpt ("app](https://…)"): keep the word, drop the URL.
    s = re.sub(r"\]\([^)]*(\)|…$)", "", s)
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


def load_verification(alerts_dir: pathlib.Path) -> dict | None:
    """pipeline/verification.json beside the alerts (pipeline/verify_alerts.py),
    or None when this install doesn't run the check."""
    path = alerts_dir.parent / "pipeline" / "verification.json"
    return json.loads(path.read_text()) if path.exists() else None


def load_alerts(alerts_dir: pathlib.Path) -> list[dict]:
    """Every alert that may be shown. When the verification file exists, an
    alert is shown only if it passed: no result counts as not passed."""
    entries = [parse_alert(p) for p in sorted(alerts_dir.glob("*.md"))]
    verification = load_verification(alerts_dir)
    if verification is not None:
        entries = [e for e in entries if verification.get(e["stem"], {}).get("status") == "pass"]
        for e in entries:
            e["checked"] = verification[e["stem"]]
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


def change_rows(entries: list[dict], root: str, show_vendor: bool = True, empty: str = "") -> str:
    """A ledger of changes: service, document, topics, date. Reads as a
    table on wide screens and as stacked rows on phones."""
    if not entries:
        return f'<p class="empty">{esc(empty) or "No changes yet."}</p>'
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


def topic_stats(watchlist: dict, by_vendor: dict[str, list[str]], counts: dict[str, int], entries: list[dict]) -> list[dict]:
    """One definition of the numbers on the home page, the topic tiles and
    /services. A service is a watched vendor (each counted once in totals). A
    change is a published, verified alert. A topic's change count is the sum
    of its services' own change counts, so it equals the sum of the rows listed
    under that topic and the number on each service page. A service in two
    topics is listed under both, so topic numbers can add up to more than the
    totals."""
    out = []
    for t in tracks_with_alerts(watchlist, entries):
        vendors = sorted((v for v, ts in by_vendor.items() if t["id"] in ts), key=str.lower)
        out.append({"track": t, "vendors": vendors, "changes": sum(counts.get(v, 0) for v in vendors)})
    return out


def stats_line(n_services: int, n_changes: int) -> str:
    return f"{n_services} services, {n_changes} changes"


DUAL_NOTE = "A service that fits two topics is listed under both, but counted once in the totals."


def coverage_bento(stats: list[dict], total_services: int, entries: list[dict]) -> str:
    """The one bento on the site: a search tile plus a tile per topic."""
    tiles = []
    for st in stats:
        t = st["track"]
        tiles.append(
            f'<a class="tile" href="services.html#{esc(t["id"])}">'
            f'<span class="tile-title">{esc(t["label"])}</span>'
            f'<span class="tile-stats">{stats_line(len(st["vendors"]), st["changes"])}</span></a>'
        )
    return f"""
<section class="coverage">
  <div class="wrap">
    <div class="bento">
      <div class="tile tile-search">
        <div>
          <h2 id="search-heading">Is your app watched?</h2>
          <p class="section-lede">{total_services} services today. Missing one? Ask us to add it.</p>
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
    <p class="form-note">The tiles count the services and changes in each topic. {DUAL_NOTE}</p>
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
PITCH = "Terms of service and privacy policy monitor: we email you the exact words when a watched app changes them."
DEFAULT_DESCRIPTION = PITCH
OG_IMAGE = f"{BASE_URL}/og.png"


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
<meta property="og:image" content="{OG_IMAGE}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="tos.watch, Terms of Service watched">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{OG_IMAGE}">
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
    <p class="footer-links"><span class="footer-brand">{WORDMARK}<span>tos.watch</span></span><a href="{root}feed.xml">RSS</a><a href="{root}self-hosting.html">Self-hosting</a><a href="{root}privacy.html">Privacy</a><a href="{root}terms.html">Terms</a><a href="{root}support.html">Support</a><a href="https://github.com/watthem/tos-watch">Source on GitHub</a><span>Not affiliated with any company we track.</span></p>
    <p class="footer-fine">{ATTRIBUTION_HTML} Diffs by <a href="https://diffs.com">@pierre/diffs</a> (Apache-2.0).</p>
  </div>
</footer>
<script>window.TOS_WATCH_API_BASE = "{API_BASE}";</script>
{scripts}
</body>
</html>
"""


def tracks_with_alerts(watchlist: dict, entries: list[dict]) -> list[dict]:
    """The tracks shown on the site: only those with at least one published
    alert. Every track is still watched and still has its service pages; a
    topic is listed once there is something real to show for it."""
    have = {e.get("track") for e in entries}
    return [t for t in watchlist.get("tracks", []) if t["id"] in have]


def track_blurb(track: dict) -> str:
    return re.sub(r"\s*--\s*", ": ", track.get("description", ""))


# ---------------------------------------------------------------- pages

def render_home(entries: list[dict], watchlist: dict, by_vendor: dict[str, list[str]], counts: dict[str, int] | None = None) -> str:
    counts = counts or {}
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
      <p class="kicker">tos.watch means Terms of Service, watched</p>
      <h1>Know when an app changes its terms or privacy policy.</h1>
      <p class="lede">{PITCH}</p>
      <form id="subscribe" class="signup-form" data-source="landing-page" novalidate>
        <div class="field-row">
          <label for="email" class="visually-hidden">Email address</label>
          <input id="email" name="email" type="email" placeholder="you@example.com" required autocomplete="email">
          <button class="button" type="submit">Subscribe</button>
        </div>
      </form>
      <p class="form-note" id="form-note">Free. One email per real change, across every topic we watch.</p>
      <p class="form-links"><a href="https://github.com/watthem/tos-watch">Open source</a><a href="privacy.html">Privacy</a></p>
      <p class="form-note support-note">tos.watch is built and run by one person. If it saves you from missing a change that matters, you can <a href="{SUPPORT_URL}" rel="noopener">chip in from $1</a>. It keeps the alerts coming and pays for the email service. No account needed.</p>
    </div>
    {example}
  </div>
</section>

{coverage_bento(topic_stats(watchlist, by_vendor, counts, entries), len(by_vendor), entries)}

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

<section class="signals">
  <div class="wrap narrow">
    <h2>Help decide what comes next</h2>
    <p class="section-lede">Two things we might build. Tick any you'd use, add your email once, and we count it as a vote. Nothing is built or charged yet.</p>
    <form class="signal-form" novalidate>
      <fieldset class="signal-options">
        <legend class="visually-hidden">Which would you use?</legend>
        <label class="signal-option"><input type="checkbox" name="feature" value="founding-supporter"><span><strong>A paid supporter tier.</strong> tos.watch is free and run by one person. If a small paid tier opens, I'd join.</span></label>
        <label class="signal-option"><input type="checkbox" name="feature" value="vendor-watch"><span><strong>Vendor watchlists for teams.</strong> A private list of the services your company relies on, with alerts to your inbox or a webhook. I'd use this.</span></label>
      </fieldset>
      <div class="field-row">
        <label for="signal-email" class="visually-hidden">Your email</label>
        <input id="signal-email" name="email" type="email" placeholder="you@example.com" required autocomplete="email">
        <button class="button button-secondary" type="submit">Send my vote</button>
      </div>
    </form>
    <p class="request-note" id="signal-note"></p>
    <p class="form-note">What happens next: we email you one confirmation link if you're new here, and your vote counts once you confirm. Confirming also subscribes you to alerts; unsubscribe any time. <a href="privacy.html">Privacy</a>.</p>
  </div>
</section>
"""
    return layout(
        "tos.watch: know when an app changes its terms or privacy policy",
        body,
        description=PITCH,
        scripts='<script src="subscribe.js"></script>\n<script src="search.js"></script>\n<script src="signals.js"></script>',
        path="index.html",
    )


def how_checked_html(e: dict) -> str:
    c = e.get("checked")
    if not c:
        return ""
    return (
        '<h2 class="h3">How this was checked</h2>'
        f'<p class="section-lede">The change is dated {esc(nice_date(e.get("date", "")))}, the day the new version of the document was archived. '
        f'A program, not a person, ran the check below on {esc(nice_date(c.get("checked_on", "")))}. '
        "Documents are fetched about once a day. The program matched every quoted passage word for word "
        "against the archived copy of the document, and confirmed that at least one passage is new text, "
        "absent from the version before. That is the guarantee: the quotes and the Sources above are the evidence.</p>"
        '<p class="section-lede">The topic labels (AI, Data use, Your rights and the rest) are different. '
        "An AI model assigns them, and it can be wrong. The program does not check them. "
        "Trust the quoted words, and read the original if a label matters to you.</p>"
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
  {how_checked_html(e)}
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
            f"{vendor} changed its {doc} on {nice_date(e.get('date', ''))}. "
            f"{n} passage{'s' if n != 1 else ''} changed{flagged}. See the exact words removed and added."
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
    <p class="form-note">Free. You get every alert we send, {esc(vendor)}’s included. One email per real change. Unsubscribe with one click. <a href="{root}privacy.html">Privacy</a>.</p>
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


def render_services(watchlist: dict, by_vendor: dict[str, list[str]], counts: dict[str, int], entries: list[dict]) -> str:
    def group_html(t: dict, vendors: list[str]) -> str:
        items = "".join(
            f'<li><a href="s/{slugify(v)}.html"><span>{esc(v)}</span> '
            + (
                f'<span class="muted">{counts[v]} change{"s" if counts[v] != 1 else ""}</span>'
                if counts.get(v, 0)
                else '<span class="muted">watching, nothing flagged yet</span>'
            )
            + "</a></li>"
            for v in vendors
        )
        n_changes = sum(counts.get(v, 0) for v in vendors)
        return (
            f'<section class="service-group" id="{esc(t["id"])}"><h2 class="h3">{esc(t["label"])}</h2>'
            f'<p class="muted">{esc(track_blurb(t))}</p>'
            f'<p class="group-stats">{stats_line(len(vendors), n_changes)}</p><ul class="service-list">{items}</ul></section>'
        )

    groups = [group_html(st["track"], st["vendors"]) for st in topic_stats(watchlist, by_vendor, counts, entries) if st["vendors"]]
    # Topics with no published alert yet: still watched, so named here with
    # their services instead of dropped.
    shown = {st["track"]["id"] for st in topic_stats(watchlist, by_vendor, counts, entries)}
    quiet = []
    for t in watchlist.get("tracks", []):
        vendors = sorted((v for v, ts in by_vendor.items() if t["id"] in ts), key=str.lower)
        if t["id"] not in shown and vendors:
            quiet.append((t, vendors))
    total = len(by_vendor)
    quiet_text = ""
    if quiet:
        names = " and ".join(f"{t['label']} ({len(vs)} services)" for t, vs in quiet)
        quiet_text = f" {names} has no flagged change yet, so it is listed last." if len(quiet) == 1 else f" {names} have no flagged change yet, so they are listed last."
        groups += [group_html(t, vs) for t, vs in quiet]
    lede = (
        f"We watch {total} services, grouped by topic. {DUAL_NOTE}{quiet_text} "
        'Missing one? <a href="index.html#search-heading">Search for it</a> and ask us to add it.'
    )
    description = f"The {total} apps and services tos.watch watches for privacy policy and terms changes, grouped by topic."
    body = f"""
<div class="wrap page">
  <div class="narrow-left">
    <h1>Services we watch</h1>
    <p class="section-lede">{lede}</p>
  </div>
  <div class="service-groups">{"".join(groups)}</div>
</div>
"""
    return layout(
        "Services we watch: tos.watch",
        body,
        description=description,
        path="services.html",
    )


def watched_documents(watchlist: dict, vendor: str) -> list[tuple[str, str]]:
    """(document name, source URL) for every document watched for a vendor."""
    docs = []
    for key in ("documents", "web_pages", "direct_pages"):
        for d in watchlist.get(key, []):
            if d["vendor"] == vendor:
                docs.append((d.get("doc", ""), d.get("source_url") or d.get("url") or ""))
    return sorted(set(docs), key=lambda x: x[0].lower())


def documents_html(docs: list[tuple[str, str]], last_checked: str) -> str:
    if not docs:
        return ""
    items = "".join(
        f'<li><a href="{esc(url)}" rel="noopener">{esc(name)}</a></li>' if url.startswith("https://") else f"<li>{esc(name)}</li>"
        for name, url in docs
    )
    when = f" Last checked {esc(nice_date(last_checked))}." if last_checked else ""
    return (
        '<h2 class="h3">Documents we watch</h2>'
        f'<p class="section-lede">We fetch {"this document" if len(docs) == 1 else f"these {len(docs)} documents"} about once a day and compare each new version with the one before.{when}</p>'
        f'<ul class="plain-list">{items}</ul>'
    )


def render_service_page(vendor: str, tracks: list[str], track_labels: dict[str, str], entries: list[dict],
                        docs: list[tuple[str, str]] | None = None, last_checked: str = "") -> str:
    slug = slugify(vendor)
    labels = [track_labels.get(t, t) for t in tracks]
    docs = docs or []
    if entries:
        empty = ""
    else:
        empty = (
            f"We checked {'its document' if len(docs) == 1 else 'its documents'}"
            + (f" on {nice_date(last_checked)}" if last_checked else "")
            + " and found nothing to flag. We are still watching, and a change will show up here."
        )
    body = f"""
<div class="wrap narrow page">
  <p class="crumbs"><a href="../services.html">Services</a></p>
  <h1>{esc(vendor)}</h1>
  <p class="section-lede">Listed under {esc(" and ".join(labels) or "no topic yet")}. {len(entries)} change{"s" if len(entries) != 1 else ""} flagged so far.</p>
  {service_signup(vendor, slug, ",".join(tracks), "../")}
  <h2 class="h3">Changes</h2>
  {change_rows(entries, "../", show_vendor=False, empty=empty)}
  {documents_html(docs, last_checked)}
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


LEGAL_UPDATED = "October 30, 2026" # bump when the text of privacy/terms changes


def render_privacy() -> str:
    body = f"""
<div class="wrap narrow page">
  <h1>Privacy at tos.watch</h1>
  <p class="muted"><em>Last updated: {LEGAL_UPDATED}</em></p>
  <p>tos.watch is a free newsletter that tells you when a company changes its terms or privacy policy. It's run by Matthew Hendricks, a sole proprietorship in Washington State, USA. Contact: <a href="mailto:hello@tos.watch">hello@tos.watch</a>.</p>
  <p>We watch what other companies do with your data, so here's exactly what we do with yours.</p>

  <h2 class="h3">What we collect</h2>
  <ul>
    <li><strong>Your email address</strong>, when you subscribe, vote for a feature, or give an email with a service request.</li>
    <li><strong>Which topics you picked, and where you signed up</strong> (the home page or a service's page). These are labels on your record at Buttondown.</li>
    <li><strong>What you asked us to track</strong>, if you use the request box. We store the service name or link you typed, as an anonymous count that isn't tied to any email. If you also gave an email, your Buttondown record gets a label naming the service (or a simplified form of the link).</li>
    <li><strong>Feature votes.</strong> We count each vote per feature, with no email attached. If you gave an email, your Buttondown record gets a label for that vote.</li>
  </ul>
  <p>If you vote or request something with an email we don't have yet, Buttondown sends you a confirmation email, and confirming also subscribes you to alerts.</p>
  <p>tos.watch sets no cookies and runs no advertising or tracking scripts, and our fonts are hosted by us. We do count visits with Cloudflare Web Analytics, which is cookieless. It records which pages are viewed, the site that sent the visitor, and the country, browser and device type. It is run by Cloudflare, the company that hosts the site.</p>

  <h2 class="h3">Who else handles it</h2>
  <ul>
    <li><strong>Buttondown</strong> stores our subscriber list and sends the newsletter and the confirmation email. It receives your email address, the labels above, and your IP address, which it uses to block spam signups. See <a href="https://buttondown.com/legal/privacy">Buttondown's privacy policy</a>.</li>
    <li><strong>Stripe</strong> handles payments if you choose to chip in from the <a href="support.html">support page</a>. You enter your card details on Stripe's page, not ours, and we never see them. Stripe tells us the amount and the name or email you give it. See <a href="https://stripe.com/privacy">Stripe's privacy policy</a>.</li>
    <li><strong>Cloudflare</strong> hosts the site and our signup service. Like any host, it processes your IP address and basic request details to deliver pages and stop abuse. Our signup service also keeps your IP address in memory for about a minute to limit repeated signups; it is never written to a database or file. See <a href="https://www.cloudflare.com/privacypolicy/">Cloudflare's privacy policy</a>.</li>
  </ul>
  <p>We don't sell, rent or share your email with anyone else, and we don't use it for anything but tos.watch.</p>
  <p>Buttondown and the email services that deliver or display our emails may record whether an email was opened and which links were clicked, under their own settings and policies.</p>

  <h2 class="h3">How long we keep it</h2>
  <p>Your email address is kept only at Buttondown, until you unsubscribe or ask us to delete it. Your signup source, topic choices, votes and requests are labels on that one record, so deleting it removes them too. Buttondown keeps a record that an address unsubscribed, so it doesn't email you again unless you ask. Our own database and logs don't hold your email address. The counts of requested services and voted features stay, because they contain nothing about you.</p>

  <h2 class="h3">Your choices</h2>
  <ul>
    <li>Every email has an unsubscribe link.</li>
    <li>Write to <a href="mailto:hello@tos.watch">hello@tos.watch</a> to see, correct or delete what we hold about you. We'll answer within 30 days.</li>
    <li>If you're in the EU or UK, we rely on your consent to email you, and you can withdraw it any time by unsubscribing. You can also complain to your data protection authority.</li>
  </ul>

  <h2 class="h3">Children</h2>
  <p>tos.watch isn't meant for children under 13, and we don't knowingly collect their email addresses.</p>

  <h2 class="h3">Changes</h2>
  <p>If this page changes in a way that affects you, we'll say so in the newsletter. The code that runs the site is <a href="https://github.com/watthem/tos-watch">open source</a>. See also the <a href="terms.html">terms of use</a>.</p>
</div>
"""
    return layout(
        "Privacy: tos.watch",
        body,
        description="What tos.watch collects when you subscribe, who handles it, and how to have it deleted.",
        path="privacy.html",
    )


def render_terms() -> str:
    body = f"""
<div class="wrap narrow page">
  <h1>Terms of use</h1>
  <p class="muted"><em>Last updated: {LEGAL_UPDATED}</em></p>
  <p>By using tos.watch or subscribing to the newsletter, you agree to these terms.</p>

  <h2 class="h3">What tos.watch is</h2>
  <p>tos.watch watches public terms of service and privacy policies and tells you when they change. Each alert shows a short excerpt of what changed and links to the full text.</p>

  <h2 class="h3">It isn't legal advice</h2>
  <p>Alerts are summaries and excerpts, not legal advice. Our filter decides which changes are worth an email, and it can be wrong: it can miss a change, flag one that doesn't matter, or describe one imperfectly. Always read the company's own current terms before relying on them. If a decision matters, talk to a lawyer.</p>

  <h2 class="h3">No warranty</h2>
  <p>tos.watch is free and provided as is. We don't promise it will be complete, accurate, timely or always available. To the extent the law allows, we aren't liable for any loss from using it or relying on it.</p>

  <h2 class="h3">Where the text comes from</h2>
  <p>Policy text and version history come from <a href="https://opentermsarchive.org/">Open Terms Archive</a> and its contributing collections, under the <a href="https://opendatacommons.org/licenses/by/1-0/">Open Data Commons Attribution License (ODC-By 1.0)</a>, and from pages we fetch ourselves. The terms themselves belong to the companies that wrote them. We quote short excerpts and link to the source.</p>

  <h2 class="h3">Fair use of the site</h2>
  <p>Don't abuse the signup or request forms, sign up addresses that aren't yours, or try to break the service. We can remove a subscription or block access if you do.</p>

  <h2 class="h3">The code</h2>
  <p>The code behind tos.watch is open source under the <a href="https://github.com/watthem/tos-watch/blob/main/LICENSE">Apache License 2.0</a>. That license covers the code, not the alert history or the newsletter.</p>

  <h2 class="h3">Changes</h2>
  <p>We may update these terms. The date at the top shows the latest version, and meaningful changes will be announced in the newsletter.</p>
  <p>Questions: <a href="mailto:hello@tos.watch">hello@tos.watch</a>. How we handle your data is on the <a href="privacy.html">privacy page</a>.</p>
</div>
"""
    return layout(
        "Terms of use: tos.watch",
        body,
        description="The terms for using tos.watch and its newsletter.",
        path="terms.html",
    )


def render_support() -> str:
    body = f"""
<div class="wrap narrow page">
  <h1>Support tos.watch</h1>
  <p><strong>Support tos.watch.</strong> Alerts are free. If you want to help keep them running, pick an amount from $1. Payments go through Stripe; tos.watch never sees your card. This is a voluntary contribution, not a subscription, and it doesn&rsquo;t change what you receive. Questions: reply to any alert email.</p>
  <p><a class="button" href="{SUPPORT_URL}" rel="noopener">Chip in from $1</a></p>
</div>
"""
    return layout(
        "Support: tos.watch",
        body,
        description="Alerts are free. Chip in from $1 to help keep tos.watch running.",
        path="support.html",
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


# Hand-written files in this repo's site/ that every deploy needs.
STATIC_FILES = ["og.png", "style.css", "diffs.css", "subscribe.js", "search.js", "signals.js", "services.json"]


def copy_static(site_dir: pathlib.Path) -> None:
    """Copy the code repo's static files into a site/ that lives elsewhere."""
    if site_dir.resolve() == SITE_DIR.resolve():
        return
    for name in STATIC_FILES:
        shutil.copyfile(SITE_DIR / name, site_dir / name)
    shutil.copytree(SITE_DIR / "fonts", site_dir / "fonts", dirs_exist_ok=True)


def render_diffs(code_root: pathlib.Path, site_dir: pathlib.Path) -> str:
    script = code_root / "build" / "render-diffs.mjs"
    if not shutil.which("node") or not (code_root / "build" / "node_modules").exists():
        return "diffs: node or build/node_modules missing, plain fallback kept"
    res = subprocess.run(["node", str(script), str(site_dir)], capture_output=True, text=True)
    return (res.stdout or res.stderr).strip()


def check_counts_agree(site_dir: pathlib.Path) -> None:
    """Build-time assertion: the service and change numbers on the home page,
    its topic tiles and /services must agree (three pages once disagreed).
    Reads the rendered HTML, so it checks what visitors see."""
    home = (site_dir / "index.html").read_text()
    svc = (site_dir / "services.html").read_text()
    total = int(re.search(r"(\d+) services today", home).group(1))
    lede_total = int(re.search(r"We watch (\d+) services", svc).group(1))
    assert total == lede_total, f"home says {total} services, /services says {lede_total}"
    tiles = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(
        r'<a class="tile" href="services\.html#([\w-]+)">.*?(\d+) services, (\d+) changes', home)}
    groups = re.findall(r'<section class="service-group" id="([\w-]+)">(.*?)</section>', svc, re.S)
    by_id = {}
    for gid, inner in groups:
        stats = re.search(r"(\d+) services, (\d+) changes", inner)
        rows = re.findall(r'<li><a href="s/([\w-]+)\.html">.*?(?:(\d+) changes?|watching, nothing flagged yet)</span>', inner)
        by_id[gid] = (int(stats.group(1)), int(stats.group(2)), rows)
        assert len(rows) == int(stats.group(1)), f"/services {gid}: {len(rows)} rows but header says {stats.group(1)}"
        assert sum(int(n or 0) for _, n in rows) == int(stats.group(2)), f"/services {gid}: rows do not add up to the header"
    for tid, (n, c) in tiles.items():
        assert tid in by_id and by_id[tid][:2] == (n, c), f"tile {tid} {(n, c)} disagrees with /services {by_id.get(tid, ('missing',))[:2]}"
    listed = {slug for _, _, rows in by_id.values() for slug, _ in rows}
    assert len(listed) == total, f"/services lists {len(listed)} distinct services, home says {total}"


def build(code_root: pathlib.Path, data_root: pathlib.Path | None = None) -> None:
    data_root = data_root or code_root
    site_dir = data_root / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    copy_static(site_dir)
    entries = load_alerts(data_root / "alerts")
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
    state_path = data_root / "pipeline" / "state.json"
    last_checked = ""
    if state_path.exists():
        last_checked = str(json.loads(state_path.read_text()).get("last_run", ""))[:10]
    for vendor, tracks in by_vendor.items():
        page = render_service_page(vendor, tracks, track_labels, per_vendor.get(vendor, []),
                                   watched_documents(watchlist, vendor), last_checked)
        (site_dir / "s" / f"{slugify(vendor)}.html").write_text(page)

    counts = {v: len(es) for v, es in per_vendor.items()}
    (site_dir / "index.html").write_text(render_home(entries, watchlist, by_vendor, counts))
    (site_dir / "services.html").write_text(render_services(watchlist, by_vendor, counts, entries))
    (site_dir / "self-hosting.html").write_text(render_self_hosting())
    (site_dir / "privacy.html").write_text(render_privacy())
    (site_dir / "terms.html").write_text(render_terms())
    (site_dir / "support.html").write_text(render_support())
    (site_dir / "feed.xml").write_text(render_feed(entries))
    (site_dir / "404.html").write_text(render_404())
    (site_dir / "favicon.svg").write_text(FAVICON_SVG)
    (site_dir / "robots.txt").write_text(render_robots())
    pages = [("index.html", ""), ("alerts/index.html", entries[0].get("date", "") if entries else "")]
    pages += [(f"alerts/{e['stem']}.html", e.get("date", "")) for e in entries]
    pages += [("services.html", ""), ("self-hosting.html", ""), ("privacy.html", ""), ("terms.html", ""), ("support.html", "")]
    pages += [(f"s/{slugify(v)}.html", "") for v in sorted(by_vendor, key=str.lower)]
    (site_dir / "sitemap.xml").write_text(render_sitemap(pages))
    copy_fonts(site_dir)
    check_counts_agree(site_dir)
    print(f"site build: {len(entries)} alerts, {len(by_vendor)} service pages")
    print(render_diffs(code_root, site_dir))


if __name__ == "__main__":
    import os

    code = pathlib.Path(__file__).resolve().parent.parent
    build(code, pathlib.Path(os.environ.get("TOS_WATCH_DATA") or code).expanduser().resolve())
