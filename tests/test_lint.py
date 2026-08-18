"""Tests for project_agent.lint — structural health checks over the vault."""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from project_agent import events as events_api
from project_agent import lint, wiki
from project_agent.schemas import Event, LLMActor, RiskAddedPayload


def _write(vault: Path, path: str, body: str, **kw: object) -> wiki.Article:
    article = wiki.Article(
        path=Path(path),
        title=kw.pop("title", Path(path).stem),  # type: ignore[arg-type]
        type=kw.pop("type", "risk"),  # type: ignore[arg-type]
        body=body,
        **kw,  # type: ignore[arg-type]
    )
    wiki.write_article(vault, article)
    return article


def _risk_event(description: str, owner: str | None) -> Event:
    payload = RiskAddedPayload(
        type="risk_added", description=description, severity="high",
        owner=owner, status="open",
    )
    return Event(
        event_id=str(uuid.uuid4()),
        ts=datetime(2026, 5, 14, tzinfo=timezone.utc),
        run_id="run-001", project_id="acme--one", type="risk_added",
        actor=LLMActor(kind="llm", model="m", prompt="p@1"),
        source_ref="raw/docs/n.md", source_hash="sha256:aaa", payload=payload,
        hash=events_api.hash_event(payload.model_dump(), "sha256:aaa"),
    )


def _checks(report: lint.Report) -> set[str]:
    return {f.check for f in report.findings}


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_broken_link_is_an_error(vault_dir: Path) -> None:
    _write(vault_dir, "projects/acme--one/index.md", "# I\n\n[[kb/people/ghost|Ghost]]", type="project")
    report = lint.lint_vault(vault_dir)
    assert "broken_link" in _checks(report)
    assert report.errors


@pytest.mark.integration
def test_relative_link_is_an_error(vault_dir: Path) -> None:
    """Relative links resolve to whichever same-named file Obsidian finds first."""
    _write(vault_dir, "projects/acme--one/index.md", "# I\n\n[[redis-spof]]", type="project")
    report = lint.lint_vault(vault_dir)
    assert "relative_link" in _checks(report)


@pytest.mark.integration
def test_resolvable_link_is_not_reported(vault_dir: Path) -> None:
    _write(vault_dir, "people/bob-kim.md", "# Bob", type="person")
    _write(
        vault_dir, "projects/acme--one/index.md",
        "# I\n\n[[kb/people/bob-kim|Bob]]", type="project",
    )
    assert "broken_link" not in _checks(lint.lint_vault(vault_dir))


# ---------------------------------------------------------------------------
# Orphans and front-matter
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_orphan_article_is_a_warning(vault_dir: Path) -> None:
    _write(vault_dir, "projects/acme--one/risks/lonely.md", "# Lonely")
    report = lint.lint_vault(vault_dir)
    assert "orphan_article" in _checks(report)
    assert not report.errors


@pytest.mark.integration
def test_linked_article_is_not_an_orphan(vault_dir: Path) -> None:
    _write(vault_dir, "projects/acme--one/risks/linked.md", "# Linked")
    _write(
        vault_dir, "projects/acme--one/index.md",
        "# I\n\n[[kb/projects/acme--one/risks/linked|Linked]]", type="project",
    )
    assert "orphan_article" not in _checks(lint.lint_vault(vault_dir))


@pytest.mark.integration
def test_missing_front_matter_is_an_error(vault_dir: Path) -> None:
    path = vault_dir / "projects/acme--one/risks/bare.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: risk\n---\n\n# Bare\n", encoding="utf-8")
    assert "missing_front_matter" in _checks(lint.lint_vault(vault_dir))


# ---------------------------------------------------------------------------
# Plugin markers — the DuckDB/MotherDuck freeze hazard
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_frozen_sql_block_in_a_compiled_article_is_an_error(vault_dir: Path) -> None:
    _write(
        vault_dir, "projects/acme--one/risks/r.md",
        "# R\n\n<!-- md:cache hash=b54c0ac2 conn=local ts=2026-05-15T09:27:08Z rows=5 -->\n",
    )
    report = lint.lint_vault(vault_dir)
    assert "forbidden_marker" in _checks(report)


@pytest.mark.integration
def test_scheduled_refresh_marker_is_an_error(vault_dir: Path) -> None:
    path = vault_dir / "projects/acme--one/risks/r.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntitle: R\ntype: risk\nschema_version: 1\ncompiled_by: template@1\n"
        "duckdb-motherduck-refresh: daily\n---\n\n# R\n",
        encoding="utf-8",
    )
    assert "forbidden_marker" in _checks(lint.lint_vault(vault_dir))


@pytest.mark.integration
def test_outputs_may_hold_sql_blocks(vault_dir: Path) -> None:
    """kb/outputs/ is the human-owned lane; the contract does not apply there."""
    (vault_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (vault_dir / "outputs" / "2026-05-15-answer.md").write_text(
        "# Answer\n\n<!-- md:cache hash=abc ts=2026-05-15T09:27:08Z rows=5 -->\n",
        encoding="utf-8",
    )
    assert "forbidden_marker" not in _checks(lint.lint_vault(vault_dir))


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_article_citing_a_missing_event_is_an_error(vault_dir: Path) -> None:
    _write(vault_dir, "projects/acme--one/risks/r.md", "# R", event_ids=["evt-gone"])
    report = lint.lint_vault(vault_dir, events=[_risk_event("Redis", None)])
    assert "dangling_event" in _checks(report)


@pytest.mark.integration
def test_stale_manifest_entry_is_reported(vault_dir: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "compile_manifest.json"
    wiki.save_compile_manifest(manifest, {
        "projects/acme--one/risks/deleted.md": wiki.CompileRecord(
            input_hash="sha256:x", compiled_by="template@1",
        )
    })
    report = lint.lint_vault(vault_dir, manifest_paths=[manifest])
    assert "stale_manifest" in _checks(report)


# ---------------------------------------------------------------------------
# Unresolved entities — the loop that keeps the shared layer honest
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_unregistered_owner_is_proposed_for_the_registry(vault_dir: Path) -> None:
    report = lint.lint_vault(vault_dir, events=[_risk_event("Redis", "Carol Davis")])
    assert "unresolved_entity" in _checks(report)
    assert "carol-davis" in report.proposed_people
    assert report.proposed_people["carol-davis"]["name"] == "Carol Davis"


@pytest.mark.integration
def test_registered_owner_is_not_proposed(vault_dir: Path) -> None:
    (vault_dir / "_registry" / "people.yml").write_text(
        "carol-davis:\n  name: Carol Davis\n", encoding="utf-8"
    )
    report = lint.lint_vault(vault_dir, events=[_risk_event("Redis", "Carol Davis")])
    assert "unresolved_entity" not in _checks(report)


@pytest.mark.integration
def test_aliases_of_one_person_collapse_to_a_single_proposal(vault_dir: Path) -> None:
    report = lint.lint_vault(vault_dir, events=[
        _risk_event("A", "Carol Davis"),
        _risk_event("B", "carol davis"),
    ])
    assert list(report.proposed_people) == ["carol-davis"]
    assert len(report.proposed_people["carol-davis"]["aliases"]) == 2


@pytest.mark.unit
def test_registry_proposal_renders_as_valid_yaml() -> None:
    import yaml as _yaml
    rendered = lint.render_registry_proposal(
        {"carol-davis": {"name": "Carol Davis", "aliases": ["Carol Davis"]}}
    )
    assert _yaml.safe_load(rendered)["carol-davis"]["name"] == "Carol Davis"


@pytest.mark.unit
def test_empty_proposal_renders_nothing() -> None:
    assert lint.render_registry_proposal({}) == ""


# ---------------------------------------------------------------------------
# A compiled vault should lint clean
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_a_freshly_compiled_vault_has_no_errors(vault_dir: Path) -> None:
    from project_agent.schemas import SourceIngestedPayload

    payload = SourceIngestedPayload(
        type="source_ingested", filename="notes.md", source_type="doc", size_bytes=5
    )
    source = Event(
        event_id=str(uuid.uuid4()), ts=datetime(2026, 5, 14, tzinfo=timezone.utc),
        run_id="r", project_id="acme--one", type="source_ingested",
        actor=LLMActor(kind="llm", model="m", prompt="p@1"),
        source_ref="raw/docs/notes.md", source_hash="sha256:aaa", payload=payload,
        hash=events_api.hash_event(payload.model_dump(), "sha256:aaa"),
    )
    events = [source, _risk_event("Redis SPOF", None)]
    registries = wiki.Registries.load(vault_dir)
    for article in wiki.compile_project(vault_dir, "acme--one", events, {}, registries):
        wiki.write_article(vault_dir, article)
    for article in wiki.compile_shared(vault_dir, registries):
        wiki.write_article(vault_dir, article)

    report = lint.lint_vault(vault_dir, events=events)
    assert report.errors == [], [f.model_dump() for f in report.errors]


# ---------------------------------------------------------------------------
# The lint stage — health events, deduped
# ---------------------------------------------------------------------------

def _project_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A projects/ root with one project, plus the sibling vault."""
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "acme--one"
    (project_dir / "raw").mkdir(parents=True)
    (project_dir / "project.notes.md").write_text("# Notes\n", encoding="utf-8")
    (project_dir / "events.ndjson").touch()

    vault = tmp_path / "kb"
    for sub in ("projects", "people", "clients", "concepts", "_registry"):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    return projects_dir, vault


@pytest.mark.integration
def test_lint_stage_emits_a_health_event_for_findings(tmp_path: Path) -> None:
    from pipeline.stages import run_lint

    projects_dir, vault = _project_tree(tmp_path)
    _write(vault, "projects/acme--one/index.md", "# I\n\n[[kb/people/ghost|Ghost]]", type="project")

    result, report = run_lint(vault, projects_dir, "run-001")
    assert result.status == "ok"
    assert report.errors

    events = events_api.query(
        projects_dir / "acme--one" / "events.ndjson", types=["pipeline_health"]
    )
    assert len(events) == 1
    assert events[0].payload.model_dump()["category"] == "wiki_lint"


@pytest.mark.idempotence
def test_lint_stage_does_not_re_emit_unchanged_findings(tmp_path: Path) -> None:
    """A second lint over an unchanged vault must append nothing."""
    from pipeline.stages import run_lint

    projects_dir, vault = _project_tree(tmp_path)
    _write(vault, "projects/acme--one/index.md", "# I\n\n[[kb/people/ghost|Ghost]]", type="project")
    events_path = projects_dir / "acme--one" / "events.ndjson"

    run_lint(vault, projects_dir, "run-001")
    first = events_path.read_text(encoding="utf-8")
    run_lint(vault, projects_dir, "run-002")

    assert events_path.read_text(encoding="utf-8") == first


@pytest.mark.integration
def test_lint_stage_emits_nothing_for_a_clean_vault(tmp_path: Path) -> None:
    from pipeline.stages import run_lint

    projects_dir, vault = _project_tree(tmp_path)
    _write(vault, "projects/acme--one/index.md", "# I", type="project")

    run_lint(vault, projects_dir, "run-001")
    assert events_api.query(projects_dir / "acme--one" / "events.ndjson") == []


@pytest.mark.integration
def test_no_events_flag_suppresses_emission(tmp_path: Path) -> None:
    from pipeline.stages import run_lint

    projects_dir, vault = _project_tree(tmp_path)
    _write(vault, "projects/acme--one/index.md", "# I\n\n[[kb/people/ghost]]", type="project")

    run_lint(vault, projects_dir, "run-001", emit_events=False)
    assert events_api.query(projects_dir / "acme--one" / "events.ndjson") == []


# ---------------------------------------------------------------------------
# Review rendering
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_review_renders_observations_with_links() -> None:
    out = lint.render_review(
        [{"kind": "gap", "articles": ["projects/x/risks/y.md"], "detail": "No owner."}],
        "2026-05-15",
    )
    assert "## Gap" in out
    assert "No owner." in out
    assert "[[kb/projects/x/risks/y|y]]" in out


@pytest.mark.unit
def test_review_renders_an_empty_result_honestly() -> None:
    assert "No observations" in lint.render_review([], "2026-05-15")
