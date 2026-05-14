from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from project_agent import events as events_api
from project_agent.ingest import inbox
from project_agent.schemas import DeterministicActor, Event, SourceIngestedPayload, StageResult

logger = logging.getLogger(__name__)


def run_ingest(
    project_id: str,
    project_dir: Path,
    events_path: Path,
    run_id: str,
) -> StageResult:
    """Stage 1 — file-drop ingest (PRD §8.1, MVP)."""
    start = time.monotonic()
    sources_seen = 0
    sources_new = 0

    try:
        results = inbox.scan_inbox(project_dir)
        sources_seen = len(results)

        for result in results:
            payload = SourceIngestedPayload(
                type="source_ingested",
                filename=result.filename,
                source_type=result.source_type,
                size_bytes=result.size_bytes,
            )
            event = Event(
                event_id=str(uuid.uuid4()),
                ts=datetime.now(tz=timezone.utc),
                run_id=run_id,
                project_id=project_id,
                type="source_ingested",
                actor=DeterministicActor(kind="deterministic", detector="inbox"),
                source_ref=result.source_ref,
                source_hash=result.source_hash,
                payload=payload,
                hash=events_api.hash_event(payload.model_dump(), result.source_hash),
            )
            events_api.append(event, events_path)
            sources_new += 1

    except Exception:
        logger.exception("Ingest stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="ingest",
            status="error",
            duration_ms=elapsed,
            counts={"sources_seen": sources_seen, "sources_new": sources_new, "errors": 1},
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(
        "Ingest complete: %d seen, %d new (%dms)", sources_seen, sources_new, elapsed
    )
    return StageResult(
        name="ingest",
        status="ok",
        duration_ms=elapsed,
        counts={"sources_seen": sources_seen, "sources_new": sources_new, "errors": 0},
    )
