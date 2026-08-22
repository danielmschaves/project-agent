"""The compiled wiki: articles, wikilinks, the entity registry, and the
projection from events into markdown.

Every write into `kb/` goes through this module (PRD §5.1) — nothing outside
the package may create or edit an article.

Determinism is the whole design constraint. An article's identity is its
`input_hash`: the canonical JSON of the events that feed it. Same events →
same hash → same bytes, so a re-run produces a zero diff. That is also the
key the LLM response cache is keyed on once prose generation lands, which is
what lets an LLM write these articles without breaking the contract.

Two rules follow from it, and both are enforced by the wiki lint:
  * no wall-clock timestamps in a compiled article — only values derived
    from events, which are fixed once written;
  * wikilinks are always vault-absolute, because two projects may legitimately
    hold articles with the same filename.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from project_agent.schemas import Event

logger = logging.getLogger(__name__)

VAULT_DIRNAME = "kb"

#: Directories whose contents the compiler owns outright and may delete.
COMPILER_OWNED = ("projects", "people", "clients", "concepts", "research")

ArticleType = Literal[
    "index", "project", "risk", "decision", "milestone",
    "source", "person", "client", "concept",
]

#: event type -> (article type, folder under the project)
ENTITY_KINDS: dict[str, tuple[ArticleType, str]] = {
    "risk_added": ("risk", "risks"),
    "decision_made": ("decision", "decisions"),
    "milestone_added": ("milestone", "milestones"),
}

TEMPLATE_VERSION = "template@1"

#: Only these event types are projected into articles.
#:
#: signal_detected and pipeline_health are deliberately excluded. Analyze runs
#: after compile and appends signals to the same log, so including them would
#: make every second run see different inputs and rewrite the vault. It is also
#: the anti-circularity rule (CLAUDE.md): the wiki describes the project, not
#: the pipeline's own opinions about it.
PROJECTED_TYPES = frozenset({
    "source_ingested",
    "action_added",
    "blocker_added",
    "risk_added",
    "decision_made",
    "milestone_added",
})


# ---------------------------------------------------------------------------
# Paths, slugs, links
# ---------------------------------------------------------------------------

def default_vault_dir(project_dir: Path) -> Path:
    """The vault for a project at <root>/projects/<id> is <root>/kb."""
    return project_dir.parent.parent / VAULT_DIRNAME


_NON_SLUG = re.compile(r"[^a-z0-9]+")

#: Trimmed from the end of a truncated slug, so titles do not break mid-phrase
#: ("...-point-of-failure-for" reads worse than "...-point-of-failure").
_TRAILING_STOPWORDS = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "if", "in",
    "into", "is", "of", "on", "or", "over", "the", "to", "with",
})


def slugify(text: str, max_words: int = 8) -> str:
    """Stable, filesystem-safe slug from free text.

    Truncated to `max_words` so a slug stays readable and so two phrasings of
    the same fact are more likely to converge on one article. These become
    permanent filenames, so the rule has to stay stable — changing it renames
    every article in the vault.
    """
    words = [w for w in _NON_SLUG.sub("-", text.casefold()).split("-") if w]
    words = words[:max_words]
    while len(words) > 1 and words[-1] in _TRAILING_STOPWORDS:
        words.pop()
    return "-".join(words) or "untitled"


def link_target(article_path: Path) -> str:
    """Vault-absolute link target for a vault-relative article path."""
    return f"{VAULT_DIRNAME}/{article_path.with_suffix('').as_posix()}"


def wikilink(article_path: Path, alias: str | None = None) -> str:
    target = link_target(article_path)
    return f"[[{target}|{alias}]]" if alias else f"[[{target}]]"


_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def parse_wikilinks(body: str) -> list[str]:
    """Every link target in a body, in order of appearance."""
    return [m.group(1).strip() for m in _WIKILINK.finditer(body)]


# ---------------------------------------------------------------------------
# Article
# ---------------------------------------------------------------------------

class Article(BaseModel):
    """One markdown file in the vault."""

    path: Path  # vault-relative, e.g. projects/acme--x/risks/redis-spof.md
    title: str
    type: ArticleType
    project: str | None = None
    tags: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    source_hashes: list[str] = Field(default_factory=list)
    compiled_from: str = ""
    compiled_by: str = TEMPLATE_VERSION
    schema_version: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)
    body: str = ""

    def front_matter(self) -> dict[str, Any]:
        fm: dict[str, Any] = {
            "title": self.title,
            "type": self.type,
            "schema_version": self.schema_version,
            "compiled_by": self.compiled_by,
        }
        if self.compiled_from:
            fm["compiled_from"] = self.compiled_from
        if self.project:
            fm["project"] = self.project
        if self.tags:
            fm["tags"] = sorted(self.tags)
        if self.event_ids:
            fm["event_ids"] = sorted(self.event_ids)
        if self.source_hashes:
            fm["source_hashes"] = sorted(set(self.source_hashes))
        fm.update(self.metadata)
        return fm

    def render(self) -> str:
        """Serialize to markdown. Deterministic: front-matter keys are sorted."""
        fm = yaml.safe_dump(
            self.front_matter(),
            sort_keys=True,
            allow_unicode=True,
            default_flow_style=False,
        )
        return f"---\n{fm}---\n\n{self.body.strip()}\n"


def read_article(vault_dir: Path, rel_path: Path) -> Article:
    """Load an article back off disk."""
    text = (vault_dir / rel_path).read_text(encoding="utf-8")
    fm_text, _, body = text.partition("---\n")[2].partition("\n---\n")
    meta: dict[str, Any] = yaml.safe_load(fm_text) or {}
    known = {
        "title", "type", "schema_version", "compiled_by", "compiled_from",
        "project", "tags", "event_ids", "source_hashes",
    }
    return Article(
        path=rel_path,
        title=str(meta.get("title", rel_path.stem)),
        type=meta.get("type", "index"),
        project=meta.get("project"),
        tags=list(meta.get("tags", [])),
        event_ids=list(meta.get("event_ids", [])),
        source_hashes=list(meta.get("source_hashes", [])),
        compiled_from=str(meta.get("compiled_from", "")),
        compiled_by=str(meta.get("compiled_by", TEMPLATE_VERSION)),
        schema_version=int(meta.get("schema_version", 1)),
        metadata={k: v for k, v in meta.items() if k not in known},
        body=body.strip(),
    )


def write_article(vault_dir: Path, article: Article) -> bool:
    """Write an article; return True when the bytes actually changed."""
    path = vault_dir / article.path
    text = article.render()
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def prune(vault_dir: Path, subtree: Path, keep: set[Path]) -> list[Path]:
    """Delete compiled articles under `subtree` that are no longer produced.

    Without this, an entity that disappears from the events (retracted, or
    re-parsed under a new prompt into a different slug) would leave a stale
    article behind and the vault would stop describing the event log.
    """
    root = vault_dir / subtree
    if not root.exists():
        return []
    removed: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(vault_dir)
        if rel not in keep:
            path.unlink()
            removed.append(rel)
    return removed


# ---------------------------------------------------------------------------
# Entity registry — deterministic name resolution (see plan, multi-project)
# ---------------------------------------------------------------------------

class RegistryEntry(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)


class Registry(BaseModel):
    """Alias table for one entity kind, loaded from kb/_registry/<kind>.yml."""

    kind: str
    entries: dict[str, RegistryEntry] = Field(default_factory=dict)

    def resolve(self, name: str) -> str | None:
        """Slug for `name`, or None when the registry has never seen it.

        A pure lookup on purpose: entity resolution must not depend on a model,
        or the same corpus would resolve differently between runs.
        """
        key = name.strip().casefold()
        if not key:
            return None
        for slug, entry in self.entries.items():
            if key == slug.casefold() or key == entry.name.casefold():
                return slug
            if any(key == alias.casefold() for alias in entry.aliases):
                return slug
        return None


def load_registry(vault_dir: Path, kind: str) -> Registry:
    path = vault_dir / "_registry" / f"{kind}.yml"
    if not path.exists():
        return Registry(kind=kind)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Registry(
        kind=kind,
        entries={slug: RegistryEntry(**fields) for slug, fields in raw.items()},
    )


class Registries(BaseModel):
    people: Registry
    clients: Registry
    concepts: Registry

    @staticmethod
    def load(vault_dir: Path) -> Registries:
        return Registries(
            people=load_registry(vault_dir, "people"),
            clients=load_registry(vault_dir, "clients"),
            concepts=load_registry(vault_dir, "concepts"),
        )


def person_link(registries: Registries, name: str | None) -> str:
    """Wikilink to a registered person, or the bare name when unregistered.

    Unregistered names are surfaced by `pipeline lint` as proposed registry
    additions rather than guessed at here.
    """
    if not name:
        return "—"
    slug = registries.people.resolve(name)
    if slug is None:
        return name
    return wikilink(Path("people") / f"{slug}.md", name)


# ---------------------------------------------------------------------------
# Compile manifest + input hashing
# ---------------------------------------------------------------------------

class CompileRecord(BaseModel):
    input_hash: str
    compiled_by: str
    event_ids: list[str] = Field(default_factory=list)


def load_compile_manifest(path: Path) -> dict[str, CompileRecord]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: CompileRecord(**v) for k, v in raw.items()}


def save_compile_manifest(path: Path, manifest: dict[str, CompileRecord]) -> None:
    payload = {k: v.model_dump() for k, v in manifest.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def input_hash(bundle: Any) -> str:
    """Content address for an article's inputs."""
    canonical = json.dumps(
        bundle, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def event_bundle(events: list[Event]) -> list[dict[str, Any]]:
    """Canonical, order-independent representation of an article's inputs."""
    return sorted(
        (
            {
                "event_id": e.event_id,
                "ts": e.ts.isoformat(),
                "type": e.type,
                "source_ref": e.source_ref,
                "source_hash": e.source_hash,
                "payload": e.payload.model_dump(mode="json"),
            }
            for e in events
        ),
        key=lambda d: str(d["event_id"]),
    )


# ---------------------------------------------------------------------------
# Section builders — the pipe-delimited formats render.py parses back
# ---------------------------------------------------------------------------

def _facts(events: list[Event], event_type: str) -> list[dict[str, Any]]:
    return [e.payload.model_dump() for e in events if e.type == event_type]


def build_actions(events: list[Event]) -> str:
    lines = [
        f"- [ ] {a['description']} | owner: {a.get('owner') or '—'}"
        f" | due: {a.get('due') or '—'} | status: {a.get('status', 'open')}"
        for a in _facts(events, "action_added")
    ]
    return "\n".join(lines)


def build_blockers(events: list[Event]) -> str:
    lines = [
        f"- {b['description']} | owner: {b.get('owner') or '—'} | due: {b.get('due') or '—'}"
        for b in _facts(events, "blocker_added")
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase A — compile one project's articles from its events
# ---------------------------------------------------------------------------

def _source_paths(project_id: str, events: list[Event]) -> dict[str, Path]:
    """source_hash -> vault-relative path of that document's summary note."""
    paths: dict[str, Path] = {}
    taken: dict[str, str] = {}
    for event in sorted(
        (e for e in events if e.type == "source_ingested"), key=lambda e: e.source_hash
    ):
        stem = Path(str(event.payload.model_dump().get("filename", "source"))).stem
        slug = slugify(stem, max_words=12)
        if slug in taken and taken[slug] != event.source_hash:
            slug = f"{slug}-{event.source_hash[7:15]}"
        taken[slug] = event.source_hash
        paths[event.source_hash] = Path("projects") / project_id / "sources" / f"{slug}.md"
    return paths


def _evidence_section(
    events: list[Event], source_paths: dict[str, Path]
) -> str:
    lines = ["## Evidence", ""]
    for event in sorted(events, key=lambda e: e.event_id):
        src = source_paths.get(event.source_hash)
        label = wikilink(src, src.stem) if src else f"`{event.source_ref}`"
        lines.append(f"- {label} — `{event.event_id}`")
    return "\n".join(lines)


def _entity_articles(
    project_id: str,
    events: list[Event],
    source_paths: dict[str, Path],
    registries: Registries,
) -> list[Article]:
    """One article per distinct entity, grouped by slug.

    Slug collision is treated as identity: two events describing the risk the
    same way are the same risk and merge into one article carrying both
    event ids. Differently-worded duplicates stay separate — the wiki lint
    surfaces those rather than the compiler guessing.
    """
    articles: list[Article] = []
    for event_type, (article_type, folder) in ENTITY_KINDS.items():
        grouped: dict[str, list[Event]] = {}
        for event in events:
            if event.type != event_type:
                continue
            description = str(event.payload.model_dump().get("description", ""))
            grouped.setdefault(slugify(description), []).append(event)

        for slug, group in sorted(grouped.items()):
            payload = group[0].payload.model_dump()
            description = str(payload.get("description", slug))
            path = Path("projects") / project_id / folder / f"{slug}.md"

            facts: list[str] = []
            meta: dict[str, Any] = {}
            if article_type == "risk":
                meta["severity"] = payload.get("severity", "medium")
                meta["status"] = payload.get("status", "open")
                facts = [
                    f"**Severity:** {meta['severity']}",
                    f"**Status:** {meta['status']}",
                    f"**Owner:** {person_link(registries, payload.get('owner'))}",
                ]
            elif article_type == "decision":
                facts = [
                    f"**Decided by:** {person_link(registries, payload.get('decided_by'))}",
                ]
                if payload.get("rationale"):
                    meta["rationale"] = payload["rationale"]
            else:  # milestone
                meta["status"] = payload.get("status", "open")
                if payload.get("target_date"):
                    meta["target_date"] = payload["target_date"]
                facts = [
                    f"**Status:** {meta['status']}",
                    f"**Target:** {payload.get('target_date') or '—'}",
                    f"**Owner:** {person_link(registries, payload.get('owner'))}",
                ]

            body_parts = [f"# {description}", "", " · ".join(facts), ""]
            if payload.get("rationale"):
                body_parts += ["## Rationale", "", str(payload["rationale"]), ""]
            body_parts.append(_evidence_section(group, source_paths))

            articles.append(
                Article(
                    path=path,
                    title=description,
                    type=article_type,
                    project=project_id,
                    tags=[article_type, f"project/{project_id}"],
                    event_ids=[e.event_id for e in group],
                    source_hashes=[e.source_hash for e in group],
                    compiled_from=input_hash(event_bundle(group)),
                    metadata=meta,
                    body="\n".join(body_parts),
                )
            )
    return articles


def _source_articles(
    project_id: str,
    events: list[Event],
    source_paths: dict[str, Path],
    entity_articles: list[Article],
) -> list[Article]:
    """One summary note per raw document, backlinked to what it produced."""
    produced: dict[str, list[Article]] = {}
    for article in entity_articles:
        for source_hash in article.source_hashes:
            produced.setdefault(source_hash, []).append(article)

    articles: list[Article] = []
    for event in events:
        if event.type != "source_ingested":
            continue
        payload = event.payload.model_dump()
        filename = str(payload.get("filename", "source"))
        path = source_paths[event.source_hash]

        facts = [
            f"**Type:** {payload.get('source_type', 'doc')}",
            f"**Ingested:** {event.ts.date().isoformat()}",
        ]
        if payload.get("sender"):
            facts.append(f"**From:** {payload['sender']}")

        body = [f"# {filename}", "", " · ".join(facts), "", f"`{event.source_ref}`", ""]
        downstream = sorted(produced.get(event.source_hash, []), key=lambda a: a.path)
        body += ["## Produced", ""]
        if downstream:
            body += [f"- {wikilink(a.path, a.title)}" for a in downstream]
        else:
            body.append("_Nothing extracted from this document._")

        articles.append(
            Article(
                path=path,
                title=filename,
                type="source",
                project=project_id,
                tags=["source", f"project/{project_id}"],
                event_ids=[event.event_id],
                source_hashes=[event.source_hash],
                compiled_from=input_hash(
                    event_bundle([event]) + [str(a.path) for a in downstream]
                ),
                body="\n".join(body),
            )
        )
    return articles


def _index_article(
    project_id: str,
    events: list[Event],
    metadata: dict[str, Any],
    entity_articles: list[Article],
) -> Article:
    """The project's home note — replaces the old flat project.md."""
    counts = {
        "Sources": sum(1 for e in events if e.type == "source_ingested"),
        "Actions": sum(1 for e in events if e.type == "action_added"),
        "Blockers": sum(1 for e in events if e.type == "blocker_added"),
        "Risks": sum(1 for e in events if e.type == "risk_added"),
        "Decisions": sum(1 for e in events if e.type == "decision_made"),
        "Milestones": sum(1 for e in events if e.type == "milestone_added"),
    }
    title = str(metadata.get("project_name") or metadata.get("title") or project_id)

    body: list[str] = [f"# {title}", ""]
    # Counts are inlined so an agent reads the answer instead of querying for it.
    body += ["| " + " | ".join(counts) + " |"]
    body += ["|" + "---|" * len(counts)]
    body += ["| " + " | ".join(str(v) for v in counts.values()) + " |", ""]

    for heading in ("Mission", "Scope"):
        if metadata.get(heading.casefold()):
            body += [f"## {heading}", "", str(metadata[heading.casefold()]), ""]

    body += ["## Actions", "", build_actions(events) or "_No open actions._", ""]
    body += ["## Blockers", "", build_blockers(events) or "_No open blockers._", ""]

    for article_type, heading in (
        ("risk", "Risks"), ("decision", "Decisions"), ("milestone", "Milestones"),
    ):
        linked = sorted(
            (a for a in entity_articles if a.type == article_type), key=lambda a: a.path
        )
        body += [f"## {heading}", ""]
        body += (
            [f"- {wikilink(a.path, a.title)}" for a in linked]
            if linked
            else [f"_No {heading.casefold()} recorded._"]
        )
        body.append("")

    passthrough = {
        k: v
        for k, v in metadata.items()
        if k not in {"title", "type", "schema_version", "mission", "scope"}
    }
    passthrough.setdefault("id", project_id)

    return Article(
        path=Path("projects") / project_id / "index.md",
        title=title,
        type="project",
        project=project_id,
        tags=["project", f"project/{project_id}"],
        compiled_from=input_hash(
            event_bundle(events) + [sorted(str(k) for k in passthrough)]
        ),
        metadata=passthrough,
        body="\n".join(body),
    )


def compile_project(
    vault_dir: Path,
    project_id: str,
    events: list[Event],
    metadata: dict[str, Any],
    registries: Registries,
) -> list[Article]:
    """Phase A — project the event log into this project's articles.

    Shared people/clients/concepts are NOT written here: they fan in across
    projects and are compiled once, after every project, by Phase B.
    """
    events = [e for e in events if e.type in PROJECTED_TYPES]
    source_paths = _source_paths(project_id, events)
    entities = _entity_articles(project_id, events, source_paths, registries)
    sources = _source_articles(project_id, events, source_paths, entities)
    index = _index_article(project_id, events, metadata, entities)
    return [index, *entities, *sources]


# ---------------------------------------------------------------------------
# Phase B — shared articles, compiled once after every project
# ---------------------------------------------------------------------------

def read_project_articles(vault_dir: Path) -> list[Article]:
    """Every project article currently on disk, across all projects.

    Phase B reads the vault rather than taking Phase A's output as an
    argument: a single-project run still sees the other projects' articles,
    so the shared layer never silently narrows to whatever ran today.
    """
    root = vault_dir / "projects"
    if not root.exists():
        return []
    return [read_article(vault_dir, p.relative_to(vault_dir)) for p in sorted(root.rglob("*.md"))]


def _backlinks_to(articles: list[Article], target_prefix: str) -> dict[str, list[Article]]:
    """slug -> articles whose body links to <target_prefix>/<slug>."""
    found: dict[str, list[Article]] = {}
    for article in articles:
        for target in parse_wikilinks(article.body):
            if not target.startswith(target_prefix):
                continue
            slug = target[len(target_prefix):].strip("/")
            if slug and article not in found.setdefault(slug, []):
                found[slug].append(article)
    return found


def _mention_section(mentions: list[Article]) -> list[str]:
    lines: list[str] = []
    by_project: dict[str, list[Article]] = {}
    for article in mentions:
        by_project.setdefault(article.project or "unknown", []).append(article)
    for project_id in sorted(by_project):
        lines += [f"### {wikilink(Path('projects') / project_id / 'index.md', project_id)}", ""]
        lines += [
            f"- {wikilink(a.path, a.title)}"
            for a in sorted(by_project[project_id], key=lambda a: a.path)
        ]
        lines.append("")
    return lines


def compile_shared(vault_dir: Path, registries: Registries) -> list[Article]:
    """Phase B — people, clients, concepts and the portfolio home note.

    These fan in across projects, so they cannot be written inside the
    per-project loop: the iteration handling the first project does not yet
    know about the last one.
    """
    project_articles = read_project_articles(vault_dir)
    indexes = [a for a in project_articles if a.type == "project"]
    people_mentions = _backlinks_to(project_articles, f"{VAULT_DIRNAME}/people/")
    concept_mentions = _backlinks_to(project_articles, f"{VAULT_DIRNAME}/concepts/")

    articles: list[Article] = []

    for slug, entry in sorted(registries.people.entries.items()):
        mentions = people_mentions.get(slug, [])
        body = [f"# {entry.name}", ""]
        if entry.aliases:
            body += ["_Also known as: " + ", ".join(sorted(entry.aliases)) + "._", ""]
        body += ["## Mentioned in", ""]
        body += _mention_section(mentions) if mentions else ["_No linked articles yet._", ""]
        articles.append(
            Article(
                path=Path("people") / f"{slug}.md",
                title=entry.name,
                type="person",
                tags=["person"],
                compiled_from=input_hash([slug, sorted(entry.aliases), sorted(str(a.path) for a in mentions)]),
                body="\n".join(body),
            )
        )

    for slug, entry in sorted(registries.concepts.entries.items()):
        mentions = concept_mentions.get(slug, [])
        body = [f"# {entry.name}", ""]
        if entry.aliases:
            body += ["_Also known as: " + ", ".join(sorted(entry.aliases)) + "._", ""]
        body += ["## Referenced by", ""]
        body += _mention_section(mentions) if mentions else ["_No linked articles yet._", ""]
        articles.append(
            Article(
                path=Path("concepts") / f"{slug}.md",
                title=entry.name,
                type="concept",
                tags=["concept"],
                compiled_from=input_hash([slug, sorted(entry.aliases), sorted(str(a.path) for a in mentions)]),
                body="\n".join(body),
            )
        )

    # Clients come from the <client>--<project> id convention, so a client
    # article exists as soon as one of its projects does.
    by_client: dict[str, list[Article]] = {}
    for index in indexes:
        client = (index.project or "").split("--", 1)[0] or "unknown"
        by_client.setdefault(client, []).append(index)

    for client, client_indexes in sorted(by_client.items()):
        client_entry = registries.clients.entries.get(client)
        name = client_entry.name if client_entry else client
        ordered = sorted(client_indexes, key=lambda a: a.path)
        body = [f"# {name}", "", "## Projects", ""]
        body += [f"- {wikilink(a.path, a.title)}" for a in ordered]
        articles.append(
            Article(
                path=Path("clients") / f"{client}.md",
                title=name,
                type="client",
                tags=["client"],
                compiled_from=input_hash([client, name, sorted(str(a.path) for a in ordered)]),
                body="\n".join(body),
            )
        )

    body = ["# Portfolio", ""]
    if by_client:
        for client, client_indexes in sorted(by_client.items()):
            client_entry = registries.clients.entries.get(client)
            label = client_entry.name if client_entry else client
            body += [f"## {wikilink(Path('clients') / f'{client}.md', label)}", ""]
            body += [
                f"- {wikilink(a.path, a.title)}"
                for a in sorted(client_indexes, key=lambda a: a.path)
            ]
            body.append("")
    else:
        body += ["_No projects compiled yet._", ""]

    articles.append(
        Article(
            path=Path("index.md"),
            title="Portfolio",
            type="index",
            compiled_from=input_hash(sorted(str(a.path) for a in indexes)),
            body="\n".join(body),
        )
    )
    return articles
