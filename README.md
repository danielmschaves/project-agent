# Project Agent

A git-versioned, AI-augmented project operating system.

A daily pipeline ingests project sources, parses them into an append-only event log,
**compiles that log into a markdown wiki you read in Obsidian**, indexes both in DuckDB,
detects signals, and opens a PR for the PM to review. Nothing reaches `main` without a
human merge.

The wiki is the point. Instead of one flat status file, each risk, decision, milestone and
source document becomes an article that links to the others and back to the exact event it
came from.

## Quick Start

```bash
cp .env.example .env          # fill in ANTHROPIC_API_KEY and GH_TOKEN
docker compose build

# Run the demo project (synthetic 30-day corpus included)
docker compose run --rm pipeline python -m pipeline run --project demo--sample-2026

# Run everything: all projects, the shared layer, one PR
docker compose run --rm pipeline python -m pipeline portfolio

docker compose run --rm pipeline pytest
```

Then **open the repository root as an Obsidian vault** and browse `kb/`. The committed
`.obsidian/` config sets absolute wikilinks and hides the source directories.

## Using your own data

1. **Create a project directory** following the `<client>--<project>` convention:
   `projects/acme--website-relaunch/`

2. **Create `project.notes.md`** — PM-owned, never written by the pipeline, and the
   anchor project discovery keys on. Its front-matter carries declared metadata:

   ```markdown
   ---
   status: green
   sponsor: Dana Reed
   phase: discovery
   stakeholders:
     - name: Bob Kim
       contact_email: bob.kim@example.com
       cadence_days: 7
   ---

   # Acme — Website Relaunch

   Free-form notes. The pipeline reads these as an excerpt and never writes here.
   ```

3. **Get documents into `raw/_inbox/`.** Drop files directly, or ask Claude Code to fetch
   them through its MCP connectors — the pipeline itself holds no credentials. Keep the
   `<date>-<source>-<id>.<ext>` naming so ordering is preserved. Recognised:
   `.md`, `.txt`, `.eml`, `.msg`, `.csv`, `.json`.

4. **Run it.** The pipeline opens a PR on an `auto/<date>` branch. Review and merge —
   that is the HITL gate.

Reset the demo to its original state with `uv run python scripts/reset_demo.py`.

> **Data in git:** runtime outputs (`events.ndjson`, `reports/`, manifests) are gitignored
> on dev branches and force-added on `auto/<date>` branches so they land in the PR.
> `kb/` is the deliberate exception — it is committed normally, because a vault has to
> exist on a fresh clone. Pipeline runs therefore dirty `kb/` on dev branches; use
> `--dry-run` when you don't want that.

## Architecture

```
1. Ingest         → raw/_inbox/ → typed folders + manifest.json
2. Parse          → LLM extraction (cached) → fact events
2b. Compile       → events → kb/ articles     (Phase A per project, Phase B once)
3. Warehouse Load → events + kb/ → DuckDB views
4. Analyze        → SQL + LLM detectors → signal_detected events
5. Render         → portfolio dashboard HTML
6. Commit         → auto/<date> branch → PR
```

`pipeline run --project <id>` does Phase A only. `pipeline portfolio` is the full compile,
including the shared `people/`, `clients/` and `concepts/` articles that fan in across
projects.

A second lane, `research/`, ingests papers and web clippings and compiles them into
`kb/research/sources/`, sharing the concept layer with the project lane.

## Querying the wiki

```bash
python -m pipeline search "redis circuit breaker"
python -m pipeline wiki-sql "SELECT project, title FROM documents WHERE type = 'risk'"
python -m pipeline lint            # structural health + proposed registry additions
```

Relations available to `wiki-sql`: `documents`, `links`, `backlinks`, `broken_links`,
`orphan_documents`, `document_tags`, `document_provenance` (article → the events it was
compiled from), plus the event views `events`, `risks`, `decisions`, `milestones`,
`actions`, `blockers`.

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | For Parse, Compile prose, LLM signals | Claude API |
| `GH_TOKEN` | For the Commit stage | GitHub CLI for opening PRs |
| `GIT_AUTHOR_NAME` | No | Identity for pipeline commits |
| `GIT_AUTHOR_EMAIL` | No | Identity for pipeline commits |

Set the LLM spend cap in `config/signals.yaml`; `0.0` disables all model calls and the
pipeline runs deterministic-only.

## Current Phase

**v2.0 shipped** — the knowledge-base migration. Seven stages, two lanes, the compiled
wiki with provenance and backlinks, DuckDB over events and vault, search and SQL, and a
structural lint with a registry-proposal loop.

**Phase 2 (current) — the agent lane.** Claude Code emits *events* the compiler projects,
so it can name concepts across project material and merge duplicate risks: work the
per-article pipeline structurally cannot do. See `PRD.md` §4.3 and §11.

Known gaps are tracked openly in `PRD.md` §10 — including that `kb/` has not yet been
populated from a real run, and that PDFs and images are accepted but not decoded.

## Project Layout

```
kb/                  ← the compiled wiki — bot-owned, committed, an Obsidian vault
.obsidian/           ← committed vault config
src/project_agent/   ← the package — all state mutation goes through here
pipeline/            ← thin orchestration glue
prompts/             ← versioned Claude API prompts
projects/<id>/
  project.notes.md   ← PM-owned, always committed (discovery anchor)
  raw/               ← source documents; _inbox/ is the entry point
  events.ndjson      ← gitignored; committed on auto/* branches
research/raw/        ← the research corpus lane
design-system/       ← tokens.css, system.css, portfolio.css
config/              ← signals.yaml (thresholds + LLM spend cap)
data/                ← warehouse.duckdb, cache/, runs/ (all gitignored)
scripts/             ← reset_demo.py
tests/               ← pytest (unit / integration / idempotence / boundary)
```

Documentation lives in two files: **`PRD.md`** for the why, **`CLAUDE.md`** for the how.
