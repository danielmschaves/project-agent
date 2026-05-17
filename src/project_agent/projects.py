from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import frontmatter

logger = logging.getLogger(__name__)

_CANONICAL_SECTIONS = {"Actions", "Blockers", "Risks", "Decisions", "Milestones"}


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_project(project_dir: Path) -> dict[str, Any]:
    """Parse project.md front-matter and section bodies."""
    path = project_dir / "project.md"
    post = frontmatter.load(str(path))
    return {
        "metadata": dict(post.metadata),
        "sections": _parse_sections(str(post.content)),
        "content": str(post.content),
    }


def _parse_sections(content: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in content.splitlines(keepends=True):
        m = re.match(r"^## (.+)", line)
        if m:
            if current_name is not None:
                sections[current_name] = "".join(current_lines).strip()
            current_name = m.group(1).strip()
            current_lines = []
        else:
            if current_name is not None:
                current_lines.append(line)

    if current_name is not None:
        sections[current_name] = "".join(current_lines).strip()

    return sections


# ---------------------------------------------------------------------------
# Update — full rewrite of canonical sections, never touches project.notes.md
# ---------------------------------------------------------------------------

def update_project(project_dir: Path, facts: list[dict[str, Any]]) -> None:
    """Rewrite project.md canonical sections from facts.

    Canonical sections (Actions, Blockers, Risks, Decisions, Milestones) are
    fully overwritten from the provided facts list.  All other sections
    (Mission, Scope, …) are preserved verbatim.
    project.notes.md is never read or written.
    """
    path = project_dir / "project.md"
    post = frontmatter.load(str(path))
    existing = _parse_sections(str(post.content))

    updated = dict(existing)
    updated["Actions"] = _build_actions(facts)
    updated["Blockers"] = _build_blockers(facts)
    updated["Risks"] = _build_risks(facts)
    updated["Decisions"] = _build_decisions(facts)
    updated["Milestones"] = _build_milestones(facts)

    # Preserve original section order; append canonical ones if absent
    order = list(existing.keys())
    for sec in ("Actions", "Blockers", "Risks", "Decisions", "Milestones"):
        if sec not in order:
            order.append(sec)

    body_parts: list[str] = []
    for sec in order:
        body = updated.get(sec, "")
        if body:
            body_parts.append(f"## {sec}\n\n{body}")
        else:
            body_parts.append(f"## {sec}")

    post.content = "\n\n".join(body_parts) + "\n"
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    logger.info("project.md updated: %d facts applied", len(facts))


def _build_actions(facts: list[dict[str, Any]]) -> str:
    items = [f for f in facts if f.get("type") == "action_added"]
    if not items:
        return ""
    lines: list[str] = []
    for a in items:
        owner = a.get("owner") or "—"
        due = a.get("due") or "—"
        status = a.get("status", "open")
        lines.append(f"- [ ] {a['description']} | owner: {owner} | due: {due} | status: {status}")
    return "\n".join(lines)


def _build_blockers(facts: list[dict[str, Any]]) -> str:
    items = [f for f in facts if f.get("type") == "blocker_added"]
    if not items:
        return ""
    lines: list[str] = []
    for b in items:
        owner = b.get("owner") or "—"
        due = b.get("due") or "—"
        lines.append(f"- {b['description']} | owner: {owner} | due: {due}")
    return "\n".join(lines)


def _build_risks(facts: list[dict[str, Any]]) -> str:
    items = [f for f in facts if f.get("type") == "risk_added"]
    if not items:
        return ""
    lines = ["| Description | Severity | Owner | Status |", "|---|---|---|---|"]
    for r in items:
        owner = r.get("owner") or "—"
        severity = r.get("severity", "medium")
        status = r.get("status", "open")
        lines.append(f"| {r['description']} | {severity} | {owner} | {status} |")
    return "\n".join(lines)


def _build_decisions(facts: list[dict[str, Any]]) -> str:
    items = [f for f in facts if f.get("type") == "decision_made"]
    if not items:
        return ""
    lines = ["| Decision | Rationale | Decided By |", "|---|---|---|"]
    for d in items:
        rationale = d.get("rationale") or "—"
        decided_by = d.get("decided_by") or "—"
        lines.append(f"| {d['description']} | {rationale} | {decided_by} |")
    return "\n".join(lines)


def _build_milestones(facts: list[dict[str, Any]]) -> str:
    items = [f for f in facts if f.get("type") == "milestone_added"]
    if not items:
        return ""
    lines: list[str] = []
    for m in items:
        status = m.get("status", "open")
        checkbox = "[x]" if status == "done" else "[ ]"
        target_date = m.get("target_date") or "—"
        owner = m.get("owner") or "—"
        lines.append(f"- {checkbox} {m['description']} | target: {target_date} | owner: {owner}")
    return "\n".join(lines)
