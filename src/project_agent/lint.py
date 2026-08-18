"""Health checks over the compiled wiki.

The vault is generated, so most defects are structural rather than editorial:
a link that points at nothing, an article nothing links to, a reference to an
event that has since been retracted. These are cheap to find deterministically
and they are what stop the wiki drifting away from the event log.

The one check that is not structural is the unresolved-entity report. The
compiler resolves people and concepts through `kb/_registry/*.yml` and refuses
to guess; every name it could not resolve is collected here and proposed as a
registry addition for a human to merge. That is the loop that keeps the shared
layer honest as projects are added — see the multi-project notes in the plan.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from project_agent import wiki
from project_agent.schemas import Event

logger = logging.getLogger(__name__)

Severity = Literal["error", "warning", "info"]

#: Article trees the compiler owns outright. Hand-written content and
#: DuckDB-plugin blocks are allowed in kb/outputs/ and nowhere else.
_COMPILER_TREES = ("projects", "people", "clients", "concepts", "research")

#: Markers written by the Obsidian DuckDB/MotherDuck plugin. Its frozen result
#: blocks carry a wall-clock timestamp, and its scheduled refresh writes one
#: into front-matter; either makes a compiled article differ on every sweep.
_FORBIDDEN_MARKERS = (
    "<!-- md:cache",
    "duckdb-motherduck-refresh",
)

#: Front-matter every compiled article must carry.
_REQUIRED_FIELDS = ("title", "type", "schema_version", "compiled_by")


class Finding(BaseModel):
    """One thing wrong with the vault."""

    check: str
    severity: Severity
    message: str
    path: str | None = None
    suggestion: str | None = None


class Report(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    #: slug -> proposed registry entry, ready to paste into kb/_registry/
    proposed_people: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def by_check(self) -> dict[str, list[Finding]]:
        grouped: dict[str, list[Finding]] = {}
        for finding in self.findings:
            grouped.setdefault(finding.check, []).append(finding)
        return grouped


def _is_compiler_owned(path: Path) -> bool:
    return path.parts[0] in _COMPILER_TREES if path.parts else False


def _load_articles(vault_dir: Path) -> list[wiki.Article]:
    articles: list[wiki.Article] = []
    for path in sorted(vault_dir.rglob("*.md")):
        rel = path.relative_to(vault_dir)
        if rel.parts and rel.parts[0] == "outputs":
            continue  # human-owned; not held to the compiled-article contract
        try:
            articles.append(wiki.read_article(vault_dir, rel))
        except Exception:
            articles.append(
                wiki.Article(path=rel, title=rel.stem, type="index", body="")
            )
    return articles


def check_links(articles: list[wiki.Article]) -> list[Finding]:
    """Links that resolve to nothing, and links that are not vault-absolute."""
    known = {a.path.as_posix() for a in articles}
    findings: list[Finding] = []
    for article in articles:
        for target in wiki.parse_wikilinks(article.body):
            if not target.startswith(f"{wiki.VAULT_DIRNAME}/"):
                findings.append(Finding(
                    check="relative_link",
                    severity="error",
                    path=article.path.as_posix(),
                    message=f"link '{target}' is not vault-absolute",
                    suggestion=(
                        "Two projects may hold the same filename; Obsidian would "
                        "resolve this to whichever it finds first. Emit "
                        f"[[{wiki.VAULT_DIRNAME}/…]]."
                    ),
                ))
                continue
            resolved = target.removeprefix(f"{wiki.VAULT_DIRNAME}/")
            if not resolved.endswith(".md"):
                resolved += ".md"
            if resolved not in known:
                findings.append(Finding(
                    check="broken_link",
                    severity="error",
                    path=article.path.as_posix(),
                    message=f"link '{target}' points at nothing",
                ))
    return findings


def check_orphans(articles: list[wiki.Article]) -> list[Finding]:
    """Articles nothing links to — usually a stale file the prune missed."""
    linked: set[str] = set()
    for article in articles:
        for target in wiki.parse_wikilinks(article.body):
            resolved = target.removeprefix(f"{wiki.VAULT_DIRNAME}/")
            linked.add(resolved if resolved.endswith(".md") else f"{resolved}.md")

    findings: list[Finding] = []
    for article in articles:
        posix = article.path.as_posix()
        # index.md files are entry points; source notes are reached from the
        # project index rather than linked to individually.
        if article.type in ("index", "project") or posix in linked:
            continue
        findings.append(Finding(
            check="orphan_article",
            severity="warning",
            path=posix,
            message="nothing links to this article",
        ))
    return findings


def check_front_matter(vault_dir: Path, articles: list[wiki.Article]) -> list[Finding]:
    """Required fields, read from the file rather than the parsed model.

    `read_article` fills in defaults for anything absent, so checking the model
    would always pass. The point of this check is to catch an article whose
    front-matter was never written properly in the first place.
    """
    findings: list[Finding] = []
    for article in articles:
        if not _is_compiler_owned(article.path):
            continue
        text = (vault_dir / article.path).read_text(encoding="utf-8")
        raw = text.partition("---\n")[2].partition("\n---\n")[0]
        try:
            fm = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            findings.append(Finding(
                check="invalid_front_matter",
                severity="error",
                path=article.path.as_posix(),
                message=f"front-matter is not valid YAML: {exc}",
            ))
            continue
        missing = [field for field in _REQUIRED_FIELDS if not fm.get(field)]
        if missing:
            findings.append(Finding(
                check="missing_front_matter",
                severity="error",
                path=article.path.as_posix(),
                message=f"missing required field(s): {', '.join(missing)}",
            ))
    return findings


def check_forbidden_markers(vault_dir: Path, articles: list[wiki.Article]) -> list[Finding]:
    """Plugin-written content inside a compiled article."""
    findings: list[Finding] = []
    for article in articles:
        if not _is_compiler_owned(article.path):
            continue
        text = (vault_dir / article.path).read_text(encoding="utf-8")
        for marker in _FORBIDDEN_MARKERS:
            if marker in text:
                findings.append(Finding(
                    check="forbidden_marker",
                    severity="error",
                    path=article.path.as_posix(),
                    message=f"contains '{marker}'",
                    suggestion=(
                        "The DuckDB/MotherDuck plugin stamps a wall-clock "
                        "timestamp when it freezes or refreshes, so the article "
                        "changes on every sweep. Keep its blocks in kb/outputs/."
                    ),
                ))
    return findings


def check_event_references(
    articles: list[wiki.Article], events: list[Event]
) -> list[Finding]:
    """Articles citing events that are gone — retracted, or never existed."""
    live = {e.event_id for e in events}
    if not live:
        return []
    findings: list[Finding] = []
    for article in articles:
        dangling = [eid for eid in article.event_ids if eid not in live]
        if dangling:
            findings.append(Finding(
                check="dangling_event",
                severity="error",
                path=article.path.as_posix(),
                message=f"cites {len(dangling)} event(s) not in the log: {dangling[0]}",
                suggestion="Re-run compile; a retraction may have removed the evidence.",
            ))
    return findings


def check_stale_manifest(
    vault_dir: Path, manifest_paths: list[Path]
) -> list[Finding]:
    """compile_manifest entries whose article is no longer on disk."""
    findings: list[Finding] = []
    for manifest_path in manifest_paths:
        for article_path in sorted(wiki.load_compile_manifest(manifest_path)):
            if not (vault_dir / article_path).exists():
                findings.append(Finding(
                    check="stale_manifest",
                    severity="warning",
                    path=article_path,
                    message=f"recorded in {manifest_path.name} but missing from the vault",
                ))
    return findings


def check_unresolved_entities(
    events: list[Event], registries: wiki.Registries
) -> tuple[list[Finding], dict[str, dict[str, Any]]]:
    """Names the compiler could not resolve, as proposed registry entries.

    The compiler never guesses at identity, so these names currently render as
    plain text and contribute nothing to the shared layer. Merging them is a
    human decision, which is exactly why they surface here instead.
    """
    seen: dict[str, set[str]] = {}
    for event in events:
        payload = event.payload.model_dump()
        for field in ("owner", "decided_by"):
            name = payload.get(field)
            if not name or not isinstance(name, str):
                continue
            if registries.people.resolve(name) is not None:
                continue
            seen.setdefault(wiki.slugify(name, max_words=4), set()).add(name.strip())

    findings: list[Finding] = []
    proposed: dict[str, dict[str, Any]] = {}
    for slug, names in sorted(seen.items()):
        canonical = sorted(names, key=len)[-1]
        proposed[slug] = {"name": canonical, "aliases": sorted(names)}
        findings.append(Finding(
            check="unresolved_entity",
            severity="info",
            message=f"'{canonical}' is not in kb/_registry/people.yml",
            suggestion=f"Add '{slug}' so their articles link into the shared layer.",
        ))
    return findings, proposed


def lint_vault(
    vault_dir: Path,
    events: list[Event] | None = None,
    manifest_paths: list[Path] | None = None,
) -> Report:
    """Run every deterministic check over the vault."""
    articles = _load_articles(vault_dir)
    events = events or []
    registries = wiki.Registries.load(vault_dir)

    report = Report()
    report.findings += check_front_matter(vault_dir, articles)
    report.findings += check_links(articles)
    report.findings += check_orphans(articles)
    report.findings += check_forbidden_markers(vault_dir, articles)
    report.findings += check_event_references(articles, events)
    report.findings += check_stale_manifest(vault_dir, manifest_paths or [])

    entity_findings, proposed = check_unresolved_entities(events, registries)
    report.findings += entity_findings
    report.proposed_people = proposed
    return report


def render_registry_proposal(proposed: dict[str, dict[str, Any]]) -> str:
    """The YAML a human can paste into kb/_registry/people.yml."""
    if not proposed:
        return ""
    return str(yaml.safe_dump(proposed, sort_keys=True, allow_unicode=True))


# ---------------------------------------------------------------------------
# LLM review — the problems a structural check cannot see
# ---------------------------------------------------------------------------

def vault_inventory(
    vault_dir: Path, report: Report
) -> dict[str, Any]:
    """The digest handed to the review prompt.

    Titles and paths only — not bodies. Cheap, and it keeps the model from
    asserting detail it has not actually been shown.
    """
    articles = sorted(_load_articles(vault_dir), key=lambda a: a.path)
    return {
        "articles": [
            {
                "path": a.path.as_posix(),
                "type": a.type,
                "project": a.project,
                "title": a.title,
            }
            for a in articles
        ],
        "structural_findings": sorted(
            f"{f.check}: {f.path or '-'} — {f.message}" for f in report.findings
        ),
    }


def render_review(observations: list[dict[str, Any]], date_str: str) -> str:
    """The markdown filed into kb/outputs/."""
    lines = [
        "---",
        f"title: Wiki review {date_str}",
        "type: review",
        "---",
        "",
        f"# Wiki review — {date_str}",
        "",
    ]
    if not observations:
        lines += ["No observations. The wiki reads as internally consistent.", ""]
        return "\n".join(lines)

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        by_kind.setdefault(str(observation.get("kind", "other")), []).append(observation)

    for kind in sorted(by_kind):
        lines += [f"## {kind.title()}", ""]
        for observation in by_kind[kind]:
            lines.append(f"- {observation.get('detail', '').strip()}")
            for article in observation.get("articles", []) or []:
                path = Path(str(article))
                lines.append(f"  - {wiki.wikilink(path, path.stem)}")
        lines.append("")
    return "\n".join(lines)
