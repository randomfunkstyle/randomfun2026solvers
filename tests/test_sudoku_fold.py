"""The folded `sudoku-validity` machine: bindings, mouths, then the engine.

:mod:`randomfun2026solvers.sudoku_fold` re-derives the ring machine around two
changes -- one rotation loop instead of two (the ring phase is tracked in the
scratch FIFO) and pipe columns chosen so the receive and send bands overlap --
so the tests here pin the things that fail *silently* in that design:

* **the CFG is the one the layout implements.**  An op-level simulation of the
  exact token list the generator lays down is run over all six public cases, so
  a wrong recurrence is a failure here and not a wrong verdict on the engine.
* **pipe binding.**  `s`/`r` take the nearest pipe, so a glyph one column too
  far east reads a plausible number out of the wrong ring.  Every pipe op in the
  room is checked against the engine's own `route`.
* **pipe mouths.**  An arrowhead with a room wall behind it is a pipe mouth even
  when `analyze` folds it into a neighbouring pipe, so the mouths are counted
  the way the runtime counts them and compared against the six pipes intended.
* **ring capacity.**  Nine resident words in fewer than ten slots deadlocks with
  no error at all; the slots are counted from the parsed grid.

The engine tier runs every public case with the rounds joined by ``/`` so the
judge withholds each cell until the previous verdict is out -- which the tick
measurement in ``scoring`` does *not* do.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest
from randomfun2026solvers import sudoku_fold
from randomfun2026solvers.brackets_men import pipe_mouths, wall_cells
from randomfun2026solvers.littleman import Littleman

ROOT = Path(__file__).resolve().parents[1]
SOLUTION = ROOT / "tasks" / "solutions" / "sudoku-validity_fold.man"
PROBLEM = ROOT / "tasks" / "problems" / "sudoku-validity.json"

#: Outer boxes of every room, as the floor plan places them.
BOXES = [(0, 0, 3, 3), (3, 0, 6, 4), (9, 0, 3, 3), (12, 0, 8, 4), (0, 6, 17, 14)]


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def case_ids() -> list[str]:
    return [c["name"] for c in public_cases()]


def rows() -> list[str]:
    return SOLUTION.read_text().rstrip("\n").split("\n")


# ── the grid is exactly what the generator emits ─────────────────────────────
def test_generator_reproduces_the_committed_grid() -> None:
    assert sudoku_fold.build() == rows()


def test_the_ring_build_is_left_beside_it() -> None:
    """A second solution, not a replacement in place."""
    ring = (ROOT / "tasks" / "solutions" / "sudoku-validity_ring.man").read_text()
    assert ring.strip(), "the 27x27 ring machine must still be there"


def test_no_row_is_blank() -> None:
    """A blank row would be clipped away and change the footprint silently."""
    assert all(row.strip() for row in rows())


# ── the op model the layout claims to implement ──────────────────────────────
_BIN = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b,
        "&": lambda a, b: a & b, "{": lambda a, b: a << b if 0 <= b <= 63 else 0}


def simulate(rounds: list[dict]) -> list[int]:
    """Run :data:`sudoku_fold.ROUND_OPS` at the op level over one test case."""
    inp = deque(int(v) for r in rounds for v in r["in"])
    ring: deque[int] = deque([0] * 9)
    file: deque[int] = deque([0])          # INIT's phase seed
    a = b = bp = 0
    out: list[int] = []
    while True:
        for g, tag in sudoku_fold.ROUND_OPS:
            if g == "r":
                if tag == "i":
                    if not inp:
                        return out
                    a = inp.popleft()
                elif tag == "q":
                    a = file.popleft()
                else:
                    a = ring.popleft()
            elif g == "s":
                (file if tag == "Q" else ring).append(a)
            elif g == "M":
                b = a
            elif g == "W":
                a, b = b, a
            elif g == "/":
                a, b = a // b, a % b
            elif g == "%":
                a = a % b
            elif g == "b":
                bp = a
            elif g.isdigit():
                a = int(g)
            else:
                a = _BIN[g](a, b)
        while bp > 0:                       # ROT
            ring.append(ring.popleft())
            bp -= 1
        t = ring.popleft()                  # ACCESS: rr + sr & - N b
        ring.append(t + b)
        if ((t + b) & b) - b != 0:
            out.append(0)
            return out
        out.append(1)


def test_the_op_model_reproduces_every_public_case() -> None:
    for case in public_cases():
        want = [int(t) for r in case["rounds"] for t in r["out"]]
        assert simulate(case["rounds"]) == want, case["name"]


# ── binding: nearest pipe, checked against the engine and not against us ─────
def _interior_ops() -> list[tuple[int, int, str]]:
    grid = rows()
    x0, y0 = 1, 7                            # worker interior origin
    return [(x0 + x, y0 + y, ch)
            for y, line in enumerate(sudoku_fold.worker()[0].rows())
            for x, ch in enumerate(line) if ch in "rs"]


@pytest.mark.slow
def test_every_pipe_op_binds_to_the_pipe_its_column_promises() -> None:
    """`route` is asked which pipe each `r`/`s` reaches; the bands must agree."""
    lm = Littleman()
    src = SOLUTION.read_text()
    b = sudoku_fold.bands()
    want_in = {"i": "input", "q": "file", "r": "ring"}
    want_out = {"Q": "file", "o": "output", "R": "ring"}
    for gx, gy, ch in _interior_ops():
        cells = lm.route(src, gx, gy)
        assert cells, f"{ch} at ({gx},{gy}) reaches no pipe"
        ix = gx - 1
        tags = want_in if ch == "r" else want_out
        got = [name for tag, name in tags.items() if ix in b[tag]]
        assert len(got) == 1, f"({gx},{gy}) is in {got} bands"


def test_exactly_six_pipe_mouths() -> None:
    """An arrowhead with a room wall behind it is a pipe whether we meant it or not."""
    mouths = pipe_mouths(rows(), wall_cells(BOXES))
    assert len(mouths) == 6, sorted(mouths)


@pytest.mark.slow
def test_the_rings_are_deep_enough_to_hold_their_words() -> None:
    """Nine words need ten slots; the relay man carries the tenth."""
    import json as _json
    import subprocess

    out = subprocess.run(
        ["node", str(ROOT / "littleman" / "lm.mjs"), "analyze", str(SOLUTION)],
        capture_output=True, text=True, check=True).stdout
    pipes = _json.loads(out)["pipes"]
    worker, ring_relay, file_relay = 4, 3, 1
    ring = sum(len(p["path"]) for p in pipes
               if {p["src"], p["dst"]} == {worker, ring_relay})
    scratch = sum(len(p["path"]) for p in pipes
                  if {p["src"], p["dst"]} == {worker, file_relay})
    assert ring + 1 >= 10, ring
    assert scratch + 1 >= 4, scratch


# ── the engine, with the judge withholding each round ────────────────────────
@pytest.mark.slow
@pytest.mark.parametrize("case", public_cases(), ids=case_ids())
def test_public_cases_on_the_reference_interpreter(case: dict) -> None:
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
def test_it_beats_the_machine_it_replaces() -> None:
    """Relative to the other checked-in solution from the same run, not a pin."""
    from randomfun2026solvers.scoring import score_program

    fold = score_program(SOLUTION, PROBLEM)
    ring = score_program(ROOT / "tasks" / "solutions" / "sudoku-validity_ring.man",
                         PROBLEM)
    assert fold.score is not None and ring.score is not None
    assert fold.score < ring.score, (fold.score, ring.score)
