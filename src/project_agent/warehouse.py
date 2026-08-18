from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import duckdb

from project_agent import events as events_api

logger = logging.getLogger(__name__)

_CREATE_EVENTS_RAW = """
    CREATE OR REPLACE TABLE events_raw (
        event_id         VARCHAR NOT NULL,
        schema_version   INTEGER,
        ts               VARCHAR,
        run_id           VARCHAR,
        project_id       VARCHAR,
        type             VARCHAR,
        actor            VARCHAR,
        source_ref       VARCHAR,
        source_hash      VARCHAR,
        payload          VARCHAR,
        hash             VARCHAR,
        confidence       DOUBLE,
        supersedes       VARCHAR,
        superseded_by    VARCHAR,
        retracted        BOOLEAN DEFAULT false,
        retracted_reason VARCHAR
    )
"""

_ACTIONS_COLS = """
    event_id,
    ts,
    run_id,
    project_id,
    source_hash,
    source_ref,
    json_extract_string(payload, '$.description') AS description,
    json_extract_string(payload, '$.owner')       AS owner,
    json_extract_string(payload, '$.due')         AS due,
    json_extract_string(payload, '$.status')      AS status
"""

_BLOCKERS_COLS = """
    event_id,
    ts,
    run_id,
    project_id,
    source_hash,
    source_ref,
    json_extract_string(payload, '$.description') AS description,
    json_extract_string(payload, '$.owner')       AS owner,
    json_extract_string(payload, '$.due')         AS due
"""

_RISKS_COLS = """
    event_id,
    ts,
    run_id,
    project_id,
    source_hash,
    source_ref,
    json_extract_string(payload, '$.description') AS description,
    json_extract_string(payload, '$.severity')    AS severity,
    json_extract_string(payload, '$.owner')       AS owner,
    json_extract_string(payload, '$.status')      AS status
"""

_DECISIONS_COLS = """
    event_id,
    ts,
    run_id,
    project_id,
    source_hash,
    source_ref,
    json_extract_string(payload, '$.description') AS description,
    json_extract_string(payload, '$.rationale')   AS rationale,
    json_extract_string(payload, '$.decided_by')  AS decided_by
"""

_MILESTONES_COLS = """
    event_id,
    ts,
    run_id,
    project_id,
    source_hash,
    source_ref,
    json_extract_string(payload, '$.description') AS description,
    json_extract_string(payload, '$.target_date') AS target_date,
    json_extract_string(payload, '$.status')      AS status,
    json_extract_string(payload, '$.owner')       AS owner
"""


def load(events_path: Path | list[Path], db_path: Path) -> None:
    """Read one or more events.ndjson logs and materialize them into DuckDB.

    Accepts a list so a portfolio run can hold every project's events in one
    warehouse — rows stay distinguishable by the `project_id` column, and all
    detectors already scope their SQL to a single project.  Passing one path
    loads just that project.

    Called idempotently: CREATE OR REPLACE TABLE rebuilds events_raw from
    scratch on every load, so re-runs produce identical state.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    paths = [events_path] if isinstance(events_path, Path) else events_path
    all_events = [event for path in paths for event in events_api.read_all(path)]

    # Retractions and supersessions live on the correcting event, so resolve
    # them here and materialize the effective flags — otherwise the SQL views
    # would keep serving rows that events.query() already hides.
    corrections = events_api.resolve_corrections(all_events)

    con = duckdb.connect(str(db_path))
    try:
        con.execute(_CREATE_EVENTS_RAW)

        if all_events:
            rows = [
                (
                    e.event_id,
                    e.schema_version,
                    e.ts.isoformat(),
                    e.run_id,
                    e.project_id,
                    e.type,
                    e.actor.model_dump_json(),
                    e.source_ref,
                    e.source_hash,
                    e.payload.model_dump_json(),
                    e.hash,
                    e.confidence,
                    json.dumps(e.supersedes),
                    e.superseded_by or corrections.superseded_by.get(e.event_id),
                    e.retracted or e.event_id in corrections.retracted,
                    e.retracted_reason or corrections.retracted.get(e.event_id),
                )
                for e in all_events
            ]
            con.executemany(
                "INSERT INTO events_raw VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )

        _create_views(con)
    finally:
        con.close()

    logger.info("Warehouse loaded: %d events", len(all_events))


def query(db_path: Path, sql: str) -> list[dict[str, Any]]:
    """Execute SQL against the warehouse and return rows as dicts."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    finally:
        con.close()


def _create_views(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE OR REPLACE VIEW events AS
        SELECT * FROM events_raw
        WHERE NOT retracted AND superseded_by IS NULL
    """)
    con.execute("""
        CREATE OR REPLACE VIEW events_full AS
        SELECT * FROM events_raw
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW actions AS
        SELECT {_ACTIONS_COLS}
        FROM events_raw
        WHERE type = 'action_added'
          AND NOT retracted AND superseded_by IS NULL
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW actions_full AS
        SELECT {_ACTIONS_COLS}
        FROM events_raw
        WHERE type = 'action_added'
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW blockers AS
        SELECT {_BLOCKERS_COLS}
        FROM events_raw
        WHERE type = 'blocker_added'
          AND NOT retracted AND superseded_by IS NULL
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW blockers_full AS
        SELECT {_BLOCKERS_COLS}
        FROM events_raw
        WHERE type = 'blocker_added'
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW risks AS
        SELECT {_RISKS_COLS}
        FROM events_raw
        WHERE type = 'risk_added'
          AND NOT retracted AND superseded_by IS NULL
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW risks_full AS
        SELECT {_RISKS_COLS}
        FROM events_raw
        WHERE type = 'risk_added'
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW decisions AS
        SELECT {_DECISIONS_COLS}
        FROM events_raw
        WHERE type = 'decision_made'
          AND NOT retracted AND superseded_by IS NULL
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW decisions_full AS
        SELECT {_DECISIONS_COLS}
        FROM events_raw
        WHERE type = 'decision_made'
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW milestones AS
        SELECT {_MILESTONES_COLS}
        FROM events_raw
        WHERE type = 'milestone_added'
          AND NOT retracted AND superseded_by IS NULL
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW milestones_full AS
        SELECT {_MILESTONES_COLS}
        FROM events_raw
        WHERE type = 'milestone_added'
    """)
