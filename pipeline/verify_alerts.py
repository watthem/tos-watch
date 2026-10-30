#!/usr/bin/env python3
"""Independent check of every alert against the primary document's stored history.

An alert ships only if a machine can re-derive it without trusting the run
that wrote it. For each alerts/*.md this checks, against the Open Terms
Archive clone (cache/<repo>), not against anything the pipeline produced:

  source     source_url is https and ota_commit_url names a commit that exists
             in the archive repo and touched a document
  quoted     at least one quoted passage ("Added" or "Removed")
  verbatim   every quoted passage appears word for word in the document version
             it claims to come from (Added: this version; Removed: previous).
             A quote that is not in the archived text is a fabrication.
  evidence   at least one passage is a real change (Added quote absent from the
             previous version, Removed quote absent from this one) and prose,
             not table or markup debris, a table of contents or a menu
             (lib/noise.py). Weaker passages beside it are allowed; an alert
             whose passages are all noise is withheld.

Result: pipeline/verification.json {stem: {status, failed, commit, ...}}.
Only status "pass" alerts are shown on the site and drafted for email. Already
verified alerts are kept (a commit hash cannot change), so a daily run checks
only new ones. Exit code is 0 either way; the caller reads the JSON.

Usage:
    verify_alerts.py              # check alerts with no stored result
    verify_alerts.py --all        # re-check everything
    verify_alerts.py --report     # print failures and counts, check nothing new
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from lib import paths
from lib.noise import has_header, is_noise, same_text

COMMIT_RE = re.compile(r"^https://github\.com/OpenTermsArchive/([\w.-]+)/commit/([0-9a-f]{40})$")
MIN_QUOTE_WORDS = 6
# Bump when a rule changes: results from older rules are checked again.
RULES = 3


def ws(s: str) -> str:
    return " ".join(s.split())


def parse(path: pathlib.Path) -> tuple[dict, list[tuple[str, str]]]:
    text = path.read_text()
    fm: dict[str, str] = {}
    body = text
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
        body = text[m.end():]
    quotes = re.findall(r"\*\*(Removed|Added):\*\*\n> (.+)", body)
    return fm, quotes


def segments(quote: str) -> list[str]:
    """Excerpts are windowed with a leading/trailing ellipsis; each stretch
    between ellipses must appear verbatim."""
    return [s for s in (ws(p) for p in quote.split("…")) if s]


def git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout


def archive_versions(repo: pathlib.Path, sha: str) -> tuple[str, str]:
    """Text of the commit's document(s) at the commit and at the version before."""
    files = [f for f in git(repo, "show", "--name-only", "--format=", sha).splitlines() if f]
    if not files:
        raise ValueError("commit touches no document")
    new, old = [], []
    for f in files:
        shas = git(repo, "log", "-2", "--format=%H", sha, "--", f).split()
        new.append(ws(git(repo, "show", f"{sha}:{f}")))
        old.append(ws(git(repo, "show", f"{shas[1]}:{f}")) if len(shas) > 1 else "")
    return " ".join(new), " ".join(old)


def snapshot_versions(dirs: list[pathlib.Path], date: str, quotes) -> tuple[str, str, str] | None:
    """Direct-fetch pages: snapshots are <slug>-<date>.json (or <date>.json),
    a list of {sha256, text}. Find the slug with a snapshot on the alert date
    whose text holds a quoted passage, paired with the previous snapshot."""
    for d in dirs:
        by_slug: dict[str, dict[str, pathlib.Path]] = {}
        for f in d.glob("*.json"):
            slug, _, day = f.stem.rpartition("-20")
            day = "20" + day if _ else f.stem
            by_slug.setdefault(slug if _ else "", {})[day] = f
        for slug, days in by_slug.items():
            if date not in days:
                continue
            earlier = sorted(x for x in days if x < date)
            if not earlier:
                continue
            new = ws(" ".join(p["text"] for p in json.loads(days[date].read_text())))
            old = ws(" ".join(p["text"] for p in json.loads(days[earlier[-1]].read_text())))
            probe = [seg for k, q in quotes for seg in segments(q)]
            if probe and all(seg in new or seg in old for seg in probe):
                return new, old, f"{days[earlier[-1]].name} -> {days[date].name}"
    return None


def readable(quote: str) -> bool:
    words = quote.split()
    if len(words) < MIN_QUOTE_WORDS:
        return False
    junk = sum(1 for w in words if set(w) <= set("|-:*_#`\\"))
    return junk / len(words) < 0.2


def check(path: pathlib.Path, cache: pathlib.Path, snap_dirs: list[pathlib.Path]) -> dict:
    fm, quotes = parse(path)
    failed: list[str] = []
    out = {"status": "fail", "failed": failed, "checked_on": time.strftime("%Y-%m-%d"), "rules": RULES}

    commit_url = fm.get("ota_commit_url", "")
    m = COMMIT_RE.match(commit_url)
    if not fm.get("source_url", "").startswith("https://") or (commit_url and not m):
        failed.append("source: malformed source_url / ota_commit_url")
    if not quotes:
        failed.append("quoted: no quoted passage")
    if failed:
        return out
    if m:
        repo_name, sha = m.groups()
        out["commit"] = sha
        try:
            new_all, old_all = archive_versions(cache / repo_name, sha)
        except (subprocess.CalledProcessError, ValueError) as e:
            failed.append(f"source: commit not readable in the archive ({e})")
            return out
    else:
        # No archive commit: a page we fetch ourselves. Evidence is our own
        # dated snapshots (previous fetch and the alert date's fetch).
        pair = snapshot_versions(snap_dirs, fm.get("date", ""), quotes)
        if pair is None:
            failed.append("source: no archive commit and no stored snapshot pair holds the quote")
            return out
        new_all, old_all, out["snapshots"] = pair
    # A quote that is not in the archived text is a fabrication: always fatal.
    # A quote that is unreadable, or also present in the other version (a
    # paragraph that only moved or was re-split), is a weak passage: the alert
    # needs at least one strong passage to stand on.
    strong = 0
    # An edited passage (Removed then Added) whose two sides read the same
    # once markup is gone is not a change.
    same = set()
    for i in range(len(quotes) - 1):
        if quotes[i][0] == "Removed" and quotes[i + 1][0] == "Added" and same_text(quotes[i][1], quotes[i + 1][1]):
            same.update((i, i + 1))
    for i, (kind, q) in enumerate(quotes):
        segs = segments(q)
        home, other = (new_all, old_all) if kind == "Added" else (old_all, new_all)
        if not all(s in home for s in segs):
            failed.append(f"verbatim: {kind} quote not found word for word in the archived version")
            continue
        weak = []
        if i in same:
            weak.append("only markup differs")
        if all(s in other for s in segs):
            weak.append("also in the other version")
        if has_header(q):
            # The excerpt opens on the document's own date line ("Last Updated:
            # ..."), which changes with every revision and shows the reader a
            # header, not a clause. Not evidence on its own.
            weak.append("starts with the document's date line")
        if not readable(q):
            weak.append("table or markup debris")
        elif is_noise(q):
            weak.append("navigation or markup noise")
        if not weak:
            strong += 1
    if not failed and not strong:
        failed.append("evidence: no passage that is both prose and a real change")
    if not failed:
        out["status"] = "pass"
        out["passages"] = f"{strong} of {len(quotes)} quoted passages checked as new prose"
    return out


def run_verification(alerts: pathlib.Path, cache: pathlib.Path, out: pathlib.Path,
                     recheck: bool = False) -> dict:
    """Check alerts with no stored result (all of them if recheck); save and return."""
    results = json.loads(out.read_text()) if out.exists() else {}
    snap_dirs = [paths.DIRECT_SNAPSHOTS_DIR, paths.MUSE_SNAPSHOTS_DIR]
    for p in sorted(alerts.glob("*.md")):
        prior = results.get(p.stem, {})
        if prior.get("status") == "pass" and prior.get("rules") == RULES and not recheck:
            continue
        results[p.stem] = check(p, cache, snap_dirs)
    for stem in list(results):
        if not (alerts / f"{stem}.md").exists():
            del results[stem]
    out.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--alerts", type=pathlib.Path, default=paths.ALERTS_DIR)
    ap.add_argument("--cache", type=pathlib.Path, default=paths.CACHE_DIR)
    ap.add_argument("--out", type=pathlib.Path, default=paths.DATA / "pipeline" / "verification.json")
    a = ap.parse_args()

    if a.report:
        results = json.loads(a.out.read_text()) if a.out.exists() else {}
    else:
        results = run_verification(a.alerts, a.cache, a.out, a.all)
    bad = {s: r for s, r in results.items() if r["status"] != "pass"}
    print(f"verified {len(results) - len(bad)} of {len(results)} alerts")
    for s, r in sorted(bad.items()):
        print(f"WITHHELD {s}: " + "; ".join(r["failed"])[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
