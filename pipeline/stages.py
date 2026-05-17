from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_agent import events as events_api
from project_agent import git, llm, projects, render, warehouse
from project_agent.ingest import inbox, mcp as mcp_ingest
from project_agent.render import PortfolioProject
from project_agent.schemas import (
    ActionAddedPayload,
    BlockerAddedPayload,
    DecisionMadePayload,
    DeterministicActor,
    Event,
    LLMActor,
    MilestoneAddedPayload,
    RiskAddedPayload,
    RunLog,
    Signal,
    SignalDetectedPayload,
    SourceIngestedPayload,
    StageResult,
)
from project_agent.signals import deterministic

logger = logging.getLogger(__name__)


def run_ingest(
    project_id: str,
    project_dir: Path,
    events_path: Path,
    run_id: str,
) -> StageResult:
    """Stage 1 — file-drop + MCP ingest (PRD §8.1, Phase 0.5).

    MCP adapters run when GOOGLE_TOKEN_PATH is set.  OAuth failures are
    non-fatal: the stage logs a warning and continues with file-drop sources.
    """
    import os
    start = time.monotonic()
    sources_seen = 0
    sources_new = 0
    sources_from_mcp = 0

    try:
        inbox_dir = project_dir / "sources" / "_inbox"
        token_path_str = os.environ.get("GOOGLE_TOKEN_PATH", "")
        if token_path_str:
            token_path = Path(token_path_str)
            try:
                creds = mcp_ingest.build_credentials(token_path)
                for fetch_fn in (
                    mcp_ingest.fetch_gmail,
                    mcp_ingest.fetch_drive,
                    mcp_ingest.fetch_calendar,
                ):
                    fetched = fetch_fn(project_id, inbox_dir, creds)
                    sources_from_mcp += len(fetched)
            except Exception:
                logger.warning("MCP ingest failed — continuing with file-drop only")

        results = inbox.scan_inbox(project_dir)
        sources_seen = len(results)

        for result in results:
            payload = SourceIngestedPayload(
                type="source_ingested",
                filename=result.filename,
                source_type=result.source_type,
                size_bytes=result.size_bytes,
                sender=result.sender,
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
            counts={
                "sources_seen": sources_seen,
                "sources_new": sources_new,
                "sources_from_mcp": sources_from_mcp,
                "errors": 1,
            },
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(
        "Ingest complete: %d seen, %d new, %d from MCP (%dms)",
        sources_seen, sources_new, sources_from_mcp, elapsed,
    )
    return StageResult(
        name="ingest",
        status="ok",
        duration_ms=elapsed,
        counts={
            "sources_seen": sources_seen,
            "sources_new": sources_new,
            "sources_from_mcp": sources_from_mcp,
            "errors": 0,
        },
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

    payload: ActionAddedPayload | BlockerAddedPayload | RiskAddedPayload | DecisionMadePayload | MilestoneAddedPayload
    if fact_type == "action_added":
        payload = ActionAddedPayload(
            type="action_added",
            description=fact["description"],
            owner=fact.get("owner"),
            due=fact.get("due"),
            status=fact.get("status", "open"),
        )
    elif fact_type == "blocker_added":
        payload = BlockerAddedPayload(
            type="blocker_added",
            description=fact["description"],
            owner=fact.get("owner"),
            due=fact.get("due"),
        )
    elif fact_type == "risk_added":
        payload = RiskAddedPayload(
            type="risk_added",
            description=fact["description"],
            severity=fact.get("severity", "medium"),
            owner=fact.get("owner"),
            status=fact.get("status", "open"),
        )
    elif fact_type == "decision_made":
        payload = DecisionMadePayload(
            type="decision_made",
            description=fact["description"],
            rationale=fact.get("rationale"),
            decided_by=fact.get("decided_by"),
        )
    elif fact_type == "milestone_added":
        payload = MilestoneAddedPayload(
            type="milestone_added",
            description=fact["description"],
            target_date=fact.get("target_date"),
            status=fact.get("status", "open"),
            owner=fact.get("owner"),
        )
    else:
        raise ValueError(f"Unknown fact type: {fact_type!r}")

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
            for key, type_tag in [
                ("actions", "action_added"),
                ("blockers", "blocker_added"),
                ("risks", "risk_added"),
                ("decisions", "decision_made"),
                ("milestones", "milestone_added"),
            ]:
                for fact in result.get(key, []):
                    fact = {**fact, "type": type_tag}
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


# ---------------------------------------------------------------------------
# Stage 3 — Warehouse Load
# ---------------------------------------------------------------------------

def run_warehouse(
    events_path: Path,
    db_path: Path,
    run_id: str,
) -> StageResult:
    """Stage 3 — materialize events into DuckDB views (PRD §8.3, MVP)."""
    start = time.monotonic()
    events_loaded = 0

    try:
        warehouse.load(events_path, db_path)
        rows = warehouse.query(db_path, "SELECT COUNT(*) AS n FROM events_raw")
        events_loaded = int(rows[0]["n"]) if rows else 0
    except Exception:
        logger.exception("Warehouse stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="warehouse",
            status="error",
            duration_ms=elapsed,
            counts={"events_loaded": 0, "errors": 1},
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info("Warehouse complete: %d events loaded (%dms)", events_loaded, elapsed)
    return StageResult(
        name="warehouse",
        status="ok",
        duration_ms=elapsed,
        counts={"events_loaded": events_loaded, "errors": 0},
    )


# ---------------------------------------------------------------------------
# Stage 4 — Analyze (deterministic signals)
# ---------------------------------------------------------------------------

def _load_signal_thresholds(schemas_dir: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in ("action_aging", "action_unowned", "blocker_unowned"):
        path = schemas_dir / f"{name}.json"
        result[name] = dict(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}
    return result


def _signal_source_hash(signal_type: str, evidence: list[str]) -> str:
    """Stable hash encoding signal type + evidence; used as the dedup key."""
    content = f"{signal_type}:{','.join(sorted(evidence))}"
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


def run_analyze(
    project_id: str,
    db_path: Path,
    events_path: Path,
    run_id: str,
    schemas_dir: Path | None = None,
) -> tuple[StageResult, list[Signal]]:
    """Stage 4 — deterministic signal detection (PRD §8.4, MVP).

    Returns (StageResult, signals) so the render stage can consume signals
    without re-reading the events log.
    """
    if schemas_dir is None:
        schemas_dir = Path("data/schemas/signals")

    start = time.monotonic()
    signals_new = 0
    signals_skipped = 0
    errors = 0
    emitted: list[Signal] = []

    try:
        thresholds = _load_signal_thresholds(schemas_dir)
        # Dedup key: source_hash encodes signal_type + sorted evidence, stable across runs
        seen_source_hashes = {
            e.source_hash
            for e in events_api.query(events_path, types=["signal_detected"])
        }

        all_signals: list[Signal] = []
        for detector_fn, key in [
            (deterministic.detect_action_aging, "action_aging"),
            (deterministic.detect_action_unowned, "action_unowned"),
            (deterministic.detect_blocker_unowned, "blocker_unowned"),
        ]:
            try:
                all_signals.extend(detector_fn(db_path, project_id, run_id, thresholds.get(key, {})))
            except Exception:
                logger.exception("Detector %s failed", key)
                errors += 1

        for signal in all_signals:
            source_hash = _signal_source_hash(signal.type, signal.evidence)

            if source_hash in seen_source_hashes:
                signals_skipped += 1
                continue

            payload = SignalDetectedPayload(
                type="signal_detected",
                signal_id=signal.signal_id,
                signal_type=signal.type,
                category=signal.category,
                severity=signal.severity,
                confidence=signal.confidence,
                evidence=signal.evidence,
                method=signal.method,
                rationale=signal.rationale,
                recommended_action=signal.recommended_action,
            )
            event = Event(
                event_id=str(uuid.uuid4()),
                ts=datetime.now(tz=timezone.utc),
                run_id=run_id,
                project_id=project_id,
                type="signal_detected",
                actor=DeterministicActor(kind="deterministic", detector=signal.type),
                source_ref="derived/signals",
                source_hash=source_hash,
                payload=payload,
                hash=events_api.hash_event(payload.model_dump(), source_hash),
                confidence=signal.confidence,
            )
            events_api.append(event, events_path)
            seen_source_hashes.add(source_hash)
            emitted.append(signal)
            signals_new += 1

    except Exception:
        logger.exception("Analyze stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="analyze",
            status="error",
            duration_ms=elapsed,
            counts={"signals_new": 0, "signals_skipped": 0, "errors": errors + 1},
            error="See logs for details",
        ), []

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(
        "Analyze complete: %d new signals, %d skipped (%dms)",
        signals_new, signals_skipped, elapsed,
    )
    return StageResult(
        name="analyze",
        status="ok",
        duration_ms=elapsed,
        counts={"signals_new": signals_new, "signals_skipped": signals_skipped, "errors": errors},
    ), emitted


# ---------------------------------------------------------------------------
# Stage 5 — Render
# ---------------------------------------------------------------------------

def run_render(
    project_id: str,
    project_dir: Path,
    signals: list[Signal],
    run_id: str,
    design_system_dir: Path | None = None,
) -> StageResult:
    """Stage 5 — render MD + HTML status reports (PRD §8.5, Phase 0.5)."""
    start = time.monotonic()
    try:
        render.render_status(project_dir, signals, run_id)
        render.render_html(project_dir, signals, run_id, design_system_dir=design_system_dir)
        logger.info("Render complete: %s (MD + HTML)", project_id)
    except Exception:
        logger.exception("Render stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="render",
            status="error",
            duration_ms=elapsed,
            counts={"reports_written": 0, "errors": 1},
            error="See logs for details",
        )
    elapsed = int((time.monotonic() - start) * 1000)
    return StageResult(
        name="render",
        status="ok",
        duration_ms=elapsed,
        counts={"reports_written": 2, "errors": 0},
    )


def run_render_portfolio(
    project_summaries: list[PortfolioProject],
    output_dir: Path,
    design_system_dir: Path | None = None,
) -> StageResult:
    """Stage 5 (portfolio) — render the cross-project HTML dashboard."""
    start = time.monotonic()
    try:
        html_path = render.render_portfolio_html(
            project_summaries, output_dir, design_system_dir=design_system_dir
        )
        logger.info("Portfolio HTML written: %s", html_path)
    except Exception:
        logger.exception("Portfolio render stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="render_portfolio",
            status="error",
            duration_ms=elapsed,
            counts={"reports_written": 0, "errors": 1},
            error="See logs for details",
        )
    elapsed = int((time.monotonic() - start) * 1000)
    return StageResult(
        name="render_portfolio",
        status="ok",
        duration_ms=elapsed,
        counts={"reports_written": 1, "errors": 0},
    )


# ---------------------------------------------------------------------------
# Stage 6 — Commit + PR
# ---------------------------------------------------------------------------

def run_commit(
    project_id: str,
    project_dir: Path,
    run_log: RunLog,
    repo_root: Path,
) -> StageResult:
    """Stage 6 — branch, commit changed files, open GitHub PR (PRD §8.6, MVP).

    Set DRY_RUN=1 to skip the actual git operations (useful in tests).
    """
    import os
    start = time.monotonic()

    if os.environ.get("DRY_RUN") == "1":
        elapsed = int((time.monotonic() - start) * 1000)
        logger.info("DRY_RUN=1 — skipping commit stage")
        return StageResult(
            name="commit",
            status="skipped",
            duration_ms=elapsed,
            counts={"errors": 0},
        )

    try:
        from datetime import date
        branch_name = f"auto/{date.today().isoformat()}"
        git.branch(repo_root, branch_name)

        signals_new = sum(
            s.counts.get("signals_new", 0)
            for s in run_log.stages
            if s.name == "analyze"
        )
        commit_msg = (
            f"chore(agent): run {run_log.run_id} — {signals_new} signal(s)\n\n"
            f"Project: {project_id}\n"
            f"Stages: {', '.join(s.name for s in run_log.stages)}"
        )
        git.commit_all(repo_root, commit_msg, force_paths=[
            project_dir / "events.ndjson",
            project_dir / "project.md",
            project_dir / "reports",
            project_dir / "sources" / "manifest.json",
            project_dir / "sources" / "parse_manifest.json",
        ])

        pr_body = (
            f"## Summary\n\n"
            f"- Run ID: `{run_log.run_id}`\n"
            f"- Project: `{project_id}`\n"
            f"- Signals detected: {signals_new}\n\n"
            f"## Stages\n\n"
            + "\n".join(
                f"- **{s.name}**: {s.status} {s.counts}"
                for s in run_log.stages
            )
            + "\n\n🤖 Generated with [Project Agent](https://github.com/danielmschaves/project-agent)"
        )
        pr_url = git.open_pr(repo_root, f"[agent] {project_id} — run {run_log.run_id}", pr_body)

    except Exception:
        logger.exception("Commit stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="commit",
            status="error",
            duration_ms=elapsed,
            counts={"errors": 1},
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info("Commit stage complete: %s", pr_url or "(dry-run)")
    return StageResult(
        name="commit",
        status="ok",
        duration_ms=elapsed,
        counts={"errors": 0},
    )


def run_commit_portfolio(
    project_results: list[tuple[str, RunLog]],
    repo_root: Path,
) -> StageResult:
    """Stage 6 (portfolio) — one branch + PR covering all projects (PRD §13.8).

    Set DRY_RUN=1 to skip actual git operations (useful in tests).
    """
    import os
    start = time.monotonic()

    if os.environ.get("DRY_RUN") == "1":
        elapsed = int((time.monotonic() - start) * 1000)
        logger.info("DRY_RUN=1 — skipping portfolio commit stage")
        return StageResult(
            name="commit",
            status="skipped",
            duration_ms=elapsed,
            counts={"errors": 0},
        )

    try:
        from datetime import date
        today = date.today().isoformat()
        branch_name = f"auto/{today}"
        git.branch(repo_root, branch_name)

        total_signals = sum(
            sum(s.counts.get("signals_new", 0) for s in run_log.stages if s.name == "analyze")
            for _, run_log in project_results
        )
        commit_msg = (
            f"chore(agent): portfolio {today} — {len(project_results)} project(s), "
            f"{total_signals} signal(s)\n\n"
            f"Projects: {', '.join(pid for pid, _ in project_results)}"
        )
        projects_root = repo_root / "projects"
        force: list[Path] = [repo_root / "reports"]
        for pid, _ in project_results:
            pd = projects_root / pid
            force += [
                pd / "events.ndjson",
                pd / "project.md",
                pd / "reports",
                pd / "sources" / "manifest.json",
                pd / "sources" / "parse_manifest.json",
            ]
        git.commit_all(repo_root, commit_msg, force_paths=force)

        pr_body = _build_portfolio_pr_body(project_results, today)
        pr_title = (
            f"[agent] portfolio — {today} — {len(project_results)} project(s)"
        )
        pr_url = git.open_pr(repo_root, pr_title, pr_body)

    except Exception:
        logger.exception("Portfolio commit stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="commit",
            status="error",
            duration_ms=elapsed,
            counts={"errors": 1},
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info("Portfolio commit complete: %s", pr_url or "(dry-run)")
    return StageResult(
        name="commit",
        status="ok",
        duration_ms=elapsed,
        counts={"projects": len(project_results), "errors": 0},
    )


def _build_portfolio_pr_body(
    project_results: list[tuple[str, RunLog]],
    today: str,
) -> str:
    rows = []
    for project_id, run_log in project_results:
        signals_new = sum(
            s.counts.get("signals_new", 0) for s in run_log.stages if s.name == "analyze"
        )
        errors = sum(s.counts.get("errors", 0) for s in run_log.stages)
        stage_statuses = {s.name: s.status for s in run_log.stages}
        overall = "✓ ok" if errors == 0 else "✗ error"
        rows.append((project_id, overall, signals_new, errors, run_log, stage_statuses))

    table_lines = [
        "| Project | Status | New Signals | Errors |",
        "|---|---|---|---|",
    ]
    for project_id, overall, signals_new, errors, _, _ in rows:
        table_lines.append(f"| {project_id} | {overall} | {signals_new} | {errors} |")

    detail_sections = []
    for project_id, overall, signals_new, errors, run_log, stage_statuses in rows:
        signals_skipped = sum(
            s.counts.get("signals_skipped", 0) for s in run_log.stages if s.name == "analyze"
        )
        stage_summary = ", ".join(
            f"{name} {'✓' if status == 'ok' else ('−' if status == 'skipped' else '✗')}"
            for name, status in stage_statuses.items()
        )
        detail_sections.append(
            f"### {project_id}\n"
            f"- Run ID: `{run_log.run_id}`\n"
            f"- Stages: {stage_summary}\n"
            f"- Signals: {signals_new} new, {signals_skipped} skipped"
        )

    body = (
        f"## Portfolio Run — {today}\n\n"
        + "\n".join(table_lines)
        + "\n\n"
        + "\n\n".join(detail_sections)
        + "\n\n🤖 Generated with [Project Agent](https://github.com/danielmschaves/project-agent)"
    )
    return body
