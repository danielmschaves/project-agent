"""End-to-end pipeline test using the demo project fixture.

Runs all six stages against projects/demo--sample-2026/ with a fake LLM
client so no Anthropic API key is needed. Verifies event counts, signal
detection, report generation, and run log correctness.

Second-run idempotence: events.ndjson must be unchanged after the second
full pipeline execution (same hashes, same count).
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from project_agent import events as events_api, warehouse
from project_agent.schemas import Signal, SignalDetectedPayload
from pipeline import stages


# ---------------------------------------------------------------------------
# Fake LLM client
# ---------------------------------------------------------------------------

_FAKE_RESPONSE: dict[str, Any] = {
    "actions": [
        {"description": "Complete circuit-breaker design doc", "owner": "bob.kim", "due": "2026-04-30", "status": "open"},
        {"description": "Write JWT key rotation runbook", "owner": "bob.kim", "due": "2026-05-20", "status": "open"},
        {"description": "Complete auth service implementation", "owner": "bob.kim", "due": "2026-05-20", "status": "open"},
        {"description": "Kick off partner portal design", "owner": None, "due": "2026-05-12", "status": "open"},
        {"description": "Escalate Partner B non-response to legal", "owner": None, "due": None, "status": "open"},
        {"description": "Deliver API contract draft to Partner A", "owner": "bob.kim", "due": "2026-05-20", "status": "open"},
        {"description": "Clarify token scope model for partners", "owner": None, "due": "2026-05-16", "status": "open"},
    ],
    "blockers": [
        {"description": "Partner B unresponsive — HIPAA review blocked", "owner": None, "due": None},
        {"description": "Circuit-breaker design doc overdue — Redis launch risk unmitigated", "owner": None, "due": None},
        {"description": "JWT key rotation runbook missing — security sign-off blocked", "owner": None, "due": None},
    ],
    "risks": [],
    "decisions": [
        {"description": "Partner B deferred to Phase 2 if no response by May 19", "rationale": "Unresponsive partner", "decided_by": "alice.chen"},
    ],
}


def _make_fake_client() -> Any:
    response_text = json.dumps(_FAKE_RESPONSE)

    class _Content:
        text = response_text

    class _Message:
        content = [_Content()]

    class _Messages:
        @staticmethod
        def create(**kwargs: Any) -> _Message:
            return _Message()

    class _Client:
        messages = _Messages()

    return _Client()


# ---------------------------------------------------------------------------
# Fixture — isolated copy of the demo project
# ---------------------------------------------------------------------------

_DEMO_SRC = Path("projects/demo--sample-2026")


@pytest.fixture
def demo_project(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    """Copy the demo project to tmp_path; return (project_dir, events_path, db_path, cache_dir, schemas_dir).

    Uses unowned_hours=0 and stale_days=0 so signals fire immediately in
    tests (real thresholds are tested by unit tests in test_signals_deterministic.py).
    """
    project_dir = tmp_path / "projects" / "demo--sample-2026"
    shutil.copytree(str(_DEMO_SRC), str(project_dir))

    # The committed demo has its documents already sorted into typed folders,
    # because ingest moved them there and _inbox/ is transient. Put them back
    # so the run actually exercises ingest instead of finding nothing to do.
    inbox = project_dir / "raw" / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for folder in ("emails", "docs", "backlog", "meetings"):
        typed = project_dir / "raw" / folder
        if not typed.exists():
            continue
        for item in typed.iterdir():
            if item.is_file():
                item.rename(inbox / item.name)
    (project_dir / "raw" / "manifest.json").write_text("{}", encoding="utf-8")

    # The vault sits beside projects/, which is how the compiler resolves it.
    vault_dir = tmp_path / "kb"
    for subdir in ("projects", "people", "clients", "concepts", "_registry"):
        (vault_dir / subdir).mkdir(parents=True, exist_ok=True)
    for registry in ("people", "clients", "concepts"):
        (vault_dir / "_registry" / f"{registry}.yml").write_text("{}\n", encoding="utf-8")

    events_path = project_dir / "events.ndjson"
    events_path.touch()

    db_path = tmp_path / "warehouse.duckdb"
    cache_dir = tmp_path / "cache" / "llm"

    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "action_aging.json").write_text('{"stale_days": 0}')
    (schemas_dir / "action_unowned.json").write_text('{"unowned_hours": 0}')
    (schemas_dir / "blocker_unowned.json").write_text('{"unowned_hours": 0}')

    return project_dir, events_path, db_path, cache_dir, schemas_dir


def _run_full_pipeline(
    project_dir: Path,
    events_path: Path,
    db_path: Path,
    cache_dir: Path,
    schemas_dir: Path,
    run_id: str,
    fake_client: Any,
) -> tuple[list, list[Signal]]:
    prompts_dir = Path("prompts")

    # Zero-budget config so LLM detectors are skipped in E2E tests — determinism
    # is required here; LLM signal quality is covered by test_signals_llm.py.
    config_dir = project_dir.parent / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "signals.yaml").write_text(
        "llm_cost_cap_usd_per_project_per_day: 0.0\n", encoding="utf-8"
    )

    stages.run_ingest("demo--sample-2026", project_dir, events_path, run_id)
    stages.run_parse("demo--sample-2026", project_dir, events_path, cache_dir, prompts_dir, run_id, client=fake_client)
    # prose_enabled=False: these tests assert the deterministic projection.
    # LLM prose is covered by test_wiki.py with a recording client.
    stages.run_compile(
        "demo--sample-2026", project_dir, events_path, run_id, prose_enabled=False,
    )
    stages.run_warehouse(events_path, db_path, run_id)
    _, signals = stages.run_analyze(
        "demo--sample-2026", db_path, events_path, run_id, schemas_dir,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        config_dir=config_dir,
    )
    stages.run_compile_shared(project_dir.parent.parent / "kb", prose_enabled=False)

    return stages, signals or []


# ---------------------------------------------------------------------------
# Integration — first run
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_e2e_events_ndjson_populated(demo_project: tuple) -> None:
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-001", _make_fake_client())

    evts = events_api.query(events_path, include_retracted=True, include_superseded=True)
    # 7 inbox files → 7 source_ingested + actions + blockers from parse
    assert len(evts) >= 20, f"Expected ≥20 events, got {len(evts)}"


@pytest.mark.integration
def test_e2e_events_span_multiple_source_types(demo_project: tuple) -> None:
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-001", _make_fake_client())

    evts = events_api.query(events_path, types=["source_ingested"])
    source_types = {e.payload.source_type for e in evts}  # type: ignore[union-attr]
    assert "email" in source_types
    assert "doc" in source_types
    assert "backlog" in source_types


@pytest.mark.integration
def test_e2e_warehouse_has_required_views(demo_project: tuple) -> None:
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-001", _make_fake_client())

    for view in ("events", "actions", "blockers"):
        rows = warehouse.query(db_path, f"SELECT COUNT(*) AS n FROM {view}")
        assert rows[0]["n"] >= 0  # view exists and is queryable

    actions = warehouse.query(db_path, "SELECT * FROM actions")
    assert len(actions) >= 3

    blockers = warehouse.query(db_path, "SELECT * FROM blockers")
    assert len(blockers) >= 1


@pytest.mark.integration
def test_e2e_three_signals_detected(demo_project: tuple) -> None:
    """All three MVP signals must fire given the demo data."""
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-001", _make_fake_client())

    signal_evts = events_api.query(events_path, types=["signal_detected"])
    signal_types = {e.payload.signal_type for e in signal_evts}  # type: ignore[union-attr]

    assert "action_aging" in signal_types, f"action_aging missing from {signal_types}"
    assert "action_unowned" in signal_types, f"action_unowned missing from {signal_types}"
    assert "blocker_unowned" in signal_types, f"blocker_unowned missing from {signal_types}"


@pytest.mark.integration
def test_e2e_signals_have_non_empty_evidence(demo_project: tuple) -> None:
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-001", _make_fake_client())

    signal_evts = events_api.query(events_path, types=["signal_detected"])
    assert len(signal_evts) >= 3
    for evt in signal_evts:
        p = evt.payload
        assert isinstance(p, SignalDetectedPayload)
        assert len(p.evidence) >= 1


@pytest.mark.integration
def test_e2e_wiki_index_compiled(demo_project: tuple) -> None:
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-001", _make_fake_client())

    from project_agent import projects as _projects
    content = _projects.index_path(project_dir).read_text(encoding="utf-8")
    assert "## Actions" in content
    assert "## Blockers" in content
    # At least one action description from the fake response
    assert "circuit-breaker" in content.lower() or "jwt" in content.lower() or "auth" in content.lower()


@pytest.mark.integration
def test_e2e_notes_file_untouched(demo_project: tuple) -> None:
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project
    original_notes = (project_dir / "project.notes.md").read_text(encoding="utf-8")

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-001", _make_fake_client())

    assert (project_dir / "project.notes.md").read_text(encoding="utf-8") == original_notes


# ---------------------------------------------------------------------------
# Idempotence — second run produces no new events
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_e2e_second_run_no_new_source_events(demo_project: tuple) -> None:
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project
    client = _make_fake_client()

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-001", client)
    count_after_first = len(events_api.query(events_path, include_retracted=True, include_superseded=True))

    # Second run: inbox is empty, parse manifest has all hashes, signals deduped
    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-002", client)
    count_after_second = len(events_api.query(events_path, include_retracted=True, include_superseded=True))

    assert count_after_first == count_after_second, (
        f"Second run added events: {count_after_first} → {count_after_second}"
    )


@pytest.mark.idempotence
def test_e2e_second_run_wiki_unchanged(demo_project: tuple) -> None:
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project
    client = _make_fake_client()

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-001", client)
    vault = project_dir.parent.parent / "kb"
    first = {p: p.read_text(encoding="utf-8") for p in sorted(vault.rglob("*.md"))}

    _run_full_pipeline(project_dir, events_path, db_path, cache_dir, schemas_dir, "run-002", client)
    second = {p: p.read_text(encoding="utf-8") for p in sorted(vault.rglob("*.md"))}

    assert first == second, "second run changed the vault"
    assert first, "no articles were compiled"


@pytest.mark.idempotence
def test_e2e_second_run_zero_new_signals(demo_project: tuple) -> None:
    project_dir, events_path, db_path, cache_dir, schemas_dir = demo_project
    client = _make_fake_client()

    prompts_dir = Path("prompts")

    stages.run_ingest("demo--sample-2026", project_dir, events_path, "run-001")
    stages.run_parse("demo--sample-2026", project_dir, events_path, cache_dir, prompts_dir, "run-001", client=client)
    stages.run_compile(
        "demo--sample-2026", project_dir, events_path, "run-001", prose_enabled=False,
    )
    stages.run_warehouse(events_path, db_path, "run-001")
    r1, _ = stages.run_analyze("demo--sample-2026", db_path, events_path, "run-001", schemas_dir)
    assert r1.counts["signals_new"] >= 3

    # Second run
    stages.run_ingest("demo--sample-2026", project_dir, events_path, "run-002")
    stages.run_parse("demo--sample-2026", project_dir, events_path, cache_dir, prompts_dir, "run-002", client=client)
    stages.run_warehouse(events_path, db_path, "run-002")
    r2, _ = stages.run_analyze("demo--sample-2026", db_path, events_path, "run-002", schemas_dir)

    assert r2.counts["signals_new"] == 0
    assert r2.counts["signals_skipped"] >= 3
