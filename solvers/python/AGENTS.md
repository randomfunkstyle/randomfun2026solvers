# Python Solver Instructions

This directory contains Python solver implementations.

Run Python solvers from the repository root through the shared worker contract:

```sh
./solve --solver sudoku --input /tmp/input.json --output /tmp/output.json
```

For direct CLI debugging, set `PYTHONPATH` to this directory and run the module:

```sh
PYTHONPATH=solvers/python python3 -m randomfun2026solvers.cli --solver sudoku --input /tmp/input.json --output /tmp/output.json
```

Use the repository test suite after changes:

```sh
uv run pytest
```
