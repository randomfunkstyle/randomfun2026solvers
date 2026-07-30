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
    """A block it cannot draw correctly must not be approximated (ARCH.md §4.4)."""
    from randomfun2026solvers.lm1.stream import StreamError

    with pytest.raises(StreamError, match="the drawing does not"):
        stream.build_stream(a_slots=16, b_slots=856, c_slots=80, trie_bits=4)
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
