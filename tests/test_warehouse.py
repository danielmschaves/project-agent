"""Tests for project_agent.warehouse (load, query, views)."""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from project_agent import events as events_api, warehouse
from project_agent.schemas import (
    ActionAddedPayload,
    BlockerAddedPayload,
    DeterministicActor,
    Event,
    LLMActor,
    SourceIngestedPayload,
)
from pipeline.stages import run_warehouse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "warehouse.duckdb"


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------

def _actor() -> DeterministicActor:
    return DeterministicActor(kind="deterministic", detector="inbox")


def _llm_actor() -> LLMActor:
    return LLMActor(kind="llm", model="claude-sonnet-4-6", prompt="extract-context@1")


def _source_event(
    source_hash: str = "sha256:aaa",
    retracted: bool = False,
    superseded_by: str | None = None,
) -> Event:
    payload = SourceIngestedPayload(
        type="source_ingested", filename="doc.md", source_type="doc", size_bytes=10
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime.now(tz=timezone.utc),
        run_id="run-001",
        project_id="test--project",
        type="source_ingested",
        actor=_actor(),
        source_ref="sources/docs/doc.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
        retracted=retracted,
        superseded_by=superseded_by,
    )


def _action_event(
    description: str = "Do the thing",
    owner: str | None = "alice",
    due: str | None = "2026-06-01",
    source_hash: str = "sha256:bbb",
    retracted: bool = False,
    superseded_by: str | None = None,
) -> Event:
    payload = ActionAddedPayload(
        type="action_added",
        description=description,
        owner=owner,
        due=due,
        status="open",
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime.now(tz=timezone.utc),
        run_id="run-001",
        project_id="test--project",
        type="action_added",
        actor=_llm_actor(),
        source_ref="sources/docs/doc.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
        retracted=retracted,
        superseded_by=superseded_by,
    )


def _blocker_event(
    description: str = "No access",
    owner: str | None = None,
    source_hash: str = "sha256:ccc",
    retracted: bool = False,
    superseded_by: str | None = None,
) -> Event:
    payload = BlockerAddedPayload(
        type="blocker_added",
        description=description,
        owner=owner,
        due=None,
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime.now(tz=timezone.utc),
        run_id="run-001",
        project_id="test--project",
        type="blocker_added",
        actor=_llm_actor(),
        source_ref="sources/docs/doc.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
        retracted=retracted,
        superseded_by=superseded_by,
    )


# ---------------------------------------------------------------------------
# Unit tests — view filter behaviour
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_events_view_excludes_retracted(events_path: Path, db_path: Path) -> None:
    events_api.append(_source_event("sha256:1"), events_path)
    events_api.append(_source_event("sha256:2", retracted=True), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM events")
    assert len(rows) == 1


@pytest.mark.unit
def test_events_full_includes_retracted(events_path: Path, db_path: Path) -> None:
    events_api.append(_source_event("sha256:1"), events_path)
    events_api.append(_source_event("sha256:2", retracted=True), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM events_full")
    assert len(rows) == 2


@pytest.mark.unit
def test_events_view_excludes_superseded(events_path: Path, db_path: Path) -> None:
    old_id = str(uuid.uuid4())
    events_api.append(_source_event("sha256:1", superseded_by=old_id), events_path)
    events_api.append(_source_event("sha256:2"), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM events")
    assert len(rows) == 1


@pytest.mark.unit
def test_events_full_includes_superseded(events_path: Path, db_path: Path) -> None:
    old_id = str(uuid.uuid4())
    events_api.append(_source_event("sha256:1", superseded_by=old_id), events_path)
    events_api.append(_source_event("sha256:2"), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM events_full")
    assert len(rows) == 2


@pytest.mark.unit
def test_actions_view_excludes_retracted(events_path: Path, db_path: Path) -> None:
    events_api.append(_action_event(source_hash="sha256:1"), events_path)
    events_api.append(_action_event(source_hash="sha256:2", retracted=True), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM actions")
    assert len(rows) == 1


@pytest.mark.unit
def test_blockers_view_excludes_retracted(events_path: Path, db_path: Path) -> None:
    events_api.append(_blocker_event(source_hash="sha256:1"), events_path)
    events_api.append(_blocker_event(source_hash="sha256:2", retracted=True), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM blockers")
    assert len(rows) == 1


@pytest.mark.unit
def test_all_views_expose_event_id(events_path: Path, db_path: Path) -> None:
    events_api.append(_source_event("sha256:src"), events_path)
    events_api.append(_action_event(source_hash="sha256:act"), events_path)
    events_api.append(_blocker_event(source_hash="sha256:blk"), events_path)

    warehouse.load(events_path, db_path)

    for view in ("events", "events_full", "actions", "actions_full", "blockers", "blockers_full"):
        rows = warehouse.query(db_path, f"SELECT * FROM {view}")
        for row in rows:
            assert "event_id" in row
            assert row["event_id"] is not None


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_load_empty_events_file(events_path: Path, db_path: Path) -> None:
    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT COUNT(*) AS n FROM events_raw")
    assert rows[0]["n"] == 0


@pytest.mark.integration
def test_load_populates_actions_view_columns(events_path: Path, db_path: Path) -> None:
    events_api.append(
        _action_event(description="Deploy API", owner="alice", due="2026-06-01"),
        events_path,
    )
    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM actions")
    assert len(rows) == 1
    assert rows[0]["description"] == "Deploy API"
    assert rows[0]["owner"] == "alice"
    assert rows[0]["due"] == "2026-06-01"
    assert rows[0]["status"] == "open"


@pytest.mark.integration
def test_load_populates_blockers_view_columns(events_path: Path, db_path: Path) -> None:
    events_api.append(
        _blocker_event(description="No prod access", owner="bob"),
        events_path,
    )
    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM blockers")
    assert len(rows) == 1
    assert rows[0]["description"] == "No prod access"
    assert rows[0]["owner"] == "bob"


@pytest.mark.integration
def test_query_raw_sql(events_path: Path, db_path: Path) -> None:
    events_api.append(_action_event(source_hash="sha256:q1"), events_path)
    events_api.append(_blocker_event(source_hash="sha256:q2"), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT type, COUNT(*) AS n FROM events_raw GROUP BY type ORDER BY type")
    types = {r["type"]: r["n"] for r in rows}
    assert types["action_added"] == 1
    assert types["blocker_added"] == 1


@pytest.mark.integration
def test_run_warehouse_stage(events_path: Path, db_path: Path) -> None:
    events_api.append(_source_event("sha256:s1"), events_path)
    events_api.append(_action_event(source_hash="sha256:a1"), events_path)

    result = run_warehouse(events_path=events_path, db_path=db_path, run_id="run-001")

    assert result.status == "ok"
    assert result.counts["events_loaded"] == 2
    assert result.counts["errors"] == 0


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_load_twice_same_row_count(events_path: Path, db_path: Path) -> None:
    events_api.append(_action_event(source_hash="sha256:x1"), events_path)
    events_api.append(_blocker_event(source_hash="sha256:x2"), events_path)

    warehouse.load(events_path, db_path)
    count_1 = warehouse.query(db_path, "SELECT COUNT(*) AS n FROM events_raw")[0]["n"]

    warehouse.load(events_path, db_path)
    count_2 = warehouse.query(db_path, "SELECT COUNT(*) AS n FROM events_raw")[0]["n"]

    assert count_1 == count_2 == 2


@pytest.mark.idempotence
def test_run_warehouse_idempotent(events_path: Path, db_path: Path) -> None:
    events_api.append(_source_event("sha256:y1"), events_path)

    r1 = run_warehouse(events_path=events_path, db_path=db_path, run_id="run-001")
    r2 = run_warehouse(events_path=events_path, db_path=db_path, run_id="run-002")

    assert r1.status == r2.status == "ok"
    assert r1.counts["events_loaded"] == r2.counts["events_loaded"]
