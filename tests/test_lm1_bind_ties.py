"""``check_bindings`` must resolve §7.1 ties exactly as the engines do.

``SPEC.md:183``: "Ties break by **reading order** (top to bottom, left to
right)". ``fast_littleman._bind_pipe_ops`` implements that as
``min(candidates, key=(distance, attach_y, attach_x))`` and the reference WASM
``route()`` agrees; the native engine consumes the Python bindings, so that one
key is the whole rule.

The builder used to refuse a tie *outright* — including one the intended pipe
wins — which made it strictly stronger than the machine it builds for and cost
``deadman-3d_hires`` men-v3 a whole ``mem_pad`` column (81,042,708 -> 80,342,861
at 21 rounds). These pin the rule so it cannot quietly revert to "ties fail".
"""
from __future__ import annotations

import pytest

from randomfun2026solvers.lm1.machine import Band, MachineError, check_bindings


def _glyph(x: int, y: int, glyph: str, band) -> list[tuple[int, int, str, str]]:
    return [(x, y, glyph, band)]


def test_a_tie_the_intended_pipe_wins_on_reading_order_is_accepted() -> None:
    """``mem_resp`` at y=154 reads before ``rom`` at y=178, so it takes the tie.

    This is ``'r' at (22,163)`` on the shipped men-v3 grid, the one binding in
    that machine decided by a tie — verified against the WASM oracle, which
    routes it to the pipe attached at (43,154).
    """
    touches = {"rom": (7, 178), "mem_resp": (43, 154), "in": (9, 137)}
    assert abs(7 - 22) + abs(178 - 163) == 30
    assert abs(43 - 22) + abs(154 - 163) == 30

    check_bindings(_glyph(22, 163, "r", Band.MEM), touches)


def test_a_tie_the_intended_pipe_loses_is_still_refused() -> None:
    """Same distances, opposite intent: the ``rom`` glyph must not silently bind.

    A tie is *decidable*, not free — whoever loses reading order loses the
    binding, and the builder has to say so.
    """
    touches = {"rom": (7, 178), "mem_resp": (43, 154), "in": (9, 137)}

    with pytest.raises(MachineError, match="must bind 'rom'"):
        check_bindings(_glyph(22, 163, "r", "rom"), touches)


def test_reading_order_is_row_before_column() -> None:
    """Equal distance and equal row: the westward attach wins.

    Guards the second half of the key. Both attaches are 10 away from (10,10);
    ``rom`` at x=5 reads before ``in`` at x=15 on the same row.
    """
    touches = {"rom": (5, 10), "in": (15, 10)}

    check_bindings(_glyph(10, 10, "r", "rom"), touches)
    with pytest.raises(MachineError, match="must bind 'in'"):
        check_bindings(_glyph(10, 10, "r", Band.IN), touches)


def test_distance_still_beats_reading_order() -> None:
    """Reading order is only the *tiebreak* — a nearer pipe wins outright.

    ``in`` reads first but sits 12 away; ``mem_resp`` is 2 away and takes it.
    """
    touches = {"in": (10, 100), "mem_resp": (10, 114)}

    check_bindings(_glyph(10, 112, "r", Band.MEM), touches)
    with pytest.raises(MachineError, match="must bind 'in'"):
        check_bindings(_glyph(10, 112, "r", Band.IN), touches)


def test_sends_and_reads_draw_from_disjoint_pools() -> None:
    """An ``s`` never competes with an incoming pipe, however near it sits."""
    touches = {"mem_resp": (10, 10), "mem_req": (40, 40), "out": (41, 41)}

    # mem_resp is 0 away but incoming, so the send ignores it entirely.
    check_bindings(_glyph(10, 10, "s", Band.MEM), touches)


def test_an_absent_pipe_is_named_rather_than_mis_bound() -> None:
    touches = {"rom": (7, 178), "mem_resp": (43, 154)}

    with pytest.raises(MachineError, match="wants pipe 'in'"):
        check_bindings(_glyph(22, 163, "r", Band.IN), touches)
