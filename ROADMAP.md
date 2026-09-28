# Roadmap: what's deliberately not built yet

## "On demand" checks are a mock, not a live fetch (2026-09-26)

The owner's original ask (chat, 2026-09-26) was "if people search for a
site it takes them to an endpoint to search it on demand." What shipped is
`GET /check?url=` — a **mock**: it never fetches the URL a visitor pastes
in, or any URL. It looks the normalized domain up against the same static
`site/services.json` catalog the site's search box already has client-side,
and for an unknown site, offers a "+1" (`POST /vote`, feature
`on-demand-check`) instead of a dead end.

**Why not build the real thing:** an unauthenticated endpoint that fetches
arbitrary attacker-supplied URLs server-side is a textbook SSRF vector (it
can be pointed at internal/cloud-metadata addresses, used to port-scan, or
abused as a free proxy/DoS amplifier), on top of the ordinary abuse-rate
problem of a public "fetch this URL for me" endpoint. tos.watch's actual
pipeline only ever fetches URLs it already trusts (~40 vendor pages listed
by hand in `watchlist.json`, run on a schedule) — that's a fundamentally
different, much smaller trust surface than "fetch whatever a visitor typed."

**What we'd build instead, if this becomes a priority:** a moderation queue
fed by the `requests` table (already logging every ask) — a human (or a
tightly scoped, rate-limited worker with an allowlist of retryable
statuses) reviews requested URLs before `watchlist.json` ever adds one, the
same as any new tracked vendor today. Real "check this site right now"
would mean triggering an out-of-band pipeline run against one already-
vetted URL, not fetching visitor input directly.

# Roadmap: what Buttondown can't do for tos.watch (yet)

Buttondown (docs.buttondown.com) is the chosen email provider (owner decision,
2026-09-26). This tracks the gaps found while wiring the Worker to it, each
with what we'd build and a citation. Only the first item is (partly) built;
the rest is a queue for when it matters.

- **Alert emails from the pipeline: built as drafts only (2026-09-27).**
  `pipeline/draft_emails.py` runs after `pipeline/run.py` (wired into
  `.github/workflows/watch.yml`, key from the `BUTTONDOWN_API_KEY` repo
  secret) and turns each new alert into one Buttondown email via
  `POST https://api.buttondown.com/v1/emails` with `status: "draft"`. It
  never sends or publishes: the owner reviews and sends each draft by hand
  from Buttondown. Dedup is a committed state file,
  `pipeline/drafts_state.json` (high-water date + already-handled alerts, with
  a 14-day lookback for late OTA crawls), plus an `X-Idempotency-Key` per
  alert; history that existed when it was set up is never drafted. Without
  the key, or with `--dry-run`, it prints what it would draft.
  https://docs.buttondown.com/api-emails-create ,
  https://docs.buttondown.com/api-idempotency-keys
  **Not built:** sending without a human (send-draft or publish),
  https://docs.buttondown.com/api-emails-send-draft ,
  https://docs.buttondown.com/api-emails-publish — or RSS-to-email below
  instead, so the pipeline doesn't need an emails-API call at all.

- **RSS-to-email requires the paid Basic plan ($9/mo, 1,000 subscriber cap),
  not the Free plan (100 subscribers) the account will likely start on.**
  Buttondown can watch `site/feed.xml` and auto-send (or auto-draft) a new
  email whenever a new `<item>` (matched by `guid`) appears, polling every
  30 minutes — which would let the pipeline just regenerate the feed and do
  nothing else.
  https://docs.buttondown.com/rss-to-email ,
  https://buttondown.com/pricing
  **Build (open owner decision, not started):** either upgrade to Basic and
  point an RSS automation at the deployed `feed.xml`, or stay on Free and
  send via the emails API item above instead.

- **Per-vendor/per-document subscriptions aren't a Buttondown gap so much as
  a "we haven't built the preference UI" gap.** Buttondown does support
  targeting a send to subscribers with a given tag (or without one) via
  `filters` on `POST /v1/emails`, and tags exist as a first-class object —
  but tags require the Basic plan or higher, same as RSS-to-email.
  https://docs.buttondown.com/api-tags-introduction ,
  https://docs.buttondown.com/api-emails-introduction (Audience filters)
  **Build:** add a vendor checklist to the signup form, tag each subscriber
  with the vendors they picked (`POST /v1/subscribers` `tags`), and target
  each alert email's `filters` at the matching tag instead of the whole list.

- **Confirmed/unsubscribed status in D1 is not live-synced back from
  Buttondown.** Today D1 only reflects our own token flow; if someone
  confirms or unsubscribes on Buttondown's side (their own double opt-in
  email, their own one-click unsubscribe link), our copy doesn't hear about
  it. Buttondown does support this via webhooks
  (`subscriber.created` / type-change events).
  https://docs.buttondown.com/api-webhooks-introduction ,
  https://docs.buttondown.com/events-and-webhooks-introduction
  **Build:** a `POST /webhooks/buttondown` route that verifies the webhook
  signature and updates the matching D1 row's `status`.

- **100-subscriber cap on the Free plan.** Worth flagging early: a single
  good launch post could clear 100 signups before a Basic-plan decision
  gets made.
  https://buttondown.com/pricing
  **Build:** nothing to build; this is a plan-upgrade decision to make when
  signups approach 100.
