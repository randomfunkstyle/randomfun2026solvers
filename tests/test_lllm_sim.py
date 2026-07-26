"""The pure-Python ``little-little-little-man`` reference, against every public frame.

This module is the oracle any hardware/asm build is written against, so the bar is
byte-for-byte: each round commits exactly one 16x16 frame of 16 hex digits, and every
one of them has to come out identical to the published data.

The interesting semantics that the public data pins down, and that are asserted below:

* the little man starts on the ``@`` facing **East**;
* the ``@`` cell is ordinary empty space once he leaves it (colour 0), and walking back
  over it does nothing (``revolving door`` does exactly that);
* walking into a wall moves him **onto** the wall cell, where he stays and is drawn;
* ``+``/``-`` are walls when they sit on the room's border rectangle and arithmetic
  operations everywhere else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers.lllm_sim import (
    COLOUR_ARITH,
    COLOUR_WALL,
    KIND_ARITH,
    KIND_WALL,
    LLLMState,
    classify,
    parse_program,
    render,
    run_case,
)

REPO = Path(__file__).parents[1]


def public_cases() -> list[dict]:
    problem = REPO / "tasks" / "problems" / "little-little-little-man.json"
    return json.loads(problem.read_text(encoding="utf-8"))["publicTestData"]


CASES = {case["name"]: case for case in public_cases()}


def case_rounds(case: dict) -> list[list[str]]:
    return [list(r["in"]) for r in case["rounds"]]


def expected_frames(case: dict) -> list[list[list[str]]]:
    return [list(r["frames"]) for r in case["rounds"]]


def program_of(case: dict):
    head = [int(t) for t in case["rounds"][0]["in"]]
    return parse_program(head[2:], head[0], head[1])


def replay(case: dict) -> LLLMState:
    """Run the whole case and hand back the final state."""
    state = LLLMState(program_of(case))
    for rnd in case["rounds"][1:]:
        state.run(int(rnd["in"][0]))
    return state


def test_there_are_ten_public_cases() -> None:
    assert len(CASES) == 10


@pytest.mark.parametrize("name", list(CASES))
def test_every_public_frame_matches(name: str) -> None:
    case = CASES[name]
    assert run_case(case_rounds(case)) == expected_frames(case)


@pytest.mark.parametrize("name", list(CASES))
def test_every_round_commits_exactly_one_16x16_frame(name: str) -> None:
    case = CASES[name]
    for frames in run_case(case_rounds(case)):
        assert len(frames) == 1
        rows = frames[0]
        assert len(rows) == 16
        assert all(len(row) == 16 for row in rows)
        assert all(ch in "0123456789abcdef" for row in rows for ch in row)


#: ``(W, H, rounds, sum(k), ticks actually run, index of the round he halts on)``.
#: ``ticks`` is below ``sum(k)`` whenever he halts part-way through a round; the halting
#: round index is 1-based over the *step* rounds, i.e. round ``len(rounds) - 1`` always,
#: because a case ends after the round in which the program halts.
EXPECTED_STATS = {
    "one tick at a time": (11, 7, 26, 25, 24, 25),
    "first steps": (4, 4, 4, 3, 2, 3),
    "around the block": (16, 16, 12, 142, 123, 11),
    "off the edge": (16, 5, 15, 28, 28, 14),
    "widdershins": (7, 16, 10, 70, 57, 9),
    "crossroads": (13, 9, 3, 44, 24, 2),
    "revolving door": (5, 5, 6, 9, 9, 5),
    "swan dive": (10, 5, 9, 10, 9, 8),
    "hall of mirrors": (14, 10, 26, 32, 31, 25),
    "victory lap": (16, 10, 5, 222, 182, 4),
}


@pytest.mark.parametrize("name", list(CASES))
def test_measured_statistics(name: str) -> None:
    case = CASES[name]
    program = program_of(case)
    ks = [int(r["in"][0]) for r in case["rounds"][1:]]
    state = LLLMState(program)
    halt_round = None
    for index, k in enumerate(ks, start=1):
        state.run(k)
        if state.halted and halt_round is None:
            halt_round = index
    got = (
        program.width,
        program.height,
        len(case["rounds"]),
        sum(ks),
        state.ticks,
        halt_round,
    )
    assert got == EXPECTED_STATS[name]


@pytest.mark.parametrize("name", list(CASES))
def test_the_case_ends_on_the_round_that_halts(name: str) -> None:
    """No step command ever arrives after the program has halted."""
    case = CASES[name]
    state = LLLMState(program_of(case))
    for index, rnd in enumerate(case["rounds"][1:], start=1):
        assert not state.halted, f"round {index} arrived after the halt"
        state.run(int(rnd["in"][0]))
    assert state.halted


#: How each case dies: the glyph he ends on, and whether that glyph is a wall cell.
EXPECTED_ENDINGS = {
    "one tick at a time": ("H", False),
    "first steps": ("H", False),
    "around the block": ("H", False),
    "off the edge": ("-", True),
    "widdershins": ("|", True),
    "crossroads": ("H", False),
    "revolving door": ("-", True),
    "swan dive": ("-", True),
    "hall of mirrors": ("H", False),
    "victory lap": ("|", True),
}


@pytest.mark.parametrize("name", list(CASES))
def test_how_each_case_halts(name: str) -> None:
    case = CASES[name]
    state = replay(case)
    program = state.program
    glyph = program.char_at(state.x, state.y)
    assert (glyph, program.is_wall(state.x, state.y)) == EXPECTED_ENDINGS[name]


@pytest.mark.parametrize("name", list(CASES))
def test_the_halted_man_is_drawn_where_he_stopped_and_never_moves_again(
    name: str,
) -> None:
    """He is painted 9 on the cell he stopped on — wall or ``H`` — and stays there."""
    case = CASES[name]
    state = replay(case)
    frame = render(state)
    assert frame[state.y][state.x] == "9"
    assert frame == case["rounds"][-1]["frames"][0]

    x, y = state.x, state.y
    for _ in range(50):
        state.step()
    assert (state.x, state.y) == (x, y)
    assert render(state) == frame


@pytest.mark.parametrize(
    "name", ["off the edge", "widdershins", "revolving door", "swan dive", "victory lap"]
)
def test_hitting_a_wall_moves_him_onto_the_wall_cell(name: str) -> None:
    """The wall halt is *on* the wall, not one cell short of it."""
    case = CASES[name]
    program = program_of(case)
    state = LLLMState(program)
    before = (state.x, state.y)
    while not state.halted:
        before = (state.x, state.y)
        state.step()
    assert program.is_wall(state.x, state.y)
    assert not program.is_wall(*before)
    assert abs(state.x - before[0]) + abs(state.y - before[1]) == 1


def test_the_starting_cell_is_empty_space_afterwards() -> None:
    """``revolving door`` walks back over its own ``@``: it must be black and inert."""
    case = CASES["revolving door"]
    program = program_of(case)
    assert classify(program, *program.start) == ("empty", 0)

    state = LLLMState(program)
    seen_again = False
    while not state.halted:
        state.step()
        if (state.x, state.y) == program.start:
            seen_again = True
            # He is drawn, but the underlying cell is still black.
            assert render(state)[state.y][state.x] == "9"
    assert seen_again
    assert render(state)[program.start[1]][program.start[0]] == "0"


def test_plus_and_minus_are_walls_only_on_the_room_border() -> None:
    """The disambiguation is positional: perimeter -> wall (4), inside -> arithmetic (a)."""
    for name, case in CASES.items():
        program = program_of(case)
        for y in range(program.height):
            for x in range(program.width):
                if program.char_at(x, y) not in "+-":
                    continue
                kind, colour = classify(program, x, y)
                if program.is_wall(x, y):
                    assert (kind, colour) == (KIND_WALL, COLOUR_WALL), (name, x, y)
                else:
                    assert (kind, colour) == (KIND_ARITH, COLOUR_ARITH), (name, x, y)

    # ``swan dive``'s second row is ``|@8-+2MX |``: the outer bars are walls, the inner
    # ``-`` and ``+`` are arithmetic.
    program = program_of(CASES["swan dive"])
    assert program.rows[1] == "|@8-+2MX |"
    assert [classify(program, x, 1)[1] for x in range(program.width)] == [
        COLOUR_WALL,
        0,
        8,
        COLOUR_ARITH,
        COLOUR_ARITH,
        8,
        12,
        3,
        0,
        COLOUR_WALL,
    ]


def test_the_input_guarantees_hold_in_the_public_data() -> None:
    for name, case in CASES.items():
        program = program_of(case)
        assert 4 <= program.width <= 16 and 4 <= program.height <= 16, name
        assert len(case["rounds"]) <= 30, name
        assert sum(row.count("@") for row in program.rows) == 1, name
        # Every public room fills its whole program grid.
        assert program.room == (0, 0, program.width - 1, program.height - 1), name
        ks = [int(r["in"][0]) for r in case["rounds"][1:]]
        assert all(1 <= k <= 64 for k in ks), name
        assert replay(case).ticks <= 200, name
        body = "".join(program.rows)
        assert set(body) <= set(" @+-|<>^v0123456789MXH"), name


def test_the_man_starts_on_the_at_sign_facing_east() -> None:
    """``first steps`` is the minimal proof: one tick east from ``@`` onto the ``v``."""
    program = parse_program("+--+|@v|| H|+--+", 4, 4)
    state = LLLMState(program)
    assert (state.x, state.y) == program.start == (1, 1)
    state.step()
    assert (state.x, state.y) == (2, 1)


def test_x_turns_clockwise_and_counter_clockwise() -> None:
    """``swan dive`` turns clockwise on A>0; ``hall of mirrors`` reaches A<0."""
    assert replay(CASES["swan dive"]).a > 0
    assert replay(CASES["hall of mirrors"]).a < 0
