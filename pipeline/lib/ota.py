"""Clone/pull an Open Terms Archive versions repo into the local cache.

These repos (pga-versions, genai-eu-versions) are a few MB each, so a plain
full clone is simpler and faster than a partial/shallow clone here; the
"gitignored cache dir" requirement is met by keeping them under
pipeline/cache/ota/, which .gitignore excludes.
"""
from __future__ import annotations

import pathlib
import subprocess


def ensure_repo(cache_dir: pathlib.Path, name: str, clone_url: str) -> str:
    repo_dir = cache_dir / name
    if (repo_dir / ".git").exists():
        subprocess.run(
            ["git", "-C", str(repo_dir), "pull", "--ff-only", "--quiet"],
            check=True,
        )
    else:
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", clone_url, str(repo_dir)], check=True
        )
    return str(repo_dir)
