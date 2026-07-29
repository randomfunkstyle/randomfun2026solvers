"""The four STREAM arms the CNN needs and matmul did not.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §4.2a.

The existing eight arms fill rings from the *input room* and emit to the *output
room*; a training loop needs to push CPU-computed scalars in, rotate ring B to a tap
offset, read a partial sum back at the CPU, and update weights in place without
routing 190 words through the CPU. This file pins the semantics; the grid that
implements them is pinned in the same file's slow tier once it exists.

``trie_bits`` is a property of the *unit*, not of the word: a depth-3 unit (the
default, ``matmul``'s own width) and a depth-4 unit (this machine's, with the four
new arms) are never mixed in one decode. See ``StreamUnit``'s docstring in
``store.py`` for why no single decode can serve both — this task's own
NEEDS_CONTEXT round found that widening to mod-16 corrupts real, already-shipped
``matmul`` words (``FILLA`` with an odd argument), and that a mod-8 decode can
never reach the new arms' codes at all. Hence: two widths, chosen at construction,
never one decode trying to serve both.
"""

from __future__ import annotations

import os
import shutil
from collections import deque
from pathlib import Path

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import stream
from randomfun2026solvers.lm1.asm import assemble
from randomfun2026solvers.lm1.emulator import Emulator
from randomfun2026solvers.lm1.store import StoreError, StreamUnit

REPO = Path(__file__).parents[1]
LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)
reference_sweeps = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the reference-interpreter sweeps",
)


def _unit(trie_bits: int = 3, lr_shift: int | None = None) -> StreamUnit:
    return StreamUnit(
        read_input=lambda: 0, emit=lambda v: None, trie_bits=trie_bits, lr_shift=lr_shift
    )


def _updb_words(n: int) -> int:
    return 16 * n + StreamUnit.CODES["UPDB"]


def _model_updb(
    scalar: int, weights: list[int], grads: list[int], *, shift: int
) -> tuple[list[int], list[int]]:
    """Run the *model's* PUSHA/UPDB pair and return ``(ring_b, p2)``. The oracle."""
    u = _unit(trie_bits=4, lr_shift=shift)
    u.ring_b = deque(weights)
    u.p1 = deque(grads)
    u.command(16 * scalar + StreamUnit.CODES["PUSHA"])
    u.command(_updb_words(len(weights)))
    return list(u.ring_b), list(u.p2)


def test_new_arms_have_distinct_codes():
    codes = StreamUnit.CODES
    for arm in ("PUSHA", "ROTB", "RDP", "UPDB"):
        assert arm in codes, f"{arm} must be a STREAM arm"
    assert len(set(codes.values())) == len(codes), "two arms cannot share a command code"
    assert max(codes.values()) < 16, "a depth-4 trie has 16 leaves"


def test_pusha_puts_a_cpu_value_on_ring_a():
    u = _unit(trie_bits=4)
    u.command(16 * 42 + StreamUnit.CODES["PUSHA"])
    assert list(u.ring_a) == [42]


def test_rotb_rotates_without_consuming():
    u = _unit(trie_bits=4)
    u.ring_b = deque([1, 2, 3, 4, 5])
    u.command(16 * 2 + StreamUnit.CODES["ROTB"])
    assert list(u.ring_b) == [3, 4, 5, 1, 2], "a rotation preserves the multiset and the order"


def test_rotb_by_full_length_is_identity():
    u = _unit(trie_bits=4)
    u.ring_b = deque(range(7))
    u.command(16 * 7 + StreamUnit.CODES["ROTB"])
    assert list(u.ring_b) == list(range(7))


def test_rdp_returns_one_partial_sum_to_the_cpu():
    u = _unit(trie_bits=4)
    u.p1 = deque([111, 222])
    assert u.command(16 * 0 + StreamUnit.CODES["RDP"]) == 111
    assert list(u.p1) == [222]


def test_updb_applies_a_rank_one_update_in_place():
    """W[j] -= (a * g[j]) >> lr, with g rotating through the accumulator ring."""
    u = _unit(trie_bits=4)
    u.ring_b = deque([1000, 2000, 3000])
    u.p1 = deque([4096, 8192, 0])  # Q12 gradients 1.0, 2.0, 0.0
    u.lr_shift = 12
    u.command(16 * 3 + StreamUnit.CODES["PUSHA"])  # scalar a = 3
    u.command(16 * 3 + StreamUnit.CODES["UPDB"])
    assert list(u.ring_b) == [1000 - 3, 2000 - 6, 3000 - 0]
    assert list(u.p2) == [4096, 8192, 0], "the gradients must circulate, not be consumed"


def test_mac_still_does_what_matmul_relies_on():
    """A regression guard: the depth-4 trie must not renumber the existing arms' behaviour."""
    u = _unit(trie_bits=4)
    u.ring_a = deque([2])
    u.ring_b = deque([10, 20, 30])
    u.p2 = deque([0, 0, 0])  # MAC reads its running sum from P2 (ZEROC's ring), not P1
    u.command(16 * 3 + StreamUnit.CODES["MAC"])
    assert list(u.ring_b) == [10, 20, 30], "MAC rotates B back to its start after a full lap"
    assert u.macs == 3


@pytest.mark.parametrize("n", [0, 1, 2, 7, 8, 35, 100])
def test_trie3_decodes_the_original_eight_arms_correctly_including_odd_args(n: int):
    """The actual matmul guard: this checks the *decode*, not just the code table.

    Comparing ``StreamUnit.CODES`` by hand only proves the labels didn't move.
    This proves that a depth-3 unit — the one ``matmul`` actually runs against —
    still recovers the right argument for every original arm, at several sizes
    including 35: the odd argument from the real ``FILLA`` word (283) that this
    task's design notes used to prove a mod-16 decode corrupts existing behaviour.
    A mod-8 decode, unchanged, must not.
    """
    codes = StreamUnit.CODES

    u = _unit()
    u.command(8 * n + codes["FILLA"])
    assert len(u.ring_a) == n

    u = _unit()
    u.command(8 * n + codes["FILLB"])
    assert len(u.ring_b) == n

    u = _unit()
    u.command(8 * n + codes["ZEROC"])
    assert list(u.p2) == [0] * n

    u = _unit()
    u.ring_b = deque(range(n))
    u.command(8 * n + codes["DRAINB"])
    assert len(u.ring_b) == 0

    u = _unit()
    u.p1 = deque(range(n))
    u.command(8 * n + codes["FWD"])
    assert list(u.p2) == list(range(n))

    emitted: list[int] = []
    u = StreamUnit(read_input=lambda: 0, emit=emitted.append)
    u.p1 = deque(range(n))
    u.command(8 * n + codes["EMIT"])
    assert emitted == list(range(n))

    u = _unit()
    u.ring_a = deque([2])
    u.ring_b = deque(range(1, n + 1))
    u.p2 = deque([0] * n)  # MAC reads its running sum from P2 (ZEROC's ring), not P1
    u.command(8 * n + codes["MAC"])
    assert list(u.ring_b) == list(range(1, n + 1)), "MAC must rotate B back after one full lap"
    assert u.macs == n

    u = _unit()
    u.command(8 * n + codes["RDIN"])
    assert u.recv() == 0, "RDIN's argument is unused; only its code needs to decode correctly"


def test_trie3_rejects_a_code_the_trie_does_not_have():
    """A depth-3 unit must refuse a code outside its own trie, not misdecode one.

    There is no *word* that exercises this through :meth:`StreamUnit.command`: a
    3-bit ``divmod`` can never produce a code >= 8 in the first place (``word % 8``
    is always < 8, for any word) — which is precisely why a depth-3 unit handed a
    depth-4 program's word can't notice: it silently decodes to some other, wrong,
    original arm instead (see ``test_pusha_puts_a_cpu_value_on_ring_a``'s 3-bit
    encoding, ``EMIT(43)`` instead of ``PUSHA(42)``, in this task's design notes).
    So this test exercises the seam one level down, at the already-decoded
    ``(code, arg)`` pair, which is where the real guard has to live.
    """
    u = _unit()
    with pytest.raises(StoreError, match="3-bit"):
        u._dispatch(StreamUnit.CODES["PUSHA"], 0)


def test_rdp_reply_reaches_rcv_through_the_real_instruction_pair():
    """RDP must answer through RCV, not only through ``command()``'s return value.

    Every other test in this file calls ``command()`` directly, so a bug where an
    arm answers *only* as ``command()``'s Python return value — and never enqueues
    onto ``self._replies`` — would pass every one of them and still break the real
    machine: a program assembles ``SND`` (send the command word) then ``RCV`` (read
    the unit's next reply word), exactly ``RDIN``'s own idiom, and ``RCV`` reads
    only from ``_replies`` (:meth:`StreamUnit.recv`). This test goes through that
    actual instruction pair, via the emulator's own ``Sem.STREAM_SEND`` /
    ``Sem.STREAM_RECV`` handlers — the same path ``matmul`` and Task 4's training
    program both run on — rather than through ``command()``.

    ``emulator.py``'s ``stream`` property is the one production ``StreamUnit()``
    call site, and it builds a ``trie_bits=3`` unit (the emulator does not thread a
    wider trie through yet — a later task's job). RDP lives only on a depth-4 trie,
    so this test swaps in a unit built the same production way, just at
    ``trie_bits=4``, before the program runs; everything downstream of that —
    ``SND``, ``RCV``, ``em.stream.send``/``recv`` — is the real, unmodified path.
    """
    word = 16 * 0 + StreamUnit.CODES["RDP"]
    em = Emulator(assemble(f"LDI {word}\nSND\nRCV\nOUT\nHALT"))
    em._stream = StreamUnit(em._next_input, em._emit, trie_bits=4)
    em.stream.p1 = deque([111, 222])

    res = em.run(input=[])

    assert res.output == (111,), "the reply must arrive through RCV, not just command()'s return"
    assert list(em.stream.p1) == [222]


def test_pusha_rotb_updb_have_no_reply_to_lose():
    """The other three new arms answer nothing, so they have no reply path to break.

    Confirmed rather than assumed: none of ``_pusha``, ``_rotb``, ``_updb`` return
    a value or touch ``self._replies`` (checked by reading ``store.py``), so unlike
    ``RDP`` there is no second path for their effects to reach and no way for a
    ``command()``-only test to have missed one. This test pins that a program
    driving them through the real ``SND`` path — with no matching ``RCV`` — runs to
    completion instead of blocking, which is what "no reply" has to mean in
    practice.
    """
    codes = StreamUnit.CODES
    program = "\n".join(
        [
            f"LDI {16 * 5 + codes['PUSHA']}",
            "SND",
            f"LDI {16 * 2 + codes['ROTB']}",
            "SND",
            f"LDI {16 * 2 + codes['UPDB']}",
            "SND",
            "HALT",
        ]
    )
    em = Emulator(assemble(program))
    em._stream = StreamUnit(em._next_input, em._emit, trie_bits=4)
    em.stream.ring_b = deque([100, 200])
    em.stream.p1 = deque([4096, 0])

    res = em.run(input=[])

    assert res.halted
    assert list(em.stream.ring_a) == [5]
    assert em.stream._replies == deque(), "none of these three arms may queue a reply"


# ── the grid tier: the arms as drawn, not as modelled ────────────────────────
# `UPDB` is the only new arm with real register pressure — two reads, a multiply
# and a shift with two hands and an unreadable backpack — so it is probed alone
# before it is placed, the way `dsprelay`'s relay and the store selector were. The
# probe's room has exactly one incoming and one outgoing pipe, which is what takes
# geometry out of the question and leaves only the glyph order.
UPDB_CASES = [
    (3, [1000, 2000, 3000], [4096, 8192, 0]),  # Q12 gradients 1.0, 2.0, 0.0
    (0, [], []),  # a zero count must run the body zero times
    (-5, [10, -20], [4096, -4096]),  # floored, so a negative product survives
    (1 << 18, [0], [7]),  # the ring-B write's own shape: a << shift
    (7, [-1], [-1]),  # a negative weight and a negative gradient
]


def _probe_input(scalar: int, weights: list[int], grads: list[int]) -> list[int]:
    """One command word, then one ``(a, g, b)`` lap per weight.

    Feeding the scalar once per lap is not a shortcut around the arm: it is exactly
    what ring A hands it, because the drawn body pops the scalar and pushes it
    straight back every iteration (:func:`stream.updb_body`).
    """
    words = [_updb_words(len(weights))]
    for weight, grad in zip(weights, grads, strict=True):
        words += [scalar, grad, weight]
    return words


def test_the_drawn_updb_body_is_the_shift_the_program_declares():
    """18, and it has to be *drawn*: nothing about a command word carries it."""
    from randomfun2026solvers import mnist_cnn
    from randomfun2026solvers.lm1 import asm

    assert stream.UPDB_SHIFT == 18
    body = stream.updb_body()
    assert body == "rsMrs*M9W}}Mr-s"
    assert body.count("}") == 2 and "9" in body, "9 twice, because floored shifts compose"

    # The program says which shift it was written against; the block must draw that
    # one or refuse. A mismatch is wrong arithmetic with nothing to catch it.
    program = assemble(mnist_cnn.emit_source(lr_shift=6, single_step=True))
    assert program.unit == "stream4", "the depth-4 unit is the one with UPDB on it"
    assert program.equs[asm.STREAM_LR_SHIFT_EQU] == stream.UPDB_SHIFT


def test_the_updb_body_reads_and_writes_in_the_order_its_rows_impose():
    """The body string *is* the arm's row map, so its glyph order is a contract.

    Rows increase downward and a glyph's row picks its pipe (§7.1), so the sequence
    below is what forces the depth-4 unit's west wall to carry all four incoming
    pipes: ring A's return is read between the accumulator's and ring B's, and a
    pipe read between two west-wall pipes cannot itself be on the north or south
    wall — the Manhattan distances never cross the right way round.
    """
    body = stream.updb_body()
    pipe_ops = [(i, ch) for i, ch in enumerate(body) if ch in "rs"]
    assert [ch for _i, ch in pipe_ops] == ["r", "s", "r", "s", "r", "s"]
    (a_ret, _), (a_fwd, _), (p1, _), (p2, _), (b_ret, _), (b_fwd, _) = pipe_ops
    assert a_ret < a_fwd < p1 < p2 < b_ret < b_fwd
    assert a_fwd == a_ret + 1, "the scalar goes straight back: ring A is unchanged"
    assert p2 == p1 + 1, "the gradient circulates, it is not consumed"
    assert b_fwd == b_ret + 2, "one `-` between reading the weight and writing it"


@pytest.mark.parametrize("shift", [18, 12, 6])
@pytest.mark.parametrize(("scalar", "weights", "grads"), UPDB_CASES)
def test_updb_probe_grid_matches_the_model(
    shift: int, scalar: int, weights: list[int], grads: list[int]
):
    """The drawn arm against :class:`StreamUnit`, on an engine, glyph for glyph.

    The probe emits every ``s`` into the same ``O`` room, so the expected output is
    an *interleaving* — scalar, gradient, updated weight, once per lap. That is
    deliberate: emitting only the weight would pass for an arm that did the right
    arithmetic in the wrong place, and this does not.
    """
    rows = stream.build_updb_probe(shift)

    # Lesson from Task 5's R1 round: assert the engine saw the construct before
    # asserting anything about behaviour. A probe whose room or pipes did not parse
    # would otherwise "pass" while pinning nothing at all.
    engine = FastLittleman("\n".join(rows))
    assert [room.kind for room in engine.rooms] == ["compute", "input", "output"]
    assert len(engine.pipes) == 2, "one pipe in, one pipe out — that is the whole point"

    result = engine.run(_probe_input(scalar, weights, grads), max_ticks=200_000)
    assert result.fatal is None, result.fatal
    assert result.halted

    ring_b, p2 = _model_updb(scalar, weights, grads, shift=shift)
    want: list[int] = []
    for weight, grad in zip(ring_b, p2, strict=True):
        want += [scalar, grad, weight]
    assert result.output == want
    assert want == stream.updb_probe_model(scalar, weights, grads, shift=shift)


@node_required
@reference_sweeps
def test_the_updb_probe_runs_the_same_on_the_reference_interpreter(tmp_path):
    """``fast_littleman`` is an independent engine; the bundled wasm is the judge's."""
    from randomfun2026solvers.littleman import Littleman

    rows = stream.build_updb_probe()
    path = tmp_path / "updb-probe.man"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    lm = Littleman()
    analysis = lm.analyze(path)
    assert len(analysis.rooms) == 3
    assert len(analysis.pipes) == 2

    for scalar, weights, grads in UPDB_CASES:
        words = " ".join(str(v) for v in _probe_input(scalar, weights, grads))
        out = list(lm.tick(path, 4000, input=words).output)
        assert out == stream.updb_probe_model(scalar, weights, grads), (scalar, out)
