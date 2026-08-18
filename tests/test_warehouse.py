"""Tests for project_agent.warehouse (load, query, views)."""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from project_agent import events as events_api, warehouse
from project_agent.schemas import (
    ActionAddedPayload,
    BlockerAddedPayload,
    DecisionMadePayload,
    DeterministicActor,
    Event,
    LLMActor,
    MilestoneAddedPayload,
    RiskAddedPayload,
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


def _risk_event(
    description: str = "Scope creep",
    severity: str = "high",
    owner: str | None = "alice",
    status: str = "open",
    source_hash: str = "sha256:rrr",
    retracted: bool = False,
    superseded_by: str | None = None,
) -> Event:
    payload = RiskAddedPayload(
        type="risk_added",
        description=description,
        severity=severity,  # type: ignore[arg-type]
        owner=owner,
        status=status,
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime.now(tz=timezone.utc),
        run_id="run-001",
        project_id="test--project",
        type="risk_added",
        actor=_llm_actor(),
        source_ref="sources/docs/doc.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
        retracted=retracted,
        superseded_by=superseded_by,
    )


def _decision_event(
    description: str = "Use PostgreSQL",
    rationale: str | None = "Team familiarity",
    decided_by: str | None = "alice",
    source_hash: str = "sha256:ddd",
    retracted: bool = False,
    superseded_by: str | None = None,
) -> Event:
    payload = DecisionMadePayload(
        type="decision_made",
        description=description,
        rationale=rationale,
        decided_by=decided_by,
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime.now(tz=timezone.utc),
        run_id="run-001",
        project_id="test--project",
        type="decision_made",
        actor=_llm_actor(),
        source_ref="sources/docs/doc.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
        retracted=retracted,
        superseded_by=superseded_by,
    )


def _milestone_event(
    description: str = "MVP launch",
    target_date: str | None = "2026-07-01",
    status: str = "open",
    owner: str | None = "bob",
    source_hash: str = "sha256:mmm",
    retracted: bool = False,
    superseded_by: str | None = None,
) -> Event:
    payload = MilestoneAddedPayload(
        type="milestone_added",
        description=description,
        target_date=target_date,
        status=status,
        owner=owner,
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime.now(tz=timezone.utc),
        run_id="run-001",
        project_id="test--project",
        type="milestone_added",
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
def test_risks_view_excludes_retracted(events_path: Path, db_path: Path) -> None:
    events_api.append(_risk_event(source_hash="sha256:r1"), events_path)
    events_api.append(_risk_event(source_hash="sha256:r2", retracted=True), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM risks")
    assert len(rows) == 1


@pytest.mark.unit
def test_decisions_view_excludes_retracted(events_path: Path, db_path: Path) -> None:
    events_api.append(_decision_event(source_hash="sha256:d1"), events_path)
    events_api.append(_decision_event(source_hash="sha256:d2", retracted=True), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM decisions")
    assert len(rows) == 1


@pytest.mark.unit
def test_milestones_view_excludes_retracted(events_path: Path, db_path: Path) -> None:
    events_api.append(_milestone_event(source_hash="sha256:m1"), events_path)
    events_api.append(_milestone_event(source_hash="sha256:m2", retracted=True), events_path)

    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM milestones")
    assert len(rows) == 1


@pytest.mark.unit
def test_all_views_expose_event_id(events_path: Path, db_path: Path) -> None:
    events_api.append(_source_event("sha256:src"), events_path)
    events_api.append(_action_event(source_hash="sha256:act"), events_path)
    events_api.append(_blocker_event(source_hash="sha256:blk"), events_path)
    events_api.append(_risk_event(source_hash="sha256:rsk"), events_path)
    events_api.append(_decision_event(source_hash="sha256:dec"), events_path)
    events_api.append(_milestone_event(source_hash="sha256:mls"), events_path)

    warehouse.load(events_path, db_path)

    all_views = (
        "events", "events_full",
        "actions", "actions_full",
        "blockers", "blockers_full",
        "risks", "risks_full",
        "decisions", "decisions_full",
        "milestones", "milestones_full",
    )
    for view in all_views:
        rows = warehouse.query(db_path, f"SELECT * FROM {view}")
        for row in rows:
            assert "event_id" in row, f"event_id missing from {view}"
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
def test_load_populates_risks_view_columns(events_path: Path, db_path: Path) -> None:
    events_api.append(
        _risk_event(description="Vendor delay", severity="high", owner="alice", status="open"),
        events_path,
    )
    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM risks")
    assert len(rows) == 1
    assert rows[0]["description"] == "Vendor delay"
    assert rows[0]["severity"] == "high"
    assert rows[0]["owner"] == "alice"
    assert rows[0]["status"] == "open"


@pytest.mark.integration
def test_load_populates_decisions_view_columns(events_path: Path, db_path: Path) -> None:
    events_api.append(
        _decision_event(description="Use Redis", rationale="Speed", decided_by="bob"),
        events_path,
    )
    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM decisions")
    assert len(rows) == 1
    assert rows[0]["description"] == "Use Redis"
    assert rows[0]["rationale"] == "Speed"
    assert rows[0]["decided_by"] == "bob"


@pytest.mark.integration
def test_load_populates_milestones_view_columns(events_path: Path, db_path: Path) -> None:
    events_api.append(
        _milestone_event(description="Beta release", target_date="2026-09-01", status="open", owner="carol"),
        events_path,
    )
    warehouse.load(events_path, db_path)

    rows = warehouse.query(db_path, "SELECT * FROM milestones")
    assert len(rows) == 1
    assert rows[0]["description"] == "Beta release"
    assert rows[0]["target_date"] == "2026-09-01"
    assert rows[0]["status"] == "open"
    assert rows[0]["owner"] == "carol"


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


# ---------------------------------------------------------------------------
# Multi-project load — one warehouse holds the whole portfolio
# ---------------------------------------------------------------------------

def _project_event(project_id: str, source_hash: str) -> Event:
    payload = SourceIngestedPayload(
        type="source_ingested", filename="doc.md", source_type="doc", size_bytes=10
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime.now(tz=timezone.utc),
        run_id="run-001",
        project_id=project_id,
        type="source_ingested",
        actor=_actor(),
        source_ref="raw/docs/doc.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
    )


@pytest.mark.integration
def test_load_accepts_multiple_event_logs(tmp_path: Path, db_path: Path) -> None:
    log_a = tmp_path / "a.ndjson"
    log_b = tmp_path / "b.ndjson"
    events_api.append(_project_event("acme--one", "sha256:a1"), log_a)
    events_api.append(_project_event("acme--one", "sha256:a2"), log_a)
    events_api.append(_project_event("globex--two", "sha256:b1"), log_b)

    warehouse.load([log_a, log_b], db_path)

    rows = warehouse.query(
        db_path,
        "SELECT project_id, COUNT(*) AS n FROM events GROUP BY project_id ORDER BY project_id",
    )
    assert rows == [
        {"project_id": "acme--one", "n": 2},
        {"project_id": "globex--two", "n": 1},
    ]


@pytest.mark.integration
def test_load_still_accepts_a_single_path(tmp_path: Path, db_path: Path) -> None:
    log = tmp_path / "a.ndjson"
    events_api.append(_project_event("acme--one", "sha256:a1"), log)

    warehouse.load(log, db_path)

    assert warehouse.query(db_path, "SELECT COUNT(*) AS n FROM events")[0]["n"] == 1


@pytest.mark.idempotence
def test_multi_project_load_is_idempotent(tmp_path: Path, db_path: Path) -> None:
    log_a = tmp_path / "a.ndjson"
    log_b = tmp_path / "b.ndjson"
    events_api.append(_project_event("acme--one", "sha256:a1"), log_a)
    events_api.append(_project_event("globex--two", "sha256:b1"), log_b)

    warehouse.load([log_a, log_b], db_path)
    first = warehouse.query(db_path, "SELECT COUNT(*) AS n FROM events_raw")[0]["n"]
    warehouse.load([log_a, log_b], db_path)
    second = warehouse.query(db_path, "SELECT COUNT(*) AS n FROM events_raw")[0]["n"]

    assert first == second == 2


@pytest.mark.integration
def test_views_hide_events_retracted_by_a_later_event(
    events_path: Path, db_path: Path
) -> None:
    """warehouse views and events.query() must agree on what a retraction hides."""
    keep = _source_event("sha256:keep")
    drop = _source_event("sha256:drop")
    events_api.append(keep, events_path)
    events_api.append(drop, events_path)
    events_api.retract(
        events_path,
        event_id=drop.event_id,
        reason="misparsed",
        run_id="run-002",
        project_id="test--project",
        actor=_actor(),
    )

    warehouse.load(events_path, db_path)

    visible = {
        r["event_id"]
        for r in warehouse.query(
            db_path, "SELECT event_id FROM events WHERE type = 'source_ingested'"
        )
    }
    assert visible == {keep.event_id}

    from_api = {
        e.event_id for e in events_api.query(events_path, types=["source_ingested"])
    }
    assert visible == from_api

    full = warehouse.query(
        db_path,
        f"SELECT retracted, retracted_reason FROM events_full WHERE event_id = '{drop.event_id}'",
    )
    assert full[0]["retracted"] is True
    assert full[0]["retracted_reason"] == "misparsed"


@pytest.mark.integration
def test_views_hide_events_superseded_by_a_later_event(
    events_path: Path, db_path: Path
) -> None:
    old = _source_event("sha256:v1")
    new = _source_event("sha256:v2")
    new.supersedes = [old.event_id]
    events_api.append(old, events_path)
    events_api.append(new, events_path)

    warehouse.load(events_path, db_path)

    visible = {r["event_id"] for r in warehouse.query(db_path, "SELECT event_id FROM events")}
    assert visible == {new.event_id}

    linked = warehouse.query(
        db_path, f"SELECT superseded_by FROM events_full WHERE event_id = '{old.event_id}'"
    )
    assert linked[0]["superseded_by"] == new.event_id
