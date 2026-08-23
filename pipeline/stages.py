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
from project_agent import git, lint as lint_api, llm, projects, render, warehouse, wiki
from project_agent.ingest import inbox
from project_agent.render import PortfolioProject
from project_agent.schemas import (
    ActionAddedPayload,
    BlockerAddedPayload,
    DecisionMadePayload,
    DeterministicActor,
    Event,
    LLMActor,
    MilestoneAddedPayload,
    PipelineHealthPayload,
    RiskAddedPayload,
    RunLog,
    Signal,
    SignalDetectedPayload,
    SourceIngestedPayload,
    SourceSummarizedPayload,
    ConceptAddedPayload,
    StageResult,
)
from project_agent.signals import deterministic
from project_agent.signals import llm as llm_signals

logger = logging.getLogger(__name__)


def run_ingest(
    project_id: str,
    project_dir: Path,
    events_path: Path,
    run_id: str,
) -> StageResult:
    """Stage 1 — file-drop ingest (PRD §8.1).

    Everything arrives the same way: as a file in `raw/_inbox/`. Fetching from
    Gmail, Drive or Calendar is Claude Code's job through its MCP connectors —
    the pipeline no longer holds OAuth credentials of its own. Only the fetch
    moved; classification, hashing and dedup are unchanged.

    Files dropped by the agent should keep the naming convention the retired
    adapters used, because the date prefix is what preserves ordering and the
    extension is what drives classification:

        <YYYY-MM-DD>-gmail-<id>.eml
        <YYYY-MM-DD>-drive-<slug>.md
        <YYYY-MM-DD>-cal-<slug>.md
    """
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
                "errors": 1,
            },
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info("Ingest complete: %d seen, %d new (%dms)", sources_seen, sources_new, elapsed)
    return StageResult(
        name="ingest",
        status="ok",
        duration_ms=elapsed,
        counts={
            "sources_seen": sources_seen,
            "sources_new": sources_new,
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
        parse_manifest_path = project_dir / "raw" / "parse_manifest.json"
        parse_manifest = _load_parse_manifest(parse_manifest_path)

        prompt_version = llm.load_prompt(prompt_name, prompts_dir).version
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
# Stage 2b — Compile (PRD §8.2b, v2.0): events -> the wiki
# ---------------------------------------------------------------------------

def run_compile(
    project_id: str,
    project_dir: Path,
    events_path: Path,
    run_id: str,
    vault_dir: Path | None = None,
    cache_dir: Path | None = None,
    prompts_dir: Path | None = None,
    model: str = "claude-sonnet-4-6",
    config_dir: Path | None = None,
    llm_client: Any = None,
    prose_enabled: bool = True,
) -> StageResult:
    """Phase A — compile one project's articles from its event log.

    Deterministic: articles are pure projections of events plus the PM's
    declared metadata, so a re-run with no new events rewrites nothing.
    Shared people/clients/concepts are left to run_compile_shared, which
    runs once after every project has been compiled.
    """
    start = time.monotonic()
    vault = vault_dir if vault_dir is not None else wiki.default_vault_dir(project_dir)
    if cache_dir is None:
        cache_dir = Path("data/cache/llm")
    if prompts_dir is None:
        prompts_dir = Path("prompts")
    if config_dir is None:
        config_dir = Path("config")

    written = 0
    unchanged = 0
    removed = 0
    prose: wiki.ProseWriter | None = None

    try:
        events = events_api.query(events_path, project_id=project_id)
        metadata = projects.load_notes_metadata(project_dir)
        registries = wiki.Registries.load(vault)

        if prose_enabled:
            prose = wiki.ProseWriter(
                cache_dir=cache_dir,
                prompts_dir=prompts_dir,
                model=model,
                budget=wiki.ProseBudget(limit_usd=_load_llm_budget(config_dir)),
                client=llm_client,
            )

        articles = wiki.compile_project(
            vault, project_id, events, metadata, registries, prose
        )

        manifest_path = project_dir / "raw" / "compile_manifest.json"
        manifest: dict[str, wiki.CompileRecord] = {}
        for article in articles:
            if wiki.write_article(vault, article):
                written += 1
            else:
                unchanged += 1
            manifest[article.path.as_posix()] = wiki.CompileRecord(
                input_hash=article.compiled_from,
                compiled_by=article.compiled_by,
                event_ids=sorted(article.event_ids),
            )

        keep = {a.path for a in articles}
        removed = len(wiki.prune(vault, Path("projects") / project_id, keep))
        wiki.save_compile_manifest(manifest_path, manifest)

    except Exception:
        logger.exception("Compile stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="compile",
            status="error",
            duration_ms=elapsed,
            counts={"articles_written": written, "errors": 1},
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(
        "Compile complete: %d written, %d unchanged, %d pruned (%dms)",
        written, unchanged, removed, elapsed,
    )
    if prose is not None and prose.skipped:
        logger.warning(
            "Prose budget exhausted — %d article(s) fell back to the template",
            prose.skipped,
        )
    return StageResult(
        name="compile",
        status="ok",
        duration_ms=elapsed,
        counts={
            "articles_written": written,
            "articles_unchanged": unchanged,
            "articles_pruned": removed,
            "prose_written": prose.calls if prose else 0,
            "prose_skipped": prose.skipped if prose else 0,
            "errors": 0,
        },
        cost_usd=(prose.budget.spent_usd if prose and prose.budget.spent_usd else None),
    )


def run_compile_shared(
    vault_dir: Path,
    cache_dir: Path | None = None,
    prompts_dir: Path | None = None,
    model: str = "claude-sonnet-4-6",
    config_dir: Path | None = None,
    llm_client: Any = None,
    prose_enabled: bool = True,
    research_events_path: Path | None = None,
) -> StageResult:
    """Phase B — compile the shared layer once, after every project.

    people/, clients/ and concepts/ fan in across projects, so they cannot be
    written inside the per-project loop. A single-project run therefore leaves
    them untouched; `pipeline portfolio` is the full compile.
    """
    start = time.monotonic()
    written = 0
    unchanged = 0
    prose: wiki.ProseWriter | None = None

    try:
        registries = wiki.Registries.load(vault_dir)
        if prose_enabled:
            prose = wiki.ProseWriter(
                cache_dir=cache_dir or Path("data/cache/llm"),
                prompts_dir=prompts_dir or Path("prompts"),
                model=model,
                budget=wiki.ProseBudget(limit_usd=_load_llm_budget(config_dir or Path("config"))),
                client=llm_client,
            )
        extra_concepts: dict[str, wiki.ConceptRef] = {}
        if research_events_path is not None and research_events_path.exists():
            extra_concepts = wiki.research_concepts(
                events_api.query(research_events_path, project_id=wiki.RESEARCH_ID)
            )
        articles = wiki.compile_shared(vault_dir, registries, prose, extra_concepts)
        for article in articles:
            if wiki.write_article(vault_dir, article):
                written += 1
            else:
                unchanged += 1

        keep = {a.path for a in articles}
        for subtree in (Path("people"), Path("clients"), Path("concepts")):
            wiki.prune(vault_dir, subtree, keep)

    except Exception:
        logger.exception("Shared compile stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="compile_shared",
            status="error",
            duration_ms=elapsed,
            counts={"articles_written": written, "errors": 1},
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    return StageResult(
        name="compile_shared",
        status="ok",
        duration_ms=elapsed,
        counts={
            "articles_written": written,
            "articles_unchanged": unchanged,
            "prose_written": prose.calls if prose else 0,
            "errors": 0,
        },
        cost_usd=(prose.budget.spent_usd if prose and prose.budget.spent_usd else None),
    )


# ---------------------------------------------------------------------------
# Research lane (v2.0) — ingest + summarize + compile, sharing the concept layer
# ---------------------------------------------------------------------------

def run_research(
    research_dir: Path,
    events_path: Path,
    run_id: str,
    vault_dir: Path,
    cache_dir: Path | None = None,
    prompts_dir: Path | None = None,
    model: str = "claude-sonnet-4-6",
    client: Any = None,
) -> StageResult:
    """Ingest, summarize and compile the research corpus.

    Runs the same file-drop ingest as a project — drop clippings into
    research/raw/_inbox/ — but a different parse prompt and its own event
    types. Research documents are deliberately never passed to the project
    signal detectors: a paper is not a delivery risk.
    """
    start = time.monotonic()
    if cache_dir is None:
        cache_dir = Path("data/cache/llm")
    if prompts_dir is None:
        prompts_dir = Path("prompts")

    prompt_name = "summarize-research"
    sources_new = 0
    sources_parsed = 0
    concepts_found = 0
    articles_written = 0
    errors = 0

    try:
        for result in inbox.scan_inbox(research_dir):
            payload = SourceIngestedPayload(
                type="source_ingested",
                filename=result.filename,
                source_type=result.source_type,
                size_bytes=result.size_bytes,
                sender=result.sender,
            )
            events_api.append(
                Event(
                    event_id=str(uuid.uuid4()),
                    ts=datetime.now(tz=timezone.utc),
                    run_id=run_id,
                    project_id=wiki.RESEARCH_ID,
                    type="source_ingested",
                    actor=DeterministicActor(kind="deterministic", detector="inbox"),
                    source_ref=result.source_ref,
                    source_hash=result.source_hash,
                    payload=payload,
                    hash=events_api.hash_event(payload.model_dump(), result.source_hash),
                ),
                events_path,
            )
            sources_new += 1

        manifest_path = research_dir / "raw" / "parse_manifest.json"
        manifest = _load_parse_manifest(manifest_path)
        prompt_version = llm.load_prompt(prompt_name, prompts_dir).version
        key_suffix = f"{prompt_name}@{prompt_version}"

        for src in events_api.query(
            events_path, project_id=wiki.RESEARCH_ID, types=["source_ingested"]
        ):
            key = f"{src.source_hash}:{key_suffix}"
            if key in manifest:
                continue
            source_file = research_dir / src.source_ref
            if not source_file.exists():
                logger.warning("Research source missing: %s", source_file)
                errors += 1
                continue

            result_json = llm.extract(
                prompt_name=prompt_name,
                source_text=source_file.read_text(encoding="utf-8", errors="replace"),
                source_hash=src.source_hash,
                model=model,
                cache_dir=cache_dir,
                prompts_dir=prompts_dir,
                client=client,
            )
            emitted = _emit_research_events(
                result_json, src, run_id, model, prompt_name, prompt_version, events_path
            )
            concepts_found += emitted - 1
            manifest[key] = []
            _save_parse_manifest(manifest_path, manifest)
            sources_parsed += 1

        events = events_api.query(events_path, project_id=wiki.RESEARCH_ID)
        registries = wiki.Registries.load(vault_dir)
        articles = wiki.compile_research(vault_dir, events, registries)
        for article in articles:
            if wiki.write_article(vault_dir, article):
                articles_written += 1
        wiki.prune(vault_dir, Path("research"), {a.path for a in articles})

    except Exception:
        logger.exception("Research stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return StageResult(
            name="research",
            status="error",
            duration_ms=elapsed,
            counts={"sources_new": sources_new, "errors": 1},
            error="See logs for details",
        )

    elapsed = int((time.monotonic() - start) * 1000)
    return StageResult(
        name="research",
        status="ok",
        duration_ms=elapsed,
        counts={
            "sources_new": sources_new,
            "sources_parsed": sources_parsed,
            "concepts_found": concepts_found,
            "articles_written": articles_written,
            "errors": errors,
        },
    )


def _emit_research_events(
    result: dict[str, Any],
    src: Event,
    run_id: str,
    model: str,
    prompt_name: str,
    prompt_version: int,
    events_path: Path,
) -> int:
    """Append the summary and concept events for one research document."""
    actor = LLMActor(kind="llm", model=model, prompt=f"{prompt_name}@{prompt_version}")

    def _append(payload: Any) -> None:
        events_api.append(
            Event(
                event_id=str(uuid.uuid4()),
                ts=datetime.now(tz=timezone.utc),
                run_id=run_id,
                project_id=wiki.RESEARCH_ID,
                type=payload.type,
                actor=actor,
                source_ref=src.source_ref,
                source_hash=src.source_hash,
                payload=payload,
                hash=events_api.hash_event(payload.model_dump(), src.source_hash),
            ),
            events_path,
        )

    _append(SourceSummarizedPayload(
        type="source_summarized",
        summary=str(result.get("summary", "")),
        topics=[str(t) for t in result.get("topics", [])],
    ))
    count = 1
    for concept in result.get("concepts", []):
        name = str(concept.get("name", "")).strip()
        if not name:
            continue
        _append(ConceptAddedPayload(
            type="concept_added",
            slug=wiki.slugify(name, max_words=5),
            name=name,
            description=concept.get("description"),
        ))
        count += 1
    return count

# ---------------------------------------------------------------------------
# Wiki lint (v2.0) — structural health checks over the vault
# ---------------------------------------------------------------------------

def run_lint(
    vault_dir: Path,
    projects_dir: Path,
    run_id: str,
    emit_events: bool = True,
    review: bool = False,
    cache_dir: Path | None = None,
    prompts_dir: Path | None = None,
    model: str = "claude-sonnet-4-6",
    llm_client: Any = None,
) -> tuple[StageResult, lint_api.Report]:
    """Check the vault for drift and report unresolved entities.

    Findings are summarized into one pipeline_health event per project rather
    than one per finding, and deduped on the content of the findings, so a run
    that changes nothing appends nothing.
    """
    start = time.monotonic()

    try:
        project_ids = sorted(
            d.name for d in projects_dir.iterdir()
            if d.is_dir() and (d / "project.notes.md").exists()
        ) if projects_dir.exists() else []

        events: list[Event] = []
        manifests: list[Path] = []
        for project_id in project_ids:
            project_dir = projects_dir / project_id
            events += events_api.query(project_dir / "events.ndjson")
            manifest = project_dir / "raw" / "compile_manifest.json"
            if manifest.exists():
                manifests.append(manifest)

        report = lint_api.lint_vault(vault_dir, events, manifests)

        if emit_events and project_ids:
            _emit_lint_health(report, project_ids, projects_dir, run_id)

        if review:
            _write_lint_review(
                vault_dir, report,
                cache_dir or Path("data/cache/llm"),
                prompts_dir or Path("prompts"),
                model, llm_client,
            )

    except Exception:
        logger.exception("Lint stage failed")
        elapsed = int((time.monotonic() - start) * 1000)
        return (
            StageResult(
                name="lint",
                status="error",
                duration_ms=elapsed,
                counts={"errors": 1},
                error="See logs for details",
            ),
            lint_api.Report(),
        )

    elapsed = int((time.monotonic() - start) * 1000)
    counts = {check: len(items) for check, items in report.by_check().items()}
    return (
        StageResult(
            name="lint",
            status="ok",
            duration_ms=elapsed,
            counts={
                **counts,
                "findings": len(report.findings),
                "errors": len(report.errors),
            },
        ),
        report,
    )


def _write_lint_review(
    vault_dir: Path,
    report: lint_api.Report,
    cache_dir: Path,
    prompts_dir: Path,
    model: str,
    llm_client: Any,
) -> None:
    """Ask the model what the structural checks could not see, file the answer.

    The review lands in kb/outputs/, which is the human-owned lane — it is a
    dated artifact of one review, not a compiled article, so it is exempt from
    the zero-diff contract.
    """
    inventory = lint_api.vault_inventory(vault_dir, report)
    digest = json.dumps(inventory, sort_keys=True, ensure_ascii=False)
    inventory_hash = wiki.input_hash(inventory)

    try:
        result = llm.extract(
            "lint-wiki", digest, inventory_hash, model, cache_dir, prompts_dir, llm_client
        )
    except Exception:
        logger.warning("Wiki review failed — skipping (structural findings still reported)")
        return

    observations = result.get("observations", [])
    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    outputs = vault_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / f"lint-{date_str}.md").write_text(
        lint_api.render_review(observations, date_str), encoding="utf-8"
    )
    logger.info("Wiki review written: %d observation(s)", len(observations))


def _emit_lint_health(
    report: lint_api.Report,
    project_ids: list[str],
    projects_dir: Path,
    run_id: str,
) -> None:
    """One deduped pipeline_health event per project with findings."""
    actionable = [f for f in report.findings if f.severity in ("error", "warning")]
    if not actionable:
        return

    for project_id in project_ids:
        relevant = [
            f for f in actionable
            if f.path and f.path.startswith(f"projects/{project_id}/")
        ]
        if not relevant:
            continue

        detail = "; ".join(f"{f.check}: {f.path}" for f in sorted(relevant, key=lambda f: (f.check, f.path or "")))
        payload = PipelineHealthPayload(
            type="pipeline_health",
            category="wiki_lint",
            message=f"{len(relevant)} vault issue(s) found",
            detail=detail,
        )
        source_hash = events_api.hash_event(payload.model_dump(), "derived/lint")
        events_path = projects_dir / project_id / "events.ndjson"

        already = {
            e.source_hash
            for e in events_api.query(events_path, types=["pipeline_health"])
        }
        if source_hash in already:
            continue

        events_api.append(
            Event(
                event_id=str(uuid.uuid4()),
                ts=datetime.now(tz=timezone.utc),
                run_id=run_id,
                project_id=project_id,
                type="pipeline_health",
                actor=DeterministicActor(kind="deterministic", detector="wiki_lint"),
                source_ref="derived/lint",
                source_hash=source_hash,
                payload=payload,
                hash=events_api.hash_event(payload.model_dump(), source_hash),
            ),
            events_path,
        )

# ---------------------------------------------------------------------------
# Stage 3 — Warehouse Load
# ---------------------------------------------------------------------------

def run_warehouse(
    events_path: Path | list[Path],
    db_path: Path,
    run_id: str,
    vault_dir: Path | None = None,
) -> StageResult:
    """Stage 3 — materialize events and the wiki into DuckDB (PRD §8.3).

    Takes a list of logs in portfolio mode so the warehouse ends up holding
    every project rather than only the last one processed. When a vault is
    given, its articles are indexed into the same database, which is what
    makes cross-project questions and article-to-event joins a single query.
    """
    start = time.monotonic()
    events_loaded = 0
    articles_indexed = 0

    try:
        warehouse.load(events_path, db_path)
        rows = warehouse.query(db_path, "SELECT COUNT(*) AS n FROM events_raw")
        events_loaded = int(rows[0]["n"]) if rows else 0
        if vault_dir is not None and vault_dir.exists():
            articles_indexed = warehouse.load_wiki(vault_dir, db_path)
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
    logger.info(
        "Warehouse complete: %d events, %d articles (%dms)",
        events_loaded, articles_indexed, elapsed,
    )
    return StageResult(
        name="warehouse",
        status="ok",
        duration_ms=elapsed,
        counts={
            "events_loaded": events_loaded,
            "articles_indexed": articles_indexed,
            "errors": 0,
        },
    )


# ---------------------------------------------------------------------------
# Stage 4 — Analyze (deterministic signals)
# ---------------------------------------------------------------------------

def _load_signal_thresholds(schemas_dir: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in (
        "action_aging",
        "action_unowned",
        "blocker_unowned",
        "risk_unaddressed",
        "blocker_aging",
        "deliverable_drift",
        "stakeholder_inactivity",
        "risk_escalation",
        "risk_emergent",
        "tone_shift",
    ):
        path = schemas_dir / f"{name}.json"
        result[name] = dict(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}
    return result


def _load_llm_budget(config_dir: Path) -> float:
    """Load per-project-per-day LLM cost cap from config/signals.yaml. Default: 0.50 USD."""
    yaml_path = config_dir / "signals.yaml"
    if not yaml_path.exists():
        return 0.50
    try:
        import yaml  # available as transitive dep via python-frontmatter
        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        return float(cfg.get("llm_cost_cap_usd_per_project_per_day", 0.50))
    except Exception:
        logger.warning("Could not load LLM budget config from %s — defaulting to $0.50", yaml_path)
        return 0.50


def _signal_source_hash(signal_type: str, evidence: list[str]) -> str:
    """Stable hash encoding signal type + evidence; used as the dedup key."""
    content = f"{signal_type}:{','.join(sorted(evidence))}"
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


def _emit_pipeline_health(
    events_path: Path,
    project_id: str,
    run_id: str,
    category: str,
    message: str,
    detail: str | None = None,
) -> None:
    """Append a pipeline_health event to the event log."""
    payload = PipelineHealthPayload(
        type="pipeline_health",
        category=category,
        message=message,
        detail=detail,
    )
    source_hash = "sha256:" + hashlib.sha256(
        f"pipeline_health:{category}:{project_id}".encode()
    ).hexdigest()
    event = Event(
        event_id=str(uuid.uuid4()),
        ts=datetime.now(tz=timezone.utc),
        run_id=run_id,
        project_id=project_id,
        type="pipeline_health",
        actor=DeterministicActor(kind="deterministic", detector="pipeline"),
        source_ref="derived/pipeline",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
    )
    events_api.append(event, events_path)


def run_analyze(
    project_id: str,
    db_path: Path,
    events_path: Path,
    run_id: str,
    schemas_dir: Path | None = None,
    project_dir: Path | None = None,
    cache_dir: Path | None = None,
    prompts_dir: Path | None = None,
    model: str = "claude-sonnet-4-6",
    config_dir: Path | None = None,
    llm_client: Any = None,
    vault_dir: Path | None = None,
    spent_usd: float = 0.0,
) -> tuple[StageResult, list[Signal]]:
    """Stage 4 — deterministic + LLM signal detection (PRD §8.4, Phase 1).

    Runs deterministic detectors first, then LLM detectors up to the
    per-project-per-day cost cap from config/signals.yaml.  When the cap is
    hit the stage emits a pipeline_health event and continues to render with
    deterministic signals only.

    Returns (StageResult, signals) so the render stage can consume signals
    without re-reading the events log.
    """
    if schemas_dir is None:
        schemas_dir = Path("data/schemas/signals")
    if cache_dir is None:
        cache_dir = Path("data/cache/llm")
    if prompts_dir is None:
        prompts_dir = Path("prompts")
    if config_dir is None:
        config_dir = Path("config")

    start = time.monotonic()
    signals_new = 0
    signals_skipped = 0
    errors = 0
    llm_cost_spent = spent_usd
    emitted: list[Signal] = []

    try:
        thresholds = _load_signal_thresholds(schemas_dir)

        # Stakeholder watchlist: declared in project.notes.md front-matter and
        # carried into the wiki index article by the compile stage.
        if project_dir is not None:
            try:
                project_data = projects.load_project(project_dir, vault_dir)
                raw_stakeholders = project_data.get("metadata", {}).get("stakeholders", [])
                watchlist = [
                    s for s in (raw_stakeholders or [])
                    if isinstance(s, dict) and s.get("contact_email") and s.get("cadence_days")
                ]
                thresholds["stakeholder_inactivity"] = {
                    **thresholds.get("stakeholder_inactivity", {}),
                    "watchlist": watchlist,
                }
            except Exception:
                logger.warning(
                    "Could not load stakeholder config from the wiki index "
                    "— skipping stakeholder_inactivity"
                )

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
            (deterministic.detect_risk_unaddressed, "risk_unaddressed"),
            (deterministic.detect_blocker_aging, "blocker_aging"),
            (deterministic.detect_deliverable_drift, "deliverable_drift"),
            (deterministic.detect_stakeholder_inactivity, "stakeholder_inactivity"),
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

        # -------------------------------------------------------------------
        # LLM signals — runs after deterministic, subject to per-project cost cap
        # -------------------------------------------------------------------
        budget_usd = _load_llm_budget(config_dir)
        budget_exhausted = False

        for llm_fn, llm_key in [
            (llm_signals.detect_risk_escalation, "risk_escalation"),
            (llm_signals.detect_risk_emergent, "risk_emergent"),
            (llm_signals.detect_tone_shift, "tone_shift"),
        ]:
            if llm_cost_spent >= budget_usd:
                if not budget_exhausted:
                    budget_exhausted = True
                    logger.warning(
                        "LLM cost cap reached (%.4f >= %.2f) — skipping remaining LLM detectors",
                        llm_cost_spent, budget_usd,
                    )
                    # Only emit pipeline_health when real money was spent — a zero-budget
                    # config is a deliberate opt-out, not a budget-exceeded situation.
                    if llm_cost_spent > 0:
                        _emit_pipeline_health(
                            events_path, project_id, run_id,
                            "llm_budget_exceeded",
                            f"LLM cost cap ${budget_usd:.2f} reached after ${llm_cost_spent:.4f} spent.",
                        )
                break
            try:
                llm_sigs, cost = llm_fn(
                    db_path, project_id, run_id,
                    thresholds.get(llm_key, {}),
                    cache_dir, prompts_dir, model, llm_client,
                )
                llm_cost_spent += cost
                for signal in llm_sigs:
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
                        actor=LLMActor(kind="llm", model=model, prompt=llm_key),
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
                logger.exception("LLM detector %s failed", llm_key)
                errors += 1

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
        cost_usd=(llm_cost_spent - spent_usd) or None,
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
    vault_dir: Path | None = None,
) -> StageResult:
    """Stage 5 — render MD + HTML status reports (PRD §8.5, Phase 0.5)."""
    start = time.monotonic()
    try:
        render.render_status(project_dir, signals, run_id, vault_dir=vault_dir)
        render.render_html(
            project_dir, signals, run_id,
            design_system_dir=design_system_dir, vault_dir=vault_dir,
        )
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
            project_dir / "reports",
            project_dir / "raw" / "manifest.json",
            project_dir / "raw" / "parse_manifest.json",
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
                pd / "reports",
                pd / "raw" / "manifest.json",
                pd / "raw" / "parse_manifest.json",
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
