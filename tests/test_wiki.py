"""Tests for project_agent.wiki — articles, links, registry, and compilation."""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from project_agent import events as events_api
from project_agent import wiki
from project_agent.schemas import (
    DecisionMadePayload,
    DeterministicActor,
    Event,
    LLMActor,
    MilestoneAddedPayload,
    RiskAddedPayload,
    SourceIngestedPayload,
)


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------

def _event(payload, project_id: str = "test--project", source_hash: str = "sha256:aaa") -> Event:
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc),
        run_id="run-001",
        project_id=project_id,
        type=payload.type,
        actor=(
            DeterministicActor(kind="deterministic", detector="inbox")
            if payload.type == "source_ingested"
            else LLMActor(kind="llm", model="m", prompt="extract-context@2")
        ),
        source_ref=f"raw/docs/{source_hash[-3:]}.md",
        source_hash=source_hash,
        payload=payload,
        hash=events_api.hash_event(payload.model_dump(), source_hash),
    )


def _source(filename: str = "notes.md", source_hash: str = "sha256:aaa") -> Event:
    return _event(
        SourceIngestedPayload(
            type="source_ingested", filename=filename, source_type="doc", size_bytes=10
        ),
        source_hash=source_hash,
    )


def _risk(description: str, owner: str | None = None, source_hash: str = "sha256:aaa") -> Event:
    return _event(
        RiskAddedPayload(
            type="risk_added", description=description, severity="high", owner=owner, status="open"
        ),
        source_hash=source_hash,
    )


def _registries(vault: Path) -> wiki.Registries:
    return wiki.Registries.load(vault)


# ---------------------------------------------------------------------------
# Slugs and links
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_slugify_is_stable_and_filesystem_safe() -> None:
    assert wiki.slugify("Redis Single Point of Failure!") == "redis-single-point-of-failure"
    assert wiki.slugify("  Ünïcode  &  Symbols  ") == "n-code-symbols"


@pytest.mark.unit
def test_slugify_truncates_to_max_words() -> None:
    assert wiki.slugify("a b c d e f g h i j", max_words=3) == "a-b-c"


@pytest.mark.unit
def test_slugify_never_returns_empty() -> None:
    assert wiki.slugify("!!!") == "untitled"


@pytest.mark.unit
def test_wikilinks_are_vault_absolute() -> None:
    """Relative links would resolve to the wrong project when names collide."""
    link = wiki.wikilink(Path("projects/acme--x/risks/redis.md"), "Redis")
    assert link == "[[kb/projects/acme--x/risks/redis|Redis]]"


@pytest.mark.unit
def test_parse_wikilinks_round_trips_targets() -> None:
    body = f"see {wiki.wikilink(Path('people/bob-kim.md'), 'Bob')} and {wiki.wikilink(Path('index.md'))}"
    assert wiki.parse_wikilinks(body) == ["kb/people/bob-kim", "kb/index"]


# ---------------------------------------------------------------------------
# Article serialization
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_article_render_is_deterministic() -> None:
    article = wiki.Article(
        path=Path("projects/p/risks/x.md"), title="X", type="risk",
        event_ids=["b", "a"], tags=["risk", "project/p"],
    )
    assert article.render() == article.render()
    assert "event_ids:\n- a\n- b" in article.render()


@pytest.mark.integration
def test_article_round_trips_through_disk(vault_dir: Path) -> None:
    article = wiki.Article(
        path=Path("projects/p/risks/x.md"), title="Redis SPOF", type="risk",
        project="p", event_ids=["e1"], metadata={"severity": "high"}, body="# Redis SPOF",
    )
    wiki.write_article(vault_dir, article)
    loaded = wiki.read_article(vault_dir, article.path)
    assert loaded.title == "Redis SPOF"
    assert loaded.type == "risk"
    assert loaded.event_ids == ["e1"]
    assert loaded.metadata["severity"] == "high"


@pytest.mark.integration
def test_write_article_reports_no_change_on_rewrite(vault_dir: Path) -> None:
    article = wiki.Article(path=Path("a.md"), title="A", type="index", body="# A")
    assert wiki.write_article(vault_dir, article) is True
    assert wiki.write_article(vault_dir, article) is False


# ---------------------------------------------------------------------------
# Registry — deterministic entity resolution
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_registry_resolves_by_name_alias_and_slug(vault_dir: Path) -> None:
    (vault_dir / "_registry" / "people.yml").write_text(
        'bob-kim:\n  name: Bob Kim\n  aliases: ["B. Kim", "bob.kim@example.com"]\n',
        encoding="utf-8",
    )
    people = wiki.load_registry(vault_dir, "people")
    for probe in ("Bob Kim", "bob kim", "B. Kim", "bob.kim@example.com", "bob-kim"):
        assert people.resolve(probe) == "bob-kim", probe


@pytest.mark.integration
def test_registry_returns_none_for_unknown_name(vault_dir: Path) -> None:
    assert wiki.load_registry(vault_dir, "people").resolve("Nobody") is None


@pytest.mark.integration
def test_person_link_falls_back_to_bare_name(vault_dir: Path) -> None:
    """Unregistered names are reported by lint, never guessed at here."""
    assert wiki.person_link(_registries(vault_dir), "Carol Davis") == "Carol Davis"


@pytest.mark.integration
def test_person_link_uses_registry_slug(vault_dir: Path) -> None:
    (vault_dir / "_registry" / "people.yml").write_text(
        "carol-davis:\n  name: Carol Davis\n", encoding="utf-8"
    )
    link = wiki.person_link(_registries(vault_dir), "Carol Davis")
    assert link == "[[kb/people/carol-davis|Carol Davis]]"


# ---------------------------------------------------------------------------
# Phase A — compile_project
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_compile_project_produces_index_entities_and_sources(vault_dir: Path) -> None:
    events = [_source("kickoff.md"), _risk("Redis is a single point of failure")]
    articles = wiki.compile_project(
        vault_dir, "test--project", events, {"id": "test--project"}, _registries(vault_dir)
    )
    paths = {a.path.as_posix() for a in articles}
    assert "projects/test--project/index.md" in paths
    assert "projects/test--project/risks/redis-is-a-single-point-of-failure.md" in paths
    assert "projects/test--project/sources/kickoff.md" in paths


@pytest.mark.integration
def test_entity_article_carries_evidence_event_ids(vault_dir: Path) -> None:
    risk = _risk("Redis SPOF")
    articles = wiki.compile_project(
        vault_dir, "test--project", [_source(), risk], {}, _registries(vault_dir)
    )
    article = next(a for a in articles if a.type == "risk")
    assert article.event_ids == [risk.event_id]
    assert risk.event_id in article.body
    assert "## Evidence" in article.body


@pytest.mark.integration
def test_source_note_backlinks_to_what_it_produced(vault_dir: Path) -> None:
    events = [_source("kickoff.md"), _risk("Redis SPOF")]
    articles = wiki.compile_project(vault_dir, "test--project", events, {}, _registries(vault_dir))
    note = next(a for a in articles if a.type == "source")
    assert "kb/projects/test--project/risks/redis-spof" in note.body


@pytest.mark.integration
def test_same_description_from_two_sources_merges_into_one_article(vault_dir: Path) -> None:
    events = [
        _source("a.md", "sha256:aaa"),
        _source("b.md", "sha256:bbb"),
        _risk("Redis SPOF", source_hash="sha256:aaa"),
        _risk("Redis SPOF", source_hash="sha256:bbb"),
    ]
    articles = wiki.compile_project(vault_dir, "test--project", events, {}, _registries(vault_dir))
    risks = [a for a in articles if a.type == "risk"]
    assert len(risks) == 1
    assert len(risks[0].event_ids) == 2


@pytest.mark.integration
def test_index_links_every_entity_article(vault_dir: Path) -> None:
    events = [
        _source(),
        _risk("Redis SPOF"),
        _event(DecisionMadePayload(type="decision_made", description="Use Kong", rationale="Mature")),
        _event(MilestoneAddedPayload(type="milestone_added", description="M2 auth", status="open")),
    ]
    articles = wiki.compile_project(vault_dir, "test--project", events, {}, _registries(vault_dir))
    index = next(a for a in articles if a.type == "project")
    for fragment in ("risks/redis-spof", "decisions/use-kong", "milestones/m2-auth"):
        assert fragment in index.body


@pytest.mark.integration
def test_index_keeps_the_pipe_format_render_parses_back(vault_dir: Path) -> None:
    from project_agent.schemas import ActionAddedPayload
    events = [
        _source(),
        _event(ActionAddedPayload(
            type="action_added", description="Deploy", owner="alice",
            due="2026-06-01", status="open",
        )),
    ]
    articles = wiki.compile_project(vault_dir, "test--project", events, {}, _registries(vault_dir))
    index = next(a for a in articles if a.type == "project")
    assert "- [ ] Deploy | owner: alice | due: 2026-06-01 | status: open" in index.body


@pytest.mark.integration
def test_compile_carries_pm_metadata_into_the_index(vault_dir: Path) -> None:
    metadata = {"id": "test--project", "status": "amber", "sponsor": "Dana"}
    articles = wiki.compile_project(
        vault_dir, "test--project", [_source()], metadata, _registries(vault_dir)
    )
    index = next(a for a in articles if a.type == "project")
    assert index.metadata["status"] == "amber"
    assert index.metadata["sponsor"] == "Dana"


@pytest.mark.integration
def test_prune_removes_articles_no_longer_produced(vault_dir: Path) -> None:
    stale = wiki.Article(path=Path("projects/p/risks/gone.md"), title="Gone", type="risk")
    wiki.write_article(vault_dir, stale)
    removed = wiki.prune(vault_dir, Path("projects/p"), keep=set())
    assert removed == [Path("projects/p/risks/gone.md")]
    assert not (vault_dir / stale.path).exists()


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

@pytest.mark.idempotence
def test_compiling_twice_writes_nothing_the_second_time(vault_dir: Path) -> None:
    events = [_source("kickoff.md"), _risk("Redis SPOF", owner="Bob Kim")]

    first = wiki.compile_project(vault_dir, "test--project", events, {}, _registries(vault_dir))
    for article in first:
        wiki.write_article(vault_dir, article)

    second = wiki.compile_project(vault_dir, "test--project", events, {}, _registries(vault_dir))
    assert [wiki.write_article(vault_dir, a) for a in second] == [False] * len(second)


@pytest.mark.idempotence
def test_input_hash_is_order_independent(vault_dir: Path) -> None:
    a, b = _risk("One", source_hash="sha256:aaa"), _risk("Two", source_hash="sha256:bbb")
    assert wiki.input_hash(wiki.event_bundle([a, b])) == wiki.input_hash(wiki.event_bundle([b, a]))


@pytest.mark.idempotence
def test_new_event_changes_the_input_hash(vault_dir: Path) -> None:
    a = _risk("One")
    assert wiki.input_hash(wiki.event_bundle([a])) != wiki.input_hash(
        wiki.event_bundle([a, _risk("Two", source_hash="sha256:bbb")])
    )


# ---------------------------------------------------------------------------
# Phase B — the shared layer across projects
# ---------------------------------------------------------------------------

def _compile_and_write(vault: Path, project_id: str, events: list[Event]) -> None:
    for article in wiki.compile_project(vault, project_id, events, {}, _registries(vault)):
        wiki.write_article(vault, article)


@pytest.mark.integration
def test_same_risk_name_in_two_projects_stays_separate(vault_dir: Path) -> None:
    """The collision case that breaks a flat vault."""
    _compile_and_write(vault_dir, "acme--one", [_source(), _risk("Redis SPOF")])
    _compile_and_write(vault_dir, "globex--two", [_source(), _risk("Redis SPOF")])

    assert (vault_dir / "projects/acme--one/risks/redis-spof.md").exists()
    assert (vault_dir / "projects/globex--two/risks/redis-spof.md").exists()


@pytest.mark.integration
def test_shared_person_article_fans_in_from_every_project(vault_dir: Path) -> None:
    (vault_dir / "_registry" / "people.yml").write_text(
        "bob-kim:\n  name: Bob Kim\n", encoding="utf-8"
    )
    _compile_and_write(vault_dir, "acme--one", [_source(), _risk("Redis SPOF", owner="Bob Kim")])
    _compile_and_write(vault_dir, "globex--two", [_source(), _risk("Auth gap", owner="Bob Kim")])

    articles = wiki.compile_shared(vault_dir, _registries(vault_dir))
    person = next(a for a in articles if a.path == Path("people/bob-kim.md"))
    assert "acme--one" in person.body
    assert "globex--two" in person.body


@pytest.mark.integration
def test_client_article_rolls_up_its_projects(vault_dir: Path) -> None:
    _compile_and_write(vault_dir, "acme--one", [_source()])
    _compile_and_write(vault_dir, "acme--two", [_source()])

    articles = wiki.compile_shared(vault_dir, _registries(vault_dir))
    client = next(a for a in articles if a.path == Path("clients/acme.md"))
    assert "acme--one" in client.body
    assert "acme--two" in client.body


@pytest.mark.integration
def test_portfolio_index_groups_projects_under_clients(vault_dir: Path) -> None:
    _compile_and_write(vault_dir, "acme--one", [_source()])
    _compile_and_write(vault_dir, "globex--two", [_source()])

    articles = wiki.compile_shared(vault_dir, _registries(vault_dir))
    index = next(a for a in articles if a.path == Path("index.md"))
    assert "kb/clients/acme" in index.body
    assert "kb/clients/globex" in index.body


@pytest.mark.idempotence
def test_shared_compile_is_idempotent(vault_dir: Path) -> None:
    (vault_dir / "_registry" / "people.yml").write_text(
        "bob-kim:\n  name: Bob Kim\n", encoding="utf-8"
    )
    _compile_and_write(vault_dir, "acme--one", [_source(), _risk("Redis SPOF", owner="Bob Kim")])

    for article in wiki.compile_shared(vault_dir, _registries(vault_dir)):
        wiki.write_article(vault_dir, article)
    again = wiki.compile_shared(vault_dir, _registries(vault_dir))
    assert [wiki.write_article(vault_dir, a) for a in again] == [False] * len(again)


@pytest.mark.idempotence
def test_compile_ignores_signal_events(vault_dir: Path) -> None:
    """Analyze appends signals after compile — they must not perturb the vault.

    Without this the second run sees a longer log, recomputes every input hash,
    and rewrites the whole vault.
    """
    from project_agent.schemas import SignalDetectedPayload

    facts = [_source("kickoff.md"), _risk("Redis SPOF")]
    before = wiki.compile_project(vault_dir, "p", facts, {}, _registries(vault_dir))

    signal = _event(SignalDetectedPayload(
        type="signal_detected", signal_id="sig-1", signal_type="risk_unaddressed",
        category="risk", severity="high", confidence=0.9,
        evidence=[facts[1].event_id], method="deterministic",
        rationale="stale", recommended_action="review",
    ))
    after = wiki.compile_project(vault_dir, "p", [*facts, signal], {}, _registries(vault_dir))

    assert [a.render() for a in before] == [a.render() for a in after]


@pytest.mark.unit
def test_slugify_trims_trailing_stopwords() -> None:
    """Truncation must not leave a slug dangling mid-phrase."""
    assert wiki.slugify("Redis is a single point of failure for rate limiting") == (
        "redis-is-a-single-point-of-failure"
    )


@pytest.mark.unit
def test_slugify_keeps_a_lone_stopword() -> None:
    assert wiki.slugify("of the") == "of"
