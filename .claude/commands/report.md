Render the portfolio dashboard — the one artifact someone without the vault can read.

```bash
docker compose run --rm pipeline python -m pipeline portfolio
```

Then display the path of the generated `reports/portfolio-<date>.html`.

Per-project status reports were retired: Obsidian renders the same material better
straight from `kb/projects/<id>/`, and the vault is the interface now. For a single
project, open its index article — or query it:

```bash
python -m pipeline wiki-sql "
  SELECT type, title FROM documents
  WHERE project = '<id>' ORDER BY type, title"
```

The dashboard is deterministic: the same summaries render byte-identically, so a re-run
with no new signals produces no diff.
