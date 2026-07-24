"""Top-level: Python-subset source -> little-man grid."""

from __future__ import annotations

from .frontend import lower
from .layout import emit_grid


def compile_source(src: str) -> str:
    return emit_grid(lower(src))
