# Collaboration log

Fork-only notes (branch `test-all-prs`), not part of any upstream PR. Counts and one-line
summaries, never prompt text.

## Steering tally

Little things the maintainer asked for or caught, what the status page shows. Estimated on
2026-11-03 from the Claude Code sessions on the workstation and a summary of the first cloud thread.
Still a lower bound.

| Bugs found | UI tweaks | New features | Direction | Total |
|---|---|---|---|---|
| 10 | 8 | 13 | 13 | 44 |

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
- 2026-10-21 [scope][feature]: an export option to strip names and metadata, for packing sensitive parts.
- 2026-10-21 [decide][direction]: commits under the maintainer's real email on all branches.
- 2026-10-21 [decide][direction]: finish first and submit once, in the fork until then.
- 2026-10-21 [decide][direction]: PRs carry only what's relevant upstream.
- 2026-10-19 [tested][bug]: Netfabb color groups imported invisible (alpha 00).
- 2026-10-19 [tested][bug]: texture tile styles mapped wrongly.
- 2026-10-19 [scope][feature]: read and write layered, transparent textures (later).
- 2026-10-19 [domain][direction]: export recommendations per printer: 580 color, 5600, Dual Tone.
- 2026-10-14 [tested][bug]: Netfabb files with instanced parts imported stacked or misplaced.
- 2026-10-14 [tested][ui]: background import felt laggier than a frozen window with a progress bar.
- 2026-10-14 [scope][feature]: try importing in the background.
- 2026-10-14 [decide][direction]: an optional upstream docs PR with interoperability facts.
- 2026-10-13 [tested][bug]: Netfabb rejected an Orca-mode export: ill-formed UUID.
- 2026-10-13 [decide][feature]: a flat shading option on import.
- 2026-10-13 [tested][feature]: cheap performance wins for large buckets.
- 2026-10-13 [tested][ui]: "Center on Origin" also drops to the bed: relabel it.
- 2026-10-13 [decide][ui]: preferences laid out like the import and export dialogs.
- 2026-10-12 [tested][bug]: the thumbnail path's browse button errored.
- 2026-10-12 [tested][bug]: the automatic thumbnail produced nothing.
- 2026-10-12 [tested][bug]: a thumbnail relationship with no thumbnail made Netfabb reject the file.
- 2026-10-12 [tested][ui]: import and export shared one scale default.
- 2026-10-12 [tested][feature]: export always centered parts, wrong for buckets: an Export Location option.
- 2026-10-12 [decide][feature]: indented model XML, for editing by hand.
- 2026-10-12 [decide][feature]: preference defaults for the remaining export options.
- 2026-10-12 [scope][direction]: small, localized PRs in upstream's style.
- 2026-10-12 [decide][direction]: best practice first, upstream's convention when equal.
- 2026-10-12 [decide][direction]: the importer stays lenient, never stricter.
- 2026-10-12 [scope][direction]: make it work for Netfabb and MJF workflows.
