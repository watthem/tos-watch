#!/usr/bin/env python3
"""Watch a team's own vendor list with the tos.watch pipeline.

A vendor list is a JSON file:

    {"name": "Example Co",
     "vendors": [{"name": "Zoom"},
                 {"name": "OpenAI", "aliases": ["ChatGPT"]},
                 {"name": "Acme Payroll", "ota": "contrib-versions/Acme"}],
     "docs": ["privacy", "terms", "data process"]}

`ota` pins a vendor to one Open Terms Archive service directory
(<collection>/<service>). `docs`, optional, keeps only documents whose name
contains one of the given words (case-insensitive); without it every
document of a matched service is watched.

Usage:
    pipeline/vendors.py match  DIR [--pull]   # DIR/vendors.json -> DIR/watchlist.json + DIR/coverage.json
    pipeline/run.py --watchlist DIR/watchlist.json --out DIR [--since YYYY-MM-DD]
    pipeline/vendors.py digest DIR [--since YYYY-MM-DD]
                                     # DIR/alerts -> ranked Markdown on stdout

Matching only looks in Open Terms Archive collections already cloned into
the data directory's cache/ (the ones pipeline/watchlist.json names). A
vendor that isn't found is listed as not covered rather than guessed at.
Alerts stay in DIR: nothing here touches the public site or the newsletter.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from lib import jev, ota, paths

sys.path.insert(0, str(paths.SITE_SRC))
from build import parse_alert  # noqa: E402


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def ls_tree(repo: pathlib.Path, path: str = "") -> list[str]:
    """Names under `path` at HEAD. Uses git, not the filesystem, because a
    collection may be a sparse checkout."""
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "--name-only", "HEAD", *([f"{path}/"] if path else [])],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.rsplit("/", 1)[-1] for line in out.splitlines() if line]


def load_vendors(d: pathlib.Path) -> dict:
    p = d / "vendors.json"
    try:
        cfg = json.loads(p.read_text())
    except FileNotFoundError:
        sys.exit(f"{p}: not found")
    except json.JSONDecodeError as err:
        sys.exit(f"{p}: not valid JSON ({err})")
    vendors = cfg.get("vendors")
    if not isinstance(vendors, list) or not all(isinstance(v, dict) and v.get("name") for v in vendors):
        sys.exit(f'{p}: "vendors" must be a list of objects, each with a "name"')
    return cfg


def match(d: pathlib.Path, pull: bool = False) -> None:
    cfg = load_vendors(d)
    base = json.loads(paths.WATCHLIST_PATH.read_text())
    if pull:
        for name, repo in base["ota_repos"].items():
            ota.ensure_repo(paths.CACHE_DIR, name, repo["clone_url"])
    doc_words = [w.lower() for w in cfg.get("docs", [])]

    # service directory name (normalized) -> [(collection, service)]
    index: dict[str, list[tuple[str, str]]] = {}
    cloned = [n for n in base["ota_repos"] if (paths.CACHE_DIR / n / ".git").exists()]
    for coll in cloned:
        for svc in ls_tree(paths.CACHE_DIR / coll):
            index.setdefault(norm(svc), []).append((coll, svc))

    documents, covered, missing, used = [], [], [], set()
    for v in cfg["vendors"]:
        if v.get("ota"):
            coll, _, svc = v["ota"].partition("/")
            hits = [(coll, svc)] if coll in cloned else []
        else:
            hits = []
            for n in [v["name"], *v.get("aliases", [])]:
                hits += [h for h in index.get(norm(n), []) if h not in hits]
        docs = []
        for coll, svc in hits:
            for f in ls_tree(paths.CACHE_DIR / coll, svc):
                if not f.endswith(".md"):
                    continue
                doc = f[:-3]
                if any(x["doc"] == doc for x in docs):
                    continue  # same document in a second collection: keep the first
                if doc_words and not any(w in doc.lower() for w in doc_words):
                    continue
                slug = base["ota_repos"][coll]["slug"]
                docs.append({
                    "vendor": v["name"],
                    "doc": doc,
                    "repo": coll,
                    "path": f"{svc}/{f}",
                    "source_url": f"https://github.com/{slug}/commits/main/{svc}/{f}",
                    "source": f"ota:{slug}",
                })
                used.add(coll)
        if docs:
            documents += docs
            covered.append({"name": v["name"], "services": [f"{c}/{s}" for c, s in hits], "docs": [x["doc"] for x in docs]})
        else:
            why = "matched, but no document fits `docs`" if hits else "not in any cloned Open Terms Archive collection"
            missing.append({"name": v["name"], "reason": why})

    watchlist = {"ota_repos": {n: base["ota_repos"][n] for n in sorted(used)}, "documents": documents}
    (d / "watchlist.json").write_text(json.dumps(watchlist, indent=1) + "\n")
    coverage = {"vendors": len(cfg["vendors"]), "covered": covered, "not_covered": missing, "collections_searched": cloned}
    (d / "coverage.json").write_text(json.dumps(coverage, indent=1) + "\n")
    print(f"{len(covered)} of {len(cfg['vendors'])} vendors covered ({len(documents)} documents); {len(missing)} not covered")
    for m in missing:
        print(f"  not covered: {m['name']} ({m['reason']})")


def digest(d: pathlib.Path, since: str | None) -> None:
    cfg = load_vendors(d)
    alerts = [parse_alert(p) for p in sorted((d / "alerts").glob("*.md"))] if (d / "alerts").exists() else []
    if since:
        alerts = [a for a in alerts if a.get("date", "") >= since]

    def top(a: dict) -> tuple[str, float]:
        s = a["scores"]
        return max(((q, s.get(q, 0.0)) for q in jev.TOPIC_QUESTIONS), key=lambda kv: kv[1])

    alerts.sort(key=lambda a: (top(a)[1], a.get("date", "")), reverse=True)
    vendors_changed = {a["vendor"] for a in alerts}
    cov_path = d / "coverage.json"
    not_covered = json.loads(cov_path.read_text())["not_covered"] if cov_path.exists() else []

    span = f"since {since}" if since else "in the recorded history"
    print(f"# {cfg.get('name', d.name)}: vendor terms changes {span}\n")
    print(f"{len(vendors_changed)} of your {len(cfg['vendors'])} vendors changed their terms {span} ({len(alerts)} document changes).\n")
    for a in alerts:
        q, v = top(a)
        print(f"- **{a['vendor']} — {a['doc']}** ({a['date']}): mostly {q.replace('_', ' ')}, score {v:.2f}. Source: {a.get('source_url', '')}")
    if not_covered:
        print(f"\n## Not covered yet ({len(not_covered)})\n")
        for m in not_covered:
            print(f"- {m['name']}: {m['reason']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("match", help="vendors.json -> watchlist.json + coverage.json")
    m.add_argument("dir", type=pathlib.Path)
    m.add_argument("--pull", action="store_true", help="clone or update the collections first")
    g = sub.add_parser("digest", help="ranked Markdown of DIR/alerts")
    g.add_argument("dir", type=pathlib.Path)
    g.add_argument("--since", default=None)
    args = ap.parse_args()
    if args.cmd == "match":
        match(args.dir, args.pull)
    else:
        digest(args.dir, args.since)


if __name__ == "__main__":
    main()
