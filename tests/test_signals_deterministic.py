"""Tests for project_agent.signals.deterministic and run_analyze."""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from project_agent import events as events_api, warehouse
from project_agent.schemas import (
    ActionAddedPayload,
    BlockerAddedPayload,
    DeterministicActor,
    Event,
    LLMActor,
)
from project_agent.signals import deterministic
from pipeline.stages import run_analyze


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "warehouse.duckdb"


@pytest.fixture
def schemas_dir(tmp_path: Path) -> Path:
    d = tmp_path / "schemas"
    d.mkdir()
    (d / "action_aging.json").write_text('{"stale_days": 14}')
    (d / "action_unowned.json").write_text('{"unowned_hours": 48}')
    (d / "blocker_unowned.json").write_text('{"unowned_hours": 48}')
    return d


# ---------------------------------------------------------------------------
# Event builders with controllable timestamps
# ---------------------------------------------------------------------------

def _llm_actor() -> LLMActor:
    return LLMActor(kind="llm", model="claude-sonnet-4-6", prompt="extract-context@1")


def _action_event(
    *,
    description: str = "Do thing",
    owner: str | None = "alice",
    due: str | None = None,
    status: str = "open",
    ts: datetime | None = None,
    project_id: str = "test--project",
    source_hash: str | None = None,
) -> Event:
    if ts is None:
        ts = datetime.now(tz=timezone.utc)
    if source_hash is None:
        source_hash = "sha256:" + uuid.uuid4().hex
    payload = ActionAddedPayload(
        type="action_added",
        description=description,
        owner=owner,
        due=due,
        status=status,
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=ts,
        run_id="run-001",
        project_id=project_id,
        type="action_added",
        actor=_llm_actor(),
        source_ref="sources/docs/doc.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
    )


def _blocker_event(
    *,
    description: str = "Blocked",
    owner: str | None = None,
    ts: datetime | None = None,
    project_id: str = "test--project",
    source_hash: str | None = None,
) -> Event:
    if ts is None:
        ts = datetime.now(tz=timezone.utc)
    if source_hash is None:
        source_hash = "sha256:" + uuid.uuid4().hex
    payload = BlockerAddedPayload(
        type="blocker_added",
        description=description,
        owner=owner,
        due=None,
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=ts,
        run_id="run-001",
        project_id=project_id,
        type="blocker_added",
        actor=_llm_actor(),
        source_ref="sources/docs/doc.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
    )


def _load(events_path: Path, db_path: Path) -> None:
    warehouse.load(events_path, db_path)


# ---------------------------------------------------------------------------
# detect_action_aging
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_action_aging_fires_on_past_due(events_path: Path, db_path: Path) -> None:
    events_api.append(
        _action_event(due="2026-01-01", owner="alice"),  # past due
        events_path,
    )
    _load(events_path, db_path)

    signals = deterministic.detect_action_aging(db_path, "test--project", "run-001", {"stale_days": 14})
    assert len(signals) == 1
    assert signals[0].type == "action_aging"
    assert len(signals[0].evidence) == 1


@pytest.mark.unit
def test_action_aging_fires_on_stale(events_path: Path, db_path: Path) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(days=20)
    events_api.append(
        _action_event(ts=old_ts, owner="alice"),  # no due date, but 20 days old > stale_days=14
        events_path,
    )
    _load(events_path, db_path)

    signals = deterministic.detect_action_aging(db_path, "test--project", "run-001", {"stale_days": 14})
    assert len(signals) == 1
    assert len(signals[0].evidence) == 1


@pytest.mark.unit
def test_action_aging_no_signal_on_fresh_action(events_path: Path, db_path: Path) -> None:
    events_api.append(
        _action_event(due="2030-01-01", owner="alice"),  # far future due date
        events_path,
    )
    _load(events_path, db_path)

    signals = deterministic.detect_action_aging(db_path, "test--project", "run-001", {"stale_days": 14})
    assert signals == []


@pytest.mark.unit
def test_action_aging_skips_done_actions(events_path: Path, db_path: Path) -> None:
    events_api.append(
        _action_event(due="2026-01-01", status="done", owner="alice"),
        events_path,
    )
    _load(events_path, db_path)

    signals = deterministic.detect_action_aging(db_path, "test--project", "run-001", {"stale_days": 14})
    assert signals == []


@pytest.mark.unit
def test_action_aging_evidence_is_non_empty(events_path: Path, db_path: Path) -> None:
    events_api.append(_action_event(due="2026-01-01"), events_path)
    _load(events_path, db_path)

    signals = deterministic.detect_action_aging(db_path, "test--project", "run-001", {"stale_days": 14})
    assert len(signals) == 1
    assert len(signals[0].evidence) >= 1
    for eid in signals[0].evidence:
        assert isinstance(eid, str) and len(eid) > 0


# ---------------------------------------------------------------------------
# detect_action_unowned
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_action_unowned_fires_on_missing_owner(events_path: Path, db_path: Path) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(hours=72)
    events_api.append(_action_event(owner=None, due="2026-09-01", ts=old_ts), events_path)
    _load(events_path, db_path)

    signals = deterministic.detect_action_unowned(db_path, "test--project", "run-001", {"unowned_hours": 48})
    assert len(signals) == 1
    assert signals[0].type == "action_unowned"
    assert len(signals[0].evidence) == 1


@pytest.mark.unit
def test_action_unowned_fires_on_missing_due(events_path: Path, db_path: Path) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(hours=72)
    events_api.append(_action_event(owner="alice", due=None, ts=old_ts), events_path)
    _load(events_path, db_path)

    signals = deterministic.detect_action_unowned(db_path, "test--project", "run-001", {"unowned_hours": 48})
    assert len(signals) == 1


@pytest.mark.unit
def test_action_unowned_no_signal_within_threshold(events_path: Path, db_path: Path) -> None:
    # Created only 1 hour ago — under the 48h threshold
    recent_ts = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    events_api.append(_action_event(owner=None, ts=recent_ts), events_path)
    _load(events_path, db_path)

    signals = deterministic.detect_action_unowned(db_path, "test--project", "run-001", {"unowned_hours": 48})
    assert signals == []


@pytest.mark.unit
def test_action_unowned_no_signal_when_complete(events_path: Path, db_path: Path) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(hours=72)
    events_api.append(_action_event(owner="alice", due="2026-09-01", status="open", ts=old_ts), events_path)
    _load(events_path, db_path)

    signals = deterministic.detect_action_unowned(db_path, "test--project", "run-001", {"unowned_hours": 48})
    assert signals == []  # both owner and due are set


# ---------------------------------------------------------------------------
# detect_blocker_unowned
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_blocker_unowned_fires_on_missing_owner(events_path: Path, db_path: Path) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(hours=72)
    events_api.append(_blocker_event(owner=None, ts=old_ts), events_path)
    _load(events_path, db_path)

    signals = deterministic.detect_blocker_unowned(db_path, "test--project", "run-001", {"unowned_hours": 48})
    assert len(signals) == 1
    assert signals[0].type == "blocker_unowned"
    assert signals[0].category == "blocker"
    assert len(signals[0].evidence) >= 1


@pytest.mark.unit
def test_blocker_unowned_no_signal_with_owner(events_path: Path, db_path: Path) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(hours=72)
    events_api.append(_blocker_event(owner="bob", ts=old_ts), events_path)
    _load(events_path, db_path)

    signals = deterministic.detect_blocker_unowned(db_path, "test--project", "run-001", {"unowned_hours": 48})
    assert signals == []


@pytest.mark.unit
def test_blocker_unowned_no_signal_within_threshold(events_path: Path, db_path: Path) -> None:
    recent_ts = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    events_api.append(_blocker_event(owner=None, ts=recent_ts), events_path)
    _load(events_path, db_path)

    signals = deterministic.detect_blocker_unowned(db_path, "test--project", "run-001", {"unowned_hours": 48})
    assert signals == []


# ---------------------------------------------------------------------------
# run_analyze — pipeline stage
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_analyze_emits_signal_events(
    events_path: Path, db_path: Path, schemas_dir: Path
) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(days=20)
    events_api.append(_action_event(due="2026-01-01", ts=old_ts), events_path)
    _load(events_path, db_path)

    result, signals = run_analyze("test--project", db_path, events_path, "run-001", schemas_dir)

    assert result.status == "ok"
    assert result.counts["signals_new"] >= 1

    evts = events_api.query(events_path, types=["signal_detected"])
    assert len(evts) >= 1
    assert evts[0].project_id == "test--project"
    assert evts[0].run_id == "run-001"
    assert evts[0].source_hash.startswith("sha256:")


@pytest.mark.integration
def test_run_analyze_signals_have_non_empty_evidence(
    events_path: Path, db_path: Path, schemas_dir: Path
) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(hours=72)
    events_api.append(_action_event(owner=None, ts=old_ts), events_path)
    _load(events_path, db_path)

    _, signals = run_analyze("test--project", db_path, events_path, "run-001", schemas_dir)

    assert len(signals) >= 1
    for s in signals:
        assert len(s.evidence) >= 1


@pytest.mark.integration
def test_run_analyze_no_signals_on_clean_data(
    events_path: Path, db_path: Path, schemas_dir: Path
) -> None:
    # Recent action with owner and far-future due — no signal should fire
    events_api.append(_action_event(owner="alice", due="2030-01-01"), events_path)
    _load(events_path, db_path)

    result, signals = run_analyze("test--project", db_path, events_path, "run-001", schemas_dir)

    assert result.status == "ok"
    assert result.counts["signals_new"] == 0
    assert signals == []


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_run_analyze_idempotent(
    events_path: Path, db_path: Path, schemas_dir: Path
) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(days=20)
    events_api.append(_action_event(due="2026-01-01", ts=old_ts), events_path)
    _load(events_path, db_path)

    r1, _ = run_analyze("test--project", db_path, events_path, "run-001", schemas_dir)
    assert r1.counts["signals_new"] >= 1

    # Reload warehouse (events_path now also has signal_detected events)
    _load(events_path, db_path)
    r2, _ = run_analyze("test--project", db_path, events_path, "run-002", schemas_dir)

    assert r2.counts["signals_new"] == 0
    assert r2.counts["signals_skipped"] >= 1

    # Total signal events unchanged
    evts = events_api.query(events_path, types=["signal_detected"])
    assert len(evts) == r1.counts["signals_new"]


@pytest.mark.idempotence
def test_all_three_signals_fire_and_are_idempotent(
    events_path: Path, db_path: Path, schemas_dir: Path
) -> None:
    old_ts = datetime.now(tz=timezone.utc) - timedelta(days=20)
    # action_aging: past due
    events_api.append(_action_event(due="2026-01-01", owner="alice", ts=old_ts), events_path)
    # action_unowned: no owner, old enough
    events_api.append(_action_event(owner=None, due=None, ts=old_ts), events_path)
    # blocker_unowned: no owner, old enough
    events_api.append(_blocker_event(owner=None, ts=old_ts), events_path)

    _load(events_path, db_path)
    r1, signals = run_analyze("test--project", db_path, events_path, "run-001", schemas_dir)

    signal_types = {s.type for s in signals}
    assert "action_aging" in signal_types
    assert "action_unowned" in signal_types
    assert "blocker_unowned" in signal_types

    _load(events_path, db_path)
    r2, _ = run_analyze("test--project", db_path, events_path, "run-002", schemas_dir)
    assert r2.counts["signals_new"] == 0
