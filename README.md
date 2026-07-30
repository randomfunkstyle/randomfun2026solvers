# randomfun2026solvers

Team **randomfunkstyle**'s work for the **ICFP Contest 2026** — the one where you
write programs in `littleman`, a 2D ASCII language of little men walking a grid
of rooms and talking through pipes.

16 problems solved, every public and private case passing. The story of how is in
**[`WRITEUP-HUMAN.md`](WRITEUP-HUMAN.md)** — start there.

If you would rather read the agent's version 0 — the same story with a timeline
and one decision tree per problem, written before a human went near it — it is in
[`WRITEUP.md`](WRITEUP.md), with an illustrated build at
[`writeup.html`](writeup.html). :)

Two things live here:

1. **LM-1** — a general-purpose CPU *written in littleman*, plus a compiler that
   turns a small assembly into a complete machine (CPU + ROM + store + display).
   One machine, sixteen programs. See [`littleman/ARCH.md`](littleman/ARCH.md).
2. **Hand-built dataflow grids** — bespoke machines for the problems where the
   score mattered more than the coverage. A walked glyph costs 1 tick against the
   CPU's 46 per instruction, and that is worth up to 3,457× (`tcp`).

## Layout

| path | what |
|---|---|
| [`littleman/`](littleman/) | headless `.man` runner (`lm.mjs` over the bundled wasm engine), the reconstructed [`SPEC.md`](littleman/SPEC.md), every design doc, and `tools/` |
| [`littleman/DEADMAN-3D.md`](littleman/DEADMAN-3D.md) | **DOOM on the CPU** — a first-person raycaster with real level geometry, monsters and a live HUD; how to run it, play it, and build it from your own WAD |
| [`solvers/python/randomfun2026solvers/lm1/`](solvers/python/randomfun2026solvers/lm1/) | the LM-1 compiler: ISA, assembler, emulator, ROM, stores, router, layout |
| [`solvers/python/randomfun2026solvers/`](solvers/python/randomfun2026solvers/) | per-problem generators (`llm_lm1.py`, `lllm_ring.py`, `brackets_*.py`, …), the fast verifier and the submit tool |
| [`tasks/problems/`](tasks/problems/) | problem statements and public test data, as served by the contest API |
| [`tasks/solutions/`](tasks/solutions/) | the generated/hand-built grids themselves |
| [`solutions/`](solutions/) | every graded submission, archived under its server-verified score |
| [`tests/`](tests/) | ~2,600 tests; the fast tier keeps generators honest, the slow tier runs real engines |

## Running a grid

```sh
littleman/lm.mjs run  prog.man --input "1 2 3"     # run to completion
littleman/lm.mjs tick prog.man 40                  # step and dump the grid
node littleman/tools/run-cases.mjs prog.man cases.json     # score against cases
node littleman/tools/run-display-cases.mjs prog.man tasks/problems/snake.json
```

The wasm engine runs out of memory on our largest machines, so verification goes
through the native tick loop instead — same semantics, ~5–200× faster, and it is
what every submission was checked with:

```sh
uv run python -m randomfun2026solvers.fast_littleman prog.man little-little-man --tick-cap 12000000
```

Generated grids carry no comments, so every generator emits its own overlay:
`--man` / `--html` / `--json` in one invocation. The workflow is in
[`littleman/DEBUGGING.md`](littleman/DEBUGGING.md).

## Tests

```sh
uv run pytest              # fast tier, kept under ~30s
uv run pytest -m slow      # engine runs, sweeps, real grids
```

Tests assert behaviour — outputs, pipe binding, engines agreeing with each other
— never a recorded score, so an improvement is never a failure. The rules the
repo was worked under are in [`AGENTS.md`](AGENTS.md).

## Submitting

Needs the team API key in `ICFP_TOKEN` or an untracked `.icfp-token`.

```sh
uv run python -m randomfun2026solvers.submit send brackets --file path/to.man --note "what this is"
uv run python -m randomfun2026solvers.submit send brackets --dry-run    # local check only
uv run python -m randomfun2026solvers.submit list                       # what is archived
uv run python -m randomfun2026solvers.submit get <submission-id>
```

`send` verifies locally first and refuses a grid it has already sent (matched by
hash against the archive). Every graded result is archived as
`solutions/<slug>/<zero-padded-score>_<slug>.man` plus a `.descr` with the
verdict, the fingerprint and a free-form note — so a listing sorts best-first and
a worse run can never overwrite a better one.

Two gotchas that cost us time: Cloudflare `403 error code: 1010` is a
browser-signature ban on the default `Python-urllib` User-Agent, not an auth
failure; and local scores understate — the judge runs private cases too, and its
tick average ran a consistent **1.098×** ours.

## Results

| problem | score | box | | problem | score | box |
|---|---|---|---|---|---|---|
| `triangle` | 960 | 8×8 | | `plotter` | 22,774,730 | 44×56 |
| `history-lesson` | 8,100 | 90×90 | | `snake` | 108,396,066 | 75×69 |
| `reverse-a-list` | 34,535 | 14×14 | | `gradebook` | 194,662,790 | 70×70 |
| `brackets` | 330,456 | 25×25 | | `matmul` | 232,294,501 | 72×81 |
| `sort-numbers` | 413,066 | 14×14 | | `subset-sum` | 5,218,553,037 | 80×84 |
| `tcp` | 535,084 | 17×17 | | `little-little-little-man` | 8,037,334,868 | 144×202 |
| `sudoku-validity` | 2,815,180 | 20×20 | | `pathfinder` | 10,636,538,807 | 82×173 |
| `memory` | 19,973,628 | 108×107 | | `little-little-man` | 163,823,101,714 | 180×179 |

Score is `max(width, height)² × average ticks`, lower better.

## Prehistory: the batch-solver harness

The first commits (20–22 July, before the task was published) build a generic
contest harness: a `./solve` entrypoint taking `--solver/--input/--output` with
base64 JSON envelopes, plus an interactive stdio mode for problems that need a
live conversation with a server. **None of it was used.** The 2026 contest
submits whole programs over an HTTP API, so the harness had nothing to talk to.

The entrypoint still works (`./solve --solver shell-smoke --input in.json --output
out.json`) and dispatches by solver name into [`solvers/bash`](solvers/bash),
[`solvers/python`](solvers/python) and [`solvers/swift`](solvers/swift) — but it
is history, not infrastructure.
