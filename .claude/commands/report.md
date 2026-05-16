Render the status report for the current project without re-running ingest or parse (skips stages 1–3).

```bash
docker compose run --rm pipeline python -m pipeline run --project <id>
```

Then display the generated `projects/<id>/reports/status-<date>.md` content and note the path of the generated HTML report at `projects/<id>/reports/status-<date>.html`.
