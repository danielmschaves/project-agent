from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _run(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def branch(repo_root: Path, branch_name: str) -> None:
    """Create and switch to a new branch."""
    _run(["git", "checkout", "-b", branch_name], repo_root)
    logger.info("Created branch: %s", branch_name)


def commit_all(
    repo_root: Path,
    message: str,
    force_paths: list[Path] | None = None,
) -> None:
    """Stage all changes and create a commit.

    force_paths are added with -f so gitignored pipeline outputs
    (events.ndjson, the ingest and parse manifests, the portfolio report)
    reach the auto/* PR. kb/ is not among them — the vault is committed
    normally, because it has to exist on a fresh clone.
    """
    _run(["git", "add", "-A"], repo_root)
    for p in force_paths or []:
        _run(["git", "add", "-f", str(p)], repo_root)
    _run(["git", "commit", "-m", message], repo_root)
    logger.info("Committed: %s", message[:60])


def open_pr(repo_root: Path, title: str, body: str) -> str:
    """Open a GitHub PR and return its URL.

    Requires GH_TOKEN env var and the gh CLI to be installed.
    Set DRY_RUN=1 to skip actual PR creation (returns empty string).
    """
    if os.environ.get("DRY_RUN") == "1":
        logger.info("DRY_RUN=1 — skipping gh pr create")
        return ""
    url = _run(
        ["gh", "pr", "create", "--title", title, "--body", body],
        repo_root,
    )
    logger.info("PR opened: %s", url)
    return url
