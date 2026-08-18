from __future__ import annotations

import json
import logging
import re
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


# ---------------------------------------------------------------------------
# The vault as a queryable surface (v2.0)
# ---------------------------------------------------------------------------

_CREATE_WIKI_TABLES = """
CREATE OR REPLACE TABLE documents (
    path            VARCHAR,
    title           VARCHAR,
    type            VARCHAR,
    project         VARCHAR,
    compiled_by     VARCHAR,
    compiled_from   VARCHAR,
    schema_version  INTEGER,
    body            VARCHAR,
    word_count      INTEGER
);
CREATE OR REPLACE TABLE document_tags (
    path VARCHAR,
    tag  VARCHAR
);
CREATE OR REPLACE TABLE document_events (
    path     VARCHAR,
    event_id VARCHAR
);
CREATE OR REPLACE TABLE links (
    source_path VARCHAR,
    target_path VARCHAR,
    target_raw  VARCHAR
);
"""


def load_wiki(vault_dir: Path, db_path: Path) -> int:
    """Index the compiled wiki into DuckDB alongside the event log.

    Articles are parsed with `wiki.read_article` rather than DuckDB's
    `read_text`, so the front-matter contract has exactly one parser. The
    tables land in the same database as `events_raw`, and `document_events`
    carries each article's event ids — which is what makes "the raw email
    behind this risk" a single join rather than a manual hunt.

    File mtimes are deliberately not stored: they change on every checkout and
    would make query results non-reproducible.
    """
    from project_agent import wiki  # local import: wiki imports llm, warehouse does not

    db_path.parent.mkdir(parents=True, exist_ok=True)
    articles = [
        wiki.read_article(vault_dir, p.relative_to(vault_dir))
        for p in sorted(vault_dir.rglob("*.md"))
    ]

    con = duckdb.connect(str(db_path))
    try:
        con.execute(_CREATE_WIKI_TABLES)

        if articles:
            con.executemany(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        a.path.as_posix(), a.title, a.type, a.project,
                        a.compiled_by, a.compiled_from, a.schema_version,
                        a.body, len(a.body.split()),
                    )
                    for a in articles
                ],
            )
            tags = [(a.path.as_posix(), tag) for a in articles for tag in sorted(a.tags)]
            if tags:
                con.executemany("INSERT INTO document_tags VALUES (?,?)", tags)
            refs = [
                (a.path.as_posix(), eid) for a in articles for eid in sorted(a.event_ids)
            ]
            if refs:
                con.executemany("INSERT INTO document_events VALUES (?,?)", refs)

            edges = [
                (a.path.as_posix(), _normalize_link(target), target)
                for a in articles
                for target in wiki.parse_wikilinks(a.body)
            ]
            if edges:
                con.executemany("INSERT INTO links VALUES (?,?,?)", edges)

        _create_wiki_views(con)
    finally:
        con.close()

    logger.info("Wiki indexed: %d articles", len(articles))
    return len(articles)


def _normalize_link(target: str) -> str:
    """Turn a wikilink target into a documents.path.

    Links are vault-absolute and extension-less (`kb/people/bob-kim`); paths
    are vault-relative with the extension (`people/bob-kim.md`).
    """
    cleaned = target.strip().removeprefix("kb/")
    return cleaned if cleaned.endswith(".md") else f"{cleaned}.md"


def _create_wiki_views(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE OR REPLACE VIEW backlinks AS
        SELECT d.path AS path, l.source_path AS linked_from
        FROM links l
        JOIN documents d ON d.path = l.target_path
    """)
    # A link pointing at nothing — the lint's primary signal that the vault
    # has drifted from what the compiler actually produced.
    con.execute("""
        CREATE OR REPLACE VIEW broken_links AS
        SELECT l.source_path, l.target_raw
        FROM links l
        LEFT JOIN documents d ON d.path = l.target_path
        WHERE d.path IS NULL
    """)
    con.execute("""
        CREATE OR REPLACE VIEW orphan_documents AS
        SELECT d.path, d.type, d.project
        FROM documents d
        LEFT JOIN links l ON l.target_path = d.path
        WHERE l.target_path IS NULL AND d.type <> 'index'
    """)
    # Article -> the events it was compiled from, joined through to the log.
    # Skipped when the vault is indexed into a database that has no event log
    # yet; the next full run creates it.
    has_events = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'events_raw'"
    ).fetchone()
    if has_events and has_events[0]:
        con.execute("""
            CREATE OR REPLACE VIEW document_provenance AS
            SELECT de.path, e.event_id, e.type, e.source_ref, e.ts
            FROM document_events de
            JOIN events_raw e ON e.event_id = de.event_id
        """)


def search(db_path: Path, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Rank vault articles against a free-text query.

    A deliberately naive scorer — term frequency with the title weighted — run
    in SQL over the `documents` table. DuckDB's `fts` extension would give
    proper BM25, but it is a downloadable extension and the pipeline has to
    work on a machine that cannot reach extensions.duckdb.org. At vault scale
    the difference is not worth the dependency; swapping in `match_bm25` later
    only changes this function.
    """
    terms = [t for t in re.findall(r"[\w']+", query.casefold()) if t]
    if not terms:
        return []

    def occurrences(column: str, index: int) -> str:
        # (len - len without the term) / len(term) == number of occurrences
        return (
            f"(length(lower({column})) - length(replace(lower({column}), ${index}, ''))) "
            f"/ length(${index})"
        )

    score_parts: list[str] = []
    where_parts: list[str] = []
    params: list[Any] = []
    for i, term in enumerate(terms, start=1):
        params.append(term)
        score_parts.append(f"3 * {occurrences('title', i)} + {occurrences('body', i)}")
        where_parts.append(f"(lower(title) LIKE '%' || ${i} || '%' OR lower(body) LIKE '%' || ${i} || '%')")

    params.append(limit)
    sql = f"""
        SELECT path, title, type, project,
               {' + '.join(score_parts)} AS score
        FROM documents
        WHERE {' AND '.join(where_parts)}
        ORDER BY score DESC, path ASC
        LIMIT ${len(params)}
    """

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rel = con.execute(sql, params)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    finally:
        con.close()
