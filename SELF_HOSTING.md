# Self-hosting tos.watch

tos.watch's code is Apache-2.0 (see [LICENSE](LICENSE)). You can run your
own copy for the documents you care about.

Nothing here needs a paid service. The pipeline is stdlib-first Python; the
only external dependency is an OpenRouter API key for the Jev classifier
(pay-as-you-go, no subscription).

## What you get

Your own copy of the pipeline, watching whatever documents *you* list, with
your own alert archive and RSS feed. You are not on tos.watch's mailing
list, database, or Cloudflare account — this is a fully independent copy.

## Steps

1. **Fork or clone the repo**, or copy this directory tree:
   `pipeline/`, `alerts/` (start empty), `site/`, `LICENSE`.

2. **Edit `pipeline/watchlist.json`.** This is the whole configuration
   surface:
   - `tracks`: your own labels for grouping documents (optional — the
     pipeline works with zero tracks defined).
   - `ota_repos`: any [Open Terms Archive](https://opentermsarchive.org/en/)
     collection whose git history has documents you want (`clone_url`,
     `slug` for display, `collection`/`collection_url` for attribution).
     Browse <https://github.com/OpenTermsArchive> for the full list of
     collections — pga, genai-eu, genai-contrib, dating, contrib (OTA's own
     README calls this last one a catch-all, "use only as a last resort"
     since coverage/freshness varies more), vlopses-us, and others.
   - `documents`: one entry per tracked document, pointing at a path inside
     one of the `ota_repos`.
   - `web_pages` / `direct_pages`: for a service with no Open Terms Archive
     declaration at all. tos.watch fetches these directly and keeps its own
     dated paragraph-hash snapshots (`pipeline/muse_snapshots/`,
     `pipeline/direct_snapshots/`) rather than OTA's git history. Store only
     hashes and short excerpts (tos.watch caps these at 300 characters) —
     never commit full policy text you fetched directly; that's not yours to
     redistribute the way OTA's ODC-By-licensed data is.

3. **Get an OpenRouter API key** (<https://openrouter.ai>) and export it:
   ```
   export OPENROUTER_API_KEY=...
   ```
   The questions Jev answers come from `pipeline/questions.example.json`.
   It's a generic set that runs but isn't tuned. Copy it to
   `pipeline/questions.json` and write narrow questions for the changes you
   care about. Before you trust the `alert_threshold`, label 30–40 real
   changes by hand and check that the scores separate them. Bump `version`
   whenever you change wording; it invalidates the cached answers.

4. **Run it:**
   ```
   python3 pipeline/run.py                 # first run backfills all history
   python3 pipeline/run.py --since 2025-01-01   # bound Jev spend on a big backfill
   python3 pipeline/run.py --no-pull       # rerun without re-fetching/re-cloning
   ```
   Jev answers are cached on disk by content hash (`cache/jev/`, gitignored)
   — a rerun over the same history costs nothing new. Alert files
   (`alerts/YYYY-MM-DD-vendor-doc.md`) are written once per document version,
   so reruns are idempotent.

5. **Automate it**, either:
   - **GitHub Actions cron**: `.github/workflows/watch.yml` is already in
     this repo, inactive until pushed to a remote with the
     `OPENROUTER_API_KEY` secret set (Settings → Secrets → Actions). It runs
     the pipeline daily and commits new alert files.
   - **Local cron**: `0 6 * * * cd /path/to/tos-watch && OPENROUTER_API_KEY=... python3 pipeline/run.py >> pipeline.log 2>&1`

6. **Publish your own output.** `python3 site/build.py` regenerates
   `site/alerts/index.html` (a full archive page) and `site/feed.xml` (RSS,
   newest 50) from `alerts/*.md` — no email service required. Point any
   static host at `site/`, or just read `alerts/*.md` as plain Markdown, or
   point your own RSS-to-email tool (Buttondown, etc.) at `feed.xml`.

## What's tos.watch-specific, not required for self-hosting

The Cloudflare Worker in `worker/` (search box backend, subscribe/confirm/
unsubscribe, service-request and feature-vote logging) is how tos.watch
itself runs its hosted newsletter and search UI. A self-hosted copy doesn't
need it at all — `alerts/*.md` and `site/feed.xml` are the whole product.
If you do want the same search-and-request flow, `worker/` is Apache-2.0
too, but you'd need your own Cloudflare account, D1 database, and (if you
want double opt-in email) your own Buttondown account and API key.

## Attribution you must keep

Any document pulled from Open Terms Archive is licensed
[ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/), which requires
attribution for public use. Keep (or adapt, but don't remove) the
attribution block in `site/build.py`'s `ATTRIBUTION_HTML` and each alert's
frontmatter `source_url`. Documents you fetch directly (not via OTA) are not
covered by ODC-By — that's the company's own copyrighted text; only your
short excerpt/hash snapshot is yours to keep, not the full page.
