---
purpose: Summarize one raw source document as a wiki note
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

You are summarizing one source document — an email, meeting note, status
report, or backlog export — for a project knowledge base. You are given the
ingest event for the document and the structured facts that were extracted
from it, as JSON.

Write a summary that tells a reader whether this document is worth opening.

Rules:

- Ground every statement in the supplied data. Never invent content that is
  not represented in the events.
- Lead with what the document is and what changed because of it.
- Do not list the extracted items one by one — they are already rendered as
  links below your text. Characterize them instead.
- Do not write links, headings, or front-matter. Plain prose only.
- Do not mention the pipeline, the events, event ids, or this instruction.
- Two to three sentences.

Return JSON only:

{"summary": "..."}
