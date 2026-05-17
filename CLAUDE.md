# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Read this first, every session.**
> This is the operating manual for working on Project Agent.
> It exists to keep you (Claude Code) aligned with the architecture between sessions, since you have no memory of prior work beyond what's in git.
>
> **v0.4 changes:** MVP scope trimmed — file-drop ingest only, MD-only render, fewer warehouse tables. Idempotence contract rebuilt around source-hash + LLM response cache. `project.md` split into bot-owned + PM-owned notes. Event schema gains `schema_version`, `run_id`, `retracted`, `supersedes`, structured `actor`, discriminated `payload`. Operations log added. Stage 3 renamed Warehouse Load. See `REVIEW_NOTES.md`.
>
> **v0.5 changes:** Phase 0.5 complete. HTML rendering ships — both single-project status dashboard and portfolio cross-project dashboard. MCP ingest live (Gmail/Drive/Calendar). `pipeline portfolio` command added. `_BASE_CSS` dropped; design system CSS inlined from `design-system/`. `PortfolioProject` model extended with `client_id`, `sponsor`, `phase`, `last_source_ts`, `events_count`.
>
> **v1.0 (Phase 1, PR 1):** Schema + Parse extensions. `run_parse()` now emits `risk_added`, `decision_made`, and `milestone_added` events from extracted source content. `extract-context.md` bumped to v2 (adds `milestones` extraction — triggers cache miss on all prior sources, re-parsing them under the new prompt). `SourceIngestedPayload` gains optional `sender` field (populated from `From:` header for email sources; used by the upcoming `stakeholder_inactivity` signal). Two new payload types: `MilestoneAddedPayload`, `PipelineHealthPayload`. `projects.update_project()` now renders `Risks`, `Decisions`, and `Milestones` sections in `project.md`.

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
│  5. Render         → MD + HTML reports (both shipped)  │
├───────────────────────────────────────────────────────┤
│  4. Analyze        → DuckDB queries + Claude API      │
├───────────────────────────────────────────────────────┤
│  3. Warehouse Load → NDJSON → DuckDB views            │
├───────────────────────────────────────────────────────┤
│  2. Parse          → sources → project.md + events    │
├───────────────────────────────────────────────────────┤
│  1. Ingest         → file drops + MCP (both shipped)  │
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
├── render.py           ← MD + HTML emitter (single-project + portfolio)
├── git.py              ← branch/PR helpers
├── llm.py              ← Claude API client + cache wrapper
├── ingest/
│   ├── inbox.py        ← file-drop reader
│   ├── backlog.py      ← CSV/JSON reader
│   └── mcp.py          ← Gmail/Drive/Calendar (shipped Phase 0.5)
└── signals/
    ├── deterministic.py ← SQL-based detectors
    └── llm.py           ← Claude-evaluated detectors (Phase 1+)
```

---

## Current Phase: Phase 1 (LLM Signals)

Phase 0.5 is **complete** — MCP ingest, 30-day corpus, HTML rendering v2, and portfolio PR shape are all shipped.

**Phase 0.5 — delivered:**
- ✅ MCP ingest — `project_agent.ingest.mcp` (Gmail/Drive/Calendar via `google-api-python-client`). Gate: set `GOOGLE_TOKEN_PATH=<path-to-token.json>`. If unset, falls back to file-drop only.
- ✅ 30-day history backfill — 17 synthetic source files spanning 2026-04-10 to 2026-05-14.
- ✅ HTML rendering — both single-project status dashboard and portfolio cross-project dashboard. Design system CSS inlined from `design-system/`. `_BASE_CSS` removed. Editorial header, 5-card fact strip, indicator grid (6 categories × project), attention strip (top-3 by score), verdict callout. Byte-identical on re-run.
- ✅ Portfolio PR shape — `pipeline portfolio` discovers all projects in `projects/`, runs stages 1–5 per project, renders portfolio HTML dashboard, then opens one PR. `pipeline run --project <id>` retained for single-project dev use.

**Phase 1 — in progress (sequenced as 4 PRs):**
- ✅ PR 1: Schema + Parse extensions (`MilestoneAddedPayload`, `PipelineHealthPayload`, `sender` on `SourceIngestedPayload`; `run_parse()` emits `risk_added`/`decision_made`/`milestone_added`; `extract-context.md` v2)
- ✅ PR 2: Warehouse Phase 1 views — `risks`, `decisions`, `milestones` views (+ `_full` variants). All expose `event_id` for evidence linking. All filter `retracted=false AND superseded_by IS NULL` by default.
- 🔲 PR 3: New deterministic signals (`risk_unaddressed`, `blocker_aging`, `deliverable_drift`, `stakeholder_inactivity`)
- 🔲 PR 4: LLM signals + cost cap (`signals/llm.py`; 3 prompts; `config/signals.yaml`)
- 🔲 PR 5: Scheduled operation (deferred — after LLM signals are stable)

**Out of scope until later phases:**
- Portfolio cross-project signals (Phase 2), outbound emails (Phase 3), Jira (Phase 4), multi-agent (Phase 5)

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
- **Expecting `_inbox/` to retain skipped files.** `scan_inbox` now deletes files from `_inbox/` when their `source_hash` is already in `manifest.json`. This keeps the inbox clean across MCP re-fetches; don't rely on skipped files persisting there.
- **Hand-editing `events.ndjson`.** Append-only, package-mediated. Use `event_retracted` events instead.
- **Forgetting the operations log.** Each stage must update `data/runs/<run_id>.json`.
- **Committing pipeline outputs to a dev branch.** `project.md`, `events.ndjson`, `reports/`, `manifest.json`, `parse_manifest.json`, and `data/runs/*.json` are all gitignored. On dev branches they stay local. The pipeline's `run_commit` / `run_commit_portfolio` stages use `git add -f` (via `project_agent.git.commit_all(force_paths=[...])`) to include them only on `auto/<date>` branches. Don't bypass this with `git add -A` outside of the package.
- **Using `project.md` as the project discovery anchor.** `_discover_projects` checks for `project.notes.md`, not `project.md`. `project.md` is gitignored and won't exist on a fresh clone. Always create `project.notes.md` first when adding a new project.

---

## Decisions Already Made

- **MCP ingest is live in Phase 0.5.** Set `GOOGLE_TOKEN_PATH=<path-to-token.json>` (OAuth2 authorized-user format). Without it, the stage silently skips MCP and runs file-drop only. Token is refreshed in-place when expired.
- **MCP adapters write to `_inbox/` only.** They do not emit events. `inbox.scan_inbox` handles classification, manifest dedup, and event emission — same as file-drop. `scan_inbox` now deletes already-known files from `_inbox/` (hash in manifest) so re-fetched MCP content does not accumulate.
- **Phase 0.5 signals are deterministic only.** LLM signals come in Phase 1.
- **Phase 0.5 render ships both MD and HTML.** `render_status()` emits MD; `render_html()` emits single-project HTML; `render_portfolio_html()` emits the cross-project dashboard. All three are idempotent.
- **Idempotence is anchored on source-hash + LLM response cache.** Event-hash dedup is a secondary defense.
- **`event_retracted` is the mechanism for fixing bad data.** Never mutate `events.ndjson`.
- **`Event.payload` and `actor` are discriminated unions.** No `dict[str, Any]`.
- **No ABCs, no plugin systems, no DI containers.** Pure functions and pydantic. `typing.Protocol` if two concrete impls share a shape.
- **Project ID convention:** `<client>--<project>` until decided (PRD §13.1).
- **Runtime outputs are gitignored; pipeline uses `git add -f` to commit them.** All generated files (`project.md`, `events.ndjson`, `reports/`, manifests, run logs) are in `.gitignore` so dev branches stay clean. `project_agent.git.commit_all(force_paths=[...])` adds them explicitly on `auto/<date>` branches. This is intentional — do not remove these gitignore rules.
- **Project discovery uses `project.notes.md`.** `pipeline portfolio` finds projects via `_discover_projects`, which checks for `project.notes.md` (PM-owned, always committed) rather than `project.md` (bot-generated, gitignored). A fresh clone will have `project.notes.md` but not `project.md`; that is the correct state.
- **`design-system/` is tracked in git.** `tokens.css`, `system.css`, and the reference HTML files are committed so teammates can reference and extend the design language when building new report layouts.

**Open and deferred** (ask first, don't decide unilaterally): outbox storage policy, cron host & threat model, source artifact size, LLM cost cap policy, Δ7d snapshot strategy for indicator grid. See PRD §13.

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
4. Check `projects/<id>/events.ndjson` for the last few events (per-project log; not tracked on dev branches).
5. If project state looks corrupt: the source files and `project.notes.md` are always in git. Re-run the pipeline from a clean state using `uv run python scripts/reset_demo.py` (demo) or by deleting the local `events.ndjson` and `project.md` files and re-running.

---

*CLAUDE.md v0.5.1 — companion to PRD.md v0.5 and ARCHITECTURE.md v0.2.*
*Update this file whenever a working agreement changes.*
