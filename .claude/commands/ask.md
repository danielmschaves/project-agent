Answer a question against the compiled wiki.

Work from the vault, not from memory. Start by narrowing with the search CLI
rather than reading the whole tree:

```bash
docker compose run --rm pipeline python -m pipeline search "<terms from the question>"
```

Then read the top articles it returns. Follow their wikilinks — an article's
Evidence section points at the source note for the raw document behind it, and
shared `kb/people/`, `kb/clients/` and `kb/concepts/` articles fan back out to
every project that touches them.

For anything aggregate ("which projects have open high-severity risks", "what
did this email produce", "who owns the most blockers"), query the warehouse
directly instead of reading files:

```bash
docker compose run --rm pipeline python -m pipeline wiki-sql "
  SELECT project, title FROM documents
  WHERE type = 'risk' ORDER BY project"
```

Useful relations: `documents`, `links`, `backlinks`, `broken_links`,
`orphan_documents`, `document_tags`, `document_provenance` (article → the
events it was compiled from), plus the event views `events`, `risks`,
`decisions`, `milestones`, `actions`, `blockers`.

Cite the articles you used by path, and say plainly when the wiki does not
answer the question rather than filling the gap. If the answer is worth
keeping, offer to file it into `kb/outputs/<date>-<slug>.md` — that is the one
part of the vault where hand-written and SQL-block content is allowed.
