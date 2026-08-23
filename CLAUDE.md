# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Read this first, every session.**
> This is the operating manual for Project Agent. It exists to keep you aligned with the
> architecture between sessions, since you have no memory of prior work beyond what's in git.
> `PRD.md` carries the *why*; this file carries the *how*.

---

## What this repo is

A daily pipeline ingests project sources, parses them into an append-only event log,
**compiles that log into a markdown wiki** read in Obsidian, indexes both in DuckDB,
detects signals, renders a portfolio dashboard, and opens a PR for the PM to review.
Nothing reaches `main` without a human merge.

The full vision is in `PRD.md` (v2.0). **Read it before any non-trivial work.**

---

## Commands

```bash
# Setup — Docker preferred, uv is the documented alternative
docker compose build
uv sync --extra dev

# Run
python -m pipeline run --project <id>      # one project; Compile Phase A only
python -m pipeline portfolio               # all projects + shared layer + PR
python -m pipeline research                # the research corpus lane

# Read
python -m pipeline search "redis circuit breaker"
python -m pipeline wiki-sql "SELECT type, count(*) FROM documents GROUP BY 1"
python -m pipeline lint [--review] [--strict]

# Verify
pytest                    # full suite
pytest -m unit            # pure functions, no I/O
pytest -m integration     # filesystem / DuckDB / git
pytest -m idempotence     # run twice, assert zero diff
pytest -m boundary        # the §5.1 lint — must never fail
mypy --strict src/
```

---

## The Rules That Matter Most

### 1. The Package Boundary (PRD §5.1)

**All state mutation goes through `src/project_agent/` APIs.** Nothing in `pipeline/` or
`agents/` may:

- open `events.ndjson` directly → use `project_agent.events`
- open a DuckDB connection → use `project_agent.warehouse`
- write into `kb/` → use `project_agent.wiki`
- call `anthropic.*` → use `project_agent.llm`
- shell out to `git` for state changes → use `project_agent.git`

Three boundary lints enforce this. Failing one fails the build. To do any of the above
from outside the package, **add the function to the package instead.**

The lint is regex/AST over direct imports and writes — it misses `importlib`, transitive
imports and re-exports. Don't circumvent it on purpose.

### 2. The HITL Invariant (PRD §9)

**The pipeline never takes externally observable action without a merged PR.** No commits
to `main` — always `auto/<date>` + PR. No emails sent. No calendar or Jira writes.

### 3. The LLM Cache is the Idempotence Backbone (PRD §6.2)

Every model call is cached in `data/cache/llm/`, keyed on
`(prompt_name, prompt_version, source_hash, model)`. Cache hits replay byte-identical
responses, making re-runs zero-diff.

- **Never** call `anthropic.*` without routing through the cache wrapper.
- **Never** mutate cached responses. To re-parse under new instructions, bump the prompt
  `version`. Old events get superseded via the `supersedes` chain.
- `llm.is_cached` is checked *before* the spend budget, so hitting the cap can never
  regress a finished article back to its template.

### 4. `kb/` is Bot-Owned; `project.notes.md` is PM-Owned (PRD §6.1)

**`project.md` does not exist.** A project's state is the compiled wiki at
`kb/projects/<id>/`, rewritten by Compile on every run.

- Writing to `kb/**` outside `project_agent.wiki` → **stop.** Articles must stay
  reproducible from the event log.
- Writing to `project.notes.md` from any stage → **stop.** PM-owned. Never.
- PM metadata (`status`, `sponsor`, `phase`, `stakeholders`) lives in `project.notes.md`
  front-matter; Compile copies it into the index article.
- `kb/_registry/*.yml` is human-owned: the compiler reads it, never writes it.
- `kb/outputs/` is the one hand-written lane, and the only place `​```duckdb` blocks belong.

### 5. Compile is Deterministic (PRD §6.1)

An article's identity is its `input_hash` — the canonical JSON of the events feeding it.
Same events → same hash → same bytes → zero diff.

- **Never put a wall-clock timestamp in a compiled article.** `datetime.now()` anywhere in
  the compile path is a bug.
- **Only fact events are projected** (`wiki.PROJECTED_TYPES`). `signal_detected` is
  excluded: Analyze runs *after* Compile and appends to the same log, so including signals
  would make every second run rewrite the vault. It is also the anti-circularity rule.
- **Wikilinks are vault-absolute** (`[[kb/...]]`). Two projects may hold the same
  filename; Obsidian's shortest-path resolution would silently pick one.
- **LLM prose is additive, never structural.** Front-matter, links, evidence and backlinks
  are always templated; a failed call degrades to the deterministic article.

### 6. The Agent's Output is Events, Never Articles (PRD §4.3)

When Claude Code enriches the wiki — naming concepts, merging duplicate risks — it emits
**events** and lets the compiler project them. It does not write articles.

This keeps one writer for `kb/`, preserves provenance, and means zero-diff holds
regardless of which intelligence produced the events.

---

## Architecture

```
6. Commit          → auto/<date> branch + PR (HITL)
5. Render          → portfolio dashboard (HTML)
4. Analyze         → deterministic + LLM signals
3. Warehouse Load  → events + kb/ → DuckDB
2b. Compile        → events → kb/ articles   (Phase A per project, Phase B once)
2. Parse           → raw documents → fact events
1. Ingest          → raw/_inbox/ → typed folders
```

**Package modules:**

```
src/project_agent/
├── schemas.py       ← pydantic: Event, Signal + discriminated unions
├── events.py        ← NDJSON append/query/retract, correction chains
├── warehouse.py     ← DuckDB: events + the vault, search, backlinks
├── wiki.py          ← articles, wikilinks, registry, events → kb/
├── lint.py          ← structural health checks over the vault
├── projects.py      ← reads the wiki index + PM front-matter
├── render.py        ← the portfolio dashboard
├── git.py           ← branch/PR helpers
├── llm.py           ← Claude API client + cache wrapper
├── ingest/inbox.py  ← file-drop reader (the only ingest path)
└── signals/         ← deterministic.py, llm.py
```

**Idempotence flows:**
- Same file re-ingested → `source_hash` in `manifest.json` → skipped, zero events.
- Same source + prompt version → LLM cache hit → byte-identical events.
- Same events → same `input_hash` → byte-identical articles.
- Prompt version bumped → cache miss → re-parse, new events supersede old.

---

## Conventions

**Python.** 3.12+. Pydantic v2 for all schemas — no dataclasses, no `TypedDict`, no bare
`dict` where a model fits. `mypy --strict` on `src/`. No `print()` — use `logging`. No
`os.path` — use `pathlib`. No global state, no singletons; pass dependencies as arguments.

**Tests.** Four markers, exactly one per test: `unit` (no I/O), `integration` (fs/DuckDB/
git via `tmp_path`), `idempotence` (run twice, assert no-op; must assert zero API calls on
cache hit via a recording client), `boundary` (the §5.1 lint).

**Git.** `auto/<YYYY-MM-DD>` for pipeline runs, `feat/<desc>` for dev work. Commit
messages: imperative, first line ≤ 72 chars, body explains *why*. One PR per stage.

**Prompts.** All in `prompts/*.md`, loaded by `llm.load_prompt`. Front-matter carries
`purpose`, `model`, `max_tokens`, `expected_schema`, **`version`**. Bumping `version`
invalidates the cache for that prompt. Never inline prompt text in Python.

Compiler prompts (`write-article`, `write-source-note`, `write-concept`, `write-index`)
return `{"summary": ...}` and must never emit links, headings or front-matter — the article
supplies all structure. Bumping one recompiles every article it touches.

---

## Common Gotchas

- **Writing an article from a pipeline script.** Violates Rule 1. Use `wiki.write_article`.
- **Calling `datetime.now()` in the compile path.** Breaks zero-diff — Rule 5.
- **Emitting a relative wikilink.** Two projects may share a filename — Rule 5.
- **Writing to `project.notes.md`.** PM-owned. Never.
- **Calling `anthropic.*` outside the cache wrapper.** Silently breaks idempotence: tests
  pass once, fail tomorrow.
- **Opening DuckDB in a stage.** Use `warehouse.query`.
- **Skipping the second-run idempotence check.** A stage that "works" but produces a
  different vault on re-run is broken.
- **Emitting `signal_detected` without `evidence`.** Pydantic rejects it.
- **Adding an event type without updating the discriminated union.** `schemas.py` first.
- **Removing a member from a discriminated union.** Breaks validation for stored events
  that used it. `MCPActor` stays for exactly this reason, though nothing produces it.
- **Hand-editing `events.ndjson`.** Append-only. Use `events.retract`.
- **Expecting `_inbox/` to retain skipped files.** `scan_inbox` deletes files whose hash is
  already in the manifest, so re-fetches don't accumulate.
- **Forgetting the operations log.** Each stage returns a `StageResult`; the CLI writes
  `data/runs/<run_id>.json`.
- **Assuming `pipeline run` compiles everything.** It does Phase A only. Shared
  people/clients/concepts need `pipeline portfolio`.

---

## Decisions Already Made

- **Claude Code's MCP connectors do the fetching.** The Python Google adapters are
  retired; the pipeline holds no OAuth credentials. The agent drops files into
  `raw/_inbox/` using the `<date>-<source>-<id>.<ext>` convention — the date prefix
  preserves ordering, the extension drives classification.
- **The repo root is an Obsidian vault; `kb/` is the wiki.** `.obsidian/` is committed
  with `newLinkFormat: absolute` and `useMarkdownLinks: false`. Per-machine UI state is
  gitignored.
- **`kb/` is deliberately NOT gitignored**, unlike every other generated artifact — the
  vault must exist on a fresh clone. The consequence is real: pipeline runs dirty `kb/` on
  dev branches. Use `--dry-run` when you don't want that.
- **`kb/_registry/*.yml` is human-owned.** Entity resolution is a dictionary lookup, which
  is what keeps it deterministic. `pipeline lint` proposes additions; a human merges them.
- **The research lane is a second corpus, not a project.** It shares `kb/concepts/` and is
  never passed to the signal detectors — `_discover_projects` keys on `project.notes.md`,
  which `research/` deliberately lacks.
- **Per-project status reports are retired.** Obsidian renders `kb/projects/<id>/` better.
  The portfolio dashboard remains as the artifact for someone without the vault.
- **Search uses a term-frequency scorer, not DuckDB `fts`.** The extension needs a
  download from `extensions.duckdb.org`, which is unreachable from some hosts. Swapping in
  `match_bm25` later changes only `warehouse.search`.
- **`sources/` was renamed `raw/`** (v2.0), freeing "sources" to mean the per-document
  summary notes in the wiki.

**Open — ask first, don't decide unilaterally:** one vault holding several clients
(PRD §13.2), binary/PDF decoding (§13.9), `outbox/` storage policy (§13.6), scale beyond
the demo corpus (§13.12).

---

## Slash Commands

`/sync` · `/report` · `/pr` · `/signals` · `/ask` · `/lint` — all defined in
`.claude/commands/` and delegating to `python -m pipeline ...`.

---

## When Something Is Wrong

1. Don't fix speculatively. Ask the maintainer.
2. Run the suite. Read the failures.
3. Check `git log` and recent PR descriptions for context.
4. Check `projects/<id>/events.ndjson` for the last few events.
5. If the vault looks wrong, run `pipeline lint` — it reports drift between the vault and
   the log it was compiled from.
6. To start clean: `uv run python scripts/reset_demo.py`, which clears the events, the
   manifests *and* the compiled articles for that project.

---

*CLAUDE.md v2.0 — companion to PRD.md v2.0.*
