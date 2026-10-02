#!/usr/bin/env python3
"""Turn new alerts into Buttondown *draft* emails. Never sends or publishes.

Run after pipeline/run.py. For every alert in alerts/ that hasn't been
drafted yet and is new enough (see "Which alerts count as new" below), this
creates one draft via Buttondown's emails API. The owner reviews and sends
each draft by hand from the Buttondown dashboard; nothing here can reach a
subscriber's inbox on its own.

API shape (checked 2026-09-27):
  POST https://api.buttondown.com/v1/emails
  Authorization: Token <key>; JSON body; `subject` is the only required
  field; `status: "draft"` means "only visible to you and not sent out to
  subscribers". `body` is Markdown or HTML (auto-detected, or forced with a
  `<!-- buttondown-editor-mode: plaintext -->` prefix), and a body starting
  with `---` frontmatter is rejected with 400 `body_contains_frontmatter`.
  https://docs.buttondown.com/api-emails-create
  `X-Idempotency-Key` makes a repeated request return the first response
  instead of creating a second object.
  https://docs.buttondown.com/api-idempotency-keys

Who a draft is addressed to (checked 2026-10-01)
-------------------------------------------------
`filters` on the create call is a FilterGroup tree:
  {"predicate": "and"|"or", "filters": [{"field": "subscriber.tags",
   "operator": "contains"|"not_contains", "value": <tag ID>}], "groups": [<FilterGroup>...]}
Groups nest, so the OR we need is expressible. What it can NOT do: match a tag
by name or prefix ("any tag starting service:"). Values are tag IDs from
GET /v1/tags, so "has no service:/track: tag" is spelled as an AND of
`not_contains` over every such tag that exists today.
  https://docs.buttondown.com/api-emails-filters
  https://docs.buttondown.com/api-emails-filter
An alert about service S on track T goes to subscribers who have
`service:<slug of S>`, OR `track:<T>`, OR no service:/track: tag at all.
A tag nobody has yet doesn't exist in Buttondown, so it's simply left out.
The alert's track comes from its frontmatter, else from the watchlist
entries for its vendor. If no track can be found, or no service:/track: tags
exist yet, the draft has no filter and goes to everyone: too many readers is
better than missing someone who asked. A failed tag lookup does the same.
Landing-page signups carry all five track:* tags (site/subscribe.js), so
they match whichever track the alert is on.

Which alerts count as new, and why a committed state file
----------------------------------------------------------
pipeline/drafts_state.json holds `high_water` (the newest alert date seen)
and `handled` (alert stems already drafted, or already present when the
drafter was first set up). An alert is drafted when its stem is not in
`handled` and its date is no more than LOOKBACK_DAYS older than
`high_water`. With no state file, the first run only records the current
alerts as handled and drafts nothing, so the back catalog is never drafted.

- A state file, not a Buttondown lookup: dedup then works in --dry-run and
  without a key, costs no API call per run, and the committed file shows
  exactly what was drafted and when (the workflow commits it with the
  alerts). The idempotency key (the alert stem) is a second guard for the
  one gap a file can't cover, a crash between Buttondown's reply and the
  state write; Buttondown's docs don't say how long they keep keys, so it
  isn't relied on across runs.
- The lookback window, not a strict "newer than high_water": OTA
  collections are crawled on different schedules, so a document version
  dated a few days back can arrive after a newer one from another
  collection. A strict mark would silently skip it. The window still keeps
  a backfill (a new vendor's history, a `--since` rerun) out of the drafts,
  and `handled` is pruned to the window so the file stays small.

Usage:
    pipeline/draft_emails.py            # draft new alerts (needs BUTTONDOWN_API_KEY)
    pipeline/draft_emails.py --dry-run  # print what would be drafted; writes nothing

Without BUTTONDOWN_API_KEY in the environment it behaves like --dry-run
and exits 0. Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from lib import paths  # noqa: E402

ROOT = paths.CODE
ALERTS_DIR = paths.ALERTS_DIR
STATE_PATH = paths.DRAFTS_STATE_PATH
API_URL = "https://api.buttondown.com/v1/emails"
TAGS_URL = "https://api.buttondown.com/v1/tags"
LOOKBACK_DAYS = 14
# Other spellings of a track tag known to exist in Buttondown. The worker
# (worker/src/index.js trackTags) writes `track:<watchlist id>`, i.e.
# `track:ai-assistants`, but the 2026-10-01 subscriber shows `track:ai`.
# Unresolved which is live; matching both reaches too many, never too few.
TRACK_TAG_ALIASES = {"ai-assistants": ["track:ai"]}

sys.path.insert(0, str(ROOT / "site"))
import build as site  # noqa: E402  (parse_alert, plain_policy_text, BASE_URL, attribution)


# ---------------------------------------------------------------- rendering

def md_escape(text: str) -> str:
    """Policy text is shown literally, as the site does; stop Markdown from
    reading stray *, _, [, < etc. in it as formatting."""
    text = re.sub(r"([\\`*_\[\]<>#|])", r"\\\1", text)
    return re.sub(r"^([-+=])", r"\\\1", text)


def quote(text: str) -> list[str]:
    """One quoted paragraph per line (bullets stay on their own lines)."""
    lines = site.plain_policy_text(text).splitlines()
    out: list[str] = []
    for line in lines:
        out += [">", f"> {md_escape(line)}"] if out else [f"> {md_escape(line)}"]
    return out or ["> …"]


def attribution_md() -> str:
    return re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"[\2](\1)", site.ATTRIBUTION_HTML)


def render_email(entry: dict) -> dict:
    """The alert page (site/build.py render_alert_page), as a Markdown
    email: same excerpts, same links, same attribution."""
    vendor, doc, date = entry.get("vendor", ""), entry.get("doc", ""), entry.get("date", "")
    page_url = f"{site.BASE_URL}/alerts/{entry['stem']}.html"
    n = len(entry["changes"])

    body = [
        "<!-- buttondown-editor-mode: plaintext -->"
        f"{vendor} changed its {doc}, in the version dated {site.nice_date(date)}. "
        f"{n} passage{'s' if n != 1 else ''} changed; these are excerpts, cut to the part that changed.",
        "",
    ]
    for change in entry["changes"]:
        if change["old"]:
            body += ["**Removed:**", *quote(change["old"]), ""]
        if change["new"]:
            body += ["**Added:**", *quote(change["new"]), ""]
    if not entry["changes"]:
        body += ["This version was flagged, but its changed passages were too long to excerpt. "
                 "The links below show the full text.", ""]
    body += [f"[See the whole change on tos.watch]({page_url})", ""]
    sources = []
    if entry.get("ota_commit_url"):
        sources.append(f"[the full diff on Open Terms Archive]({entry['ota_commit_url']})")
    if entry.get("source_url"):
        sources.append(f"[the current {doc} on {vendor}’s site]({entry['source_url']})")
    if sources:
        body += ["Sources: " + " and ".join(sources) + ".", ""]
    body += [f"*{attribution_md()}*"]

    return {
        "subject": f"{vendor} changed its {doc.lower()}",
        "body": "\n".join(body) + "\n",
        "status": "draft",  # never "about_to_send"/"scheduled": the owner sends by hand
        "metadata": {"tos_watch_alert": entry["stem"]},
    }


# ---------------------------------------------------------------- audience

def alert_tracks(entry: dict) -> list[str]:
    if entry.get("track"):
        return [entry["track"]]
    return site.tracked_vendors(site.load_watchlist()).get(entry.get("vendor", ""), [])


def audience_tags(entry: dict) -> list[str]:
    """Tag names an alert's audience is matched on (not counting the
    untagged subscribers). Empty means: no way to target, send to everyone."""
    tracks = alert_tracks(entry)
    slug = site.slugify(entry.get("vendor", ""))
    if not tracks or not slug:
        return []
    names = [f"service:{slug}"]
    for t in tracks:
        names += [f"track:{t}", *TRACK_TAG_ALIASES.get(t, [])]
    return names


def build_filters(names: list[str], tag_ids: dict[str, str]) -> dict | None:
    """The Buttondown FilterGroup for `names` plus untagged subscribers, or
    None (everyone) when it can't be built safely."""
    scoped = {n: i for n, i in tag_ids.items() if n.startswith(("service:", "track:"))}
    if not names or not scoped:
        return None
    wanted = [{"field": "subscriber.tags", "operator": "contains", "value": scoped[n]}
              for n in names if n in scoped]
    untagged = {"predicate": "and", "groups": [],
                "filters": [{"field": "subscriber.tags", "operator": "not_contains", "value": i}
                            for i in scoped.values()]}
    return {"predicate": "or", "filters": wanted, "groups": [untagged]}


def describe_audience(names: list[str]) -> str:
    if not names:
        return "everyone (no service/track known for this alert)"
    return "tagged " + " or ".join(names) + ", or with no service:/track: tag"


def list_tags(api_key: str) -> dict[str, str]:
    """Buttondown tag name -> id, following pagination."""
    out: dict[str, str] = {}
    url = TAGS_URL
    while url:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Token {api_key}", "User-Agent": "tos-watch-pipeline"})
        with urllib.request.urlopen(req, timeout=30) as res:
            page = json.loads(res.read() or b"{}")
        out.update({t["name"]: t["id"] for t in page.get("results", [])})
        url = page.get("next")
    return out


# ---------------------------------------------------------------- state

def load_state(state_path: pathlib.Path) -> dict | None:
    if state_path.exists():
        return json.loads(state_path.read_text())
    return None


def save_state(state_path: pathlib.Path, state: dict) -> None:
    state_path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def window_start(high_water: str) -> str:
    d = datetime.date.fromisoformat(high_water) - datetime.timedelta(days=LOOKBACK_DAYS)
    return d.isoformat()


def prune(state: dict, dates: dict[str, str]) -> None:
    floor = window_start(state["high_water"])
    state["handled"] = sorted(s for s in state["handled"] if dates.get(s, s[:10]) >= floor)


# ---------------------------------------------------------------- HTTP

def post_draft(payload: dict, idempotency_key: str, api_key: str) -> dict:
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key,
            "User-Agent": "tos-watch-pipeline",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Buttondown returned {err.code}: {detail}") from err


# ---------------------------------------------------------------- main

def run(alerts_dir: pathlib.Path, state_path: pathlib.Path, api_key: str | None,
        dry_run: bool = False, post=post_draft, tags=list_tags) -> list[dict]:
    """Draft every new alert; return the payloads drafted (or, in a dry
    run, that would be). State is saved after each successful draft so a
    failure part-way never re-drafts the ones before it."""
    entries = site.load_alerts(alerts_dir)
    dates = {e["stem"]: e.get("date", "") for e in entries}
    state = load_state(state_path)

    if state is None:
        newest = max(dates.values(), default=datetime.date.today().isoformat())
        state = {"high_water": newest, "handled": list(dates)}
        prune(state, dates)
        print(f"No draft state yet: marking {len(dates)} existing alert(s) as handled "
              f"(high-water {newest}); nothing drafted on this first run.")
        if not dry_run:
            save_state(state_path, state)
        return []

    floor = window_start(state["high_water"])
    handled = set(state["handled"])
    new = sorted((e for e in entries if e["stem"] not in handled and e.get("date", "") >= floor),
                 key=lambda e: (e.get("date", ""), e["stem"]))
    if not new:
        print("No new alerts to draft.")
        return []

    drafted = []
    tag_ids: dict[str, str] | None = None
    for e in new:
        payload = render_email(e)
        names = audience_tags(e)
        if dry_run:
            print(f"--- would draft {e['stem']} ---")
            print(f"audience: {describe_audience(names)}")
            print(json.dumps({k: v for k, v in payload.items() if k != "body"}, ensure_ascii=False))
            print(payload["body"])
            drafted.append(payload)
            continue
        if names and tag_ids is None:
            try:
                tag_ids = tags(api_key)
            except Exception as err:  # reach everyone rather than fail or skip the alert
                print(f"Could not list Buttondown tags ({err}); drafting to everyone.")
                tag_ids = {}
        filters = build_filters(names, tag_ids or {})
        if filters:
            payload["filters"] = filters
        print(f"Audience for {e['stem']}: {describe_audience(names) if filters else 'everyone'}")
        res = post(payload, f"tos-watch-alert-{e['stem']}", api_key)
        print(f"Drafted {e['stem']} as Buttondown email {res.get('id', '?')} (status {res.get('status', '?')})")
        drafted.append(payload)
        state["handled"] = sorted(set(state["handled"]) | {e["stem"]})
        state["high_water"] = max(state["high_water"], e.get("date", ""))
        prune(state, dates)
        save_state(state_path, state)
    return drafted


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print what would be drafted; no API call, no state write")
    args = ap.parse_args()
    api_key = os.environ.get("BUTTONDOWN_API_KEY", "").strip() or None
    dry_run = args.dry_run or api_key is None
    if api_key is None and not args.dry_run:
        print("BUTTONDOWN_API_KEY is not set: dry run, nothing will be drafted.")
    drafted = run(ALERTS_DIR, STATE_PATH, api_key, dry_run=dry_run)
    verb = "would be drafted" if dry_run else "drafted"
    print(f"{len(drafted)} alert email(s) {verb}.")


if __name__ == "__main__":
    main()
