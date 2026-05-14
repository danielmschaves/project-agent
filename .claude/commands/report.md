Render the status report for the current project without re-running ingest or parse (skips stages 1–3).

```bash
uv run python -m pipeline render --project <id>
```

Then display the generated `projects/<id>/reports/status-<date>.md` content.
