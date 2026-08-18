from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Actor — discriminated union over actor.kind
# ---------------------------------------------------------------------------

class LLMActor(BaseModel):
    kind: Literal["llm"]
    model: str
    prompt: str  # e.g. "extract-context.md@1"


class HumanActor(BaseModel):
    kind: Literal["human"]
    id: str


class MCPActor(BaseModel):
    kind: Literal["mcp"]
    source: str  # "gmail" | "drive" | "calendar"


class DeterministicActor(BaseModel):
    kind: Literal["deterministic"]
    detector: str  # e.g. "action_aging"


Actor = Annotated[
    LLMActor | HumanActor | MCPActor | DeterministicActor,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Payload — discriminated union over payload.type
# ---------------------------------------------------------------------------

class SourceIngestedPayload(BaseModel):
    type: Literal["source_ingested"]
    filename: str
    source_type: str  # "email" | "doc" | "backlog" | "meeting"
    size_bytes: int
    sender: str | None = None  # From: header for email sources; used by stakeholder_inactivity


class ActionAddedPayload(BaseModel):
    type: Literal["action_added"]
    description: str
    owner: str | None = None
    due: str | None = None  # ISO date string, e.g. "2026-05-20"
    status: str = "open"


class ActionUpdatedPayload(BaseModel):
    type: Literal["action_updated"]
    action_ref: str  # references a prior action_added event_id
    description: str | None = None
    owner: str | None = None
    due: str | None = None
    status: str | None = None


class BlockerAddedPayload(BaseModel):
    type: Literal["blocker_added"]
    description: str
    owner: str | None = None
    due: str | None = None


class BlockerUpdatedPayload(BaseModel):
    type: Literal["blocker_updated"]
    blocker_ref: str
    description: str | None = None
    owner: str | None = None
    due: str | None = None
    status: str | None = None


class RiskAddedPayload(BaseModel):
    type: Literal["risk_added"]
    description: str
    severity: Literal["low", "medium", "high"]
    owner: str | None = None
    status: str = "open"


class DecisionMadePayload(BaseModel):
    type: Literal["decision_made"]
    description: str
    rationale: str | None = None
    decided_by: str | None = None


class MilestoneAddedPayload(BaseModel):
    type: Literal["milestone_added"]
    description: str
    target_date: str | None = None  # ISO date string e.g. "2026-05-20"
    status: str = "open"
    owner: str | None = None


class PipelineHealthPayload(BaseModel):
    type: Literal["pipeline_health"]
    category: str  # "llm_budget_exceeded" | "ingest_failed" | ...
    message: str
    detail: str | None = None


class SignalDetectedPayload(BaseModel):
    type: Literal["signal_detected"]
    signal_id: str
    signal_type: str
    category: str
    severity: Literal["low", "medium", "high"]
    confidence: float
    evidence: list[str]  # non-empty list of event_ids
    method: Literal["deterministic", "llm"]
    rationale: str
    recommended_action: str


class SourceSummarizedPayload(BaseModel):
    """A research document condensed into the log (v2.0, research lane).

    The summary lives here rather than only in the article so the wiki stays a
    projection of the event log — a re-compile must not need the LLM again.
    """

    type: Literal["source_summarized"]
    summary: str
    topics: list[str] = Field(default_factory=list)


class ConceptAddedPayload(BaseModel):
    """A concept extracted from a research document."""

    type: Literal["concept_added"]
    slug: str
    name: str
    description: str | None = None


class EventRetractedPayload(BaseModel):
    type: Literal["event_retracted"]
    retracted_event_id: str
    reason: str


Payload = Annotated[
    SourceIngestedPayload
    | ActionAddedPayload
    | ActionUpdatedPayload
    | BlockerAddedPayload
    | BlockerUpdatedPayload
    | RiskAddedPayload
    | DecisionMadePayload
    | MilestoneAddedPayload
    | PipelineHealthPayload
    | SignalDetectedPayload
    | SourceSummarizedPayload
    | ConceptAddedPayload
    | EventRetractedPayload,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Event — core append-only log entry (PRD §6.2)
# ---------------------------------------------------------------------------

class Event(BaseModel):
    event_id: str
    schema_version: int = 1
    ts: datetime
    run_id: str
    project_id: str
    type: str
    actor: Actor
    source_ref: str
    source_hash: str
    payload: Payload
    hash: str
    confidence: float | None = None
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    retracted: bool = False
    retracted_reason: str | None = None


# ---------------------------------------------------------------------------
# Signal — typed observation about project health (PRD §6.3)
# ---------------------------------------------------------------------------

class Signal(BaseModel):
    signal_id: str
    schema_version: int = 1
    project_id: str
    run_id: str
    category: Literal["risk", "deliverable", "action", "blocker", "backlog", "communication"]
    type: str
    severity: Literal["low", "medium", "high"]
    confidence: float
    evidence: list[str] = Field(min_length=1)  # must be non-empty
    method: Literal["deterministic", "llm"]
    rationale: str
    detected_at: datetime
    recommended_action: str


# ---------------------------------------------------------------------------
# Operations log (PRD §6.4)
# ---------------------------------------------------------------------------

class StageResult(BaseModel):
    name: str
    status: Literal["ok", "error", "skipped"]
    duration_ms: int
    counts: dict[str, int] = Field(default_factory=dict)
    cost_usd: float | None = None
    error: str | None = None


class RunLog(BaseModel):
    run_id: str
    started_at: datetime
    ended_at: datetime | None = None
    project_ids: list[str]
    stages: list[StageResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    total_cost_usd: float = 0.0
