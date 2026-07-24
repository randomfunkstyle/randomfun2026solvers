"""External batch solver implementations."""

from __future__ import annotations

from randomfun2026solvers.dispatch import SolverError, run_solver

__all__ = ["SolverError", "run_solver"]

# The littleman wrapper is a self-contained module; import it directly:
#   from randomfun2026solvers.littleman import Littleman, Snapshot, ...
# It is intentionally not eagerly imported here (keeps `python -m
# randomfun2026solvers.littleman` free of a double-import RuntimeWarning).
