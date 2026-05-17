# Project Agent

A git-versioned, AI-augmented project operating system. A daily pipeline ingests project sources, parses them into a markdown source-of-truth and an append-only event log, detects signals, renders reports, and opens a PR for the PM to review. Nothing reaches `main` without a human merge.

## Quick Start

```bash
# 1. Copy and fill in your credentials
cp .env.example .env

# 2. Build the Docker image
docker compose build

# 3. Run the demo project (synthetic 30-day corpus included)
docker compose run --rm pipeline python -m pipeline run --project demo--sample-2026

# 4. Run the portfolio pipeline (all projects, one PR)
docker compose run --rm pipeline python -m pipeline portfolio

# 5. Run tests
docker compose run --rm pipeline pytest
```

## Using with your own data

1. **Create a project directory** following the `<client>--<project>` convention:
   ```
   projects/acme--website-relaunch/
   ```

2. **Create `project.notes.md`** — this PM-owned file is the discovery anchor. The portfolio command finds projects by its presence.
   ```markdown
   # Acme — Website Relaunch
   <!-- PM notes, goals, context. Pipeline never writes here. -->
   ```

3. **Drop source files** into `projects/acme--website-relaunch/sources/_inbox/`. Supported types: `.md`, `.txt`, `.eml`, `.csv`, `.json`.

4. **Run the pipeline:**
   ```bash
   docker compose run --rm pipeline python -m pipeline run --project acme--website-relaunch
   ```
   The pipeline opens a PR on an `auto/<date>` branch. Review and merge it — that's the HITL gate.

> **Data in git:** Runtime outputs (`project.md`, `events.ndjson`, `reports/`) are gitignored on dev branches. The pipeline force-adds them on `auto/<date>` branches so they land in the HITL PR. Nothing generated ever appears in your feature branches.

To reset the demo project back to its original state:
```bash
uv run python scripts/reset_demo.py
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
projects/<id>/
  project.notes.md   ← PM-owned, always committed (discovery anchor)
  sources/           ← drop source files here; _inbox/ is the entry point
  reports/           ← gitignored; pipeline commits on auto/* branches
  events.ndjson      ← gitignored; pipeline commits on auto/* branches
  project.md         ← gitignored; pipeline commits on auto/* branches
data/                ← warehouse.duckdb (gitignored), runs/ (gitignored), cache/ (gitignored)
design-system/       ← tokens.css, system.css, reference HTML — use these when extending reports
reports/             ← portfolio HTML; gitignored on dev branches
scripts/             ← reset_demo.py and other maintenance helpers
tests/               ← pytest (unit / integration / idempotence / boundary)
docs/                ← rendered architecture and field manual HTML
```
