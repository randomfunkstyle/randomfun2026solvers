# Working rules for agents in this repo

## Tests: fast by default

**Target ~10s. 30s is a hard maximum.** The default `pytest` run is a loop you use
dozens of times an hour; the moment it costs minutes, it stops getting run, and
that is worse than having fewer tests.

So the default run excludes anything heavy. `pyproject.toml` sets
`addopts = "-m 'not slow'"`:

```sh
uv run pytest                # the fast tier — keep this under ~10s, never over 30s
uv run pytest -m slow        # the heavy tier — run occasionally, and before a push
uv run pytest -m ""          # everything
```

It also runs `-n auto` (pytest-xdist). Roughly 193 of these tests shell out to
node/wasm, so they are dominated by process startup and parallelise almost
linearly: 25s serial becomes ~11s on ten cores. If you add a test that cannot run
in parallel, mark it rather than removing `-n auto` for everyone.

Mark a test `@pytest.mark.slow` when it is dominated by **a full simulation or a
search** rather than by the logic you are testing:

- an optimiser sweep (these were 82s and 29s — 111 of the suite's original 145s);
- driving the wasm engine across a whole problem's public cases;
- anything measuring ticks on a real grid.

**Concentrate the fast tier on fast functionality.** Most of what is worth pinning
is pure: the ROM's backtick-column invariant, the fixed-width rescaling of jump
counts, pipe-binding *assertions* (the generator raises without running anything),
archive naming, verdict parsing. Those cost milliseconds and catch the bugs that
are invisible in the output.

When a heavy test is the only real proof of something — a generated grid actually
passing on the reference interpreter — keep it, mark it slow, and make sure the
fast tier still fails if the *generator* changes shape (e.g. assert the checked-in
grid still matches what the generator emits).

## deadman-3d is out of contest scope: optimise ticks, not footprint

`deadman-3d` and `deadman-3d_hires` are a **post-contest demo**. They are not
scored, not judged, and there is no problem by either name — which is exactly why
`test_public_cases_pass_on_the_real_interpreter[deadman-3d-600]` fails with
`problem not found`, and why `lambda-deadman`, `pathfinder-unit` and `snake-ring`
fail the same way. Those are expected, not latent bugs.

So the contest metric `max(w, h)**2 * ticks` **does not apply to this family.**
The only thing that matters is **how fast a frame renders** — frames per second,
which on this machine is CPU ops per second, which is **ticks per frame**.

- **Optimise ticks.** Report ticks. Do not quote `max**2 * ticks` for this family.
- **A change that trades ticks for columns is a regression here**, however well it
  scores. The `wings` tape-gate body was declined on exactly this basis: 275x253
  and -7.13% on the score metric, but **+1.15% ticks**.
- **A change that spends columns to save ticks is free.** If a teleport room wants
  more space than a corridor has, take the space.
- Size still matters where it is a **constraint** rather than a score: the taped
  machine's 300-column ceiling is pinned in `tests/test_deadman3d.py`, and every
  pipe must keep binding. Stay inside those; never minimise for its own sake.

### The one metric: ticks to the last frame

For `deadman-3d_hires`, report **ticks until the last frame of the tour is on the
wall** — `res.frame_ticks[-1]`, which is the whole run including boot and title,
and equals `res.step`. On the 21-round tour that is currently **369,544,763**,
mean **17,036,041 a frame**.

Quote the mean beside it when the question is frame rate; quote the total when
the question is "is this change an improvement".

**Why the total and not the frame-1→frame-N walk**, which is what every number
recorded before `0d99dd1` is: the walk can be gamed and the total cannot. Move
work into boot — precompute a table, unroll the loader differently — and
per-frame time falls while total work rises, with nothing in the gates to notice.
Boot is 7.80% of the run, so the total dilutes a gameplay win by that much; that
is a cheap price for a number nobody can quietly cheat.

The two differ by ~8.5%, so **say which one you mean.** `revalidate.py` writes
both to `measurements.jsonl` for exactly that reason. Per-frame times spread
1.55x across the tour (14.3M to 22.2M — monster-heavy frames cost more), so two
runs are comparable only at the same round count.

### Test scope: DOOM only

The contest is over and this family is all that is being worked on, so **do not
spend suite time on other problems' simulations.** When a run is slow because of
`snake`, `matmul`, `gradebook`, `sudoku-validity`, `brackets`, `tcp`,
`pathfinder`, `little-little-*` and friends, skip or deselect them and say so —
`-k`, `--deselect`, or a marker. The DOOM tests are the ones that must be green:

```sh
uv run pytest tests/test_deadman3d.py tests/test_deadman3d_hires.py \
              tests/test_memory_taped.py tests/test_wadimport.py -q
uv run pytest tests/test_deadman3d.py -q -m slow      # the pixel gate: 12/12
```

**One exception, and it is not optional: keep the byte-identity checks.**
`machine.py`, `rom.py`, `memory_taped.py` and the CPU builder are shared by every
slug, so a DOOM change can silently move another machine's grid. Those assertions
are milliseconds — they are not what makes a run slow, and they are the safety net
that has caught the most this session. Skip other problems' **simulations**, never
their **hash pins**.

Also expected and not yours to fix: the 12 pre-existing `-m slow` failures on
`main` (`test_lm1_two_tier` x2, `test_lm1_snake`, `test_matmul_grid`,
`test_lm1_pipe_cost`, `test_lm1_grid_store`, `test_lm1_lane_order[deadman-3d]`,
five `test_public_cases_pass_on_the_real_interpreter` params). Confirm your run
shows that same set and nothing new.

Three measurement traps this family has already sprung, all of which cost real
work before they were understood:

1. **Pipe length is not tick cost.** A pipe shifts in O(1) per tick whatever its
   length, so cost is length x *frequency* x who blocks on it. `cpu->drum` is the
   longest pipe in the machine at 437 cells and worth **0.019%**; the 60-cell
   `adapter->store` is worth an estimated **8.5%**, because every one of ~87,000
   store reads pays all 60 of its cells.
2. **`q` counts values anywhere in a pipe, not just at its destination.** The seek
   drum notices a request the tick it is sent and walks its cascade *concurrently*
   with transit, so a seek costs `max(pipe, cascade)`. Padding a pipe pays the full
   per-cell rate; shortening it recovers only the overhang.
3. **Profile before optimising, and profile the right thing.** The CPU spends
   **~48% of the run blocked** on `store:collector->cpu`. Region occupancy said so;
   pipe lengths did not. `scratch/doom_heatmap.py` and `scratch/doom_pipes.py`
   re-run it. Note `littleman/tools/heatmap.mjs` **cannot** profile this machine —
   the wasm engine OOMs its 4 GB heap on it.
4. **A `cpu:lane:*` region is not that lane.** The lanes' descent columns cut
   through the long lanes (`JMPS` spans x=22..58), and the lanes' exits are
   *stacked* — `ADD` drops onto `ST`'s own `v`, `MUL`/`LDA`/`DIV` onto `SUB`'s,
   every immediate onto `SND`'s. Occupancy of a lane's rectangle therefore mixes
   in other opcodes falling past it, so `cpu:lane:JMPS` is mostly not `JMPS`.
   Attribute by (cell, arrival direction) — `scratch/doom_opcodes.py` does, and
   `scratch/DOOM-OPCODES.md` is the per-opcode table it produces.

## Tests assert correctness, not quality

**Do not assert a footprint, score, or measured tick count as a recorded value.**
The judge already keeps our best submission, so an improvement is not a test
failure. Exact deliverable metrics belong in generator reports, submission
archives, docs, and commit messages.

Assert behavior instead:

- outputs and frames are correct, round by round;
- every pipe binds and the grid loads;
- a checked-in generated `.man` matches its generator;
- cases stay within the problem's semantic tick cap;
- independent engines agree with each other;
- an optimizer candidate improves relative to the baseline from the same run.

This is safe because the artifact-matches-generator assertion makes every shape
change visible as a regenerated `.man` diff. Do not pin dimensions or settle ticks
to force that visibility.

## Little Man validation backends

`optimize.verify` uses the independent in-memory `FastLittleman` validator by
default. It parses a grid once and runs a native C++ tick loop loaded into the
Python process, so repeated cases do not start Node or boot the Go/WASM engine.
The native library is compiled once with the system C++ compiler and cached in
the temporary directory by source hash.

```sh
uv run littleman-validate tasks/solutions/triangle_cpu.man triangle
LM_VALIDATOR=fast uv run pytest -m ""
LM_VALIDATOR=reference uv run pytest -m ""  # force the Node/WASM oracle
```

Pass an explicit `Littleman` instance to `optimize.verify` when a test needs a
fake backend or specifically needs the reference implementation. Keep
`littleman.py` and `lm.mjs` as the semantic oracle for differential tests,
debug snapshots, stepping, analysis, and routing; `FastLittleman` is the
validation-focused backend and does not replace those debugging APIs.

**The fast backend understates ticks on grids that use `Y`.** The parity
evidence below predates any splitting machine — every family in it was
single-runner-per-room. On `matmul-c9920b5f.man` (three `Y`s) the fast engine
reports avg 28,286 against the reference's 29,859, diverging on exactly the two
cases that keep split children in flight; on `matmul-5818b2cc.man` (no `Y`) the
two still agree to the tick. Both *pass* either way, so this is a timing
divergence, not a correctness one — but the judge runs reference semantics, so on
a split machine **explore with the fast engine and accept with the reference**.
`test_fast_and_reference_engines_disagree_on_ticks_once_Y_is_used` pins both
halves.

Current parity/performance evidence (2026-07-25):

- all public cases for all 12 checked-in solution families matched Node/WASM
  verdicts and exact tick counts, including `palette` and `plotter` frames;
- median validation speedup was 5.79x on `sudoku-validity` and 239x on the short
  `triangle` workload (warm native cache);
- the complete suite passed identically with both backends: 605 passed,
  13 skipped; whole-suite wall time was 86.05s fast versus 90.85s reference
  (only 1.06x overall because most tests do not go through `optimize.verify`).

## Never touch production from a test

Submitting is a real, rate-limited, outward-facing action. `tests/test_submit.py`
installs an autouse fixture that makes any network call raise, and clears
`ICFP_TOKEN`. Keep it that way.

## Generators emit their own debug sidecars

A generated `.man` carries no comments, so the generator is the only thing that
knows what a cell means. Every generator takes `--man` / `--html` / `--json` and
writes all three in one invocation, so an overlay can never drift from the grid it
describes. See `littleman/DEBUGGING.md`.

## Shared worktrees

Several agents work this repo at once, each in its own worktree, and `main` moves
underneath you. Before pushing: fetch, merge `origin/main` into your branch,
re-verify, then push. **Do not fast-forward another checkout's working tree** — it
may hold someone's uncommitted work; push `HEAD:main` instead, which moves the
remote and leaves every working tree alone.

## Optimisation work: the measurement is not separable

The most expensive mistake in this repo is treating an optimisation's measured
value as a property of the optimisation. It is a property of the optimisation
**and the machine it was measured on**, because these levers compete for one
critical path and whoever owns that path absorbs everyone else's gains.

Measured on `deadman-3d_hires` — same builder change, same program, before and
after the 11-bank store cut (`c51a748`) landed:

| lever | before | after |
|---|---|---|
| `LANE_PITCH` | −0.401% | **−4.351%** |
| `TAPED_CHAIN_REACH` | −0.020% *(declined)* | **−2.678%** *(shipped)* |
| `TAPED_SKIP_BATCH` | −27.29% | **+0.185%** *(declined)* |

Two of the three reversed. The store was ~68% of that run, so before it was fixed
every CPU-side lever was measuring idle time and every store-side lever was
measuring a bottleneck about to vanish. Nothing was wrong with the measurements —
they were answers to a question that stopped applying.

Four rules follow, and they are cheap:

**1. Agents do not commit generated artifacts.** `.man`, `.debug.html` and
`.debug.json` are derived; regenerate them once at the landing point. Every merge
conflict this workflow has produced was a generated artifact or `METRICS.md`,
while `machine.py` has auto-merged clean every time. An agent that commits no
artifact merges with zero conflicts — that is measured, not hoped.

**2. Order by profile share, not by convenience.** Optimise the subsystem owning
the largest fraction of the run, land it, re-profile, then pick again.
Parallelising *across* subsystems is safe only when they are weakly coupled (the
ROM block and the store block are; the store block and anything CPU-side are not).
Parallelising *within* one lever — sweeping eight bank counts — is always safe:
the variants are mutually exclusive and only one wins.

**3. Agents search; one owner ships.** An agent's deliverable is a measured table
and a candidate config. Registry edits, artifact regeneration and the landing
commit belong to whoever holds the integration branch. Search parallelises
cleanly; landing is what collides.

**4. Re-validate after every landing; do not reason about it.**
`scratch/deadman3d-opt/revalidate.py` re-runs the shipped config plus every
declined lever on a 3-round tour, ~30s a variant. Run it after anything lands. A
decline is a fact about a machine and the machine keeps changing:
`TAPED_CHAIN_REACH` sat declined at −0.020% while being worth −2.678%, and
nothing in the suite could have noticed.

**Numbering.** `METRICS.md` milestones (`M<n>`) are one global sequence. Agents
branching from the same base all reach for the same next number — M18 was
allocated twice. Take the number from the *integration branch's* `METRICS.md` at
the moment you write it, not from the base you branched off.
