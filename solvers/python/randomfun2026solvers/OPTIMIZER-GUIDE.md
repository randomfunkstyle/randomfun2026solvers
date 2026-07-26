# Semantic `.man` optimizer — run it, extend it

A post-optimizer for littleman `.man` programs. It shrinks the contest score
(`max(w,h)² × avgTicks`, or `max(w,h)²` on footprint-mode problems) by applying
correctness-preserving rewrites and keeping only candidates that pass every public
case and strictly lower the real judged score. It never returns a worse or failing
grid — worst case it returns the input.

If you are the next agent picking this up: read the two "Understand first" docs
below before touching a rule, then use the templates here.

## 0. Understand first — specs & task context

Ground truth for the language and scoring (all local, no need to hit the site):

- `littleman/SPEC.md` — the language: rooms, pipes, displays, the **full glyph/ISA
  table** (every op's exact effect), tick order, `Y` split, load errors. The single
  most important file for writing a correct rule.
- `littleman/GRADING.md` — scoring modes, `max(w,h)²×avgTicks`, tick counting,
  rounds, limits, the submission API.
- `littleman/reference/` — verbatim official text + `interpreter-probe.txt` (empirical
  per-glyph state deltas, the ISA confirmed against the real engine).
- `littleman/DETAILS.md`, `littleman/ARCH.md`, `littleman/DATAFLOW-SURVEY.md`,
  `littleman/PLOTTER-BLOCK.md` — deeper mechanics (rings, capacities, display
  wiring, nearest-pipe binding hazards).
- `littleman/OPTIMIZATION.md` — the score frontier, what past submissions taught,
  the accept-policy rules. Read before proposing a footprint-vs-tick trade.
- `SEMANTIC-OPTIMIZER-PLAN.md` (repo root) — the design of THIS system: what exists,
  the gap the semantic layer fills, the parallel work breakdown, and the **five
  correctness hazards** every rule must respect. Read this to know why the code is
  shaped the way it is.
- Per-problem statements + public test data: `tasks/problems/<slug>.json`
  (`scoring.load_problem(slug)` resolves these). The scored solutions live in
  `solutions/<slug>/<zero-padded-score>_<slug>.man` (lowest number = best).

Ground-truth engine oracles (the reference interpreter, `littleman.wasm`, driven by
`littleman/lm.mjs` and wrapped by `littleman.Littleman`):

- `Littleman.analyze(rows)` — rooms / pipes / displays (the authoritative parser).
- `Littleman.route(rows, x, y)` — which pipe a send/recv at `(x,y)` binds to
  (the nearest-pipe oracle; use it to check a move did not silently re-bind).
- `Littleman.judge(...)` / `lm.mjs run|tick|analyze|route|judge` — run/step/validate.
  `FastLittleman` is the fast in-process equivalent used for inner-loop verify.

## 1. Run the optimizer

CLI (one program against one problem):
```sh
# geometric passes only (default)
uv run python -m randomfun2026solvers.optimize solutions/tcp/000001678313030_tcp.man tcp
# + the semantic content-rewrite passes (opt-in)
uv run python -m randomfun2026solvers.optimize <prog.man> <slug> --semantic --out /tmp/out.man
```

Python:
```python
from randomfun2026solvers.optimize import optimize
res = optimize("prog.man", "tcp", semantic=True)   # semantic defaults to False
res.improved, res.base_score, res.score, res.render()   # res.log has the per-move trace
```

Benchmark the whole archive portfolio (real judged scores, with progress + ETA):
```sh
uv run python -m randomfun2026solvers.manbench --full           # all 8 slugs, judged
uv run python -m randomfun2026solvers.manbench --slugs brackets,tcp
```
```python
from randomfun2026solvers.optimize import PASSES, semantic_passes
from randomfun2026solvers.manbench import bench, format_rows
rows = bench(list(PASSES) + semantic_passes(), fast=False, progress=True)  # progress→stderr
print(format_rows(rows))
```
`bench` runs on the best archive per slug (`solutions/<slug>/`), cheapest-first so the
ETA calibrates early. Full portfolio ≈ 13 min (snake/gradebook/matmul dominate). Use
`fast=True` for a quick footprint-only pre-scan (no tick measurement).

To attribute a win to the semantic layer, diff geometric-only vs geometric+semantic:
`bench(list(PASSES), [slug])` vs `bench(list(PASSES)+semantic_passes(), [slug])`.

## 2. Architecture — where things live

| module | role |
|---|---|
| `manparse` / `manast` / `manstruct` | ASCII → graph / mutable round-tripping AST / cell lattice (transit tables, blocks, `transparent`/`shareable`, `Freedom`) |
| `manlogic` | inner-logic graph per room: `build_logic_graph`, `build_all`, `bp_provenance` (register dataflow, BP divisibility) |
| `manatom` | gadget cost model: `Gadget`, `counted_loop`, `counted_loop_horizontal`, `unrolled` (ticks/footprint of loop shapes) |
| `mansem` | pure ISA effect table: `glyph_effect`, `run_effect`, `BPFacts` |
| `manrules` | **the rule schema**: `RewriteRule`, `MatchSite`, `CostDelta`, `CATALOG`, `register`, `rules_for`, `FAMILIES` |
| `manrewrite` | applier: `apply_rules`, `rule_pass(family)`, `all_rules_pass()`, `swap_gadget` |
| `manrecog` | gadget recognition (match a block back to a `manatom.Gadget`) |
| `rules_{loops,arith,const,steer,pipe,io,macros}` | **the rule catalogs** (18 rules; each self-registers on import) |
| `optimize` | driver + accept gate: `optimize()`, `PASSES`, `semantic_passes()`, `verify`, `bindings_preserved`, `score_grid` |
| `manbench` | portfolio benchmark + progress/ETA |
| `manopt` / `manmoves` / `manroute` / `manfree` / `mancompact` / `layout` | the pre-existing **geometric** compaction (cuts, reroute, squash, relayout) |

Families: `{"loop","arith","const","steer","pipe","io"}` (frozen in `manrules.FAMILIES`).

## 3. Add a new rule/heuristic

A rule is a `RewriteRule` that (a) recognizes a pattern and (b) produces a replacement.
Put it in the `rules_<family>.py` for its family (or `rules_macros.py` for a
cross-family macro — register it into the family of its *dominant effect*). Adding a
rule needs **no change to `optimize.py` or the driver** — the accept gate handles
correctness for free.

### Two ways to express the edit
1. **Gadget swap** — leave `apply=None`, set `build: (MatchSite) -> [Gadget]`. The
   applier replaces the matched `Atom` with the single built `Gadget` at the same
   origin. Use only when entry/exit ports line up (e.g. loop tall↔unrolled).
2. **Cell edit** — set `apply: (Ast, MatchSite) -> None`. It mutates a **deep copy**
   of the AST in place (delete/rewrite a run, reflow after a shrink, re-route the man).
   `build` is unused; supply a stub `build=lambda _s: []`. This is the path for
   constant folds, identity elisions, corridor trims, steer coalescing, etc.

### Template
```python
# in rules_<family>.py
from .manrules import CostDelta, MatchSite, RewriteRule, register
from .mansem import run_effect          # dataflow: needs/writes over a glyph run
# from .manlogic import build_logic_graph, bp_provenance   # if you need CFG/BP facts

def _recognize(ast, room) -> list[MatchSite]:
    # called once per room by the applier with (ast, room).
    # Find occurrences; return [] for no match (never raise on a real grid —
    # recognize is NOT wrapped in try/except, only apply is).
    sites = []
    # ... inspect room.paint()/AST nodes; be conservative, prove preconditions ...
    return sites

def _apply(ast, site: MatchSite) -> None:
    # mutate `ast` (already a private deep copy) in place. May raise → candidate skipped.
    ...

def _cost(site: MatchSite) -> CostDelta:
    return CostDelta(d_cells=-2, d_ticks_per_value=0.0)  # signed; negative = better

register(RewriteRule(
    name="steer.trim_corridor",       # unique within the family
    family="steer",                   # must be in FAMILIES
    recognize=_recognize,
    build=lambda _s: [],              # unused for a cell-edit rule
    apply=_apply,
    cost_delta=_cost,
    preconditions=lambda s: True,     # extra legality the recognizer couldn't settle
    clobbers=frozenset(),             # A/B/BP/HEAD the replacement writes that the original didn't
    resizes_room=False,               # True only if you move a pipe-op cell / change footprint
))
```

`MatchSite.env` carries recognizer→builder data; keys are fixed per family (see the
table in `manrules.py`'s docstring: loop→`k`/`body`/`pairs`, const→`value`, …). You may
add family-private keys your own `apply`/`cost_delta` read.

### The accept gate (automatic — do not reimplement)
Every candidate your rule emits is run through `optimize`'s driver:
`round_trip_ok` → (if `placement` set) `bindings_preserved` → `verify` (FastLittleman,
**every public case, exact output/frames**) → `score_grid` → accept iff strictly lower.
Content rewrites carry `placement=None` and rely on `verify` for pipe-binding safety
(NOT `bindings_preserved`, which false-positives on interior edits). So a buggy rule at
worst yields no improvement — it can never ship a wrong grid.

### Correctness hazards (from the plan — every rule must respect the relevant ones)
1. **Nearest-pipe rebind** — moving an `s/r/S/R/U/q` cell can silently rebind (Manhattan
   nearest, ties by reading order). Set `resizes_room` and lean on `verify`; a pure
   content edit that keeps pipe-op cells put is safest.
2. **BP divisibility** — `unrolled(v)` is only correct when `BP % v == 0` is *proven*
   (`bp_provenance` → literal const multiple, or `x`-peel). Refuse `q`/unknown.
3. **Backtick reversal** — literals load on the *closing* backtick, reversed; mirror/
   relocate only when `manstruct._mirror_safe`.
4. **transparent vs shareable** — a relocated glyph must land on shareable floor and not
   cross another lane's exclusive turn (crossing an OP executes it). Check
   `CellInfo.shareable`/`crossable_by` + `manroute.route_man`.
5. **Execution order** — blocks are rigid (4-adjacency = execution order); keep the man's
   executed-glyph sequence identical.

### Verify your rule (required, per `AGENTS.md`)
- `uv run ruff check <your files>` — clean (`E,F,I,UP,B`, line length 100).
- `tests/test_rules_<family>.py` — **fast** unit tests: recognizer hits on the pattern,
  MISSES on near-misses and when a precondition can't be proven, and `cost_delta` signs.
  Mirror `tests/test_manatom.py` / `tests/test_rules_loops.py`.
- One `@pytest.mark.slow` test: a hand-built `.man` fixture (input→worker→output) that
  passes the engine, asserting `optimize.optimize(fixture, problem_dict, semantic=True)`
  (or `passes=[rule_pass("<family>")]`) accepts the rewrite and the objective drops with
  `passed=True`. A clean no-match on packed archives is an acceptable outcome — the
  fixture win is the required proof.
- `uv run pytest -q` stays green. Then `manbench` to measure real portfolio impact.

## 4. Known gaps / next levers
- `loop.mirror_horizontal` is registered but inert alone — it needs a companion man
  entry/exit re-router + a relayout pass to realize its footprint win.
- `arith` rules currently fire only on runs ending in `H` (conservative reflow); a real
  reflow using `manlogic` would widen their reach.
- Pipe-op **relocation** (recv side-flip, relay splice, attach re-siding) is deferred —
  it needs a binding gate keyed on geometry, not room-interior bytes (`bindings_preserved`
  can't be used for it).
- Display resolution shrink needs the problem (`problem["io"]["display"]`), so it must be
  a problem-aware pass wiring `rules_io.display_tighten_rule(w,h)` — not a self-contained
  catalog rule.
- Speed: `optimize` uses place-then-prune routing, a reordered gate, and an exact
  per-candidate tick budget (`_cand_cap`); keep new passes cheap-proxy-first so the engine
  is paid only on promising candidates.
