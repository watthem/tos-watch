"""Jev scoring of a changed passage, with a local disk cache.

The questions Jev answers are loaded from a JSON question set (see below),
not written here.

Caller pattern: a single decision call to the OpenRouter alpha decisions
endpoint, with a Bearer key from OPENROUTER_API_KEY.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.request

JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"

# The question set: pipeline/questions.json if present (tos.watch's tuned
# set, kept private), else pipeline/questions.example.json (a generic set
# that runs but isn't tuned). "version" goes into the cache key, so bump it
# whenever the wording changes. The topic questions can trigger an alert;
# anything else (e.g. "noise") is diagnostic only.
_PIPELINE_DIR = pathlib.Path(__file__).resolve().parent.parent


def _load_question_set() -> dict:
    for name in ("questions.json", "questions.example.json"):
        path = _PIPELINE_DIR / name
        if path.exists():
            return json.loads(path.read_text())
    sys.exit("no pipeline/questions.json or pipeline/questions.example.json")


_QSET = _load_question_set()
QUESTION_SET_VERSION = _QSET["version"]
QUESTIONS = _QSET["questions"]
TOPIC_QUESTIONS = _QSET["topic_questions"]
ALERT_THRESHOLD = _QSET["alert_threshold"]


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("set OPENROUTER_API_KEY in the environment (https://openrouter.ai/keys)")
    return key


def delta_key(doc: str, removed: str, added: str) -> str:
    h = hashlib.sha256()
    h.update(QUESTION_SET_VERSION.encode())
    h.update(b"\0")
    h.update(doc.encode())
    h.update(b"\0")
    h.update(removed.encode())
    h.update(b"\0")
    h.update(added.encode())
    return h.hexdigest()


class JevCache:
    """Cache Jev answers by (delta sha256, question-set version) on disk.

    Gitignored (pipeline/cache/jev/): reruns of the same delta cost nothing.
    """

    def __init__(self, cache_dir: pathlib.Path):
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> pathlib.Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        p = self._path(key)
        try:
            return json.loads(p.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None  # missing, or a write cut short: score it again

    def put(self, key: str, value: dict) -> None:
        p = self._path(key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        tmp.replace(p)  # atomic, so an interrupted run never leaves a half-written entry


def score(doc: str, version_date: str, removed: str, added: str, cache: JevCache) -> dict:
    """Return {question: score, ...} for one changed passage, using the cache."""
    key = delta_key(doc, removed, added)
    cached = cache.get(key)
    if cached is not None:
        return cached
    body = {
        "model": JEV_MODEL,
        "state": {
            "document": doc,
            "version_date": version_date,
            "removed_text": removed[:700],
            "added_text": added[:700],
        },
        "questions": QUESTIONS,
    }
    req = urllib.request.Request(
        JEV_URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key(),
        },
    )
    last_err = None
    for attempt in range(4):
        try:
            data = json.load(urllib.request.urlopen(req, timeout=90))
            result = {q: data["answers"][q]["noul"] for q in QUESTIONS}
            cache.put(key, result)
            return result
        except Exception as err:  # noqa: BLE001 - retry any transport/HTTP error
            last_err = err
            time.sleep(3 * 2**attempt)
    raise RuntimeError(f"Jev unavailable after retries: {last_err}")


def alerts_on(scores: dict) -> bool:
    return any(scores.get(q, 0) >= ALERT_THRESHOLD for q in TOPIC_QUESTIONS)
