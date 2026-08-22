# PRD — Project Agent

> Version: v2.0
> Status: Phase 2 (agent lane) — Phases 0, 0.5, 1 and the knowledge-base migration are shipped.

> **v2.0 changes (the knowledge-base migration).** `project.md` is retired. A project's
> state is now a compiled wiki under `kb/`, read in Obsidian, written only by
> `project_agent.wiki`. A Compile stage sits between Parse and Warehouse Load, split into
> a per-project Phase A and a portfolio-wide Phase B. The warehouse indexes the vault
> alongside the event log, so an article joins back to the events it was compiled from.
> `sources/` is renamed `raw/`. A second lane carries a research corpus that shares the
> concept layer. The Python Google adapters are retired in favour of Claude Code's MCP
> connectors. Per-project status reports are retired in favour of the vault; the portfolio
> dashboard remains. `docs/*.html` is deleted — this document and `CLAUDE.md` carry the
> architecture.

---

## 0. Origin & Vision

**Origin.** Project Agent began as an event-based reporting pipeline: Python ingestion of
structured (backlog tables) and unstructured (emails, meetings, docs) sources, NDJSON
consolidation into a DuckDB warehouse, and static MD + HTML reports. That pipeline proved
the substrate. Project Agent extended it into a versioned, multi-project repository where
the project record *is* the system of work.

**The v2.0 turn.** Two ideas reshaped the compiled layer:

- **Karpathy's LLM knowledge bases.** Raw documents indexed into `raw/`, incrementally
  compiled by a model into a wiki of markdown — summaries, backlinks, concept articles —
  read in Obsidian, queried by an agent, health-checked, with answers filed back so
  explorations accumulate.
- **DuckDB over an Obsidian vault** (MotherDuck). Markdown as the substrate an agent and
  SQL can both read, with computed values inlined into notes so the answer is read rather
  than recomputed.

The flat `project.md` was the wrong shape for this. One file of pipe-delimited lines has
no links, no per-entity narrative, and no way back from a rendered row to the event that
produced it. The wiki replaces it.

**Vision.** Move project management from the manual era — the PM as curator of slides and
meeting notes describing what already happened — to a **signal-first** model where:

- Project records are versioned in git, machine-readable, continuously updated, and
  navigable as a linked knowledge base rather than a document.
- Detection over those records surfaces risks, drift and stakeholder issues *weeks before*
  they reach a status dashboard.
- The PM becomes a **Delivery Architect**: someone who designs the signal layer, audits
  its outputs, and intervenes early.

---

## 1. Goals

- **G1 — Source of truth.** Every project's state is a compiled wiki under
  `kb/projects/<id>/`, versioned in git, written only by the pipeline. The PM never edits
  it; their input is `project.notes.md`.
- **G2 — Idempotent compilation.** Re-running against unchanged sources produces a zero
  diff. Idempotence is anchored at the source (file hash), reinforced by an LLM response
  cache, and extended to the wiki by content-addressing every article on its inputs
  (§6.1).
- **G3 — Signal layer.** Risks, blockers, slipping deliverables, action aging and
  communication gaps are detected — deterministically where possible, LLM-evaluated where
  needed — and surfaced as events and in the portfolio dashboard.
- **G4 — PR-driven trust.** Every pipeline run opens a git PR. Nothing reaches `main`, and
  no email leaves the system, without PM review.
- **G5 — Multi-project from day one.** The same code runs across N projects. Shared
  people, clients and concepts fan in across them.
- **G6 — Queryable knowledge.** The vault is answerable: BM25-style search and SQL over
  articles, links, backlinks and provenance, designed for an agent to shell out to.
- **G7 — Open source & cloneable.** A second PM can fork the repo and onboard a portfolio.
- **G8 — Agent-ready.** The architecture lets Claude Code do the reasoning the pipeline
  cannot, without a rewrite — achieved by the package boundary (§5.1) and the
  agent-emits-events rule (§4.3).

## 2. Non-Goals

- No PPTX generation. *(Phase 5a.)*
- No multi-agent orchestration. *(Phase 5b.)*
- No Jira/Asana writeback. *(Phase 4. Read-only via export until then.)*
- No web dashboard. Static HTML plus the Obsidian vault.
- No multi-user collaboration semantics. One PM, one repo. Git is the only sync.
- No image or PDF decoding yet. See §13.9.

---

## 3. Core Concepts

| Term | Definition |
|---|---|
| **Package** | `src/project_agent/` — the substrate. All state mutation goes through it. |
| **Pipeline** | Thin orchestration in `pipeline/` that composes package functions. |
| **Raw corpus** | `projects/<id>/raw/` — immutable ingested artifacts. Append-only, never edited. |
| **Event log** | `projects/<id>/events.ndjson` — append-only log of every parsed fact. |
| **Vault / wiki** | `kb/` — the compiled markdown knowledge base. Bot-owned; the repo root opens as an Obsidian vault. |
| **Article** | One markdown file in the vault, content-addressed on the events that produced it. |
| **`input_hash`** | The canonical JSON hash of an article's inputs. Its identity, and the LLM cache key for its prose. |
| **Registry** | `kb/_registry/*.yml` — human-owned alias tables. The only entity resolution the compiler performs. |
| **Lane** | An ingest path with its own prompts and event types. Two exist: project and research. |
| **Warehouse** | `data/warehouse.duckdb` — events *and* the vault, SQL-queryable. |
| **Signal** | A typed observation about project health. Deterministic or LLM-derived. |
| **PR Draft** | The auto-generated git PR containing the run's changes. |

---

## 4. Architecture

### 4.1 The stack

```
┌───────────────────────────────────────────────────────┐
│  6. Commit         → auto/<date> branch + PR (HITL)   │
├───────────────────────────────────────────────────────┤
│  5. Render         → portfolio dashboard (HTML)       │
├───────────────────────────────────────────────────────┤
│  4. Analyze        → deterministic + LLM signals      │
├───────────────────────────────────────────────────────┤
│  3. Warehouse Load → events + kb/ → DuckDB            │
├───────────────────────────────────────────────────────┤
│  2b. Compile       → events → kb/ articles            │
├───────────────────────────────────────────────────────┤
│  2. Parse          → raw documents → fact events      │
├───────────────────────────────────────────────────────┤
│  1. Ingest         → raw/_inbox/ → typed folders      │
└───────────────────────────────────────────────────────┘

On demand:  lint · search · wiki-sql · research
```

Three code layers: **Substrate** (`src/project_agent/` — pure functions, pydantic models,
all state mutation), **Glue** (`pipeline/` — thin imperative stages), **Wrappers**
(`agents/`, Phase 5b).

### 4.2 Two lanes, one concept layer

The **project lane** ingests engagement material and compiles risks, decisions,
milestones and source notes. The **research lane** (`research/raw/`) ingests papers,
articles and clippings and compiles source notes plus concepts.

They meet at `kb/concepts/`. A concept extracted from a paper is the same article a
project risk links to — that shared layer is the reason for a single vault rather than one
per engagement.

Research documents are never passed to the signal detectors. `_discover_projects` keys on
`project.notes.md`, which `research/` deliberately lacks: a paper is not a delivery risk.

### 4.3 The hybrid compiler

Two intelligences maintain the wiki, and one rule keeps them from becoming two systems:

> **The agent's output is events, never articles.**

The deterministic compiler projects events into articles. Claude Code — reading the vault
through `search`, `wiki-sql` and plain file reads — forms judgments the pipeline cannot,
and emits **events**. Both land at the same seam. Consequences:

- `project_agent.wiki` stays the single writer for `kb/`; the boundary lint enforces it.
- Provenance holds: an agent-authored concept carries an `event_id` and an actor, exactly
  as an API-authored one does.
- **Zero-diff holds.** Same events → same `input_hash` → same bytes, whichever
  intelligence produced the events.
- The HITL gate is unchanged: everything lands as a PR diff.

This is what the pipeline structurally cannot do alone, and what the agent lane addresses:
naming concepts that run through project material, merging two phrasings of one risk,
reorganizing as the corpus grows. All three require reasoning *across* the vault, which a
per-article prompt cannot reach at any prompt quality.

---

## 5. Repository Layout

```
project-agent/                      # the repo root IS the Obsidian vault
├── CLAUDE.md  PRD.md  README.md
├── .obsidian/                      # committed vault config
├── kb/                             # THE WIKI — bot-owned, committed
│   ├── index.md                    # portfolio home
│   ├── projects/<id>/              # index.md + risks/ decisions/ milestones/ sources/
│   ├── people/ clients/ concepts/  # shared across projects and lanes
│   ├── research/sources/
│   ├── outputs/                    # filed-back answers; the only hand-written lane
│   └── _registry/*.yml             # human-owned alias tables
├── src/project_agent/              # THE PACKAGE
│   ├── schemas.py  events.py  warehouse.py  wiki.py  lint.py
│   ├── projects.py  render.py  git.py  llm.py
│   ├── ingest/{inbox,backlog}.py
│   └── signals/{deterministic,llm}.py
├── pipeline/{cli,stages}.py
├── projects/<client>--<project>/
│   ├── project.notes.md            # PM-owned; the discovery anchor
│   ├── raw/                        # _inbox/ + typed folders + manifests
│   └── events.ndjson
├── research/raw/                   # second lane
├── prompts/*.md                    # versioned
├── design-system/                  # tokens.css, system.css, portfolio.css
├── config/signals.yaml
├── data/{warehouse.duckdb,cache/llm/,runs/,schemas/signals/}
└── tests/
```

### 5.1 Package Boundary *(architectural invariant)*

Agents and pipeline scripts may only read or mutate state through `project_agent` APIs.
Forbidden outside the package: direct file I/O on `events.ndjson`, direct DuckDB
connections, direct writes into `kb/`, direct Anthropic SDK calls.

Enforced by three boundary lints in `tests/test_boundary.py`. The lint is regex/AST over
direct imports and writes; it does not trace transitive imports, `importlib`, or
re-exports. It catches honest mistakes, not deliberate circumvention.

Anti-goals: no ABCs, no plugin registries, no DI containers, no metaprogramming.

---

## 6. Data Contracts

### 6.1 The wiki article

Every compiled article carries its own provenance in front-matter:

```yaml
---
title: HIPAA external audit
type: risk                     # project|risk|decision|milestone|source|person|client|concept|index
project: demo--sample-2026
severity: high
status: open
event_ids: [ev_01H..., ev_01H...]
source_hashes: ["sha256:abc...", "sha256:def..."]
compiled_from: "sha256:9f2..."     # input_hash — the article's identity
compiled_by: "write-article@1"     # or template@1 when prose was unavailable
schema_version: 1
tags: [risk, project/demo--sample-2026]
---
```

**`input_hash` is the identity.** It is the sha256 of the canonical JSON of the events
feeding the article. Same events → same hash → same bytes. It is also the LLM cache key
for the article's prose, which is what allows a model to write articles without breaking
the zero-diff contract.

Three rules follow, all enforced by `pipeline lint`:

- **No wall-clock timestamps** in a compiled article. Only event-derived values, which are
  fixed once written.
- **Wikilinks are vault-absolute** (`[[kb/...]]`). Two projects may hold articles with the
  same filename; Obsidian's shortest-path resolution would silently pick one.
- **Only fact events are projected** (`wiki.PROJECTED_TYPES`). `signal_detected` is
  excluded because Analyze runs *after* Compile and appends to the same log — including
  signals would make every second run rewrite the vault. It is also the anti-circularity
  rule.

**LLM prose is additive, never structural.** Front-matter, links, evidence and backlinks
are always templated; a failed call or exhausted budget degrades to the deterministic
article rather than losing a link or a provenance record.

**`project.notes.md`** is PM-owned and never written by any stage. Its front-matter
carries declared metadata (`status`, `sponsor`, `phase`, `stakeholders`), which Compile
copies forward into the index article; the prose below is read only as an excerpt.

### 6.2 Event log

Append-only NDJSON at `projects/<id>/events.ndjson`. Required: `event_id`,
`schema_version`, `ts`, `run_id`, `project_id`, `type`, `actor` (discriminated by `kind`),
`source_ref`, `source_hash`, `payload` (discriminated by `type`), `hash`. Optional:
`confidence`, `supersedes`, `superseded_by`, `retracted`, `retracted_reason`.

Payload types: `source_ingested`, `action_added`, `action_updated`, `blocker_added`,
`blocker_updated`, `risk_added`, `decision_made`, `milestone_added`, `pipeline_health`,
`signal_detected`, `source_summarized`, `concept_added`, `event_retracted`.

**Idempotence at two layers**: a source-hash manifest at ingest, and the LLM response
cache keyed on `(prompt_name, prompt_version, source_hash, model)` at parse and compile.

**Corrections are appends, never rewrites.** An `event_retracted` event hides its target;
an event listing `supersedes: [X]` hides X. Both chains are resolved across the whole log
by `events.resolve_corrections()`, and `warehouse.load()` materializes the same resolved
flags — the SQL views and the Python API must never disagree.

### 6.3 Signal

`signal_id`, `schema_version`, `project_id`, `run_id`, `category`
(risk/deliverable/action/blocker/backlog/communication), `type`, `severity`, `confidence`,
`evidence` (non-empty list of `event_id`s), `method` (deterministic|llm), `rationale`,
`detected_at`, `recommended_action`. Written back as `signal_detected` events.

Deterministic detectors must return triggering `event_id`s, not aggregates. LLM detectors
must be handed concrete `event_id`s and return them. **Anti-circularity:** detectors skip
`signal_detected` when reading the warehouse, and LLM prompts may not cite them.

### 6.4 Operations log

`data/runs/<run_id>.json` — per-stage `name`, `status`, `duration_ms`, `counts`, optional
`cost_usd`. Joined to events by `run_id`. Committed with each PR.

### 6.5 Entity registry

`kb/_registry/{people,clients,concepts}.yml` — human-owned alias tables:

```yaml
bob-kim:
  name: Bob Kim
  aliases: ["B. Kim", "bob.kim@example.com"]
  projects: [demo--sample-2026]
```

The compiler resolves names through this file and nothing else — a plain dictionary
lookup, because resolving identity with a model would resolve differently between runs and
churn the vault. Unresolved names render as plain text and are reported by `pipeline lint`
as proposed additions for a human to merge, which keeps entity resolution behind the HITL
gate.

---

## 7. Signal Catalog

| Category | Signal | Method | Status |
|---|---|---|---|
| Action | `action_aging`, `action_unowned` | deterministic | shipped |
| Blocker | `blocker_unowned`, `blocker_aging` | deterministic | shipped |
| Risk | `risk_unaddressed` | deterministic | shipped |
| Risk | `risk_escalation`, `risk_emergent` | LLM | shipped |
| Deliverable | `deliverable_drift` | deterministic | shipped |
| Communication | `stakeholder_inactivity` | deterministic | shipped |
| Communication | `tone_shift` | LLM | shipped |
| Pipeline | `pipeline_health` (budget, wiki lint) | deterministic | shipped |
| Deliverable | `deliverable_dependency_stall` | LLM | Phase 3 |
| Backlog | `scope_churn`, `velocity_anomaly` | deterministic | Phase 4 |

Thresholds live in `data/schemas/signals/<type>.json`; the LLM spend cap lives in
`config/signals.yaml` and covers the whole run — Compile draws on it first and passes what
it spent to Analyze.

---

## 8. Pipeline Stages

**1 · Ingest.** `inbox.scan_inbox` reads `raw/_inbox/`, classifies by extension, moves into
typed folders, records hashes in `manifest.json`, emits `source_ingested`. A file whose
hash is already known is deleted from the inbox rather than re-ingested. Fetching from
Gmail/Drive/Calendar is Claude Code's job through its MCP connectors — the pipeline holds
no OAuth credentials. Agent-dropped files keep the `<date>-<source>-<id>.<ext>` convention,
because the date prefix preserves ordering and the extension drives classification.

**2 · Parse.** For each unparsed source, `extract-context` yields facts, emitted as
`*_added` / `decision_made` events. Skips anything already in `parse_manifest.json` under
the current prompt version. Parse emits events and nothing else.

**2b · Compile.** Phase A, per project: events → `kb/projects/<id>/` index, risk, decision,
milestone and source articles, plus a `compile_manifest.json`. Phase B, once after every
project: `people/`, `clients/`, `concepts/` and `kb/index.md`, which fan in across
projects and so cannot be written inside the per-project loop. **`pipeline run --project`
performs Phase A only; `pipeline portfolio` is the full compile.**

**3 · Warehouse Load.** Events and the vault into DuckDB. Views: `events`, `actions`,
`blockers`, `risks`, `decisions`, `milestones` (each with a `_full` variant), plus
`documents`, `links`, `backlinks`, `broken_links`, `orphan_documents` and
`document_provenance` — the join that makes "which raw email produced this risk" one query.

**4 · Analyze.** Deterministic detectors, then LLM detectors up to the spend cap. On cap,
emits `pipeline_health` and continues with what it has.

**5 · Render.** The portfolio dashboard, `reports/portfolio-<date>.html`. Per-project
status reports were retired — Obsidian renders the same material from `kb/`.

**6 · Commit.** `auto/<date>` branch, one commit, one PR.

**On demand.** `lint` (structural health + proposed registry additions, `--review` for the
LLM pass), `search` (ranked articles), `wiki-sql` (read-only SQL), `research` (the second
lane).

---

## 9. Human-in-the-Loop Model

**The invariant: the pipeline never takes externally observable action without a merged
PR.**

| Output | Behaviour |
|---|---|
| Wiki articles | arrive as a PR diff; the PM does not hand-edit `kb/` |
| Event log | append-only; corrections are appends |
| Registry additions | proposed by lint, merged by a human |
| Portfolio dashboard | committed with the PR |
| Client emails | drafted only (Phase 3); `--send` is opt-in and later |
| Operations log | committed with each PR |

---

## 10. Current State

Shipped: seven-stage pipeline; two lanes; the compiled wiki with provenance and
backlinks; DuckDB over events and vault; search and SQL CLIs; structural lint with the
registry-proposal loop; portfolio dashboard; 272 tests across unit / integration /
idempotence / boundary markers.

Known gaps, tracked rather than hidden:

- The vault has never been populated from a real run — `kb/` holds scaffolding and
  registries only.
- The project lane extracts no concepts; only research does.
- `.pdf`, `.docx` and `.png` classify as documents and are then read as text (§13.9).
- `kb/outputs/` is written only by `lint --review`.
- Nothing has been exercised at Karpathy's scale (~100 articles); the demo corpus is 31
  documents.

## 11. Roadmap

- **Phase 0 / 0.5 / 1 — complete.** Six stages, HTML rendering, MCP ingest (since
  retired), LLM signals and the cost cap.
- **v2.0 — complete.** The knowledge-base migration (PRs #16–#22) and this cleanup.
- **Phase 2 — the agent lane (current).** `AgentActor`; a proposals path so Claude Code
  can emit events through the package; `/enrich` for concepts from project material and
  duplicate merging; prose as an `article_prose` event so agent and API land at the same
  seam. This closes the gaps in §10.
- **Phase 3 — portfolio signals and drafted communications.** Cross-project signals,
  `deliverable_dependency_stall`, outbox drafts.
- **Phase 4 — backlog integration.** Native Jira read, `scope_churn`, `velocity_anomaly`.
- **Phase 5 — PPTX (5a) and multi-agent (5b).**
- **Phase 6 — open-source release.** PII boundary, onboarding doc, license.

## 12. Acceptance Criteria

A phase is done when: the full suite passes with the boundary marker green; a second run
over unchanged sources produces a zero diff and zero API calls; `mypy --strict src/`
does not regress; every new signal has a JSON spec and evidence `event_id`s; and
`pipeline lint` reports no errors on the compiled vault.

---

## 13. Open Questions

1. **Project ID convention** — `<client>--<project>` in use; unresolved whether a year
   belongs in it.
2. **Sensitive data.** One portfolio-wide vault holds several clients' material in one
   tree. Private repo per engagement, or one repo with boundaries? **Decide before a
   second real client.**
3. *Closed (v0.4).* PM edits to the project record — the two-file split settled it, and
   v2.0 completed it by retiring `project.md`.
4. **Design system source** — ship default tokens, or leave a stub for forks?
5. *Closed (v1.1).* Signal thresholds live in `data/schemas/signals/*.json`.
6. **`outbox/` storage policy (Phase 3)** — drafts in git history are a confidentiality
   risk.
7. *Closed (v2.0).* Cron host threat model — moot once the Python OAuth adapters were
   retired. No tokens at rest.
8. *Closed (v0.5).* Portfolio PR shape — portfolio-scoped.
9. **Source artifact size and binary decoding.** `.pdf`, `.docx` and images are accepted
   by the classifier and then read as text. Decide between a decoding dependency, Git LFS,
   an external blob store, or rejecting binaries at ingest. **Blocks the research lane's
   main use case.**
10. *Closed (v1.2).* LLM cost cap — one per-project-per-day cap, degrade gracefully.
11. **Test data origin** — synthetic corpus today; real client data would make tests
    unpublishable.
12. **Scale.** Untested beyond 31 documents. At ~600 articles the Obsidian graph and the
    `read_all` linear scans both want revisiting.

---

## 14. References

- Andrej Karpathy, *LLM knowledge bases* — raw corpus compiled into a linked markdown
  wiki, read in Obsidian, queried by an agent, health-checked, answers filed back.
- MotherDuck, *Your Obsidian Vault Can Now Run SQL (and Your Agent Can Read It)* —
  markdown as agent middleware; the cache-vs-query argument for inlining computed values.
  Its plugin stamps wall-clock timestamps on freeze and scheduled refresh, which is why
  its blocks are confined to `kb/outputs/` (§6.1).
- Predecessor event-based pipeline — the proof-of-substrate this project generalizes.

---

## 15. Execution Notes for Claude Code

Read `CLAUDE.md` first, every session: it is the operating manual and it carries the rules
that matter most. This document carries the *why*; `CLAUDE.md` carries the *how*.
