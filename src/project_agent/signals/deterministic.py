from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
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
