from __future__ import annotations

import html as _html_lib
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from project_agent import events as _events_api
from project_agent import projects
from project_agent.schemas import Signal

logger = logging.getLogger(__name__)

_SEVERITY_ICON: dict[str, str] = {"high": "🔴", "medium": "⚠️", "low": "ℹ️"}

_SEVERITY_LABEL: dict[str, str] = {
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}

# Status → CSS modifier (project.md uses "amber"; pill class uses "status-yellow")
_STATUS_CSS: dict[str, str] = {
    "green": "status-green",
    "amber": "status-yellow",
    "red": "status-red",
}

# SVG glyphs for status pills (matches Design System §06)
_PILL_GLYPH: dict[str, str] = {
    "green": (
        '<svg class="pill-glyph" viewBox="0 0 12 12" fill="none">'
        '<path d="M2 6.5l2.5 2.5L10 3.5" stroke="currentColor"'
        ' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    ),
    # project.md emits "amber"; CSS class is status-yellow — both keys map to warning glyph
    "amber": (
        '<svg class="pill-glyph" viewBox="0 0 12 12" fill="none">'
        '<path d="M6 2v5M6 9.5v0.2" stroke="currentColor"'
        ' stroke-width="1.6" stroke-linecap="round"/>'
        "</svg>"
    ),
    "yellow": (
        '<svg class="pill-glyph" viewBox="0 0 12 12" fill="none">'
        '<path d="M6 2v5M6 9.5v0.2" stroke="currentColor"'
        ' stroke-width="1.6" stroke-linecap="round"/>'
        "</svg>"
    ),
    "red": (
        '<svg class="pill-glyph" viewBox="0 0 12 12" fill="none">'
        '<path d="M3 3l6 6M9 3l-6 6" stroke="currentColor"'
        ' stroke-width="1.6" stroke-linecap="round"/>'
        "</svg>"
    ),
}

_GOOGLE_FONTS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Fraunces:ital,opsz,wght,SOFT@0,9..144,300..600,30..100;"
    "1,9..144,300..600,30..100"
    "&family=JetBrains+Mono:wght@400;500;600&display=swap"
)

# SVG symbol defs for indicator-grid cell glyphs (copied from Portfolio Dashboard.html §504-512).
_HTML_SVG_DEFS = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true">\n'
    "  <defs>\n"
    '    <symbol id="g-check" viewBox="0 0 16 16">'
    '<path d="M3 8.5l3.2 3.2L13 5" stroke="currentColor" stroke-width="1.8"'
    ' stroke-linecap="round" stroke-linejoin="round" fill="none"/></symbol>\n'
    '    <symbol id="g-warn" viewBox="0 0 16 16">'
    '<path d="M8 3v6.5M8 12.5v0.2" stroke="currentColor" stroke-width="1.8"'
    ' stroke-linecap="round" fill="none"/></symbol>\n'
    '    <symbol id="g-cross" viewBox="0 0 16 16">'
    '<path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.8"'
    ' stroke-linecap="round" fill="none"/></symbol>\n'
    '    <symbol id="g-dot" viewBox="0 0 16 16">'
    '<circle cx="8" cy="8" r="1.4" fill="currentColor"/></symbol>\n'
    "  </defs>\n"
    "</svg>"
)

# Ordered columns for the indicator grid: (display label, signal category)
_CATEGORY_GRID: list[tuple[str, str]] = [
    ("Risk", "risk"),
    ("Delivery", "deliverable"),
    ("Action", "action"),
    ("Blocker", "blocker"),
    ("Backlog", "backlog"),
    ("Comms", "communication"),
]


class PortfolioProject(BaseModel):
    """Per-project data for portfolio HTML rendering."""

    project_id: str
    status: str
    run_id: str
    signals: list[Signal]
    signals_new: int
    errors: int
    stage_statuses: dict[str, str]
    client_id: str | None = None
    sponsor: str | None = None
    phase: str | None = None
    last_source_ts: datetime | None = None
    events_count: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_status(
    project_dir: Path,
    signals: list[Signal],
    run_id: str,
    output_dir: Path | None = None,
) -> Path:
    """Write a markdown status report and return its path.

    Output is always regenerated from inputs — safe to call repeatedly.
    Never touches project.notes.md (reads it as an excerpt only).
    """
    if output_dir is None:
        output_dir = project_dir / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    project = projects.load_project(project_dir)
    notes_path = project_dir / "project.notes.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    report_path = output_dir / f"status-{date_str}.md"

    report_path.write_text(
        _build_md_report(project, signals, run_id, notes, date_str),
        encoding="utf-8",
    )
    logger.info("Status report written: %s", report_path)
    return report_path


def render_html(
    project_dir: Path,
    signals: list[Signal],
    run_id: str,
    output_dir: Path | None = None,
    design_system_dir: Path | None = None,
) -> Path:
    """Write an HTML status report and return its path.

    CSS is loaded from design_system_dir (tokens.css + system.css + portfolio layout).
    When design_system_dir is None, auto-detects design-system/ from repo root.
    Never touches project.notes.md (reads it as an excerpt only).
    """
    if output_dir is None:
        output_dir = project_dir / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    css = _load_design_css(_resolve_design_dir(design_system_dir))

    project: dict[str, Any] = projects.load_project(project_dir)
    notes_path = project_dir / "project.notes.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""

    project_id = str(project.get("metadata", {}).get("id", "unknown"))
    events_path = project_dir / "events.ndjson"
    last_source_ts: datetime | None = None
    if events_path.exists():
        source_events = _events_api.query(
            events_path, project_id=project_id, types=["source_ingested"]
        )
        last_source_ts = max((e.ts for e in source_events), default=None)

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    report_path = output_dir / f"status-{date_str}.html"
    report_path.write_text(
        _build_html_report(project, signals, run_id, notes, date_str, css, last_source_ts),
        encoding="utf-8",
    )
    logger.info("HTML report written: %s", report_path)
    return report_path


def render_portfolio_html(
    project_summaries: list[PortfolioProject],
    output_dir: Path,
    design_system_dir: Path | None = None,
) -> Path:
    """Write a portfolio HTML dashboard and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    css = _load_design_css(_resolve_design_dir(design_system_dir))
    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    report_path = output_dir / f"portfolio-{date_str}.html"
    report_path.write_text(
        _build_portfolio_html(project_summaries, date_str, css),
        encoding="utf-8",
    )
    logger.info("Portfolio HTML written: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# CSS / shell helpers
# ---------------------------------------------------------------------------


def _resolve_design_dir(design_system_dir: Path | None) -> Path:
    if design_system_dir is not None:
        return design_system_dir
    default = Path(__file__).parent.parent.parent / "design-system"
    if default.is_dir():
        return default
    raise FileNotFoundError(
        f"Design system directory not found at {default}. "
        "Pass design_system_dir explicitly or ensure design-system/ is in the repo root."
    )


def _load_design_css(design_system_dir: Path) -> str:
    """Load tokens.css + system.css + portfolio layout CSS from design_system_dir."""
    parts: list[str] = []
    for name in ("tokens.css", "system.css"):
        p = design_system_dir / name
        if not p.exists():
            raise FileNotFoundError(
                f"Design system file not found: {p}. "
                "Ensure design-system/ is present in the repo root."
            )
        parts.append(p.read_text(encoding="utf-8"))
    # Extract portfolio-specific layout styles from the reference HTML.
    # These classes (.pd-crumb, .ig-wrap, .pd-attn, etc.) live only in
    # Portfolio Dashboard.html — not in system.css — by design.
    dashboard = design_system_dir / "Portfolio Dashboard.html"
    if dashboard.exists():
        html_text = dashboard.read_text(encoding="utf-8")
        m = re.search(r"<style>(.*?)</style>", html_text, re.DOTALL)
        if m:
            parts.append(m.group(1))
    return "\n".join(parts)


def _html_shell(title: str, body: str, design_css: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{_esc(title)}</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        f'<link href="{_GOOGLE_FONTS_URL}" rel="stylesheet">\n'
        f"<style>\n{design_css}\n</style>\n"
        "</head>\n"
        '<body class="ground">\n'
        f"{_HTML_SVG_DEFS}\n"
        f"{body}\n"
        "</body>\n"
        "</html>"
    )


# ---------------------------------------------------------------------------
# HTML helpers (unchanged from MVP)
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    return _html_lib.escape(text, quote=True)


def _status_pill_html(status: str) -> str:
    key = status.lower()
    css_mod = _STATUS_CSS.get(key, "")
    cls = f"pill {css_mod}".strip() if css_mod else "pill"
    glyph = _PILL_GLYPH.get(key, "")
    return f'<span class="{cls}">{glyph}{_esc(status)}</span>'


def _severity_badge_html(severity: str) -> str:
    label = _SEVERITY_LABEL.get(severity, severity.capitalize())
    return (
        f'<span class="badge-sev sev-{_esc(severity)}">'
        f'<span class="sev-mark"></span>'
        f"{_esc(label)}"
        f"</span>"
    )


def _count_nonempty_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _method_tag_html(method: str) -> str:
    if method == "llm":
        return (
            '<span style="margin-left:auto;font-size:10px;letter-spacing:0.08em;'
            'color:var(--violet);font-weight:600;font-family:var(--font-mono)">LLM</span>'
        )
    return (
        '<span style="margin-left:auto;font-size:10px;letter-spacing:0.08em;'
        'color:var(--sky);font-weight:600;font-family:var(--font-mono)">DET</span>'
    )


def _signal_cards_html(signals: list[Signal]) -> str:
    if not signals:
        return '<p class="text-muted">No signals detected.</p>'
    by_category: dict[str, list[Signal]] = {}
    for s in signals:
        by_category.setdefault(s.category, []).append(s)
    parts: list[str] = []
    for category in sorted(by_category):
        cat_signals = by_category[category]
        parts.append(
            f'<p class="t-eyebrow" style="margin-bottom:var(--space-4)">'
            f"{_esc(category.capitalize())} ({len(cat_signals)})"
            f"</p>"
        )
        for s in cat_signals:
            conf_html = (
                f' &middot; <span title="confidence">{s.confidence:.0%}</span>'
                if s.method == "llm"
                else ""
            )
            parts.append(
                f'<div class="signal-card">'
                f'<div class="signal-head">'
                f"{_severity_badge_html(s.severity)}"
                f'<span class="signal-category">{_esc(s.category)}</span>'
                f"{_method_tag_html(s.method)}"
                f"</div>"
                f'<h4 class="signal-title">{_esc(s.type)}</h4>'
                f'<p class="signal-rationale">{_esc(s.rationale)}</p>'
                f'<div class="signal-foot">'
                f'<span class="signal-evidence">'
                f"{len(s.evidence)} event(s){conf_html}"
                f"</span>"
                f'<span class="signal-action">{_esc(s.recommended_action)}</span>'
                f"</div>"
                f"</div>"
            )
    return "\n".join(parts)


def _pm_notes_section_html(notes: str) -> str:
    body = notes.strip()
    if not body:
        return ""
    excerpt = _esc(body[:500])
    suffix = "\n<em>(see project.notes.md for full notes)</em>" if len(body) > 500 else ""
    return (
        f'<section class="panel">'
        f'<h2 class="t-section-title">PM Notes</h2>'
        f'<div class="pullquote">'
        f'<p class="t-pull">{excerpt}{suffix}</p>'
        f'<span class="pullquote-attr">project.notes.md</span>'
        f"</div>"
        f"</section>"
    )


# ---------------------------------------------------------------------------
# New helpers (v2)
# ---------------------------------------------------------------------------


def _client_of(project_id: str) -> str:
    return project_id.split("--", 1)[0]


def _severity_weight(sev: str) -> int:
    return {"high": 10, "medium": 5, "low": 2}.get(sev, 1)


def _attention_score(p: PortfolioProject) -> int:
    return sum(_severity_weight(s.severity) for s in p.signals) + p.errors * 10


def _category_cell(signals: list[Signal], category: str) -> tuple[str, str]:
    """Return (css_modifier, rationale_excerpt) for one indicator-grid cell."""
    matching = [s for s in signals if s.category == category]
    if not matching:
        return "is-empty", ""
    severities = {s.severity for s in matching}
    if "high" in severities:
        css_class = "is-red"
    elif "medium" in severities:
        css_class = "is-yellow"
    else:
        css_class = "is-green"
    return css_class, matching[0].rationale[:100]


def _severity_breakdown(signals: list[Signal]) -> str:
    counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for s in signals:
        if s.severity in counts:
            counts[s.severity] += 1
    parts: list[str] = []
    if counts["high"]:
        parts.append(f"{counts['high']} high")
    if counts["medium"]:
        parts.append(f"{counts['medium']} med")
    if counts["low"]:
        parts.append(f"{counts['low']} low")
    return " · ".join(parts)


def _silent_days(last_source_ts: datetime | None, today: date) -> int | None:
    if last_source_ts is None:
        return None
    return (today - last_source_ts.date()).days


def _headline_html(n_total: int, n_red: int) -> str:
    """Portfolio page title with italic coral (on-red) or sage (all-green) emphasis."""
    suffix = "s" if n_total != 1 else ""
    if n_red > 0:
        return (
            f'<h1 class="t-page-title">{n_total} project{suffix}, '
            f'<em style="color:var(--coral)">{n_red} on red.</em></h1>'
        )
    return (
        f'<h1 class="t-page-title">{n_total} project{suffix}, '
        f'<em style="color:var(--sage)">all green.</em></h1>'
    )


def _lead_paragraph(
    n_signals: int, n_high: int, n_total: int, n_red: int, mode: str
) -> str:
    if mode == "portfolio":
        if n_red > 0:
            attn = (
                f'{n_red} project{"s" if n_red != 1 else ""} '
                f'need{"s" if n_red == 1 else ""} immediate attention.'
            )
            sig = (
                f" {n_signals} signal{'s' if n_signals != 1 else ''} detected"
                + (f", including {n_high} high-priority." if n_high else ".")
            )
            return attn + sig
        elif n_signals > 0:
            return (
                f'All {n_total} project{"s" if n_total != 1 else ""} '
                f'{"are" if n_total != 1 else "is"} healthy. '
                f'{n_signals} signal{"s" if n_signals != 1 else ""} detected for review.'
            )
        else:
            return (
                f'All {n_total} project{"s" if n_total != 1 else ""} '
                f'{"are" if n_total != 1 else "is"} healthy. No signals detected.'
            )
    else:  # project
        if n_high > 0:
            return (
                f'{n_signals} signal{"s" if n_signals != 1 else ""} detected. '
                f'{n_high} require{"s" if n_high == 1 else ""} immediate review.'
            )
        elif n_signals > 0:
            return (
                f'{n_signals} signal{"s" if n_signals != 1 else ""} detected. '
                "No high-priority issues."
            )
        else:
            return "All clear — no signals detected."


def _breadcrumb_html(project_id: str | None = None) -> str:
    parts = [
        '<nav class="pd-crumb">',
        '<a href="#">Project Agent</a>',
        '<span class="sep">/</span>',
        '<a href="#">Portfolio</a>',
    ]
    if project_id:
        parts += [
            '<span class="sep">/</span>',
            f'<span class="here">{_esc(project_id)}</span>',
        ]
    parts.append("</nav>")
    return "\n".join(parts)


def _verdict_callout_html(top_signal: Signal | None) -> str:
    if top_signal is None:
        return ""
    return (
        f'<div class="callout is-verdict" style="margin-top:var(--space-7)">'
        f"<p><strong>If you do one thing today:</strong> "
        f"{_esc(top_signal.recommended_action)}</p>"
        f"</div>"
    )


def _parse_item_row(line: str) -> tuple[str, str, str]:
    """Parse a checkbox action/blocker line. Returns (title, owner, due)."""
    stripped = line.strip()
    if not stripped:
        return ("", "", "")
    m = re.match(r"^-\s*\[[ xX?]\]\s*(.+)", stripped)
    if m:
        rest = m.group(1)
        parts = [p.strip() for p in rest.split("|")]
        title = parts[0]
        owner = ""
        due = ""
        for part in parts[1:]:
            low = part.lower()
            if low.startswith("owner:"):
                owner = part[6:].strip()
            elif low.startswith("due:"):
                due = part[4:].strip()
        return (title, owner, due)
    return (stripped, "", "")


def _item_rows_html(text: str, empty_msg: str) -> str:
    if not text.strip():
        return f'<p class="text-muted">{empty_msg}</p>'
    rows: list[str] = []
    for line in text.splitlines():
        title, owner, due = _parse_item_row(line)
        if not title:
            continue
        meta_parts: list[str] = []
        if owner:
            meta_parts.append(f"owner: {_esc(owner)}")
        if due:
            meta_parts.append(f"due: {_esc(due)}")
        meta_html = (
            f' <span style="color:var(--text-muted);font-size:11px;font-family:var(--font-mono)">'
            f'{" &middot; ".join(meta_parts)}'
            f"</span>"
            if meta_parts
            else ""
        )
        rows.append(
            f'<div style="display:flex;align-items:baseline;gap:var(--space-3);'
            f'padding:var(--space-2) 0;border-bottom:1px solid var(--border);font-size:13px">'
            f'<span style="color:var(--text-faint);flex-shrink:0">—</span>'
            f'<span style="flex:1;color:var(--text-secondary)">{_esc(title)}{meta_html}</span>'
            f"</div>"
        )
    return "\n".join(rows) if rows else f'<p class="text-muted">{empty_msg}</p>'


def _actions_panel_html(actions_text: str) -> str:
    return _item_rows_html(actions_text, "No open actions.")


def _blockers_panel_html(blockers_text: str) -> str:
    return _item_rows_html(blockers_text, "No open blockers.")


# ---------------------------------------------------------------------------
# Single-project page sub-helpers
# ---------------------------------------------------------------------------


def _project_header_html(
    project_id: str,
    status: str,
    run_id: str,
    n_signals: int,
    n_high: int,
    date_str: str,
) -> str:
    key = status.lower()
    color = (
        "var(--coral)"
        if key == "red"
        else "var(--amber)"
        if key in ("amber", "yellow")
        else "var(--sage)"
    )
    lead = _lead_paragraph(
        n_signals, n_high, 1, 1 if key in ("red", "amber") else 0, "project"
    )
    return (
        f'<header class="pd-head">'
        f"<div>"
        f'<p class="t-eyebrow">Project status</p>'
        f'<h1 class="t-page-title">{_esc(project_id)} &mdash; '
        f'<em style="color:{color}">{_esc(status)}.</em></h1>'
        f'<p class="t-body" style="margin-top:var(--space-3)">{lead}</p>'
        f"</div>"
        f'<div class="head-meta">'
        f'<span class="meta-row"><b>{_esc(date_str)}</b></span>'
        f'<span class="meta-row">Run <b>{_esc(run_id)}</b></span>'
        f"</div>"
        f"</header>"
    )


def _project_fact_strip_html(
    status: str,
    signals: list[Signal],
    actions_text: str,
    blockers_text: str,
    last_source_ts: datetime | None,
    today: date,
) -> str:
    breakdown = _severity_breakdown(signals)
    n_actions = _count_nonempty_lines(actions_text)
    n_blockers = _count_nonempty_lines(blockers_text)
    days = _silent_days(last_source_ts, today)
    last_src_val = str(days) if days is not None else "—"
    last_src_detail = "days ago" if days is not None else "no source data"

    def card(label: str, value: str, detail: str = "") -> str:
        detail_html = (
            f'<span class="fact-detail">{_esc(detail)}</span>' if detail else ""
        )
        return (
            f'<div class="fact-card">'
            f'<span class="fact-label">{_esc(label)}</span>'
            f'<span class="fact-value">{value}</span>'
            f"{detail_html}"
            f"</div>"
        )

    status_card = (
        f'<div class="fact-card">'
        f'<span class="fact-label">Status</span>'
        f"{_status_pill_html(status)}"
        f"</div>"
    )
    return (
        f'<div class="pd-facts">'
        + status_card
        + card("Signals", str(len(signals)), breakdown or "none")
        + card("Open actions", str(n_actions))
        + card("Open blockers", str(n_blockers))
        + card("Last source", last_src_val, last_src_detail)
        + "</div>"
    )


# ---------------------------------------------------------------------------
# Portfolio page sub-helpers
# ---------------------------------------------------------------------------


def _portfolio_breadcrumb_html(date_str: str) -> str:
    return (
        '<nav class="pd-crumb">'
        '<a href="#">Project Agent</a>'
        '<span class="sep">/</span>'
        '<a href="#">Portfolio</a>'
        '<span class="sep">/</span>'
        f'<span class="here">Daily &middot; {_esc(date_str)}</span>'
        "</nav>"
    )


def _portfolio_header_html(
    summaries: list[PortfolioProject],
    date_str: str,
    run_id: str,
) -> str:
    n_total = len(summaries)
    n_red = sum(1 for p in summaries if p.status.lower() == "red")
    all_signals = [s for p in summaries for s in p.signals]
    n_signals = len(all_signals)
    n_high = sum(1 for s in all_signals if s.severity == "high")
    breakdown = _severity_breakdown(all_signals)
    lead = _lead_paragraph(n_signals, n_high, n_total, n_red, "portfolio")
    totals_detail = (
        f"{n_total} project{'s' if n_total != 1 else ''}"
        f" &middot; {n_signals} signal{'s' if n_signals != 1 else ''}"
        + (f" &middot; {_esc(breakdown)}" if breakdown else "")
    )
    return (
        f'<header class="pd-head">'
        f"<div>"
        f'<p class="t-eyebrow">Portfolio dashboard</p>'
        f"{_headline_html(n_total, n_red)}"
        f'<p class="t-body" style="margin-top:var(--space-3);max-width:480px">{lead}</p>'
        f"</div>"
        f'<div class="head-meta">'
        f'<span class="meta-row"><b>{_esc(date_str)}</b></span>'
        f'<span class="meta-row">Run <b>{_esc(run_id)}</b></span>'
        f'<span class="meta-row">{totals_detail}</span>'
        f"</div>"
        f"</header>"
    )


def _portfolio_fact_strip_html(summaries: list[PortfolioProject], today: date) -> str:
    n_red = sum(1 for p in summaries if p.status.lower() == "red")
    all_signals = [s for p in summaries for s in p.signals]
    breakdown = _severity_breakdown(all_signals)
    n_actions = sum(1 for s in all_signals if s.category == "action")
    n_blockers = sum(1 for s in all_signals if s.category == "blocker")
    n_silent = sum(
        1 for p in summaries
        if (d := _silent_days(p.last_source_ts, today)) is not None and d > 7
    )

    def card(label: str, value: str, detail: str = "") -> str:
        detail_html = (
            f'<span class="fact-detail">{_esc(detail)}</span>' if detail else ""
        )
        return (
            f'<div class="fact-card">'
            f'<span class="fact-label">{_esc(label)}</span>'
            f'<span class="fact-value">{value}</span>'
            f"{detail_html}"
            f"</div>"
        )

    return (
        f'<div class="pd-facts">'
        + card("Projects red", str(n_red), f"of {len(summaries)} total")
        + card("Signals open", str(len(all_signals)), breakdown or "none")
        + card("Actions aging", str(n_actions))
        + card("Blockers open", str(n_blockers))
        + card("Silent > 7d", str(n_silent))
        + "</div>"
    )


def _attention_strip_html(top3: list[PortfolioProject]) -> str:
    if not top3:
        return ""
    cards: list[str] = []
    for i, p in enumerate(top3[:3], 1):
        score = _attention_score(p)
        top_sig = (
            max(p.signals, key=lambda s: _severity_weight(s.severity))
            if p.signals
            else None
        )
        reason = top_sig.rationale[:120] if top_sig else "No signals detected."
        sub_parts: list[str] = []
        if p.sponsor:
            sub_parts.append(p.sponsor)
        if p.phase:
            sub_parts.append(f"Phase {p.phase}")
        sub_html = _esc(" · ".join(sub_parts)) if sub_parts else "&nbsp;"
        cards.append(
            f'<div class="attn-card">'
            f'<span class="ac-rank">#{i}</span>'
            f"<div>"
            f'<h3 class="ac-title">{_esc(p.project_id)}</h3>'
            f'<p class="ac-sub">{sub_html}</p>'
            f"</div>"
            f'<p class="ac-reason">{_esc(reason)}</p>'
            f'<div class="ac-score-row">'
            f'<div class="ac-foot">'
            f'<a href="#row-{_esc(p.project_id)}">Jump to project →</a>'
            f"</div>"
            f'<div class="ac-score">{score}</div>'
            f"</div>"
            f"</div>"
        )
    return (
        f'<div class="pd-attn" style="margin-bottom:var(--space-7)">'
        f'<div class="attn-lead">'
        f"<h2>Today's <em>top three</em></h2>"
        f"<p>Ranked by signal severity and error weight.</p>"
        f"</div>"
        f'<div class="attn-list">'
        + "".join(cards)
        + "</div>"
        + "</div>"
    )


def _ig_row_html(p: PortfolioProject) -> str:
    cells: list[str] = []
    for _label, cat in _CATEGORY_GRID:
        css_class, rationale = _category_cell(p.signals, cat)
        pop_html = (
            f'<div class="pop">'
            f'<div class="pop-head">{_esc(cat.capitalize())}</div>'
            f"{_esc(rationale)}"
            f"</div>"
            if rationale
            else ""
        )
        cells.append(f'<div class="cell {css_class}">{pop_html}</div>')

    n_sig = len(p.signals)
    breakdown = _severity_breakdown(p.signals)
    sig_html = (
        f'<span title="{_esc(breakdown)}" style="cursor:help">{n_sig}</span>'
        if breakdown
        else str(n_sig)
    )
    return (
        f'<div class="ig-row" id="row-{_esc(p.project_id)}">'
        f'<div class="row-label">'
        f'<span class="pname">{_esc(p.project_id)}</span>'
        f'<span class="pmeta">{_esc(p.status)}</span>'
        f"</div>"
        + "".join(cells)
        + f'<div class="row-actions"><span class="num">{sig_html}</span></div>'
        + f'<div class="row-trend"><span class="spark">'
        # TODO Phase 1: compute Δ7d from prior-run snapshot
        + f'<span class="delta flat">0</span>'
        + f"</span></div>"
        + "</div>"
    )


def _indicator_grid_html(summaries: list[PortfolioProject], today: date) -> str:
    clients: dict[str, list[PortfolioProject]] = {}
    for p in summaries:
        client = p.client_id or _client_of(p.project_id)
        clients.setdefault(client, []).append(p)

    col_labels = "".join(
        f'<span class="col-label">{_esc(label)}</span>'
        for label, _ in _CATEGORY_GRID
    )
    col_head = (
        f'<div class="ig-col-head">'
        f'<span class="col-label first">Project</span>'
        f"{col_labels}"
        f'<span class="col-label num">Signals</span>'
        f'<span class="col-label num">Δ7d</span>'
        f"</div>"
    )

    rows: list[str] = []
    for client, ps in clients.items():
        n = len(ps)
        n_red = sum(1 for p in ps if p.status.lower() == "red")
        count_suffix = f", {n_red} red" if n_red else ""
        rows.append(
            f'<div class="ig-group-header">'
            f'<span class="gh-name">{_esc(client)}</span>'
            f'<span class="gh-count">{n} project{"s" if n != 1 else ""}'
            f"{_esc(count_suffix)}</span>"
            f"</div>"
        )
        rows += [_ig_row_html(p) for p in ps]

    legend = (
        '<div class="pd-legend">'
        '<span class="leg"><span class="sw r"></span> High severity</span>'
        '<span class="leg"><span class="sw y"></span> Medium</span>'
        '<span class="leg"><span class="sw g"></span> Low / info</span>'
        '<span class="leg"><span class="sw e"></span> No data</span>'
        "</div>"
    )
    return (
        f'<div class="ig-wrap" style="margin-bottom:var(--space-7)">'
        + col_head
        + "".join(rows)
        + legend
        + "</div>"
    )


def _recent_signals_html(summaries: list[PortfolioProject], limit: int = 8) -> str:
    all_sigs: list[tuple[str, Signal]] = [
        (p.project_id, s) for p in summaries for s in p.signals
    ]
    all_sigs.sort(key=lambda x: x[1].detected_at, reverse=True)
    top = all_sigs[:limit]
    if not top:
        return '<p class="text-muted">No signals detected.</p>'
    lines: list[str] = []
    for pid, sig in top:
        lines.append(
            f'<div class="signal-line">'
            f"{_severity_badge_html(sig.severity)}"
            f'<span class="sl-title">{_esc(sig.type)}'
            f' <span class="pid">— {_esc(pid)}</span></span>'
            f'<span class="sl-meta">{_esc(sig.category)}</span>'
            f"</div>"
        )
    return "\n".join(lines)


def _silent_list_html(
    summaries: list[PortfolioProject],
    threshold_days: int = 7,
    today: date | None = None,
) -> str:
    if today is None:
        today = datetime.now(tz=timezone.utc).date()
    silent: list[tuple[PortfolioProject, int]] = []
    for p in summaries:
        days = _silent_days(p.last_source_ts, today)
        if days is not None and days > threshold_days:
            silent.append((p, days))
    if not silent:
        return ""
    silent.sort(key=lambda x: x[1], reverse=True)
    rows = "".join(
        f'<div class="silent-row">'
        f'<span class="name">{_esc(proj.project_id)}</span>'
        f'<span class="meta">{d}d silent</span>'
        f"</div>"
        for proj, d in silent
    )
    return (
        f'<h3 class="pd-section-title">Silent <em>&gt; {threshold_days}d</em></h3>'
        + rows
        + '<div style="margin-bottom:var(--space-6)"></div>'
    )


def _client_rollup_html(summaries: list[PortfolioProject]) -> str:
    clients: dict[str, list[PortfolioProject]] = {}
    for p in summaries:
        client = p.client_id or _client_of(p.project_id)
        clients.setdefault(client, []).append(p)
    if not clients:
        return ""
    cards: list[str] = []
    for client, ps in clients.items():
        n_sig = sum(len(p.signals) for p in ps)
        n_r = sum(1 for p in ps if p.status.lower() == "red")
        n_y = sum(1 for p in ps if p.status.lower() in ("amber", "yellow"))
        n_g = sum(1 for p in ps if p.status.lower() == "green")
        mini_cells = "".join(
            '<span class="mini {cls}" title="{title}"></span>'.format(
                cls=(
                    "r"
                    if p.status.lower() == "red"
                    else "y"
                    if p.status.lower() in ("amber", "yellow")
                    else "g"
                ),
                title=_esc(p.project_id),
            )
            for p in ps
        )
        cards.append(
            f'<div class="client-card">'
            f'<h4 class="cc-name">{_esc(client)}</h4>'
            f'<p class="cc-meta">'
            f'{len(ps)} project{"s" if len(ps) != 1 else ""}'
            f' &middot; {n_sig} signal{"s" if n_sig != 1 else ""}'
            f"</p>"
            f'<div class="cc-cells">{mini_cells}</div>'
            f'<div class="cc-foot">'
            f"<span>{n_r}r &middot; {n_y}y &middot; {n_g}g</span>"
            f"</div>"
            f"</div>"
        )
    return (
        '<h3 class="pd-section-title">By client</h3>'
        '<div class="client-summary">'
        + "".join(cards)
        + "</div>"
    )


def _portfolio_footer_html(date_str: str, run_id: str) -> str:
    return (
        f'<footer class="pd-foot">'
        f"<div><b>Portfolio &middot; {_esc(date_str)}</b></div>"
        f"<div>Run <b>{_esc(run_id)}</b> &middot; Built offline</div>"
        f"</footer>"
    )


# ---------------------------------------------------------------------------
# Main HTML builders
# ---------------------------------------------------------------------------


def _build_html_report(
    project: dict[str, Any],
    signals: list[Signal],
    run_id: str,
    notes: str,
    date_str: str,
    css: str,
    last_source_ts: datetime | None = None,
) -> str:
    meta: dict[str, Any] = project.get("metadata", {})
    sections: dict[str, Any] = project.get("sections", {})
    project_id = str(meta.get("id", "unknown"))
    status = str(meta.get("status", "unknown"))
    actions_text = str(sections.get("Actions", "")).strip()
    blockers_text = str(sections.get("Blockers", "")).strip()
    n_signals = len(signals)
    n_high = sum(1 for s in signals if s.severity == "high")
    today = datetime.now(tz=timezone.utc).date()
    top_signal: Signal | None = (
        max(signals, key=lambda s: _severity_weight(s.severity)) if signals else None
    )

    body = "\n".join([
        '<div class="pd-shell">',
        _breadcrumb_html(project_id),
        _project_header_html(project_id, status, run_id, n_signals, n_high, date_str),
        _project_fact_strip_html(
            status, signals, actions_text, blockers_text, last_source_ts, today
        ),
        '<section class="panel">',
        f'  <h2 class="t-section-title">Signals ({n_signals})</h2>',
        f"  {_signal_cards_html(signals)}",
        "</section>",
        '<section class="panel">',
        '  <h2 class="t-section-title">Actions</h2>',
        f"  {_actions_panel_html(actions_text)}",
        "</section>",
        '<section class="panel">',
        '  <h2 class="t-section-title">Blockers</h2>',
        f"  {_blockers_panel_html(blockers_text)}",
        "</section>",
        _pm_notes_section_html(notes),
        _verdict_callout_html(top_signal),
        f'<footer class="pd-foot">'
        f"<div>Project Agent &middot; {_esc(date_str)}</div>"
        f"<div>Run <b>{_esc(run_id)}</b> &middot; Built offline</div>"
        f"</footer>",
        "</div>",
    ])
    return _html_shell(f"{project_id} — Status {date_str}", body, css)


def _build_portfolio_html(
    project_summaries: list[PortfolioProject],
    date_str: str,
    css: str,
) -> str:
    today = datetime.now(tz=timezone.utc).date()
    run_id = project_summaries[0].run_id if project_summaries else "run-unknown"

    top3 = sorted(project_summaries, key=_attention_score, reverse=True)[:3]
    top_signal: Signal | None = None
    if top3 and top3[0].signals:
        top_signal = max(top3[0].signals, key=lambda s: _severity_weight(s.severity))

    recent = _recent_signals_html(project_summaries)
    silent_panel = _silent_list_html(project_summaries, today=today)
    client_panel = _client_rollup_html(project_summaries)
    verdict = _verdict_callout_html(top_signal)

    right_parts: list[str] = ["<div>"]
    if silent_panel:
        right_parts.append(silent_panel)
    if client_panel:
        right_parts.append(client_panel)
    if verdict:
        right_parts.append(verdict)
    right_parts.append("</div>")

    secondary = (
        '<div class="pd-secondary">'
        "<div>"
        '<h3 class="pd-section-title">Recent signals</h3>'
        + recent
        + "</div>"
        + "\n".join(right_parts)
        + "</div>"
    )

    body = "\n".join([
        '<div class="pd-shell">',
        _portfolio_breadcrumb_html(date_str),
        _portfolio_header_html(project_summaries, date_str, run_id),
        _portfolio_fact_strip_html(project_summaries, today),
        _attention_strip_html(top3) if any(p.signals for p in top3) else "",
        _indicator_grid_html(project_summaries, today),
        secondary,
        _portfolio_footer_html(date_str, run_id),
        "</div>",
    ])
    return _html_shell(f"Portfolio — {date_str}", body, css)


# ---------------------------------------------------------------------------
# Markdown helpers (unchanged from MVP)
# ---------------------------------------------------------------------------


def _build_md_report(
    project: dict[str, Any],
    signals: list[Signal],
    run_id: str,
    notes: str,
    date_str: str,
) -> str:
    meta = project["metadata"]
    sections = project.get("sections", {})
    lines: list[str] = []

    lines += [
        "# Project Status Report",
        "",
        f"**Status:** {meta.get('status', 'unknown')}  ",
        f"**Run:** {run_id}  ",
        f"**Generated:** {date_str}",
        "",
    ]

    if signals:
        lines += [f"## Signals ({len(signals)})", ""]
        by_category: dict[str, list[Signal]] = {}
        for s in signals:
            by_category.setdefault(s.category, []).append(s)
        for category in sorted(by_category):
            cat_signals = by_category[category]
            lines += [f"### {category.capitalize()} ({len(cat_signals)})", ""]
            for s in cat_signals:
                icon = _SEVERITY_ICON.get(s.severity, "•")
                lines.append(f"- {icon} **{s.type}** [{s.severity}]: {s.rationale}")
                lines.append(f"  - *Recommended:* {s.recommended_action}")
                lines.append(f"  - *Evidence:* {len(s.evidence)} event(s)")
            lines.append("")
    else:
        lines += ["## Signals", "", "No signals detected.", ""]

    lines += ["## Actions", ""]
    actions_body = sections.get("Actions", "").strip()
    lines.append(actions_body if actions_body else "No open actions.")
    lines.append("")

    lines += ["## Blockers", ""]
    blockers_body = sections.get("Blockers", "").strip()
    lines.append(blockers_body if blockers_body else "No open blockers.")
    lines.append("")

    notes_body = notes.strip()
    if notes_body:
        excerpt = notes_body[:500]
        if len(notes_body) > 500:
            excerpt += "\n\n*(see project.notes.md for full notes)*"
        lines += ["## PM Notes", "", excerpt, ""]

    return "\n".join(lines)
