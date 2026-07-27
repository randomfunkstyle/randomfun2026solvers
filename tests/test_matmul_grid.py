"""What `matmul_grid` has actually been measured to do.

A dataflow ring machine fails silently in three different ways, and every check
here exists because one of them nearly happened:

* a cell that is **walked but holds no glyph** is invisible -- the grid loads,
  the pipes bind, and the machine computes something else.  So the grid is
  walked from every block's first cell and compared against the CFG.
* a **mis-bound** `s` still runs.  So every pipe op is put to the engine's own
  ``route`` oracle and matched against the ring it was meant for.
* a ring **one word too short deadlocks** with no message.  So each ring's cell
  count is compared with the peak occupancy measured over every shape in 2..16.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

from randomfun2026solvers import matmul_cfg as cfg
from randomfun2026solvers import matmul_grid as mg
from randomfun2026solvers import scoring

REPO = Path(__file__).resolve().parents[1]
GRID = REPO / "tasks" / "solutions" / "matmul_ring.man"
PROBLEM = REPO / "tasks" / "problems" / "matmul.json"


def _cases() -> list[tuple[str, list[int], list[int]]]:
    prob = json.loads(PROBLEM.read_text())
    out = []
    for case in prob["publicTestData"]:
        rnd = case["rounds"][0]
        out.append((case["name"], [int(v) for v in rnd["in"]],
                    [int(v) for v in rnd["out"]]))
    return out


def _shapes() -> list[tuple[int, int, int]]:
    return [(n, m, k) for n in (2, 3, 5, 16) for m in (2, 3, 7, 16)
            for k in (1, 2, 3, 4, 9, 16)]


def _case(n: int, m: int, k: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [n, m, k] + [rng.randint(-99, 99) for _ in range(n * m + m * k)]


# ── the rewritten literals are the same program ───────────────────────────────
def test_the_single_digit_rewrite_computes_what_the_literal_did():
    for tok, run in mg.LITERALS.items():
        a = b = 0
        for t in run:
            if t.startswith("L"):
                a = int(t[1:])
            elif t == "M":
                b = a
            elif t == "*":
                a *= b
            elif t == "+":
                a += b
            else:  # pragma: no cover - the rewrite table is closed
                raise AssertionError(t)
        assert a == int(tok[1:])


def test_every_rewritten_literal_is_followed_by_a_dead_b():
    # `expand` raises otherwise; this pins the property the rewrite rests on.
    for toks, _ in cfg.WORKER.values():
        for i, tok in enumerate(toks):
            if tok in mg.LITERALS:
                assert toks[i + 1] == "M"


def test_the_laid_program_still_multiplies_matrices():
    for n, m, k in _shapes():
        case = _case(n, m, k, seed=n * 100 + m * 10 + k)
        runs, lanes, out = mg.trace(case)
        assert out == cfg.matmul_reference(case), (n, m, k)
        assert runs["HEAD"] == 1


# ── the lanes that are not drawn are the lanes that never fire ────────────────
def test_no_negative_lane_is_ever_taken():
    seen: set[tuple[str, str]] = set()
    for n, m, k in _shapes():
        _, lanes, _ = mg.trace(_case(n, m, k, seed=n + m + k))
        seen |= set(lanes)
    assert {key for key in seen if key[1] == "neg"} == set()
    assert all(key not in seen for key in mg.DEAD_LANES)


# ── the room is the CFG ───────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def room() -> mg.Room:
    return mg.build_room()


def test_every_block_walks_its_own_tokens(room: mg.Room):
    mg.check_room(room)


def test_every_pipe_op_stands_in_the_band_that_binds_it(room: mg.Room):
    bands = room.bands
    for chain in room.chains:
        flat = mg.flatten(chain)
        for it in flat:
            if it.band is None:
                continue
            lo, hi = bands.span(it.band, it.send)
            assert lo <= hi, (it.band, it.send)


def test_the_two_partitions_never_tie(room: mg.Room):
    # A tie breaks by reading order over pipe *segments*; excluding tie columns
    # from the bands is what makes the nearest-column rule safe to rely on.
    for cols in (room.bands.recv_col, room.bands.send_col):
        assert len(set(cols.values())) == len(cols)
    for ring in mg.BANDS:
        assert room.bands.send_col[ring] + 1 == room.bands.recv_col[ring]


def test_the_grid_holds_no_backtick():
    # Every multi-digit literal is rewritten, so no column is spent on a pair --
    # and a stray one would pair vertically with the next and swallow a turn.
    assert "`" not in GRID.read_text()


# ── the rings hold what the program puts in them ──────────────────────────────
def test_peak_ring_occupancy_is_what_the_pipes_are_sized_for():
    peak = dict.fromkeys(mg.RING_WORDS, 0)
    for n, m, k in _shapes():
        case = _case(n, m, k, seed=7 * n + m + k)
        inp, ring = list(case), {r: 0 for r in peak}
        del inp
        # replay the token stream, counting each ring's depth
        from collections import deque
        q = deque(case)
        rings = {r: deque() for r in peak}
        a = b = bp = 0
        block = "HEAD"
        while True:
            toks, succ = mg.LAID[block]
            branch = None
            for t in toks:
                if t == "H":
                    break
                if t.startswith("L") and t != "L":
                    a = int(t[1:])
                elif t == "ri":
                    a = q.popleft()
                elif t == "so":
                    pass
                elif t[0] == "r" and t[1:] in rings:
                    a = rings[t[1:]].popleft()
                elif t[0] == "s" and t[1:] in rings:
                    rings[t[1:]].append(a)
                    ring[t[1:]] = max(ring[t[1:]], len(rings[t[1:]]))
                elif t == "M":
                    b = a
                elif t == "W":
                    a, b = b, a
                elif t == "N":
                    a = -a
                elif t == "/":
                    a, b = a // b, a % b
                elif t == "b":
                    bp = a
                elif t == "m":
                    bp -= 1
                elif t == "X":
                    branch = "zero" if a == 0 else ("pos" if a > 0 else "neg")
                elif t == "d":
                    branch = "pos" if bp > 0 else "zero"
                else:
                    a = cfg._BIN[t](a, b)
            else:
                block = succ if isinstance(succ, str) else succ[branch]
                continue
            break
        for r, v in ring.items():
            peak[r] = max(peak[r], v)
    for r, want in mg.RING_WORDS.items():
        assert peak[r] <= want, f"ring {r} peaked at {peak[r]}, sized for {want}"


def test_each_ring_is_longer_than_the_words_it_carries():
    _, _, info = mg.build_grid()
    for ring, cells in info["rings"].items():
        assert cells >= mg.RING_WORDS[ring] + 1, ring


# ── the checked-in grid ───────────────────────────────────────────────────────
def test_the_checked_in_grid_is_what_the_generator_emits():
    art, _, _ = mg.build_grid()
    assert GRID.read_text() == "\n".join(art) + "\n"


def test_the_footprint_is_what_the_report_claims():
    w, h, area2 = scoring.footprint(GRID)
    assert (w, h, area2) == (88, 96, 9216)


# ── the engine ────────────────────────────────────────────────────────────────
def test_the_estimator_agrees_with_the_engine_on_the_full_size_case():
    # The geometry search is only as good as this: it prices thousands of
    # candidate layouts by walking them, and never runs one.
    room = mg.build_room()
    traces = mg.public_traces()
    est = mg.estimate_ticks(room, *traces[3])
    assert abs(est - 128_424) < 0.02 * 128_424


@pytest.mark.slow
def test_every_public_case_passes_on_the_fast_engine():
    from randomfun2026solvers.fast_littleman import FastLittleman

    machine = FastLittleman(GRID)
    for name, inp, exp in _cases():
        res = machine.run(input=inp, expected=exp, max_ticks=2_000_000)
        assert list(res.output) == exp, name


@pytest.mark.slow
def test_the_shapes_the_public_cases_do_not_reach_also_pass():
    """The corners of the constraint box, on the engine rather than the model.

    The judge's cases are not the public ones, and the two that matter here are
    ``K = 1`` -- one lane of one group, so the `c` ring is at its shortest -- and
    ``M = 1``, which turns the `t` loop over exactly once.  Both are shapes where
    a ring holding fewer words than its pipe is long would stall rather than
    deadlock, and would show up only as a slow wrong answer.
    """
    from randomfun2026solvers.fast_littleman import FastLittleman

    machine = FastLittleman(GRID)
    for n, m, k in [(1, 1, 1), (2, 2, 1), (16, 16, 1), (16, 1, 16),
                    (2, 16, 2), (16, 2, 2), (3, 16, 16), (16, 16, 3)]:
        case = _case(n, m, k, seed=n * 1000 + m * 10 + k)
        exp = cfg.matmul_reference(case)
        res = machine.run(input=case, expected=exp, max_ticks=3_000_000)
        assert list(res.output) == exp, (n, m, k)


@pytest.mark.slow
def test_every_public_case_passes_on_the_reference_engine():
    if os.environ.get("LM_VALIDATOR", "").lower() != "reference":
        pytest.skip("set LM_VALIDATOR=reference to cross-check the wasm engine")
    from randomfun2026solvers.littleman import Littleman

    lm = Littleman()
    for name, inp, exp in _cases():
        snap = lm.judge(GRID, input=" ".join(map(str, inp)),
                        expected=" ".join(map(str, exp)), max_ticks=2_000_000)
        assert snap.fatal is None, (name, snap.fatal)
        assert list(snap.output) == exp, name


@pytest.mark.slow
def test_every_pipe_op_binds_to_the_ring_it_means():
    """The engine's own ``route`` oracle, against the band the planner used."""
    from randomfun2026solvers.littleman import Littleman

    lm = Littleman()
    room = mg.build_room()
    art, _, _ = mg.build_grid()
    wy, off = mg.NB + 1, mg.WX + room.margin
    ends = {}
    for ring in mg.BANDS:
        ends[(room.bands.send_col[ring] + off, wy - 2)] = (ring, True)
        ends[(room.bands.recv_col[ring] + off, wy - 2)] = (ring, False)

    checked = 0
    for chain in room.chains:
        for it, (x, y) in _pipe_cells(room, chain):
            cells = lm.route(GRID, x, y)
            assert cells, (it.block, it.band, x, y)
            # Only the segment touching this room counts: the source end for an
            # outgoing pipe, the destination end for an incoming one.
            end = cells[0] if it.send else cells[-1]
            assert ends.get((end.x, end.y)) == (it.band, it.send), (
                it.block, it.band, "send" if it.send else "recv",
                (x, y), (end.x, end.y))
            checked += 1
    assert checked == 142            # every pipe op in the CFG


def _pipe_cells(room: mg.Room, chain: mg.Chain):
    """Every pipe op of one chain, with the grid cell it was drawn in."""
    wy, off = mg.NB + 1, mg.WX + room.margin
    flat = [it for it in mg.flatten(chain)]
    walked: list[tuple[int, int]] = []
    for row in chain.rows:
        for col, glyph in row.cells:
            if glyph != " ":
                walked.append((col + off, row.y + wy))
    ops = [it for it in flat if it.kind != "lit"] + []
    laid = []
    i = 0
    for it in flat:
        n = 1 if it.kind == "g" else len(it.payload) + 2
        if it.band is not None:
            laid.append((it, walked[i]))
        i += n
    del ops
    return laid
