"""Where tos.watch's code ends and its data begins.

The code (this repo) is public. The data a run reads and writes -- the tuned
question set, dismissed alerts, alerts, the Jev cache, snapshots, run state
and the generated site -- lives in a data directory:

    TOS_WATCH_DATA=/path/to/data python3 pipeline/run.py

Without TOS_WATCH_DATA the data directory is this repo itself, so a fresh
self-hosted clone works in place (everything it writes is gitignored here).
tos.watch itself points it at a checkout of its private data repo. Paths
inside the data directory mirror this repo's layout.
"""
from __future__ import annotations

import os
import pathlib

CODE = pathlib.Path(__file__).resolve().parent.parent.parent
DATA = pathlib.Path(os.environ.get("TOS_WATCH_DATA") or CODE).expanduser().resolve()

ALERTS_DIR = DATA / "alerts"
CACHE_DIR = DATA / "cache"
STATE_PATH = DATA / "pipeline" / "state.json"
DRAFTS_STATE_PATH = DATA / "pipeline" / "drafts_state.json"
DISMISSED_PATH = DATA / "pipeline" / "dismissed.json"
QUESTIONS_PATH = DATA / "pipeline" / "questions.json"
MUSE_SNAPSHOTS_DIR = DATA / "pipeline" / "muse_snapshots"
DIRECT_SNAPSHOTS_DIR = DATA / "pipeline" / "direct_snapshots"
SITE_OUT = DATA / "site"
DATASETS_CACHE = DATA / "datasets" / "cache"

# Code-side files.
WATCHLIST_PATH = CODE / "pipeline" / "watchlist.json"
QUESTIONS_EXAMPLE_PATH = CODE / "pipeline" / "questions.example.json"
SITE_SRC = CODE / "site"
