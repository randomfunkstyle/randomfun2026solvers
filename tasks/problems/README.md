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
| Semester 4 | `little-little-little-man` | LLLM | footprint-tick | 10 | 0 | 16x16 | Interpret an LLLM program and show its state on a display. |
| Semester 4 | `little-little-man` | LLM | footprint-tick | 14 | 0 | 16x16 | Interpret an LLM program and show its state on a display. |
| Semester 4 | `pathfinder` | Pathfinder | footprint-tick | 7 | 0 | 16x16 | Guide a robot through a maze to find a flag and draw the robot's path on a display. |
| Semester 4 | `snake` | Snake | footprint-tick | 5 | 0 | 16x16 | Simulate a game of Snake and draw it on a display. |
| Practice Problems (Ungraded) | `atoi` | atoi | footprint-tick | 2 | 0 |  | Read a string of ASCII digits and output the integer it denotes. |
| Practice Problems (Ungraded) | `hello-world` | Hello World | footprint-tick | 1 | 0 |  | Output the eleven bytes of `hello world` (lowercase, a single space, no |
| Practice Problems (Ungraded) | `max-element` | Max Element | footprint-tick | 10 | 0 |  | Output the largest number in a list. |
| Practice Problems (Ungraded) | `palette` | Palette | footprint-tick | 1 | 0 | 8x8 | Show all sixteen palette colors on the display. |

Semesters 1–3 and the practice problems have `tickCap: null`, i.e. the default
5,000,000-step cap. Semester 4 raises it: `little-little-man` gets 50,000,000,
and `little-little-little-man`, `pathfinder` and `snake` get 15,000,000 each.
`status: practice` problems are ungraded and reject submissions.

Semester 4 also added an `uberStrict` field to the per-problem response
(`false` on every problem so far, and undocumented in the API reference). The
four Semester 4 files carry it; the sixteen older files predate it and do not.

**`privateTestCount: 0` is not a promise.** `snake` reports 0 and the judge graded
**17** cases against its 5 public ones, `pathfinder` reports 0 and was graded on **18**
against its 7, and the hidden ones are dearer than the public
set (avgTicks 1,000,411 against the 640,777 the same grid measures locally, i.e. 1.6x).
Size hardware to the *constraint box*, never to the public data — the same lesson
`gradebook` taught, now with a number attached.

## Rival scores seen on the leaderboard (2026-07-25)

Not from the API — the API never serves other teams' submissions. These were read
off the site and are worth keeping because they say how much headroom each problem
still has. Lower is better; `max(w,h)² × avg ticks`.

| Slug | Best rival scores | Implied footprint (score ÷ tick cap) | Our best |
|---|---|---|---|
| `snake` | 200,000,000 · 2,500,000,000 | ≥ 13² at 15M ticks | **3,369,020,288** (17/17) |
| `pathfinder` | 50,000,000,000 · 300,000,000,000 | ≥ 58² at 15M ticks | **11,096,155,486** (18/18, bespoke 84×175) |
| `little-little-little-man` | 1,300,000,000,000 | ≥ 295² at 15M ticks | — |
| `little-little-man` | not seen yet | — | — |
| `subset-sum` | 448,000,000 | ≥ 10² at 5M ticks | — (blocked) |

The implied-footprint column is the useful part: a score divided by the tick cap
is a hard lower bound on the leader's `area2`, because avg ticks cannot exceed the
cap. `little-little-little-man`'s 1.3T therefore means the best team is running a
program at least ~295 cells on a side — nobody has a compact interpreter yet, on
by far the simplest of the four. That is the largest open gap in the contest.

`snake`'s 200M leader implies a grid no bigger than ~13 cells on a side at the cap, or
a bigger one that is much faster; ours is 121x136 = 18,496 at 182k ticks, so the gap is
footprint, not speed. A bespoke machine — no ISA, no ROM, a few men around the panel —
is the only thing that closes it (§1's trade, and `plotter_block` is the precedent).

## `pathfinder`: solved bespoke at 11.1bn; the LM-1 build stalls at 17/18

The problem is **solved** — `tasks/solutions/pathfinder_grid.man`, a dedicated dataflow
machine, passes 18/18 at 84×175 for a score of **11,096,155,486**, which beats the best
rival score seen (50bn) by 4.5x. What follows is about the *LM-1* build, kept because it
prices the tier: it passes 17 of 18 and therefore scores nothing.

The first submission passes **17 of the 18** cases the judge runs, which scores nothing — a
full pass is required — but it pins the remaining work exactly. Measured on the engine over
the seven public cases, cost is linear in the number of moves:

```
ticks = 1,680,572 + 61,159 x (total moves in the case)      max residual 248k
```

so the 15,000,000 cap is reached at **~218 total moves**. The public cases run 22-89 moves
(worst 7.15M ticks, a 2.1x margin), and the spec bounds each path at 64 moves but places
**no bound on the number of rounds** — so a case of ~10 long rounds exceeds the cap, and
that is almost certainly the 18th. This is a *ticks* failure, not a correctness one: all
seven public cases match frame for frame on the reference engine.

Two things worth keeping. The per-move figure is the whole target — it needs to come down
~2x for headroom, not a few percent. And `privateTestCount: 0` has now lied on three
problems (`gradebook`, `snake`, `pathfinder`); here it cost a score rather than a scare.
