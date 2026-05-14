Run the full pipeline (all six stages) for the current project on the working branch.

Determine the project ID from the projects/ directory or ask the user if there are multiple. Then run:

```bash
uv run python -m pipeline run --project <id>
```

After the run completes, report the contents of the latest `data/runs/<run_id>.json` (stage statuses, counts, any errors).
