from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from project_agent.schemas import Actor, Event, EventRetractedPayload

logger = logging.getLogger(__name__)


def hash_event(payload_dict: dict[str, Any], source_hash: str) -> str:
    """Stable content hash for an event (PRD §6.2, secondary dedup defense)."""
    canonical = json.dumps(payload_dict, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256((canonical + source_hash).encode()).hexdigest()
    return f"sha256:{digest}"


def append(event: Event, events_path: Path) -> None:
    """Append one event as a NDJSON line. The only write path for events.ndjson."""
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(event.model_dump_json() + "\n")
    logger.debug("Appended event %s (%s)", event.event_id, event.type)


def retract(
    events_path: Path,
    *,
    event_id: str,
    reason: str,
    run_id: str,
    project_id: str,
    actor: Actor,
    ts: datetime | None = None,
) -> Event:
    """Append an `event_retracted` event marking `event_id` as bad data.

    This is the only supported way to correct the log (PRD §6.2): the target
    line is never rewritten, and `query()` hides the target from then on.
    """
    payload = EventRetractedPayload(
        type="event_retracted", retracted_event_id=event_id, reason=reason
    )
    source_ref = "derived/retraction"
    source_hash = hash_event(payload.model_dump(), source_ref)
    event = Event(
        event_id=str(uuid.uuid4()),
        ts=ts or datetime.now(timezone.utc),
        run_id=run_id,
        project_id=project_id,
        type="event_retracted",
        actor=actor,
        source_ref=source_ref,
        source_hash=source_hash,
        payload=payload,
        hash=hash_event(payload.model_dump(), source_hash),
    )
    append(event, events_path)
    logger.info("Retracted event %s: %s", event_id, reason)
    return event


class Corrections(BaseModel):
    """Retractions and supersessions implied by later appends to the log.

    Both corrections are recorded on the *correcting* event, never written
    back onto the corrected one, so they have to be resolved by reading the
    whole log. `query()` and `warehouse.load()` must agree on the result —
    otherwise SQL views would still show data the Python API hides.
    """

    retracted: dict[str, str] = Field(default_factory=dict)  # event_id -> reason
    superseded_by: dict[str, str] = Field(default_factory=dict)  # old id -> superseding id


def resolve_corrections(all_events: list[Event]) -> Corrections:
    """Collect the retraction and supersession chains across a whole log."""
    corrections = Corrections()
    for event in all_events:
        if isinstance(event.payload, EventRetractedPayload):
            corrections.retracted[event.payload.retracted_event_id] = event.payload.reason
        for old_id in event.supersedes:
            corrections.superseded_by[old_id] = event.event_id
    return corrections


def read_all(events_path: Path) -> list[Event]:
    """Parse every line of the log, unfiltered."""
    if not events_path.exists():
        return []
    with events_path.open(encoding="utf-8") as fh:
        return [Event.model_validate_json(line) for line in (raw.strip() for raw in fh) if line]


def query(
    events_path: Path,
    *,
    project_id: str | None = None,
    types: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    include_retracted: bool = False,
    include_superseded: bool = False,
) -> list[Event]:
    """Return events matching the given filters, in append order.

    Retraction and supersession are resolved across the whole log, not just
    from the target event's own flags: an `event_retracted` event hides its
    target, and an event listing `supersedes: [X]` hides X. Both corrections
    arrive as later appends, so the target line itself is never updated.
    """
    all_events = read_all(events_path)
    corrections = resolve_corrections(all_events)

    results: list[Event] = []
    for event in all_events:
        if project_id is not None and event.project_id != project_id:
            continue
        if types is not None and event.type not in types:
            continue
        if since is not None and event.ts < since:
            continue
        if until is not None and event.ts > until:
            continue
        if not include_retracted and (event.retracted or event.event_id in corrections.retracted):
            continue
        if not include_superseded and (
            event.superseded_by is not None or event.event_id in corrections.superseded_by
        ):
            continue

        results.append(event)

    return results
