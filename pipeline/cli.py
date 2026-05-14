"""Entry point: python -m pipeline <subcommand>."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from project_agent import events as events_api
from project_agent.schemas import RunLog, Signal, SignalDetectedPayload
from pipeline import stages

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Project Agent — AI-augmented project operating system",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run the full pipeline for a project")
    run_p.add_argument("--project", required=True, metavar="ID",
                       help="Project ID, e.g. demo--sample-2026")
    run_p.add_argument("--projects-dir", default="projects",
                       help="Root directory containing project folders (default: projects/)")
    run_p.add_argument("--data-dir", default="data",
                       help="Data directory for warehouse and cache (default: data/)")
    run_p.add_argument("--dry-run", action="store_true",
                       help="Skip git commit/PR (sets DRY_RUN=1)")

    args = parser.parse_args()
    if args.command == "run":
        sys.exit(_cmd_run(args))


def _cmd_run(args: argparse.Namespace) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1

    if args.dry_run:
        os.environ["DRY_RUN"] = "1"

    project_id: str = args.project
    projects_dir = Path(args.projects_dir)
    data_dir = Path(args.data_dir)
    project_dir = projects_dir / project_id

    if not project_dir.exists():
        print(f"ERROR: project directory not found: {project_dir}", file=sys.stderr)
        return 1

    run_id = uuid.uuid4().hex[:8]
    events_path = project_dir / "events.ndjson"
    db_path = data_dir / "warehouse.duckdb"
    cache_dir = data_dir / "cache" / "llm"
    prompts_dir = Path("prompts")
    schemas_dir = Path("data/schemas/signals")

    events_path.touch()
    data_dir.mkdir(parents=True, exist_ok=True)

    run_log = RunLog(
        run_id=run_id,
        started_at=datetime.now(tz=timezone.utc),
        project_ids=[project_id],
    )

    print(f"[{run_id}] Project: {project_id}")

    def _step(name: str, result_or_pair: object) -> StageResult:  # type: ignore[name-defined]
        # run_analyze returns (StageResult, signals); others return StageResult
        from project_agent.schemas import StageResult as SR
        if isinstance(result_or_pair, tuple):
            result, extra = result_or_pair
        else:
            result, extra = result_or_pair, None
        run_log.stages.append(result)
        status_str = f"✓" if result.status == "ok" else f"✗ {result.status}"
        print(f"  {name:12s} {status_str}  {result.counts}")
        return result, extra  # type: ignore[return-value]

    # Stage 1 — Ingest
    r1, _ = _step("ingest", stages.run_ingest(project_id, project_dir, events_path, run_id))

    # Stage 2 — Parse
    r2, _ = _step("parse", stages.run_parse(
        project_id, project_dir, events_path, cache_dir, prompts_dir, run_id
    ))

    # Stage 3 — Warehouse
    r3, _ = _step("warehouse", stages.run_warehouse(events_path, db_path, run_id))

    # Stage 4 — Analyze
    r4, signals = _step("analyze", stages.run_analyze(
        project_id, db_path, events_path, run_id, schemas_dir
    ))
    signals = signals or []

    # Reconstruct any previously-emitted signals not in this run's batch
    if r4 and r4.status == "ok":
        existing = events_api.query(events_path, project_id=project_id, types=["signal_detected"])
        already_ids = {s.signal_id for s in signals}  # type: ignore[attr-defined]
        for evt in existing:
            p = evt.payload
            if isinstance(p, SignalDetectedPayload) and p.signal_id not in already_ids:
                signals.append(Signal(
                    signal_id=p.signal_id,
                    project_id=evt.project_id,
                    run_id=evt.run_id,
                    category=p.category,
                    type=p.signal_type,
                    severity=p.severity,
                    confidence=p.confidence,
                    evidence=p.evidence,
                    method=p.method,
                    rationale=p.rationale,
                    detected_at=evt.ts,
                    recommended_action=p.recommended_action,
                ))

    # Stage 5 — Render
    _step("render", stages.run_render(project_id, project_dir, signals, run_id))

    # Stage 6 — Commit
    _step("commit", stages.run_commit(project_id, project_dir, run_log, Path(".")))

    # Write run log
    run_log.ended_at = datetime.now(tz=timezone.utc)
    runs_dir = data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_log_path = runs_dir / f"{run_id}.json"
    run_log_path.write_text(run_log.model_dump_json(indent=2), encoding="utf-8")

    errors = sum(s.counts.get("errors", 0) for s in run_log.stages)
    print(f"\nRun log: {run_log_path}  errors={errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    main()
