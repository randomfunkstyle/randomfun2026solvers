# Raw official reference text

Mechanically extracted string literals from the contest site's compiled
JavaScript bundles (`icfpcontest2026.com/assets/*.js`), saved so we never need
the site again. These are **verbatim but unformatted** — the site is a SPA, so
prose, code labels and grid art land interleaved and some single-character glyph
labels sit on their own lines.

Read [`../SPEC.md`](../SPEC.md) and [`../GRADING.md`](../GRADING.md) first — they
are the cleaned-up, cross-checked versions. Come here only to confirm exact
official wording.

| File | Source page | Contents |
|---|---|---|
| `language-reference.txt` | `/language-reference` | machine model, full instruction set, pipes, pipe targeting, I/O rooms, LM-75 display, judging, fine print |
| `grading.txt` | `/grading` | test cases, submitting, program scoring, ranking/points, rounds, display judging, ASCII, limits |
| `contest-rules.txt` | `/rules` | official contest rules |
| `api.txt` | `/api-help` | submission API: endpoints, result fields, error codes |
| `textbook.txt` | `/textbook` | tutorial prose plus a pile of worked example grids (arithmetic, backpack loops, pipe rings, multi-room fan-out) — good source of idioms |
| `interpreter-probe.txt` | — | our own empirical probe: every glyph in `validOps` run against four preset (A, B, BP) states through `littleman.wasm`, showing the exact state delta. This is what verifies `SPEC.md`, and how `Y` (split) was identified. |

Problem statements and public test data live in
[`../../tasks/problems/`](../../tasks/problems/).
