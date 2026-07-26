"""The `sudoku-validity` ring machine: layout invariants, then the engine.

The design lives in :mod:`randomfun2026solvers.sudoku_cfg` (proved by
``test_sudoku_cfg.py``); :mod:`randomfun2026solvers.sudoku_ring` only *places*
it.  So the tests here are about placement, and they pin the three things that
fail **silently** rather than loudly:

* **glyph census** -- a cell that is walked but holds no glyph is invisible: the
  grid loads, the pipes bind, and the machine computes something else.  The
  census below reconstructs each block's token list out of the rendered room and
  compares it against the CFG, so a dropped `M` is a test failure rather than a
  wrong answer.
* **pipe binding** -- `s`/`r` take the *nearest* pipe, so a glyph one column too
  far east reads a plausible number out of the wrong ring.  Every op is checked
  against the engine's own `route`, not against the module's model of it.
* **ring capacity** -- a ring shorter than its resident word count deadlocks with
  no error at all, so both rings are measured from the parsed grid.

The engine tier runs all six public cases twice, on the reference interpreter and
on ``FastLittleman``, with the rounds joined by ``/`` so the judge withholds each
cell until the previous verdict is out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers import sudoku_cfg, sudoku_ring
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.scoring import score_program

ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "tasks" / "solutions" / "sudoku-validity_ring.man"
PROBLEM = ROOT / "tasks" / "problems" / "sudoku-validity.json"


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def case_ids() -> list[str]:
    return [c["name"] for c in public_cases()]


def rows() -> list[str]:
    return SOLUTION.read_text().rstrip("\n").split("\n")


# ── the grid is the generator's output, and the CPU build is left alone ───────
def test_generator_reproduces_the_committed_grid() -> None:
    assert sudoku_ring.build() == rows()


def test_the_cpu_build_is_left_beside_it() -> None:
    """The ring machine is a second solution, not a replacement in place."""
    cpu = (ROOT / "tasks" / "solutions" / "sudoku-validity_cpu.man").read_text()
    assert cpu.strip(), "the CPU build must still be there"


def test_the_generator_emits_all_three_artefacts() -> None:
    grid, dbg, info = sudoku_ring.build_grid()
    assert grid == rows()
    assert info["blocks"] == len(sudoku_cfg.WORKER)
    assert info["glyph_cells"] == sudoku_cfg.worker_glyph_cells()
    assert {r.name for r in dbg.regions} >= {
        f"block:{n}" for n in sudoku_ring.BLOCK_ROWS
    }


def test_the_footprint_is_square_and_the_box_is_what_it_claims() -> None:
    grid = rows()
    w, h = max(len(r) for r in grid), len(grid)
    assert (w, h) == (27, 27)
    assert all(row.strip() for row in grid), "a blank row would be clipped away"


# ── the census: every block walks its own tokens, and nothing else ────────────
#: Interior cells each block's glyphs occupy, in walking order.  Written out
#: rather than derived, so that moving a glyph and forgetting to move its
#: neighbour is caught by the *token* comparison below rather than by luck.
BLOCK_CELLS: dict[str, list[tuple[int, int]]] = {
    "INIT": [(1, 0), (2, 0), (3, 0)],
    "FILL": [(21, 0)],
    "FILL_BODY": [(21, 1), (20, 1)],
    "ROUND": [
        (2, 3), (9, 3), *((x, 3) for x in range(10, 20)),
        (18, 4), (5, 4),
        (9, 5), *((x, 5) for x in range(10, 15)), (15, 5), (16, 5), (17, 5),
        *((x, 5) for x in range(18, 22)),
        (18, 6), (17, 6), *((x, 6) for x in range(16, 10, -1)),
        (10, 6), (9, 6), *((x, 6) for x in range(8, 4, -1)),
        (7, 7), (8, 7), (9, 7), (10, 7), (11, 7), (12, 7),
        (5, 8),
        (9, 9), (10, 9), (11, 9),
    ],
    "ROT1": [(19, 11)],
    "ROT1_BODY": [(20, 11), (21, 11), (22, 11)],
    "ACCESS": [(22, 13), (21, 13), (20, 13), (19, 13), (18, 13), (17, 13)],
    "BAD": [(6, 12), (5, 12), (4, 12)],
    "OK": [(9, 13), (8, 13), (7, 13), (6, 13), (5, 13), (4, 13), (3, 13), (2, 13)],
    "ROT2": [(19, 15)],
    "ROT2_BODY": [(20, 15), (21, 15), (22, 15)],
}

#: What each CFG token looks like once written into a cell.  `rr`/`sr`/`ri`/... are
#: a *column discipline*, not a glyph: they all compile to a bare `r` or `s`.
_GLYPH = {"rr": "r", "rq": "r", "ri": "r", "sr": "s", "sq": "s", "so": "s"}


def _glyph(token: str) -> str:
    if token.startswith("L") and token[1:].isdigit():
        assert int(token[1:]) <= 9, "a multi-digit literal would need backticks"
        return token[1:]
    return _GLYPH.get(token, token)


def test_every_block_walks_its_own_tokens() -> None:
    """The rendered room, read along each block's path, is the CFG token list.

    This is the static check for the failure mode that produces a *working
    looking* machine: a cell the man walks that holds no glyph is a nop, and a
    two-character token like `rr` written literally would shift its whole row.
    """
    room = sudoku_ring.worker().rows()
    for name, (tokens, _succ) in sudoku_cfg.WORKER.items():
        cells = BLOCK_CELLS[name]
        assert len(cells) == len(tokens), f"{name}: {len(cells)} cells, {len(tokens)} tokens"
        got = [room[y][x] for x, y in cells]
        assert got == [_glyph(t) for t in tokens], name


def test_the_census_accounts_for_every_glyph_in_the_room() -> None:
    """No block's cells overlap, and the count matches `sudoku_cfg`."""
    seen = [c for cells in BLOCK_CELLS.values() for c in cells]
    assert len(seen) == len(set(seen)), "two blocks claim the same cell"
    assert len(seen) == sudoku_cfg.worker_glyph_cells()


# ── pipes ────────────────────────────────────────────────────────────────────
def test_the_grid_has_exactly_the_six_pipes_it_was_drawn_with() -> None:
    """A pipe whose first cell does not point away from its room fails to parse
    *silently*: the grid still loads and the ring simply does nothing."""
    analysis = Littleman().analyze(SOLUTION)
    assert len(analysis.pipes) == 6
    assert len(analysis.rooms) == 5      # worker, two relays, input, output


def test_every_pipe_op_binds_to_the_pipe_it_was_written_for() -> None:
    """Checked against the engine's own `route`, not against the module's model.

    All six pipes anchor on the worker's north wall, so the distance from any
    cell is ``|x - col| + y + 1`` and "nearest pipe" is one-dimensional -- but a
    wrong bind reads a plausible value rather than faulting, so the rule is
    verified rather than trusted.
    """
    lm = Littleman()
    ends = {}
    for pipe in lm.analyze(SOLUTION).pipes:
        cells = tuple(s.pos.as_tuple() for s in pipe.path)
        ends[cells] = (cells[0], cells[-1])

    want = {
        "io": ((1, 3), (2, 6)),          # input room -> worker
        "out": ((5, 6), (5, 3)),         # worker -> output room
        "file_in": ((13, 5), (13, 6)),
        "file_out": ((14, 6), (14, 5)),
        "ring_in": ((23, 3), (24, 6)),
        "ring_out": ((25, 6), (23, 1)),
    }
    assert set(want.values()) <= set(ends.values())

    wx, wy = sudoku_ring.WX, sudoku_ring.WY
    census: dict[str, int] = {}
    for name, cells in BLOCK_CELLS.items():
        for (x, y), token in zip(cells, sudoku_cfg.WORKER[name][0], strict=True):
            if token not in _GLYPH:
                continue
            got = tuple(c.as_tuple() for c in lm.route(SOLUTION, wx + x, wy + y))
            key = {"r": {"io": "io", "file": "file_in", "ring": "ring_in"},
                   "s": {"io": "out", "file": "file_out", "ring": "ring_out"}}
            zone = {"i": "io", "o": "io", "q": "file", "r": "ring"}[token[1]]
            expected = want[key[token[0]][zone]]
            assert ends[got] == expected, f"{name} {token} at ({x},{y}) bound {ends[got]}"
            census[zone] = census.get(zone, 0) + 1
    # 3 `ri` + 2 `so`; 7 `sq` + 6 `rq` in ROUND plus OK's `rq`; and the seven
    # ring ops -- FILL's `sr`, both rotation bodies and ACCESS's pair.  The
    # rotation loops carry a *second* r/s pair on their far side, asserted at
    # build time by `_r`/`_s`; the census walks the CFG, which names one each.
    assert census == {"io": 5, "file": 14, "ring": 7}


def test_both_rings_hold_their_resident_words() -> None:
    """An under-capacity ring deadlocks with no error at all."""
    lengths = {
        tuple(s.pos.as_tuple() for s in p.path)[0]: len(p.path)
        for p in Littleman().analyze(SOLUTION).pipes
    }
    store = lengths[(25, 6)] + lengths[(23, 3)]
    file_ = lengths[(14, 6)] + lengths[(13, 5)]
    assert store >= sudoku_cfg.RING_WORDS + 1, store
    assert file_ >= sudoku_cfg.FILE_WORDS + 1, file_


# ── the op model agrees with the design it was written from ──────────────────
def test_the_op_model_reproduces_every_public_case() -> None:
    """`sudoku_cfg.simulate_worker` is what the layout claims to implement."""
    for case in public_cases():
        want = [int(t) for r in case["rounds"] for t in r["out"]]
        out, _ticks = sudoku_cfg.simulate_worker(case["rounds"])
        assert out == want, case["name"]


# ── the engine ───────────────────────────────────────────────────────────────
@pytest.mark.slow
@pytest.mark.parametrize("case", public_cases(), ids=case_ids())
def test_public_cases_on_the_reference_interpreter(case: dict) -> None:
    """Rounds joined with `/`: the next cell is withheld until we answer."""
    want = [int(t) for r in case["rounds"] for t in r["out"]]
    snap = Littleman().judge(
        SOLUTION.read_text(),
        input=" / ".join(" ".join(r["in"]) for r in case["rounds"]),
        expected=" / ".join(" ".join(r["out"]) for r in case["rounds"]),
        max_ticks=200_000,
    )
    assert snap.fatal is None, f"fatal: {snap.fatal}"
    assert list(snap.output) == want


@pytest.mark.slow
def test_public_cases_on_fast_littleman() -> None:
    """The second, independent engine -- same grid, same six verdict streams."""
    machine = FastLittleman(SOLUTION)
    for case in public_cases():
        result = machine.run(
            " / ".join(" ".join(r["in"]) for r in case["rounds"]),
            expected=" / ".join(" ".join(r["out"]) for r in case["rounds"]),
            max_ticks=200_000,
        )
        assert result.passed, (case["name"], result)


@pytest.mark.slow
def test_the_score_is_measured_from_the_generated_grid() -> None:
    """`littleman-validate` caps at 5,000,000 whatever the problem says; the
    contest cap for a `footprint-tick` problem comes from the problem file."""
    result = score_program(SOLUTION, PROBLEM)
    assert result.area2 == 729
    assert result.avg_ticks is not None
    assert result.score == pytest.approx(result.area2 * result.avg_ticks)
    assert result.score < 2_384_422_055, "must beat the submitted CPU build"
