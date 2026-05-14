# PRD — Project Agent

> Version: v0.4
> A git-versioned, AI-augmented project operating system.
> Living project records, machine-readable, signal-first.
>
> **v0.4 changes:** MVP scope trimmed; idempotence contract reworked around source-hash + LLM response cache; `project.md` split into bot-owned canonical + PM-owned notes; Event schema gains `schema_version`, `run_id`, `retracted`, `supersedes`, structured `actor`, discriminated `payload`; operations log added as a first-class artifact; stage 3 renamed Warehouse Load; Phase 0.5 introduced for HTML + MCP slice-out; `velocity_anomaly` and `scope_churn` deferred to Phase 4. See `REVIEW_NOTES.md` for the full review and what was deferred.

---

## 0. Origin & Vision

**Origin.** Project Agent is the next-generation evolution of an existing event-based pipeline already in production:

- Python ingestion of structured (backlog tables) + unstructured (emails, meetings, docs) sources.
- NDJSON consolidation + DuckDB warehouse for queryable state.
- Gmail / Drive / Calendar MCP integrations syncing into project markdown files.
- Static MD + HTML reports following a design system.
- Daily progress detection with PM-actionable outputs.

That pipeline proved the substrate works. Project Agent extends it from a single-purpose reporting tool into a **versioned, multi-project repository where the project record itself is the system of work** — managed by Claude Code locally and Claude API for reasoning.

**Vision.** Move project management from the manual era — where the PM is a curator of slides, spreadsheets, and meeting notes describing what already happened — to a **signal-first** model where:

- Project records are versioned in git, machine-readable, and continuously updated.
- LLM and deterministic detection over those records surface risks, drift, and stakeholder issues *weeks before* they hit a status dashboard.
- The PM stops being a clerical bottleneck and becomes a **Delivery Architect**: someone who designs the signal layer, audits its outputs, and intervenes early.

The PM's focus shifts from managing meetings and stakeholders to:

1. **Data transformation** — curating the schemas and prompts that turn project artifacts into machine-readable records.
2. **App development** — extending the repository with new reports, signals, and (later) agents.
3. **Auditing** — verifying that artifacts trace back to the project source of truth.

---

## 1. Goals

- **G1 — Source of truth.** Every project has one canonical `project.md` corpus, versioned in git, updated by the pipeline without manual edits.
- **G2 — Idempotent consolidation.** A pipeline ingests structured + unstructured sources and consolidates them into an append-only NDJSON event log + DuckDB warehouse. Idempotence is anchored at the *source* (file hash) and reinforced by an LLM response cache, so re-runs against unchanged sources produce zero diff regardless of LLM non-determinism. (See §6.2.)
- **G3 — Signal layer.** Risks, blockers, slipping deliverables, action aging, scope churn, and communication gaps are detected — deterministically where possible, LLM-evaluated where needed — and surfaced in static MD/HTML reports.
- **G4 — PR-driven trust.** Every pipeline run opens a git PR. Nothing reaches `main`, and no email leaves the system, without PM review.
- **G5 — Multi-project from day one.** The same code runs across N projects in a monorepo. MVP exercises 1; architecture supports many.
- **G6 — Open source & cloneable.** A second PM can fork the repo and onboard their own portfolio.
- **G7 — Multi-agent ready.** Architecture enables Phase 5b sub-agents (Risk Agent, Stakeholder Agent, Scope Agent…) without a rewrite. Achieved by the package boundary in §5.1. (See `REVIEW_NOTES.md` §3.8 for the orchestration concerns 5b also has to address.)

## 2. Non-Goals (MVP)

- No PPTX generation. *(Phase 5a, via Cowork skill.)*
- No multi-agent orchestration. *(Phase 5b.)*
- No Jira/Asana writeback. *(Phase 4. Read-only via export until then.)*
- No web dashboard. Static HTML files only.
- No multi-user collaboration semantics. One PM, one repo. Git is the only sync.

---

## 3. Core Concepts

| Term | Definition |
|---|---|
| **Project Agent** | The system as a whole: repository + package + pipeline + (later) agents. |
| **Repository** | The git monorepo containing all managed projects, pipeline code, prompts, outputs. |
| **Package** | `src/project_agent/` — the importable Python package. The substrate. All state mutation goes through it. |
| **Pipeline** | Thin orchestration scripts in `pipeline/` that compose package functions. |
| **Project Record** | The `project.md` at the root of each project — the source of truth. Front-matter + canonical sections. |
| **Sources** | Immutable raw artifacts ingested from MCP or file drops. Append-only; never edited. |
| **Event Log** | `data/events.ndjson` — append-only log of every parsed fact. |
| **Warehouse** | `data/warehouse.duckdb` — materialized, SQL-queryable tables. |
| **Signal** | A typed observation about project health. Deterministic or LLM-derived. |
| **Report** | A rendered artifact (MD + HTML) committed to git. Static, cacheable, diff-able. |
| **PR Draft** | The auto-generated git PR containing the day's changes. PM reviews, edits, merges. |

---

## 4. Architecture

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

All six layers are implemented as modules inside the `project_agent` package (§5.1). Pipeline scripts compose them; agents (Phase 5) wrap them.

Re-runs are safe — events are deduped by content hash.

---

## 5. Repository Layout

```
project-agent/
├── README.md
├── CLAUDE.md                       # operating manual for Claude Code
├── PRD.md
├── pyproject.toml                  # package config, deps, console scripts
├── .claude/
│   ├── commands/                   # /sync, /report, /pr, /signals
│   └── settings.json
├── src/
│   └── project_agent/              # THE PACKAGE — all state-mutating logic
│       ├── __init__.py
│       ├── schemas.py              # pydantic: Event, Signal, Project, ...
│       ├── events.py               # NDJSON append/query/dedupe
│       ├── warehouse.py            # DuckDB load/query
│       ├── projects.py             # project.md parse/render/markers
│       ├── render.py               # MD/HTML emitters
│       ├── git.py                  # branch/PR helpers
│       ├── llm.py                  # Claude API client wrapper
│       ├── ingest/
│       │   ├── __init__.py
│       │   ├── inbox.py            # file-drop reader (MVP)
│       │   ├── backlog.py          # CSV/JSON reader
│       │   └── mcp.py              # Gmail/Drive/Calendar (Phase 1)
│       └── signals/
│           ├── __init__.py
│           ├── deterministic.py    # SQL-based detectors
│           └── llm.py              # Claude-evaluated detectors
├── pipeline/                       # thin orchestration — imports from package
│   ├── __init__.py
│   ├── cli.py                      # entry point
│   └── stages.py                   # one function per layer
├── agents/                         # Phase 5b — empty until then
│   └── .gitkeep
├── projects/                       # managed projects
│   └── <client>--<project>/
│       ├── project.md              # SOURCE OF TRUTH — bot-owned, full-rewrite canonical sections
│       ├── project.notes.md        # PM-owned, NEVER touched by the pipeline. Free-form.
│       ├── sources/                # immutable raw inputs
│       │   ├── _inbox/             # file-drop staging
│       │   ├── emails/
│       │   ├── meetings/
│       │   ├── docs/
│       │   ├── backlog/
│       │   └── manifest.json       # ingestion ledger with hashes
│       ├── derived/                # auto-generated supplementary md
│       └── reports/                # daily MD outputs (HTML added Phase 0.5)
├── portfolio/                      # cross-project rollup (Phase 2)
│   └── portfolio.md
├── data/
│   ├── events.ndjson               # APPEND-ONLY EVENT LOG
│   ├── warehouse.duckdb
│   ├── runs/                       # OPERATIONS LOG — per-run summaries
│   │   └── <run_id>.json           # counts, durations, errors, cost per stage
│   ├── cache/
│   │   └── llm/                    # LLM response cache: keyed on (prompt_version, input_hash)
│   └── schemas/                    # JSON Schema mirrors of pydantic models
├── design-system/                  # HTML/CSS templates + tokens (Phase 0.5)
│   ├── tokens.css
│   ├── templates/
│   │   ├── status.html.j2
│   │   └── portfolio.html.j2
│   └── assets/
├── prompts/                        # versioned Claude API prompts
│   ├── extract-context.md
│   ├── detect-signals.md
│   ├── draft-status.md
│   └── draft-email.md
└── tests/
```

---

### 5.1 Package Boundary *(architectural invariant)*

**The rule:** *agents and pipeline scripts may only read or mutate project state through `project_agent` APIs.* Direct file I/O on `data/events.ndjson`, direct DuckDB connections, direct edits to `project.md`, direct calls to the Anthropic SDK — all forbidden outside the package.

**Why this is in the PRD and not deferred to a later refactor:**

1. **Schema invariants live in one place.** Event hashing, idempotence, pydantic validation — defined inside the package, enforced for every caller. A pipeline script and a Phase 5 agent see the same `Event` type.
2. **Phase 5 sub-agents become thin.** A Risk Agent in Phase 5 is ~200 lines: a system prompt, tool definitions wrapping `project_agent.signals.write_signal` / `project_agent.events.query`, and a Claude API loop. It does not reimplement file paths or NDJSON serialization.
3. **Tests target functions, not script side effects.** `pytest` against `project_agent.events.append(...)` is straightforward. Testing a 400-line orchestration script that does I/O and shells out is not.
4. **The substrate stays auditable.** The auditing focus (G3, vision §0) requires every state mutation to be traceable. One package = one audit surface.

**Three layers, each boring:**

| Layer | What lives here | What does NOT live here |
|---|---|---|
| **Substrate** (`src/project_agent/`) | Pure functions, pydantic models, narrow I/O primitives. | Orchestration logic, CLI parsing, agent loops. |
| **Glue** (`pipeline/`) | Thin imperative scripts: read args → call package → log. | Direct file I/O, direct DuckDB, direct API calls. |
| **Wrappers** (`agents/`, Phase 5) | Claude API loops with tool definitions that call the package. | Schema redefinition, custom event formats, anything not also available to the substrate. |

**Anti-goals for the substrate:** no abstract base classes, no plugin registries, no DI containers, no metaprogramming. Pure functions and pydantic. If Phase 5 needs polymorphism, add it then with evidence.

**Enforcement:** the test suite includes a static lint that fails CI if any module under `pipeline/` or `agents/` imports `duckdb`, opens `*.ndjson` directly, calls `anthropic.*` directly, or writes to `project.md` outside `project_agent.projects.*`. This is an MVP DoD item (§10).

> **Known lint limitations.** The lint is a regex/AST check over direct imports and a small set of forbidden calls. It does not trace transitive imports, `importlib.import_module(...)`, or re-exports. It catches honest mistakes, not deliberate circumvention. The point is to make the boundary cheap to maintain, not airtight against an adversary. Documented; do not invest further until evidence demands it.

---

## 6. Data Contracts

### 6.1 `project.md` + `project.notes.md` (two-file split)

The project record is split across **two files** in each project folder. The split exists to keep the bot/PM ownership boundary unambiguous and to remove a class of "I edited it and the next run wiped my edits" failures.

#### `project.md` — bot-owned, full-rewrite

Everything inside is overwritten by the parse layer on every run. The PM does not edit this file by hand. If they do, edits are clobbered without warning — that is the contract, not a bug.

```yaml
---
id: acme--demo-platform-2026
client: Acme Corp
project_name: Demo Platform
status: green | yellow | red
phase: discovery | execution | closing
start_date: 2026-01-15
target_end_date: 2026-09-30
delivery_manager: <your-name>
stakeholders:
  - { name: Alex, role: Sponsor, org: Acme Corp }
last_updated: 2026-05-10T08:00:00-03:00
schema_version: 1
---

## Mission
<one paragraph — stable, regenerated from sources>

## Scope
- In: ...
- Out: ...

## Milestones
- [ ] M1 — Discovery complete (2026-02-15)
- [x] M2 — Architecture approved (2026-03-20)

## Risks
| ID | Description | Owner | Severity | Status | Source |

## Decisions
| Date | Decision | Rationale | Source |

## Actions
| ID | Description | Owner | Due | Status | Source |

## Stakeholder Map
...
```

#### `project.notes.md` — PM-owned, never touched

Free-form markdown for hypotheses, context, side conversations, anything the PM wants to write. The pipeline reads this file as a *source* (it's an input to Parse like any other) but **never writes to it**. There is no schema. There is no canonical structure. It is the PM's notebook.

The render layer composes `project.md` + `project.notes.md` into the daily status report; neither file alone is the report.

> **Why not the `<!-- pm:* -->` marker approach from v0.3?** It demanded the PM remember to wrap edits in markers that don't render visibly, in a file the bot also rewrites. The first time markers were forgotten — or the first time a PM edit crossed a section boundary — work would be lost without warning. The two-file split costs one extra file per project and removes the failure mode entirely. See `REVIEW_NOTES.md` for the analysis.

#### Idempotence implication

Because `project.md` is full-rewrite from sources, the Parse layer must be deterministic given the same inputs. That determinism is provided by the LLM response cache (§6.2 below); without it, `project.md` would differ between runs and idempotence would not hold.

### 6.2 Event Log (NDJSON, one event per line)

```json
{
  "event_id": "uuid-v4",
  "schema_version": 1,
  "ts": "2026-05-10T08:00:00-03:00",
  "run_id": "2026-05-10T08:00:00-03:00--abc12",
  "project_id": "acme--demo-platform-2026",
  "type": "risk_added | action_added | decision_made | status_change | source_ingested | signal_detected | event_retracted | ...",
  "actor": {
    "kind": "llm | human | mcp | deterministic",
    "model":  "claude-sonnet-4-7",
    "prompt": "extract-context.md@3",
    "id":     "<user>",
    "source": "gmail"
  },
  "source_ref": "projects/.../sources/emails/2026-05-09-alex.eml",
  "source_hash": "sha256:abc123...",
  "payload": { "type": "<event-type>", "...": "type-specific fields" },
  "confidence": 0.93,
  "supersedes":     ["event_id_x", "event_id_y"],
  "superseded_by":  null,
  "retracted":      false,
  "retracted_reason": null,
  "hash": "sha256(canonical(payload) + source_hash)"
}
```

The `actor` object is a discriminated union — only the fields relevant to its `kind` are populated. The `payload` object is a discriminated union over `type`; per-type pydantic models live in `project_agent.schemas` (no `dict[str, Any]`).

**New since v0.3:** `schema_version`, `run_id`, `source_hash`, structured `actor`, `confidence`, `supersedes` / `superseded_by`, `retracted` / `retracted_reason`. Each costs one pydantic field now and saves a migration in 3 months.

#### Idempotence contract (revised)

Idempotence is enforced at **two layers**, neither of which depends on LLM determinism:

1. **Source layer.** Every ingested file gets a `source_ingested` event, keyed on `source_hash = sha256(file_bytes)`. The same file ingested twice produces zero new `source_ingested` events. This is enforced by `manifest.json`.
2. **Parse layer.** Parse is wrapped in an LLM response cache keyed on `(prompt_version, source_hash, model)`. The first parse calls Claude; every subsequent parse of the same source under the same prompt version replays the cached response. Events derived from parse are therefore byte-identical across runs.

Event-level dedup (`hash` field) is a secondary defense, not the primary one. With source-hash + LLM cache, the same source cannot produce events with different hashes under the same prompt version. This was the structural flaw in v0.3: hashing LLM output and calling it idempotent only worked if the LLM was deterministic, which it is not.

**Edited-source semantics.** When a source is re-ingested with a different `source_hash` (e.g. an email was edited, a doc was updated):
- A new `source_ingested` event fires.
- Parse re-runs against the new content.
- New events emitted from the new parse carry `supersedes: [<old_event_ids>]` for the equivalent facts from the prior version.
- The old events remain in the log but are findable via `superseded_by`. Downstream queries (warehouse views, signal detectors) filter on `superseded_by IS NULL` by default.

**Prompt-version semantics.** When a prompt is rewritten (e.g. `extract-context.md` goes from v3 to v4), the cache key changes; every source is re-parsed under the new prompt. New events carry `supersedes` against the prior-version events for the same source. This is also how backfill/replay works — bump the prompt version, re-run, supersede chain links new to old. Audit-clean.

**Retraction.** If the LLM hallucinates an event that survived review (or a deterministic detector misfires on bad data), the operator may emit an `event_retracted` event identifying the bad `event_id`. The retracted event stays in the log; queries filter on `retracted = false` by default. There is no in-place mutation, ever.

### 6.3 Signal Schema

```json
{
  "signal_id": "uuid-v4",
  "schema_version": 1,
  "project_id": "...",
  "run_id": "...",
  "category": "risk | deliverable | action | blocker | backlog | communication",
  "type": "<see signal catalog §7>",
  "severity": "low | medium | high",
  "confidence": 0.0,
  "evidence": ["event_id_1", "event_id_2"],
  "method": "deterministic | llm",
  "rationale": "string",
  "detected_at": "...",
  "recommended_action": "..."
}
```

Signals are written back to `events.ndjson` as `signal_detected` events (the `payload` carries the signal body).

**Evidence-linking implementation requirements** (new in v0.4 — was implicit in v0.3):

- **Deterministic SQL detectors** must return the `event_id`s that triggered the match, not just an aggregate. A `risk_unaddressed` detector returns one row per (risk, latest_status_event_id, threshold_breach_event_id) tuple; the warehouse views must expose `event_id` on each row to make this possible.
- **LLM detectors** must be given concrete `event_id`s in the prompt and required to return them in the response. Prompts that ask "did anything bad happen?" without anchoring on specific events are not allowed; their output cannot be audited.

**Anti-circularity invariant** (relevant Phase 1+, when LLM signals come online):

> LLM signal prompts may cite only non-`signal_detected` events as evidence. Signal detectors of any kind must skip `signal_detected` events when reading the warehouse.

This breaks the chain that would otherwise let `risk_escalation` cite last week's `risk_emergent` as evidence, compounding LLM confidence with no primary source. State it now so it isn't bolted on later.

### 6.4 Operations Log

Distinct from `events.ndjson` (which is project content). The operations log is the system's record of *itself*.

```
data/runs/<run_id>.json
```

```json
{
  "run_id": "2026-05-10T08:00:00-03:00--abc12",
  "started_at": "2026-05-10T08:00:00-03:00",
  "ended_at":   "2026-05-10T08:00:47-03:00",
  "project_ids": ["acme--demo-platform-2026"],
  "stages": [
    {
      "name": "ingest",
      "status": "ok",
      "duration_ms": 1240,
      "counts": { "sources_seen": 7, "sources_new": 2, "errors": 0 }
    },
    {
      "name": "parse",
      "status": "ok",
      "duration_ms": 18430,
      "counts": { "sources_parsed": 2, "events_emitted": 9, "cache_hits": 5, "cache_misses": 2 },
      "cost_usd": 0.082
    }
  ],
  "errors": [],
  "total_cost_usd": 0.082
}
```

This is what you read at 3am when something is wrong. Without it, debugging is `git log` archaeology over `events.ndjson`, which is the wrong tool for the question "did the pipeline run yesterday and did Parse complete?"

`run_id` on every event in `events.ndjson` joins back to this file.

---

## 7. Signal Catalog

Organized by the six surveillance categories aligned to the PM's actionable surface.

| Category | Signal | Method | Phase | Definition |
|---|---|---|---|---|
| **Risk** | `risk_unaddressed` | deterministic | 1 | Risk open with no status change in N days. |
| **Risk** | `risk_escalation` | LLM | 1 | Tone or content across recent sources suggests an existing risk is worsening. |
| **Risk** | `risk_emergent` | LLM | 1 | Pattern in recent events suggests a new risk worth raising — not yet captured deterministically. |
| **Deliverable** | `deliverable_drift` | deterministic | 1 | Target date moved ≥ 2 times, or open within 7 days of target with status off-track. |
| **Deliverable** | `deliverable_dependency_stall` | **LLM** | 2 | Deliverable depends on action/blocker that is itself flagged. *Reclassified to LLM: the dependency edges are not first-class in the schema, so determinism is not possible without a schema extension. Defer to Phase 2 or add `depends_on` to milestones earlier.* |
| **Action** | **`action_aging`** | deterministic | **MVP** | Action open > X days past due, or open > Y days with no status change. |
| **Action** | **`action_unowned`** | deterministic | **MVP** | Action without owner or due date for ≥ 48h. |
| **Blocker** | **`blocker_unowned`** | deterministic | **MVP** | Blocker without owner or due date for ≥ 48h. |
| **Blocker** | `blocker_aging` | deterministic | 1 | Blocker open > X days. |
| **Backlog** | `scope_churn` | deterministic | **4** | Backlog deltas (added/removed/re-estimated) > X% week-over-week. *Requires Jira/backlog integration — deferred from Phase 1 to Phase 4 in v0.4: cannot fire on data we don't have.* |
| **Backlog** | `velocity_anomaly` | deterministic | **4** | Throughput drops > X% vs rolling baseline. *Same: requires sprint cadence data. Deferred to Phase 4.* |
| **Communication** | `stakeholder_inactivity` | deterministic | 1 | No inbound from a key stakeholder for ≥ N business days. Threshold per role (requires stakeholder cadence config in `project.md` front-matter). |
| **Communication** | `tone_shift` | LLM | 1 | Sentiment / escalation language detected in recent stakeholder messages. |

**MVP fires only deterministic signals** in the Action and Blocker categories: `action_aging`, `action_unowned`, `blocker_unowned` (bolded above). Everything else lights up across Phases 1–2 (and 4 for backlog signals).

Both methods are first-class. Deterministic signals are the auditable backbone; LLM signals are early-warning radar. Each signal has a JSON spec under `data/schemas/signals/<type>.json` defining thresholds, prompt anchors, and severity rules.

### 7.1 Candidate signals (under review, not yet committed)

These were flagged in design review as likely gaps. Promote to the catalog above with a PR after MVP if they prove useful. See `REVIEW_NOTES.md`.

| Candidate | Method | Rationale |
|---|---|---|
| `decision_pending` | deterministic | A decision asked-for in a source but no `decision_made` event within N days. Probably the most common PM blind spot. |
| `action_reopened` | deterministic | An action moved from done back to open. Strong leading indicator. |
| `commitment_unconfirmed` | LLM | "I'll do X by Friday" with no confirming event by Friday+1. The honest version of `action_aging` for verbal commits. |
| `stakeholder_specific_inactivity` | deterministic | Distinct from the aggregate signal: "Alex was asked X 5 days ago, no response." |

### 7.2 Implementation notes (new in v0.4)

- **Evidence linking is mandatory.** See §6.3. Deterministic detectors return triggering `event_id`s; LLM detectors must cite them. A signal without traceable evidence is treated as a bug, not a soft warning.
- **Anti-circularity.** Signal detectors skip `signal_detected` events when reading the warehouse. LLM prompts may not cite `signal_detected` events. See §6.3.

---

## 8. Pipeline Stages

CLI: `python -m pipeline run --project <id>` or `python -m pipeline run --all`.

Each stage is one function in `pipeline/stages.py` that imports primitives from `project_agent.*`. Every stage writes a stage summary into the operations log (§6.4) before returning.

### 8.1 Ingest
- **MVP:** `project_agent.ingest.inbox` scans `sources/_inbox/`, classifies by extension, moves to typed folders, updates `manifest.json` with hashes. **File-drop is the only ingest path in MVP.** This isolates pipeline-shape bugs from auth/network/vendor variance.
- **MVP:** `project_agent.ingest.backlog` reads CSV/JSON via a per-project mapper config.
- **Phase 0.5:** `project_agent.ingest.mcp` ports Gmail/Drive/Calendar integrations from the existing pipeline.
- **Idempotence:** files already in `manifest.json` (by `source_hash`) are skipped, regardless of ingest source. The first time a file is seen, a `source_ingested` event is emitted with the hash.

> **Deferred decision — MCP vs native Google API (revisit in Phase 1+).**
> The existing pipeline uses Python MCP clients (no LLM in the transport path), so the cost concern attributed to MCP in earlier drafts was misframed. With Python MCP clients, daily token cost from transport is effectively zero. A future migration to native Google API clients (`google-api-python-client`) under the same `project_agent.ingest.*` module signatures remains an option, but is not currently motivated. Trigger to revisit: protocol churn, rate-limit pain, or auth-refresh complexity that wouldn't be solved equivalently in either world.

### 8.2 Parse
- For each new source, calls `project_agent.llm.extract` with `prompts/extract-context.md`.
- **LLM responses are cached** under `data/cache/llm/` keyed on `(prompt_version, source_hash, model)`. Cache hits replay byte-identical responses; cache misses call Claude and write the response. This is what makes Parse deterministic — and therefore what makes the system idempotent under daily cron (§6.2).
- Output: structured facts validated against pydantic models in `project_agent.schemas` (discriminated payload per event `type`).
- `project_agent.projects.update` rewrites canonical sections in `project.md` end-to-end. `project.notes.md` is **never written to** by this stage.
- Each extracted fact emits an event via `project_agent.events.append`. `confidence` from the extraction is carried through.
- Soft-validation failures (e.g. owner not in stakeholder list) tag the event with a `needs_review` flag in payload rather than rejecting; the PR surfaces these for the PM.

### 8.3 Warehouse Load *(renamed from Consolidate in v0.4)*
- `project_agent.warehouse.load` materializes `events.ndjson` into DuckDB views: `events`, `actions`, `blockers` for MVP; `risks`, `decisions`, `milestones`, `stakeholders`, `signals` added as later phases need them.
- Views filter `retracted = false` and `superseded_by IS NULL` by default. A `*_full` view is available for audit queries.
- Every view exposes `event_id` on the rows it derives from, so downstream detectors can return evidence (§6.3).

> **Why renamed.** In v0.3 this stage was called "Consolidate" alongside a description of "ensuring every fact is represented as an event" — but Parse (§8.2) already emits events. The only work remaining here is warehouse materialization. Naming it Warehouse Load reflects what the code actually does. See `REVIEW_NOTES.md`.

### 8.4 Analyze
- `project_agent.signals.deterministic` runs SQL queries for the deterministic signal types (per §7 catalog). Each query returns `event_id`s along with the aggregate.
- `project_agent.signals.llm` (Phase 1+) calls Claude API with `prompts/detect-signals.md` for LLM signals. Prompts receive concrete `event_id`s as input and must return them as evidence (§6.3).
- Both kinds skip `signal_detected` events when reading the warehouse (anti-circularity invariant, §6.3).
- Both write `signal_detected` events back via `project_agent.events.append`.

### 8.5 Render
- **MVP:** `project_agent.render.status` emits per-project `reports/status-<date>.md`. Markdown only.
- The renderer composes inputs from `project.md` + `project.notes.md` + signals from this run. The output is regenerable from those inputs alone — PM edits to a rendered report would be overwritten by the next run and are not supported.
- **Phase 0.5:** add HTML rendering with Jinja2 templates in `design-system/templates/` and CSS tokens.
- **Phase 2:** `project_agent.render.portfolio` emits the portfolio rollup.

### 8.6 Commit
- `project_agent.git.branch` creates `auto/<date>`.
- `project_agent.git.pr` opens a PR with summary: new signals, new actions, status delta, links to reports, ops-log link.
- The full operations log (`data/runs/<run_id>.json`) is committed in this stage; it's how the PM sees per-stage counts and any errors at a glance.
- PM reviews → edits → merges.
- **Never** pushes to `main`. **Never** sends external messages without explicit approval flag (Phase 3+).

---

## 9. Human-in-the-Loop Model

| Output | MVP behavior | Later |
|---|---|---|
| `project.md` updates | PR diff, PM merges. PM does **not** edit by hand (use `project.notes.md`). | Same |
| `project.notes.md` | PM writes freely between runs. The pipeline reads it as a source, never writes to it. | Same |
| Status report (MD) | Drafted in PR. Regenerable from `project.md` + `project.notes.md`; not PM-edited. | HTML rendering added Phase 0.5 |
| Signal alerts | Listed in PR description | + Slack/email notification (P3) |
| Risks in `project.md` | Drafted by Claude in PR | Same |
| Client emails | Drafted as `outbox/<date>-<recipient>.md`; PM copies and sends manually. **`outbox/` should be gitignored or routed to a separate private store** — see §13.6. | Sent via Gmail MCP after explicit `--send` flag (P3) |
| Calendar events | n/a | Drafted via Calendar MCP, PM approves (P3) |
| Operations log | Committed with each PR for at-a-glance run health. | Same |

**The invariant:** the pipeline never takes externally observable action without a merged PR.

---

## 10. MVP Scope (Phase 0)

**Definition of Done:**

1. One project (`projects/<one-project>/`) onboarded with hand-curated `project.md`, an empty (or seeded) `project.notes.md`, and a folder of past artifacts dropped into `sources/_inbox/`.
2. `python -m pipeline run --project <id>` executes all six stages end-to-end with no manual intervention. Ingest is **file-drop only** in MVP (MCP is Phase 0.5).
3. Re-running on the same day produces zero diff (idempotence verified by `git status` clean after a second run). The LLM response cache (§6.2) is what makes this hold.
4. NDJSON contains ≥ 20 events spanning ≥ 7 days of project history. *(Reduced from 100 events / 30 days in v0.3 — smaller fixtures are enough to prove the pipeline shape; backfill of real history is a separate exercise.)*
5. DuckDB warehouse exposes the tables needed by MVP signals: `events`, `actions`, `blockers`. *(Reduced from 5 tables; `risks`, `decisions`, `milestones`, `stakeholders` are added in Phase 1 when signals on them fire.)*
6. At least 3 deterministic signals detected on real data: `action_aging`, `action_unowned`, `blocker_unowned`. Each signal's `signal_detected` event carries `evidence: [event_id, ...]` linking to triggering rows (§6.3).
7. MD daily status report renders, committed via PR. *(HTML rendering deferred to Phase 0.5.)*
8. **All state mutations go through `project_agent` package APIs** — package-boundary lint passes (§5.1 invariant).
9. `project_agent.events.query` exists as a first-class API (filter by project, type, time range, retracted/superseded). Without this, signals cannot be written and the audit story is theoretical.
10. Operations log (`data/runs/<run_id>.json`, §6.4) written per run with per-stage counts, durations, and errors. Committed with the PR.
11. Event schema includes `schema_version`, `run_id`, `source_hash`, structured `actor`, `confidence`, `supersedes`/`superseded_by`, `retracted`/`retracted_reason`. The `payload` is a discriminated union with a pydantic model per event `type`.
12. Test suite covers: schema validation, idempotence per stage, the boundary lint, the LLM-cache-hit path, and a smoke test asserting `events.query` returns deterministic results across two runs.

**Out of scope for MVP (moved or deferred):**
- HTML rendering with design-system tokens → Phase 0.5.
- MCP ingest (Gmail/Drive/Calendar) → Phase 0.5.
- 30-day backfill of historical sources → exercise after MVP shape is verified.
- LLM signals → Phase 1.
- Stakeholder cadence config, `risks`/`decisions`/`milestones` warehouse tables → Phase 1 alongside signals that need them.

**Estimated scope:** 1–2 weekends of focused build with Claude Code.

---

## 11. Phased Roadmap

### Phase 0.5 — HTML + MCP (sliced out of MVP in v0.4)
- Add HTML rendering with Jinja2 templates and design-system tokens.
- Port `project_agent.ingest.mcp` (Gmail/Drive/Calendar) from the existing pipeline.
- Backfill ≥ 30 days of real history on the MVP project.
- **Portfolio PR shape decision** (new in v0.4): even with one project, decide whether the daily PR is project-scoped or portfolio-scoped before Phase 2 starts adding projects. The portfolio-shape PR (one PR per run with project subdirs and a top-of-PR digest) scales better past 2 projects; the project-shape PR is simpler at one. Choose explicitly and document. See `REVIEW_NOTES.md`.

### Phase 1 — LLM Signals + Scheduled Operation
- Activate `risk_escalation`, `risk_emergent`, `tone_shift` LLM signals.
- Promote `risk_unaddressed`, `deliverable_drift`, `blocker_aging`, `stakeholder_inactivity` deterministic signals as their warehouse tables come online.
- Add `risks`, `decisions`, `milestones`, `stakeholders` warehouse views.
- Schedule daily run via local cron / launchd.
- Define cron-host threat model: where do OAuth tokens live, where does pipeline stdout/stderr go, what's the failure-loud guarantee on auth refresh.
- Per-project per-day LLM cost cap; operational signal when cap is approached.

### Phase 2 — Portfolio Layer
- `python -m pipeline run --all` across `projects/*`.
- Portfolio MD + HTML in `portfolio/`.
- Cross-project signals: resource conflicts, stakeholder overload, correlated drift.
- `deliverable_dependency_stall` (reclassified to LLM) goes live here.
- Review candidate signals from §7.1 and promote those that earn it.

### Phase 3 — Drafted Communications
- Client status emails drafted in `outbox/` (gitignored or routed to private store; see §13.6).
- 1:1 prep notes auto-generated from upcoming Calendar events.
- Optional `--send` flag for Gmail MCP after PM approval.
- Slack/email notification of new PRs.

### Phase 4 — Backlog Integration
- Native Jira read API replaces CSV export.
- `scope_churn` and `velocity_anomaly` signals come online (deferred from Phase 1 in v0.4 — they require the data this phase provides).
- Round-trip: PR-approved actions create Jira issues.

### Phase 5 — PPTX & Multi-Agent (split into two milestones)
- **5a — PPTX:** Steerco deck generation via Cowork skill. Render target only; no architectural shift.
- **5b — Multi-Agent:** Specialized sub-agents under `agents/`: Risk Agent, Stakeholder Agent, Scope Agent. Each is a thin wrapper over `project_agent` APIs (per §5.1). The package boundary is what makes this milestone tractable rather than a rewrite — but note that the boundary alone does not solve agent orchestration (conflict resolution, concurrency, cost budgeting). Those are 5b's actual work.

### Phase 6 — Open Source Release
- Strip personal config to `.example` files.
- PII/confidentiality boundary defined and tested: redaction layer, "internal-only" event flag honored by render.
- Onboarding doc, demo project, screencast.
- License (MIT or Apache-2), contribution guide.

---

## 12. Acceptance Criteria per Phase

| Phase | Acceptance |
|---|---|
| MVP | DoD §10 verified end-to-end. Package-boundary lint passes. Idempotence verified across two consecutive runs. |
| 0.5 | HTML report renders with design system. MCP ingest pulls real Gmail/Drive/Calendar data without regression to idempotence. ≥ 30 days of real history backfilled. Portfolio-PR-shape decision made. |
| 1 | ≥ 1 LLM signal fires on real data; daily cron runs without manual intervention for ≥ 5 consecutive days; cost cap enforced. |
| 2 | Portfolio HTML renders ≥ 3 projects with ≥ 1 cross-project signal. |
| 3 | PR contains ≥ 1 outbox email draft per week, PM-merged and sent. `outbox/` storage policy implemented. |
| 4 | Jira read parity with CSV; one Jira issue created round-trip from a merged PR action. `scope_churn` and `velocity_anomaly` fire. |
| 5a | Steerco PPTX generated for one project, reviewed and approved. |
| 5b | At least one sub-agent in `agents/` operating under §5.1 with a documented orchestration policy (conflict resolution, cost budgeting). |
| 6 | Repo cloned + onboarded by a second PM in < 1 hour following docs. PII/confidentiality boundary tested. |

---

## 13. Open Questions

1. **Project ID convention:** `<client>--<project>` or `<year>-<client>-<project>`?
2. **Sensitive data:** Private repo per engagement, or one private monorepo with `.gitignore` boundaries? Public repo carries only the substrate (no client data) for the OSS release.
3. **PM edits to `project.md`:** *Closed in v0.4.* The two-file split (`project.md` bot-owned, `project.notes.md` PM-owned) replaces the `<!-- pm:* -->` marker mechanism from v0.3.
4. **Design system source:** ship a default open-source token set, or leave tokens.css as an empty stub for forks to fill in?
5. **Signal thresholds:** Where do per-signal thresholds (N days, X%) live — global config, per-project override, or both? *Recommendation: global defaults in `config/signals.yaml`, per-project overrides in `project.md` front-matter.*
6. **`outbox/` storage policy (Phase 3):** drafts in git history are a confidentiality risk. Options: `outbox/` is gitignored and writes to a parallel private store; `outbox/` lives in a sibling repo that isn't pushed; drafts redacted to placeholders before commit. Decide before Phase 3.
7. **Cron host threat model (Phase 1):** where does the pipeline run? Laptop (sleep = no run), server (OAuth tokens at rest = different threat model), GitHub Action (tokens in CI secrets). Decide before scheduling.
8. **Portfolio PR shape (Phase 0.5):** project-scoped daily PRs or portfolio-scoped with project subdirs and a digest? Project-scoped is simpler at 1; portfolio-scoped scales better past 2. Decide before adding a second project.
9. **Source artifact size:** large PDFs / email attachments will bloat the repo. Git LFS, external blob store, or `.gitignore` raw binaries and rely on the parsed-text representation? Decide before backfilling real history.
10. **LLM cost cap (Phase 1):** per-project per-day budget; what's the policy when the cap is hit — fail the run, skip LLM signals, or warn-and-continue?
11. **Test data origin:** real client data (tests can't be public) or synthetic (need a generator)? Affects MVP DoD #4 once OSS release is contemplated.
12. **OAuth refresh failure mode (Phase 0.5):** when tokens expire silently, the PR is empty. Should this fire an `ingest_failed` event and a `pipeline_health` signal so silent failure is loud?

---

## 14. References

- Predecessor event-based pipeline (Python + NDJSON + DuckDB + Gmail/Drive/Calendar) — the proof-of-substrate this project generalizes.
- Design system tokens — TBD; placeholder until a default open-source token set is committed.

---

## 15. Execution Plan for Claude Code

When this PRD is dropped into a fresh repo as `PRD.md`, the prompt to Claude Code is:

> Read `PRD.md` and `CLAUDE.md`.
> Scaffold the repo per §5, with `src/project_agent/` as a proper Python package configured in `pyproject.toml`.
> Implement MVP (§10) one stage at a time, with tests for schema validation, idempotence (including LLM-cache-hit path), and the §5.1 package-boundary invariant.
> After each stage: open a PR, wait for review.
> After MVP merges: stop. Wait for explicit instruction to proceed to Phase 0.5.

Each stage (§8) is its own PR. No stage is "done" until idempotence is verified by a clean `git status` after a second run **and** no `pipeline/` or `agents/` module imports DuckDB, opens NDJSON, or calls the Anthropic SDK directly **and** the operations log is populated for both runs **and** the `events.query` API returns identical results for both runs.

---

*End of PRD v0.4.*
