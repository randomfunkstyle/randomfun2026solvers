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
