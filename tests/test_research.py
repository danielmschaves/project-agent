"""Tests for the research corpus lane."""
import json
from pathlib import Path
from typing import Any

import pytest

from project_agent import events as events_api
from project_agent import wiki
from pipeline.stages import run_compile_shared, run_research


_RESPONSE = {
    "summary": "Argues that markdown is a good substrate for agent knowledge bases.",
    "topics": ["knowledge-bases", "agents"],
    "concepts": [
        {"name": "Knowledge base", "description": "A structured store of compiled knowledge."},
        {"name": "Retrieval augmented generation", "description": "Fetching context at query time."},
    ],
}


def _client(response: dict[str, Any] | None = None) -> Any:
    text = json.dumps(response or _RESPONSE)

    class _Content:
        def __init__(self) -> None:
            self.text = text

    class _Message:
        content = [_Content()]

    class _Messages:
        calls = 0

        @classmethod
        def create(cls, **kwargs: Any) -> Any:
            cls.calls += 1
            return _Message()

    class _Client:
        messages = _Messages()

    return _Client()


@pytest.fixture
def research_lane(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A research lane with one clipping waiting in the inbox."""
    research_dir = tmp_path / "research"
    inbox = research_dir / "raw" / "_inbox"
    inbox.mkdir(parents=True)
    (inbox / "markdown-for-agents.md").write_text(
        "# Markdown for agents\n\nFiles over apps.\n", encoding="utf-8"
    )

    vault = tmp_path / "kb"
    for sub in ("projects", "people", "clients", "concepts", "research/sources", "_registry"):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    for registry in ("people", "clients", "concepts"):
        (vault / "_registry" / f"{registry}.yml").write_text("{}\n", encoding="utf-8")

    events_path = research_dir / "events.ndjson"
    events_path.touch()
    return research_dir, events_path, vault


@pytest.mark.integration
def test_research_run_ingests_summarizes_and_compiles(
    research_lane: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    research_dir, events_path, vault = research_lane

    result = run_research(
        research_dir, events_path, "run-001", vault,
        cache_dir=tmp_path / "cache", prompts_dir=Path("prompts"), client=_client(),
    )

    assert result.status == "ok"
    assert result.counts["sources_new"] == 1
    assert result.counts["concepts_found"] == 2
    assert (vault / "research" / "sources" / "markdown-for-agents.md").exists()


@pytest.mark.integration
def test_research_note_links_to_its_concepts(
    research_lane: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    research_dir, events_path, vault = research_lane
    run_research(
        research_dir, events_path, "run-001", vault,
        cache_dir=tmp_path / "cache", prompts_dir=Path("prompts"), client=_client(),
    )
    body = (vault / "research" / "sources" / "markdown-for-agents.md").read_text(encoding="utf-8")
    assert "kb/concepts/knowledge-base" in body
    assert "Files over apps" not in body  # a summary, not a copy
    assert "markdown is a good substrate" in body


@pytest.mark.integration
def test_summary_is_stored_in_the_event_log(
    research_lane: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """A re-compile must not need the LLM again, so the summary lives in the log."""
    research_dir, events_path, vault = research_lane
    run_research(
        research_dir, events_path, "run-001", vault,
        cache_dir=tmp_path / "cache", prompts_dir=Path("prompts"), client=_client(),
    )
    summarized = events_api.query(events_path, types=["source_summarized"])
    assert len(summarized) == 1
    assert "markdown" in summarized[0].payload.model_dump()["summary"].casefold()


@pytest.mark.integration
def test_concepts_reach_the_shared_layer(
    research_lane: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    research_dir, events_path, vault = research_lane
    run_research(
        research_dir, events_path, "run-001", vault,
        cache_dir=tmp_path / "cache", prompts_dir=Path("prompts"), client=_client(),
    )
    run_compile_shared(vault, prose_enabled=False, research_events_path=events_path)

    concept = vault / "concepts" / "knowledge-base.md"
    assert concept.exists()
    assert "kb/research/sources/markdown-for-agents" in concept.read_text(encoding="utf-8")


@pytest.mark.integration
def test_research_is_not_a_discoverable_project(
    research_lane: tuple[Path, Path, Path]
) -> None:
    """Research must never be swept into the project signal detectors."""
    from pipeline.cli import _discover_projects

    research_dir, _, _ = research_lane
    assert not (research_dir / "project.notes.md").exists()
    assert _discover_projects(research_dir.parent) == []


@pytest.mark.idempotence
def test_second_research_run_is_a_no_op(
    research_lane: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    research_dir, events_path, vault = research_lane
    cache_dir = tmp_path / "cache"

    run_research(
        research_dir, events_path, "run-001", vault,
        cache_dir=cache_dir, prompts_dir=Path("prompts"), client=_client(),
    )
    events_after_first = events_path.read_text(encoding="utf-8")
    note = (vault / "research" / "sources" / "markdown-for-agents.md").read_text(encoding="utf-8")

    result = run_research(
        research_dir, events_path, "run-002", vault,
        cache_dir=cache_dir, prompts_dir=Path("prompts"), client=_client(),
    )

    assert events_path.read_text(encoding="utf-8") == events_after_first
    assert (vault / "research" / "sources" / "markdown-for-agents.md").read_text(
        encoding="utf-8"
    ) == note
    assert result.counts["articles_written"] == 0


@pytest.mark.unit
def test_research_concepts_take_the_latest_description() -> None:
    """Re-parsing under a newer prompt refreshes, rather than accumulating."""
    from project_agent.schemas import ConceptAddedPayload, DeterministicActor, Event
    from datetime import datetime, timezone
    import uuid

    def _concept(description: str) -> Event:
        payload = ConceptAddedPayload(
            type="concept_added", slug="rag", name="RAG", description=description
        )
        return Event(
            event_id=str(uuid.uuid4()), ts=datetime(2026, 5, 1, tzinfo=timezone.utc),
            run_id="r", project_id="research", type="concept_added",
            actor=DeterministicActor(kind="deterministic", detector="t"),
            source_ref="raw/docs/a.md", source_hash="sha256:a", payload=payload,
            hash=events_api.hash_event(payload.model_dump(), "sha256:a"),
        )

    concepts = wiki.research_concepts([_concept("old"), _concept("new")])
    assert concepts["rag"].description == "new"
