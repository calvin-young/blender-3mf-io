# Collaboration log

Fork-only notes (branch `test-all-prs`), not part of any upstream PR. Counts and one-line
summaries, never prompt text.

## Steering tally

Little things the maintainer asked for or caught, what the status page shows. Estimated on
2026-11-03 from the Claude Code sessions on the workstation. Earlier work in cloud chats can't be read
from here and isn't counted, so it's a lower bound.

| Bugs found | UI tweaks | New features | Direction | Total |
|---|---|---|---|---|
| 3 | 4 | 5 | 6 | 18 |

## Steering

One line for each thing, newest first, tagged with its kind and category.

- 2026-10-29 [decide][ui]: round trip with default settings keeps the same part names in Blender.
- 2026-10-29 [decide][feature]: batch export naming like the STL exporter's.
- 2026-10-29 [decide][feature]: a toggle to keep a single 3MF as one part on import.
- 2026-10-29 [decide][feature]: part naming as a dropdown: filename, part, mesh.
- 2026-10-29 [decide][feature]: importer option to name parts after the file.
- 2026-10-29 [tested][bug]: names defaulted to the blend file's.
- 2026-10-29 [tested][ui]: export defaults to the part's name.
- 2026-10-29 [tested][ui]: the import progress bar jumps and sticks at 5%.
- 2026-10-29 [tested][bug]: importing several files by drag and drop gave them all one texture.
- 2026-10-29 [tested][bug]: imports took the wrong name.
- 2026-10-29 [decide][ui]: thumbnails in the test catalog's table.
- 2026-10-29 [scope][feature]: a public catalog of non-sensitive test parts and buckets.
- 2026-10-29 [decide][direction]: record exporter settings and versions for traceability.
- 2026-10-27 [scope][direction]: test against all the local parts and buckets, and find blind spots.
- 2026-10-27 [scope][direction]: benchmark against other software packages.
- 2026-10-27 [decide][direction]: real files stay private; a public suite must be synthetic.
- 2026-10-21 [decide][direction]: finish first and submit once, in the fork until then.
- 2026-10-21 [decide][direction]: PRs carry only what's relevant upstream.
