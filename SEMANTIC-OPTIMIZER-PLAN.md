# Post-optimizer for littleman `.man` programs — research + parallelizable plan

## Context

`task.md` asks for a post-optimizer for written `.man` littleman programs: parse to a
graph, run heuristics to optimize room contents (incl. inner-logic modeling + macro
rewrites), compact placement to minimize `max(w,h)`, and validate/score on the engine.

**Key research finding: ~80% of the requested machinery already exists** in
`solvers/python/randomfun2026solvers/`. The task's features 1, 3, 4 are essentially
built; feature 2's *geometric* part is built. The genuine gap is feature 2's *semantic*
part — **optimizing the contents/logic inside a room** — which no current pass does.

This plan is a **design + parallelizable work breakdown** (deliverable is this document;
no code this session). It targets that gap and reuses everything else as-is.

### Scoring objective (all work optimizes this)
`footprint-tick` mode (19/20 problems): `max(width,height)² × avgTicks`.
`footprint` mode (`history-lesson`): `max(width,height)²` only.
Footprint is squared → narrow-and-looping beats wide-and-unrolled; a tick win must
pay for any footprint growth it causes.

## What already exists — REUSE, do not rebuild

| Task feature | Status | Existing modules (in `randomfun2026solvers/`) |
|---|---|---|
| 1. Parse ASCII → room/pipe graph | **Done** | `manparse.parse_program` (engine `analyze`/`route` as oracle → `Program/Room/Pipe/PipeOp`); `manast.parse_ast` (mutable, round-tripping `Ast`; `render`, `round_trip_ok`, `bbox`, `geometry_factor`); `manstruct.analyze_structure` (per-cell `Kind` + transit table + `transparent`/`shareable`, blocks, `Freedom`); `layout.Graph` (graph↔ASCII) |
| 2. Optimize room CONTENTS (heuristics, inner-logic graph, macros) | **GAP** | only substrate exists: `manatom` gadget cost model (unused by any optimizer), `manroute.route_man`/`ManPath.ticks` (man-walk cost), `circuit.py` (intra-room man-path router) |
| 3. Compact placement, min `max(w,h)`, pipe constraints | **Done** | `manopt` (`--moves layout/cuts/squash/all`), `manmoves` (drop/squash/stretch/shift/reglyph/ring_capacity), `manroute.Plan` (parity-aware reroute, capacity floors), `manfree` (line/fold/ring/squash reports), `mancompact`, `layout` (A* + capacity router). Handles Manhattan pipe binding, min-len 2, ring-capacity contracts, display port side (Top=ADDR/Left=DATA/Bottom=SWAP) |
| 4. Run on engine, validate + score | **Done** | `littleman.Littleman` (WASM oracle: run/tick/analyze/route/judge/display_frames), `fast_littleman.FastLittleman` (fast in-memory), `scoring` (footprint, score_program), `optimize.verify`, `optimize.bindings_preserved` |

### The integration seam (host = `optimize.PASSES`, per decision)
`optimize.optimize()` is a finished greedy driver + accept-gate. A "pass" is just:
```python
PassFn    = Callable[[Program], list[Candidate]]           # Program from parse_program
Candidate = (grid: list[str], placement: dict[str,Cell] | None, label: str)  # optimize.py:~499
PASSES    = [trim_margins, relayout, relayout_keep_capacity]                  # optimize.py:631
```
Driver loop (`optimize.py:684-720`) already runs, per candidate:
`bindings_preserved` (route-oracle rebind gate) → `verify` (FastLittleman, all public
cases exact; reference WASM as diff-test) → `score_grid` → accept iff score strictly
lower; never returns a worse/failing grid.

**Consequence:** every new semantic rewrite plugs in as one more `PassFn` appended to
`PASSES`. The correctness gate, binding safety, and "never regress" guarantee are reused
unchanged — new code only needs to (a) recognize a pattern, (b) emit a rewritten grid.
A buggy recognizer, at worst, yields no improvement (gate rejects it).

## Design of the missing semantic layer

New modules in `randomfun2026solvers/`. Single-writer ownership per file (collision-free).

- **`manrules.py`** — shared schema (the contract everything agrees on):
  `RewriteRule{name, family, recognize, build→list[manatom.Gadget], preconditions,
  clobbers, cost_delta, resizes_room, mirrorable}`, `MatchSite{rule, room_id, cells,
  entry: manast.Port, exits, env}`, `CostDelta{d_cells, d_ticks_per_value}`, and an
  append-only `CATALOG: dict[family, list[RewriteRule]]` + `register()`.
  `manatom.Gadget` (name/rows/entry/exits/ticks/per_lap/count_multiple/needs/writes,
  `ticks_per_value`, `to_atom()`) is already ~80% of the RHS schema — reuse it verbatim.
- **`manlogic.py`** — inner-logic graph: `LogicNode{op, kind, cells, reads, writes,
  ins, outs}` (register names frozen to `A/B/BP/HEAD`), `InnerLogicGraph{nodes, entry}`,
  `build_logic_graph(prog, room_id)`. Built by walking `manstruct.CellInfo.exits`
  (entry-heading→exit-heading transit) chained through `manast` nodes. This is the
  single room-walker; families must NOT each invent one. Branch/`X`/`d`/`a`/`x`/`Y`/`U`
  glyphs have unknown/multi exits → model as conditional multi-successor edges.
  A small ISA effect table (glyph → needs/writes/heading-class) lives here or in a
  `mansem` helper: e.g. `+ - * / % & | ~ { }` need `{A,B}` write `{A}` (`/` writes
  `{A,B}`, rem→B); `M` A→B; `W` swaps; `b` A→BP; `m ]` BP→BP; `q` writes BP
  (source=pipe, count unknown); `s/S` need A+pipe; `r/R/U` write A+pipe.
- **`manrewrite.py`** — the pass adapter: `rule_pass(family) -> PassFn` that builds a
  logic graph per room, gathers `MatchSite`s from `CATALOG[family]`, applies each via
  `manast.Ast` node replacement (`Gadget.to_atom`) → `render` (guarded by
  `round_trip_ok`) → `Candidate`. `placement=None` for pure content rewrites; set
  `placement` when `rule.resizes_room` so `bindings_preserved` runs too.
- **`manbench.py`** — value oracle: `bench(passes, slugs=None, fast=True)` iterating
  `solutions/<slug>/<score>_<slug>.man` (8 slugs: brackets, gradebook, matmul, memory,
  plotter, snake, sudoku-validity, tcp) → `tasks/problems/<slug>.json` → `optimize()`,
  reporting per-solution objective delta + portfolio win/regression counts.
  Engine-cost control (required): FastLittleman first (`verify` default), reference WASM
  only for final confirm; cache verify per `(source,case)`; keep candidate cap low.

### Rewrite catalog (seed rules, grouped by glyph family)
Each rule states: recognition pattern, equivalent/mirrored form, cost (footprint Δ +
ticks/value from `Gadget.ticks_per_value`), clobbers, preconditions.
- **Loops/backpack** (`b m d a x ] q`): recognize `counted_loop` shape; **tall↔horizontal
  mirror** (`counted_loop`↔`counted_loop_horizontal`, equal 8 ticks/value, different
  footprint — the footprint lever, use when the loop's long side binds); **unroll**
  (`unrolled(v)`, `4v+4` ticks/lap → ~4 ticks/value floor) *only when* BP divisibility
  by `v` is provable (literal const multiple, or `x`-peeled remainder); binary decompose
  (`]`/`x`).
- **Arithmetic/hands** (`+ - * / % N M W & | ~ { }`): constant-fold, identity/strength
  reductions (`0+`, `1*`, `0|`, `NN`, `~self`), redundant `M`/`W`/nop elision — gated by
  a no-intervening-read check from the effect table.
- **Constants** (`0-9`, backtick literals): dead-literal removal, digit↔backtick
  re-encoding for footprint, shared-literal hoist. Mirror/relocate only if
  `manstruct._mirror_safe` passes (backtick loads on the CLOSING tick, reversed).
- **Heading/branch** (`> < ^ v V X`): steer coalescing / path straightening (fewer walk
  ticks), `X` sign-branch simplification, mirror-safe reflections.
- **Pipes** (`s S r R U`): capacity-preserving reshapes; coordinate with `manroute.Plan`
  / `manfree`; always `resizes_room`/`placement` so `bindings_preserved` guards.
- **Corridor/latency**: erase floor the walk doesn't need (`Corridor.rigid_content=False`)
  subject to `route_man` parity + no change to any pipe-op's nearest segment.

## Parallelizable task breakdown (the task's explicit ask)

Dependency graph:
```
S0 contracts (blocking)
  └─ S1 vertical slice (blocking gate)
       ├─ P1..P6 per-family catalogs  ── fully parallel, data-only, no cross-imports
       ├─ P7 logic-graph builder (full) ─┐
       ├─ P8 matcher/applier engine     ─┤ parallel engine work
       ├─ P9 macro rules (each 1 rule)  ─┘
       └─ P10 benchmark harness (full)   ── parallel; gates every merge
              └─ S2 integration into PASSES + portfolio benchmark (sequential, last)
```

- **S0 — interface stubs (1 agent, FIRST, serialization point).** Write `manrules.py`,
  `manlogic.py` node types, `manrewrite.rule_pass` signature, `manbench.bench` skeleton
  as type-checking stubs. Freeze `MatchSite.env` conventions, `LogicNode` register
  names, and the `family` string set. Nothing else starts until this lands.
- **S1 — vertical slice (1 agent, SECOND, de-risk gate).** Prove end-to-end on the
  cheapest rule (`loop.unroll2`: both sides already exist as `counted_loop` and
  `unrolled(2)`): real `build_logic_graph` for one room, one recognizer + precondition
  `BP%2==0`, wire `rule_pass("loop")` as a 4th `PASSES` entry, `bench` on one loop-bound
  problem (`memory` or `brackets`). **Exit criterion:** engine-verified objective
  improvement on ≥1 archived solution, or a clean no-match with correct unchanged grid.
  Only then fan out — schema churn happens while only one agent depends on it.
- **P1–P6 — per-glyph-family modeling (6 parallel agents; the "backpack" example ×6).**
  Each ships `rules_<family>.py` that `register()`s `RewriteRule`s + a
  `tests/test_rules_<family>.py` (mirror `tests/test_manatom.py`). Pure data: reads
  `manstruct`/`manlogic`/`manatom`, writes only its `CATALOG[family]` slice. Families:
  P1 loops/backpack, P2 arithmetic/hands, P3 constants, P4 heading/branch, P5 pipes,
  P6 IO/display/split. No family imports another; none touch `optimize.py`.
- **P7 — full logic-graph builder** (owner of `manlogic.py`; all room kinds, register
  inference).
- **P8 — matcher/applier engine** (owner of `manrewrite.py`; `manast` mutation + render;
  depends only on the S0 schema, iterates catalog as data).
- **P9 — macro rules** (cross-family peephole macros, each an independently assignable
  `RewriteRule`, e.g. "load-const then loop-count" → precomputed unroll).
- **P10 — benchmark harness** (owner of `manbench.py`; the value oracle; engine-cost
  discipline above). No rule is "done" without a green benchmark row.
- **S2 — integration (1 agent, LAST, sequential).** Decide `PASSES` ordering (geometric
  first, then content rewrites, then footprint-enlarging loop-tick levers so the driver
  can still trade footprint↔ticks), register each family's `rule_pass` behind an enable
  flag, run the full portfolio benchmark, accept only score-reducing merges. Do not
  loosen the strict `<` accept gate without benchmark evidence.

## Hardest correctness hazards (call out in every stream)

1. **Nearest-pipe rebind on relocation/substitution.** `s`/`r` bind to Manhattan-nearest
   *attached segment*, NEAREST not nearest-ready, ties by reading order. Any rewrite
   touching a pipe-op cell (esp. horizontal-loop recv flipping sides) must set
   `resizes_room` → `bindings_preserved` runs; a splice must re-route the feeding pipe to
   keep its segment nearest. Never skip this gate.
2. **BP divisibility for unrolling.** `unrolled(v)` over-rotates if `BP%v≠0` and no later
   check catches it — accept only when divisibility is provable; refuse `q`/unknown BP.
3. **Backtick reversal.** Literals load on the closing backtick, reversed L→R and
   vertically — mirror/relocate only when `manstruct._mirror_safe`.
4. **transparent vs shareable when moving glyphs.** Relocated glyph must land on
   `shareable` floor and not cross another lane's exclusive turn (crossing an OP executes
   it). Validate with `CellInfo.shareable`/`crossable_by` + `route_man`.
5. **Execution-order preservation.** Blocks are rigid because 4-adjacency = execution
   order; any repack must keep the man's executed-glyph sequence identical.

## Critical files (reuse points)

- `solvers/python/randomfun2026solvers/optimize.py` — `PASSES` (extension point, line 631),
  `verify`, `bindings_preserved`, `score_grid`, driver loop (684-720)
- `.../manatom.py` — `Gadget`, `LIBRARY`, `counted_loop`, `counted_loop_horizontal`,
  `unrolled` (RHS schema + substitution targets/templates)
- `.../manstruct.py` — `CellInfo` transit tables, `_build_cells`, blocks, `_mirror_safe`,
  `Freedom` (recognition + legality)
- `.../manast.py` — `Ast`, `RoomNode`/`Run`/`Joint`/`Corridor`/`Atom`, `Port`, `render`,
  `round_trip_ok` (mutation surface)
- `.../manroute.py` — `route_man`/`ManPath.ticks`, `Plan.reroute` (walk cost + pipe reroute)
- `.../scoring.py` — `footprint`, `score_program` (objective)
- `.../fast_littleman.py`, `.../littleman.py` — verify backends
- `tests/test_manatom.py` — fixture pattern for the new `tests/test_rules_<family>.py`

## Verification (how to prove each rewrite works)

1. Unit: `tests/test_rules_<family>.py` asserts recognizer hits/misses + cost signs on
   hand-built fixtures; `pytest` via `uv`.
2. End-to-end per rule: `manbench.bench([rule_pass(fam)], [slug])` shows engine-verified
   `max(w,h)²×avgTicks` delta on the archived solution — the source of truth.
3. Portfolio (S2): full `bench` across all 8 `solutions/` archives; merge only on
   non-negative portfolio delta with zero regressions.
4. Safety is structural: the `optimize` driver's `bindings_preserved` + `verify` +
   strict-`<` accept gate guarantees no merged rule can produce a worse or incorrect grid.
