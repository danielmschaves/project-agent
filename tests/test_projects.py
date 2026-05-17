"""Tests for project_agent.projects (load_project, update_project)."""
from pathlib import Path

import pytest

from project_agent import projects


def _write_project_md(project_dir: Path, content: str) -> None:
    (project_dir / "project.md").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# load_project
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_load_project_parses_frontmatter(project_dir: Path) -> None:
    result = projects.load_project(project_dir)
    assert result["metadata"]["id"] == "test--project"
    assert result["metadata"]["status"] == "green"


@pytest.mark.unit
def test_load_project_parses_sections(project_dir: Path) -> None:
    result = projects.load_project(project_dir)
    assert "Mission" in result["sections"]
    assert "Test project" in result["sections"]["Mission"]


@pytest.mark.unit
def test_load_project_missing_section_not_present(project_dir: Path) -> None:
    result = projects.load_project(project_dir)
    assert "Actions" not in result["sections"]


# ---------------------------------------------------------------------------
# update_project
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_update_project_writes_actions_section(project_dir: Path) -> None:
    facts = [
        {"type": "action_added", "description": "Deploy API", "owner": "alice", "due": "2026-06-01", "status": "open"},
    ]
    projects.update_project(project_dir, facts)

    content = (project_dir / "project.md").read_text(encoding="utf-8")
    assert "## Actions" in content
    assert "Deploy API" in content
    assert "owner: alice" in content
    assert "due: 2026-06-01" in content


@pytest.mark.integration
def test_update_project_writes_blockers_section(project_dir: Path) -> None:
    facts = [
        {"type": "blocker_added", "description": "Missing credentials", "owner": None, "due": None},
    ]
    projects.update_project(project_dir, facts)

    content = (project_dir / "project.md").read_text(encoding="utf-8")
    assert "## Blockers" in content
    assert "Missing credentials" in content


@pytest.mark.integration
def test_update_project_preserves_mission_section(project_dir: Path) -> None:
    facts = [
        {"type": "action_added", "description": "Ship it", "owner": None, "due": None, "status": "open"},
    ]
    projects.update_project(project_dir, facts)

    content = (project_dir / "project.md").read_text(encoding="utf-8")
    assert "## Mission" in content
    assert "Test project" in content


@pytest.mark.integration
def test_update_project_preserves_frontmatter(project_dir: Path) -> None:
    facts = [{"type": "action_added", "description": "x", "owner": None, "due": None, "status": "open"}]
    projects.update_project(project_dir, facts)

    result = projects.load_project(project_dir)
    assert result["metadata"]["id"] == "test--project"
    assert result["metadata"]["status"] == "green"


@pytest.mark.integration
def test_update_project_owner_null_renders_dash(project_dir: Path) -> None:
    facts = [{"type": "action_added", "description": "Task A", "owner": None, "due": None, "status": "open"}]
    projects.update_project(project_dir, facts)

    content = (project_dir / "project.md").read_text(encoding="utf-8")
    assert "owner: —" in content
    assert "due: —" in content


@pytest.mark.integration
def test_update_project_never_touches_notes_file(project_dir: Path) -> None:
    original_notes = (project_dir / "project.notes.md").read_text(encoding="utf-8")
    facts = [{"type": "action_added", "description": "x", "owner": None, "due": None, "status": "open"}]

    projects.update_project(project_dir, facts)

    assert (project_dir / "project.notes.md").read_text(encoding="utf-8") == original_notes


@pytest.mark.integration
def test_update_project_idempotent_on_same_facts(project_dir: Path) -> None:
    facts = [
        {"type": "action_added", "description": "Do thing", "owner": "bob", "due": "2026-07-01", "status": "open"},
        {"type": "blocker_added", "description": "No access", "owner": None, "due": None},
    ]
    projects.update_project(project_dir, facts)
    content_first = (project_dir / "project.md").read_text(encoding="utf-8")

    projects.update_project(project_dir, facts)
    content_second = (project_dir / "project.md").read_text(encoding="utf-8")

    assert content_first == content_second


@pytest.mark.integration
def test_update_project_empty_facts_leaves_empty_canonical_sections(project_dir: Path) -> None:
    projects.update_project(project_dir, [])
    content = (project_dir / "project.md").read_text(encoding="utf-8")
    assert "## Actions" in content
    assert "## Blockers" in content
    assert "## Mission" in content
