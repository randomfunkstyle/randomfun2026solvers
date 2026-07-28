"""The seek-drum (lm1.seekrom) and machine's opt-in hybrid jump acceleration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import optimize  # noqa: E402
from randomfun2026solvers.lm1 import machine  # noqa: E402
from randomfun2026solvers.lm1.isa import SEEK_OF, SEEK_SEMS, TARGET_SEMS  # noqa: E402
from randomfun2026solvers.lm1.programs import load  # noqa: E402
from randomfun2026solvers.lm1.rom import token_cells  # noqa: E402
from randomfun2026solvers.lm1.seekrom import (  # noqa: E402
    SEEK_K,
    build_seek_rom,
    pack_rows_even,
    seek_target,
)


def test_pack_rows_even_keeps_word_counts_even() -> None:
    words = list(range(200, 231))
    rows = pack_rows_even([token_cells(w) for w in words], 40)
    counts = [r.count("s") for r in rows]
    assert sum(counts) == len(words)
    # every row but the last closes on an even word, which is the counted
    # discard's invariant for the seek remainder
    assert all(c % 2 == 0 for c in counts[:-1])


def test_seek_rom_maps_every_word_to_a_row_and_offset() -> None:
    words = [200 + j for j in range(24)]
    rom = build_seek_rom(words, rows=4)
    assert len(rom.word_pos) == len(words)
    assert {r for r, _ in rom.word_pos} == set(range(rom.rows_used))
    for wi in range(0, len(words), 2):
        row, off = rom.word_pos[wi]
        assert seek_target(rom, wi) == row * SEEK_K + off
        assert off % 2 == 0  # instruction starts land on even offsets


def test_wide_operands_do_not_move_the_packing() -> None:
    """Fixed-width jump literals are what make the operand fixpoint terminate."""
    words = [7] * 40
    wide = frozenset(range(1, 40, 2))
    a = build_seek_rom(words, rows=4, wide=wide, wide_digits=4)
    b = build_seek_rom(
        [w if i not in wide else 1234 for i, w in enumerate(words)],
        rows=4,
        wide=wide,
        wide_digits=4,
    )
    assert a.word_pos == b.word_pos


def test_seek_split_only_rewrites_long_jumps() -> None:
    prog = load("deadman-3d")
    split = machine.seek_split(prog)
    assert len(split.instrs) == len(prog.instrs)
    seeks = [i for i in split.instrs if i.sem in SEEK_SEMS]
    assert seeks, "deadman-3d has long jumps to seek"
    # default SEEK_OPS is JMPF alone — measured best (see SEEK_DRUM's table)
    assert {i.mnemonic for i in seeks} == {"JMPS"}
    assert all(i.sem in TARGET_SEMS for i in seeks)
    # a threshold above every distance rewrites nothing
    assert not [
        i for i in machine.seek_split(prog, threshold=10**9).instrs if i.sem in SEEK_SEMS
    ]


def test_seek_of_covers_every_classic_target_sem() -> None:
    classic = {s for s in TARGET_SEMS if s not in SEEK_SEMS}
    assert set(SEEK_OF) == classic
    assert set(SEEK_OF.values()) == SEEK_SEMS


def test_registry_is_off_so_every_machine_stays_byte_identical() -> None:
    assert machine.SEEK_DRUM == set()
    assert (
        machine.build(load("brackets")).rows
        == machine.build(load("brackets"), seek=False).rows
    )


def test_seek_declines_when_no_jump_is_long_enough() -> None:
    # brackets is 72 image words: nothing to seek at the real threshold, and
    # saying so is better than building hardware that never fires.
    with pytest.raises(machine.MachineError, match="no jump is long enough"):
        machine.build(load("brackets"), seek=True)


@pytest.mark.slow
def test_brackets_seek_variants_pass_public_cases() -> None:
    """Both the all-seek and the mixed-slab builds run on the engine."""
    for threshold in (0, 32):
        m = machine.build(load("brackets"), seek=True, seek_threshold=threshold)
        res = optimize.verify(m.rows, "brackets")
        assert res.n_passed == len(res.cases) == 9, [
            c.detail for c in res.cases if not c.passed
        ]
        # doctrine, not a bounding box: inside the size class, roughly square
        assert max(m.width, m.height) <= 130
        assert (
            max(m.width, m.height) - min(m.width, m.height)
            <= max(m.width, m.height) // 3
        )


@pytest.mark.slow
def test_deadman_seek_variant_is_near_square() -> None:
    m = machine.build_for("deadman-3d", trim_dead=True, seek=True)
    assert max(m.width, m.height) <= 420
    assert (
        max(m.width, m.height) - min(m.width, m.height) <= max(m.width, m.height) // 10
    )
