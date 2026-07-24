"""Tests for the LM-1 assembler (randomfun2026solvers.lm1.asm)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1.asm import (  # noqa: E402
    RING_SLACK_MAX,
    RING_SLACK_MIN,
    AsmError,
    assemble,
    check_ring_writeback,
    strip_comment,
)
from randomfun2026solvers.lm1.isa import LM1_EXT, LM1_V1, Isa, Micro, Op, Sem  # noqa: E402


# ── words, positions, P ─────────────────────────────────────────────────────
def test_words_are_opcode_then_operand() -> None:
    prog = assemble("LDI 42\nOUT\nHALT")
    ldi, out, halt = (LM1_EXT.by_mnemonic(m) for m in ("LDI", "OUT", "HALT"))
    assert prog.words == (ldi.code, 42, out.code, halt.code)
    assert prog.P == 4
    assert [i.pos for i in prog.instrs] == [0, 2, 3]


def test_ring_capacity_is_p_plus_slack() -> None:
    prog = assemble("NOP\nHALT")
    assert prog.ring_capacity == (prog.P + RING_SLACK_MIN, prog.P + RING_SLACK_MAX)


def test_empty_program_is_rejected() -> None:
    with pytest.raises(AsmError, match="empty program"):
        assemble("; nothing but a comment\n")


# ── comments, labels, directives ────────────────────────────────────────────
def test_comments_and_blank_lines_are_ignored() -> None:
    assert assemble("; head\n\nHALT  # tail\n").P == 1


def test_comment_delimiter_inside_a_string_is_kept() -> None:
    assert strip_comment('.ascii "a;b#c"  ; real comment') == '.ascii "a;b#c"  '
    prog = assemble('.ascii "a;b"\nHALT')
    assert prog.P == 3 * 3 + 1


def test_label_on_the_same_line_as_an_instruction() -> None:
    prog = assemble("start: NOP\nHALT")
    assert prog.labels == {"start": 0}


def test_duplicate_label_is_rejected() -> None:
    with pytest.raises(AsmError, match="duplicate label"):
        assemble("a: NOP\na: HALT")


def test_equ_defines_a_symbolic_address() -> None:
    prog = assemble(".equ SLOT 7\nLDI 1\nST SLOT\nHALT")
    st = next(i for i in prog.instrs if i.mnemonic == "ST")
    assert st.operand == 7
    assert prog.equs == {"SLOT": 7}


def test_ascii_directive_expands_to_ldi_out_pairs() -> None:
    prog = assemble('.ascii "hi"\nHALT')
    assert [i.mnemonic for i in prog.instrs] == ["LDI", "OUT", "LDI", "OUT", "HALT"]
    assert [i.operand for i in prog.instrs if i.mnemonic == "LDI"] == [104, 105]


def test_ascii_escapes() -> None:
    prog = assemble('.ascii "a\\"b\\\\c"\nHALT')
    assert [i.operand for i in prog.instrs if i.mnemonic == "LDI"] == [97, 34, 98, 92, 99]


def test_emit_directive_takes_integers() -> None:
    prog = assemble(".emit 1 2 3\nHALT")
    assert [i.operand for i in prog.instrs if i.mnemonic == "LDI"] == [1, 2, 3]


def test_char_literal_operand() -> None:
    assert assemble("LDI 'A'\nHALT").instrs[0].operand == 65


def test_unknown_directive() -> None:
    with pytest.raises(AsmError, match="unknown directive"):
        assemble(".nope 1\nHALT")


# ── jump resolution (ARCH §5.3) ─────────────────────────────────────────────
def test_forward_jump_skips_the_words_in_between() -> None:
    prog = assemble("JMP done\nNOP\nNOP\ndone: HALT")
    jmp = prog.instrs[0]
    # after the 2-word JMPF the phase is 2; the target is at word 4.
    assert jmp.operand == 2


def test_backward_jump_costs_a_whole_lap() -> None:
    # loop body starts at word 0; the JMP occupies words 1..2, so after it the
    # phase is 3 and P is 4 -> n = (0 - 3) mod 4 = 1 = P - L with L = 3.
    prog = assemble("top: NOP\nJMP top\nHALT")
    jmp = prog.instrs[1]
    assert prog.P == 4
    assert jmp.operand == 1
    lap_back = prog.P - (jmp.pos + 2)
    assert jmp.operand == lap_back % prog.P


def test_zero_skip_is_a_fall_through() -> None:
    prog = assemble("JMP next\nnext: HALT")
    assert prog.instrs[0].operand == 0


def test_branches_resolve_labels_too() -> None:
    prog = assemble("BRZ z\nNOP\nz: HALT")
    assert prog.instrs[0].operand == 1


def test_raw_skip_count_is_accepted_and_range_checked() -> None:
    assert assemble("JMP 1\nHALT").instrs[0].operand == 1
    with pytest.raises(AsmError, match="out of range"):
        assemble("JMP 99\nHALT")


def test_unknown_label_and_symbol() -> None:
    with pytest.raises(AsmError, match="unknown label 'nowhere'"):
        assemble("JMP nowhere\nHALT")
    with pytest.raises(AsmError, match="unknown symbol 'nowhere'"):
        assemble("LDI nowhere\nHALT")


def test_label_as_a_data_operand_gives_its_word_index() -> None:
    prog = assemble("LDI here\nhere: HALT")
    assert prog.instrs[0].operand == 2


# ── operand arity and the ROM's non-negative literals ───────────────────────
def test_missing_and_extra_operands() -> None:
    with pytest.raises(AsmError, match="needs an operand"):
        assemble("LDI\nHALT")
    with pytest.raises(AsmError, match="takes no operand"):
        assemble("HALT 1")


def test_unknown_mnemonic() -> None:
    with pytest.raises(AsmError, match="unknown mnemonic"):
        assemble("FROB 1\nHALT")


def test_negative_operand_is_rejected_by_default() -> None:
    with pytest.raises(AsmError, match="negative"):
        assemble("LDI -1\nHALT")
    prog = assemble("LDI -1\nHALT", allow_negative_words=True)
    assert prog.words[1] == -1


# ── the §5.3 ring-write-back invariant ──────────────────────────────────────
def test_check_passes_for_the_shipped_isas() -> None:
    check_ring_writeback(LM1_V1)
    check_ring_writeback(LM1_EXT)


def test_unpaired_ring_read_is_caught() -> None:
    # An opcode that reads more ring words than it declares operands consumes a
    # word without writing it back, erasing the program on the first lap.
    # `Op`'s own validator rejects this, so bypass it to test the asm-side gate.
    eat = Op.model_construct(
        code=0,
        mnemonic="EAT",
        operands=1,
        description="reads the ring twice for one operand word",
        micro=(Micro.RING_READ, Micro.RING_READ, Micro.MOV),
        sem=Sem.NOP,
        ext=False,
        aliases=(),
    )
    with pytest.raises(AsmError, match="ring read"):
        check_ring_writeback(Isa.model_construct(name="bad", ops=(eat,)))


def test_jump_without_a_skip_cycle_is_caught() -> None:
    leaky = Isa(
        name="leaky",
        ops=(
            Op(
                code=0,
                mnemonic="JMPF",
                operands=1,
                description="skips words but never sends them back",
                micro=(Micro.RING_READ, Micro.BP_LOAD),
                sem=Sem.JUMP,
            ),
        ),
    )
    with pytest.raises(AsmError, match="skip cycle"):
        check_ring_writeback(leaky)
    with pytest.raises(AsmError, match="skip cycle"):
        assemble("JMP 0", isa=leaky)


# ── reporting ───────────────────────────────────────────────────────────────
def test_report_mentions_p_capacity_and_ext_usage() -> None:
    report = assemble("LDI 3\nDIVI 3\nHALT", name="demo").report()
    assert "P=5" in report
    assert "ring capacity 7..9" in report
    assert "DIVI" in report


def test_ext_ops_is_empty_for_a_v1_program() -> None:
    assert assemble("LDI 1\nOUT\nHALT").ext_ops == ()


def test_v1_isa_rejects_extension_mnemonics() -> None:
    with pytest.raises(AsmError, match="unknown mnemonic"):
        assemble("DIVI 2\nHALT", isa=LM1_V1)


def test_listing_and_static_ticks() -> None:
    prog = assemble("top: NOP\nJMP top")
    assert "top:" in prog.listing()
    assert prog.static_ticks() > 0
