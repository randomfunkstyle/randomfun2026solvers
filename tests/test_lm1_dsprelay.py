"""The display fan-out relay's arm decision.

`isa.py` records that `DSP p` "cannot be built" because a lane's `s` binds to a
pipe statically. That is true of a *lane* and is what this relay works around: the
CPU sends one selector word and one value down a single pipe, and a room behind the
seam picks the port. These tests pin the control flow; placing the three outgoing
pipes is a separate, geometric question.
"""

from __future__ import annotations

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1.dsprelay import (
    ARM_TAG,
    PORT_ADDR,
    PORT_DATA,
    PORT_SWAP,
    build_relay_probe,
    relay_model,
)


def _probe() -> FastLittleman:
    return FastLittleman("\n".join(build_relay_probe()))


def test_port_codes_match_the_emulator():
    """The selector reuses `display_writes`' own codes, so nothing has to translate."""
    from randomfun2026solvers.lm1.emulator import Emulator  # noqa: F401

    assert (PORT_ADDR, PORT_DATA, PORT_SWAP) == (0, 1, 2)


def test_each_port_takes_its_own_arm():
    """The whole point. Arms emit *different* tags, so a passing run cannot be one
    where the branch did nothing and the value simply fell through."""
    machine = _probe()
    for port in (PORT_ADDR, PORT_DATA, PORT_SWAP):
        result = machine.run([port, 7], max_ticks=400)
        assert result.fatal is None, (port, result.fatal)
        assert result.halted, port
        assert result.output == [relay_model(port, 7)], (port, result.output)
    assert len(set(ARM_TAG.values())) == 3, "tags must distinguish the arms"


@pytest.mark.parametrize("value", [0, 1, 7, 42, 255])
def test_the_value_passes_through_every_arm(value: int):
    """Two words in, and the second must survive the branch on the first."""
    machine = _probe()
    for port in (PORT_ADDR, PORT_DATA, PORT_SWAP):
        assert machine.run([port, value], max_ticks=400).output == [relay_model(port, value)]


def test_zero_is_a_decided_arm_not_a_fallthrough():
    """`X` is three-way and the selector is non-negative, so the relay subtracts one:
    `p - 1` is -1/0/+1. The middle arm is DATA, reached by *zero*, which is the case
    a two-way branch would have had to leave to luck."""
    result = _probe().run([PORT_DATA, 3], max_ticks=400)
    assert result.output == [ARM_TAG[PORT_DATA] + 3]


def test_rom_literals_cannot_carry_the_sign_themselves():
    """Why the subtraction exists at all: a negative selector would be simpler, and
    `rom.digit_width` refuses it — so the sign has to be made inside the room."""
    from randomfun2026solvers.lm1.rom import digit_width

    with pytest.raises(ValueError, match="non-negative"):
        digit_width([-1])
