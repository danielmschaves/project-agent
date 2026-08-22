from pathlib import Path

import pytest

PROJECT_ID = "test--project"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A realistic project tree.

    Laid out as <root>/projects/<id> with the vault at <root>/kb, because the
    compiler resolves one from the other (wiki.default_vault_dir). A flat
    tmp_path would put the vault outside the tree.
    """
    root = tmp_path
    pdir = root / "projects" / PROJECT_ID
    for subdir in [
        "raw/_inbox",
        "raw/emails",
        "raw/docs",
        "raw/backlog",
        "raw/meetings",
        "reports",
    ]:
        (pdir / subdir).mkdir(parents=True)

    (pdir / "project.notes.md").write_text(
        f"---\nid: {PROJECT_ID}\nstatus: green\n---\n\n# PM Notes\n", encoding="utf-8"
    )
    (pdir / "raw" / "manifest.json").write_text("{}", encoding="utf-8")

    _make_vault(root)
    (root / "kb" / "projects" / PROJECT_ID).mkdir(parents=True)
    (root / "kb" / "projects" / PROJECT_ID / "index.md").write_text(
        f"---\nid: {PROJECT_ID}\nstatus: green\ntitle: Test Project\ntype: project\n---\n\n"
        "## Mission\n\nTest project.\n",
        encoding="utf-8",
    )
    return pdir


def _make_vault(root: Path) -> Path:
    vault = root / "kb"
    for subdir in ["projects", "people", "clients", "concepts", "outputs", "_registry"]:
        (vault / subdir).mkdir(parents=True, exist_ok=True)
    for registry in ["people", "clients", "concepts"]:
        (vault / "_registry" / f"{registry}.yml").write_text("{}\n", encoding="utf-8")
    return vault


@pytest.fixture
def events_path(tmp_path: Path) -> Path:
    """Empty events log file."""
    path = tmp_path / "events.ndjson"
    path.touch()
    return path


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    """Raw source tree for one project: drop zone plus typed folders."""
    root = tmp_path / "raw"
    for subdir in ["_inbox", "emails", "docs", "backlog", "meetings"]:
        (root / subdir).mkdir(parents=True)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Empty kb/ vault skeleton with its entity registry."""
    return _make_vault(tmp_path)
