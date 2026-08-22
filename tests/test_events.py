"""Tests for project_agent.events (append / query / hash_event)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from project_agent import events as events_api
from project_agent.schemas import DeterministicActor, Event, SourceIngestedPayload


def _make_event(
    event_id: str = "evt-001",
    project_id: str = "test--project",
    run_id: str = "run-001",
    source_hash: str = "sha256:abc",
    retracted: bool = False,
    superseded_by: str | None = None,
) -> Event:
    payload = SourceIngestedPayload(
        type="source_ingested",
        filename="doc.md",
        source_type="doc",
        size_bytes=100,
    )
    return Event(
        event_id=event_id,
        ts=datetime.now(tz=timezone.utc),
        run_id=run_id,
        project_id=project_id,
        type="source_ingested",
        actor=DeterministicActor(kind="deterministic", detector="inbox"),
        source_ref="sources/docs/doc.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
        retracted=retracted,
        superseded_by=superseded_by,
    )


@pytest.mark.unit
def test_hash_event_is_deterministic() -> None:
    payload = {"type": "source_ingested", "filename": "x.md", "source_type": "doc", "size_bytes": 1}
    h1 = events_api.hash_event(payload, "sha256:aaa")
    h2 = events_api.hash_event(payload, "sha256:aaa")
    assert h1 == h2
    assert h1.startswith("sha256:")


@pytest.mark.unit
def test_hash_event_differs_on_different_inputs() -> None:
    payload = {"type": "source_ingested", "filename": "x.md", "source_type": "doc", "size_bytes": 1}
    h1 = events_api.hash_event(payload, "sha256:aaa")
    h2 = events_api.hash_event(payload, "sha256:bbb")
    assert h1 != h2


@pytest.mark.integration
def test_append_and_query_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    path.touch()
    event = _make_event()
    events_api.append(event, path)

    results = events_api.query(path)
    assert len(results) == 1
    assert results[0].event_id == "evt-001"


@pytest.mark.integration
def test_query_multiple_events_in_order(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    path.touch()
    for i in range(3):
        events_api.append(_make_event(event_id=f"evt-{i:03d}", source_hash=f"sha256:{i}"), path)

    results = events_api.query(path)
    assert [e.event_id for e in results] == ["evt-000", "evt-001", "evt-002"]


@pytest.mark.integration
def test_query_filters_by_project_id(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    path.touch()
    events_api.append(_make_event(project_id="proj-a", source_hash="sha256:1"), path)
    events_api.append(_make_event(project_id="proj-b", source_hash="sha256:2"), path)

    results = events_api.query(path, project_id="proj-a")
    assert len(results) == 1
    assert results[0].project_id == "proj-a"


@pytest.mark.integration
def test_query_excludes_retracted_by_default(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    path.touch()
    events_api.append(_make_event(event_id="evt-ok", source_hash="sha256:1"), path)
    events_api.append(
        _make_event(event_id="evt-bad", source_hash="sha256:2", retracted=True), path
    )

    results = events_api.query(path)
    assert len(results) == 1
    assert results[0].event_id == "evt-ok"


@pytest.mark.integration
def test_query_includes_retracted_when_asked(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    path.touch()
    events_api.append(_make_event(event_id="evt-ok", source_hash="sha256:1"), path)
    events_api.append(
        _make_event(event_id="evt-bad", source_hash="sha256:2", retracted=True), path
    )

    results = events_api.query(path, include_retracted=True)
    assert len(results) == 2


@pytest.mark.integration
def test_query_excludes_superseded_by_default(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    path.touch()
    events_api.append(_make_event(event_id="evt-old", source_hash="sha256:1", superseded_by="evt-new"), path)
    events_api.append(_make_event(event_id="evt-new", source_hash="sha256:2"), path)

    results = events_api.query(path)
    assert len(results) == 1
    assert results[0].event_id == "evt-new"


@pytest.mark.integration
def test_query_filters_by_type(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    path.touch()
    events_api.append(_make_event(source_hash="sha256:1"), path)

    results = events_api.query(path, types=["source_ingested"])
    assert len(results) == 1

    results = events_api.query(path, types=["action_added"])
    assert len(results) == 0


@pytest.mark.integration
def test_query_filters_by_time_range(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    path.touch()
    now = datetime.now(tz=timezone.utc)

    old_payload = SourceIngestedPayload(type="source_ingested", filename="old.md", source_type="doc", size_bytes=1)
    old_event = Event(
        event_id="evt-old",
        ts=now - timedelta(days=2),
        run_id="run-001",
        project_id="test--project",
        type="source_ingested",
        actor=DeterministicActor(kind="deterministic", detector="inbox"),
        source_ref="sources/docs/old.md",
        source_hash="sha256:old",
        payload=old_payload,
        hash=events_api.hash_event(old_payload.model_dump(), "sha256:old"),
    )
    events_api.append(old_event, path)
    events_api.append(_make_event(event_id="evt-now", source_hash="sha256:now"), path)

    results = events_api.query(path, since=now - timedelta(hours=1))
    assert len(results) == 1
    assert results[0].event_id == "evt-now"


@pytest.mark.integration
def test_query_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    path.touch()
    assert events_api.query(path) == []


@pytest.mark.integration
def test_query_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    assert events_api.query(path) == []


# ---------------------------------------------------------------------------
# Retraction and supersession (PRD §6.2) — corrections arrive as later appends
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_retract_hides_target_from_query(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    events_api.append(_make_event(event_id="evt-bad"), path)
    events_api.append(_make_event(event_id="evt-good", source_hash="sha256:good"), path)

    events_api.retract(
        path,
        event_id="evt-bad",
        reason="misparsed owner",
        run_id="run-002",
        project_id="test--project",
        actor=DeterministicActor(kind="deterministic", detector="manual"),
    )

    ids = [e.event_id for e in events_api.query(path, types=["source_ingested"])]
    assert ids == ["evt-good"]


@pytest.mark.integration
def test_retract_never_rewrites_the_original_line(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    events_api.append(_make_event(event_id="evt-bad"), path)
    before = path.read_text(encoding="utf-8")

    events_api.retract(
        path,
        event_id="evt-bad",
        reason="wrong",
        run_id="run-002",
        project_id="test--project",
        actor=DeterministicActor(kind="deterministic", detector="manual"),
    )

    assert path.read_text(encoding="utf-8").startswith(before)


@pytest.mark.integration
def test_include_retracted_returns_the_retracted_event(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    events_api.append(_make_event(event_id="evt-bad"), path)
    events_api.retract(
        path,
        event_id="evt-bad",
        reason="wrong",
        run_id="run-002",
        project_id="test--project",
        actor=DeterministicActor(kind="deterministic", detector="manual"),
    )

    ids = [e.event_id for e in events_api.query(path, include_retracted=True)]
    assert "evt-bad" in ids


@pytest.mark.integration
def test_forward_supersedes_link_hides_the_old_event(tmp_path: Path) -> None:
    """Event B listing supersedes:[A] hides A, without A being rewritten."""
    path = tmp_path / "events.ndjson"
    events_api.append(_make_event(event_id="evt-v1"), path)

    newer = _make_event(event_id="evt-v2", source_hash="sha256:v2")
    newer.supersedes = ["evt-v1"]
    events_api.append(newer, path)

    ids = [e.event_id for e in events_api.query(path)]
    assert ids == ["evt-v2"]
    assert len(events_api.query(path, include_superseded=True)) == 2
