# Agent rules for tos-watch

This repository is public. Keep private planning, subscriber data, and credentials out of it.

## Branches and releases

Many sessions (Claude, Codex, the owner) work in this repo at once. `main` is protected: change it only by pull request, squash-merged, and never force-push.

- One branch per task, named `<actor>/<slug>` (`claude/…`, `codex/…`, `owner/…`). Only that actor pushes to it.
- Use a separate git worktree per concurrent session; never share a checkout.
- Branches delete on merge. Delete your own leftover branch when you are done.
- Agents never publish (`npm publish`, releases, deploys to production). A release is a tag the owner pushes.

## Review guidelines

Codex reviews each PR when it opens (exhaustive). Flag these first:

- Subscriber data: email addresses and anything stored in the D1 database `tos-watch-subscribers` (`worker/src/index.js`, `worker/migrations/`). Flag logging of addresses, returning them in responses, or new fields stored without a reason.
- Signup and unsubscribe paths in `worker/`: a response that reports success before the write succeeded, error branches that swallow a failure, missing input validation, and missing rate or abuse limits.
- Secrets: API keys or tokens in `site/`, in client-side JavaScript, in `wrangler.toml`, or in test fixtures. Secrets belong in Worker secrets.
- `site/privacy.html` drift: a change that collects, stores or sends data the privacy page does not describe.
- Public-repo leaks: private planning notes, subscriber exports, or vault paths committed by mistake.
- Pipeline output: `pipeline/` claims about a vendor's terms must link the captured source; flag a summary with no source.
