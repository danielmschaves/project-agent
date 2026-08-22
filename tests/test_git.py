"""Tests for project_agent.git and run_commit (DRY_RUN=1 skips real git ops)."""
import os
import subprocess
from pathlib import Path

import pytest

from project_agent import git
from project_agent.schemas import RunLog, StageResult
from pipeline.stages import run_commit, run_commit_portfolio
from pipeline.cli import _discover_projects
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """Minimal git repo with one commit for branch/commit testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"],
                   cwd=str(repo), check=True, capture_output=True)
    return repo


def _run_log() -> RunLog:
    return RunLog(
        run_id="run-test",
        started_at=datetime.now(tz=timezone.utc),
        project_ids=["test--project"],
        stages=[
            StageResult(name="ingest", status="ok", duration_ms=10,
                        counts={"sources_new": 1, "errors": 0}),
            StageResult(name="analyze", status="ok", duration_ms=10,
                        counts={"signals_new": 2, "errors": 0}),
        ],
    )


# ---------------------------------------------------------------------------
# git.branch + git.commit_all
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_branch_creates_new_branch(bare_repo: Path) -> None:
    git.branch(bare_repo, "test/my-branch")
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=str(bare_repo), capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "test/my-branch"


@pytest.mark.integration
def test_commit_all_creates_commit(bare_repo: Path) -> None:
    (bare_repo / "new_file.txt").write_text("hello")
    git.commit_all(bare_repo, "test commit")

    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=str(bare_repo), capture_output=True, text=True, check=True,
    )
    assert "test commit" in result.stdout


# ---------------------------------------------------------------------------
# git.open_pr (DRY_RUN=1 → no real gh call)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_open_pr_dry_run_returns_empty(bare_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "1")
    url = git.open_pr(bare_repo, "Test PR", "body")
    assert url == ""


# ---------------------------------------------------------------------------
# run_commit with DRY_RUN=1
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_commit_dry_run_returns_skipped(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRY_RUN", "1")
    result = run_commit("test--project", project_dir, _run_log(), project_dir)
    assert result.status == "skipped"
    assert result.counts["errors"] == 0


@pytest.mark.integration
def test_run_commit_full_flow(bare_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "1")
    log = _run_log()
    result = run_commit("test--project", bare_repo, log, bare_repo)
    assert result.status == "skipped"  # DRY_RUN skips git ops


# ---------------------------------------------------------------------------
# run_commit_portfolio with DRY_RUN=1
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_commit_portfolio_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRY_RUN", "1")
    log_a = RunLog(
        run_id="run-a",
        started_at=datetime.now(tz=timezone.utc),
        project_ids=["proj-a"],
        stages=[
            StageResult(name="ingest", status="ok", duration_ms=5,
                        counts={"sources_new": 1, "errors": 0}),
            StageResult(name="analyze", status="ok", duration_ms=5,
                        counts={"signals_new": 2, "errors": 0}),
        ],
    )
    log_b = RunLog(
        run_id="run-b",
        started_at=datetime.now(tz=timezone.utc),
        project_ids=["proj-b"],
        stages=[
            StageResult(name="ingest", status="ok", duration_ms=5,
                        counts={"sources_new": 0, "errors": 0}),
            StageResult(name="analyze", status="ok", duration_ms=5,
                        counts={"signals_new": 0, "errors": 0}),
        ],
    )
    result = run_commit_portfolio([("proj-a", log_a), ("proj-b", log_b)], tmp_path)
    assert result.status == "skipped"
    assert result.counts["errors"] == 0


# ---------------------------------------------------------------------------
# _discover_projects
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_discover_projects(tmp_path: Path) -> None:
    # Discovery anchors on project.notes.md (PM-owned, always committed),
    # not project.md (bot-generated, gitignored — absent on fresh clone).
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-a" / "project.notes.md").write_text("# A notes")
    (tmp_path / "proj-b").mkdir()
    (tmp_path / "proj-b" / "project.notes.md").write_text("# B notes")
    (tmp_path / "no-notes").mkdir()  # has no project.notes.md — must be excluded
    (tmp_path / "a-file.txt").write_text("not a dir")

    found = _discover_projects(tmp_path)
    assert found == ["proj-a", "proj-b"]


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_cli_help(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the CLI entry point is importable and --help works."""
    import subprocess as sp
    import sys
    # sys.executable, not bare "python": the latter is whatever is on PATH and
    # generally has no project_agent installed.
    result = sp.run(
        [sys.executable, "-m", "pipeline", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "pipeline" in result.stdout.lower()
