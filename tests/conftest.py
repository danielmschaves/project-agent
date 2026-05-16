from pathlib import Path

import pytest


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Minimal project directory tree for tests."""
    for subdir in [
        "sources/_inbox",
        "sources/emails",
        "sources/docs",
        "sources/backlog",
        "sources/meetings",
        "derived",
        "reports",
    ]:
        (tmp_path / subdir).mkdir(parents=True)

    (tmp_path / "project.md").write_text(
        "---\nid: test--project\nstatus: green\n---\n\n## Mission\nTest project.\n"
    )
    (tmp_path / "project.notes.md").write_text("# PM Notes\n")
    (tmp_path / "sources" / "manifest.json").write_text("{}")
    return tmp_path


@pytest.fixture
def events_path(tmp_path: Path) -> Path:
    """Empty events log file."""
    path = tmp_path / "events.ndjson"
    path.touch()
    return path
