"""The STREAM block: rotate-only rings, a fused MAC, and eleven pipes that bind.

The block is the answer to ``ARCH.md`` §4.1's open question ("Banking — the only
route to matmul's 768 slots, and still a stretch"): not banking, a third tier.
Three things are checked here, and the middle one is the one that would rot
silently.

* **The arithmetic.** Every arm of the unit, driven by a looping ROM instead of a
  CPU, computing a real dot product on the reference interpreter.
* **The bindings.** Seventeen ``r``/``s`` glyphs, each asserted against the
  *engine's own* ``route`` — not against the generator's idea of Manhattan
  distance. The block's whole design rests on "a glyph on its pipe's row binds
  that pipe", and the tightest margin in it is one cell.
* **The capacities.** A ring's capacity *is* its length in cells, so the
  generator's ring sizes are checked against what the emulator's model actually
  queues at the largest legal shape.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1 import machine, stream  # noqa: E402
from randomfun2026solvers.lm1 import rom as rommod  # noqa: E402
from randomfun2026solvers.lm1.store import StreamUnit  # noqa: E402

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)
slow = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the reference-interpreter sweeps",
)


# ── the decode trie ──────────────────────────────────────────────────────────
def test_arm_codes_come_from_the_tries_geometry() -> None:
    """The codes are *read off* the trie, not assigned — and the model agrees.

    ``x`` turns clockwise on BP's low bit and a man heading south turns clockwise
    to the west, so each west branch sets that level's bit. If a leaf moves, these
    numbers move with it, and the emulator's model would silently dispatch to the
    wrong arm — hence the equality against :attr:`StreamUnit.CODES`.
    """
    codes = stream.arm_codes()
    assert codes == StreamUnit.CODES
    assert sorted(codes.values()) == list(range(8))
    assert set(codes) == set(stream.ARMS)


def test_the_command_word_survives_a_negative_argument() -> None:
    """``8 * arg + code`` with the raw low bits and a floored ``/`` (SPEC.md).

    Nothing in matmul sends a negative argument, but the encoding is the block's
    public interface and floored division is the only reason it would work — a
    truncating one would decode ``-1`` as ``0``.
    """
    for arg in (-9, -1, 0, 1, 256):
        for code in range(8):
            word = 8 * arg + code
            assert word & 7 == code
            assert word >> 3 == arg


# ── the unit's interior ──────────────────────────────────────────────────────
def test_every_pipe_glyph_sits_on_its_own_pipes_row() -> None:
    """Rule 1 of the block: the row *is* the pipe.

    An ``s`` on its pipe's east-wall row is at distance ``IW - x`` and every rival
    is that plus a row difference, so it wins strictly whatever column its arm is
    in. This asserts the property the layout is built on, before any geometry.
    """
    unit = stream.unit_interior()
    rows = {**{b: r for b, r in unit.west.items()}, **{b: r for b, r in unit.east.items()}}
    for x, y, glyph, band in unit.glyphs:
        if band in ("cmd", "p1"):  # north and south walls: columns, not rows
            continue
        assert rows[band] == y, f"{glyph}@{(x, y)} claims {band}, whose row is {rows[band]}"


def test_the_two_accumulator_readers_are_the_eastern_arms() -> None:
    """``FWD``/``EMIT`` read the south wall, so they must sit east of everything.

    The accumulator's return is the one incoming pipe *not* on the west wall, and
    it only beats the west-wall pipes for a glyph that is far from them. Move
    either arm west and it starts reading ring B instead — silently.
    """
    unit = stream.unit_interior()
    cols = {arm: stream.LEAF0 + stream.LEAF_PITCH * i for i, arm in enumerate(stream.ARMS)}
    assert cols["FWD"] > cols["MAC"] > cols["FILLA"]
    assert cols["EMIT"] == max(cols.values())
    assert unit.south["p1"] == cols["EMIT"] + 1  # directly under EMIT's own `r`


def test_the_fused_mac_is_four_glyphs() -> None:
    """``r s * s`` — and the ADDER is why it can be that short.

    With the partial sum living in the accumulator ring, ``B`` holds the scalar for
    the whole row and never has to be spilled: the alternative (a scratch ring)
    costs two more glyphs per multiply-accumulate *and* a fourth ring.
    """
    _entry, body = stream._BODIES["MAC"]
    assert body.replace(" ", "") == "rs*s"


def test_y_seeds_two_persistent_relays_in_one_room() -> None:
    """The combined A/B relay scaffold needs one starter and two disjoint loops."""
    cells = stream.dual_relay_cells()
    assert stream.DUAL_RELAY_IW == 3
    assert stream.DUAL_RELAY_IH == 9
    assert list(cells.values()).count("@") == 1
    assert list(cells.values()).count("Y") == 1
    assert list(cells.values()).count("r") == 2
    assert list(cells.values()).count("s") == 2

    rows = [
        "".join(cells.get((x, y), " ") for x in range(1, stream.DUAL_RELAY_IW + 1))
        for y in range(1, stream.DUAL_RELAY_IH + 1)
    ]
    source = "\n".join(
        [
            "+" + "-" * stream.DUAL_RELAY_IW + "+",
            *["|" + row + "|" for row in rows],
            "+" + "-" * stream.DUAL_RELAY_IW + "+",
        ]
    )

    # At tick 5 both children are alive and following separate corridors. The
    # next operation on either loop is its blocking receive; no collision or
    # wall error is needed to retire a setup child.
    from randomfun2026solvers.littleman import Littleman

    snapshot = Littleman().tick(source, 5)
    assert snapshot.fatal is None
    assert len(snapshot.entities.runners) == 2
    assert {runner.pos.as_tuple() for runner in snapshot.entities.runners} == {
        (1, 2),
        (3, 8),
    }


# ── the placed block ─────────────────────────────────────────────────────────
def test_the_rings_are_long_enough_for_the_worst_legal_shape() -> None:
    """Capacity is length: 257 values need 257 cells, and the search must find them."""
    a, b, c = machine.STREAM_SIZE["matmul"]
    blk = stream.build_stream(a_slots=a, b_slots=b, c_slots=c)
    assert blk.ring_a >= a
    assert blk.ring_b >= b
    assert blk.ring_c >= c
    assert blk.rows_a % 2 == 1 and blk.rows_b % 2 == 1  # odd: the last leg goes west
    assert blk.pipes == 10


def test_the_block_refuses_a_size_the_band_cannot_hold() -> None:
    with pytest.raises(machine.MachineError, match="no serpentine holds"):
        stream.build_stream(a_slots=5000, b_slots=5000, c_slots=17)


# ── the real engine ──────────────────────────────────────────────────────────
def _harness(words: list[int]) -> tuple[list[str], stream.StreamBlock, tuple[int, int]]:
    """The block plus a looping ROM that replays ``words`` as commands, forever.

    No CPU: the ROM *is* the driver, which is what makes this a unit test of the
    block. Each lap of the ROM is one round of a fixed-shape matmul, so the block's
    own ring alignment has to come back exactly right or lap two is wrong.
    """
    blk = stream.build_stream(a_slots=8, b_slots=8, c_slots=6)
    g = machine._Grid()
    ox, oy = 0, 14
    for (x, y), ch in blk.cells.items():
        g.put(ox + x, oy + y, ch)

    lay = rommod.build_rom(words, rows=2)
    rx, ry = 2, 0
    g.room(rx, ry, rx + lay.width, ry + lay.height + 1)
    g.blit(rx, ry + 1, lay.cells)
    cx, cy = ox + blk.cmd_cell[0], oy + blk.cmd_cell[1]
    g.draw_pipe([(rx + 1, ry + lay.height + 2), (rx + 1, cy - 1), (cx, cy - 1), (cx, cy)])

    # A sink room for the response pipe: these commands never use RDIN, but an
    # unterminated pipe is a load error, so it has to end somewhere.
    sx, sy = ox + blk.resp_cell[0], oy + blk.resp_cell[1]
    g.draw_pipe([(sx, sy), (sx, sy - 2)])
    g.room(sx - 1, sy - 5, sx + 1, sy - 3)
    return g.rows(), blk, (ox, oy)


def _commands(blk: stream.StreamBlock, m: int, k: int) -> list[int]:
    """One round for a 1xMxK product: fill, zero, MAC/FWD per term, emit, drain."""
    c = blk.codes
    cmd = [8 * m + c["FILLA"], 8 * (m * k) + c["FILLB"], 8 * k + c["ZEROC"]]
    cmd.append(8 * k + c["MAC"])
    for _ in range(m - 1):
        cmd += [8 * k + c["FWD"], 8 * k + c["MAC"]]
    cmd += [8 * k + c["EMIT"], 8 * (m * k) + c["DRAINB"]]
    return cmd


@node_required
def test_the_block_computes_a_dot_product_on_the_reference_interpreter(tmp_path) -> None:
    """1x2x1: A = [5, 7], B = [[3], [4]] -> 43, then a second lap -> 23.

    The second lap is the interesting one: it proves ring B came back into
    alignment (``DRAINB`` emptied it) and the accumulator ring came back empty, so
    the block is reusable rather than a one-shot.
    """
    from randomfun2026solvers.littleman import Littleman

    blk = stream.build_stream(a_slots=8, b_slots=8, c_slots=6)
    rows, blk, _origin = _harness(_commands(blk, 2, 1))
    path = tmp_path / "stream.man"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    lm = Littleman()
    assert len(lm.analyze(path).pipes) == blk.pipes + 1  # + the ROM's own
    out = list(lm.tick(path, 2500, input="5 7 3 4  2 3 4 5").output)
    assert out == [43, 23], out


@node_required
def test_every_glyph_binds_the_pipe_the_generator_intended(tmp_path) -> None:
    """The engine's ``route``, not the generator's arithmetic, on all 17 glyphs.

    ``check_bindings`` proves the CPU's glyphs; nothing proved the *unit's* until
    here. Each pipe is identified by the cell it starts and ends on, so a glyph
    that quietly re-bound to a neighbouring ring fails with both pipes named.
    """
    from randomfun2026solvers.littleman import Littleman

    blk = stream.build_stream(a_slots=8, b_slots=8, c_slots=6)
    rows, blk, (ox, oy) = _harness(_commands(blk, 2, 1))
    path = tmp_path / "stream.man"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    lm = Littleman()

    # band -> the pipe cell the block attached it to, in block coordinates
    ux, uy, unit = stream.UX, stream.UY, stream.unit_interior()
    want = {
        "cmd": blk.cmd_cell,
        **{b: (ux - 1, uy + r) for b, r in unit.west.items()},
        **{b: (ux + stream.UNIT_IW + 2, uy + r) for b, r in unit.east.items()},
        "p1": (ux + unit.south["p1"], uy + stream.UNIT_IH + 2),
    }
    for x, y, glyph, band in blk.glyphs:
        cells = [(c.x - ox, c.y - oy) for c in lm.route(path, ox + x, oy + y)]
        assert cells, f"{glyph}@{(x, y)} binds no pipe at all"
        target = cells[-1] if glyph == "s" else cells[0]
        # `s` fills a pipe's source end, `r` drains its destination end, so which
        # end identifies the pipe depends on which glyph is asking.
        ends = (cells[0], cells[-1])
        assert want[band] in ends, (
            f"{glyph}@{(x, y)} should bind {band} at {want[band]} but got {ends} "
            f"(target end {target})"
        )
