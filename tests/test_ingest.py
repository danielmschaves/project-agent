"""Tests for project_agent.ingest.inbox and the ingest pipeline stage."""
import json
from pathlib import Path

import pytest

from project_agent import events as events_api
from project_agent.ingest.inbox import IngestResult, _classify, scan_inbox
from pipeline.stages import run_ingest


# ---------------------------------------------------------------------------
# Unit tests — pure classification logic
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("thread.eml", "email"),
        ("notes.msg", "email"),
        ("spec.md", "doc"),
        ("report.txt", "doc"),
        ("deck.pdf", "doc"),
        ("document.docx", "doc"),
        ("backlog.json", "backlog"),
        ("export.csv", "backlog"),
        ("unknown.xyz", "doc"),  # default
    ],
)
def test_classify_by_extension(filename: str, expected: str) -> None:
    assert _classify(filename) == expected


# ---------------------------------------------------------------------------
# Integration tests — scan_inbox with real temp directories
# ---------------------------------------------------------------------------

def _seed_inbox(project_dir: Path, files: dict[str, bytes]) -> None:
    inbox = project_dir / "sources" / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (inbox / name).write_bytes(content)


@pytest.mark.integration
def test_scan_inbox_moves_files_and_returns_results(project_dir: Path) -> None:
    _seed_inbox(project_dir, {"meeting.eml": b"email body", "spec.md": b"# Spec"})

    results = scan_inbox(project_dir)

    assert len(results) == 2
    types = {r.source_type for r in results}
    assert types == {"email", "doc"}

    # Files moved out of _inbox
    assert not list((project_dir / "sources" / "_inbox").iterdir())

    # Files landed in typed folders
    assert (project_dir / "sources" / "emails" / "meeting.eml").exists()
    assert (project_dir / "sources" / "docs" / "spec.md").exists()

    # Manifest updated
    manifest = json.loads((project_dir / "sources" / "manifest.json").read_text())
    assert len(manifest) == 2
    for result in results:
        assert result.source_hash in manifest


@pytest.mark.integration
def test_scan_inbox_sets_correct_metadata(project_dir: Path) -> None:
    content = b"hello world"
    _seed_inbox(project_dir, {"notes.txt": content})

    results = scan_inbox(project_dir)

    assert len(results) == 1
    r = results[0]
    assert r.filename == "notes.txt"
    assert r.source_type == "doc"
    assert r.size_bytes == len(content)
    assert r.source_hash.startswith("sha256:")
    assert r.source_ref == "sources/docs/notes.txt"
    assert r.is_new is True


@pytest.mark.integration
def test_scan_inbox_empty_returns_empty(project_dir: Path) -> None:
    results = scan_inbox(project_dir)
    assert results == []


@pytest.mark.integration
def test_scan_inbox_skips_missing_inbox(tmp_path: Path) -> None:
    # project_dir with no _inbox directory at all
    (tmp_path / "sources").mkdir()
    results = scan_inbox(tmp_path)
    assert results == []


# ---------------------------------------------------------------------------
# Idempotence — second scan produces zero new results
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_scan_inbox_idempotent(project_dir: Path) -> None:
    _seed_inbox(project_dir, {"doc.md": b"# Doc"})

    first = scan_inbox(project_dir)
    assert len(first) == 1

    # _inbox is now empty; re-seeding would add a *new* file.
    # Instead, just run again on the already-processed state.
    second = scan_inbox(project_dir)
    assert second == []


# ---------------------------------------------------------------------------
# Pipeline stage integration
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_ingest_emits_source_ingested_events(project_dir: Path, events_path: Path) -> None:
    _seed_inbox(project_dir, {"email.eml": b"From: alice@example.com"})

    result = run_ingest(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        run_id="run-001",
    )

    assert result.status == "ok"
    assert result.counts["sources_new"] == 1
    assert result.counts["sources_seen"] == 1
    assert result.counts["errors"] == 0

    evts = events_api.query(events_path)
    assert len(evts) == 1
    assert evts[0].type == "source_ingested"
    assert evts[0].project_id == "test--project"
    assert evts[0].run_id == "run-001"
    assert evts[0].hash.startswith("sha256:")


@pytest.mark.idempotence
def test_run_ingest_idempotent(project_dir: Path, events_path: Path) -> None:
    _seed_inbox(project_dir, {"doc.md": b"# Hello"})

    first = run_ingest("test--project", project_dir, events_path, "run-001")
    assert first.counts["sources_new"] == 1

    # Second run — inbox is empty, manifest has the hash
    second = run_ingest("test--project", project_dir, events_path, "run-002")
    assert second.counts["sources_new"] == 0
    assert second.counts["sources_seen"] == 0

    # events.ndjson unchanged after second run
    evts = events_api.query(events_path)
    assert len(evts) == 1
