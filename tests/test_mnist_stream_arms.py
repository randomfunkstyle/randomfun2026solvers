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




def test_the_drawn_updb_body_is_the_shift_the_program_declares():
    """18, and it has to be *drawn*: nothing about a command word carries it."""
    from randomfun2026solvers import mnist_cnn
    from randomfun2026solvers.lm1 import asm

    assert stream.UPDB_SHIFT == 18
    body = stream.updb_body()
    assert body == "rMrWsWs*M9W}}Mr-s"
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
    assert [ch for _i, ch in pipe_ops] == ["r", "r", "s", "s", "r", "s"]
    (a_ret, _), (p1, _), (a_fwd, _), (p2, _), (b_ret, _), (b_fwd, _) = pipe_ops
    # Both reads first, then both pushes: the *interleaved* order. It costs two `W`
    # glyphs to shuffle a value out of B, and it is the only one of the five
    # admissible orders that lets the whole block be routed (see the planarity tests).
    assert a_ret < p1 < a_fwd < p2 < b_ret < b_fwd
    assert body[a_ret + 1] == "M" and body[p1 + 1] == "W" and body[a_fwd + 1] == "W"
    assert b_fwd == b_ret + 2, "one `-` between reading the weight and writing it"
    assert list(stream.UPDB_BANDS) == ["a_ret", "p1", "a_fwd", "p2", "b_ret", "b_fwd"]


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

    words = stream.updb_probe_input(scalar, weights, grads)
    assert words[0] == _updb_words(len(weights)), "the probe decodes a real command word"
    result = engine.run(words, max_ticks=200_000)
    assert result.fatal is None, result.fatal
    assert result.halted

    # The oracle is the model, not the builder's own idea of the answer: read the
    # updated weights and the circulated gradients back off StreamUnit and lay them
    # out in the arm's own send order.
    ring_b, p2 = _model_updb(scalar, weights, grads, shift=shift)
    sends = {"p2": p2, "a_fwd": [scalar] * len(ring_b), "b_fwd": ring_b}
    want = [
        sends[band][i]
        for i in range(len(ring_b))
        for band in stream.UPDB_BANDS
        if band in sends
    ]
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
        words = " ".join(str(v) for v in stream.updb_probe_input(scalar, weights, grads))
        out = list(lm.tick(path, 4000, input=words).output)
        assert out == stream.updb_probe_model(scalar, weights, grads), (scalar, out)


# ── the depth-4 unit, drawn ──────────────────────────────────────────────────
def test_depth_three_unit_is_unchanged():
    """matmul's arm codes must not move at all: its grid is shipped and judged."""
    codes = stream.arm_codes(trie_bits=3)
    for arm in ("EMIT", "FILLB", "ZEROC", "FILLA", "FWD", "DRAINB", "MAC", "RDIN"):
        assert codes[arm] == StreamUnit.CODES[arm]
    assert sorted(codes.values()) == list(range(8))


def test_the_depth_four_codes_are_read_off_the_trie_not_assigned():
    """Twelve arms on sixteen leaves, and *which* four are spare is not free either.

    The codes are the leaves' own trie paths, so the arms have to sit on the leaves
    whose paths spell the numbers :attr:`StreamUnit.CODES` already publishes — eight
    of them because ``matmul`` ships them, four because Task 3's model has been
    tested against them since. That is the property that stops the table drifting
    from the drawing: move a leaf and this fails.
    """
    codes = stream.arm_codes(trie_bits=4)
    assert codes == StreamUnit.CODES
    assert sorted(codes.values()) == list(range(12))

    cols = {arm: col for arm, col in stream._spec(4).cols.items()}
    assert set(cols) == set(StreamUnit.CODES)
    spare = [i for i, arm in enumerate(stream._spec(4).leaves) if not arm]
    assert len(spare) == 4, "sixteen leaves, twelve arms"


def test_the_depth_four_unit_reuses_the_rings_and_adds_no_pipes():
    """Four new arms, no new hardware: that is what makes them cheap.

    Eleven pipes either way — the module docstring's number — because ``PUSHA``
    sends to ring A's fill, ``ROTB`` rotates ring B, ``RDP`` drains the accumulator
    and ``UPDB`` uses all three. A new pipe would be a new rival for every ``r`` and
    ``s`` in the unit (``ARCH.md`` §7.1), so *not* adding one is load-bearing.
    """
    assert stream.unit_pipe_count(3) == stream.EXPECTED_PIPES == 11
    assert stream.unit_pipe_count(4) == stream.EXPECTED_PIPES

    three, four = stream._spec(3), stream._spec(4)
    assert set(three.east) == set(four.east), "the same six outgoing bands"
    assert set(three.west) | set(three.south) == set(four.west), "p1 moves west, no more"
    assert four.south == ()


def test_every_depth_four_pipe_glyph_sits_on_its_own_pipes_row():
    """Rule 1 and the depth-4 shape of rule 2, as pure geometry.

    Every incoming pipe is on the west wall now, one row each, so an ``r`` on its
    row is nearest its own by ``x + 0`` against ``x + |dy|`` — the same argument
    rule 1 makes for the east wall, which is why the depth-4 unit needs no
    per-arm reasoning at all. ``cmd`` is the one exception and it loses to every
    west pipe from any column east of MAIN's own.
    """
    unit = stream.unit_interior(4)
    rows = {**unit.west, **unit.east}
    assert unit.south == {}
    for x, y, glyph, band in unit.glyphs:
        if band == "cmd":
            continue
        assert rows[band] == y, f"{glyph}@{(x, y)} claims {band}, whose row is {rows[band]}"

    # rule 2, stated: the west rows are distinct and rise in UPDB's read order.
    assert unit.west["a_ret"] < unit.west["p1"] < unit.west["b_ret"]
    assert len(set(unit.west.values())) == len(unit.west)
    assert len(set(unit.east.values())) == len(unit.east)
    # `resp` must be the topmost outgoing row. It is the one pipe that leaves the
    # block northward, so its pipe cuts the routing region, and any port above it is
    # cut off from every other one — see `stream.block_crossings`.
    assert unit.east["resp"] == min(unit.east.values())


def test_the_drawn_updb_arm_touches_the_pipes_the_probe_proved():
    """The placed arm's six glyphs, in order, against :data:`stream.UPDB_BANDS`."""
    unit = stream.unit_interior(4)
    col = stream._spec(4).cols["UPDB"] + 1  # a counted loop's body is one column east
    body = [(y, glyph, band) for x, y, glyph, band in unit.glyphs if x == col]
    assert [band for _y, _g, band in sorted(body)] == list(stream.UPDB_BANDS)
    assert [g for _y, g, _b in sorted(body)] == ["r", "r", "s", "s", "r", "s"]


def test_the_new_arms_do_what_their_glyphs_say():
    """One assertion per new arm, tying its drawn glyphs to the model's behaviour."""
    unit = stream.unit_interior(4)
    spec = stream._spec(4)
    by_arm: dict[str, list[str]] = {}
    for i, arm in enumerate(spec.leaves):
        if not arm:
            continue
        cols = {spec.col(i), spec.col(i) + 1}
        by_arm[arm] = [
            band for x, _y, _g, band in sorted(unit.glyphs, key=lambda t: t[1]) if x in cols
        ]
    # PUSHA v -> ring_a.append(v): one send, to ring A's fill, and no read at all.
    assert by_arm["PUSHA"] == ["a_fwd"]
    # ROTB n -> pop from ring B and push straight back: a rotation, no product.
    assert by_arm["ROTB"] == ["b_ret", "b_fwd"]
    # RDP -> pop one partial sum and answer it, exactly RDIN's shape on P1.
    assert by_arm["RDP"] == ["p1", "resp"]
    assert by_arm["RDIN"] == ["in", "resp"]
    # UPDB: all three rings, in the order updb_body imposes.
    assert by_arm["UPDB"] == list(stream.UPDB_BANDS)
    # and the accumulator convention the model was verified against, unchanged:
    # ZEROC seeds P2, MAC consumes P2 and produces P1, FWD recirculates, EMIT drains.
    assert by_arm["ZEROC"] == ["p2"]
    assert by_arm["MAC"] == ["a_ret", "b_ret", "b_fwd", "prod"]
    assert by_arm["FWD"] == ["p1", "p2"]
    assert by_arm["EMIT"] == ["p1", "out"]


def test_the_builder_refuses_a_width_it_cannot_place():
    """A block it cannot draw correctly must not be approximated (ARCH.md §4.4).

    Both widths are drawn and placed now, so what this pins is the *real* guard rather
    than the stopgap it replaced: a depth-3 unit has no leaf for any of the four new
    arms and refuses their decoded codes, and an undrawn width raises instead of
    falling back to a drawn one.
    """
    from randomfun2026solvers.lm1.stream import StreamError

    for bits, pipes in ((3, 10), (4, 11)):
        blk = stream.build_stream(a_slots=16, b_slots=200, c_slots=16, trie_bits=bits)
        assert blk.pipes == pipes
    # the shape the trainer actually asks for, which only the depth-4 band can hold
    assert stream.build_stream(a_slots=16, b_slots=856, c_slots=80, trie_bits=4)

    assert not ({"PUSHA", "ROTB", "RDP", "UPDB"} & set(stream.arm_codes(3)))
    three = StreamUnit(lambda: 0, lambda _v: None, trie_bits=3)
    with pytest.raises(StoreError, match="3-bit"):
        three._dispatch(StreamUnit.CODES["PUSHA"], 0)

    with pytest.raises(StreamError, match="depth 5"):
        stream.build_stream(a_slots=8, b_slots=8, c_slots=6, trie_bits=5)


def test_an_undrawable_shift_is_refused_rather_than_rounded():
    from randomfun2026solvers.lm1.stream import StreamError

    assert stream.updb_body(9) == "rMrWsWs*M9W}Mr-s"
    with pytest.raises(StreamError, match="at least 1"):
        stream.updb_body(0)


# ── the depth-4 unit, on the engine ──────────────────────────────────────────
def _unit_grid_engine(trie_bits: int) -> tuple[FastLittleman, dict[str, tuple[int, int]]]:
    """The unit alone in a room with all eleven pipes, plus where each attaches.

    ``ARCH.md`` §4.4: a mis-bound pipe produces a machine that runs to completion
    doing the wrong thing, and a stray ``|`` one cell behind an arrowhead deletes a
    whole pipe with ``analyze`` reporting one fewer. So the count is asserted, not
    argued, and every glyph is then checked against the engine's own binding.
    """
    spec = stream._spec(trie_bits)
    unit = stream.unit_interior(trie_bits)
    engine = FastLittleman("\n".join(stream.unit_interior_grid(trie_bits)))
    pad = 4
    ux, uy = pad + 2, pad + 2
    want = {band: (ux, uy + row) for band, row in unit.west.items()}
    want |= {band: (ux + spec.iw + 1, uy + row) for band, row in unit.east.items()}
    want |= {band: (ux + col, uy) for band, col in unit.north.items()}
    want |= {band: (ux + col, uy + spec.ih + 1) for band, col in unit.south.items()}
    return engine, want


@pytest.mark.parametrize("trie_bits", [3, 4])
def test_every_stream_pipe_still_binds_where_it_should(trie_bits: int):
    """The engine's own binding for all eleven pipes and every pipe glyph."""
    engine, want = _unit_grid_engine(trie_bits)
    assert len(engine.pipes) == stream.EXPECTED_PIPES, "the count drawn vs the count found"
    assert set(want) == set(stream._spec(trie_bits).west) | set(
        stream._spec(trie_bits).east
    ) | {"cmd"} | set(stream._spec(trie_bits).south)

    unit = stream.unit_interior(trie_bits)
    pad = 4
    ux, uy = pad + 2, pad + 2
    for x, y, glyph, band in unit.glyphs:
        pid = engine._bindings.get((ux + x, uy + y))
        assert isinstance(pid, int) and pid >= 0, f"{glyph}@{(x, y)} binds no pipe at all"
        pipe = engine.pipes[pid]
        attach = pipe.src_attach if glyph == "s" else pipe.dst_attach
        assert attach == want[band], (
            f"{glyph}@{(x, y)} should bind {band} at {want[band]} but got {attach}"
        )


@node_required
@reference_sweeps
@pytest.mark.parametrize("trie_bits", [3, 4])
def test_the_reference_interpreter_agrees_about_every_binding(trie_bits: int, tmp_path):
    """``Littleman.route`` — the judge's own engine — on all eleven pipes."""
    from randomfun2026solvers.littleman import Littleman

    spec = stream._spec(trie_bits)
    unit = stream.unit_interior(trie_bits)
    path = tmp_path / f"unit{trie_bits}.man"
    path.write_text("\n".join(stream.unit_interior_grid(trie_bits)) + "\n", encoding="utf-8")
    lm = Littleman()
    assert len(lm.analyze(path).pipes) == stream.EXPECTED_PIPES

    pad = 4
    ux, uy = pad + 2, pad + 2
    want = {band: (ux - 1, uy + row) for band, row in unit.west.items()}
    want |= {band: (ux + spec.iw + 2, uy + row) for band, row in unit.east.items()}
    want |= {band: (ux + col, uy - 1) for band, col in unit.north.items()}
    want |= {band: (ux + col, uy + spec.ih + 2) for band, col in unit.south.items()}
    for x, y, glyph, band in unit.glyphs:
        cells = [(c.x, c.y) for c in lm.route(path, ux + x, uy + y)]
        assert cells, f"{glyph}@{(x, y)} binds no pipe at all"
        assert want[band] in (cells[0], cells[-1]), (
            f"{glyph}@{(x, y)} should bind {band} at {want[band]} but got {cells[0], cells[-1]}"
        )


@node_required
@reference_sweeps
def test_matmul_grid_is_byte_identical(tmp_path):
    """The real regression guard, and it does not depend on any test's colour."""
    from randomfun2026solvers.lm1 import machine

    generated = "\n".join(machine.build_for("matmul").rows) + "\n"
    assert generated == Path("tasks/solutions/matmul_cpu.man").read_text(encoding="utf-8")


def test_updb_requires_ring_a_to_hold_exactly_its_scalar():
    """The one place the drawn arm is narrower than the model, now a raise.

    The model reads a remembered ``_scalar_a`` and leaves ``ring_a`` alone; the drawn
    arm has no register to remember it in (the shift needs both hands), so it rotates
    ring A once per iteration. Those agree exactly for a one-value ring and would
    disagree *silently* otherwise — the grid would multiply by whatever was queued
    ahead of the scalar and the emulator would not. So the generator asserts the
    precondition at emit time rather than leaving it to a comment.
    """
    from randomfun2026solvers import mnist_cnn

    g = mnist_cnn._Gen(lr_shift=6)
    g.p1 = ["g0", "g1"]
    with pytest.raises(mnist_cnn._RingError, match="ring A holding"):
        g.updb(2)  # nothing pushed at all

    g.aq = ["stale", "scalar"]
    with pytest.raises(mnist_cnn._RingError, match="ring A holding"):
        g.updb(2)  # a leftover word ahead of the scalar

    g.aq = ["scalar"]
    g.updb(2)  # exactly the scalar: the shape `updb_from_acc` creates
    assert g.p2 == ["g0", "g1"], "the gradients circulate onto P2"


def test_the_real_program_satisfies_that_precondition():
    """And the guard is not vacuous: `mnist_cnn`'s own emission passes it."""
    from randomfun2026solvers import mnist_cnn

    source = mnist_cnn.emit_source(lr_shift=6, single_step=True)
    assert "UPDB" in source


# ── the block's planarity, which is why it is not placed ─────────────────────
def test_the_depth_three_block_is_planar_and_the_depth_four_one_is_not():
    """The proof that stops the depth-4 placement, executable so it cannot rot.

    ``cmd`` and ``resp`` both run north to the CPU, so no pipe of the block may cross
    the top: the region its pipes can use is the unit's perimeter *as an interval*.
    The ADDER's three legs form a tree touching that interval at ``p2``, ``prod`` and
    ``p1``, cutting it into sectors, and a ring's ``fwd``/``ret`` pair is routable
    exactly when both ports fall in one sector. Splitting a pipe with a relay changes
    neither endpoint, so no relay, leg span, band depth or column allocation can move
    a pair across a sector boundary — only the perimeter order or the tree can.

    The depth-3 block is the control: it *is* placed, judged and shipped, so the model
    had better report it planar. It does.
    """
    assert stream.block_crossings(3) == []
    assert stream.block_crossings(4) == [("b_fwd", "b_ret")]


def test_the_remaining_depth_four_crossing_is_forced_by_two_arms():
    """``prod`` always sits between ring B's two ports, for two independent reasons.

    ``MAC`` must push ``b`` back before it can multiply and send the product, so
    ring B's fill is always above ``prod`` on the east wall; and ``UPDB`` reads ring B
    last, so ring B's return is always the bottom-most west port. So ``prod`` is the
    east port immediately below ring B's fill and ring B's return is the west port
    immediately after ``prod`` — cyclically, ``prod`` is between them, whatever the
    row numbers are.
    """
    spec = stream._spec(4)
    entry, body = spec.plan["MAC"].loop
    assert body == "r s*s", "read b, push it back, multiply, send the product"
    assert spec.rows["b_fwd"] < spec.rows["prod"], "the push-back precedes the product"
    assert spec.rows["b_ret"] == max(spec.rows[b] for b in spec.west)
    assert spec.rows["prod"] == max(spec.rows[b] for b in spec.east)
    order = stream.perimeter_order(4)
    i, j, k = (order.index(b) for b in ("b_fwd", "prod", "b_ret"))
    assert i < j < k, "prod is cyclically between ring B's two ports"


def test_routing_prod_through_ring_bs_relay_makes_the_block_planar():
    """The fix, checked: grow the ADDER's tree so ring B's ports become its leaves.

    ``ARCH.md`` §2.1's "one room can turn around many rings" — which is what
    :func:`stream.dual_relay_cells` was exposed for. Two men from one ``Y`` keep
    ring B's loop from blocking behind the product stream, which matters because
    §2.1's caution is about sharing a relay with a ring that can *drain*: ring B holds
    the weights and is permanently full, the product stream is not.
    """
    grown = ("p2", "prod", "p1", "b_fwd", "b_ret")
    assert stream.block_crossings(4, tree=grown) == []
    # and it is ring B's relay specifically: ring A's would leave ring B crossing.
    assert stream.block_crossings(4, tree=("p2", "prod", "p1", "a_fwd", "a_ret")) == [
        ("b_fwd", "b_ret")
    ]
    # And the dual relay this needs is already drawn and already has two loops.
    cells = stream.dual_relay_cells()
    assert list(cells.values()).count("Y") == 1
    assert list(cells.values()).count("r") == list(cells.values()).count("s") == 2


def test_resp_leaving_northward_is_what_pins_the_unit_s_row_order():
    """The cut `block_crossings` first missed, and the reason for UPDB's body order.

    ``resp`` is the only pipe that leaves the block northward, so within the block it
    is a curve from the unit's boundary to the outer boundary: it *splits* the
    perimeter, and a ring whose two ports end up on opposite sides of it is
    unroutable by any layout at all. This module drew ``updb_body``'s other valid
    order once — which puts ring A's fill above ``resp`` — and reverted it for exactly
    this reason, so the assertion belongs here rather than in a comment.
    """
    spec = stream._spec(4)
    assert spec.rows["resp"] == min(spec.rows[b] for b in spec.east)
    order = stream.perimeter_order(4)
    assert order[0] == "resp", "nothing may sit above resp on the east wall"

    # The cut is not decoration: move ring A's fill above `resp` — the one order the
    # arm's semantics allow that does so — and ring A becomes unroutable by *any*
    # layout, tree or relay, which is a different and worse failure than a crossing.
    severed = ["a_fwd", "resp", "out", "p2", "b_fwd", "prod", "b_ret", "p1", "in", "a_ret"]
    assert stream.block_crossings(4, order=severed, tree=("p2", "prod", "p1")) == [
        ("a_fwd", "a_ret")
    ]
    assert stream.block_crossings(
        4, order=severed, tree=("p2", "prod", "p1", "a_fwd", "a_ret", "b_fwd", "b_ret")
    ) == [("a_fwd", "a_ret")], "no tree can reconnect what the cut severed"


# ── the four-pipe dual relay: one room, two men, two jobs ────────────────────
def test_the_dual_relay_s_two_loops_each_read_before_they_send():
    """A relay that sends first would push the 0 its man was born holding.

    ``_RELAY``'s own man reaches his ``r`` before his ``s``; the upper loop's child is
    born heading north *below* its ``s``, so it has to detour round the west column to
    enter at the top. Before this was fixed the detour returned to the west column
    instead of the ``s``, so the upper loop read for ever and never sent — a defect
    that survived because the existing test pinned tick-5 positions and nothing else.
    """
    cells = stream.dual_relay_cells()
    steps = {">": (1, 0), "<": (-1, 0), "^": (0, -1), "v": (0, 1)}

    def walk(pos, direction, n=30):
        seen = []
        for _ in range(n):
            glyph = cells.get(pos, " ")
            seen.append(glyph)
            direction = steps.get(glyph, direction)
            pos = (pos[0] + direction[0], pos[1] + direction[1])
        return seen

    for start, direction in (((2, 4), (0, -1)), ((2, 6), (0, 1))):
        ops = [g for g in walk(start, direction) if g in "rs"]
        assert ops[:4] == ["r", "s", "r", "s"], f"loop from {start} is {ops[:4]}"


def test_the_dual_relay_s_two_men_cannot_meet():
    """By layout, not by timing — which is what makes it safe to place around.

    ``SPEC.md``: a child born on another live man kills both, and so do same-cell
    arrivals and head-on swaps, *without a fatal error*. So "the two men never meet"
    has to be a property of the drawing. It is: their steady-state cycles are disjoint
    cell sets, three columns apart in the same room but six rows apart.
    """
    cells = stream.dual_relay_cells()
    steps = {">": (1, 0), "<": (-1, 0), "^": (0, -1), "v": (0, 1)}

    def cycle(pos, direction, skip, n=40):
        seen = []
        for i in range(n):
            glyph = cells.get(pos, " ")
            if i >= skip:
                seen.append(pos)
            direction = steps.get(glyph, direction)
            pos = (pos[0] + direction[0], pos[1] + direction[1])
        return set(seen)

    upper, lower = cycle((2, 4), (0, -1), 8), cycle((2, 6), (0, 1), 4)
    assert upper and lower
    assert not (upper & lower), f"the two men share {upper & lower}"


@pytest.mark.parametrize("upper_driven", [True, False])
def test_the_dual_relay_passes_a_pipe_through_without_starving_its_ring(upper_driven: bool):
    """Four pipes, two men, and an empty pipe parks one loop without stalling the other.

    ``ARCH.md`` §2.1 allows one room to turn around many rings *only while they are
    permanently full*, because a shared relay blocks on the first empty one. Two men
    is the way round that, and this is the check: the idle loop's ``r`` is wired to a
    manless stub room so nothing ever arrives, and the driven loop still passes every
    value. Argued, that would be a liveness claim; run, it is a fact.
    """
    rows = stream.dual_relay_probe(upper_driven)
    engine = FastLittleman("\n".join(rows))
    assert len(engine.pipes) == 4, "two loops, one in and one out each"
    assert sorted(room.kind for room in engine.rooms) == [
        "compute", "compute", "compute", "input", "output",
    ]

    result = engine.run([11, 22, 33], max_ticks=20_000)
    assert result.fatal is None, result.fatal
    assert result.output == [11, 22, 33]


def test_every_dual_relay_pipe_binds_by_position():
    """All four, against the engine's own binding — ARCH.md §4.4's silent-misbind family."""
    rows = stream.dual_relay_probe(True)
    engine = FastLittleman("\n".join(rows))
    rx, ry = 10, 8
    south = ry + stream.DUAL_RELAY_IH + 1
    wall = {}
    for band, (side, off) in stream.DUAL_RELAY_PORTS.items():
        wall[band] = {"north": (rx + off, ry), "south": (rx + off, south), "west": (rx, ry + off)}[
            side
        ]
    glyphs = {
        ("turn_in", "r"): (rx + 3, ry + 2),
        ("turn_out", "s"): (rx + 2, ry + 2),
        ("pass_in", "r"): (rx + 3, ry + 8),
        ("pass_out", "s"): (rx + 2, ry + 8),
    }
    for (band, glyph), cell in glyphs.items():
        pipe = engine.pipes[engine._bindings[cell]]
        attach = pipe.src_attach if glyph == "s" else pipe.dst_attach
        assert attach == wall[band], f"{glyph}@{cell} should bind {band} at {wall[band]}"


def test_the_two_refuted_fixes_stay_refuted():
    """The load-bearing negative results, so neither can be proposed a third time.

    Both of these were proposed and both are refuted by the enumeration in the task-6
    report (114 admissible row maps x 4 trees, with the depth-3 block as a control run
    through the search's own assembly code). They are asserted here because a negative
    result that lives only in prose gets re-proposed.
    """
    # (1) The ADDER alone never closes: a shared relay is *necessary*, so no
    #     capacity-shaped idea — disjoint leg spans, deeper bands, more columns —
    #     can work, because the obstruction is not capacity.
    assert stream.block_crossings(4, tree=("p2", "prod", "p1")) == [("b_fwd", "b_ret")]
    # (2) It has to be ring B's relay. Ring A's chord encloses ring B's — forced,
    #     because UPDB reads ring A before the accumulator and ring B after it — so
    #     passing prod through ring A's relay leaves ring B crossing instead.
    assert stream.block_crossings(4, tree=("p2", "prod", "p1", "a_fwd", "a_ret")) == [
        ("b_fwd", "b_ret")
    ]
    # (3) and ring B's relay does close it.
    assert stream.block_crossings(4, tree=("p2", "prod", "p1", "b_fwd", "b_ret")) == []
    # The control, through the same call: the depth-3 block is placed and judged.
    assert stream.block_crossings(3) == []


# ── the crossed shared relay: the room that actually passes prod across ring B ──
def _interleaves(iw: int, ih: int, ports: dict[str, tuple[str, int]]) -> bool:
    """Do ring B's two ports separate the passed pipe's two, around the boundary?

    The topological condition, and the only reason a shared relay is worth having: the
    chord meets the room at ``turn_in`` and ``turn_out``, splitting its boundary into two
    arcs, and ``prod`` can only cross the chord if it enters on one and leaves on the
    other. Nothing about ``SPEC.md``'s binding rule enters here — this is the geometry.
    """
    order = stream.boundary_clockwise(iw, ih)
    at = {port: i for i, port in enumerate(order)}
    lo, hi = sorted((at[ports["turn_in"]], at[ports["turn_out"]]))
    return sum(lo < at[ports[b]] < hi for b in ("pass_in", "pass_out")) == 1


def _binds(iw: int, ih: int, glyphs: dict, ports: dict) -> dict[str, tuple[str, int]]:
    """Every glyph's bound port and margin, both pools, via SPEC's own direction."""
    seg = stream.wall_segments(iw, ih)
    out: dict[str, tuple[str, int]] = {}
    for kind in "rs":
        bands = [b for b, (g, _c) in glyphs.items() if g == kind]
        out |= stream.pipe_bindings(
            [(b, glyphs[b][1]) for b in bands], [(b, seg[ports[b]]) for b in bands]
        )
    return out


STACKED_GLYPHS = {
    "turn_in": ("r", (3, 2)),
    "turn_out": ("s", (2, 2)),
    "pass_in": ("r", (3, 8)),
    "pass_out": ("s", (2, 8)),
}


def test_the_stacked_relays_documented_ports_do_not_let_prod_cross():
    """The real defect in ``DUAL_RELAY_PORTS``, stated at exactly its true strength.

    All four pipes bind, with a minimum margin of 5, and no two of the four legs need to
    cross — which is what this module checked, and neither check can see the property
    that matters: ``prod`` has to enter on one side of ring B's chord and leave on the
    other. These four ports put both passed ports on one side.
    """
    iw, ih = stream.DUAL_RELAY_IW, stream.DUAL_RELAY_IH
    bound = _binds(iw, ih, STACKED_GLYPHS, stream.DUAL_RELAY_PORTS)
    assert all(got == band for band, (got, _m) in bound.items()), bound
    assert min(m for _b, m in bound.values()) == 5, bound
    assert not _interleaves(iw, ih, stream.DUAL_RELAY_PORTS), (
        "if this ever interleaves the stacked room could carry the block after all"
    )


def test_the_stacked_relay_can_interleave_after_all():
    """Round 9 claimed the stacked room could not do it at any size. That was false.

    The claim rested on a helper that computed ``port -> nearest glyph`` when
    ``SPEC.md`` binds ``glyph -> nearest pipe``; those are different propositions and
    only the second is what the engine evaluates. Correcting the direction
    (:func:`~randomfun2026solvers.lm1.stream.pipe_bindings`) leaves **1238** strictly
    binding interleaved assignments at 3x9, and this pins one of them.

    It is verified on the engine rather than on the model, because a model is what got
    this wrong once already.
    """
    iw, ih = stream.DUAL_RELAY_IW, stream.DUAL_RELAY_IH
    witness = {
        "turn_in": ("north", 2),
        "pass_in": ("west", 3),
        "turn_out": ("west", 7),
        "pass_out": ("east", 8),
    }
    bound = _binds(iw, ih, STACKED_GLYPHS, witness)
    assert all(got == band for band, (got, _m) in bound.items()), bound
    assert min(m for _b, m in bound.values()) >= 1, "must not rest on the tie-break"
    assert _interleaves(iw, ih, witness)

    rows = _relay_probe(stream.dual_relay_cells(), iw, ih, witness)
    engine = FastLittleman("\n".join(rows))
    assert len(engine.pipes) == 4
    for band, (kind, (gx, gy)) in STACKED_GLYPHS.items():
        pipe = engine.pipes[engine._bindings[(_RX + gx, _RY + gy)]]
        attach = pipe.src_attach if kind == "s" else pipe.dst_attach
        assert attach == _wall_cell(iw, ih, witness[band]), (
            f"the engine binds {band}'s {kind} to {attach}, not its own port"
        )


_RX, _RY = 14, 14


def _wall_cell(iw: int, ih: int, port: tuple[str, int]) -> tuple[int, int]:
    wall, off = port
    return {
        "north": (_RX + off, _RY),
        "south": (_RX + off, _RY + ih + 1),
        "west": (_RX, _RY + off),
        "east": (_RX + iw + 1, _RY + off),
    }[wall]


def _relay_probe(cells: dict, iw: int, ih: int, ports: dict) -> list[str]:
    """A relay room with one two-cell stub leg per port — the shortest legal pipe.

    Short legs on purpose: nothing about the *leg* may influence the binding, so if the
    engine disagrees with the model it is about the glyphs and the walls and nothing else.
    """
    from randomfun2026solvers.lm1.machine import _Grid

    g = _Grid()
    g.room(_RX, _RY, _RX + iw + 1, _RY + ih + 1)
    g.blit(_RX, _RY, cells)
    step = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}
    for band, port in ports.items():
        wx, wy = _wall_cell(iw, ih, port)
        dx, dy = step[port[0]]
        near, far = (wx + dx, wy + dy), (wx + 2 * dx, wy + 2 * dy)
        home = (wx + 4 * dx, wy + 4 * dy)
        g.room(home[0] - 1, home[1] - 1, home[0] + 1, home[1] + 1)
        g.draw_pipe([far, near] if band.endswith("_in") else [near, far])
    return g.rows()


def test_what_actually_favours_the_crossed_room_is_four_distinct_walls():
    """The narrowed claim, enumerated: 0 for the stacked room against 2809 here.

    Four distinct walls is what :func:`~randomfun2026solvers.lm1.stream._place4` wants —
    ring B arrives from the far west, returns east into the unit, ``prod`` climbs from
    below, ``pass_out`` leaves north into the ADDER — and it is the only sense in which
    the stacked room is unable to do this job. It is *not* unusable in general.
    """
    from itertools import permutations

    def four_wall_solutions(cells_iw, cells_ih, glyphs):
        order = stream.boundary_clockwise(cells_iw, cells_ih)
        n = 0
        for combo in permutations(order, 4):
            if len({wall for wall, _off in combo}) != 4:
                continue
            ports = dict(zip(("turn_in", "pass_in", "turn_out", "pass_out"), combo, strict=True))
            bound = _binds(cells_iw, cells_ih, glyphs, ports)
            if any(got != band or m < 1 for band, (got, m) in bound.items()):
                continue
            n += _interleaves(cells_iw, cells_ih, ports)
        return n

    assert four_wall_solutions(stream.DUAL_RELAY_IW, stream.DUAL_RELAY_IH, STACKED_GLYPHS) == 0
    crossed = four_wall_solutions(
        stream.SHARED_RELAY_IW, stream.SHARED_RELAY_IH, stream.SHARED_RELAY_GLYPHS
    )
    assert crossed == 2809, crossed


def test_the_shared_relays_ports_bind_interleaved_at_the_margin_it_claims():
    """The drawn assignment: every glyph on its own pipe, interleaved, margin asserted.

    The margin is asserted because the round-9 figure for it was wrong in both value and
    direction — it said 5, and said that beat the stacked room, when 5 is the stacked
    room's. Interleaving, not margin, is why this room is here.
    """
    iw, ih = stream.SHARED_RELAY_IW, stream.SHARED_RELAY_IH
    bound = stream.shared_relay_bindings()
    assert all(got == band for band, (got, _m) in bound.items()), bound
    assert sorted(m for _b, m in bound.values()) == [3, 3, 7, 7], bound
    assert min(m for _b, m in bound.values()) == stream.SHARED_RELAY_MIN_MARGIN
    assert _interleaves(iw, ih, stream.SHARED_RELAY_PORTS)

    clockwise = [
        band
        for pos in stream.boundary_clockwise(iw, ih)
        for band, port in stream.SHARED_RELAY_PORTS.items()
        if port == pos
    ]
    cut = clockwise.index("turn_in")
    rotated = clockwise[cut:] + clockwise[:cut]
    assert rotated == ["turn_in", "pass_out", "turn_out", "pass_in"], rotated


def test_pipe_bindings_models_specs_direction_and_not_its_converse():
    """The two readings genuinely disagree, so a regression to the wrong one fails here.

    Round 9's helper asked "which glyph is port ``F`` nearest to"; ``SPEC.md`` asks
    "which pipe is glyph ``a`` nearest to". The relation is not symmetric and it is not
    invertible: several glyphs may bind one pipe, and a pipe's nearest glyph need not be
    a glyph that binds it. An impossibility argument built on the converse is unsound,
    which is what happened.

    In the case below ``SPEC.md`` binds ``a -> F`` and ``b -> P``, so both pipes are
    served; the converse reading calls ``a`` the nearest glyph to *both* ports, which
    would have put ``P`` in ``a``'s "arc" and concluded that ``a`` binds ``P``.
    """
    glyphs = [("a", (1, 1)), ("b", (5, 1))]
    ports = [("F", (1, -1)), ("P", (2, -1))]

    spec = {g: port for g, (port, _m) in stream.pipe_bindings(glyphs, ports).items()}
    assert spec == {"a": "F", "b": "P"}, spec

    converse = {
        port: min(glyphs, key=lambda g: abs(g[1][0] - c[0]) + abs(g[1][1] - c[1]))[0]
        for port, c in ports
    }
    assert converse == {"F": "a", "P": "a"}, converse
    assert spec["a"] != "P", "SPEC's direction does not give a the port the converse does"

    # and a margin of 0 is reported honestly, because a tie is decided by reading order
    # of the *segments* and so is not something a layout should be resting on
    tied = stream.pipe_bindings([("a", (1, 1))], [("F", (0, 1)), ("P", (2, 1))])
    assert tied["a"] == ("F", 0), tied


def test_the_shared_relays_two_loops_each_read_before_they_send():
    """Neither man may push the 0 he was born holding.

    The ``Y`` sits in the annulus between the two nested loops, and each child is born
    facing into its own loop at a cell *after* that loop's ``s`` and *before* its ``r``.
    """
    cells = stream.shared_relay_cells()
    steps = {">": (1, 0), "<": (-1, 0), "^": (0, -1), "v": (0, 1)}

    def walk(pos, direction, n=60):
        seen = []
        for _ in range(n):
            glyph = cells.get(pos, " ")
            seen.append((pos, glyph))
            direction = steps.get(glyph, direction)
            pos = (pos[0] + direction[0], pos[1] + direction[1])
        return seen

    for start, direction in (((4, 3), (0, 1)), ((4, 1), (0, -1))):
        ops = [g for _, g in walk(start, direction) if g in "rs"]
        assert ops[:4] == ["r", "s", "r", "s"], f"loop from {start} is {ops[:4]}"


def test_the_shared_relays_two_men_cannot_meet():
    """Nested loops, disjoint cells — and the starter's own two cells in neither.

    ``SPEC.md`` kills both men on a same-cell arrival without a fatal error, so this
    has to be a property of the drawing. The inner loop is rows 3..5 x columns 3..7;
    the outer one is the frame; the ``@``/``Y`` pair is in the annulus between them.
    """
    cells = stream.shared_relay_cells()
    steps = {">": (1, 0), "<": (-1, 0), "^": (0, -1), "v": (0, 1)}

    def cycle(pos, direction, skip, n=90):
        seen = []
        for i in range(n):
            glyph = cells.get(pos, " ")
            if i >= skip:
                seen.append(pos)
            direction = steps.get(glyph, direction)
            pos = (pos[0] + direction[0], pos[1] + direction[1])
        return set(seen)

    inner, outer = cycle((4, 3), (0, 1), 14), cycle((4, 1), (0, -1), 30)
    assert len(inner) == 12 and len(outer) == 28, (len(inner), len(outer))
    assert not (inner & outer), f"the two men share {inner & outer}"
    assert not ({(3, 2), (4, 2)} & (inner | outer)), "the starter sits on a loop"


@pytest.mark.parametrize("inner_driven", [True, False])
def test_the_shared_relay_passes_a_pipe_through_without_starving_its_ring(inner_driven: bool):
    """Four pipes, two men, one loop's ``r`` on a manless stub — and values still flow."""
    rows = stream.shared_relay_probe(inner_driven)
    engine = FastLittleman("\n".join(rows))
    assert len(engine.pipes) == 4, "two loops, one in and one out each"
    result = engine.run([11, 22, 33], max_ticks=40_000)
    assert result.fatal is None, result.fatal
    assert result.output == [11, 22, 33]


def test_every_shared_relay_pipe_binds_by_position():
    """All four, against the engine's own binding — ARCH.md §4.4's silent-misbind family."""
    rows = stream.shared_relay_probe(True)
    engine = FastLittleman("\n".join(rows))
    rx, ry = 12, 12
    south = ry + stream.SHARED_RELAY_IH + 1
    east = rx + stream.SHARED_RELAY_IW + 1
    for band, (wall, off) in stream.SHARED_RELAY_PORTS.items():
        glyph, (gx, gy) = stream.SHARED_RELAY_GLYPHS[band]
        want = {
            "north": (rx + off, ry),
            "south": (rx + off, south),
            "west": (rx, ry + off),
            "east": (east, ry + off),
        }[wall]
        pipe = engine.pipes[engine._bindings[(rx + gx, ry + gy)]]
        attach = pipe.src_attach if glyph == "s" else pipe.dst_attach
        assert attach == want, f"{glyph}@{(rx + gx, ry + gy)} should bind {band} at {want}"


# ── the *placed* block, at both widths, through one assembly path ─────────────
def _block_harness(trie_bits: int) -> tuple[list[str], stream.StreamBlock, tuple[int, int]]:
    """The placed block plus a looping ROM that drives its ``cmd`` pipe.

    Everything above this pins the *unit*; ``unit_interior_grid`` deliberately gives
    every pipe a bare stub so the row map is tested without the block's own rings in
    the way. This is the other half: the block as placed, so a leg that fails to parse
    or two legs that share a cell show up as a pipe count rather than as a picture
    nobody looked at. Both widths run through the same code, which is the point — a
    depth-3 regression and a depth-4 one fail the same assertion.
    """
    from randomfun2026solvers.lm1 import machine
    from randomfun2026solvers.lm1 import rom as rommod

    blk = stream.build_stream(a_slots=16, b_slots=200, c_slots=16, trie_bits=trie_bits)
    g = machine._Grid()
    ox, oy = 0, 16
    for (x, y), ch in blk.cells.items():
        g.put(ox + x, oy + y, ch)
    lay = rommod.build_rom([16 * 2 + 3], rows=2)
    rx, ry = 2, 0
    g.room(rx, ry, rx + lay.width, ry + lay.height + 1)
    g.blit(rx, ry + 1, lay.cells)
    cx, cy = ox + blk.cmd_cell[0], oy + blk.cmd_cell[1]
    g.draw_pipe([(rx + 1, ry + lay.height + 2), (rx + 1, cy - 1), (cx, cy - 1), (cx, cy)])
    sx, sy = ox + blk.resp_cell[0], oy + blk.resp_cell[1]
    g.draw_pipe([(sx, sy), (sx, sy - 2)])
    g.room(sx - 1, sy - 5, sx + 1, sy - 3)
    return g.rows(), blk, (ox, oy)


@pytest.mark.parametrize("trie_bits", [3, 4])
def test_the_placed_block_has_every_pipe_it_drew(trie_bits: int):
    """Pipes drawn against pipes found, at both widths.

    ``SPEC.md``'s first parse rule is what this catches: a pipe starts with the
    arrowhead whose *backward* cell is on the source room's border, so a leg leaving a
    vertical wall has to step away from it before it may turn. Drawn with the turn on
    the attach cell, ``p1`` silently did not parse at all — ``analyze`` reported one
    fewer pipe and nothing else complained.
    """
    rows, blk, _origin = _block_harness(trie_bits)
    engine = FastLittleman("\n".join(rows))
    assert len(engine.pipes) == blk.pipes + 1, "+ the ROM's own command pipe"
    assert blk.pipes == (10 if trie_bits == 3 else 11)


@pytest.mark.parametrize("trie_bits", [3, 4])
def test_no_two_legs_of_the_placed_block_share_a_cell(trie_bits: int):
    """``_Grid.put`` only catches a collision when the two glyphs *differ*.

    So one horizontal leg crossing another — both ``-`` — is silent, and the values go
    to the wrong room with no error anywhere.

    **How this is measured matters, and the first version of this test got it wrong.**
    It took each leg's cells as a set-difference of ``_Grid.drawn`` before and after the
    call. ``drawn`` only grows, so consecutive differences are disjoint *by
    construction*: two pipes laid over identical cells produce a full set and then an
    empty one, and the assertion passes. ``_test_the_overlap_guard_would_catch_an_overlap``
    below is the meta-test that keeps this honest — it feeds two deliberately
    overlapping legs through the same measurement and requires it to complain.

    So the cells are recomputed here from each call's own ``points``, by
    :func:`_polyline_cells`, which never consults the grid.
    """
    blk = stream.build_stream(a_slots=16, b_slots=200, c_slots=16, trie_bits=trie_bits)
    legs = _record_legs(trie_bits, blk)
    assert len(legs) == blk.pipes, (len(legs), blk.pipes)
    _assert_legs_disjoint(legs)


def _polyline_cells(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Every cell a rectilinear polyline covers, in flow order, grid-independently.

    Deliberately a second implementation of ``_Grid.draw_pipe``'s own walk: if the two
    ever disagree the test fails, which is the right outcome for a check whose whole job
    is to not trust the thing it is checking.
    """
    cells = [points[0]]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        assert x0 == x1 or y0 == y1, f"leg {(x0, y0)}->{(x1, y1)} is not rectilinear"
        sx, sy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x, y = x + sx, y + sy
            cells.append((x, y))
    return cells


def _record_legs(trie_bits: int, blk: stream.StreamBlock) -> list[list[tuple[int, int]]]:
    """Replay the placement the search settled on, capturing each leg's own cells."""
    from randomfun2026solvers.lm1 import machine

    legs: list[list[tuple[int, int]]] = []
    original = machine._Grid.draw_pipe

    def recording(self, points):
        legs.append(_polyline_cells(points))
        return original(self, points)

    place = stream._place if trie_bits == 3 else stream._place4
    machine._Grid.draw_pipe = recording
    try:
        place(16, 200, 16, blk.rows_a, blk.rows_b)
    finally:
        machine._Grid.draw_pipe = original
    return legs


def _assert_legs_disjoint(legs: list[list[tuple[int, int]]]) -> None:
    """No cell used twice, within a leg or across two — the property under test."""
    seen: dict[tuple[int, int], int] = {}
    for i, leg in enumerate(legs):
        for cell in leg:
            if cell in seen:
                raise AssertionError(f"legs {seen[cell]} and {i} share {cell}")
            seen[cell] = i
    assert len(seen) == sum(len(leg) for leg in legs)


def test_the_overlap_guard_would_catch_an_overlap():
    """The meta-test: the measurement above must fail on legs that really do overlap.

    A disjointness check that reads a monotonically growing accumulator passes
    vacuously, which is what the first version of the test above did. This is the
    cheapest way to keep that from coming back — it asserts the *checker* discriminates,
    not that the block is correct.
    """
    straight = [(0, 0), (5, 0)]
    assert _polyline_cells(straight) == [(x, 0) for x in range(6)]

    # two legs over the same cells, and one leg doubling back over itself
    with pytest.raises(AssertionError, match="share"):
        _assert_legs_disjoint([_polyline_cells(straight), _polyline_cells(straight)])
    with pytest.raises(AssertionError, match="share"):
        _assert_legs_disjoint([_polyline_cells([(0, 0), (3, 0), (1, 0)])])
    # a partial crossing, which is the realistic failure: one `-` leg over another
    with pytest.raises(AssertionError, match="share"):
        _assert_legs_disjoint(
            [_polyline_cells([(0, 5), (9, 5)]), _polyline_cells([(4, 0), (4, 9)])]
        )
    # and it accepts genuinely disjoint legs
    _assert_legs_disjoint(
        [_polyline_cells([(0, 0), (9, 0)]), _polyline_cells([(0, 1), (9, 1)])]
    )


@pytest.mark.parametrize("trie_bits", [3, 4])
def test_every_placed_glyph_binds_the_pipe_the_block_meant(trie_bits: int):
    """All 17 glyphs at depth 3 and all 28 at depth 4, against the engine's binding.

    ``unit_interior_grid`` proves the row map with straight stub legs. This proves the
    same glyphs once the real rings, relays and ADDER are around them — which is where
    a rival ``r`` two cells nearer would actually appear.
    """
    rows, blk, (ox, oy) = _block_harness(trie_bits)
    engine = FastLittleman("\n".join(rows))
    spec = stream._spec(trie_bits)
    unit = stream.unit_interior(trie_bits)
    ux = (stream.UX if trie_bits == 3 else stream.UX4) + ox
    uy = (stream.UY if trie_bits == 3 else stream.UY4) + oy
    want = {"cmd": (ux + unit.north["cmd"], uy)}
    want |= {band: (ux, uy + row) for band, row in unit.west.items()}
    want |= {band: (ux + spec.iw + 1, uy + row) for band, row in unit.east.items()}
    want |= {band: (ux + col, uy + spec.ih + 1) for band, col in unit.south.items()}

    assert len(blk.glyphs) == (17 if trie_bits == 3 else 28)
    for x, y, glyph, band in blk.glyphs:
        cell = (ox + x, oy + y)
        pid = engine._bindings.get(cell)
        assert isinstance(pid, int) and pid >= 0, f"{glyph}@{cell} binds no pipe at all"
        pipe = engine.pipes[pid]
        attach = pipe.src_attach if glyph == "s" else pipe.dst_attach
        assert attach == want[band], (
            f"{glyph}@{cell} should bind {band} at {want[band]} but got {attach}"
        )


def test_the_placed_shared_relay_and_adder_bind_by_position():
    """The four relay pipes and the ADDER's three, in the block rather than in a probe.

    The relay's whole job is to pass ``prod`` across ring B's chord, so if ``prod``
    bound the *ring's* ``r`` the block would still load, still run, and quietly send
    products round ring B. That is the failure this asserts against.
    """
    rows, blk, (ox, oy) = _block_harness(4)
    engine = FastLittleman("\n".join(rows))
    rbx, rby = stream.RELAY_B4[0] + ox, stream.RELAY_B4[1] + oy
    adx, ady = stream.ADDER4[0] + ox, stream.ADDER4[1] + oy
    east = rbx + stream.SHARED_RELAY_IW + 1
    south = rby + stream.SHARED_RELAY_IH + 1

    for band, (wall, off) in stream.SHARED_RELAY_PORTS.items():
        glyph, (gx, gy) = stream.SHARED_RELAY_GLYPHS[band]
        want = {
            "north": (rbx + off, rby),
            "south": (rbx + off, south),
            "west": (rbx, rby + off),
            "east": (east, rby + off),
        }[wall]
        pipe = engine.pipes[engine._bindings[(rbx + gx, rby + gy)]]
        attach = pipe.src_attach if glyph == "s" else pipe.dst_attach
        assert attach == want, f"the relay's {band} {glyph} bound {attach}, not {want}"

    # the ADDER: `p2` on the west wall, `prod` out of the relay up into the south wall,
    # `p1` east. Addition is commutative, so the two `r`s could swap without a wrong
    # answer — but they cannot bind the *same* pipe, and this is what says so.
    adder = {
        (adx + 2, ady + 1): (adx, ady + 1),  # r -> p2's west-wall attach
        (adx + 6, ady + 1): (adx + 6, ady + stream.ADDER_IH + 1),  # r -> pass_out's
        (adx + 8, ady + 2): (adx + stream.ADDER_IW + 1, ady + 2),  # s -> p1's
    }
    bound = set()
    for cell, want in adder.items():
        pid = engine._bindings[cell]
        pipe = engine.pipes[pid]
        attach = pipe.src_attach if cell == (adx + 8, ady + 2) else pipe.dst_attach
        assert attach == want, f"the ADDER's glyph at {cell} bound {attach}, not {want}"
        bound.add(pid)
    assert len(bound) == 3, "the ADDER's three glyphs must bind three different pipes"


def test_the_placed_block_is_planar_at_both_widths():
    """``block_crossings`` through the same assembly the drawing uses, depth 3 control."""
    assert stream.block_crossings(3) == []
    assert stream.block_crossings(4, tree=("p2", "prod", "p1", "b_fwd", "b_ret")) == []


def test_the_depth_four_block_dimensions_are_recorded():
    """A size regression should name itself rather than surface as a placement failure.

    Depth 3 is measured at ``matmul``'s own shipped ring sizes, so this is also a guard
    on the grid that is already judged.
    """
    from randomfun2026solvers.lm1 import machine

    a, b, c = machine.STREAM_SIZE["matmul"]
    three = stream.build_stream(a_slots=a, b_slots=b, c_slots=c)
    four = stream.build_stream(a_slots=16, b_slots=856, c_slots=80, trie_bits=4)
    assert (three.width, three.height) == (67, 45)
    assert (four.width, four.height) == (112, 51)
    assert four.ring_c >= 80, "p1's boustrophedon holds a whole row of C"
    assert four.rows_a % 2 == 1 and four.rows_b % 2 == 1  # odd: the last leg goes west


@node_required
@reference_sweeps
@pytest.mark.parametrize("trie_bits", [3, 4])
def test_the_reference_interpreter_agrees_about_the_placed_block(trie_bits: int, tmp_path):
    """``Littleman.analyze``/``route`` — the judge's own engine — on the placed block.

    ``FastLittleman`` agrees with the reference everywhere it has been checked, but the
    thing being asserted here is a *load* property and the judge is the authority on
    those. So the pipe count and every unit glyph go through ``lm.mjs`` as well.
    """
    from randomfun2026solvers.littleman import Littleman

    rows, blk, (ox, oy) = _block_harness(trie_bits)
    path = tmp_path / f"block{trie_bits}.man"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    lm = Littleman()
    assert len(lm.analyze(path).pipes) == blk.pipes + 1, "+ the ROM's own command pipe"

    spec = stream._spec(trie_bits)
    unit = stream.unit_interior(trie_bits)
    ux = (stream.UX if trie_bits == 3 else stream.UX4) + ox
    uy = (stream.UY if trie_bits == 3 else stream.UY4) + oy
    want = {"cmd": (ux + unit.north["cmd"], uy - 1)}
    want |= {band: (ux - 1, uy + row) for band, row in unit.west.items()}
    want |= {band: (ux + spec.iw + 2, uy + row) for band, row in unit.east.items()}
    want |= {band: (ux + col, uy + spec.ih + 2) for band, col in unit.south.items()}
    for x, y, glyph, band in blk.glyphs:
        cells = [(c.x, c.y) for c in lm.route(path, ox + x, oy + y)]
        assert cells, f"{glyph}@{(x, y)} binds no pipe at all"
        assert want[band] in (cells[0], cells[-1]), (
            f"{glyph}@{(x, y)} should bind {band} at {want[band]} but got {cells[0], cells[-1]}"
        )


# ── the placed depth-4 block, running ─────────────────────────────────────────
def _driven(words: list[int]) -> tuple[list[str], stream.StreamBlock]:
    """The depth-4 block with a looping ROM replaying ``words`` as commands, forever."""
    from randomfun2026solvers.lm1 import machine
    from randomfun2026solvers.lm1 import rom as rommod

    blk = stream.build_stream(a_slots=8, b_slots=8, c_slots=6, trie_bits=4)
    g = machine._Grid()
    ox, oy = 0, 16
    for (x, y), ch in blk.cells.items():
        g.put(ox + x, oy + y, ch)
    lay = rommod.build_rom(words, rows=2)
    rx, ry = 2, 0
    g.room(rx, ry, rx + lay.width, ry + lay.height + 1)
    g.blit(rx, ry + 1, lay.cells)
    cx, cy = ox + blk.cmd_cell[0], oy + blk.cmd_cell[1]
    g.draw_pipe([(rx + 1, ry + lay.height + 2), (rx + 1, cy - 1), (cx, cy - 1), (cx, cy)])
    sx, sy = ox + blk.resp_cell[0], oy + blk.resp_cell[1]
    g.draw_pipe([(sx, sy), (sx, sy - 2)])
    g.room(sx - 1, sy - 5, sx + 1, sy - 3)
    return g.rows(), blk


def test_the_depth_four_block_computes_a_dot_product():
    """1x2x1 on the placed depth-4 grid: A = [5, 7], B = [[3], [4]] -> 43, then -> 23.

    The same two numbers ``test_the_block_computes_a_dot_product...`` asks of depth 3,
    which is the point: every glyph could bind the right pipe and the *shared relay*
    still be wrong, because ``prod`` now reaches the ADDER through it. The second lap is
    the interesting one — it says ring B came back into alignment and the accumulator
    ring came back empty, so the depth-4 block is reusable rather than a one-shot.
    """
    c, w = StreamUnit.CODES, 16
    m, k = 2, 1
    words = [w * m + c["FILLA"], w * (m * k) + c["FILLB"], w * k + c["ZEROC"], w * k + c["MAC"]]
    for _ in range(m - 1):
        words += [w * k + c["FWD"], w * k + c["MAC"]]
    words += [w * k + c["EMIT"], w * (m * k) + c["DRAINB"]]

    rows, blk = _driven(words)
    engine = FastLittleman("\n".join(rows))
    assert len(engine.pipes) == blk.pipes + 1
    result = engine.run([5, 7, 3, 4, 2, 3, 4, 5], max_ticks=300_000)
    assert result.fatal is None, result.fatal
    assert result.output[:2] == [43, 23], result.output


def test_rotb_and_pusha_do_on_the_grid_what_they_do_in_the_model():
    """Two new arms, observed through ``MAC``/``EMIT`` because ``resp`` goes to the CPU.

    ``PUSHA 1`` makes ``MAC``'s products equal ring B's values, so the accumulator ring
    reports ring B's contents — which is how a rotation becomes observable at the O room
    without a second output pipe.
    """
    c, w = StreamUnit.CODES, 16
    words = [
        w * 3 + c["FILLB"],
        w * 1 + c["ROTB"],
        w * 1 + c["PUSHA"],
        w * 3 + c["ZEROC"],
        w * 3 + c["MAC"],
        w * 3 + c["EMIT"],
        w * 3 + c["DRAINB"],
    ]
    rows, _blk = _driven(words)
    result = FastLittleman("\n".join(rows)).run([10, 20, 30], max_ticks=300_000)
    assert result.fatal is None, result.fatal
    assert result.output[:3] == [20, 30, 10], result.output


def test_updb_applies_its_rank_one_update_on_the_placed_grid():
    """The whole ``UPDB`` round, on the grid, with the drawn 18-bit shift.

    ``UPDB``'s six pipe glyphs bind six *different* pipes by geometry, and ``prod``
    reaches the ADDER through the shared relay, so this is the one check that exercises
    every part of the depth-4 topology at once against arithmetic with a known answer.

    Gradients have to reach the accumulator ring the way the trainer sends them — as
    ``MAC`` products against a unit scalar — so the round is: load ``g`` onto ring B,
    push it round the ADDER onto ``p1``, empty ring B, load the weights, ``PUSHA`` the
    scalar, ``UPDB``, then read the updated weights back out through ``MAC``/``EMIT``
    (which also confirms ``UPDB`` left ``g`` circulating on ``p2`` rather than eating it).
    """
    c, w = StreamUnit.CODES, 16
    grads = [1 << stream.UPDB_SHIFT, 2 << stream.UPDB_SHIFT, 0]
    weights = [1000, 2000, 3000]
    scalar = 3
    words = [
        w * 1 + c["PUSHA"],  # a = 1, so MAC's products are ring B itself
        w * 3 + c["FILLB"],  # ring B <- the gradients
        w * 3 + c["ZEROC"],
        w * 3 + c["MAC"],  # p1 <- the gradients, through the ADDER
        w * 3 + c["DRAINB"],
        w * 3 + c["FILLB"],  # ring B <- the weights
        w * scalar + c["PUSHA"],
        w * 3 + c["UPDB"],  # the update, in place
        w * 3 + c["MAC"],  # products = scalar * updated weight, paired with p2 = g
        w * 3 + c["EMIT"],
        w * 3 + c["DRAINB"],
    ]
    rows, _blk = _driven(words)
    result = FastLittleman("\n".join(rows)).run(grads + weights, max_ticks=900_000)
    assert result.fatal is None, result.fatal
    want = [
        scalar * (b - ((scalar * g) >> stream.UPDB_SHIFT)) + g
        for b, g in zip(weights, grads, strict=True)
    ]
    assert result.output[:3] == want, (result.output[:3], want)
