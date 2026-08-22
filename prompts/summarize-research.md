---
purpose: Summarize a research document and extract the concepts it is about
model: claude-sonnet-4-6
max_tokens: 2048
version: 1
expected_schema:
  type: object
  required: [summary, concepts]
  properties:
    summary:
      type: string
    topics:
      type: array
      items:
        type: string
    concepts:
      type: array
      items:
        type: object
        required: [name]
        properties:
          name:
            type: string
          description:
            type: string
---

You are indexing a document into a research knowledge base — an article, a
paper, a repository README, a set of notes. Produce a summary that stands in
for the document, and name the concepts it is actually about.

For `summary`: three to six sentences. Say what the document argues or
describes, what evidence or mechanism it rests on, and what a reader would take
away. Write for someone deciding whether to open it. Ground every claim in the
document; where it is speculative or opinionated, say so rather than restating
it as fact.

For `topics`: up to six short tags describing subject area. Lowercase,
hyphenated, no spaces.

For `concepts`: the ideas this document is genuinely *about*, each with a
one-sentence description written to stand on its own — the concept article may
end up linked from a dozen documents, so define the concept, not this
document's take on it.

- Between one and five. A document about one thing well should yield one.
- Name concepts as they are normally named in the field, so two documents about
  the same idea converge on the same name. Prefer the established term to the
  author's coinage.
- Do not extract people, companies, product names, or the document's own title
  as concepts.
- Do not invent a concept to reach a count. Fewer, well-named ones are the
  point — every concept becomes a permanent article.

Return JSON only:

{"summary": "...", "topics": ["..."], "concepts": [{"name": "...", "description": "..."}]}
