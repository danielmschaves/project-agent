# Project Agent

A git-versioned, AI-augmented project operating system. A daily pipeline ingests project sources, parses them into a markdown source-of-truth and an append-only event log, detects signals, renders reports, and opens a PR for the PM to review. Nothing reaches `main` without a human merge.

## Quick Start

```bash
# 1. Copy and fill in your credentials
cp .env.example .env

# 2. Build the Docker image
docker compose build

# 3. Run the full pipeline for a project
docker compose run --rm pipeline python -m pipeline run --project <id>

# 4. Run tests
docker compose run --rm pipeline pytest
```

## Architecture

The pipeline runs in six idempotent stages:

```
1. Ingest         → file drops → manifest.json
2. Parse          → LLM extraction (cached) → events.ndjson + project.md
3. Warehouse Load → events.ndjson → DuckDB views
4. Analyze        → SQL signal detectors → signal_detected events
5. Render         → project.md + signals → reports/status-<date>.md
6. Commit         → auto/<date> branch → GitHub PR (PM reviews + merges)
```

Visual references (open locally):
- [Architecture diagrams](docs/architecture.html)
- [Field manual / CLAUDE.md](docs/field_manual.html)

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (Parse stage) | Claude API for LLM extraction |
| `GH_TOKEN` | Yes (Commit stage) | GitHub CLI for opening PRs |
| `GIT_AUTHOR_NAME` | No | Identity for pipeline commits |
| `GIT_AUTHOR_EMAIL` | No | Identity for pipeline commits |

## Current Phase

**Phase 0 — MVP.** See `CLAUDE.md` for the Definition of Done and what is explicitly out of scope.

## Project Layout

```
src/project_agent/   ← package — all state mutation goes through here
pipeline/            ← thin orchestration glue
prompts/             ← versioned Claude API prompts
projects/<id>/       ← per-project source files and reports
data/                ← events.ndjson, warehouse.duckdb, runs/, cache/
tests/               ← pytest (unit / integration / idempotence / boundary)
docs/                ← rendered architecture and field manual HTML
```
