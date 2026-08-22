# Research corpus

The second ingest lane. Project documents describe an engagement; these
describe a subject. Both compile into the same vault, and they meet at
`kb/concepts/` — a concept extracted from a paper here is the same article a
project risk links to.

## Dropping documents in

Anything readable as text goes in `raw/_inbox/`:

- **Web articles** — the [Obsidian Web Clipper](https://obsidian.md/clipper)
  extension saves a page as markdown. Point it at `research/raw/_inbox/`.
- **Papers and notes** — `.md`, `.txt`, `.pdf` (parsed text only).
- **Repositories** — save the README, or a written summary of what you read.

Ingest classifies by extension, moves each file into its typed folder, and
records its hash. Re-dropping the same bytes is a no-op.

## What gets compiled

Each document becomes `kb/research/sources/<slug>.md` — a summary standing in
for the document, tagged by topic, linking to the concepts it is about. Each
concept becomes `kb/concepts/<slug>.md`, shared with every project that
references it.

## What does not happen

Research documents are never passed to the project signal detectors. A paper
is not a delivery risk, and `_discover_projects` looks for `project.notes.md`,
which is deliberately not here.
