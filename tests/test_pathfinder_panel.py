"""The `pathfinder` display block: geometry in the fast tier, frames in the slow one.

Everything that can be decided without an engine is decided here in milliseconds —
the pipe-binding table, the delivery-ordering inequalities, the bounding box, and
the painter's *timing*, which is walked off the glyphs rather than trusted to the
constants in the module. That last one matters: :func:`delivery_ok` compares pipe
lengths against tick gaps, so a one-cell edit to the painter silently invalidates
the ordering argument unless the gaps are re-derived from the grid.

The engine runs are marked slow. They are the only proof that the block actually
draws, and they drive the exact stream the solver will: a 256-pixel setup frame,
then two single-pixel deltas, then a three-pixel run — three commits, compared
frame for frame.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.circuit import Circuit  # noqa: E402
from randomfun2026solvers.pathfinder_panel import (  # noqa: E402
    BLOCK_H,
    BLOCK_W,
    GAP_ADDR_DATA,
    GAP_DATA_ADDR,
    GAP_DATA_DATA,
    GAP_DATA_SWAP,
    GAP_SWAP_ADDR,
    P_ADDR,
    P_DATA,
    P_SWAP,
    PAINTER_IH,
    PAINTER_IW,
    PROBE_OX,
    PROBE_OY,
    build_block,
    build_probe,
    delivery_ok,
    expected_frames,
    free_cells,
    painter,
    send_bindings,
    stream_for,
)

GRID = REPO / "tasks" / "solutions" / "pathfinder_panel_probe.man"
LM_MJS = REPO / "littleman" / "lm.mjs"

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)

# A 256-pixel setup frame, two single-pixel deltas committed together, and a
# three-pixel run that exercises the cursor's auto-advance.
RUNS: list[list[tuple[int, list[int]]]] = [
    [(0, [(x * 7 + y * 3) % 16 for y in range(16) for x in range(16)])],
    [(0x11, [9]), (0xEE, [3])],
    [(0x40, [1, 2, 4])],
]


def _block() -> dict:
    g = Circuit(BLOCK_W, BLOCK_H)
    return build_block(g, 0, 0)


# ── the painter, walked ───────────────────────────────────────────────────────
_TURN = {">": (1, 0), "<": (-1, 0), "^": (0, -1), "v": (0, 1)}
_CW = {(1, 0): (0, 1), (0, 1): (-1, 0), (-1, 0): (0, -1), (0, -1): (1, 0)}
_CCW = {v: k for k, v in _CW.items()}
_PORT = {P_DATA: "data", P_ADDR: "addr", P_SWAP: "swap"}


def _walk(stream: list[int], ticks: int = 4000) -> list[tuple[int, str, int]]:
    """Run the painter's interior as a little man; return ``(tick, port, value)`` sends.

    An op-level model of exactly the glyphs :func:`painter` uses. Running dry on
    the input ends the walk, which is how the real painter idles.
    """
    rows = painter().rows()
    spawn = next((x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "@")
    pos, d, a, bp = spawn, (1, 0), 0, 0
    queue = list(stream)
    sends: list[tuple[int, str, int]] = []
    for tick in range(ticks):
        x, y = pos
        if not (0 <= x < PAINTER_IW and 0 <= y < PAINTER_IH):
            raise AssertionError(f"the man walked into the wall at {pos} on tick {tick}")
        ch = rows[y][x]
        if ch in _TURN:
            d = _TURN[ch]
        elif ch in " @":
            pass
        elif ch.isdigit():
            a = int(ch)
        elif ch == "r":
            if not queue:
                return sends
            a = queue.pop(0)
        elif ch == "s":
            sends.append((tick, _PORT[(x, y)], a))
        elif ch == "b":
            bp = a
        elif ch == "m":
            bp -= 1
        elif ch == "d":
            d = _CW[d] if bp > 0 else d
        elif ch == "a":
            d = _CCW[d] if bp > 0 else d
        else:  # pragma: no cover - the glyph set is closed
            raise AssertionError(f"unmodelled glyph {ch!r} at {pos}")
        pos = (x + d[0], y + d[1])
    raise AssertionError("the painter never ran dry")


def test_the_painters_first_act_is_a_read_not_a_commit() -> None:
    """A spurious first frame fails the streaming compare on frame 0."""
    rows = painter().rows()
    spawn = next((x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "@")
    pos, d = spawn, (1, 0)
    for _ in range(20):
        ch = rows[pos[1]][pos[0]]
        if ch not in " @" and ch not in _TURN:
            assert ch == "r", f"the man's first real act is {ch!r}, not a read"
            return
        if ch in _TURN:
            d = _TURN[ch]
        pos = (pos[0] + d[0], pos[1] + d[1])
    raise AssertionError("the man never reached an instruction")


def test_the_painter_speaks_the_run_length_protocol() -> None:
    sends = _walk(stream_for(RUNS))
    want: list[tuple[str, int]] = []
    for frame in RUNS:
        for addr, colours in frame:
            want.append(("addr", addr))
            want += [("data", c) for c in colours]
        want.append(("swap", 1))
    assert [(p, v) for _t, p, v in sends] == want


def test_the_tick_gaps_are_what_delivery_ok_assumes() -> None:
    """Re-derive every ``GAP_*`` by walking the glyphs, not by trusting the module."""
    sends = _walk(stream_for(RUNS))
    gap = {}
    for (t0, p0, _v0), (t1, p1, _v1) in zip(sends, sends[1:], strict=False):
        gap.setdefault((p0, p1), set()).add(t1 - t0)
    assert gap[("addr", "data")] == {GAP_ADDR_DATA}
    assert gap[("data", "data")] == {GAP_DATA_DATA}
    assert gap[("data", "addr")] == {GAP_DATA_ADDR}
    assert gap[("data", "swap")] == {GAP_DATA_SWAP}
    assert gap[("swap", "addr")] == {GAP_SWAP_ADDR}


# ── geometry ──────────────────────────────────────────────────────────────────
def test_the_painter_interior_is_eleven_by_three() -> None:
    c = painter()
    assert (c.w, c.h) == (PAINTER_IW, PAINTER_IH)
    # the east-wall descent column has to stay inside the panel's top wall, which
    # is what caps the interior width; see the module docstring, rule 2.
    assert PAINTER_IW <= 12


def test_every_send_binds_the_port_it_is_named_for() -> None:
    """The generator's own assertion, re-stated: each margin must be strictly positive."""
    for name, dists in send_bindings().items():
        rest = sorted(v for port, v in dists.items() if port != name)
        assert dists[name] < rest[0], f"s@{name.upper()} is not the strict nearest: {dists}"


def test_the_block_is_twenty_by_twenty_four() -> None:
    info = _block()
    assert (info["w"], info["h"]) == (20, 24) == (BLOCK_W, BLOCK_H)
    # snake_ring's equivalent block; the win comes from deleting the two-row band
    # between the painter and the panel.
    assert max(info["w"], info["h"]) < max(22, 26)


def test_the_block_fits_its_own_bounding_box() -> None:
    """Stamping into an exactly-sized grid must not raise, and must not spill."""
    g = Circuit(BLOCK_W, BLOCK_H)
    build_block(g, 0, 0)
    assert all(0 <= x < BLOCK_W and 0 <= y < BLOCK_H for x, y in g.cell)


def test_the_incoming_pipe_terminates_west_of_the_painter() -> None:
    info = _block()
    assert info["in_side"] == "west"
    in_x, in_y = info["in_cell"]
    px, py = info["painter_at"]
    assert (in_x + 1, in_y) == (px - 1, py), "in_cell must abut the painter's west wall"
    g = Circuit(BLOCK_W, BLOCK_H)
    build_block(g, 0, 0)
    assert g.get(in_x, in_y) == " ", "the block must leave in_cell free for the worker"


def test_the_free_cells_include_the_incoming_lane() -> None:
    free = set(free_cells())
    info = _block()
    assert info["in_cell"] in free
    # the two columns west of the painter above the DATA gutter are the approach
    assert {(0, 0), (0, 1), (0, 2), (1, 0)} <= free


# ── delivery ordering ─────────────────────────────────────────────────────────
def test_the_measured_pipe_lengths_deliver_in_order() -> None:
    info = _block()
    assert (info["l_addr"], info["l_data"], info["l_swap"]) == (4, 12, 29)
    assert delivery_ok(info["l_addr"], info["l_data"], info["l_swap"])


def test_delivery_ok_rejects_each_way_the_order_can_break() -> None:
    l_addr, l_data, l_swap = 4, 12, 29
    # a run's ADDR arriving after its own first colour
    assert not delivery_ok(l_data + GAP_ADDR_DATA + 1, l_data, l_swap)
    assert delivery_ok(l_data + GAP_ADDR_DATA, l_data, l_swap)
    # the next run's ADDR overtaking colours still in flight (strict: same tick
    # loses, because the display processes ADDR before DATA)
    assert not delivery_ok(l_addr, l_addr + GAP_DATA_ADDR, l_swap)
    assert delivery_ok(l_addr, l_addr + GAP_DATA_ADDR - 1, l_swap)
    # SWAP overtaking the colours it is meant to commit
    assert not delivery_ok(l_addr, l_data, l_data - GAP_DATA_SWAP - 1)
    # the next frame's colours overtaking the SWAP
    assert not delivery_ok(l_addr, l_data, l_data + GAP_SWAP_ADDR + GAP_ADDR_DATA)


# ── the checked-in grid ───────────────────────────────────────────────────────
def test_the_checked_in_probe_matches_the_generator() -> None:
    assert GRID.exists(), f"{GRID} is missing; run the generator's --man"
    assert GRID.read_text() == "\n".join(build_probe()) + "\n"


def test_the_probe_is_the_block_plus_one_input_room() -> None:
    rows = build_probe()
    assert len(rows) == BLOCK_H
    assert max(len(r) for r in rows) == PROBE_OX + BLOCK_W
    assert sum(r.count("I") for r in rows) == 1
    assert sum(r.count("@") for r in rows) == 1
    assert PROBE_OY == 0


# ── the engine ────────────────────────────────────────────────────────────────
@pytest.mark.slow
@node_required
def test_the_probe_commits_exactly_the_expected_frames_on_the_reference_engine() -> None:
    from randomfun2026solvers.littleman import Littleman

    want = expected_frames(RUNS)
    runs = Littleman().display_frames(
        GRID,
        [{"name": "pathfinder-panel", "rounds": [{"in": stream_for(RUNS), "frames": want}]}],
        max_ticks=200_000,
    )
    (res,) = runs
    assert res.fatal is None, res.fatal
    assert res.output == [], "a display-judged problem may emit no program output"
    assert (res.width, res.height) == (16, 16)
    assert res.frames == want


@pytest.mark.slow
@node_required
def test_the_engine_judges_the_frames_and_commits_no_others() -> None:
    from randomfun2026solvers.littleman import Littleman

    lm = Littleman()
    stream = stream_for(RUNS)
    snap = lm.judge(GRID, input=stream, frames=[expected_frames(RUNS)], max_ticks=200_000)
    assert snap.fatal is None
    assert snap.frame_judge is not None and snap.frame_judge.passed, snap.frame_judge
    assert snap.frame_judge.matched == 3
    # nothing spurious before, between or after: the panel's own commit counter
    # long after the stream ran dry is still three.
    late = lm.tick(GRID, 20_000, input=stream)
    (panel,) = late.entities.displays
    assert panel.frames == 3
    assert panel.rows() == expected_frames(RUNS)[-1]


@pytest.mark.slow
def test_the_probe_passes_the_frame_judge_on_fast_littleman() -> None:
    from randomfun2026solvers.fast_littleman import FastLittleman

    res = FastLittleman(GRID).run(
        stream_for(RUNS), frames=[expected_frames(RUNS)], max_ticks=200_000
    )
    assert res.fatal is None, res.fatal
    assert res.passed is True
    assert res.output == []


@pytest.mark.slow
@node_required
def test_every_send_binds_the_pipe_the_generator_intended_on_the_engine() -> None:
    """``route-check.mjs``'s question, asked through ``Littleman.route``."""
    from randomfun2026solvers.littleman import Littleman

    lm = Littleman()
    info = _block()
    px, py = PROBE_OX + 3, PROBE_OY + 1  # painter interior origin in the probe
    ends = {
        "addr": (px + PAINTER_IW + 1, py + 1),
        "swap": (px + PAINTER_IW + 1, py + 0),
        "data": (px - 2, py + 2),
    }
    for name, (sx, sy) in {"data": P_DATA, "addr": P_ADDR, "swap": P_SWAP}.items():
        cells = lm.route(GRID, px + sx, py + sy)
        assert cells, f"s@{name.upper()} binds no pipe"
        assert (cells[0].x, cells[0].y) == ends[name], (
            f"s@{name.upper()} at {(px + sx, py + sy)} binds a pipe starting at "
            f"{(cells[0].x, cells[0].y)}, wanted {ends[name]}"
        )
    assert (info["l_addr"], info["l_data"], info["l_swap"]) == (
        len(lm.route(GRID, px + P_ADDR[0], py + P_ADDR[1])),
        len(lm.route(GRID, px + P_DATA[0], py + P_DATA[1])),
        len(lm.route(GRID, px + P_SWAP[0], py + P_SWAP[1])),
    )
