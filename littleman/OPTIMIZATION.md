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
  --moves squash --problem SLUG --rounds 100 \
  --out tasks/compacted/SLUG_squashed.man
```

`--moves cuts` limits the search to row/column deletion. `--moves all` combines
cuts, room placement, declared-pipe rerouting, and loop squashing. The default
remains the original layout search.

The optimizer refuses a move when the grid stops loading, a send/receive binds
to another pipe, a public case fails, or the measured
`max(width,height)² × averageTicks` objective does not improve.

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
