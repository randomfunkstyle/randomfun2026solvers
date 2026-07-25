"""The SNAKE unit: geometry that must hold, and a game played on the real engine.

Three things are checked, and the middle one is the one that would rot silently.

* **The geometry.** Every ``r``/``s`` sits on its own band's row, with a computed
  margin — the block's whole design rests on "a glyph on its pipe's row binds that
  pipe", and ``ADDR`` is one row from ``ring``. The engine's own ``route`` is the
  oracle for the placed block.
* **The ring discipline.** A ``STEP`` that finds no match must leave the ring in
  *exactly* its original rotation, or every later tick reads the wrong cell. That
  is invisible in a single frame and fatal three frames later, so it is proved by
  replaying a whole game against :mod:`snake_sim`.
* **The panel ports.** ADDR/DATA/SWAP are three pipes with three transit times; a
  DATA that overtakes its ADDR paints the wrong pixel. Their lengths are part of
  the contract, not an accident of routing.
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

from randomfun2026solvers import snake_sim  # noqa: E402
from randomfun2026solvers.lm1 import snake_unit as su  # noqa: E402

LM_MJS = REPO / "littleman" / "lm.mjs"
PROBE = REPO / "littleman" / "examples" / "snake-unit-probe.man"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)

#: A straight snake, oldest cell first: the tail is 16, the head 21.
BODY = [16, 17, 18, 19, 20, 21]


# ── the trie and the word encoding ───────────────────────────────────────────
def test_codes_come_from_the_tries_geometry() -> None:
    """The codes are *read off* the trie, not assigned.

    ``x`` turns clockwise on BP's low bit and a man heading south turns clockwise
    to the west, so each west branch sets that level's bit. If a leaf moves these
    numbers move with it — which is why the CPU has to take them from here.
    """
    codes = su.arm_codes()
    assert set(codes) == set(su.ARMS)
    assert sorted(codes.values()) == [0, 1, 2, 3]
    # STEP is the easternmost leaf because it is the only arm that outgrows its
    # six columns; nothing may sit east of it.
    cols = su.arm_columns()
    assert cols["STEP"] == max(cols.values())
    assert list(cols.values()) == sorted(cols.values())


def test_the_command_word_survives_a_negative_argument() -> None:
    """``8*arg + code`` with a floored ``/`` (``SPEC.md``) — the block's interface."""
    for arg in (-9, -1, 0, 1, 255, 50 * 256 + 255):
        for code in range(4):
            w = su.word(code, arg)
            assert w & 7 == code
            assert w >> 3 == arg


def test_step_splits_its_argument_the_way_the_arm_does() -> None:
    """``n`` in the high part is what lets ``B`` hold the candidate for the lap.

    ``/`` puts the quotient in ``A`` and the remainder in ``B``, and ``-`` does not
    disturb ``B`` — so with the count high, the candidate survives every iteration
    for free. Reverse the two and the arm needs a register it does not have.
    """
    for n in (1, 2, 50):
        for cell in (0, 1, 255):
            arg = n * 256 + cell
            assert arg // 256 == n and arg % 256 == cell


# ── the unit's interior ──────────────────────────────────────────────────────
def test_every_send_sits_on_its_own_bands_row() -> None:
    """Rule 1: the row *is* the pipe.

    All four outgoing pipes attach to the east wall, so the ``IW + 1 - x`` term
    cancels and the row distance alone decides. An ``s`` on its own row is 0 away
    and every rival at least 1 — which is exactly what lets ``ring`` (20) and
    ``addr`` (21) be adjacent.
    """
    unit = su.unit_interior()
    for x, y, glyph, band in unit.glyphs:
        if glyph != "s":
            continue
        wall, row = su.BANDS[band]
        assert wall == "east", f"{band} is not on the east wall"
        assert y == row, f"s@{(x, y)} claims {band}, whose row is {row}"


def test_every_receive_prefers_its_own_pipe_with_a_margin() -> None:
    """Rule 2: only two pipes are incoming, and ``cmd``'s ``y`` term settles it.

    ``MAIN``'s ``r`` is one cell from ``cmd``; every arm's ``r`` is deep in the
    unit, where ``cmd`` pays its whole row index. A margin of 0 would be a
    reading-order tie — a coin flip — so the assertion is ``>= 1``.
    """
    unit = su.unit_interior()
    margins = su.binding_margins(unit)
    assert margins, "no pipe glyphs at all"
    for (x, y), margin in margins.items():
        assert margin >= 1, f"glyph at {(x, y)} is only {margin} nearer its own pipe"
    receives = [(x, y, band) for x, y, g, band in unit.glyphs if g == "r"]
    assert [b for *_, b in receives].count("cmd") == 1
    assert min(margins.values()) >= 1


def test_the_step_lap_cannot_exit_early() -> None:
    """No early exit means no desynchronised ring — the whole point of the sentinel.

    The match lane is the *body's own tail* (``1 N M``): it continues straight out
    of ``X``, installs ``-1`` in ``B`` and falls into the loop's bottom corner, so
    the decrement always runs and the lap always completes ``n-1`` times. A version
    that jumped out of the loop would have to drain the rest of the ring by hand.
    """
    assert su.STEP_BODY == "rs-X1NM"
    assert su.STEP_BODY.startswith("rs-X")
    assert su.STEP_BODY[su.STEP_BODY.index("X") + 1 :] == "1NM"
    # `r` on the row above the ring band, `s` on it: the value goes straight back.
    assert su.R_LOOP + 1 + su.STEP_BODY.index("s") == su.R_RING


def test_the_red_lap_is_one_counted_loop_body() -> None:
    """``RED``'s whole lap is ``"rss  9s"`` — read, re-send, ADDR, pad, ``9``, DATA.

    The padding is not decoration: it is what puts each send on its own band's row
    (``data`` is four rows below ``addr`` because ``GROW`` needs three glyphs there
    to make 10, and one body has to fit both).
    """
    rows = {su.R_LOOP + 1 + i: ch for i, ch in enumerate(su.RED_BODY)}
    assert rows[su.R_RING] == "s"
    assert rows[su.R_ADDR] == "s"
    assert rows[su.R_DATA] == "s"
    assert rows[su.R_DATA - 1] == "9"


def test_grow_makes_green_without_a_literal() -> None:
    """``5 M +`` is A = 10: three cells against a backtick pair's five, and no
    backtick means no accidental vertical pairing with a neighbouring arm."""
    cells = su.unit_interior().cells
    x = su.arm_columns()["GROW"]
    assert "".join(cells[(x, su.R_ADDR + 1 + i)] for i in range(3)) == "5M+"
    ticks = [(p, ch) for p, ch in cells.items() if ch == "`"]
    assert len(ticks) == 2, "only STEP's 256 needs a literal"
    assert len({x for (x, _), _ in ticks}) == 1, "both backticks in one column: a vertical pair"


def test_the_panel_ports_are_on_three_different_walls() -> None:
    """Which side a pipe lands on is what makes a port that port (``SPEC.md``)."""
    blk = su.build_snake()
    px, py = blk.panel
    assert blk.lengths["addr"] == blk.lengths["data"], "a pixel would land at a stale cursor"
    assert blk.lengths["swap"] >= blk.lengths["data"], "a commit could overtake its pixels"
    # skew must stay under the gap between two commands (~80 ticks, measured)
    assert blk.lengths["swap"] - blk.lengths["addr"] < 60
    assert (px, py) == su.PANEL_AT
    assert blk.ring >= 51, f"ring holds {blk.ring} values; 50 body + 1 header needed"


def test_the_block_is_one_room_short_of_a_machine() -> None:
    """The interface the CPU sees: one outgoing command pipe, and nothing else.

    An incoming response pipe would be a rival for *every* ``r`` in the CPU
    (§7.1), including the jump slab's ROM read — which is why this unit answers
    nothing and acts on its own findings instead.
    """
    blk = su.build_snake()
    assert blk.pipes == 5  # ring x2, addr, data, swap — the cmd pipe is the caller's
    cx, cy = blk.cmd_cell
    assert cy == su.UY - 1, "the command pipe must arrive from the north"
    assert cx == su.UX + su.BANDS["cmd"][1]


def test_the_checked_in_probe_still_matches_the_generator() -> None:
    """The fast tier must fail if the generator changes shape (``AGENTS.md``)."""
    rows, _dbg, _blk = su.build_probe()
    assert PROBE.read_text(encoding="utf-8") == "\n".join(rows) + "\n"


def test_commands_for_rounds_is_the_cpus_whole_job() -> None:
    """One word per drawing round, and the CPU never looks at the body.

    A direction round draws nothing and sends nothing; a tick becomes ``GROW`` if
    it eats, ``RED`` if it leaves the grid (arithmetic the unit cannot do) and
    ``STEP`` otherwise — the unit decides self-collision on its own.
    """
    codes = su.arm_codes()
    rounds = [[2, 2], [1, 5, 2], [0], [3], [0], [0]]
    cmds = su.commands_for_rounds(rounds)
    assert len(cmds) == sum(1 for f in snake_sim.simulate(rounds) if f)
    assert cmds[0] == su.word(codes["GROW"], 2 * 16 + 2)
    assert cmds[1] == su.word(codes["FRUIT"], 2 * 16 + 5)
    assert cmds[2] & 7 == codes["STEP"]
    # walking off the east edge is a RED, and its argument is the body length
    off = su.commands_for_rounds([[15, 0], [0]])
    assert off[1] == su.word(codes["RED"], 1)


# ── the real engine ──────────────────────────────────────────────────────────
def _frames(cmds: list[int], count: int, *, cap: int = 200_000) -> list[list[str]]:
    """Commit ``count`` frames from one ungated round of ``cmds``."""
    from randomfun2026solvers.littleman import Littleman

    case = {"name": "probe", "rounds": [{"in": cmds, "frames": [["0" * 16] * 16] * count}]}
    run = Littleman().display_frames(PROBE, [case], max_ticks=cap)[0]
    assert run.fatal in (None, ""), run.fatal
    return run.frames


def _painted(frame: list[str], colour: str) -> set[int]:
    return {y * 16 + x for y, row in enumerate(frame) for x, ch in enumerate(row) if ch == colour}


@node_required
@pytest.mark.slow  # 25 separate `lm.mjs route` boots — seconds, not milliseconds
def test_every_glyph_binds_the_pipe_the_generator_intended() -> None:
    """The engine's own ``route`` on all 25 glyphs, not the generator's arithmetic.

    Each pipe is identified by the cell it starts and ends on, so a glyph that
    quietly re-bound to a neighbouring band fails with both pipes named.
    """
    from randomfun2026solvers.littleman import Littleman

    blk = su.build_snake()
    lm = Littleman()
    want = {
        "cmd": blk.cmd_cell,
        **{b: (su.UX + su.UNIT_IW + 2, su.UY + r) for b, (w, r) in su.BANDS.items() if w == "east"},
    }
    for x, y, glyph, band in blk.glyphs:
        cells = [(c.x, c.y) for c in lm.route(PROBE, x, y)]
        assert cells, f"{glyph}@{(x, y)} binds no pipe at all"
        assert want[band] in (cells[0], cells[-1]), (
            f"{glyph}@{(x, y)} should bind {band} at {want[band]} but got {(cells[0], cells[-1])}"
        )


@node_required
def test_grow_paints_green_and_fruit_paints_red() -> None:
    """One commit per command, and the panel is persistent between them."""
    c = su.arm_codes()
    frames = _frames([su.word(c["GROW"], 34), su.word(c["FRUIT"], 37)], 2, cap=2000)
    assert _painted(frames[0], "a") == {34}
    assert _painted(frames[1], "a") == {34}, "the panel keeps `next`: SWAP = 1"
    assert _painted(frames[1], "9") == {37}


@node_required
def test_step_drops_the_tail_and_leaves_the_ring_aligned() -> None:
    """The invisible bug: a ``STEP`` that rotates wrong is only wrong *later*.

    ``RED`` reads the body out of the ring, so painting it after two ``STEP``s is
    the direct question "is the ring still in its original rotation": the answer is
    a frame, and a rotation error shows up as a missing or a stale cell.
    """
    c = su.arm_codes()
    cmds = [su.word(c["GROW"], b) for b in BODY]
    cmds += [su.word(c["STEP"], 6 * 256 + 22), su.word(c["STEP"], 6 * 256 + 23)]
    cmds += [su.word(c["RED"], 6)]
    frames = _frames(cmds, len(cmds), cap=20_000)
    assert _painted(frames[5], "a") == set(BODY)
    assert _painted(frames[6], "a") == {17, 18, 19, 20, 21, 22}, "tail 16 blackened, 22 appended"
    assert _painted(frames[7], "a") == {18, 19, 20, 21, 22, 23}
    assert _painted(frames[8], "9") == {18, 19, 20, 21, 22, 23}, "RED repaints the live body"


@node_required
def test_step_matches_anywhere_but_the_tail() -> None:
    """The tail is exempt because it moves first; everything else is a loss.

    Three probes on the same body: the newest cell (the last thing the lap
    compares), the second-oldest (the first thing it compares), and the tail
    itself, which must *not* match — stepping into the cell the tail has just
    vacated is legal, and the frame then looks unchanged.
    """
    c = su.arm_codes()
    seed = [su.word(c["GROW"], b) for b in BODY]
    for cell, dead in ((21, True), (17, True), (16, False)):
        frames = _frames([*seed, su.word(c["STEP"], 6 * 256 + cell)], 7, cap=20_000)
        if dead:
            assert _painted(frames[6], "9") == set(BODY), f"{cell} should be a loss"
            assert _painted(frames[6], "a") == set()
        else:
            assert _painted(frames[6], "a") == set(BODY), "the tail's own cell is legal"
            assert _painted(frames[6], "9") == set()


@node_required
@pytest.mark.slow
def test_the_ring_holds_the_longest_legal_snake() -> None:
    """50 values plus the header, which is the ring's real capacity requirement.

    ``STEP`` parks the count *in the ring* while it walks the lap, so the ring has
    to hold ``body + 1``. At 50 body cells that is 51 values in 52 cells of pipe —
    one free cell, which is exactly what keeps the send from blocking forever.
    """
    c = su.arm_codes()
    body = list(range(100, 150))  # 50 cells, none adjacent to the probes below
    cmds = [su.word(c["GROW"], b) for b in body]
    cmds += [su.word(c["STEP"], 50 * 256 + 200)]  # no match: drop 100, append 200
    cmds += [su.word(c["RED"], 50)]
    frames = _frames(cmds, len(cmds), cap=400_000)
    assert _painted(frames[49], "a") == set(body)
    assert _painted(frames[50], "a") == set(body[1:]) | {200}
    assert _painted(frames[51], "9") == set(body[1:]) | {200}


#: A game that hits every arm and both of ``STEP``'s outcomes: the opening frame,
#: three fruit spawns, three growths, ordinary ticks, a move into the cell the tail
#: has just vacated (legal — the rule ``STEP`` exists to honour) and finally a move
#: into the body, which is a loss.
SELF_COLLISION = [
    [2, 2], [1, 5, 2], [0], [0], [0], [1, 7, 2], [0], [0], [1, 8, 3], [0],
    [4], [0], [5], [0], [2], [0], [4], [0],
]  # fmt: skip
#: The other death: walking off the east edge. The unit cannot see the wall, so
#: the CPU sends ``RED`` — and the frame still has to match the oracle's, fruit
#: pixel included.
WALL_DEATH = [[13, 5], [1, 14, 5], [0], [1, 15, 5], [0], [0]]


@node_required
@pytest.mark.slow
@pytest.mark.parametrize(
    ("rounds", "nframes"), [(SELF_COLLISION, 14), (WALL_DEATH, 6)], ids=["self", "wall"]
)
def test_a_played_game_agrees_with_the_oracle_frame_for_frame(
    rounds: list[list[int]], nframes: int
) -> None:
    """The acceptance test: every commit, against :mod:`snake_sim`.

    Frame-for-frame equality is the only proof that covers the ring's *rotation*:
    a ``STEP`` that came back one place out would draw a plausible frame now and a
    wrong one two ticks later. Rounds are gated the way the judge gates them, so
    each command is released only once the previous frame has committed.
    """
    from randomfun2026solvers.littleman import Littleman

    want = snake_sim.simulate(rounds)
    cmds = iter(su.commands_for_rounds(rounds))
    case = {
        "name": "game",
        "rounds": [{"in": [next(cmds)] if frames else [], "frames": frames} for frames in want],
    }
    run = Littleman().display_frames(PROBE, [case], max_ticks=200_000)[0]
    expected = [f for frames in want for f in frames]
    assert run.fatal in (None, ""), run.fatal
    assert run.frames == expected
    assert len(run.frames) == nframes


@node_required
@pytest.mark.slow
def test_route_check_agrees_with_the_generator() -> None:
    """``route-check.mjs`` is the one people skip (``DEBUGGING.md``)."""
    out = subprocess.run(
        ["node", str(REPO / "littleman" / "tools" / "route-check.mjs"), str(PROBE)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "ERR" not in out
    # every pipe glyph in the grid: the unit's own, plus the ring relay's and the
    # driver relay's `r`/`s` pairs.
    n = len(su.build_snake().glyphs) + 4
    assert out.count("'s' at") + out.count("'r' at") == n
    assert out.count("pipes:") == 1
