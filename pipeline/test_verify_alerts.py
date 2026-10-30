#!/usr/bin/env python3
"""A quote that is not in the archived document must never pass (it would put
words in a company's mouth in an email to subscribers)."""
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import verify_alerts as v


def run_git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                          capture_output=True, text=True, check=True).stdout.strip()


def alert(path, sha, quote):
    path.write_text(f"---\nvendor: V\ndoc: D\ndate: 2026-01-01\nsource_url: https://example.com/p\n"
                    f"ota_commit_url: https://github.com/OpenTermsArchive/r-versions/commit/{sha}\n---\n\n"
                    f"# V D changed\n\n**Added:**\n> {quote}\n")


with tempfile.TemporaryDirectory() as t:
    t = pathlib.Path(t)
    repo = t / "r-versions"
    repo.mkdir()
    run_git(repo, "init", "-q")
    (repo / "doc.md").write_text("We keep your data for thirty days.\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-qm", "v1")
    (repo / "doc.md").write_text("We keep your data for thirty days.\n\nWe may share your messages with advertising partners.\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-qm", "v2")
    sha = run_git(repo, "rev-parse", "HEAD")
    a = t / "a.md"
    alert(a, sha, "We may share your messages with advertising partners.")
    assert v.check(a, t, [])["status"] == "pass"
    alert(a, sha, "We never share your messages with advertising partners.")
    r = v.check(a, t, [])
    assert r["status"] == "fail" and r["failed"][0].startswith("verbatim"), r
print("ok")
