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
- `worker/`: a Cloudflare Worker and D1 database for signup, site requests, feature votes, and a mock on-demand check.
- [SELF_HOSTING.md](SELF_HOSTING.md): run your own copy for the documents you care about.
- [ROADMAP.md](ROADMAP.md): what's deliberately not built yet, and why.

## What isn't here

tos.watch is open core. The public code runs end to end. What stays private is the judgment built on top of it:
- the tuned question set, and the hand labels it was checked against;
- the dataset-selection work;
- the alert history, with its classifier scores;
- the hosted service.

The alerts themselves are public on [tos.watch](https://tos.watch).

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
python3 pipeline/run.py --dry-run   # extract and filter only: no Jev calls, no writes
python3 pipeline/run.py --no-pull   # reuse what's already cloned in cache/
```

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

Without `BUTTONDOWN_API_KEY` it's a dry run that exits 0. `pipeline/drafts_state.json` records which alerts already have drafts, and a first run over existing history drafts nothing.

## Site

```
(cd build && npm install)   # fonts and the diff renderer
python3 site/build.py       # alert pages, service pages, RSS feed
```

tos.watch deploys the `site/` output to Cloudflare Pages.

## Search, request, and the on-demand check

The search box reads the `site/services.json` catalog in the browser, so it's instant. There are three outcomes:
- **Tracked:** a link to that service's page, with a subscribe form tagged to its tracks.
- **In Open Terms Archive but not tracked:** a link to that history, plus a request form.
- **Not found:** a request form with an optional URL.

`GET /check?url=` is a **mock**: it never fetches any URL, and a test asserts that. It only looks the domain up in the same bundled catalog. A public endpoint that fetches whatever URL a stranger pastes in is a classic SSRF and abuse vector, so it's deliberately not built. See [ROADMAP.md](ROADMAP.md).

## Worker

```
cd worker
npm install
npm test          # vitest with the Workers pool (Miniflare), no network
npm run dev       # wrangler dev with a local D1
npm run deploy    # wrangler deploy
```

Endpoints:
- `POST /subscribe` takes `{email, tracks[]}`. It returns `next: "confirm"` when Buttondown has sent its confirmation email, or `error: "blocked"` when Buttondown's spam firewall rejected the address.
- `GET /confirm` and `GET /unsubscribe`.
- `POST /request` takes `{service?, url?, email?}`.
- `POST /vote` takes `{feature, email}`.
- `GET /check?url=`: the mock above.

No route lists or exports subscriber emails, requests, or votes.

A vote or a request with an email counts only once that address is confirmed; an unknown address gets double opt-in first. The response never reveals which case applied.

The D1 schema is in `worker/migrations/`. Apply it with `npm run db:migrate:local` or `npm run db:migrate:remote`.

D1 keeps its own copy of the subscriber list. Buttondown sends the double opt-in email and handles one-click unsubscribe. Set the key with:

```
npx wrangler secret put BUTTONDOWN_API_KEY
```

Without it, the Worker logs and skips the Buttondown call.

## Licence

The code is Apache-2.0. Policy text and history from Open Terms Archive stay under ODC-By 1.0, with attribution. The fonts are SIL OFL 1.1.
