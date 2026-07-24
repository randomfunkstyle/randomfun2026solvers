"""Deterministic interpreter of the little-man 2D grid language.

Spec: task_docs/language.md.  Entry point: `run`.
"""

from .machine import RunResult, run
from .parse import ParseError, parse_grid

__all__ = ["run", "RunResult", "parse_grid", "ParseError"]
