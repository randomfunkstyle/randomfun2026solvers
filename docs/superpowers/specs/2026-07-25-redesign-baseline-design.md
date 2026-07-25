# Redesign Baseline

## Goal

Reduce the repository to a clean starting point for redesigning the Little Man
solution flow. Checked-in `.man` programs are first-class artifacts; solver
generators and historical implementations are out of scope.

## Retained functionality

- Keep every task JSON under `tasks/problems/`. Each JSON is the source of truth
  for the task description, I/O schema, public I/O examples, and scoring mode.
- Keep one solution at `tasks/solutions/triangle.man`, containing the approved
  hand-written grid.
- Keep the reference Little Man runtime:
  `littleman/lm.mjs`, `littleman/littleman.wasm`, and
  `littleman/wasm_exec.js`.
- Keep stable language and scoring documentation:
  `littleman/SPEC.md` and `littleman/GRADING.md`.
- Keep a Python wrapper and scorer in a neutral `littleman_tools` package.
  These tools run and score existing `.man` files; they do not generate
  solutions.
- Keep focused tests proving that the runtime executes `.man` files, scoring
  works against problem JSON, and the retained Triangle program passes every
  public case.

## Removed functionality

- Remove the root external-solver entrypoint and all Bash, Swift, and Python
  solver dispatch.
- Remove assemblers, generators, optimizers, compaction tools, submission code,
  archived submissions, generated debug sidecars, and experimental programs.
- Remove all non-Triangle solutions and case fixtures from solution locations.
- Remove worked/example `.man` programs outside the retained Triangle solution.
- Remove implementation-history documentation, generated HTML, raw reference
  dumps, debugging tools, and all plan/spec files.

## Final layout

```text
README.md
pyproject.toml
uv.lock
littleman/
  README.md
  SPEC.md
  GRADING.md
  lm.mjs
  littleman.wasm
  wasm_exec.js
littleman_tools/
  __init__.py
  runner.py
  scoring.py
tasks/
  README.md
  problems/
    README.md
    _index.json
    <all task JSON files>
  solutions/
    triangle.man
tests/
  test_runner.py
  test_scoring.py
  test_triangle.py
```

Repository metadata such as `.gitignore` and `AGENTS.md` remains.

## Interfaces

The Node CLI remains the reference execution interface:

```sh
node littleman/lm.mjs run tasks/solutions/triangle.man --input "4"
```

The Python wrapper mirrors the runner:

```sh
uv run python -m littleman_tools.runner run \
  tasks/solutions/triangle.man --input "4"
```

The scorer accepts a `.man` path plus a task slug or problem JSON:

```sh
uv run python -m littleman_tools.scoring \
  tasks/solutions/triangle.man triangle
```

## Error handling

Runner load and execution failures remain explicit nonzero CLI exits. Scoring
fails if a problem cannot be loaded, a program has a fatal runtime error, or it
does not emit the expected number of output values within the task tick cap.

## Verification

- Assert the tracked file set matches the intended baseline and contains no
  solver, archive, compacted, example, plan, or spec artifacts.
- Run the focused test suite with `uv run pytest`.
- Run Triangle through the Node CLI for a representative input.
- Judge Triangle against every public case from `tasks/problems/triangle.json`.
- Score Triangle with the retained scoring CLI.
