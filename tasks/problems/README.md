# Contest problems (ICFP 2026, littleman)

Every released problem, fetched from the public API and saved so we never need
the site again:

```sh
curl https://icfpcontest2026.com/api/v1/public/problems              # -> _index.json
curl https://icfpcontest2026.com/api/v1/public/problems/<slug>       # -> <slug>.json
```

Each `<slug>.json` carries `description`, `io` (grammar + constraints),
`scoring`, `tickCap`, `privateTestCount`, and `publicTestData` — the same public
cases the editor runs. Private cases are never served. `_index.json` is the
listing, and holds the `problemId` needed for submissions (`id`).

Rules and scoring: [`../../littleman/GRADING.md`](../../littleman/GRADING.md).
Language: [`../../littleman/SPEC.md`](../../littleman/SPEC.md).

| Set | Slug | Name | Scoring | Public cases | Private | Display | Summary |
|---|---|---|---|---|---|---|---|
| Semester 1 | `memory` | Memory | footprint-tick | 7 | 0 |  | Simulate a 100-cell memory. |
| Semester 1 | `reverse-a-list` | Reverse a List | footprint-tick | 8 | 0 |  | Read a list of integers and print the same list in reverse order. |
| Semester 1 | `sort-numbers` | Sort | footprint-tick | 7 | 0 |  | Read a list of integers and print the same list sorted into ascending order. |
| Semester 1 | `triangle` | Triangle | footprint-tick | 6 | 0 |  | Output the *n*-th triangular number. |
| Semester 2 | `brackets` | Brackets | footprint-tick | 9 | 0 |  | Read a string of bracket characters and report whether it is balanced. |
| Semester 2 | `history-lesson` | History Lesson | footprint | 1 | 0 |  | This problem has no input. Your program must output an |
| Semester 2 | `plotter` | Plotter | footprint-tick | 6 | 0 | 32x24 | Graph line segments on a display. |
| Semester 2 | `tcp` | Packet Reassembly | footprint-tick | 6 | 0 |  | Reassemble a stream of packets. |
| Semester 3 | `gradebook` | Grade Book | footprint-tick | 7 | 0 |  | Process operations over student grades across several subjects. |
| Semester 3 | `matmul` | Matrix Multiply | footprint-tick | 7 | 0 |  | Multiply two matrices. |
| Semester 3 | `subset-sum` | Subset Sum | footprint-tick | 7 | 0 |  | Find a set of integers in a list that sum to a target number. |
| Semester 3 | `sudoku-validity` | Sudoku Auditor | footprint-tick | 6 | 0 |  | Validate a Sudoku solution. |
| Semester 4 | `snake` | Snake | footprint-tick | 5 | 0 | 16x16 | Simulate a game of Snake and draw it on a display. |
| Semester 4 | `pathfinder` | Pathfinder | footprint-tick | 7 | 0 | 16x16 | Guide a robot through a maze to a flag and draw its path. |
| Semester 4 | `little-little-man` | LLM | footprint-tick | 14 | 0 | 16x16 | Interpret an LLM program and show its state on a display. |
| Semester 4 | `little-little-little-man` | LLLM | footprint-tick | 10 | 0 | 16x16 | Interpret an LLLM program and show its state on a display. |
| Practice Problems (Ungraded) | `atoi` | atoi | footprint-tick | 2 | 0 |  | Read a string of ASCII digits and output the integer it denotes. |
| Practice Problems (Ungraded) | `hello-world` | Hello World | footprint-tick | 1 | 0 |  | Output the eleven bytes of `hello world` (lowercase, a single space, no |
| Practice Problems (Ungraded) | `max-element` | Max Element | footprint-tick | 10 | 0 |  | Output the largest number in a list. |
| Practice Problems (Ungraded) | `palette` | Palette | footprint-tick | 1 | 0 | 8x8 | Show all sixteen palette colors on the display. |

Semesters 1–3 all have `tickCap: null`, i.e. the default 5,000,000-step cap.
Semester 4 raises it: 15,000,000 for `snake`, `pathfinder` and
`little-little-little-man`, and 50,000,000 for `little-little-man`. Every Semester 4
problem is display-judged at 16x16 and carries an `uberStrict: false` flag the earlier
sets do not have. `status: practice` problems are ungraded and reject submissions.

**`privateTestCount: 0` is not a promise.** `snake` reports 0 and the judge graded
**17** cases against its 5 public ones, and they are dearer than the public set
(avgTicks 1,000,411 against the 640,777 the public cases measure locally). Size
hardware to the *constraint box*, never to the public data — the same lesson
`gradebook` taught, now with a number attached.
