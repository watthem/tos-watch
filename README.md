# tos.watch

Get an email when a company changes what it does with your data.
The newsletter is free at [tos.watch](https://tos.watch). The code is
Apache-2.0 (see [LICENSE](LICENSE)).

tos.watch watches about 40 services in five tracks: AI assistants, dating
apps, things you type into (writing tools, keyboards, notes, translation,
transcription, email), developer tools (telemetry, training on your code,
credit-based pricing), and the broad consumer apps most people use every day.
The exact list is `pipeline/watchlist.json`.

Not affiliated with any company it tracks.

## What's here

- `pipeline/`: Python 3, stdlib only. For each tracked document it:
  - pulls the history from [Open Terms Archive](https://opentermsarchive.org/en/), or fetches the page itself when OTA has no declaration (`pipeline/lib/direct.py`);
  - diffs it paragraph by paragraph;
  - asks the [Jev](https://openrouter.ai) classifier a few narrow questions about each real change;
  - drops scraper flap and duplicate captures;
  - writes one alert per document version to `alerts/`.
- `pipeline/questions.example.json`: the questions Jev answers. It's a generic set that runs but isn't tuned; see "Questions" below.
- `pipeline/draft_emails.py`: turns each new alert into a [Buttondown](https://buttondown.com) **draft** email. Nothing is ever sent automatically.
- `site/build.py`: builds the static site from `alerts/`:
  - a landing page with search;
  - a page per service and per change, with word-level diffs by [`@pierre/diffs`](https://www.npmjs.com/package/@pierre/diffs), Apache-2.0;
  - an RSS feed.
- `worker/`: a Cloudflare Worker for signup, site requests, feature votes, and a mock on-demand check. Emails live only in Buttondown; D1 holds anonymous request counts.
- [SELF_HOSTING.md](SELF_HOSTING.md): run your own copy for the documents you care about.
- [ROADMAP.md](ROADMAP.md): what's deliberately not built yet, and why.

## What isn't here

tos.watch is open core. The public code runs end to end. What stays private is the judgment built on top of it:
- the tuned question set, and the hand labels it was checked against;
- the dataset-selection work;
- the alert history, with its classifier scores;
- the hosted service.

The alerts themselves are public on [tos.watch](https://tos.watch).

## Code here, data elsewhere

This repo is code only. Everything a run reads or writes, apart from `pipeline/watchlist.json`, lives in a data directory set by `TOS_WATCH_DATA`: alerts, the Jev cache, run state, snapshots, your own `questions.json` and the generated site. Its layout mirrors this repo (`alerts/`, `cache/jev/`, `pipeline/state.json`, `site/`…). Unset, the data directory is this repo, and all of it is gitignored. tos.watch keeps its data in a private repo; see `pipeline/lib/paths.py`.

`scripts/check-public.sh` runs in CI and fails if run data, generated pages, local paths or personal addresses are ever committed here.

## Data sources and attribution

Policy history comes from [Open Terms Archive](https://opentermsarchive.org/en/) collections, including:
- the [Platform Governance Archive](https://www.platformgovernancearchive.org/) (ZeMKI, University of Bremen);
- the GenAI Governance Archive.

They're licensed [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/), which requires attribution for any public use. The site footer, the feed and every alert carry it. Alerts quote short excerpts cut to the part that changed, never whole documents.

The fonts are Public Sans and Source Serif 4, both SIL OFL 1.1. They're self-hosted, so the site makes no third-party requests. Their licences are in `site/fonts/`.

## Running the pipeline

```
export OPENROUTER_API_KEY=...
python3 pipeline/run.py             # pull, diff, score, write alerts, rebuild the site
python3 pipeline/run.py --dry-run   # collect, extract and filter; skip scoring and alert writing
python3 pipeline/run.py --no-pull   # reuse OTA clones; HTTP sources are still collected
```

`--dry-run` is not an offline or read-only mode: source collection runs
before the dry-run exit, so it can clone/pull OTA history, fetch HTTP
sources and write source snapshots. `--no-pull` only skips OTA clone/pull;
it does not skip the HTTP source collectors. `--out` also creates its
alerts directory during a dry run.

Reruns are cheap. Jev answers are cached on disk by a hash of the change (`cache/jev/`), and each alert file is written once per document version. `--since YYYY-MM-DD` limits Jev spend on a large backfill.

### Questions

`pipeline/lib/jev.py` loads its question set from `pipeline/questions.json`, falling back to `pipeline/questions.example.json`.

A question set has four parts:
- a `version`, which goes into the cache key, so bump it when you change wording;
- the `questions` themselves;
- the `topic_questions` that can trigger an alert;
- an `alert_threshold`.

The example set is a starting point. Before trusting a threshold, check your questions against changes you've labelled by hand.

### Email drafts

```
export BUTTONDOWN_API_KEY=...
python3 pipeline/draft_emails.py             # one draft per new alert
python3 pipeline/draft_emails.py --dry-run   # print the drafts; no API call, no state written
python3 pipeline/test_draft_emails.py        # new alert -> one draft; rerun -> none
```

Without `BUTTONDOWN_API_KEY` it's a dry run that exits 0. The data directory's
`pipeline/drafts_state.json` records which alerts already have drafts. The
first authenticated run marks existing history as handled without drafting
it; later runs draft new alerts once.

## Site

```
(cd build && npm ci)   # locked fonts and diff-renderer dependencies
python3 site/build.py       # alert pages, service pages, RSS feed
```

Python builds the site without Node dependencies, keeping a plain diff
fallback. Installing `build/` dependencies enables `@pierre/diffs` rendering.

The pages go to `$TOS_WATCH_DATA/site/`, along with copies of the static files from this repo's `site/`, so that folder is the whole deployable site. tos.watch deploys it to Cloudflare Pages.

## Search, request, and the on-demand check

The search box reads the `site/services.json` catalog in the browser, so it's instant. There are three outcomes:
- **Tracked:** a link to that service's page, with a subscribe form tagged to its tracks.
- **In Open Terms Archive but not tracked:** a link to that history, plus a request form.
- **Not found:** a request form with an optional URL.

`GET /check?url=` is a **mock**: it never fetches any URL, and a test asserts that. It only looks the domain up in the same bundled catalog. A public endpoint that fetches whatever URL a stranger pastes in is a classic SSRF and abuse vector, so it's deliberately not built. See [ROADMAP.md](ROADMAP.md).

## Worker

```
cd worker
npm ci
npm test          # vitest with the Workers pool (Miniflare), no network
npm run db:migrate:local
npm run dev       # wrangler dev with a local D1
npm run deploy    # wrangler deploy
```

Endpoints:
- `POST /subscribe` takes `{email, source?, tracks?[]}`. It returns HTTP 200 with `{ok: true, next: "confirm"}` for a successful creation, an existing address, or a skipped call when no API key is set. This response does not prove delivery of a confirmation email. Buttondown firewall rejection returns HTTP 422 with `error: "blocked"`; if the request to create the subscriber fails (an error status or a network failure), it returns HTTP 503 with `error: "unavailable"`. For an address that already exists, the follow-up tag update is best effort: if it fails, the failure is only logged and the response is still HTTP 200, so the requested `tracks` may not have been saved.
- `POST /request` takes `{service?, url?, email?}`.
- `POST /vote` takes `{feature, email}`.
- `GET /check?url=`: the mock above.

No route lists or exports subscriber emails, requests, or votes.

The Worker never stores or logs an email address. Buttondown is the only list: the signup source, `track:<id>` topics, `vote:<feature>` and `requested:<key>` are tags on the Buttondown subscriber. A new address gets Buttondown's double opt-in, so a vote counts once its subscriber is confirmed. A vote never retags or resubscribes someone who unsubscribed. The response never reveals whether an address was already known.

The D1 schema is in `worker/migrations/`. Apply it with `npm run db:migrate:local` or `npm run db:migrate:remote`.

Buttondown sends the double opt-in email and handles unsubscribes. Set the key with:

```
npx wrangler secret put BUTTONDOWN_API_KEY
```

Without it, the Worker skips the Buttondown call and keeps no email at all.

## Local validation

CI uses Node 22 for the Worker. Install the locked dependencies in `build/`
and `worker/`, then run these from the repository root:

```sh
python3 pipeline/test_draft_emails.py
(cd worker && WRANGLER_SEND_METRICS=false npm test)
bash scripts/check-public.sh
```

The Python suite has four tests with mocked Buttondown calls. The Worker
suite has 23 tests across signup, requests and catalog checks, using local
D1 migrations and mocked external calls. These checks do not validate live
Buttondown delivery or OpenRouter classification. The pinned Workers test
pool currently warns that it falls back to compatibility date `2025-03-10`
from the configured `2026-09-01`; it does not test newer compatibility behavior.

With `npm run dev` running in `worker/`, a local smoke check is:

```sh
curl 'http://127.0.0.1:8787/check?url=https%3A%2F%2Fwww.grammarly.com'
```

It returns HTTP 200 with `status: "tracked"` and `slug: "grammarly"`.
An unknown domain returns `status: "unknown"`; a missing URL returns HTTP
400. The endpoint reads the bundled catalog and never fetches the supplied URL.

## Licence

The code is Apache-2.0. Policy text and history from Open Terms Archive stay under ODC-By 1.0, with attribution. The fonts are SIL OFL 1.1.
