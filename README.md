# Project Agent

A git-versioned, AI-augmented project operating system. A daily pipeline ingests project sources, parses them into a markdown source-of-truth and an append-only event log, detects signals, renders reports, and opens a PR for the PM to review. Nothing reaches `main` without a human merge.

## Quick Start

```bash
# 1. Copy and fill in your credentials
cp .env.example .env

# 2. Build the Docker image
docker compose build

# 3. Run the full pipeline for a single project
docker compose run --rm pipeline python -m pipeline run --project <id>

# 4. Run the portfolio pipeline (all projects, one PR)
docker compose run --rm pipeline python -m pipeline portfolio

# 5. Run tests
docker compose run --rm pipeline pytest
```

## Architecture

The pipeline runs in six idempotent stages:

```
1. Ingest         → file drops + MCP (Gmail/Drive/Calendar) → manifest.json
2. Parse          → LLM extraction (cached) → events.ndjson + project.md
3. Warehouse Load → events.ndjson → DuckDB views
4. Analyze        → SQL signal detectors → signal_detected events
5. Render         → project.md + signals → reports/status-<date>.md + status-<date>.html
6. Commit         → auto/<date> branch → GitHub PR (PM reviews + merges)

Portfolio mode: `pipeline portfolio` runs stages 1–5 for all projects, then renders a
cross-project HTML dashboard and opens a single PR covering all projects.
```

Visual references (open locally):
- [Architecture diagrams](docs/architecture.html)
- [Field manual / CLAUDE.md](docs/field_manual.html)

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (Parse stage) | Claude API for LLM extraction |
| `GH_TOKEN` | Yes (Commit stage) | GitHub CLI for opening PRs |
| `GOOGLE_TOKEN_PATH` | No (MCP ingest) | Path to OAuth2 token JSON for Gmail/Drive/Calendar. If unset, MCP ingest is skipped; file-drop only. |
| `GIT_AUTHOR_NAME` | No | Identity for pipeline commits |
| `GIT_AUTHOR_EMAIL` | No | Identity for pipeline commits |

## Current Phase

**Phase 0.5 — Complete.** MCP ingest, portfolio PR shape, and HTML v2 design system rendering are all shipped. Phase 1 (LLM signals) is next. See `CLAUDE.md` for the full phase status and what is out of scope.

## Project Layout

```
src/project_agent/   ← package — all state mutation goes through here
pipeline/            ← thin orchestration glue (run + portfolio commands)
prompts/             ← versioned Claude API prompts
projects/<id>/       ← per-project source files, events.ndjson, and reports/
data/                ← warehouse.duckdb, runs/, cache/
design-system/       ← tokens.css, system.css, Portfolio Dashboard.html (CSS source)
tests/               ← pytest (unit / integration / idempotence / boundary)
docs/                ← status.html and other reference docs
```
