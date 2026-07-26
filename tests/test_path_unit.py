"""The PATH unit's **geometry** — the room, the panel, the pipes, and the box.

``test_path_unit_model.py`` owns the protocol at the emulator level. This file checks
the hardware arms as well as everything that could make a correct arm paint the wrong
pixel:

* **which pipe each glyph binds**, asserted by the generator's own arithmetic *and*
  by the engine's ``route`` on the placed grid (ARCH §7.1 — binding is declared, and
  the declaration is only worth what the engine says);
* **the three panel pipes' relative lengths**, because ADDR/DATA/SWAP are three
  pipes with three transit times and a DATA that overtakes its ADDR paints at a
  stale cursor;
* **the bounding box**, which goes straight into the score as ``max(w, h)^2``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1 import path_unit as pu  # noqa: E402

LM_MJS = REPO / "littleman" / "lm.mjs"
ROUTE_CHECK = REPO / "littleman" / "tools" / "route-check.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)

#: One command per arm, in an order the unit can actually execute: ``CELL`` while
#: the panel cursor is at zero, then ``ROBOT`` before ``MOVE`` because those are the
#: register's writer and reader. ``CELL`` is the binary board value; the other
#: arguments are legal panel cells (0..255).
PROBE_COMMANDS = [
    pu.word(pu.arm_codes()["CELL"], 1),
    pu.word(pu.arm_codes()["ROBOT"], 34),
    pu.word(pu.arm_codes()["FLAG"], 50),
    pu.word(pu.arm_codes()["MOVE"], 35),
]


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, pu.PathBlock]:
    """The probe grid on disk, plus the block it wraps."""
    rows, _dbg, blk = pu.build_probe(PROBE_COMMANDS)
    path = tmp_path_factory.mktemp("path-unit") / "path-unit-probe.man"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path, blk


# ── the trie and the word encoding ───────────────────────────────────────────
def test_codes_come_from_the_tries_geometry() -> None:
    """The codes are *read off* the trie, and the arm order is what fixes them.

    ``x`` turns clockwise on BP's low bit and a man heading south turns clockwise to
    the west, so a west branch sets that level's bit: the westernmost leaf is ``11``
    and the easternmost ``00``. ``MOVE`` has to be the arm with three columns — it
    paints twice and a band only runs one way — so ``MOVE`` takes the west end, and
    the CPU-visible numbering follows from that, not the other way round.
    """
    codes = pu.arm_codes()
    assert codes == {"CELL": 0, "ROBOT": 1, "FLAG": 2, "MOVE": 3}
    cols = pu.arm_columns()
    assert cols["MOVE"] == min(cols.values())
    assert list(cols.values()) == sorted(cols.values())
    # MOVE's three columns must not reach its neighbour's leaf.
    assert cols["MOVE"] + 2 < min(c for a, c in cols.items() if a != "MOVE")


def test_the_command_word_survives_a_negative_argument() -> None:
    """``8*arg + code`` with a floored ``/`` (``SPEC.md``) — the block's interface."""
    for arg in (-9, -1, 0, 1, 255):
        for code in range(4):
            w = pu.word(code, arg)
            assert w & 7 == code
            assert w >> 3 == arg


def test_every_arm_is_a_placed_column_with_a_documented_protocol() -> None:
    """The decode leaves and their implemented commands cannot drift apart."""
    assert set(pu.ARM_STUBS) == set(pu.ARMS)
    cells = pu.unit_interior().cells
    for arm, x in pu.arm_columns().items():
        # every arm starts with the same four glyphs: A = arg, B = code
        assert "".join(cells[(x, pu.R_ARG + i)] for i in range(4)) == "M8W/", arm
        # and every arm ends on the collector row, which walks back to MAIN
        assert cells[(1, pu.R_COLLECT)] == "^"


# ── the unit's interior: one wall out, two pipes in ──────────────────────────
def test_every_send_leaves_by_the_east_wall_on_its_own_bands_row() -> None:
    """Rule 1: the row *is* the pipe.

    All four outgoing pipes attach to the east wall, so the ``IW + 1 - x`` term
    cancels and row distance alone decides. An ``s`` on its own row is 0 away and
    every rival at least 1 — which is what makes "which row a glyph is on" a free
    layout variable inside an arm's column.
    """
    unit = pu.unit_interior()
    sends = [(x, y, b) for x, y, g, b in unit.glyphs if g == "s"]
    assert sends, "no sends at all"
    for x, y, band in sends:
        wall, row = pu.BANDS[band]
        assert wall == "east", f"s@{(x, y)} claims {band}, which is not on the east wall"
        assert y == row, f"s@{(x, y)} claims {band}, whose row is {row}"
    assert {b for *_, b in sends} == {"reg", "addr", "data", "swap"}


def test_the_two_receives_come_in_on_different_walls() -> None:
    """``cmd`` from the north, the register's return from the east."""
    unit = pu.unit_interior()
    recvs = [(x, y, b) for x, y, g, b in unit.glyphs if g == "r"]
    assert [b for *_, b in recvs].count("cmd") == 1, "exactly one reader of the command pipe"
    assert [b for *_, b in recvs].count("reg_ret") == 1, "exactly one reader of the register"
    assert {pu.BANDS[b][0] for *_, b in recvs} == {"north", "east"}


def test_every_glyph_is_nearer_its_own_pipe_than_the_runner_up() -> None:
    """A margin of 0 would be a reading-order tie, i.e. a coin flip."""
    margins = pu.binding_margins()
    assert margins, "no pipe glyphs at all"
    for (x, y), margin in margins.items():
        assert margin >= 1, f"the glyph at {(x, y)} is only {margin} nearer its own pipe"


def test_the_command_port_had_to_move_east_of_main() -> None:
    """The one binding this block cannot get by argument (module docstring, rule 2).

    ``MOVE`` is the westernmost arm and its ``r`` stands nine rows below ``MAIN``'s.
    With the command port in ``MAIN``'s own column — snake's arrangement — that ``r``
    is 9 from ``cmd`` and 14 from the register, so ``MOVE`` would read the *next
    command* instead of the robot's cell. Moving the port to :data:`pu.CMD_COL`
    costs nothing and reverses both distances; this test is the counterfactual, so
    the constant cannot drift back without a failure.
    """
    unit = pu.unit_interior()
    (rx, ry, _g, _b) = next(g for g in unit.glyphs if g[3] == "reg_ret")
    reg_dist = (pu.UNIT_IW + 1 - rx) + abs(ry - pu.R_REG_RET)
    main_col = next(x for x, _y, _g, b in unit.glyphs if b == "cmd")
    assert abs(rx - main_col) + ry < reg_dist, "the counterfactual is supposed to be a misbind"
    assert abs(rx - pu.CMD_COL) + ry > reg_dist, "MOVE's `r` must prefer the register"
    assert pu.BANDS["cmd"] == ("north", pu.CMD_COL)


# ── the panel and its three ports ────────────────────────────────────────────
def test_the_three_panel_pipes_are_related_in_length() -> None:
    """Three pipes, three transit times: the relation is the contract.

    ``swap > data`` is deliberately *stricter* than snake's ``swap >= data`` — a
    sibling agent is measuring on the engine whether the tie is safe, and until that
    lands the tie is treated as a bug.
    """
    blk = pu.build_path()
    assert blk.lengths["addr"] == blk.lengths["data"], "a pixel would land at a stale cursor"
    assert blk.lengths["swap"] > blk.lengths["data"], "a commit could overtake its pixels"
    # the skew has to stay under the gap between two commands (collector walk + decode)
    assert blk.lengths["swap"] - blk.lengths["addr"] < pu.MAX_SKEW
    assert blk.lengths["addr"] >= 2, "SPEC.md: a pipe is at least two cells"


def test_the_panel_spans_addrs_column_and_the_descents_do_not_cross() -> None:
    """ADDR is the one pipe with no corridor row to turn on (ARCH §4.4).

    It drops straight into the top wall, so the panel must span its column. DATA
    turns west into the left wall and SWAP has to reach *under* the panel from
    further west still — SWAP leaves on the lowest band row, so with the columns the
    other way round its eastward leg would cross DATA's descent.
    """
    blk = pu.build_path()
    px, py = blk.panel
    assert (px, py) == pu.PANEL_AT
    assert px < pu.ADDR_COL < px + pu.PANEL + 1
    assert pu.SWAP_COL < pu.DATA_COL < px
    assert pu.R_ADDR < pu.R_DATA < pu.R_SWAP, "the band rows order the descents"


def test_the_builder_counts_the_pipes_it_drew() -> None:
    """The interface the CPU sees: one outgoing command pipe, and nothing else.

    An incoming response pipe would be a rival for *every* ``r`` in the CPU (§7.1),
    including the jump slab's ROM read — which is why this unit answers nothing.
    """
    blk = pu.build_path()
    assert blk.pipes == len(blk.lengths) == 5  # reg x2, addr, data, swap
    assert set(blk.lengths) == {"reg", "reg_ret", "addr", "data", "swap"}
    cx, cy = blk.cmd_cell
    assert cy == pu.UY - 1, "the command pipe must arrive from the north"
    assert cx == pu.UX + pu.CMD_COL


def test_the_bounding_box_is_40x33() -> None:
    """The box is the score (``max(w, h)^2 * avg_ticks``), so it is pinned here.

    **40 columns**: the unit room is 18 wide (0..17); columns 18..21 are the four
    the pipes need east of it — the shared start column, SWAP's descent, DATA's
    descent plus the register's forward turn, and the register's return turn — and
    the 16x16 panel's room is the last 18 (22..39).

    **33 rows**: the register's relay is rows 0..4, the unit room 1..20, the panel
    room 13..30 (it hangs beside the unit, not below it, which is what keeps the
    height down), and SWAP's leg under the panel needs rows 31..32.

    Every row/column here is accounted for; a change that grows either number is a
    score regression and should be argued for in the commit, not absorbed.
    """
    blk = pu.build_path()
    assert (blk.width, blk.height) == (40, 33)
    assert max(blk.width, blk.height) ** 2 == 1600
    xs = [x for x, _y in blk.cells]
    ys = [y for _x, y in blk.cells]
    assert (min(xs), min(ys)) == (0, 0), "the block is flush with its own origin"
    assert (max(xs), max(ys)) == (blk.width - 1, blk.height - 1)


def test_the_probe_is_the_block_plus_one_driver_room() -> None:
    """``build_probe`` may not change the thing it measures."""
    rows, dbg, blk = pu.build_probe(PROBE_COMMANDS)
    assert (blk.width, blk.height) == (40, 33)
    ox, oy = pu.BLOCK_AT
    for (x, y), ch in blk.cells.items():
        assert rows[oy + y][ox + x] == ch, f"the block moved at {(x, y)}"
    # `regions` holds Region objects, not names, so this has to go through `.name` —
    # `"driver" in dbg.regions` is a string against a list of records and is False for
    # every possible map, which is exactly how it passed for nothing.
    named = {r.name for r in dbg.regions}
    assert "driver" in named, sorted(named)
    # and the block's own rooms are all still on the overlay beside it
    assert {"unit", "unit:main", "unit:trie", "relay", "panel"} <= named, sorted(named)
    for arm in pu.ARMS:
        assert f"unit:{arm}" in named, f"the {arm} arm is unnamed in the overlay"
    # The ladder holds one literal per command, so the *driver room* grows by the
    # rendered literal (`digits` plus two backticks and `s`), with four rows of frame.
    # The whole grid does not: the block is taller than this four-command driver.
    # Asserting `len(short) < len(rows)` therefore compares the block against itself;
    # measure the room that moves, not the page it sits on.
    def driver_h(n: int) -> int:
        _rows, d, _blk = pu.build_probe(PROBE_COMMANDS[:n])
        return next(r for r in d.regions if r.name == "driver").h

    heights = [driver_h(n) for n in range(1, len(PROBE_COMMANDS) + 1)]
    expected = []
    height = 4
    for command in PROBE_COMMANDS:
        height += len(str(command)) + 3
        expected.append(height)
    assert heights == expected, heights
    assert all(h < blk.height for h in heights), "the driver outgrew the block it drives"


# ── the engine ───────────────────────────────────────────────────────────────
@node_required
def test_the_probe_loads_and_runs_without_a_fatal(probe: tuple[Path, pu.PathBlock]) -> None:
    """Loads, and 800 ticks of it do not fault.

    A load error, a corner attachment, a wrong pipe body glyph or a display range
    fault would all show up here — the unit never halts (its ``MAIN`` blocks on an
    empty command pipe forever), so the question is only whether anything *faults*.
    """
    from randomfun2026solvers.littleman import Littleman

    path, _blk = probe
    snap = Littleman().tick(path, 800)
    assert snap.fatal is None, snap.fatal
    assert snap.reason is None, snap.reason
    assert not snap.halted, "the unit is a servant: it waits for the next command"


@node_required
@pytest.mark.slow
def test_the_hardware_arms_commit_exactly_the_modelled_frames(
    probe: tuple[Path, pu.PathBlock],
) -> None:
    """The real glyph programs, not :class:`store.PathUnit`'s Python model.

    CELL streams a wall at cursor 0; ROBOT commits at 34; FLAG paints 50 without
    committing; MOVE erases 34, moves to 35, and commits once.
    """
    from randomfun2026solvers.littleman import Littleman

    def frame(values: dict[int, str]) -> list[str]:
        cells = ["0"] * (pu.PANEL * pu.PANEL)
        for cell, colour in values.items():
            cells[cell] = colour
        return ["".join(cells[y * pu.PANEL : (y + 1) * pu.PANEL]) for y in range(pu.PANEL)]

    want = [
        frame({0: "7", 34: "a"}),
        frame({0: "7", 35: "a", 50: "9"}),
    ]
    path, _blk = probe
    (res,) = Littleman().display_frames(
        path,
        [{"name": "path-unit", "rounds": [{"in": [], "frames": want}]}],
        max_ticks=20_000,
    )
    assert res.fatal is None, res.fatal
    assert res.output == []
    assert res.frames == want


@node_required
@pytest.mark.slow  # one `lm.mjs route` boot per glyph
def test_every_glyph_binds_the_pipe_the_generator_intended(
    probe: tuple[Path, pu.PathBlock],
) -> None:
    """The engine's own ``route``, not the generator's arithmetic (ARCH §7.1).

    Each pipe is identified by the cell it starts and ends on, so a glyph that
    quietly re-bound to a neighbouring band fails with both pipes named.
    """
    from randomfun2026solvers.littleman import Littleman

    path, blk = probe
    ox, oy = pu.BLOCK_AT
    lm = Littleman()
    want = {
        "cmd": (ox + blk.cmd_cell[0], oy + blk.cmd_cell[1]),
        **{
            band: (ox + pu.EAST, oy + pu.UY + row)
            for band, (wall, row) in pu.BANDS.items()
            if wall == "east"
        },
    }
    for x, y, glyph, band in blk.glyphs:
        cells = [(c.x, c.y) for c in lm.route(path, ox + x, oy + y)]
        assert cells, f"{glyph}@{(x, y)} binds no pipe at all"
        assert want[band] in (cells[0], cells[-1]), (
            f"{glyph}@{(x, y)} should bind {band} at {want[band]} but got {(cells[0], cells[-1])}"
        )


@node_required
@pytest.mark.slow
def test_route_check_agrees_with_the_generator(probe: tuple[Path, pu.PathBlock]) -> None:
    """``route-check.mjs`` is the one people skip (``DEBUGGING.md``)."""
    path, blk = probe
    out = subprocess.run(
        ["node", str(ROUTE_CHECK), str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "ERR" not in out
    # the block's five pipes plus the probe's command pipe, and nothing else: an
    # extra pipe means a leg ran alongside a room corner and the engine split it.
    assert len(pu.build_path().lengths) + 1 == sum(
        1
        for line in out.splitlines()
        if line.strip().startswith(("0:", "1:", "2:", "3:", "4:", "5:"))
    )
    # every pipe glyph in the grid: the unit's own, the relay's `r`/`s` pair, and one
    # `s` per command word in the driver's ladder.
    n = len(blk.glyphs) + 2 + len(PROBE_COMMANDS)
    assert out.count("'s' at") + out.count("'r' at") == n
