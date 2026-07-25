"""Tests for the LM-1 ISA table (randomfun2026solvers.lm1.isa).

The table is the single source of truth for the emulator, the assembler and the
future ``.man`` generator, so these tests pin its shape rather than any one
opcode number.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1.isa import (  # noqa: E402
    DEFAULT_ISA,
    LM1_EXT,
    LM1_V1,
    MICRO_TICKS,
    Isa,
    Micro,
    Op,
    Sem,
    TickModel,
)


# ── ARCH.md §6's table, verbatim ─────────────────────────────────────────────
def test_v1_is_sixteen_opcodes_zero_to_fifteen() -> None:
    assert len(LM1_V1) == 16
    assert sorted(op.code for op in LM1_V1) == list(range(16))


def test_v1_mnemonics_match_arch_table() -> None:
    expected = [
        "NOP",
        "LDI",
        "IN",
        "OUT",
        "ADDI",
        "SUBI",
        "MULI",
        "LD",
        "ST",
        "ADD",
        "SUB",
        "JMPF",
        "BRZ",
        "BRN",
        "DSP",
        "HALT",
    ]
    assert [LM1_V1.by_code(c).mnemonic for c in range(16)] == expected


def test_v1_has_no_ext_rows_and_ext_extends_it() -> None:
    assert not any(op.ext for op in LM1_V1)
    assert all(op in LM1_EXT.ops for op in LM1_V1.ops)
    assert {op.mnemonic for op in LM1_EXT if op.ext} == {
        "DIVI",
        "MODI",
        "MUL",
        "LDP",
        "STP",
        "LDA",
        "MOVA",
        "SND",
        "RCV",
        "DSPA",
        "DSPD",
        "DSPS",
        "NEG",
        "PUSH",
        "POP",
    }


def test_default_isa_is_the_extended_one() -> None:
    assert DEFAULT_ISA is LM1_EXT


# ── invariants every row must satisfy ───────────────────────────────────────
@pytest.mark.parametrize("op", LM1_EXT.ops, ids=lambda op: op.mnemonic)
def test_row_shape(op: Op) -> None:
    assert op.operands in (0, 1)  # ARCH §5.2: no bit packing, 0 or 1 operand words
    assert op.words == 1 + op.operands
    assert op.description
    assert all(isinstance(g, Micro) for g in op.micro)
    # ARCH §5.3: exactly one paired ring read per operand word.
    assert op.micro.count(Micro.RING_READ) == op.operands


@pytest.mark.parametrize("op", LM1_EXT.ops, ids=lambda op: op.mnemonic)
def test_every_op_has_a_unique_semantic_tag(op: Op) -> None:
    assert LM1_EXT.by_sem(op.sem) is op


def test_ring_read_count_is_validated() -> None:
    with pytest.raises(ValidationError, match="ring read"):
        Op(
            code=99,
            mnemonic="BAD",
            operands=1,
            description="operand word never fetched",
            micro=(Micro.MOV,),
            sem=Sem.NOP,
        )


def test_operand_count_is_validated() -> None:
    with pytest.raises(ValidationError, match="operands must be 0 or 1"):
        Op(
            code=99,
            mnemonic="BAD2",
            operands=2,
            description="two operand words",
            micro=(Micro.RING_READ, Micro.RING_READ),
            sem=Sem.NOP,
        )


# ── lookups and ISA algebra ─────────────────────────────────────────────────
def test_lookups() -> None:
    ldi = LM1_V1.by_mnemonic("LDI")
    assert LM1_V1.by_code(ldi.code) is ldi
    assert LM1_V1.by_sem(Sem.SET_IMM) is ldi
    assert LM1_V1.by_mnemonic("ldi") is ldi  # case-insensitive
    with pytest.raises(KeyError):
        LM1_V1.by_mnemonic("DIVI")  # v1 has no division at all
    with pytest.raises(KeyError):
        LM1_V1.by_code(99)


def test_jmp_is_an_alias_for_jmpf() -> None:
    assert LM1_V1.by_mnemonic("JMP") is LM1_V1.by_mnemonic("JMPF")


def test_jump_and_branch_ops_take_labels() -> None:
    targets = {op.mnemonic for op in LM1_EXT if op.takes_target}
    assert targets == {"JMPF", "BRZ", "BRN"}


def test_duplicate_opcode_is_rejected() -> None:
    clash = Op(
        code=0,
        mnemonic="CLASH",
        operands=0,
        description="same number as NOP",
        micro=(),
        sem=Sem.NOP,
    )
    with pytest.raises(ValidationError, match="duplicate opcode"):
        LM1_V1.extended("clash", [clash])


def test_duplicate_mnemonic_is_rejected() -> None:
    clash = Op(
        code=42,
        mnemonic="NOP",
        operands=0,
        description="same name as NOP",
        micro=(),
        sem=Sem.NOP,
    )
    with pytest.raises(ValidationError, match="duplicate mnemonics"):
        LM1_V1.extended("clash", [clash])


def test_alias_collision_is_rejected() -> None:
    clash = Op(
        code=42,
        mnemonic="GOTO",
        operands=1,
        description="steals JMPF's alias",
        micro=(Micro.RING_READ, Micro.SKIP_CYCLE),
        sem=Sem.NOP,
        aliases=("JMP",),
    )
    with pytest.raises(ValidationError, match="duplicate mnemonics"):
        LM1_V1.extended("clash", [clash])


def test_restricted_isa_is_a_subset() -> None:
    tiny = LM1_V1.restricted("tiny", ["OUT", "HALT"])
    assert [op.mnemonic for op in tiny] == ["OUT", "HALT"]
    assert tiny.decode_bits == 4  # HALT is still opcode 15


def test_decode_trie_depth_tracks_the_table() -> None:
    assert LM1_V1.decode_bits == 4  # ARCH §7.2's depth-4 trie
    assert LM1_EXT.decode_bits == 5  # eight more opcodes cost one more bit
    assert Isa(name="two", ops=LM1_V1.ops[:2]).decode_bits == 1


# ── the §7.2 tick budget ────────────────────────────────────────────────────
def test_micro_ticks_cover_every_glyph() -> None:
    assert set(MICRO_TICKS) == set(Micro)


def test_per_instruction_cost_matches_arch_budget() -> None:
    ticks = TickModel()
    # ARCH §7.2: ~40–60 ticks per instruction, x2 with an operand word.
    for mnemonic in ("NOP", "OUT", "LDI", "ADDI", "JMPF", "HALT"):
        cost = ticks.instruction(LM1_V1.by_mnemonic(mnemonic))
        assert 38 <= cost <= 70, (mnemonic, cost)
    # Memory instructions are the expensive ones (six words on the STORE wire).
    assert ticks.instruction(LM1_V1.by_mnemonic("LD")) > ticks.instruction(
        LM1_V1.by_mnemonic("LDI")
    )


def test_store_words_counted_from_micro() -> None:
    assert LM1_V1.by_mnemonic("LD").store_words == 3  # 0, addr out; value back
    assert LM1_V1.by_mnemonic("ST").store_words == 3  # 1, addr, value out
    assert LM1_V1.by_mnemonic("LDI").store_words == 0
    assert LM1_EXT.by_mnemonic("LDP").store_words == 6  # two full transactions


def test_skip_cycle_is_billed_per_word_not_per_glyph() -> None:
    assert MICRO_TICKS[Micro.SKIP_CYCLE] == 0
    assert TickModel().skip_word == 8  # ARCH §5.4
