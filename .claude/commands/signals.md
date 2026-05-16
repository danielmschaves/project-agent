Print all signals from the latest pipeline run, grouped by category.

Read `data/events.ndjson` and filter for `signal_detected` events from the most recent `run_id`. Group by `payload.category` and display each signal with: type, severity, rationale, evidence event IDs, and recommended action.
