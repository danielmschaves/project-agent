"""Tests for project_agent.render — the portfolio dashboard."""
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


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HTML — unit tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# The design system is loaded as stylesheets, not scraped out of a document
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_design_css_loads_all_three_stylesheets(design_system_dir: Path) -> None:
    css = render._load_design_css(design_system_dir)
    assert "--bg-deep" in css          # tokens.css
    assert ".pd-crumb" in css or ".ig-wrap" in css  # portfolio.css


@pytest.mark.unit
def test_design_css_no_longer_reads_the_reference_html(tmp_path: Path) -> None:
    """The renderer must not depend on 'Portfolio Dashboard.html' at runtime."""
    for name in ("tokens.css", "system.css", "portfolio.css"):
        (tmp_path / name).write_text(f"/* {name} */\n", encoding="utf-8")
    css = render._load_design_css(tmp_path)
    assert "portfolio.css" in css


@pytest.mark.unit
def test_design_css_missing_stylesheet_is_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "tokens.css").write_text("/* x */", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="system.css"):
        render._load_design_css(tmp_path)


@pytest.mark.idempotence
def test_portfolio_render_is_idempotent(tmp_path: Path, design_system_dir: Path) -> None:
    summaries = [
        render.PortfolioProject(
            project_id="acme--one", status="green", run_id="run-001",
            signals=[], signals_new=0, errors=0, stage_statuses={}, client_id="acme",
        )
    ]
    out = tmp_path / "reports"
    first = render.render_portfolio_html(summaries, out, design_system_dir=design_system_dir)
    content = first.read_text(encoding="utf-8")
    second = render.render_portfolio_html(summaries, out, design_system_dir=design_system_dir)
    assert second.read_text(encoding="utf-8") == content
