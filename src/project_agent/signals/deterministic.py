from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from project_agent import warehouse
from project_agent.schemas import Signal

logger = logging.getLogger(__name__)


def detect_action_aging(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
) -> list[Signal]:
    """Actions that are past their due date OR open longer than stale_days."""
    stale_days = int(thresholds.get("stale_days", 14))

    rows = warehouse.query(db_path, f"""
        SELECT event_id FROM actions
        WHERE project_id = '{project_id}'
          AND (status IS NULL OR status NOT IN ('done', 'closed'))
          AND (
            (due IS NOT NULL AND TRY_CAST(due AS DATE) < CURRENT_DATE)
            OR (CAST(ts AS TIMESTAMPTZ) < NOW() - INTERVAL '{stale_days} days')
          )
    """)

    if not rows:
        return []

    evidence = [str(r["event_id"]) for r in rows]
    n = len(evidence)
    severity = "high" if n >= 3 else "medium"
    return [Signal(
        signal_id=str(uuid.uuid4()),
        project_id=project_id,
        run_id=run_id,
        category="action",
        type="action_aging",
        severity=severity,
        confidence=1.0,
        evidence=evidence,
        method="deterministic",
        rationale=(
            f"{n} action(s) are past due or open >{stale_days} days without resolution."
        ),
        detected_at=datetime.now(tz=timezone.utc),
        recommended_action="Review and close or reschedule aging actions.",
    )]


def detect_action_unowned(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
) -> list[Signal]:
    """Actions missing an owner or due date for longer than unowned_hours."""
    unowned_hours = int(thresholds.get("unowned_hours", 48))

    rows = warehouse.query(db_path, f"""
        SELECT event_id FROM actions
        WHERE project_id = '{project_id}'
          AND (status IS NULL OR status NOT IN ('done', 'closed'))
          AND (owner IS NULL OR due IS NULL)
          AND (
            CAST(ts AS TIMESTAMPTZ) < NOW() - INTERVAL '{unowned_hours} hours'
            OR (due IS NOT NULL AND TRY_CAST(due AS DATE) < CURRENT_DATE)
          )
    """)

    if not rows:
        return []

    evidence = [str(r["event_id"]) for r in rows]
    n = len(evidence)
    return [Signal(
        signal_id=str(uuid.uuid4()),
        project_id=project_id,
        run_id=run_id,
        category="action",
        type="action_unowned",
        severity="medium",
        confidence=1.0,
        evidence=evidence,
        method="deterministic",
        rationale=(
            f"{n} action(s) open >{unowned_hours}h without an owner or due date."
        ),
        detected_at=datetime.now(tz=timezone.utc),
        recommended_action="Assign an owner and due date to each open action.",
    )]


def detect_blocker_unowned(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
) -> list[Signal]:
    """Blockers missing an owner for longer than unowned_hours."""
    unowned_hours = int(thresholds.get("unowned_hours", 48))

    rows = warehouse.query(db_path, f"""
        SELECT event_id FROM blockers
        WHERE project_id = '{project_id}'
          AND owner IS NULL
          AND CAST(ts AS TIMESTAMPTZ) < NOW() - INTERVAL '{unowned_hours} hours'
    """)

    if not rows:
        return []

    evidence = [str(r["event_id"]) for r in rows]
    n = len(evidence)
    return [Signal(
        signal_id=str(uuid.uuid4()),
        project_id=project_id,
        run_id=run_id,
        category="blocker",
        type="blocker_unowned",
        severity="high",
        confidence=1.0,
        evidence=evidence,
        method="deterministic",
        rationale=(
            f"{n} blocker(s) open >{unowned_hours}h without an assigned owner."
        ),
        detected_at=datetime.now(tz=timezone.utc),
        recommended_action="Assign an owner to each open blocker immediately.",
    )]


def detect_risk_unaddressed(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
) -> list[Signal]:
    """Risks open without resolution for longer than stale_days."""
    stale_days = int(thresholds.get("stale_days", 7))

    rows = warehouse.query(db_path, f"""
        SELECT event_id, severity FROM risks
        WHERE project_id = '{project_id}'
          AND (status IS NULL OR status NOT IN ('resolved', 'closed', 'accepted', 'mitigated'))
          AND CAST(ts AS TIMESTAMPTZ) < NOW() - INTERVAL '{stale_days} days'
    """)

    if not rows:
        return []

    evidence = [str(r["event_id"]) for r in rows]
    n = len(evidence)
    severity = "high" if any(r.get("severity") == "high" for r in rows) else "medium"
    return [Signal(
        signal_id=str(uuid.uuid4()),
        project_id=project_id,
        run_id=run_id,
        category="risk",
        type="risk_unaddressed",
        severity=severity,
        confidence=1.0,
        evidence=evidence,
        method="deterministic",
        rationale=(
            f"{n} risk(s) open >{stale_days} days without resolution or acceptance."
        ),
        detected_at=datetime.now(tz=timezone.utc),
        recommended_action="Review each open risk and update its status or assign an owner.",
    )]


def detect_blocker_aging(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
) -> list[Signal]:
    """Blockers open longer than aging_days."""
    aging_days = int(thresholds.get("aging_days", 7))

    rows = warehouse.query(db_path, f"""
        SELECT event_id FROM blockers
        WHERE project_id = '{project_id}'
          AND CAST(ts AS TIMESTAMPTZ) < NOW() - INTERVAL '{aging_days} days'
    """)

    if not rows:
        return []

    evidence = [str(r["event_id"]) for r in rows]
    n = len(evidence)
    severity = "high" if n >= 3 else "medium"
    return [Signal(
        signal_id=str(uuid.uuid4()),
        project_id=project_id,
        run_id=run_id,
        category="blocker",
        type="blocker_aging",
        severity=severity,
        confidence=1.0,
        evidence=evidence,
        method="deterministic",
        rationale=(
            f"{n} blocker(s) open >{aging_days} days without resolution."
        ),
        detected_at=datetime.now(tz=timezone.utc),
        recommended_action="Escalate aging blockers and set a resolution deadline.",
    )]


def detect_deliverable_drift(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
) -> list[Signal]:
    """Milestones within near_days of their target_date (or past it) and not done."""
    near_days = int(thresholds.get("near_days", 7))

    rows = warehouse.query(db_path, f"""
        SELECT event_id FROM milestones
        WHERE project_id = '{project_id}'
          AND (status IS NULL OR status NOT IN ('done', 'completed', 'closed'))
          AND target_date IS NOT NULL
          AND TRY_CAST(target_date AS DATE) <= CURRENT_DATE + INTERVAL '{near_days} days'
    """)

    if not rows:
        return []

    evidence = [str(r["event_id"]) for r in rows]
    n = len(evidence)
    return [Signal(
        signal_id=str(uuid.uuid4()),
        project_id=project_id,
        run_id=run_id,
        category="deliverable",
        type="deliverable_drift",
        severity="high",
        confidence=1.0,
        evidence=evidence,
        method="deterministic",
        rationale=(
            f"{n} milestone(s) due within {near_days} days (or overdue) without a done status."
        ),
        detected_at=datetime.now(tz=timezone.utc),
        recommended_action="Review at-risk milestones and update plan or escalate to stakeholders.",
    )]


def detect_stakeholder_inactivity(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
) -> list[Signal]:
    """Stakeholders with cadence_days set who haven't emailed within their expected window.

    Only fires when the stakeholder has at least one prior email on record
    (uses that event_id as evidence). Stakeholders with no email history are
    skipped — inactivity cannot be distinguished from a new project.
    """
    watchlist: list[dict[str, object]] = list(thresholds.get("watchlist", []))  # type: ignore[arg-type]
    if not watchlist:
        return []

    rows = warehouse.query(db_path, f"""
        SELECT
            json_extract_string(payload, '$.sender') AS sender,
            MAX(event_id)                            AS last_event_id,
            MAX(ts)                                  AS last_seen_str
        FROM events
        WHERE project_id = '{project_id}'
          AND type = 'source_ingested'
          AND json_extract_string(payload, '$.source_type') = 'email'
        GROUP BY json_extract_string(payload, '$.sender')
    """)

    last_seen_by_sender: dict[str, tuple[datetime, str]] = {}
    for r in rows:
        if not r["sender"]:
            continue
        try:
            last_seen_dt = datetime.fromisoformat(str(r["last_seen_str"]))
            if last_seen_dt.tzinfo is None:
                last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
            last_seen_by_sender[r["sender"]] = (last_seen_dt, str(r["last_event_id"]))
        except (ValueError, TypeError):
            pass

    now = datetime.now(tz=timezone.utc)
    evidence: list[str] = []
    inactive_names: list[str] = []

    for entry in watchlist:
        contact = str(entry.get("contact_email", "")) if entry.get("contact_email") else ""
        cadence = entry.get("cadence_days")
        if not contact or cadence is None:
            continue
        cadence_days = int(cadence)  # type: ignore[arg-type]

        record = last_seen_by_sender.get(contact)
        if record is None:
            continue  # no history — cannot determine inactivity

        last_seen_ts, last_event_id = record
        if (now - last_seen_ts) > timedelta(days=cadence_days):
            evidence.append(last_event_id)
            inactive_names.append(str(entry.get("name", contact)))

    if not evidence:
        return []

    n = len(evidence)
    names_str = ", ".join(inactive_names)
    return [Signal(
        signal_id=str(uuid.uuid4()),
        project_id=project_id,
        run_id=run_id,
        category="communication",
        type="stakeholder_inactivity",
        severity="medium",
        confidence=1.0,
        evidence=evidence,
        method="deterministic",
        rationale=(
            f"{n} stakeholder(s) ({names_str}) have not sent email within their expected cadence."
        ),
        detected_at=datetime.now(tz=timezone.utc),
        recommended_action="Re-engage inactive stakeholders and confirm continued participation.",
    )]
