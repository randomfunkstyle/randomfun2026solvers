"""`brackets` as three hand-folded men over a one-register base-3 stack.

The machine itself is pinned by `test_brackets_stack`, at the op level.  What is
pinned here is the **layout**: that the generator reproduces the checked-in grid,
that the grid parses as five rooms and five pipes with no route left empty, that
`COUNT`'s two outgoing pipes bind the way its blocks assumed, and that the whole
thing answers correctly on the engine.
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

import pytest
from randomfun2026solvers.brackets_men import (
    ATTACH,
    CLASS,
    COUNT,
    WORK,
    build,
    build_grid,
    check_bindings,
)
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.man_debug import render_html
from test_brackets_bounds import CASES, LEGAL, encoded, expected_answer

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = ROOT / "tasks" / "problems" / "brackets.json"
SOLUTION = ROOT / "tasks" / "solutions" / "brackets_stack.man"
DEBUG_HTML = ROOT / "littleman" / "examples" / "brackets-stack.debug.html"
DEBUG_JSON = ROOT / "littleman" / "examples" / "brackets-stack.debug.json"

#: The footprint and the tick budget the score is made of.  `brackets_goal25`,
#: the parser this replaces, is 25x25 at 1,096 average reference ticks — the same
#: `max(w,h)^2` and five times the ticks.
SIDE = 25
GOAL25_SCORE = 685_278


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text(encoding="utf-8"))["publicTestData"]


@pytest.fixture(scope="module")
def machine() -> FastLittleman:
    return FastLittleman(SOLUTION)


# ── the generator and its sidecars ────────────────────────────────────────────
def test_the_generator_reproduces_the_checked_in_grid() -> None:
    assert build() == SOLUTION.read_text(encoding="utf-8").rstrip("\n").split("\n")


def test_the_sidecars_are_current() -> None:
    rows, dbg, _info = build_grid()
    assert json.loads(DEBUG_JSON.read_text(encoding="utf-8")) == dbg.to_dict()
    assert DEBUG_HTML.read_text(encoding="utf-8") == render_html(rows, dbg)


def test_the_footprint_is_the_one_the_score_was_measured_at() -> None:
    rows = build()
    w, h = max(map(len, rows)), len(rows)
    assert (w, h) == (24, 25)
    assert max(w, h) == SIDE


# ── the shape of the grid, before anything is run ─────────────────────────────
def test_five_rooms_and_five_pipes(machine: FastLittleman) -> None:
    """A pipe that fails to parse leaves the grid loading and the `s` silently
    rebound, so the count is asserted rather than assumed."""
    assert len(machine.pipes) == 5
    assert len(machine.rooms) == 5


def test_no_pipe_op_is_left_without_a_pipe(machine: FastLittleman) -> None:
    """A pipe that loops back to its own room parses, binds nothing, and turns
    every op on it into a silent `no-pipe`; a count alone would not see it."""
    for cell, binding in machine._bindings.items():
        assert binding not in (None, (), [], -1), f"{cell} binds no pipe"


def test_every_man_spawns_once(machine: FastLittleman) -> None:
    spawned = [r for r in machine.rooms if r.kind == "compute" and r.spawn]
    assert len(spawned) == 3


def test_the_counter_binds_the_pipe_each_of_its_sends_meant() -> None:
    """`COUNT` is the only room that sends on two pipes.  Both attach cells stand
    on the same wall, so the nearest-pipe rule reduces to a column comparison and
    `check_bindings` can decide it while the room is still a dict."""
    check_bindings()
    assert ATTACH[("term", "COUNT")][2] == ATTACH[("out", "COUNT")][2] == "S"
    assert ATTACH[("term", "COUNT")][1] == ATTACH[("out", "COUNT")][1]


def test_the_rooms_hold_the_glyph_counts_the_design_says() -> None:
    """A room that lost a glyph still loads and still runs; it just computes
    something else — which is exactly how `QPUSH`'s `M` went missing once."""
    ops = {n: sum(1 for g in cells.values() if g not in "<>^v@")
           for n, cells in (("CLASS", CLASS), ("WORK", WORK), ("COUNT", COUNT))}
    assert ops == {"CLASS": 14, "WORK": 50, "COUNT": 23}
    # and the sends, which are the only ops whose *position* carries meaning
    sends = {n: sorted(c for c, g in cells.items() if g == "s")
             for n, cells in (("CLASS", CLASS), ("WORK", WORK), ("COUNT", COUNT))}
    assert len(sends["CLASS"]) == 4 and len(sends["COUNT"]) == 3


def test_every_branch_glyph_has_both_of_its_lanes(machine: FastLittleman) -> None:
    """`x` and `X` always turn, so the cells either side of them must be enterable;
    a branch against a wall is a fatal the engine only finds on the taken lane."""
    for name, (w, h, cells) in (("CLASS", (7, 6, CLASS)), ("WORK", (15, 8, WORK)),
                                ("COUNT", (12, 7, COUNT))):
        for (x, y), glyph in cells.items():
            if glyph not in "xXd":
                continue
            assert 0 < x < w - 1 and 0 < y < h - 1, \
                f"{name}: branch {glyph!r} at ({x},{y}) has a lane in the wall"


# ── the engine ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("case", public_cases(), ids=lambda c: c["name"])
def test_every_public_case(case: dict, machine: FastLittleman) -> None:
    result = machine.run(case["in"], expected=[int(v) for v in case["out"]],
                         max_ticks=100_000)
    assert result.passed, (result.fatal, result.output)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_exact_bound_and_adversarial_cases(case, machine: FastLittleman) -> None:
    """Depth 32, length 64, and the corners either side of both."""
    result = machine.run(encoded(case.text), expected=[case.answer],
                         max_ticks=100_000)
    assert result.passed, (case.name, result.fatal, result.output)


def test_every_string_through_length_four(machine: FastLittleman) -> None:
    checked = 0
    for size in range(5):
        for chars in itertools.product(sorted(LEGAL), repeat=size):
            text = "".join(chars)
            result = machine.run(encoded(text), expected=[expected_answer(text)],
                                 max_ticks=8_000)
            assert result.passed, (text, result.fatal, result.output)
            checked += 1
    assert checked == 1_555


# ── the slow tier: the reference engine, and the score it scores ──────────────
@pytest.mark.slow
def test_every_string_through_length_five(machine: FastLittleman) -> None:
    for size in (5,):
        for chars in itertools.product(sorted(LEGAL), repeat=size):
            text = "".join(chars)
            result = machine.run(encoded(text), expected=[expected_answer(text)],
                                 max_ticks=8_000)
            assert result.passed, (text, result.fatal, result.output)


@pytest.mark.slow
def test_the_reference_engine_agrees_and_the_score_beats_the_goal25_parser() -> None:
    """The score is `max(w,h)^2 x mean ticks`, measured on the bundled reference
    interpreter — the same one the contest scores with."""
    from randomfun2026solvers.littleman import Littleman
    from randomfun2026solvers.scoring import score_program

    os.environ.setdefault("LM_VALIDATOR", "reference")
    result = score_program(SOLUTION, "brackets", lm=Littleman())
    assert (result.width, result.height) == (24, 25)
    assert result.area2 == SIDE ** 2
    assert result.score < GOAL25_SCORE / 4, result.score
