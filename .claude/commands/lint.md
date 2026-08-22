Health-check the compiled wiki and report what needs a human decision.

```bash
docker compose run --rm pipeline python -m pipeline lint
```

Structural checks (all deterministic): links that resolve to nothing, links
that are not vault-absolute, articles nothing links to, missing or invalid
front-matter, articles citing events no longer in the log, stale
`compile_manifest` entries, and Obsidian DuckDB-plugin markers inside a
compiled article — the plugin stamps a wall-clock timestamp when it freezes or
refreshes, so its blocks belong in `kb/outputs/` only.

Findings are summarized into one `pipeline_health` event per project, deduped
on content, so a run that changes nothing appends nothing. Pass `--no-events`
to report without writing, and `--strict` to exit non-zero on any error.

The part that needs you is the **proposed registry additions**. The compiler
resolves people through `kb/_registry/people.yml` and never guesses, so any
name not in there renders as plain text and contributes nothing to the shared
layer. Review the proposals, merge the ones you recognise — watch for two
spellings of the same person, which should become aliases of one entry rather
than two entries — and re-run the pipeline to link them.

Add `--review` for an LLM pass over the vault (contradictions, gaps, concept
candidates, likely duplicates). It reads article titles, not bodies, and files
its answer into `kb/outputs/lint-<date>.md`.
