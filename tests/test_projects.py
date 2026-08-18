"""Tests for project_agent.projects — reading state out of the compiled wiki."""
from pathlib import Path

import pytest

from project_agent import projects


# ---------------------------------------------------------------------------
# index_path / load_project
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_index_path_resolves_vault_from_project_dir(project_dir: Path) -> None:
    path = projects.index_path(project_dir)
    assert path == project_dir.parent.parent / "kb" / "projects" / "test--project" / "index.md"


@pytest.mark.unit
def test_index_path_honours_explicit_vault(project_dir: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "other-vault"
    assert projects.index_path(project_dir, elsewhere).is_relative_to(elsewhere)


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
    assert "Actions" not in projects.load_project(project_dir)["sections"]


# ---------------------------------------------------------------------------
# PM-owned metadata — project.notes.md front-matter
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_notes_metadata_reads_declared_fields(project_dir: Path) -> None:
    (project_dir / "project.notes.md").write_text(
        "---\nstatus: amber\nsponsor: Dana\nphase: build\n---\n\n# Notes\n", encoding="utf-8"
    )
    meta = projects.load_notes_metadata(project_dir)
    assert meta["status"] == "amber"
    assert meta["sponsor"] == "Dana"
    assert meta["phase"] == "build"


@pytest.mark.unit
def test_notes_metadata_defaults_when_front_matter_absent(project_dir: Path) -> None:
    """A project with only free-form notes still compiles, using defaults."""
    (project_dir / "project.notes.md").write_text("# Just prose\n", encoding="utf-8")
    meta = projects.load_notes_metadata(project_dir)
    assert meta == {"id": "test--project", "client": "test", "status": "green"}


@pytest.mark.unit
def test_notes_metadata_defaults_when_file_missing(project_dir: Path) -> None:
    (project_dir / "project.notes.md").unlink()
    assert projects.load_notes_metadata(project_dir)["id"] == "test--project"


@pytest.mark.unit
def test_notes_metadata_derives_client_from_project_id(project_dir: Path) -> None:
    assert projects.load_notes_metadata(project_dir)["client"] == "test"


@pytest.mark.unit
def test_notes_body_strips_front_matter(project_dir: Path) -> None:
    (project_dir / "project.notes.md").write_text(
        "---\nstatus: green\n---\n\n# PM Notes\n\nRedis worries me.\n", encoding="utf-8"
    )
    body = projects.notes_body(project_dir)
    assert "Redis worries me" in body
    assert "status: green" not in body


@pytest.mark.unit
def test_notes_body_empty_when_file_missing(project_dir: Path) -> None:
    (project_dir / "project.notes.md").unlink()
    assert projects.notes_body(project_dir) == ""
