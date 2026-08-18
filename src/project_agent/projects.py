"""Reading project state out of the compiled wiki.

`project.md` is gone (v2.0). A project's state now lives at
`kb/projects/<id>/index.md`, written by `project_agent.wiki` — this module
only reads it, plus the PM-owned front-matter that seeds it.

The split is: `project.notes.md` is PM-owned and carries the project's
declared metadata (status, sponsor, phase, stakeholders); everything below
the front-matter is free-form prose the pipeline never touches. The compiler
copies that metadata forward into the index article. Nothing hand-written
lives in `kb/`.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import frontmatter

from project_agent.wiki import default_vault_dir

logger = logging.getLogger(__name__)


def index_path(project_dir: Path, vault_dir: Path | None = None) -> Path:
    """Where this project's wiki home note lives."""
    vault = vault_dir if vault_dir is not None else default_vault_dir(project_dir)
    return vault / "projects" / project_dir.name / "index.md"


def load_project(project_dir: Path, vault_dir: Path | None = None) -> dict[str, Any]:
    """Parse the project's index article: front-matter plus section bodies."""
    path = index_path(project_dir, vault_dir)
    post = frontmatter.load(str(path))
    return {
        "metadata": dict(post.metadata),
        "sections": _parse_sections(str(post.content)),
        "content": str(post.content),
    }


def load_notes_metadata(project_dir: Path) -> dict[str, Any]:
    """PM-declared metadata from project.notes.md front-matter.

    Optional and often absent — a project with no front-matter still compiles,
    it just gets defaults. Returns sensible ones so a fresh clone with only a
    `project.notes.md` produces a usable index article rather than an error.
    """
    project_id = project_dir.name
    defaults: dict[str, Any] = {
        "id": project_id,
        "client": project_id.split("--", 1)[0],
        "status": "green",
    }
    notes = project_dir / "project.notes.md"
    if not notes.exists():
        return defaults

    post = frontmatter.load(str(notes))
    declared = {k: v for k, v in dict(post.metadata).items() if v is not None}
    return {**defaults, **declared}


def notes_body(project_dir: Path) -> str:
    """The PM's free-form prose, front-matter stripped. Never written to."""
    notes = project_dir / "project.notes.md"
    if not notes.exists():
        return ""
    return str(frontmatter.load(str(notes)).content)


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
