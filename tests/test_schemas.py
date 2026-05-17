"""Unit tests for pydantic schemas (CLAUDE.md §4.2, PRD §6.2–6.3)."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from project_agent.schemas import (
    DeterministicActor,
    Event,
    EventRetractedPayload,
    LLMActor,
    RunLog,
    Signal,
    SignalDetectedPayload,
    SourceIngestedPayload,
    StageResult,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _base_event(**overrides: object) -> dict:
    return {
        "event_id": "evt-001",
        "ts": _now(),
        "run_id": "run-001",
        "project_id": "test--project",
        "type": "source_ingested",
        "actor": {"kind": "deterministic", "detector": "inbox"},
        "source_ref": "sources/_inbox/doc.md",
        "source_hash": "sha256:abc123",
        "payload": {
            "type": "source_ingested",
            "filename": "doc.md",
            "source_type": "doc",
            "size_bytes": 1024,
        },
        "hash": "sha256:deadbeef",
        **overrides,
    }


@pytest.mark.unit
def test_event_with_deterministic_actor_and_source_ingested_payload() -> None:
    event = Event(**_base_event())
    assert event.project_id == "test--project"
    assert isinstance(event.actor, DeterministicActor)
    assert isinstance(event.payload, SourceIngestedPayload)
    assert event.retracted is False
    assert event.supersedes == []


@pytest.mark.unit
def test_event_with_llm_actor() -> None:
    data = _base_event()
    data["actor"] = {"kind": "llm", "model": "claude-sonnet-4-6", "prompt": "extract-context.md@1"}
    event = Event(**data)
    assert isinstance(event.actor, LLMActor)
    assert event.actor.model == "claude-sonnet-4-6"


@pytest.mark.unit
def test_event_actor_invalid_kind_raises() -> None:
    data = _base_event()
    data["actor"] = {"kind": "robot", "id": "r2d2"}
    with pytest.raises(ValidationError):
        Event(**data)


@pytest.mark.unit
def test_event_payload_invalid_type_raises() -> None:
    data = _base_event()
    data["payload"] = {"type": "unknown_type", "stuff": "things"}
    with pytest.raises(ValidationError):
        Event(**data)


@pytest.mark.unit
def test_event_optional_fields_default() -> None:
    event = Event(**_base_event())
    assert event.confidence is None
    assert event.superseded_by is None
    assert event.retracted_reason is None


@pytest.mark.unit
def test_signal_requires_non_empty_evidence() -> None:
    with pytest.raises(ValidationError):
        Signal(
            signal_id="sig-001",
            project_id="test--project",
            run_id="run-001",
            category="action",
            type="action_aging",
            severity="high",
            confidence=0.95,
            evidence=[],  # must be non-empty
            method="deterministic",
            rationale="Action overdue.",
            detected_at=_now(),
            recommended_action="Follow up.",
        )


@pytest.mark.unit
def test_signal_valid_with_evidence() -> None:
    sig = Signal(
        signal_id="sig-001",
        project_id="test--project",
        run_id="run-001",
        category="action",
        type="action_aging",
        severity="high",
        confidence=0.95,
        evidence=["evt-001", "evt-002"],
        method="deterministic",
        rationale="Action overdue.",
        detected_at=_now(),
        recommended_action="Follow up.",
    )
    assert len(sig.evidence) == 2


@pytest.mark.unit
def test_signal_detected_payload_requires_non_empty_evidence() -> None:
    payload = SignalDetectedPayload(
        type="signal_detected",
        signal_id="sig-001",
        signal_type="action_aging",
        category="action",
        severity="high",
        confidence=0.9,
        evidence=["evt-001"],
        method="deterministic",
        rationale="Overdue.",
        recommended_action="Act.",
    )
    assert payload.evidence == ["evt-001"]


@pytest.mark.unit
def test_event_retracted_payload() -> None:
    data = _base_event()
    data["payload"] = {
        "type": "event_retracted",
        "retracted_event_id": "evt-999",
        "reason": "LLM hallucination",
    }
    data["type"] = "event_retracted"
    data["actor"] = {"kind": "human", "id": "pm@example.com"}
    event = Event(**data)
    assert isinstance(event.payload, EventRetractedPayload)


@pytest.mark.unit
def test_run_log_accumulates_stages() -> None:
    log = RunLog(
        run_id="run-001",
        started_at=_now(),
        project_ids=["test--project"],
    )
    log.stages.append(
        StageResult(name="ingest", status="ok", duration_ms=250, counts={"sources_new": 3})
    )
    assert log.stages[0].name == "ingest"
    assert log.total_cost_usd == 0.0


@pytest.mark.unit
def test_stage_result_error_status() -> None:
    result = StageResult(
        name="parse",
        status="error",
        duration_ms=500,
        error="API timeout",
    )
    assert result.status == "error"
    assert result.error == "API timeout"
