"""Tests for project_agent.render and run_render."""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from project_agent import render
from project_agent.schemas import Signal
from pipeline.stages import run_render


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signal(
    signal_type: str = "action_aging",
    category: str = "action",
    severity: str = "medium",
    rationale: str = "Some actions are aging.",
    recommended_action: str = "Review them.",
    evidence: list[str] | None = None,
) -> Signal:
    return Signal(
        signal_id=str(uuid.uuid4()),
        project_id="test--project",
        run_id="run-001",
        category=category,
        type=signal_type,
        severity=severity,
        confidence=1.0,
        evidence=evidence or ["evt-001"],
        method="deterministic",
        rationale=rationale,
        detected_at=datetime.now(tz=timezone.utc),
        recommended_action=recommended_action,
    )


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_report_contains_header(project_dir: Path) -> None:
    path = render.render_status(project_dir, [], "run-001")
    content = path.read_text(encoding="utf-8")
    assert "# Project Status Report" in content
    assert "run-001" in content
    assert "green" in content  # from project.md status field


@pytest.mark.unit
def test_report_contains_signal_types(project_dir: Path) -> None:
    signals = [
        _signal("action_aging", category="action", severity="high"),
        _signal("blocker_unowned", category="blocker", severity="high"),
    ]
    path = render.render_status(project_dir, signals, "run-001")
    content = path.read_text(encoding="utf-8")
    assert "action_aging" in content
    assert "blocker_unowned" in content


@pytest.mark.unit
def test_report_groups_signals_by_category(project_dir: Path) -> None:
    signals = [
        _signal("action_aging", category="action"),
        _signal("action_unowned", category="action"),
        _signal("blocker_unowned", category="blocker"),
    ]
    path = render.render_status(project_dir, signals, "run-001")
    content = path.read_text(encoding="utf-8")
    assert "### Action" in content
    assert "### Blocker" in content


@pytest.mark.unit
def test_report_no_signals_message(project_dir: Path) -> None:
    path = render.render_status(project_dir, [], "run-001")
    content = path.read_text(encoding="utf-8")
    assert "No signals detected" in content


@pytest.mark.unit
def test_report_contains_pm_notes(project_dir: Path) -> None:
    (project_dir / "project.notes.md").write_text("# Notes\n\nImportant PM context here.", encoding="utf-8")
    path = render.render_status(project_dir, [], "run-001")
    content = path.read_text(encoding="utf-8")
    assert "Important PM context here" in content
    assert "## PM Notes" in content


@pytest.mark.unit
def test_report_no_pm_notes_section_when_empty(project_dir: Path) -> None:
    (project_dir / "project.notes.md").write_text("", encoding="utf-8")
    path = render.render_status(project_dir, [], "run-001")
    content = path.read_text(encoding="utf-8")
    assert "## PM Notes" not in content


@pytest.mark.unit
def test_report_contains_actions_section(project_dir: Path) -> None:
    # Seed project.md with an Actions section
    (project_dir / "project.md").write_text(
        "---\nid: test--project\nstatus: green\n---\n\n## Mission\nTest.\n\n## Actions\n\n- [ ] Deploy | owner: alice | due: 2026-06-01 | status: open\n",
        encoding="utf-8",
    )
    path = render.render_status(project_dir, [], "run-001")
    content = path.read_text(encoding="utf-8")
    assert "## Actions" in content
    assert "Deploy" in content


@pytest.mark.unit
def test_report_severity_icons_present(project_dir: Path) -> None:
    signals = [
        _signal(severity="high"),
        _signal(severity="medium"),
        _signal(severity="low"),
    ]
    path = render.render_status(project_dir, signals, "run-001")
    content = path.read_text(encoding="utf-8")
    assert "🔴" in content
    assert "⚠️" in content
    assert "ℹ️" in content


@pytest.mark.unit
def test_report_written_to_reports_dir(project_dir: Path) -> None:
    path = render.render_status(project_dir, [], "run-001")
    assert path.parent == project_dir / "reports"
    assert path.name.startswith("status-")
    assert path.suffix == ".md"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_render_returns_ok(project_dir: Path) -> None:
    result = run_render("test--project", project_dir, [], "run-001")
    assert result.status == "ok"
    assert result.counts["reports_written"] == 1
    assert result.counts["errors"] == 0


@pytest.mark.integration
def test_run_render_creates_report_file(project_dir: Path) -> None:
    run_render("test--project", project_dir, [], "run-001")
    reports = list((project_dir / "reports").glob("status-*.md"))
    assert len(reports) == 1


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_render_idempotent_same_content(project_dir: Path) -> None:
    signals = [_signal("action_aging", evidence=["evt-abc"])]
    path1 = render.render_status(project_dir, signals, "run-001")
    content1 = path1.read_text(encoding="utf-8")

    path2 = render.render_status(project_dir, signals, "run-001")
    content2 = path2.read_text(encoding="utf-8")

    assert content1 == content2


@pytest.mark.idempotence
def test_render_never_writes_to_notes_file(project_dir: Path) -> None:
    original = (project_dir / "project.notes.md").read_text(encoding="utf-8")
    render.render_status(project_dir, [], "run-001")
    assert (project_dir / "project.notes.md").read_text(encoding="utf-8") == original
