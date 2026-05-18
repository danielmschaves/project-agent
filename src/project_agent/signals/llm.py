from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_agent import llm as llm_module, warehouse
from project_agent.schemas import Signal

logger = logging.getLogger(__name__)


def _input_hash(data: dict[str, Any]) -> str:
    """Stable SHA-256 of deterministically serialised input dict."""
    return "sha256:" + hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _parse_llm_signals(
    result: dict[str, Any],
    project_id: str,
    run_id: str,
    category: str,
) -> list[Signal]:
    """Convert raw LLM response dict into validated Signal objects.

    Skips any item whose evidence list is empty — the pydantic min_length=1
    constraint would reject them, and empty evidence means the LLM didn't
    anchor the signal to real events.
    """
    signals: list[Signal] = []
    for item in result.get("signals", []):
        evidence = [str(e) for e in item.get("evidence", []) if e]
        if not evidence:
            logger.warning("LLM signal missing evidence — skipping: %s", item.get("type"))
            continue
        try:
            signals.append(Signal(
                signal_id=str(uuid.uuid4()),
                project_id=project_id,
                run_id=run_id,
                category=category,  # type: ignore[arg-type]
                type=str(item["type"]),
                severity=item.get("severity", "medium"),
                confidence=float(item.get("confidence", 0.7)),
                evidence=evidence,
                method="llm",
                rationale=str(item.get("rationale", "")),
                detected_at=datetime.now(tz=timezone.utc),
                recommended_action=str(item.get("recommended_action", "")),
            ))
        except Exception:
            logger.warning("Could not parse LLM signal item: %s", item)
    return signals


def detect_risk_escalation(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
    cache_dir: Path,
    prompts_dir: Path,
    model: str,
    client: Any = None,
) -> tuple[list[Signal], float]:
    """Detect escalating risks based on recent project activity."""
    lookback_days = int(thresholds.get("lookback_days", 7))

    risk_rows = warehouse.query(db_path, f"""
        SELECT event_id,
               json_extract_string(payload, '$.description') AS description,
               json_extract_string(payload, '$.severity')    AS severity
        FROM events
        WHERE project_id = '{project_id}' AND type = 'risk_added'
    """)
    if not risk_rows:
        return [], 0.0

    recent_rows = warehouse.query(db_path, f"""
        SELECT event_id, type, ts,
               json_extract_string(payload, '$.description') AS description
        FROM events
        WHERE project_id = '{project_id}'
          AND type != 'signal_detected'
          AND CAST(ts AS TIMESTAMPTZ) > NOW() - INTERVAL '{lookback_days} days'
        ORDER BY ts DESC
        LIMIT 50
    """)
    if not recent_rows:
        return [], 0.0

    input_data: dict[str, Any] = {
        "project_id": project_id,
        "existing_risks": risk_rows,
        "recent_events": recent_rows,
    }
    result, cost_usd = llm_module.extract_with_cost(
        prompt_name="detect-risk-escalation",
        source_text=json.dumps(input_data, indent=2),
        source_hash=_input_hash(input_data),
        model=model,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        client=client,
    )
    return _parse_llm_signals(result, project_id, run_id, "risk"), cost_usd


def detect_risk_emergent(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
    cache_dir: Path,
    prompts_dir: Path,
    model: str,
    client: Any = None,
) -> tuple[list[Signal], float]:
    """Detect new risks emerging from recent activity not yet explicitly captured."""
    lookback_days = int(thresholds.get("lookback_days", 14))

    recent_rows = warehouse.query(db_path, f"""
        SELECT event_id, type, ts,
               json_extract_string(payload, '$.description') AS description
        FROM events
        WHERE project_id = '{project_id}'
          AND type != 'signal_detected'
          AND CAST(ts AS TIMESTAMPTZ) > NOW() - INTERVAL '{lookback_days} days'
        ORDER BY ts DESC
        LIMIT 75
    """)
    if not recent_rows:
        return [], 0.0

    risk_descs = warehouse.query(db_path, f"""
        SELECT json_extract_string(payload, '$.description') AS description
        FROM events
        WHERE project_id = '{project_id}' AND type = 'risk_added'
    """)

    input_data: dict[str, Any] = {
        "project_id": project_id,
        "recent_events": recent_rows,
        "existing_risk_descriptions": [r["description"] for r in risk_descs if r["description"]],
    }
    result, cost_usd = llm_module.extract_with_cost(
        prompt_name="detect-risk-emergent",
        source_text=json.dumps(input_data, indent=2),
        source_hash=_input_hash(input_data),
        model=model,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        client=client,
    )
    return _parse_llm_signals(result, project_id, run_id, "risk"), cost_usd


def detect_tone_shift(
    db_path: Path,
    project_id: str,
    run_id: str,
    thresholds: dict[str, object],
    cache_dir: Path,
    prompts_dir: Path,
    model: str,
    client: Any = None,
) -> tuple[list[Signal], float]:
    """Detect sentiment or escalation shifts in recent stakeholder communications."""
    lookback_days = int(thresholds.get("lookback_days", 7))

    source_rows = warehouse.query(db_path, f"""
        SELECT event_id, type, ts,
               json_extract_string(payload, '$.description') AS description,
               json_extract_string(payload, '$.sender')      AS sender
        FROM events
        WHERE project_id = '{project_id}'
          AND type = 'source_ingested'
          AND json_extract_string(payload, '$.source_type') IN ('email', 'meeting')
          AND CAST(ts AS TIMESTAMPTZ) > NOW() - INTERVAL '{lookback_days} days'
        ORDER BY ts DESC
        LIMIT 30
    """)
    if not source_rows:
        return [], 0.0

    input_data: dict[str, Any] = {
        "project_id": project_id,
        "recent_source_events": source_rows,
    }
    result, cost_usd = llm_module.extract_with_cost(
        prompt_name="detect-tone-shift",
        source_text=json.dumps(input_data, indent=2),
        source_hash=_input_hash(input_data),
        model=model,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        client=client,
    )
    return _parse_llm_signals(result, project_id, run_id, "communication"), cost_usd
