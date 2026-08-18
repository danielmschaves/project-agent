"""Tests for project_agent.signals.llm and the LLM signal path in run_analyze."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from project_agent import events as events_api, warehouse
from project_agent.schemas import (
    DeterministicActor,
    Event,
    LLMActor,
    RiskAddedPayload,
    SourceIngestedPayload,
)
from project_agent.signals import llm as llm_signals
from pipeline.stages import run_analyze


# ---------------------------------------------------------------------------
# Fake LLM client — zero Anthropic dependency
# ---------------------------------------------------------------------------

class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens: int = 100, output_tokens: int = 50) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, text: str, with_usage: bool = True) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage() if with_usage else None


class _FakeMessages:
    def __init__(self, response: str, call_log: list[str]) -> None:
        self._response = response
        self._log = call_log

    def create(self, **kwargs: Any) -> _FakeMessage:
        self._log.append(kwargs.get("model", "unknown"))
        return _FakeMessage(self._response)


class _FakeClient:
    def __init__(self, response: str, call_log: list[str] | None = None) -> None:
        self._log: list[str] = call_log if call_log is not None else []
        self.messages = _FakeMessages(response, self._log)

    @property
    def call_count(self) -> int:
        return len(self._log)


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------

def _llm_actor() -> LLMActor:
    return LLMActor(kind="llm", model="claude-sonnet-4-6", prompt="extract-context@2")


def _risk_event(
    *,
    description: str = "Some risk",
    severity: str = "medium",
    status: str = "open",
    ts: datetime | None = None,
    project_id: str = "test--project",
) -> Event:
    if ts is None:
        ts = datetime.now(tz=timezone.utc) - timedelta(days=10)
    sh = "sha256:" + uuid.uuid4().hex
    payload = RiskAddedPayload(type="risk_added", description=description, severity=severity, status=status)
    return Event(
        event_id=str(uuid.uuid4()), ts=ts, run_id="run-001",
        project_id=project_id, type="risk_added", actor=_llm_actor(),
        source_ref="raw/docs/doc.md", source_hash=sh, payload=payload,
        hash=events_api.hash_event(payload.model_dump(), sh),
    )


def _source_event(
    *,
    sender: str = "alice@client.com",
    source_type: str = "email",
    ts: datetime | None = None,
    project_id: str = "test--project",
) -> Event:
    if ts is None:
        ts = datetime.now(tz=timezone.utc) - timedelta(days=2)
    sh = "sha256:" + uuid.uuid4().hex
    payload = SourceIngestedPayload(
        type="source_ingested", filename="email.eml",
        source_type=source_type, size_bytes=200, sender=sender,
    )
    return Event(
        event_id=str(uuid.uuid4()), ts=ts, run_id="run-001",
        project_id=project_id, type="source_ingested",
        actor=DeterministicActor(kind="deterministic", detector="inbox"),
        source_ref=f"raw/email/email.eml", source_hash=sh,
        payload=payload, hash=events_api.hash_event(payload.model_dump(), sh),
    )


def _load_db(events_path: Path, db_path: Path) -> None:
    warehouse.load(events_path, db_path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "prompts"
    d.mkdir()
    for name in ("detect-risk-escalation", "detect-risk-emergent", "detect-tone-shift"):
        (d / f"{name}.md").write_text(
            "---\nversion: 1\npurpose: test\nmodel: claude-sonnet-4-6\nmax_tokens: 2048\n---\nTest prompt.",
            encoding="utf-8",
        )
    return d


@pytest.fixture
def schemas_dir(tmp_path: Path) -> Path:
    d = tmp_path / "schemas"
    d.mkdir()
    for name in (
        "action_aging", "action_unowned", "blocker_unowned",
        "risk_unaddressed", "blocker_aging", "deliverable_drift", "stakeholder_inactivity",
        "risk_escalation", "risk_emergent", "tone_shift",
    ):
        (d / f"{name}.json").write_text("{}", encoding="utf-8")
    return d


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    (d / "signals.yaml").write_text("llm_cost_cap_usd_per_project_per_day: 0.50\n", encoding="utf-8")
    return d


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    d.mkdir()
    return d


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "warehouse.duckdb"


# ---------------------------------------------------------------------------
# detect_risk_escalation
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_risk_escalation_returns_empty_when_no_risks(
    events_path: Path, db_path: Path, prompts_dir: Path, cache_dir: Path,
) -> None:
    _load_db(events_path, db_path)
    sigs, cost = llm_signals.detect_risk_escalation(
        db_path, "test--project", "run-001", {},
        cache_dir, prompts_dir, "claude-sonnet-4-6",
    )
    assert sigs == []
    assert cost == 0.0


@pytest.mark.unit
def test_risk_escalation_returns_signal_from_llm_response(
    events_path: Path, db_path: Path, prompts_dir: Path, cache_dir: Path,
) -> None:
    risk = _risk_event()
    # Also add a recent event so recent_rows is non-empty (required for escalation context)
    recent_src = _source_event(ts=datetime.now(tz=timezone.utc) - timedelta(days=2))
    events_api.append(risk, events_path)
    events_api.append(recent_src, events_path)
    _load_db(events_path, db_path)

    llm_response = json.dumps({
        "signals": [{
            "type": "risk_escalation",
            "severity": "high",
            "confidence": 0.9,
            "evidence": [risk.event_id],
            "rationale": "Risk is escalating.",
            "recommended_action": "Escalate immediately.",
        }]
    })
    client = _FakeClient(llm_response)

    sigs, cost = llm_signals.detect_risk_escalation(
        db_path, "test--project", "run-001", {"lookback_days": 7},
        cache_dir, prompts_dir, "claude-sonnet-4-6", client,
    )
    assert len(sigs) == 1
    assert sigs[0].type == "risk_escalation"
    assert sigs[0].severity == "high"
    assert risk.event_id in sigs[0].evidence
    assert sigs[0].method == "llm"
    assert cost > 0.0
    assert client.call_count == 1


@pytest.mark.unit
def test_risk_escalation_skips_signal_with_no_evidence(
    events_path: Path, db_path: Path, prompts_dir: Path, cache_dir: Path,
) -> None:
    risk = _risk_event()
    events_api.append(risk, events_path)
    _load_db(events_path, db_path)

    llm_response = json.dumps({
        "signals": [{"type": "risk_escalation", "severity": "high", "confidence": 0.9, "evidence": [], "rationale": "x", "recommended_action": "y"}]
    })
    sigs, _ = llm_signals.detect_risk_escalation(
        db_path, "test--project", "run-001", {},
        cache_dir, prompts_dir, "claude-sonnet-4-6", _FakeClient(llm_response),
    )
    assert sigs == []


# ---------------------------------------------------------------------------
# detect_risk_emergent
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_risk_emergent_returns_empty_when_no_recent_events(
    events_path: Path, db_path: Path, prompts_dir: Path, cache_dir: Path,
) -> None:
    _load_db(events_path, db_path)
    sigs, cost = llm_signals.detect_risk_emergent(
        db_path, "test--project", "run-001", {},
        cache_dir, prompts_dir, "claude-sonnet-4-6",
    )
    assert sigs == []
    assert cost == 0.0


@pytest.mark.unit
def test_risk_emergent_returns_signal_from_llm_response(
    events_path: Path, db_path: Path, prompts_dir: Path, cache_dir: Path,
) -> None:
    src = _source_event(ts=datetime.now(tz=timezone.utc) - timedelta(days=3))
    events_api.append(src, events_path)
    _load_db(events_path, db_path)

    llm_response = json.dumps({
        "signals": [{
            "type": "risk_emergent",
            "severity": "medium",
            "confidence": 0.75,
            "evidence": [src.event_id],
            "rationale": "New risk detected.",
            "recommended_action": "Add to risk register.",
        }]
    })
    sigs, _ = llm_signals.detect_risk_emergent(
        db_path, "test--project", "run-001", {"lookback_days": 14},
        cache_dir, prompts_dir, "claude-sonnet-4-6", _FakeClient(llm_response),
    )
    assert len(sigs) == 1
    assert sigs[0].type == "risk_emergent"
    assert sigs[0].category == "risk"


# ---------------------------------------------------------------------------
# detect_tone_shift
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_tone_shift_returns_empty_when_no_source_events(
    events_path: Path, db_path: Path, prompts_dir: Path, cache_dir: Path,
) -> None:
    _load_db(events_path, db_path)
    sigs, cost = llm_signals.detect_tone_shift(
        db_path, "test--project", "run-001", {},
        cache_dir, prompts_dir, "claude-sonnet-4-6",
    )
    assert sigs == []
    assert cost == 0.0


@pytest.mark.unit
def test_tone_shift_returns_signal_from_llm_response(
    events_path: Path, db_path: Path, prompts_dir: Path, cache_dir: Path,
) -> None:
    src = _source_event(source_type="email", ts=datetime.now(tz=timezone.utc) - timedelta(days=1))
    events_api.append(src, events_path)
    _load_db(events_path, db_path)

    llm_response = json.dumps({
        "signals": [{
            "type": "tone_shift",
            "severity": "medium",
            "confidence": 0.8,
            "evidence": [src.event_id],
            "rationale": "Client tone has shifted.",
            "recommended_action": "Schedule a call.",
        }]
    })
    sigs, _ = llm_signals.detect_tone_shift(
        db_path, "test--project", "run-001", {"lookback_days": 7},
        cache_dir, prompts_dir, "claude-sonnet-4-6", _FakeClient(llm_response),
    )
    assert len(sigs) == 1
    assert sigs[0].type == "tone_shift"
    assert sigs[0].category == "communication"


# ---------------------------------------------------------------------------
# Idempotence — cache hit → zero API calls
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_risk_escalation_cache_hit_makes_no_api_calls(
    events_path: Path, db_path: Path, prompts_dir: Path, cache_dir: Path,
) -> None:
    risk = _risk_event()
    recent_src = _source_event(ts=datetime.now(tz=timezone.utc) - timedelta(days=2))
    events_api.append(risk, events_path)
    events_api.append(recent_src, events_path)
    _load_db(events_path, db_path)

    llm_response = json.dumps({
        "signals": [{
            "type": "risk_escalation", "severity": "high", "confidence": 0.9,
            "evidence": [risk.event_id], "rationale": "r", "recommended_action": "a",
        }]
    })
    log: list[str] = []
    client = _FakeClient(llm_response, log)

    # First call — API hit
    sigs1, _ = llm_signals.detect_risk_escalation(
        db_path, "test--project", "run-001", {},
        cache_dir, prompts_dir, "claude-sonnet-4-6", client,
    )
    assert client.call_count == 1

    # Second call — cache hit, no API call
    sigs2, cost2 = llm_signals.detect_risk_escalation(
        db_path, "test--project", "run-002", {},
        cache_dir, prompts_dir, "claude-sonnet-4-6", client,
    )
    assert client.call_count == 1  # unchanged
    assert cost2 == 0.0
    assert len(sigs1) == len(sigs2)


# ---------------------------------------------------------------------------
# run_analyze — LLM signals wired in
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_analyze_emits_llm_signal(
    events_path: Path, db_path: Path, schemas_dir: Path,
    prompts_dir: Path, cache_dir: Path, config_dir: Path,
) -> None:
    risk = _risk_event()
    events_api.append(risk, events_path)
    _load_db(events_path, db_path)

    llm_response = json.dumps({
        "signals": [{
            "type": "risk_escalation", "severity": "high", "confidence": 0.85,
            "evidence": [risk.event_id], "rationale": "escalating", "recommended_action": "act",
        }]
    })
    client = _FakeClient(llm_response)

    result, signals = run_analyze(
        "test--project", db_path, events_path, "run-001", schemas_dir,
        cache_dir=cache_dir, prompts_dir=prompts_dir, model="claude-sonnet-4-6",
        config_dir=config_dir, llm_client=client,
    )

    assert result.status == "ok"
    llm_sigs = [s for s in signals if s.method == "llm"]
    assert len(llm_sigs) >= 1
    assert llm_sigs[0].type == "risk_escalation"


@pytest.mark.idempotence
def test_run_analyze_llm_idempotent(
    events_path: Path, db_path: Path, schemas_dir: Path,
    prompts_dir: Path, cache_dir: Path, config_dir: Path,
) -> None:
    risk = _risk_event()
    events_api.append(risk, events_path)
    _load_db(events_path, db_path)

    llm_response = json.dumps({
        "signals": [{
            "type": "risk_escalation", "severity": "high", "confidence": 0.85,
            "evidence": [risk.event_id], "rationale": "escalating", "recommended_action": "act",
        }]
    })
    log: list[str] = []
    client = _FakeClient(llm_response, log)

    r1, sigs1 = run_analyze(
        "test--project", db_path, events_path, "run-001", schemas_dir,
        cache_dir=cache_dir, prompts_dir=prompts_dir, config_dir=config_dir, llm_client=client,
    )
    signals_run1 = r1.counts["signals_new"]

    _load_db(events_path, db_path)
    r2, _ = run_analyze(
        "test--project", db_path, events_path, "run-002", schemas_dir,
        cache_dir=cache_dir, prompts_dir=prompts_dir, config_dir=config_dir, llm_client=client,
    )

    assert r2.counts["signals_new"] == 0
    assert r2.counts["signals_skipped"] >= 1


# ---------------------------------------------------------------------------
# Budget cap
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_budget_exceeded_emits_pipeline_health_event(
    events_path: Path, db_path: Path, schemas_dir: Path,
    prompts_dir: Path, cache_dir: Path, tmp_path: Path,
) -> None:
    # Budget lower than the cost of one fake API call (~0.00105 USD).
    # The first LLM detector runs and spends that cost; before the second
    # detector fires, the budget check triggers and emits pipeline_health.
    low_budget_dir = tmp_path / "config_low"
    low_budget_dir.mkdir()
    (low_budget_dir / "signals.yaml").write_text(
        "llm_cost_cap_usd_per_project_per_day: 0.001\n", encoding="utf-8"
    )

    risk = _risk_event()
    recent_src = _source_event(ts=datetime.now(tz=timezone.utc) - timedelta(days=2))
    events_api.append(risk, events_path)
    events_api.append(recent_src, events_path)
    _load_db(events_path, db_path)

    llm_response = json.dumps({"signals": []})
    client = _FakeClient(llm_response)

    result, signals = run_analyze(
        "test--project", db_path, events_path, "run-001", schemas_dir,
        cache_dir=cache_dir, prompts_dir=prompts_dir, config_dir=low_budget_dir, llm_client=client,
    )

    assert result.status == "ok"
    # First detector made exactly one API call before budget was exceeded
    assert client.call_count == 1
    # pipeline_health event emitted (cost > 0 triggered the guard)
    ph_events = events_api.query(events_path, project_id="test--project", types=["pipeline_health"])
    assert len(ph_events) == 1
    from project_agent.schemas import PipelineHealthPayload
    assert isinstance(ph_events[0].payload, PipelineHealthPayload)
    assert ph_events[0].payload.category == "llm_budget_exceeded"


@pytest.mark.integration
def test_budget_zero_silently_skips_llm_signals(
    events_path: Path, db_path: Path, schemas_dir: Path,
    prompts_dir: Path, cache_dir: Path, tmp_path: Path,
) -> None:
    # budget=0.0 is a deliberate opt-out — no API calls, no pipeline_health event.
    zero_budget_dir = tmp_path / "config_zero"
    zero_budget_dir.mkdir()
    (zero_budget_dir / "signals.yaml").write_text(
        "llm_cost_cap_usd_per_project_per_day: 0.0\n", encoding="utf-8"
    )

    risk = _risk_event()
    events_api.append(risk, events_path)
    _load_db(events_path, db_path)

    client = _FakeClient(json.dumps({"signals": []}))
    _, signals = run_analyze(
        "test--project", db_path, events_path, "run-001", schemas_dir,
        cache_dir=cache_dir, prompts_dir=prompts_dir, config_dir=zero_budget_dir, llm_client=client,
    )

    # No API calls and no pipeline_health — zero budget is opt-out, not exhaustion
    assert client.call_count == 0
    ph_events = events_api.query(events_path, project_id="test--project", types=["pipeline_health"])
    assert ph_events == []
    llm_sigs = [s for s in signals if s.method == "llm"]
    assert llm_sigs == []
