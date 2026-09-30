# Agent rules for tos-watch

This repository is public. Keep private planning, subscriber data, and credentials out of it.

## Branches and releases

Many sessions (Claude, Codex, the owner) work in this repo at once. `main` is protected: change it only by pull request, squash-merged, and never force-push.

- One branch per task, named `<actor>/<slug>` (`claude/…`, `codex/…`, `owner/…`). Only that actor pushes to it.
- Use a separate git worktree per concurrent session; never share a checkout.
- Branches delete on merge. Delete your own leftover branch when you are done.
- Agents never publish (`npm publish`, releases, deploys to production). A release is a tag the owner pushes.
