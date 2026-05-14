from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from project_agent import projects
from project_agent.schemas import Signal

logger = logging.getLogger(__name__)

_SEVERITY_ICON = {"high": "🔴", "medium": "⚠️", "low": "ℹ️"}


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
        _build_report(project, signals, run_id, notes, date_str),
        encoding="utf-8",
    )
    logger.info("Status report written: %s", report_path)
    return report_path


def _build_report(
    project: dict,
    signals: list[Signal],
    run_id: str,
    notes: str,
    date_str: str,
) -> str:
    meta = project["metadata"]
    sections = project.get("sections", {})
    lines: list[str] = []

    # Header
    lines += [
        "# Project Status Report",
        "",
        f"**Status:** {meta.get('status', 'unknown')}  ",
        f"**Run:** {run_id}  ",
        f"**Generated:** {date_str}",
        "",
    ]

    # Signals
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

    # Actions
    lines += ["## Actions", ""]
    actions_body = sections.get("Actions", "").strip()
    lines.append(actions_body if actions_body else "No open actions.")
    lines.append("")

    # Blockers
    lines += ["## Blockers", ""]
    blockers_body = sections.get("Blockers", "").strip()
    lines.append(blockers_body if blockers_body else "No open blockers.")
    lines.append("")

    # PM Notes excerpt
    notes_body = notes.strip()
    if notes_body:
        excerpt = notes_body[:500]
        if len(notes_body) > 500:
            excerpt += "\n\n*(see project.notes.md for full notes)*"
        lines += ["## PM Notes", "", excerpt, ""]

    return "\n".join(lines)
