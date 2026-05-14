# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Read this first, every session.**
> This is the operating manual for working on Project Agent.
> It exists to keep you (Claude Code) aligned with the architecture between sessions, since you have no memory of prior work beyond what's in git.
>
> **v0.4 changes:** MVP scope trimmed — file-drop ingest only, MD-only render, fewer warehouse tables. Idempotence contract rebuilt around source-hash + LLM response cache. `project.md` split into bot-owned + PM-owned notes. Event schema gains `schema_version`, `run_id`, `retracted`, `supersedes`, structured `actor`, discriminated `payload`. Operations log added. Stage 3 renamed Warehouse Load. See `REVIEW_NOTES.md`.

---

## What this repo is

Project Agent is a git-versioned, AI-augmented project operating system. The full vision and rationale live in `PRD.md`. The visual reference lives in `ARCHITECTURE.md`. **Read both before any non-trivial work.**

One-line summary: **a daily pipeline ingests project sources, parses them into a markdown source-of-truth + an append-only event log, detects signals, renders reports, and opens a PR for the PM to review.** Nothing reaches `main` without a human merge.

---

## Commands

### Dev setup
```bash
# Primary: Docker (no host Python env needed)
docker compose build

# Alternative: local with uv
uv sync                        # installs all deps including dev
```

### Run the pipeline
```bash
# Docker (recommended)
docker compose run --rm pipeline python -m pipeline run --project <id>

# Local
uv run python -m pipeline run --project <id>
```

### Tests
```bash
# Docker
docker compose run --rm pipeline pytest -m unit
docker compose run --rm pipeline pytest -m boundary
docker compose run --rm pipeline pytest              # full suite

# Local
uv run pytest                             # full suite
uv run pytest -m unit                     # fast pure-function tests only
uv run pytest -m integration              # filesystem / DuckDB / git tests
uv run pytest -m idempotence              # run-twice, assert zero diff tests
uv run pytest -m boundary                 # package-boundary lint (must never fail)
uv run pytest tests/test_<module>.py      # single module
```

### Type checking
```bash
docker compose run --rm pipeline mypy --strict src/
# or locally:
uv run mypy --strict src/
```

---

## The Rules That Matter Most

If you forget everything else, remember these.

### 1. The Package Boundary (PRD §5.1)

**All state mutation goes through `src/project_agent/` APIs.**

Anything in `pipeline/`, `agents/`, or anywhere else may **not**:
- Open `data/events.ndjson` directly → use `project_agent.events`
- Open a DuckDB connection directly → use `project_agent.warehouse`
- Edit `project.md` directly → use `project_agent.projects`
- Call `anthropic.*` directly → use `project_agent.llm`
- Shell out to `git` for state changes → use `project_agent.git`

Enforced by CI lint (`tests/test_boundary.py`). Failing this fails the build. If you want to do any of the above from outside the package, **stop and add the function to the package instead.**

The lint is not airtight (misses `importlib`, transitive imports, re-exports — PRD §5.1 documents this). Don't circumvent it on purpose.

### 2. The HITL Invariant (PRD §9)

**The pipeline never takes externally observable action without a merged PR.**

- No commits to `main`. Always `auto/<date>` branch + PR.
- No emails sent (Phase 3+ `--send` flag; until then, drafts only).
- No calendar events, no Jira writes (Phase 3+, 4+).

### 3. The LLM Cache is the Idempotence Backbone (PRD §6.2)

Every call to `project_agent.llm.extract` (and any other LLM call in the parse path) is cached in `data/cache/llm/`, keyed on `(prompt_version, source_hash, model)`. Cache hits replay byte-identical responses, making re-runs zero-diff.

- **Never** call `anthropic.*` from inside the package without routing through the cache wrapper.
- **Never** mutate cached responses. To re-parse under new instructions, bump the prompt `version` field. Old events get superseded via the `supersedes` chain.
- If a cache key collides with stale content, invalidate by bumping prompt version — not by deleting cache files.

### 4. `project.md` is Bot-Owned; `project.notes.md` is PM-Owned (PRD §6.1)

The pipeline **rewrites `project.md` on every run**. It **never writes to `project.notes.md`**.

- Modifying `project.notes.md` from any stage → **stop**, it's not the pipeline's surface.
- Preserving PM-written content inside `project.md` (the old `<!-- pm:* -->` marker approach) → **stop**, that was removed in PRD v0.4. PM content goes in `project.notes.md`. The render stage composes both.

---

## Architecture

Six layers, each idempotent and independently testable:

```
┌───────────────────────────────────────────────────────┐
│  6. Commit         → git PR drafts (HITL)             │
├───────────────────────────────────────────────────────┤
│  5. Render         → MD reports (HTML in Phase 0.5)   │
├───────────────────────────────────────────────────────┤
│  4. Analyze        → DuckDB queries + Claude API      │
├───────────────────────────────────────────────────────┤
│  3. Warehouse Load → NDJSON → DuckDB views            │
├───────────────────────────────────────────────────────┤
│  2. Parse          → sources → project.md + events    │
├───────────────────────────────────────────────────────┤
│  1. Ingest         → file drops (MCP in Phase 0.5)    │
└───────────────────────────────────────────────────────┘
```

Three code layers:

| Layer | What lives here |
|---|---|
| **Substrate** (`src/project_agent/`) | Pure functions, pydantic models, narrow I/O primitives. All state mutation. |
| **Glue** (`pipeline/`) | Thin imperative scripts: read args → call package → log ops. No direct I/O. |
| **Wrappers** (`agents/`, Phase 5b) | Claude API loops with tool definitions that call the package. Empty until Phase 5. |

**Key idempotence flows:**
- *Same file re-ingested* → `source_hash` matches `manifest.json` → skipped, zero events.
- *Same source, same prompt version* → LLM cache hit → byte-identical events, zero diff.
- *Edited source* → new `source_hash` → new events with `supersedes: [<old_event_ids>]`.
- *Prompt version bumped* → cache miss → re-parse, new events supersede old ones.

**Package module map:**

```
src/project_agent/
├── schemas.py          ← pydantic: Event, Signal, Project + discriminated unions
├── events.py           ← NDJSON append/query/dedupe
├── warehouse.py        ← DuckDB load/query
├── projects.py         ← project.md parse/render
├── render.py           ← MD emitter (HTML Phase 0.5)
├── git.py              ← branch/PR helpers
├── llm.py              ← Claude API client + cache wrapper
├── ingest/
│   ├── inbox.py        ← file-drop reader (MVP)
│   ├── backlog.py      ← CSV/JSON reader
│   └── mcp.py          ← Gmail/Drive/Calendar (Phase 0.5)
└── signals/
    ├── deterministic.py ← SQL-based detectors
    └── llm.py           ← Claude-evaluated detectors (Phase 1+)
```

---

## Current Phase: MVP (Phase 0)

**Out of scope for MVP — do not build these:**
- HTML rendering (Phase 0.5)
- MCP ingest — Gmail/Drive/Calendar (Phase 0.5)
- LLM signals (Phase 1)
- `risks` / `decisions` / `milestones` / `stakeholders` warehouse tables (Phase 1)
- Portfolio rollup (Phase 2), outbound emails (Phase 3), Jira (Phase 4), multi-agent (Phase 5)

**MVP Definition of Done (PRD §10):**
- `python -m pipeline run --project <id>` executes all six stages end-to-end. Ingest is file-drop only.
- Re-running produces zero diff (`git status` clean after second run).
- ≥ 20 events spanning ≥ 7 days. DuckDB exposes `events`, `actions`, `blockers` tables.
- Three deterministic signals firing: `action_aging`, `action_unowned`, `blocker_unowned`. Each `signal_detected` event carries `evidence: [event_id, ...]`.
- `project_agent.events.query` returns deterministic results across runs.
- Operations log (`data/runs/<run_id>.json`) written every run.
- Event schema includes all v0.4 fields: `schema_version`, `run_id`, `source_hash`, structured `actor`, `confidence`, `supersedes`/`superseded_by`, `retracted`/`retracted_reason`. `payload` is a discriminated union.
- Package-boundary lint passing.

---

## Conventions

### Python
- Python 3.11+. `pyproject.toml` is the source of truth for deps. Don't pin versions in code.
- `src/` layout — install editable via `pip install -e .`.
- Pydantic v2 for all schemas. No `dataclasses`, no `TypedDict`, no `dict` in function signatures where a model fits.
- `Event.payload` — discriminated union over event `type` (`RiskAddedPayload`, `ActionAddedPayload`, `SignalDetectedPayload`, …). Discriminator is `payload.type`.
- `Event.actor` — discriminated union over `actor.kind` (`LLMActor`, `HumanActor`, `MCPActor`, `DeterministicActor`).
- `mypy --strict` must pass. No `print()` — use `logging`. No `os.path` — use `pathlib.Path`.
- No global state, no module-level mutation, no singletons. Pass dependencies as arguments.

### Tests
Four pytest markers — every test gets exactly one:
- `@pytest.mark.unit` — pure functions, no I/O
- `@pytest.mark.integration` — filesystem, DuckDB, or git (uses `tmp_path`)
- `@pytest.mark.idempotence` — runs a stage twice, asserts second run is a no-op; must assert no `anthropic.*` call on cache hit via a recording client
- `@pytest.mark.boundary` — the §5.1 lint; failing this fails the build

Every package module has a corresponding `tests/test_<module>.py`. Idempotence tests are non-negotiable for any stage that mutates state.

### Git
- Branch naming: `auto/<YYYY-MM-DD>` for pipeline-generated; `feat/<short-desc>` for dev work.
- Commit messages: imperative mood, first line ≤ 72 chars, body explains *why*.
- One PR per pipeline stage during MVP build. Don't bundle.

### Prompts
- All Claude API prompts live in `prompts/*.md`, loaded by `project_agent.llm.load_prompt(name)`.
- Each prompt file has front-matter: `purpose`, `model`, `max_tokens`, `expected_schema`, **`version: <int>`**.
- Bumping `version` invalidates the LLM cache for that prompt. Never inline prompt text in Python.

---

## Data Contracts (cheat sheet)

Full specs in PRD §6.

**Event** — append-only NDJSON. Required: `event_id`, `schema_version`, `ts`, `run_id`, `project_id`, `type`, `actor` (discriminated by `kind`), `source_ref`, `source_hash`, `payload` (discriminated by `type`), `hash`. Optional: `confidence`, `supersedes`, `superseded_by`, `retracted`, `retracted_reason`.

**Signal** — written back as `signal_detected` events. Required: `signal_id`, `schema_version`, `project_id`, `run_id`, `category` (risk/deliverable/action/blocker/backlog/communication), `type`, `severity`, `confidence`, `evidence` (non-empty list of `event_id`s), `method` (deterministic|llm), `rationale`, `detected_at`, `recommended_action`.

**Operations log** — `data/runs/<run_id>.json`. Per-stage: `name`, `status`, `duration_ms`, `counts`, optional `cost_usd`. Committed with each PR.

**Fixing bad data** — emit `event_retracted` via `project_agent.events.append`. Never mutate `events.ndjson`.

---

## MVP Signal Catalog

| Category | Signal | Method | Phase |
|---|---|---|---|
| Action | **action_aging** | deterministic | **MVP** |
| Action | **action_unowned** | deterministic | **MVP** |
| Blocker | **blocker_unowned** | deterministic | **MVP** |
| Risk | risk_unaddressed | deterministic | 1 |
| Risk | risk_escalation / risk_emergent | LLM | 1 |
| Deliverable | deliverable_drift | deterministic | 1 |
| Blocker | blocker_aging | deterministic | 1 |
| Communication | stakeholder_inactivity | deterministic | 1 |
| Communication | tone_shift | LLM | 1 |
| Deliverable | deliverable_dependency_stall | LLM | 2 |
| Backlog | scope_churn / velocity_anomaly | deterministic | 4 |

**Anti-circularity (Phase 1+):** signal detectors of either kind skip `signal_detected` events when reading the warehouse. LLM prompts may not cite `signal_detected` events as evidence.

Each signal has a JSON spec under `data/schemas/signals/<type>.json`. SQL detectors must return triggering `event_id`s, not just aggregates — every `signal_detected` event needs `evidence: [event_id, ...]`.

---

## Common Gotchas

- **Editing `project.md` from a pipeline script.** Violates Rule 1. Use `project_agent.projects.update`.
- **Writing to `project.notes.md` from any code.** PM-owned. Never.
- **Calling `anthropic.*` without going through the LLM cache wrapper.** Silently breaks idempotence — tests pass once, fail next day on cron.
- **Opening DuckDB in a stage function.** Violates Rule 1. Use `project_agent.warehouse.query`.
- **Skipping the second-run idempotence check.** A stage that "works" but produces a different `events.ndjson` on re-run is broken.
- **Emitting `signal_detected` without `evidence: [event_id, ...]`.** Pydantic rejects it — surface as a clear error.
- **Inlining a prompt as a Python string.** Prompts live in `prompts/*.md`. Bump `version` when you change one.
- **Adding a new event type without updating the discriminated union.** Update `project_agent.schemas` first.
- **Reading from `sources/<typed-folder>/` before ingest runs.** Ingest moves files out of `_inbox/`. Other stages assume files are in typed folders.
- **Hand-editing `events.ndjson`.** Append-only, package-mediated. Use `event_retracted` events instead.
- **Forgetting the operations log.** Each stage must update `data/runs/<run_id>.json`.

---

## Decisions Already Made

- **MVP ingest is file-drop only.** MCP (Gmail/Drive/Calendar) is Phase 0.5.
- **MVP signals are deterministic only.** LLM signals come in Phase 1.
- **MVP render is MD only.** HTML is Phase 0.5.
- **Idempotence is anchored on source-hash + LLM response cache.** Event-hash dedup is a secondary defense.
- **`event_retracted` is the mechanism for fixing bad data.** Never mutate `events.ndjson`.
- **`Event.payload` and `actor` are discriminated unions.** No `dict[str, Any]`.
- **No ABCs, no plugin systems, no DI containers.** Pure functions and pydantic. `typing.Protocol` if two concrete impls share a shape.
- **Project ID convention:** `<client>--<project>` until decided (PRD §13.1).

**Open and deferred** (ask first, don't decide unilaterally): outbox storage policy, cron host & threat model, portfolio PR shape, source artifact size, LLM cost cap policy. See PRD §13.

---

## Slash Commands

Defined in `.claude/commands/`:

- `/sync` — run the full pipeline for the current project on the working branch.
- `/report` — render the status report only (skip ingest/parse).
- `/pr` — open a PR for the current branch with a generated summary.
- `/signals` — print all signals from the latest run, grouped by category.

All delegate to `python -m pipeline ...` under the hood.

---

## When Something Is Wrong

1. Don't fix it speculatively. Ask the maintainer first.
2. Run the test suite. Read the failures.
3. Check `git log` and recent PR descriptions for context.
4. Check `data/events.ndjson` for the last few events.
5. If `data/` looks corrupt: restore from git history (`git checkout <previous-commit> -- data/`). Do not repair manually.

---

*CLAUDE.md v0.4 — companion to PRD.md v0.4 and ARCHITECTURE.md v0.2.*
*Update this file whenever a working agreement changes.*
