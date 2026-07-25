from __future__ import annotations

import pytest

from randomfun2026solvers.circuit import Collision
from randomfun2026solvers.memory_tape import (
    build_v3_external_init,
    build_v3_upstream_init,
    initializing_relay,
)


def test_initializing_relay_is_compact_and_has_paired_fill() -> None:
    rows = initializing_relay(100)

    assert (max(map(len, rows)), len(rows)) == (12, 5)
    assert sum(row.count("s") for row in rows) == 3
    assert rows[0].startswith("@`100`b>d>r")


def test_initializing_relay_requires_even_memory_size() -> None:
    with pytest.raises(Collision, match="even memory size"):
        initializing_relay(99)


def test_external_initializer_candidate_has_stable_bounds() -> None:
    rows = build_v3_external_init(100)

    assert (max(map(len, rows)), len(rows)) == (34, 37)


def test_upstream_initializer_keeps_proof_layout_bounds() -> None:
    rows = build_v3_upstream_init(100)

    assert (max(map(len, rows)), len(rows)) == (34, 37)
    assert "|@>rv|" in rows[31]
