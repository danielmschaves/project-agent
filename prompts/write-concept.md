---
purpose: Write the overview for a shared entity article — a person, a client, or a concept
model: claude-sonnet-4-6
max_tokens: 1024
version: 1
expected_schema:
  type: object
  required: [summary]
  properties:
    summary:
      type: string
---

You are writing the overview for a shared article in a project knowledge base.
The article is about one entity — a person, a client, or a concept — that
appears across several projects. You are given the entity's registered name and
aliases, and the titles of every article that links to it, grouped by project.

Write the paragraph that orients a reader who has just landed on this page.

Rules:

- Describe the entity's role as evidenced by what links to it: which projects
  they appear in, and what kind of items they are attached to.
- Ground every statement in the supplied titles. You are given titles, not
  full articles — do not assert detail you cannot see, and never speculate
  about a person's performance, intent, or character.
- For a person, stay strictly professional and factual: where they appear and
  in what capacity. No evaluation.
- Do not write links, headings, or front-matter. Plain prose only — the
  surrounding article renders every link.
- Do not mention the pipeline, the events, or this instruction.
- Two to three sentences. If only one or two articles link here, one sentence
  is the right length.

Return JSON only:

{"summary": "..."}
