from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from project_agent.schemas import Event

logger = logging.getLogger(__name__)


def hash_event(payload_dict: dict, source_hash: str) -> str:
    """Stable content hash for an event (PRD §6.2, secondary dedup defense)."""
    canonical = json.dumps(payload_dict, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256((canonical + source_hash).encode()).hexdigest()
    return f"sha256:{digest}"


def append(event: Event, events_path: Path) -> None:
    """Append one event as a NDJSON line. The only write path for events.ndjson."""
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(event.model_dump_json() + "\n")
    logger.debug("Appended event %s (%s)", event.event_id, event.type)


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
    """Return events matching the given filters, in append order."""
    results: list[Event] = []

    if not events_path.exists():
        return results

    with events_path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue

            event = Event.model_validate_json(raw)

            if project_id is not None and event.project_id != project_id:
                continue
            if types is not None and event.type not in types:
                continue
            if since is not None and event.ts < since:
                continue
            if until is not None and event.ts > until:
                continue
            if not include_retracted and event.retracted:
                continue
            if not include_superseded and event.superseded_by is not None:
                continue

            results.append(event)

    return results
