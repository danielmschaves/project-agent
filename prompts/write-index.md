---
purpose: Write the orientation paragraph at the top of a project's wiki index
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

You are writing the opening paragraph of a project's home page in a knowledge
base. You are given the project's declared metadata and the titles of its
risks, decisions and milestones, as JSON.

Write the paragraph that tells a reader where this project stands.

Rules:

- Ground every statement in the supplied data. Do not invent status, dates,
  progress, or sentiment that is not represented.
- Say what the project is and what currently dominates it — the themes that
  the risks and blockers cluster around.
- Do not restate the counts or repeat the item titles; both are rendered
  around your text. Characterize them.
- Do not write links, headings, or front-matter. Plain prose only.
- Do not mention the pipeline, the events, or this instruction.
- Two to four sentences.

Return JSON only:

{"summary": "..."}
