# Corridor rerouting

A deterministic post-optimiser that makes a grid faster **without knowing what it
computes**. It only shortens the distance a man walks between instructions.

It found 5.8% of snake's ticks in four verification runs:
`99,133,632 -> 93,389,150` judged, 17/17 cases.

## Why this is safe to do blind

The language splits cleanly in two:

| glyphs | what they do |
|---|---|
| `' '` `'.'` `'>'` `'<'` `'^'` `'v'` `'V'` | **corridor** — carry a man somewhere. Touch no hand, no backpack, no pipe. |
| everything else | **nodes** — the actual program. Effect does not depend on the direction walked.[^1] |

So a program *is* a graph: which instruction each man runs next, and for the
conditional turns (`X` `a` `d` `x`) which arm he takes. The corridor between two
nodes is free real estate — and since one corridor cell costs exactly one tick,
a shorter corridor is a faster program that means the same thing.

[^1]: Two exceptions, both pinned by the tools: a numeric literal reads backwards
when walked backwards, and `U` turns to an absolute room side.

## The three modules

### `manflow.py` — the graph
`build_flow_graph(program) -> FlowGraph`. Symbolically walks every reachable
`(cell, direction)` state from each `@`, expanding **all** arms of every
conditional turn — not a trace, so an arm the program never takes is still in the
graph and still has to stay walkable. Nodes carry their cell, glyph and entry
direction; edges carry the corridor cells between them, and `len(cells)` is the
edge's cost in ticks.

```sh
uv run python -m randomfun2026solvers.manflow GRID.man --edges
```

### `manprofile.py` — how many ticks flow through each corridor
`profile_program(program, slug) -> Profile`. Traces every public case and replays
each man's walk against the graph, so every tick lands on exactly one node or
edge. Gives `traffic` (walks) and `ticks` (`traffic x length`) per edge.

The tracing engine is the pure-Python one plus **display and frame gating**,
which the fast engine only had natively. Tick counts match the native backend
exactly — that agreement is a test (`test_manprofile.py`), and it is what makes
the display problems, where most of the corridor lives, profileable at all.

```sh
uv run python -m randomfun2026solvers.manprofile GRID.man SLUG --top 25
```

Read the **per-man** block first. Ticks are wall clock and men run in lockstep,
so the program is only as fast as its least idle man. On snake:

```
  man 2: 62982 ticks — corridor 48696 (77.3%), blocked 4676 (7.4% idle)
  man 1: 62982 ticks — corridor 3781 (6.0%), blocked 57313 (91.0% idle)
  man 0: 62982 ticks — corridor 609 (1.0%), blocked 59531 (94.5% idle)
```

Men 0 and 1 are waiting on pipes almost all the time; shortening *their* walks
buys nothing, it just makes them wait longer at the same pipe. Man 2's corridor
is the whole opportunity, and 77.3% is the honest ceiling.

### `manreroute.py` — the router
Rip-up and reroute, hottest corridor first, A* on a length limit so no corridor
ever gets longer. Two strategies, both run, better one kept:

* **incremental** — lift one corridor, better it against everything else.
* **global** — lift *every* corridor, lay them back hottest first. Roughly 2.5x
  the win on snake, because the hot corridors get the straight runs and the
  detours land on corridors nobody walks.

The one 2D rule it runs on is local: a space passes a man through unchanged so
two corridors may **cross** on one, but an arrow re-aims everyone who steps on
it, so corridors may share an arrow only if they all leave it the same way.

```sh
uv run python -m randomfun2026solvers.manreroute GRID.man SLUG --out NEW.man --rounds 3 --verbose
uv run python -m randomfun2026solvers.manreroute GRID.man SLUG --dry-run   # propose only
```

## The two gates

**Graph signature** (`graph_signature`) — re-parse the rerouted grid and require
the identical set of `(source, arm, destination)` with the same glyphs, cells and
entry directions. Catches any accidental semantic change before a case is ever
run.

**The cases.** The graph check proves a reroute *means* the same thing; it cannot
prove the program still *works*, because ticks are also a schedule:

* two men who touch both halt, so making one faster can make him collide;
* a shorter walk to a pipe is only a longer wait at it.

So every batch is run against the public cases and kept only if it passes and is
not slower. Batches start large and halve on failure — a handful of runs when the
moves are harmless, one-at-a-time exactly where the schedule is delicate. On
`reverse-a-list_split` all three proposed moves are correctly rejected: the grid
forks with `Y`, and the men collide once the timing moves.

### `manrotate.py` — free the approach direction

Entry direction is free for every glyph except two: a numeric literal reads
backwards when walked backwards, and `U` turns to an absolute room side. A
conditional turn is *not* an exception — its arms are relative, so `-1/0/+1`
keep their meaning and simply point elsewhere on the compass. Node cells never
move, so `s`/`r` nearest-pipe binding is untouched.

```sh
uv run python -m randomfun2026solvers.manrotate GRID.man SLUG --out NEW.man --verbose
```

The gate has to be `manflow.canonical_signature` here, not
`manreroute.graph_signature`: rotation changes a node's `(cell, direction)` key
by construction, so the equality check would reject every legal turn. Canonical
numbers nodes by a BFS from the men's starting cells instead. It ignores
unreachable glyphs, which is correct — only what runs is the program.

## Measured: this grid is space-limited, not direction-limited

Worth reading before investing in either remaining lever.

A relaxation bound — shortest path per edge with directions free and *no other
corridor in the way* — said 15.5% of snake's pacing man's corridor was going to
forced approach directions, 11.8% of wall ticks. The realised figure was
**0.46%**.

The bound was wrong because it ignored congestion, and congestion is the whole
story. Replanning every corridor from an empty grid with directions unchanged
comes out **2.1% worse** than what is already there. The existing layout is
already near-optimal for the space available; there is no room to route
straighter.

Two consequences:

* Do not trust a no-congestion relaxation on a dense grid. Price it against a
  from-empty replan at the same directions first — that separates "we are
  routing badly" from "there is nowhere to route".
* The remaining lever is **space**, which means node **placement**, not
  rotation. That needs `optimize.bindings_preserved` in the gate, because moving
  an `s`/`r` silently rebinds which pipe it talks to (`ARCH.md` §7.1). On a
  footprint-tick problem, simply enlarging the room is a bad trade: area is
  squared.

## One more thing the whole-grid replan taught

The from-empty replan halves `reverse-a-list_split`'s corridor (96 -> 40 cells)
with an identical signature — and the result passes **1 of 8** cases. The
program is provably unchanged and thoroughly broken, purely by schedule. This is
why rotation is packaged as per-node moves the batch search can accept or reject
individually, rather than as one replan: an all-or-nothing grid gives the gate
nothing to back off to.
