# Deterministic AST optimization

The optimizer must mutate the parsed AST, never individual public examples and
never hand-picked ASCII coordinates. Public cases are an acceptance gate for a
candidate; they are not a source of per-case patches.

## One audit command

```sh
uv run python -m randomfun2026solvers.manaudit \
  solutions/memory/000000055105622_memory.man --slug memory
```

The audit:

1. round-trips the refined AST byte-for-byte;
2. reconstructs the archived server-score frontier;
3. separates each score gain into footprint and implied ticks;
4. reports rooms, pipes, opaque AST bodies, free lines, loop slack, reroute
   candidates, and foldable line extents;
5. prints ranked commands for deterministic AST moves.

Machine-readable output is available with `--json`.

For the full geometric explanation behind a recommendation:

```sh
uv run python -m randomfun2026solvers.manfree GRID.man --refine 1
```

## Deterministic optimizer commands

Safe dead-line pass, including public-case validation:

```sh
uv run python -m randomfun2026solvers.mancompact GRID.man \
  --problem SLUG --out tasks/compacted/SLUG_deadlines.man
```

AST placement/reroute search, validating every accepted move against the whole
problem:

```sh
uv run python -m randomfun2026solvers.manopt GRID.man \
  --moves layout --problem SLUG --rounds 100 \
  --pipe-min PIPE_ID=MINIMUM \
  --out tasks/compacted/SLUG_ast.man
```

AST loop-squash search:

```sh
uv run python -m randomfun2026solvers.manopt GRID.man \
  --moves all --infer-capacity --problem SLUG --rounds 10 \
  --out tasks/compacted/SLUG_squashed.man
```

`--moves cuts` limits the search to row/column deletion. `--moves all` combines
cuts, room placement, declared-pipe rerouting, and loop squashing. The default
remains the original layout search.

The optimizer refuses a move when the grid stops loading, a send/receive binds
to another pipe, a public case fails, or the measured
`max(width,height)² × averageTicks` objective does not improve.

The default semantic shortlist contains only moves that first reduce the
footprint factor or bounding-box area. Use `--speed-for-space` to also measure
non-compacting moves whose tick reduction might pay for their geometry. That is
an intentionally separate, much larger search rather than a cost silently paid
by every compaction run.

## What previous submissions teach

The archive filename supplies the server score. Dividing it by the grid's
`max(width,height)²` factor recovers the server's implied average ticks. This
lets the audit classify improvements without relying on missing or subjective
notes.

Representative best-solution histories:

| Problem | Repeated pattern |
|---|---|
| `memory` | topology changes and footprint cuts often traded against pipe phase; the final 31×31 improvement came from execution shortening at unchanged footprint |
| `plotter` | the long improvement sequence combined binding-axis compaction with shorter execution paths; late equal-footprint submissions were tick wins |
| `tcp` | one execution-path reduction followed by two almost pure one-column footprint cuts |
| `sudoku-validity` | the final two meaningful gains were almost pure footprint compaction; implied ticks stayed constant |

These histories produce the following rules.

### Rule 1 — optimize the product, not area or ticks alone

For `footprint-tick`, compare:

```text
objective = max(width, height)² × average ticks
```

A short-axis deletion may create useful routing space, but it has zero immediate
footprint value. A larger grid is acceptable only when its measured tick
reduction more than pays for the squared side increase.

### Rule 2 — the binding axis gets first claim

The longest side sets the footprint factor. Rank removable lines, single-pipe
reroutes, and line folds on that axis first. Once both sides balance, recompute;
the binding axis can change after every move.

### Rule 3 — pipe capacity is a contract, not whitespace

Do not infer a pipe minimum from its visual length.

- A conduit usually carries latency and may be shortened, subject to cases.
- A display/output feed's terminal cell selects a port and is semantic.
- A recirculating ring's *group total* is capacity; its individual legs may
  trade cells, but the total may not fall below the declared requirement.

Missing minima are reported before reroute recommendations. A room-graph cycle
is a conservative warning, not proof that every pipe in that cycle is storage.
When control acknowledgements and tape data share one strongly connected room
graph, annotate the actual ring groups explicitly.

### Rule 4 — preserve nearest-pipe bindings after every AST move

Moving a whole room preserves its internal walk but can silently change which
pipe an `r` or `s` selects. Re-run the binding signature before the expensive
problem cases. A grid that still loads is not sufficient evidence.

### Rule 5 — interior slack is a tick candidate

An empty room row/column may not shrink the global box, yet a loop that coasts
over it pays every lap. `manfree` reports both the removable line and ticks per
lap; `manopt --moves squash --problem …` measures the end-to-end result and
rejects neutral or regressing candidates.

### Rule 6 — two occupied lines may share geometry

A packed grid can have no removable line while two lines occupy disjoint
extents. Folding them is a placement move, not deletion. Rank exact disjoint
pairs first, then the smallest overlaps that can become disjoint after moving a
rigid block.

### Rule 7 — archived score order is a quality frontier

Archive filenames contain scores, not reliable timestamps. Audit output is
ordered worst-to-best and describes a score frontier; it does not claim that
every neighboring pair was submitted consecutively.

### Rule 8 — one acceptance policy for every input

Keep a generated candidate only when all of these hold:

1. AST rendering succeeds;
2. topology and pipe bindings are preserved;
3. declared group capacities are preserved;
4. every public case passes;
5. the complete measured objective improves.

This policy prevents example-specific tuning and makes a failed optimization a
useful structural result rather than an invitation to patch one case.

## Current audit result

The refined AST round-trips all six current best archived submission families
exactly: `memory`, `plotter`, `brackets`, `tcp`, `sudoku-validity`, and
`gradebook`. Most grids pass the fast unbound structural parse; display/feed
layouts fall back to the pipe-bound parser when that first rendering differs.

Two initial deterministic attempts were intentionally rejected:

- `memory`: the reported loop-squash candidates either changed six pipe
  bindings or produced no objective improvement;
- compacted banked `memory`: all three remaining dispatcher-row squashes changed
  five pipe bindings, so the 100×54 artifact is terminal for this move family;
- `plotter`: removing the remaining single-feed row changed two bindings.

Those are useful terminal results for those move families. The next work should
come from explicit line folding or a generator-level topology rewrite, not
coordinate-level tweaking.

## Submitted-solution AST10 campaign

The 2026-07-25 campaign deliberately covers only the best accepted file in each
`solutions/` archive. Raw CPU drafts, probes, `tasks/compacted` experiments,
historical superseded submissions, and `unscored_*.man` are outside its scope.
The accepted frontier is refreshed from `origin/main` after every improvement,
then families are processed by archived score, largest first. Each run has a
10-round budget and stops early when the complete deterministic neighborhood
contains no improving move.

```sh
uv run python -m randomfun2026solvers.manopt \
  solutions/SLUG/BEST_SLUG.man \
  --moves all --infer-capacity --problem SLUG --rounds 10 \
  --out tasks/compacted/SLUG_submitted_ast10.man
```

| Order | Submitted family | Best submitted source | Effective rounds | Result |
|---:|---|---:|---:|---|
| 1 | `gradebook` | 114×101, score 10,082,933,604 | 2 | removed row 74; 114×100, public objective 3,912,488,501.14 → 3,907,925,048.57 |
| 2 | `snake` | 102×102, score 1,816,016,976 | AST2 + ROM2 | removed row 46, then ROM 8→9 rows; 102×102, public objective 1,219,369,608 → 1,196,917,776 |
| 3 | `sudoku-validity` | 83×80, score 3,043,333,207 | 1 | fixed point; internal cuts rebound 24 ops and the behavior-preserving bottom cut had zero objective value |
| 4 | `tcp` | 109×74, score 1,678,313,030 | 2 | removed row 47; 109×73, public objective 1,051,587,310 → 1,050,755,640 |
| 5 | `matmul` | 88×90, score 1,464,201,360 | 1 | fixed point; the only compacting row cuts rebound 48 ops |
| 6 | `brackets` | 95×69, score 444,583,302 | ROM7 + AST3 | ROM 7→13 rows, then removed column 40 and row 56; 94×74, public objective 230,976,825 → 225,289,528.44 |
| 7 | `memory` | 31×31, score 55,105,622 | 1 | fixed point with the inferred 121-cell ring total held exactly |
| 8 | `plotter` | 44×56, score 22,774,730 | 1 | fixed point; the feed-row cut rebound 2 ops and room moves rebound 37 |

The verified candidates are
`tasks/compacted/gradebook_submitted_ast10.man` and
`tasks/compacted/snake_submitted_ast10.man`, and
`tasks/compacted/tcp_submitted_ast10.man`, and
`tasks/compacted/brackets_submitted_ast10.man`. Gradebook average ticks improve
from 301,053.28571428574 to 300,702.14285714284 at the same 114² footprint
(objective −0.12%). Composing AST and ROM passes on the current snake submission
improves from 117,202 to 115,044 average ticks at the same 102² footprint
(objective −1.84%); see `tasks/compacted/snake_submitted_ast_rom10.man`. TCP
average ticks improve from 88,510 to 88,440 at the same 109² footprint
(objective −0.079%). Brackets shrinks its binding side and lowers average ticks
from 25,593 to 25,510.777777777777 (objective −2.41%). These candidates derive
from the current best submitted solutions; they are not themselves claimed to
be submitted.

## ROM-shape optimization

More ROM rows make a CPU ROM narrower and taller; fewer make it wider and
shorter. `lm1.romopt` deterministically tries both neighboring folds in
binding-axis order, validates every public case, and repeats for up to 10
rounds. `--handmade-top-rom` replaces only a generator-identical top ROM room
and shifts the unchanged hand-made suffix.

For `littleman/examples/snake-handmade.man`, 8 ROM rows are the local optimum:
121×100 becomes 102×102 and the measured public objective falls from
1,738,577,755.20 to 1,219,369,608.00. The lower 90-line hand-made suffix is
otherwise byte-identical.

ROM10 was also run against every current best submitted generator-backed CPU
solution. Gradebook (31 rows), sudoku-validity (23), snake (8), and matmul (5)
are strict neighboring-fold optima. TCP has a neutral 3–5-row plateau. Brackets
can spend otherwise free height: 7→13 ROM rows keeps the grid width and 95²
factor unchanged while reducing public average ticks from 25,593 to 25,579
(objective 230,976,825 → 230,850,475). The verified standalone result is
`tasks/compacted/brackets_submitted_rom10.man`. Applying AST10 afterward removes
one binding-side column and one execution row; the composed
`tasks/compacted/brackets_submitted_rom_ast10.man` reaches 94×74, 25,496.78
average ticks, and objective 225,289,528.44.

## STORE backend replacement

`lm1.storeopt` makes memory replacement a deterministic AST seam operation:

```sh
uv run python -m randomfun2026solvers.lm1.storeopt BEST.man \
  --program SLUG --store men-y --out tasks/compacted/SLUG_men_y.man
```

It locates the registered memory rooms by translation-invariant room signatures,
records the request/response boundary pipe cells, headings, and outside room
roles, and can return an AST with the old store rooms plus all attached routes
removed. `machine.build_for(..., store="men" | "men-y")` places the replacement
and routes both seams; `storeopt` then asserts the new AST retains one incoming
and one outgoing compute-room attachment, validates all public cases, and writes
only a strict objective improvement. Exact CPU room dimensions are deliberately
not seam identity: legal `mem_pad` placement can widen that room. A hand-packed
source is accepted when its memory room group is a rigidly moved copy of the
registered drop-in block.

`men` is the one-line backend. `men-y` rounds the slot count up to two equal
banks, uses a `Y` selector, and changes the adapter to emit
`addr op [value]`; this lets it omit the standalone memory program's serialized
head because a CPU load already blocks waiting for its response. The current
best-submission sweep found no man-memory winner:

| CPU program | Slots | Line backend | `Y` backend: shape; average ticks; objective change |
|---|---:|---|---|
| `brackets` | 5 | valid, objective +29.0% | 101×81; 30,922.44 (+20.82% ticks); **+36.57%** |
| `snake-ring` | 9 | valid, objective +131.2% | 129×136; 147,743 (+26.06% ticks); **+124.10%** |
| `matmul` | 16 | valid, objective +272.4% | 130×90; 150,473.86 (+25.28% ticks); **+161.39%** |
| `sudoku-validity` | 31 | unavailable above 24 cells | 167×97; 463,293 (+6.68% ticks); **+331.90%** |
| `gradebook` | 32 | unavailable above 24 cells | 198×105; 351,246.71 (+16.67% ticks); **+251.96%** |
| `tcp` | 52 | unavailable above 24 cells | 253×78; 81,920.50 (**−7.44% ticks**); **+398.64%** |

All six `Y` grids pass every public case, including their writes and later reads.
TCP proves the parallel banks can win on CPU time, but its 253-column leaf-bank
layout overwhelms that gain under squared-footprint scoring. Because no backend
improves its best submitted source, no man-memory candidate is retained and no
follow-up AST10/ROM10 pass is warranted.
