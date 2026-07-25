"""The plotter block's op sequence, checked against the problem statement.

The statement's pseudocode is the oracle. The block computes the same pixels a
completely different way (a closed form on the display address, with the carry test
biased into a sign test), so this is the test that matters — if it passes, the only
thing left to get wrong is the ASCII.

Fast tier: every degenerate and boundary segment, plus a deterministic spread.
Slow tier: all 589,824 legal segments.
"""

from __future__ import annotations

import pytest

from randomfun2026solvers.plotter_block import (
    GAP_ADDR_DATA,
    GAP_DATA_SWAP,
    LAP_TICKS,
    OpModel,
    painter_replay,
    timing_ok,
    worker_round,
)

W, H = 32, 24


def bresenham(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """The problem statement's pseudocode, verbatim, in its symmetric error form."""
    dx, sx = abs(x1 - x0), (1 if x0 < x1 else -1)
    dy, sy = -abs(y1 - y0), (1 if y0 < y1 else -1)
    err = dx + dy
    out = []
    while True:
        out.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return out
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def addresses(x0: int, y0: int, x1: int, y1: int) -> list[int]:
    """What the block's two men actually put on the ADDR port, in order."""
    m = OpModel([x0, y0, x1, y1])
    incs = worker_round(m)
    base, n = m.paint[0], m.paint[1]
    assert n == len(incs), "the worker must send exactly one increment per pixel"
    return painter_replay(base, n, incs)


def check(x0: int, y0: int, x1: int, y1: int) -> None:
    want = [y * W + x for x, y in bresenham(x0, y0, x1, y1)]
    assert addresses(x0, y0, x1, y1) == want, f"segment {(x0, y0, x1, y1)}"


# ── the cases that break a line drawer ────────────────────────────────────────
DEGENERATE = [
    (0, 0, 0, 0), (31, 23, 31, 23), (5, 7, 5, 7),        # single point: M = 0
    (0, 0, 0, 23), (31, 23, 31, 0),                       # vertical: m = 0
    (0, 0, 31, 0), (31, 23, 0, 23),                       # horizontal: m = 0
    (0, 0, 23, 23), (23, 23, 0, 0), (0, 23, 23, 0),        # exact diagonal: m = M
    (0, 0, 2, 1), (2, 1, 0, 0), (0, 0, 1, 2), (1, 2, 0, 0),  # the exact-half tie
]
CORNERS = [
    (a, b, c, d)
    for a, b in ((0, 0), (31, 0), (0, 23), (31, 23))
    for c, d in ((0, 0), (31, 0), (0, 23), (31, 23))
]


@pytest.mark.parametrize("seg", DEGENERATE + CORNERS)
def test_degenerate_and_corner_segments(seg):
    check(*seg)


def test_direction_sensitivity():
    """A->B may pick different pixels than B->A; the block must not symmetrise."""
    differ = 0
    # These four really do pick different pixel sets in the two directions.
    for x0, y0, x1, y1 in [(0, 0, 1, 2), (0, 0, 1, 4), (0, 0, 2, 1), (2, 3, 9, 20)]:
        fwd, rev = addresses(x0, y0, x1, y1), addresses(x1, y1, x0, y0)
        assert fwd == [y * W + x for x, y in bresenham(x0, y0, x1, y1)]
        assert rev == [y * W + x for x, y in bresenham(x1, y1, x0, y0)]
        if fwd != list(reversed(rev)):
            differ += 1
    assert differ, "picked no asymmetric segment — the test proves nothing"


def test_spread():
    """A deterministic spread over every octant and both major axes."""
    for x0 in range(0, W, 7):
        for y0 in range(0, H, 5):
            for x1 in range(0, W, 5):
                for y1 in range(0, H, 7):
                    check(x0, y0, x1, y1)


def test_every_pixel_is_on_the_display():
    """No ADDR may leave the panel: out of range is a fatal error, not a no-op."""
    for seg in DEGENERATE + CORNERS:
        for a in addresses(*seg):
            assert 0 <= a < W * H, (seg, a)


def test_the_round_is_re_entrant():
    """A round must leave the ring exactly as it found it: empty.

    Each lap pops all four constants and pushes all four back to stay aligned, so
    when BP runs out they are still circulating; the drain at the end of the round
    consumes them. Without it every round after the first pops the *previous*
    round's constants in place of its own x0/y0 — each segment drawn alone was
    perfect and a sequence of them was garbage, which is what this pins down."""
    m = OpModel([0, 0, 31, 23])
    worker_round(m)
    assert list(m.ring) == []


def test_consecutive_rounds_are_independent():
    """Three segments in one stream must give what each gives on its own."""
    segs = [(0, 0, 3, 2), (5, 5, 5, 8), (31, 23, 0, 0)]
    together = OpModel([v for s in segs for v in s])
    for _ in segs:
        worker_round(together)
    apart = []
    for s in segs:
        one = OpModel(list(s))
        worker_round(one)
        apart += one.paint
    assert together.paint == apart


def test_ring_never_exceeds_its_capacity():
    """Peak ring depth sizes the forward/return pipes; a full ring blocks a PUSH."""
    peak = 0

    class Watched(OpModel):
        __slots__ = ()

        def do(self, op, arg=None):
            nonlocal peak
            out = super().do(op, arg)
            peak = max(peak, len(self.ring))
            return out

    for seg in DEGENERATE + CORNERS:
        worker_round(Watched(list(seg)))
    assert peak <= 8, f"ring peaks at {peak} values"


def test_timing_window_rejects_the_bugs_it_was_written_for():
    # a SWAP that overtakes the DATA still in flight (the real first failure)
    assert not timing_ok(50, 48, 5)
    # ADDR arriving after its own DATA
    assert not timing_ok(78, 36, 40)
    # the matched triple that actually drew a correct frame
    assert timing_ok(50, 48, 48)
    # boundaries
    assert timing_ok(50, 50 - GAP_ADDR_DATA, 99)
    assert not timing_ok(50, 50 + LAP_TICKS - GAP_ADDR_DATA, 99)
    assert not timing_ok(50, 48, 48 - GAP_DATA_SWAP)


@pytest.mark.slow
def test_all_legal_segments():
    """All 589,824 of them, against the statement's pseudocode."""
    bad = []
    for x0 in range(W):
        for y0 in range(H):
            for x1 in range(W):
                for y1 in range(H):
                    want = [y * W + x for x, y in bresenham(x0, y0, x1, y1)]
                    if addresses(x0, y0, x1, y1) != want:
                        bad.append((x0, y0, x1, y1))
                        if len(bad) > 5:
                            pytest.fail(f"mismatches: {bad}")
    assert not bad


def test_every_worker_send_and_receive_binds_to_the_pipe_it_means():
    """`Cur` checks bindings as it draws; this checks the *finished* grid.

    Anything placed with a bare `c.set` bypasses the cursor, so the two checks are
    not redundant: a mis-bound `r` loads fine and quietly reads the wrong pipe,
    which is the one class of bug the reference interpreter will not report.

    The round reads exactly four values from the input room (x0, y0, x1, y1) and
    sends the painter exactly four (base, n, and one increment per lane), so the
    *counts* pin the bindings down: any pop that strayed near enough to the north
    wall to outbid the input, or any push that strayed east of the increment
    boundary, moves one of these totals.
    """
    from randomfun2026solvers.plotter_block import (
        PAINT_MIN, PUSH_MAX, _nearer_input, build_worker,
    )

    rows = build_worker().rows()
    reads = sends = pops = pushes = 0
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "r":
                # `_nearer_input` raises on a tie, which the interpreter would settle
                # by reading order — never something the layout should lean on.
                if _nearer_input(x, y):
                    reads += 1
                else:
                    pops += 1
            elif ch == "s":
                assert x <= PUSH_MAX or x >= PAINT_MIN, f"s at {(x, y)} is a tie"
                sends += 1 if x >= PAINT_MIN else 0
                pushes += 1 if x <= PUSH_MAX else 0
    assert (reads, sends) == (4, 4), f"{reads} input reads, {sends} painter sends"
    assert pops and pushes, "found no ring glyphs at all — wrong grid?"


@pytest.mark.slow
def test_the_worker_grid_matches_the_model_on_the_reference_interpreter():
    """The op-level model is the spec; this checks the ASCII actually implements it.

    The worker probe points the increment pipe at an output room, so program output
    *is* the `base, n, inc...` stream — one string compare covers every branch, the
    ring's FIFO order, all four pipe bindings and the BP-counted loop.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from randomfun2026solvers.plotter_block import build_worker_probe

    node = shutil.which("node")
    lm = Path(__file__).resolve().parents[1] / "littleman" / "lm.mjs"
    if not node or not lm.exists():
        pytest.skip("needs node and littleman/lm.mjs")

    with tempfile.TemporaryDirectory() as td:
        man = Path(td) / "worker.man"
        man.write_text("\n".join(build_worker_probe()) + "\n")
        for seg in [(0, 0, 31, 23), (31, 23, 0, 0), (0, 23, 31, 0),
                    (5, 5, 5, 20), (3, 7, 29, 7), (0, 0, 0, 0)]:
            m = OpModel(list(seg))
            worker_round(m)
            # The worker never halts — it loops waiting for the next segment — so
            # tick a bound instead of running to completion.
            out = subprocess.run(
                [node, str(lm), "tick", str(man), "12000",
                 "--input", " ".join(map(str, seg))],
                capture_output=True, text=True, check=True, timeout=120,
            ).stdout
            got = next(ln[len("output:"):] for ln in out.splitlines()
                       if ln.startswith("output:"))
            assert got.split() == [str(v) for v in m.paint], seg


@pytest.mark.slow
def test_the_engine_agrees_with_the_layout_about_every_worker_binding():
    """Ask the interpreter itself, via `lm.mjs route`, on the assembled grid.

    The layout's own predicate could be wrong about the metric — it reasons about the
    cell where each pipe meets the room, and the ring-return and the input arrive on
    *opposite walls*, so the row term decides and there is no column boundary to eyeball.
    This is the check that cannot drift: `route` is the engine's own answer.
    """
    import json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from randomfun2026solvers.plotter_block import (
        _PATHS, _WX, _WY, PUSH_MAX, WH, WW, _nearer_input, build_block,
    )

    root = Path(__file__).resolve().parents[1]
    node, lm = shutil.which("node"), root / "littleman" / "lm.mjs"
    if not node or not lm.exists():
        pytest.skip("needs node and littleman/lm.mjs")

    head = {name: tuple(cells[0]) for name, (cells, _) in _PATHS.items()}
    with tempfile.TemporaryDirectory() as td:
        man = Path(td) / "block.man"
        rows = build_block()
        man.write_text("\n".join(rows) + "\n")
        checked = 0
        for iy in range(WH):
            for ix in range(WW):
                gx, gy = _WX + ix, _WY + iy
                row = rows[gy]
                ch = row[gx] if gx < len(row) else " "
                if ch not in "rs":
                    continue
                checked += 1
                out = subprocess.run(
                    [node, str(lm), "route", str(man), str(gx), str(gy)],
                    capture_output=True, text=True, check=True, timeout=60)
                got = tuple(json.loads(out.stdout)["cells"][0])
                want = ("i_out" if _nearer_input(ix, iy) else "r_out") if ch == "r" \
                    else ("fwd" if ix <= PUSH_MAX else "pnt")
                assert got == head[want], (
                    f"{ch!r} at interior {(ix, iy)}: layout says {want}, engine "
                    f"routes it to the pipe starting at {got}")
    assert checked == 70, f"expected 70 pipe glyphs in the worker, found {checked}"


@pytest.mark.slow
def test_the_assembled_block_draws_every_public_plotter_case():
    """The whole box against the six public cases, frame for frame.

    This is the end-to-end check the probes cannot be: it exercises the display, the
    ADDR/DATA/SWAP pipe *lengths* (latency is one tick per cell, so the lengths are
    part of the program), the painter's commit, and — because four of the six cases
    are multi-round — that a round leaves the ring as it found it.
    """
    import json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from randomfun2026solvers.plotter_block import build_block

    root = Path(__file__).resolve().parents[1]
    node, tool = shutil.which("node"), root / "littleman" / "tools" / "display-frames.mjs"
    problem = root / "tasks" / "problems" / "plotter.json"
    if not node or not tool.exists() or not problem.exists():
        pytest.skip("needs node, display-frames.mjs and tasks/problems/plotter.json")

    want = json.loads(problem.read_text())["publicTestData"]
    with tempfile.TemporaryDirectory() as td:
        man = Path(td) / "block.man"
        man.write_text("\n".join(build_block()) + "\n")
        out = subprocess.run([node, str(tool), str(man), str(problem)],
                             capture_output=True, text=True, check=True, timeout=600)
        got = json.loads(out.stdout)["cases"]

    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert g["fatal"] is None, (w["name"], g["fatal"])
        assert g["frames"] == [r["frames"][0] for r in w["rounds"]], w["name"]
