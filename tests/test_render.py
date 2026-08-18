"""Tests for project_agent.render and run_render."""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from project_agent import render
from project_agent.render import (
    PortfolioProject,
    _attention_score,
    _breadcrumb_html,
    _category_cell,
    _client_of,
    _headline_html,
    _severity_breakdown,
    _silent_list_html,
    _verdict_callout_html,
)
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


@pytest.fixture
def design_system_dir() -> Path:
    return Path(__file__).parent.parent / "design-system"


# ---------------------------------------------------------------------------
# Unit tests — markdown
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
    # Seed the wiki index article with an Actions section
    from project_agent import projects as _projects
    index = _projects.index_path(project_dir)
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        "---\nid: test--project\nstatus: green\ntype: project\n---\n\n## Mission\nTest.\n\n"
        "## Actions\n\n- [ ] Deploy | owner: alice | due: 2026-06-01 | status: open\n",
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
    assert result.counts["reports_written"] == 2
    assert result.counts["errors"] == 0


@pytest.mark.integration
def test_run_render_creates_report_file(project_dir: Path) -> None:
    run_render("test--project", project_dir, [], "run-001")
    md_reports = list((project_dir / "reports").glob("status-*.md"))
    html_reports = list((project_dir / "reports").glob("status-*.html"))
    assert len(md_reports) == 1
    assert len(html_reports) == 1


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


# ---------------------------------------------------------------------------
# HTML — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_html_report_is_valid_html(project_dir: Path, design_system_dir: Path) -> None:
    path = render.render_html(project_dir, [], "run-001", design_system_dir=design_system_dir)
    content = path.read_text(encoding="utf-8")
    assert content.startswith("<!DOCTYPE html>")
    assert "</html>" in content


@pytest.mark.unit
def test_html_report_contains_project_info(project_dir: Path, design_system_dir: Path) -> None:
    path = render.render_html(project_dir, [], "run-001", design_system_dir=design_system_dir)
    content = path.read_text(encoding="utf-8")
    assert "test--project" in content
    assert "green" in content
    assert "pd-head" in content


@pytest.mark.unit
def test_html_report_contains_signals(project_dir: Path, design_system_dir: Path) -> None:
    signals = [
        _signal("action_aging", category="action", severity="high"),
        _signal("blocker_unowned", category="blocker", severity="medium"),
    ]
    path = render.render_html(project_dir, signals, "run-001", design_system_dir=design_system_dir)
    content = path.read_text(encoding="utf-8")
    assert "action_aging" in content
    assert "blocker_unowned" in content
    assert "sev-high" in content
    assert "sev-medium" in content
    assert "signal-card" in content


@pytest.mark.unit
def test_html_report_written_with_html_suffix(project_dir: Path, design_system_dir: Path) -> None:
    path = render.render_html(project_dir, [], "run-001", design_system_dir=design_system_dir)
    assert path.suffix == ".html"
    assert path.name.startswith("status-")
    assert path.parent == project_dir / "reports"


@pytest.mark.unit
def test_portfolio_html_contains_all_projects(tmp_path: Path, design_system_dir: Path) -> None:
    summaries = [
        PortfolioProject(
            project_id="alpha--project",
            status="green",
            run_id="run-a",
            signals=[],
            signals_new=0,
            errors=0,
            stage_statuses={"ingest": "ok", "render": "ok"},
        ),
        PortfolioProject(
            project_id="beta--project",
            status="amber",
            run_id="run-b",
            signals=[_signal("action_aging")],
            signals_new=1,
            errors=0,
            stage_statuses={"ingest": "ok", "render": "ok"},
        ),
    ]
    output_dir = tmp_path / "reports"
    path = render.render_portfolio_html(summaries, output_dir, design_system_dir=design_system_dir)
    content = path.read_text(encoding="utf-8")
    assert "alpha--project" in content
    assert "beta--project" in content
    assert "action_aging" in content
    assert content.startswith("<!DOCTYPE html>")
    assert "pd-shell" in content
    assert "ig-row" in content
    assert "pd-attn" in content


# ---------------------------------------------------------------------------
# HTML — idempotence tests
# ---------------------------------------------------------------------------


@pytest.mark.idempotence
def test_html_render_idempotent(project_dir: Path, design_system_dir: Path) -> None:
    signals = [_signal("action_aging", evidence=["evt-abc"])]
    path1 = render.render_html(project_dir, signals, "run-001", design_system_dir=design_system_dir)
    content1 = path1.read_text(encoding="utf-8")
    path2 = render.render_html(project_dir, signals, "run-001", design_system_dir=design_system_dir)
    content2 = path2.read_text(encoding="utf-8")
    assert content1 == content2


@pytest.mark.idempotence
def test_html_render_never_writes_to_notes_file(project_dir: Path, design_system_dir: Path) -> None:
    original = (project_dir / "project.notes.md").read_text(encoding="utf-8")
    render.render_html(project_dir, [], "run-001", design_system_dir=design_system_dir)
    assert (project_dir / "project.notes.md").read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Unit tests — new v2 helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_attention_score_orders_by_severity() -> None:
    low_proj = PortfolioProject(
        project_id="a--x", status="green", run_id="r", signals=[_signal(severity="low")],
        signals_new=0, errors=0, stage_statuses={},
    )
    med_proj = PortfolioProject(
        project_id="b--x", status="amber", run_id="r",
        signals=[_signal(severity="medium")],
        signals_new=0, errors=0, stage_statuses={},
    )
    high_proj = PortfolioProject(
        project_id="c--x", status="red", run_id="r", signals=[_signal(severity="high")],
        signals_new=0, errors=0, stage_statuses={},
    )
    scores = [_attention_score(p) for p in [low_proj, med_proj, high_proj]]
    assert scores[2] > scores[1] > scores[0]


@pytest.mark.unit
def test_category_cell_classifies_signals() -> None:
    high_sig = _signal(category="risk", severity="high")
    med_sig = _signal(category="risk", severity="medium")
    low_sig = _signal(category="risk", severity="low")

    cls, _ = _category_cell([high_sig], "risk")
    assert cls == "is-red"

    cls, _ = _category_cell([med_sig], "risk")
    assert cls == "is-yellow"

    cls, _ = _category_cell([low_sig], "risk")
    assert cls == "is-green"

    cls, _ = _category_cell([], "risk")
    assert cls == "is-empty"


@pytest.mark.unit
def test_client_grouping_splits_on_double_dash() -> None:
    assert _client_of("acme--demo") == "acme"
    assert _client_of("acme--demo--extra") == "acme"
    assert _client_of("nodash") == "nodash"


@pytest.mark.unit
def test_severity_breakdown_text() -> None:
    signals = [
        _signal(severity="high"),
        _signal(severity="high"),
        _signal(severity="medium"),
    ]
    result = _severity_breakdown(signals)
    assert result == "2 high · 1 med"


@pytest.mark.unit
def test_severity_breakdown_omits_zero_counts() -> None:
    signals = [_signal(severity="low")]
    result = _severity_breakdown(signals)
    assert "high" not in result
    assert "med" not in result
    assert "low" in result


@pytest.mark.unit
def test_headline_all_green_uses_sage_italic() -> None:
    html_green = _headline_html(3, 0)
    assert "var(--sage)" in html_green
    assert "var(--coral)" not in html_green

    html_red = _headline_html(3, 1)
    assert "var(--coral)" in html_red
    assert "var(--sage)" not in html_red


@pytest.mark.unit
def test_verdict_callout_omitted_when_no_signals() -> None:
    assert _verdict_callout_html(None) == ""


@pytest.mark.unit
def test_silent_list_omitted_when_empty() -> None:
    summaries = [
        PortfolioProject(
            project_id="a--x", status="green", run_id="r", signals=[],
            signals_new=0, errors=0, stage_statuses={},
            last_source_ts=None,
        ),
    ]
    assert _silent_list_html(summaries) == ""


@pytest.mark.unit
def test_breadcrumb_includes_project_id() -> None:
    html = _breadcrumb_html("my--project")
    assert "my--project" in html
    assert "pd-crumb" in html


@pytest.mark.unit
def test_breadcrumb_portfolio_only_without_project() -> None:
    html = _breadcrumb_html()
    assert "pd-crumb" in html
    assert "here" not in html
