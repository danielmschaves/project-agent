Re-render the status report for the current project from state already on disk.

There is no stage-skipping flag yet, so a full run is the only way to regenerate
a report from scratch:

```bash
docker compose run --rm pipeline python -m pipeline run --project <id>
```

Re-running is cheap when nothing upstream changed: ingest skips sources whose
hash is already in `raw/manifest.json`, parse skips sources already recorded in
`raw/parse_manifest.json`, and any LLM call that does happen replays from the
response cache. The stage counts printed per run show what was actually done.

Then display the generated `projects/<id>/reports/status-<date>.md` and note the
path of the HTML report at `projects/<id>/reports/status-<date>.html`.
