"""Tests for the LM-1 emulator (randomfun2026solvers.lm1.emulator).

One test per ISA opcode, plus the pieces of LM-1 that are easy to get wrong: the
ring-as-PC, ``A`` dying on every fetch, 64-bit wraparound, floored division,
B's-sign modulo, the STORE wire protocol and round-based input gating.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1.asm import assemble  # noqa: E402
from randomfun2026solvers.lm1.emulator import (  # noqa: E402
    WORD_MAX,
    WORD_MIN,
    Emulator,
    EmulatorError,
    InputWithheld,
    Round,
    RunResult,
    floor_div,
    sign_mod,
    wrap,
)
from randomfun2026solvers.lm1.isa import LM1_EXT, TickModel  # noqa: E402
from randomfun2026solvers.lm1.store import DictStore, SpillRing, StoreError  # noqa: E402


def run(source: str, *, input: Sequence[int] = (), **kw: object) -> RunResult:
    return Emulator(assemble(source), **kw).run(input=input)  # type: ignore[arg-type]


def out(source: str, *, input: Sequence[int] = ()) -> tuple[int, ...]:
    return run(source, input=input).output


# ── one test per opcode ─────────────────────────────────────────────────────
def test_nop() -> None:
    res = run("LDI 5\nNOP\nOUT\nHALT")
    assert res.output == (5,)  # NOP leaves ACC alone


def test_ldi() -> None:
    assert out("LDI 7\nOUT\nHALT") == (7,)


def test_in() -> None:
    assert out("IN\nOUT\nIN\nOUT\nHALT", input=[3, 4]) == (3, 4)


def test_out_preserves_acc() -> None:
    # ARCH §6: the `W` `s` `W` sandwich means OUT is free of a reload.
    assert out("LDI 9\nOUT\nOUT\nHALT") == (9, 9)


def test_addi() -> None:
    assert out("LDI 40\nADDI 2\nOUT\nHALT") == (42,)


def test_subi() -> None:
    assert out("LDI 40\nSUBI 2\nOUT\nHALT") == (38,)


def test_subi_is_acc_minus_operand_not_the_reverse() -> None:
    # The easy bug: ARCH's `-` computes A - B with A = n, so it needs the `N`
    # (or a `W`); getting it wrong silently negates every subtraction.
    assert out("LDI 0\nSUBI 5\nOUT\nHALT") == (-5,)


def test_muli() -> None:
    assert out("LDI 6\nMULI 7\nOUT\nHALT") == (42,)


def test_ld_and_st() -> None:
    res = run("LDI 11\nST 3\nLDI 0\nLD 3\nOUT\nHALT")
    assert res.output == (11,)
    assert res.store_cells == {3: 11}


def test_st_preserves_acc() -> None:
    assert out("LDI 5\nST 0\nOUT\nHALT") == (5,)


def test_unwritten_cells_read_as_zero() -> None:
    assert out("LDI 9\nLD 7\nOUT\nHALT") == (0,)


def test_add_mem() -> None:
    assert out("LDI 10\nST 0\nLDI 32\nADD 0\nOUT\nHALT") == (42,)


def test_sub_mem_is_acc_minus_cell() -> None:
    assert out("LDI 10\nST 0\nLDI 3\nSUB 0\nOUT\nHALT") == (-7,)


def test_jmpf_skips_words_without_executing_them() -> None:
    res = run("JMP done\nLDI 1\nOUT\ndone: LDI 2\nOUT\nHALT")
    assert res.output == (2,)
    assert res.words_skipped == 3  # the LDI's two words plus the OUT


def test_brz_taken_and_not_taken() -> None:
    src = "IN\nBRZ zero\nLDI 1\nOUT\nHALT\nzero: LDI 0\nOUT\nHALT"
    assert out(src, input=[0]) == (0,)
    assert out(src, input=[5]) == (1,)


def test_brn_only_on_negative() -> None:
    src = "IN\nBRN neg\nLDI 1\nOUT\nHALT\nneg: LDI 2\nOUT\nHALT"
    assert out(src, input=[-1]) == (2,)
    assert out(src, input=[0]) == (1,)
    assert out(src, input=[1]) == (1,)


def test_dsp_records_a_display_write() -> None:
    res = run("LDI 5\nDSP 1\nHALT")
    assert res.display_writes == ((1, 5),)


def test_halt_stops_the_machine() -> None:
    res = run("HALT\nLDI 1\nOUT")
    assert res.halted and res.reason == "halted" and res.output == ()


# ── extension opcodes ───────────────────────────────────────────────────────
def test_divi_is_floored() -> None:
    assert out("LDI 7\nDIVI 2\nOUT\nHALT") == (3,)
    assert out("LDI 7\nNEG\nDIVI 2\nOUT\nHALT") == (-4,)


def test_modi_takes_the_divisors_sign() -> None:
    assert out("LDI 7\nMODI 3\nOUT\nHALT") == (1,)
    assert out("LDI 7\nNEG\nMODI 3\nOUT\nHALT") == (2,)


def test_mul_mem() -> None:
    assert out("LDI 6\nST 0\nLDI 7\nMUL 0\nOUT\nHALT") == (42,)


def test_ldp_and_stp_are_indexed() -> None:
    # cell 0 is the pointer; write 99 through it, then read it back.
    res = run("LDI 5\nST 0\nLDI 99\nSTP 0\nLDI 0\nLDP 0\nOUT\nHALT")
    assert res.output == (99,)
    assert res.store_cells == {0: 5, 5: 99}


def test_indirection_needs_the_spill_pipe() -> None:
    res = run("LDI 5\nST 0\nLDI 99\nSTP 0\nHALT")
    assert res.spill_high_water == 1  # exactly one parked word


def test_neg() -> None:
    assert out("LDI 5\nNEG\nOUT\nHALT") == (-5,)


def test_push_and_pop() -> None:
    res = run("LDI 1\nPUSH\nLDI 2\nPUSH\nPOP\nOUT\nPOP\nOUT\nHALT")
    assert res.output == (2, 1)  # LIFO
    assert res.spill_high_water == 2


def test_pop_from_empty_spill_is_an_error() -> None:
    with pytest.raises(StoreError, match="empty spill"):
        run("POP\nHALT")


def test_every_opcode_has_a_handler() -> None:
    for op in LM1_EXT:
        source = f"{op.mnemonic} 0" if op.operands else op.mnemonic
        # Preamble: a pointer cell for LDP/STP and a parked word for POP.
        prog = assemble(f"LDI 1\nST 0\nPUSH\n{source}\nHALT")
        Emulator(prog).run()  # must not raise "no handler"


# ── the ring is the PC (ARCH §5.3) ──────────────────────────────────────────
def test_words_come_out_in_program_order_and_recirculate() -> None:
    prog = assemble("top: LDI 1\nOUT\nJMP top")
    em = Emulator(prog)
    res = em.run(max_instructions=9)
    assert res.output == (1, 1, 1)
    assert list(em.words) == list(prog.words)  # nothing was consumed


def test_backward_jump_is_a_full_lap_of_skips() -> None:
    prog = assemble("top: NOP\nJMP top")
    em = Emulator(prog)
    em.run(max_instructions=4)  # 2 iterations
    lap = prog.instrs[1].operand
    assert lap is not None
    assert em.words_skipped == 2 * lap


def test_phase_wraps_at_p() -> None:
    prog = assemble("NOP\nNOP\nNOP")
    em = Emulator(prog)
    em.run(max_instructions=prog.P)
    assert em.phase == 0


def test_a_word_that_is_not_an_opcode_is_a_fault() -> None:
    prog = assemble("LDI 900\nHALT")  # 900 is data, but skip onto it
    em = Emulator(prog)
    em.phase = 1
    with pytest.raises(EmulatorError, match="not an opcode"):
        em.run()


# ── register model (ARCH §5.1) ──────────────────────────────────────────────
def test_fetch_clobbers_a_but_not_the_accumulator() -> None:
    em = Emulator(assemble("LDI 7\nNOP\nHALT"))
    em.step()  # LDI 7
    assert (em.a, em.b) == (7, 7)
    em.step()  # NOP — the fetch alone overwrites A with the opcode word
    assert em.a == LM1_EXT.by_mnemonic("NOP").code
    assert em.b == 7  # ACC survives every fetch (ARCH §5.1)


def test_acc_is_the_only_state_that_survives() -> None:
    # A is whatever the last fetch put there; only B carries values forward.
    em = Emulator(assemble("IN\nNOP\nOUT\nHALT"))
    res = em.run(input=[123])
    assert res.output == (123,)
    assert em.b == 123


# ── arithmetic edge cases (SPEC.md) ─────────────────────────────────────────
def test_wraparound_is_signed_64_bit() -> None:
    assert wrap(WORD_MAX + 1) == WORD_MIN
    assert wrap(WORD_MIN - 1) == WORD_MAX
    assert out("LDI 2\nMULI 4611686018427387904\nOUT\nHALT") == (WORD_MIN,)


def test_floor_div_matches_spec() -> None:
    assert floor_div(7, 2) == (3, 1)
    assert floor_div(-7, 2) == (-4, 1)
    assert floor_div(7, -2) == (-4, -1)
    assert floor_div(5, 0) == (0, 5)  # A = 0, B keeps the dividend
    for a in (-9, -1, 0, 1, 9):
        for b in (-4, -1, 1, 4):
            q, rem = floor_div(a, b)
            assert q * b + rem == a  # SPEC.md's invariant


def test_sign_mod_matches_spec() -> None:
    assert sign_mod(7, 3) == 1
    assert sign_mod(-7, 3) == 2  # takes B's sign
    assert sign_mod(7, -3) == -2
    assert sign_mod(7, 0) == 0


# ── STORE: the memory problem's wire protocol (ARCH §4.1) ───────────────────
def test_store_wire_protocol_read_and_write() -> None:
    store = DictStore()
    store.send(1)  # WRITE
    store.send(4)
    store.send(77)
    store.send(0)  # READ
    store.send(4)
    assert store.recv() == 77
    assert store.words_exchanged == 6


def test_store_read_of_untouched_cell_is_zero() -> None:
    store = DictStore()
    store.send(0)
    store.send(9)
    assert store.recv() == 0


def test_store_rejects_a_bad_request_opcode() -> None:
    store = DictStore()
    with pytest.raises(StoreError, match="opcode"):
        store.send(2)


def test_store_recv_with_no_reply_pending() -> None:
    with pytest.raises(StoreError, match="response pipe empty"):
        DictStore().recv()


def test_store_is_swappable_and_preseeded() -> None:
    store = DictStore({5: 42}, size=8)
    assert out_with_store("LD 5\nOUT\nHALT", store) == (42,)
    with pytest.raises(StoreError, match="outside"):
        out_with_store("LDI 1\nST 9\nHALT", DictStore(size=8))


def out_with_store(source: str, store: DictStore) -> tuple[int, ...]:
    return Emulator(assemble(source), store=store).run().output


def test_spill_ring_is_lifo() -> None:
    spill = SpillRing()
    spill.push(1)
    spill.push(2)
    assert (spill.pop(), spill.pop()) == (2, 1)
    assert spill.high_water == 2


# ── round-based input gating (GRADING.md) ───────────────────────────────────
def test_round_two_input_arrives_only_after_round_one_output() -> None:
    rounds = [Round(input=(1,), expected=(1,)), Round(input=(2,), expected=(2,))]
    prog = assemble("top: IN\nOUT\nJMP top")
    res = Emulator(prog).run(rounds)
    assert res.output == (1, 2)


def test_reading_ahead_of_the_gate_blocks_forever() -> None:
    rounds = [Round(input=(1,), expected=(1,)), Round(input=(2,), expected=(2,))]
    prog = assemble("IN\nIN\nOUT\nHALT")  # reads round 2 before emitting round 1
    with pytest.raises(InputWithheld, match="withheld"):
        Emulator(prog).run(rounds)


def test_a_round_expecting_no_output_unlocks_the_next_immediately() -> None:
    rounds = [Round(input=(1,), expected=()), Round(input=(2,), expected=(3,))]
    prog = assemble("IN\nST 0\nIN\nADD 0\nOUT\nHALT")
    assert Emulator(prog).run(rounds).output == (3,)


def test_running_out_of_input_stops_cleanly() -> None:
    res = Emulator(assemble("top: IN\nOUT\nJMP top")).run(input=[1, 2])
    assert res.output == (1, 2)
    assert res.reason == "input-exhausted"
    assert not res.halted  # in hardware the man simply blocks on `r`


def test_instruction_cap_is_reported() -> None:
    res = Emulator(assemble("top: NOP\nJMP top")).run(max_instructions=10)
    assert res.reason == "instruction-cap"
    assert res.instructions == 10


def test_result_matches_helper() -> None:
    rounds = [Round(input=(2,), expected=(2,))]
    res = Emulator(assemble("IN\nOUT\nHALT")).run(rounds)
    assert res.matches(rounds)


# ── tick accounting (ARCH §7.2) ─────────────────────────────────────────────
def test_ticks_are_the_sum_of_per_instruction_costs_plus_skips() -> None:
    prog = assemble("LDI 1\nOUT\nHALT")
    res = Emulator(prog).run()
    ticks = TickModel()
    expected = sum(ticks.instruction(prog.isa.by_code(i.code)) for i in prog.instrs)
    assert res.ticks == expected


def test_skipped_words_are_billed_at_the_ARCH_rate() -> None:  # noqa: N802
    prog = assemble("JMP done\nNOP\nNOP\ndone: HALT")
    res = Emulator(prog).run()
    ticks = TickModel()
    instr_cost = sum(
        ticks.instruction(prog.isa.by_code(c)) for c in (prog.words[0], prog.words[-1])
    )
    assert res.ticks == instr_cost + ticks.skip_word * res.words_skipped


def test_tick_model_is_swappable() -> None:
    prog = assemble("NOP\nHALT")
    slow = Emulator(prog, ticks=TickModel(decode=100)).run().ticks
    fast = Emulator(prog, ticks=TickModel(decode=1)).run().ticks
    assert slow > fast
