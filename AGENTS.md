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
