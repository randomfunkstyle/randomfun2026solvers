# Working rules for agents in this repo

## Baseline scope

This is an artifact-first Little Man baseline. Keep task definitions in
`tasks/problems/`, `.man` artifacts in `tasks/solutions/`, and only the runtime
and scoring tools needed to execute or measure those artifacts. Do not add
solver dispatch, generators, optimizers, submission clients, archive folders,
or implementation-planning documents without explicit direction.

## Tests: fast by default

The default test run must stay under 30 seconds:

```sh
uv run pytest                # fast tier
uv run pytest -m slow        # runtime and scoring checks
uv run pytest -m ""          # all tests
```

Mark a test `@pytest.mark.slow` if it drives the WebAssembly runtime across a
full task case set or measures a real program score. Keep fast tests focused on
pure parsing, layout, and artifact invariants.

## Never touch production from a test

Tests must not make network calls, submit programs, or depend on credentials.

## Shared worktrees

Several agents may work in the repository at once. Before pushing, fetch and
merge `origin/main` into the working branch, re-verify, then push the branch
head without moving another checkout's working tree.
