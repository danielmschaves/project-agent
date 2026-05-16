Run the full pipeline (all six stages) for the current project on the working branch.

Determine the project ID from the projects/ directory or ask the user if there are multiple. Then run:

```bash
docker compose run --rm pipeline python -m pipeline run --project <id>
```

To run all projects and render the portfolio dashboard:

```bash
docker compose run --rm pipeline python -m pipeline portfolio
```

After the run completes, report the contents of the latest `data/runs/<run_id>.json` (stage statuses, counts, any errors). Also note the paths of any generated HTML reports.
