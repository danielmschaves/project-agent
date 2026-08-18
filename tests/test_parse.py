"""Tests for the parse pipeline stage (run_parse)."""
import json
from pathlib import Path
from typing import Any

import pytest

from project_agent import events as events_api
from pipeline.stages import run_ingest, run_parse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_client(response: dict[str, Any]) -> Any:
    response_text = json.dumps(response)

    class _Content:
        text = response_text

    class _Message:
        content = [_Content()]

    class _Messages:
        @staticmethod
        def create(**kwargs: Any) -> _Message:
            return _Message()

    class _Client:
        messages = _Messages()

    return _Client()


def _raising_client() -> Any:
    class _Messages:
        @staticmethod
        def create(**kwargs: Any) -> Any:
            raise RuntimeError("Anthropic API must not be called on second run")

    class _Client:
        messages = _Messages()

    return _Client()


def _seed_prompts(prompts_dir: Path) -> None:
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "extract-context.md").write_text(
        "---\nversion: 1\npurpose: test\n---\nExtract facts.", encoding="utf-8"
    )


def _seed_inbox(project_dir: Path, filename: str, content: bytes) -> None:
    inbox = project_dir / "raw" / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / filename).write_bytes(content)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_run_parse_emits_action_events(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    _seed_inbox(project_dir, "spec.md", b"# Spec\nDeploy the thing.")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [{"description": "Deploy API", "owner": "alice", "due": "2026-06-01", "status": "open"}],
        "blockers": [],
    }

    result = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )

    assert result.status == "ok"
    assert result.counts["sources_parsed"] == 1
    assert result.counts["events_emitted"] == 1

    evts = events_api.query(events_path, types=["action_added"])
    assert len(evts) == 1
    assert evts[0].type == "action_added"
    assert evts[0].project_id == "test--project"
    assert evts[0].run_id == "run-001"
    assert evts[0].source_hash.startswith("sha256:")


@pytest.mark.integration
def test_run_parse_emits_blocker_events(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    _seed_inbox(project_dir, "email.eml", b"From: alice\nBlocker: no access to prod.")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [],
        "blockers": [{"description": "No prod access", "owner": None, "due": None}],
    }

    result = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )

    assert result.status == "ok"
    assert result.counts["events_emitted"] == 1
    evts = events_api.query(events_path, types=["blocker_added"])
    assert len(evts) == 1


@pytest.mark.integration
def test_run_parse_mixed_facts(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    _seed_inbox(project_dir, "notes.md", b"# Meeting notes")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [
            {"description": "Write tests", "owner": "bob", "due": None, "status": "open"},
            {"description": "Update docs", "owner": None, "due": "2026-07-01", "status": "open"},
        ],
        "blockers": [{"description": "Waiting on approval", "owner": "carol", "due": None}],
    }

    result = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )

    assert result.counts["events_emitted"] == 3
    assert events_api.query(events_path, types=["action_added"]).__len__() == 2
    assert events_api.query(events_path, types=["blocker_added"]).__len__() == 1


@pytest.mark.integration
def test_run_parse_emits_fact_events(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    """Parse emits events only — projecting them into the wiki is compile's job."""
    _seed_inbox(project_dir, "notes.md", b"Notes")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [{"description": "Ship feature", "owner": "alice", "due": "2026-08-01", "status": "open"}],
        "blockers": [],
    }

    run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )

    actions = events_api.query(events_path, types=["action_added"])
    assert len(actions) == 1
    payload = actions[0].payload.model_dump()
    assert payload["description"] == "Ship feature"
    assert payload["owner"] == "alice"

    # Parse must not write project state anywhere.
    assert not (project_dir / "project.md").exists()


@pytest.mark.integration
def test_run_parse_empty_inbox_no_events(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    result = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_raising_client(),
    )

    assert result.status == "ok"
    assert result.counts["sources_parsed"] == 0
    assert result.counts["events_emitted"] == 0


@pytest.mark.integration
def test_run_parse_emits_risk_events(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    _seed_inbox(project_dir, "update.md", b"# Status update")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [],
        "blockers": [],
        "risks": [{"description": "Vendor delay", "severity": "high", "owner": "alice"}],
        "decisions": [],
        "milestones": [],
    }

    result = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )

    assert result.status == "ok"
    assert result.counts["events_emitted"] == 1
    evts = events_api.query(events_path, types=["risk_added"])
    assert len(evts) == 1
    assert evts[0].project_id == "test--project"


@pytest.mark.integration
def test_run_parse_emits_decision_and_milestone_events(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    _seed_inbox(project_dir, "kickoff.md", b"# Kickoff notes")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [],
        "blockers": [],
        "risks": [],
        "decisions": [{"description": "Use PostgreSQL", "rationale": "Team familiarity", "decided_by": "alice"}],
        "milestones": [{"description": "MVP launch", "target_date": "2026-07-01", "status": "open", "owner": "bob"}],
    }

    result = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )

    assert result.status == "ok"
    assert result.counts["events_emitted"] == 2
    assert len(events_api.query(events_path, types=["decision_made"])) == 1
    assert len(events_api.query(events_path, types=["milestone_added"])) == 1


@pytest.mark.integration
def test_run_parse_all_fact_types(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    _seed_inbox(project_dir, "weekly.md", b"# Weekly update")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [{"description": "Fix auth", "owner": "alice", "due": None, "status": "open"}],
        "blockers": [{"description": "No staging env", "owner": None, "due": None}],
        "risks": [{"description": "Scope creep", "severity": "medium", "owner": None}],
        "decisions": [{"description": "Skip SSO for now", "rationale": None, "decided_by": None}],
        "milestones": [{"description": "Beta release", "target_date": "2026-08-01", "status": "open", "owner": None}],
    }

    result = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )

    assert result.counts["events_emitted"] == 5
    for event_type in ["action_added", "blocker_added", "risk_added", "decision_made", "milestone_added"]:
        assert len(events_api.query(events_path, types=[event_type])) == 1


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_run_parse_idempotent(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    """Second run must not call the API and must not emit duplicate events."""
    _seed_inbox(project_dir, "spec.md", b"# Spec")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [{"description": "Do thing", "owner": None, "due": None, "status": "open"}],
        "blockers": [],
        "risks": [],
        "decisions": [],
        "milestones": [],
    }

    # First run
    first = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )
    assert first.counts["sources_parsed"] == 1

    # Second run — raising client must never be called
    second = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-002",
        client=_raising_client(),
    )

    assert second.counts["sources_parsed"] == 0
    assert second.counts["sources_skipped"] == 1
    assert second.counts["events_emitted"] == 0

    # Events not duplicated
    evts = events_api.query(events_path, types=["action_added"])
    assert len(evts) == 1


@pytest.mark.idempotence
def test_run_parse_all_facts_idempotent(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    """Second run must emit zero new events across all five fact types."""
    _seed_inbox(project_dir, "kickoff.md", b"# Kickoff")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [{"description": "Task A", "owner": "alice", "due": None, "status": "open"}],
        "blockers": [{"description": "Blocked by X", "owner": None, "due": None}],
        "risks": [{"description": "Budget risk", "severity": "high", "owner": None}],
        "decisions": [{"description": "Use Redis", "rationale": None, "decided_by": None}],
        "milestones": [{"description": "Beta", "target_date": "2026-09-01", "status": "open", "owner": None}],
    }

    first = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )
    assert first.counts["events_emitted"] == 5

    second = run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-002",
        client=_raising_client(),
    )
    assert second.counts["sources_skipped"] == 1
    assert second.counts["events_emitted"] == 0

    for event_type in ["action_added", "blocker_added", "risk_added", "decision_made", "milestone_added"]:
        assert len(events_api.query(events_path, types=[event_type])) == 1


@pytest.mark.idempotence
def test_run_parse_event_log_idempotent(
    project_dir: Path, events_path: Path, tmp_path: Path
) -> None:
    """The event log must be byte-identical after two parse runs on one source."""
    _seed_inbox(project_dir, "notes.md", b"# Notes")
    run_ingest("test--project", project_dir, events_path, "run-001")

    prompts_dir = tmp_path / "prompts"
    _seed_prompts(prompts_dir)
    cache_dir = tmp_path / "cache"

    fake_response = {
        "actions": [{"description": "Fix bug", "owner": "alice", "due": None, "status": "open"}],
        "blockers": [],
        "risks": [],
        "decisions": [],
        "milestones": [],
    }

    run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-001",
        client=_make_fake_client(fake_response),
    )
    content_after_first = events_path.read_text(encoding="utf-8")

    # Second run skips parse — nothing may be appended
    run_parse(
        project_id="test--project",
        project_dir=project_dir,
        events_path=events_path,
        cache_dir=cache_dir,
        prompts_dir=prompts_dir,
        run_id="run-002",
        client=_raising_client(),
    )
    content_after_second = events_path.read_text(encoding="utf-8")

    assert content_after_first == content_after_second
