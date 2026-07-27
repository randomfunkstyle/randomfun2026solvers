# Where the remaining points are

Snapshot 2026-07-27. Regenerate with `scratch/headroom.py` (standings) and
`scratch/corridor_sweep.py` (reroute opportunity).

## How the score actually works

From `littleman/reference/grading.txt`, 2 points per graded problem:

```
test-case points = passing cases / total cases
ranking points   = (other eligible teams you rank above OR TIE) / (other eligible teams)
```

**Ties count in your favour.** On triangle, 140 of 268 teams are tied at 832 and
nobody is below it — all of them score `rankPoints: 1, points: 2`. There is
nothing to win by breaking that tie.

The standings API returns `rank`, `passPoints`, `rankPoints`, `points` per row.
**Read those fields.** Recomputing rank from the sorted score list is how one
session convinced itself triangle was worth half a rank point when it was worth
zero.

We hold **28.60 / 32** on graded problems with pass points maxed everywhere, so
every remaining point is rank, and rank points are *continuous* in the fraction
of the field beaten — value scales with how many teams an improvement overtakes,
not with the percentage gain. Check the density of the field near our score.

## Read this before optimising a problem

Confirm some local grid **reproduces the live score** first.

Two submit cycles went into matmul before establishing that our live
197,437,831 comes from a grid in no worktree and no archive. The best archived
was 339,567,480; `matmul_hand` judges at 220,091,697. The work was real (−35% on
the best *reproducible* grid) and bought zero rank points.

Two traps inside that one:

* `tasks/solutions/` **differs per worktree**. `matmul_hand`, our best
  reproducible matmul, exists only in `.claude/worktrees/sort-numbers-ring`.
  `find <repo> -name '*.man'` across all worktrees before deciding a lineage.
* Do **not** derive a local→judged ratio by dividing the live score by one
  grid's local score and then confirming it by multiplying back. That is
  circular. Ratios only hold *within* a lineage — across lineages the private
  cases shift them. Comparing before/after of the *same* grid is sound, because
  footprint and private cases are identical and the ratio cancels.

## Headroom per problem

| problem | headroom | teams ahead | notes |
|---|---:|---:|---|
| matmul | 0.46 | 48.3% | live grid **lost**; best reproducible now 220,091,697 |
| subset-sum | 0.33 | 38.8% | |
| brackets | 0.29 | 31.2% | |
| sudoku-validity | 0.28 | 31.9% | |
| sort-numbers | 0.27 | 29.0% | |
| plotter | 0.27 | 27.7% | |
| gradebook | 0.26 | 27.0% | |
| snake | 0.25 | 24.3% | worked this session, 99.1M → 87.6M |
| little-little-little-man | 0.23 | 22.2% | |
| memory | 0.19 | 20.8% | |
| reverse-a-list | 0.17 | 17.0% | |
| tcp | 0.15 | 16.5% | |
| history-lesson | 0.15 | 15.5% | footprint-only scoring — ticks do not count |
| pathfinder | 0.10 | 11.1% | |
| triangle | 0.00 | — | maxed (140-way tie at best score) |
| little-little-man | 0.00 | — | maxed |

## Corridor opportunity per grid

`corr%` is the pacing man's corridor as a share of wall ticks — the reroute
ceiling. `manticks` is what `manreroute` predicts it can remove. **These are
per-grid; a bloated grid has more slack and is still a worse starting point.**

| problem | grid | corr% | moves | cells | man-ticks |
|---|---|---:|---:|---:|---:|
| little-little-little-man | `_ring` | 85.0 | 91 | 4014 | **720,774** |
| snake | `_cpu` | 32.9 | 35 | 696 | 287,412 |
| gradebook | `_cpu` | 42.8 | 16 | 376 | 194,644 |
| subset-sum | `_mitm` | 48.1 | 37 | 650 | 186,032 |
| subset-sum | `_split` | 26.7 | 43 | 624 | 181,326 |
| plotter | `_cpu` | 63.5 | 22 | 398 | 143,778 |
| sudoku-validity | `_cpu` | 41.7 | 12 | 56 | 129,890 |
| matmul | `_cpu` | 44.0 | 21 | 228 | 102,756 |
| tcp | `_cpu` | 52.8 | 14 | 238 | 46,558 |
| pathfinder | `_grid` | 77.9 | 29 | 202 | 44,804 |
| brackets | `_cpu` | 61.8 | 16 | 198 | 38,310 |
| snake | `_ring` | 84.1 | 73 | 980 | 21,362 |
| history-lesson | `_base128` | 66.9 | 2 | 16 | 20,374 |
| tcp | `_queue_ast` | 88.7 | 7 | 126 | 10,406 |

Grids with nothing left: `brackets_stack`, `brackets_embedded`,
`sudoku-validity_fold`, `sort-numbers_network`, `reverse-a-list_ring`, and
`snake_reroute` (6 cells — this session already squeezed it).

Timed out at 90s in the pure-Python tracer: `subset-sum_scan_probe`,
`plotter_painter_probe`, `snake_panel_probe`, `pathfinder_cpu`.

## Unexplored: reroute then compact

Score is `area² × avg_ticks`, so **area is squared** — a 1% smaller box is worth
about 2% of score, usually more than a 1% tick cut. `manreroute` frees corridor
cells (650 on `subset-sum_mitm`, 4014 on `lllm_ring`) but never shrinks the
bounding box. `mancompact` already does dead row/column elimination. Nobody has
chained them, and freed corridor is exactly what makes a row droppable.

This is probably worth more than any remaining routing work, and it is the one
thing that helps `history-lesson`, which is scored on footprint alone.
