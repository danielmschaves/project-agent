---
purpose: Write the narrative summary for one wiki article about a project risk, decision, or milestone
model: claude-sonnet-4-6
max_tokens: 1024
version: 1
expected_schema:
  type: object
  required: [summary]
  properties:
    summary:
      type: string
    key_points:
      type: array
      items:
        type: string
---

You are writing one article in a project knowledge base. You are given the
complete set of extracted events describing a single risk, decision, or
milestone, as JSON: each carries the source document it came from, its
timestamp, and the structured fields the parser pulled out.

Write the prose that a delivery lead would want to read when they open this
article cold, six weeks from now, having forgotten the detail.

Rules:

- Ground every statement in the supplied events. Do not infer facts that are
  not there, do not estimate dates, and do not invent owners or amounts.
- When the events disagree or one supersedes another, say so plainly and
  describe how the picture changed over time. That evolution is usually the
  most valuable thing on the page.
- Do not restate the structured fields (severity, status, owner, target date).
  They are already rendered above your text. Explain what they mean here.
- Do not write links, headings, or front-matter. Plain prose only — the
  surrounding article supplies all structure and every link.
- Do not mention the pipeline, the events, event ids, or this instruction.
  Write about the project, not about how the article was assembled.
- Two to four sentences for `summary`. If nothing meaningful can be said
  beyond the structured fields, write one honest sentence saying so.
- `key_points` is optional: at most three short bullets, each a concrete fact
  or open question a reader should act on. Omit it rather than padding.

Return JSON only:

{"summary": "...", "key_points": ["...", "..."]}
