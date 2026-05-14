from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_agent import events as events_api
from project_agent import llm, projects
from project_agent.ingest import inbox
from project_agent.schemas import (
    ActionAddedPayload,
    BlockerAddedPayload,
    DeterministicActor,
    Event,
    LLMActor,
    SourceIngestedPayload,
    StageResult,
)

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


# ---------------------------------------------------------------------------
# Stage 2 — Parse
# ---------------------------------------------------------------------------

def _load_parse_manifest(path: Path) -> dict[str, list[str]]:
    if path.exists():
        return dict(json.loads(path.read_text(encoding="utf-8")))
    return {}


def _save_parse_manifest(path: Path, manifest: dict[str, list[str]]) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _make_fact_event(
    fact: dict[str, Any],
    src_event: Event,
    project_id: str,
    run_id: str,
    prompt_name: str,
    prompt_version: int,
    model: str,
) -> tuple[str, Event]:
    event_id = str(uuid.uuid4())
    actor = LLMActor(kind="llm", model=model, prompt=f"{prompt_name}@{prompt_version}")
    fact_type = fact["type"]

    if fact_type == "action_added":
        payload: ActionAddedPayload | BlockerAddedPayload = ActionAddedPayload(
            type="action_added",
            description=fact["description"],
            owner=fact.get("owner"),
            due=fact.get("due"),
            status=fact.get("status", "open"),
        )
    else:
        payload = BlockerAddedPayload(
            type="blocker_added",
            description=fact["description"],
            owner=fact.get("owner"),
            due=fact.get("due"),
        )

    event = Event(
        event_id=event_id,
        ts=datetime.now(tz=timezone.utc),
        run_id=run_id,
        project_id=project_id,
        type=fact_type,
        actor=actor,
        source_ref=src_event.source_ref,
        source_hash=src_event.source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), src_event.source_hash),
        confidence=fact.get("confidence"),
    )
    return event_id, event


def run_parse(
    project_id: str,
    project_dir: Path,
    events_path: Path,
    cache_dir: Path,
    prompts_dir: Path,
    run_id: str,
    model: str = "claude-sonnet-4-6",
    client: Any = None,
) -> StageResult:
    """Stage 2 — parse source files into structured events (PRD §8.2, MVP)."""
    start = time.monotonic()
    sources_parsed = 0
    sources_skipped = 0
    events_emitted = 0
    errors = 0
    prompt_name = "extract-context"

    try:
        parse_manifest_path = project_dir / "sources" / "parse_manifest.json"
        parse_manifest = _load_parse_manifest(parse_manifest_path)

        _, prompt_version = llm.load_prompt(prompt_name, prompts_dir)
        manifest_key_suffix = f"{prompt_name}@{prompt_version}"

        source_events = events_api.query(
            events_path, project_id=project_id, types=["source_ingested"]
        )
        all_facts: list[dict[str, Any]] = []

        for src_event in source_events:
            src_key = f"{src_event.source_hash}:{manifest_key_suffix}"
            if src_key in parse_manifest:
                sources_skipped += 1
                continue

            source_file = project_dir / src_event.source_ref
            if not source_file.exists():
                logger.warning("Source file missing: %s", source_file)
                errors += 1
                continue

            source_text = source_file.read_text(encoding="utf-8", errors="replace")

            result = llm.extract(
                prompt_name=prompt_name,
                source_text=source_text,
                source_hash=src_event.source_hash,
                model=model,
                cache_dir=cache_dir,
                prompts_dir=prompts_dir,
                client=client,
            )

            emitted_ids: list[str] = []
            for fact in result.get("actions", []):
                fact = {**fact, "type": "action_added"}
                eid, evt = _make_fact_event(fact, src_event, project_id, run_id, prompt_name, prompt_version, model)
                events_api.append(evt, events_path)
                emitted_ids.append(eid)
                events_emitted += 1
                all_facts.append(fact)

            for fact in result.get("blockers", []):
                fact = {**fact, "type": "blocker_added"}
                eid, evt = _make_fact_event(fact, src_event, project_id, run_id, prompt_name, prompt_version, model)
                events_api.append(evt, events_path)
                emitted_ids.append(eid)
                events_emitted += 1
                all_facts.append(fact)

            parse_manifest[src_key] = emitted_ids
            _save_parse_manifest(parse_manifest_path, parse_manifest)
            sources_parsed += 1

        if all_facts:
            projects.update_project(project_dir, all_facts)

    except Exception:
        logger.exception("Parse stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="parse",
            status="error",
            duration_ms=elapsed,
            counts={
                "sources_parsed": sources_parsed,
                "sources_skipped": sources_skipped,
                "events_emitted": events_emitted,
                "errors": errors + 1,
            },
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(
        "Parse complete: %d parsed, %d skipped, %d events (%dms)",
        sources_parsed,
        sources_skipped,
        events_emitted,
        elapsed,
    )
    return StageResult(
        name="parse",
        status="ok",
        duration_ms=elapsed,
        counts={
            "sources_parsed": sources_parsed,
            "sources_skipped": sources_skipped,
            "events_emitted": events_emitted,
            "errors": errors,
        },
    )
