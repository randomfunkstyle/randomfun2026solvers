"""Stage-1 stored-program LM-1 (lm1.ram_machine): unit + engine tests."""

from __future__ import annotations

import pytest

from randomfun2026solvers import optimize
from randomfun2026solvers.lm1.machine import plan
from randomfun2026solvers.lm1.programs import load
from randomfun2026solvers.lm1.ram_machine import build_ram, digit_factors, ram_words


def test_digit_factors_covers_and_builds() -> None:
    for n in (1, 7, 76, 144, 154, 2368):
        total, glyphs = digit_factors(n)
        assert total >= n
        # The glyph string evaluates to the product on a littleman A/B pair.
        a = b = 0
        for ch in glyphs:
            if ch.isdigit():
                a = int(ch)
            elif ch == "M":
                b = a
            elif ch == "*":
                a = a * b
        assert a == total
        # Padding should stay small: never more than 12% waste.
        assert total <= max(n + 1, int(n * 1.12))


def test_ram_words_targets_are_absolute_addresses() -> None:
    prog = load("brackets")
    p = plan(prog)
    words = ram_words(prog, p)
    assert len(words) == 2 * len(prog.instrs)
    instrs = sorted(prog.instrs, key=lambda i: i.pos)
    # Every jump/branch operand is an odd store address inside the image.
    from randomfun2026solvers.lm1.isa import TARGET_SEMS

    for k, ins in enumerate(instrs):
        if ins.sem in TARGET_SEMS:
            operand = words[2 * k + 1]
            assert operand % 2 == 1, "targets point at opcode words (addr = 2t + 1)"
            assert 1 <= operand <= len(words)


def test_brackets_ram_builds() -> None:
    m = build_ram(load("brackets"))
    assert m.width > 0 and m.height > 0
    assert "pstore" in m.regions and "fetcher" in m.regions


@pytest.mark.slow
def test_brackets_ram_passes_public_cases() -> None:
    m = build_ram(load("brackets"))
    res = optimize.verify(m.rows, "brackets")
    assert res.n_passed == len(res.cases) == 9, [c.detail for c in res.cases if not c.passed]


def test_brackets_ram2_builds_and_rejects_banked_store() -> None:
    from randomfun2026solvers.lm1.machine import MachineError
    from randomfun2026solvers.lm1.ram_machine2 import build_ram2

    m = build_ram2(load("brackets"))
    assert "pstore" in m.regions
    # A multi-column program store is unsound: the collector's R merges column
    # answer pipes by reading order, which lets the sentinel overtake stale
    # answers under prefetch backpressure (measured on gradebook (6,141)).
    with pytest.raises(MachineError):
        build_ram2(load("brackets"), store_shape=(2, 41))


@pytest.mark.slow
def test_brackets_ram2_passes_public_cases() -> None:
    from randomfun2026solvers.lm1.ram_machine2 import build_ram2

    m = build_ram2(load("brackets"))
    res = optimize.verify(m.rows, "brackets")
    assert res.n_passed == len(res.cases) == 9, [c.detail for c in res.cases if not c.passed]
