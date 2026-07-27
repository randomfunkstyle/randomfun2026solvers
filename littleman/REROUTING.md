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

## What it will not do yet

Node cells never move and their entry directions never change. Both are
loosenable and both are worth more than what is already there:

* **Entry direction is free** for every glyph except literals and `U` — the
  effect does not depend on the direction walked, and the arms of a conditional
  turn rotate with it because `-1/0/+1` are *relative*. Freeing it turns each
  node into a 4-way choice and would let corridors approach from whichever side
  is nearest. It needs the signature check upgraded from equality to
  isomorphism (canonical BFS numbering from the starts).
* **Node placement.** Moving a node changes `s`/`r` nearest-pipe binding, so it
  needs `optimize.bindings_preserved` in the gate — see `ARCH.md` §7.1.
