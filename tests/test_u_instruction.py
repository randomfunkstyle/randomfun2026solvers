"""The pipe-directed receive instruction, especially its free reversal."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402
from randomfun2026solvers.littleman import Littleman  # noqa: E402
from randomfun2026solvers.manstruct import Kind, _exits_for  # noqa: E402
from randomfun2026solvers.value_ring import build_reverse, build_sort  # noqa: E402


# The input pipe enters the compute room from the east.  Its value reaches U
# while the worker is also walking east, so "away from the pipe" is west: a
# true 180-degree reversal without a steer glyph.  The worker then walks back
# to `s` and emits the received value into the west-side output pipe.
U_TURN_ECHO = """\
+-+  +-------+  +-+
|O|<<|Hs  @ U|<<|I|
+-+  +-------+  +-+"""


def test_u_is_not_structurally_heading_preserving() -> None:
    exits = _exits_for(Kind.OP, "U")
    assert set(exits) == {"E", "W", "N", "S"}
    assert all(exit_ is None for exit_ in exits.values())


def test_u_reverses_away_from_the_pipe_without_a_turn_glyph() -> None:
    before = Littleman().tick(U_TURN_ECHO, 2, input=[42]).entities.runners[0]
    after = Littleman().tick(U_TURN_ECHO, 3, input=[42]).entities.runners[0]

    assert before.pos.as_tuple() == (12, 1)
    assert before.dir.as_tuple() == (1, 0)
    assert after.pos.as_tuple() == (11, 1)
    assert after.dir.as_tuple() == (-1, 0)
    assert after.a == 42


def test_fast_backends_match_the_u_reversal_oracle() -> None:
    machine = FastLittleman(U_TURN_ECHO)
    native = machine.run([42], expected=[42])
    python = machine.run([42], expected=[42], native=False)

    assert native.output == python.output == [42]
    assert native.step == python.step == 9
    assert native.passed is python.passed is True


def test_value_ring_relays_use_one_u_turnaround_each() -> None:
    for build in (build_reverse, build_sort):
        relay_grid = "\n".join(build())
        assert relay_grid.count("U") == 1
