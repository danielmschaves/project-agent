Print all signals from the latest pipeline run, grouped by category.

Read `projects/<id>/events.ndjson` (the log is per-project — there is no
repo-level `data/events.ndjson`) and filter for `signal_detected` events from the
most recent `run_id`. Group by `payload.category` and display each signal with:
type, severity, rationale, evidence event IDs, and recommended action.

With no project given, do this for every project under `projects/` that has a
`project.notes.md`.
