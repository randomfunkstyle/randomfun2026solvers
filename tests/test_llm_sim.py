"""The pure-Python ``little-little-man`` reference, against every public frame.

This module is the oracle any hardware/asm build is written against, so the bar is
byte-for-byte: each round commits exactly one 16x16 frame of 16 hex digits, and every
one of them has to come out identical to the published data.

The semantics the public data pins down, and that are asserted below:

* men start on their room's ``@`` facing **East**, one man per room;
* a tick is *shift the pipes, then execute every man, then move every man* — so a
  value that lands in the last pipe cell this tick is receivable by an ``r`` on the
  same tick (``hello neighbor``, ``bucket brigade``);
* ``s`` on a full source cell and ``r`` on an empty destination cell **block**: the
  man stays put and retries (``traffic jam`` jams for five ticks; ``coin toss`` and
  ``grand tour`` freeze with a man still blocked on an ``r``);
* stepping onto a wall freezes the *whole* program, and the tick completes in full
  first (``pileup``, ``cliffhanger``, ``coin toss``, ``grand tour``);
* glyph meaning is positional: ``v`` inside a room is a heading and outside one is a
  pipe cell, ``-``/``|``/``+`` are walls on a room perimeter, arithmetic inside, and
  pipe bodies outside.

Two things the public cases do *not* pin down were resolved against the bundled
reference engine (``littleman/lm.mjs`` + ``littleman.wasm``) and are asserted here on
synthetic grids instead:

* a **train** of adjacent values in a pipe shifts together in one tick
  (:func:`test_a_train_of_values_shifts_together`) — every public case only ever has
  adjacent values in a pipe that is completely full, so either rule fits the data;
* the ``s``/``r`` nearest-pipe **tie-break** (:func:`test_nearest_pipe_tie_break_*`) —
  no public program has two pipes on the same side of one room.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers.llm_sim import (
    COLOUR_ARITH,
    COLOUR_MAN,
    COLOUR_PIPE,
    COLOUR_PIPE_FULL,
    COLOUR_WALL,
    EAST,
    KIND_ARITH,
    KIND_HEADING,
    KIND_PIPE,
    KIND_RECV,
    KIND_SEND,
    KIND_WALL,
    LLMState,
    Program,
    classify,
    parse_program,
    render,
    run_case,
    run_rounds_from_inputs,
    self_check,
)

REPO = Path(__file__).parents[1]


def public_cases() -> list[dict]:
    problem = REPO / "tasks" / "problems" / "little-little-man.json"
    return json.loads(problem.read_text(encoding="utf-8"))["publicTestData"]


CASES = {case["name"]: case for case in public_cases()}


def case_rounds(case: dict) -> list[list[str]]:
    return [list(r["in"]) for r in case["rounds"]]


def expected_frames(case: dict) -> list[list[list[str]]]:
    return [list(r["frames"]) for r in case["rounds"]]


def program_of(case: dict) -> Program:
    head = [int(t) for t in case["rounds"][0]["in"]]
    return parse_program(head[2:], head[0], head[1])


def replay(case: dict) -> LLMState:
    """Run the whole case and hand back the final state."""
    state = LLMState(program_of(case))
    for rnd in case["rounds"][1:]:
        state.run(int(rnd["in"][0]))
    return state


def grid(text: str) -> Program:
    """Parse a rectangular ASCII block (leading newline allowed) as a program."""
    rows = text.strip("\n").split("\n")
    width = max(len(row) for row in rows)
    rows = [row.ljust(width) for row in rows]
    return parse_program("".join(rows), width, len(rows))


# ── the public data, end to end ──────────────────────────────────────────────
def test_there_are_fourteen_public_cases() -> None:
    assert len(CASES) == 14


@pytest.mark.parametrize("name", list(CASES))
def test_every_public_frame_matches(name: str) -> None:
    case = CASES[name]
    assert run_case(case_rounds(case)) == expected_frames(case)


def test_the_module_self_check_passes() -> None:
    assert self_check(verbose=False)


@pytest.mark.parametrize("name", list(CASES))
def test_every_round_commits_exactly_one_16x16_frame(name: str) -> None:
    case = CASES[name]
    frames = run_case(case_rounds(case))
    assert [f[0] for f in frames] == run_rounds_from_inputs(case_rounds(case))
    for one_round in frames:
        assert len(one_round) == 1
        rows = one_round[0]
        assert len(rows) == 16
        assert all(len(row) == 16 for row in rows)
        assert all(ch in "0123456789abcdef" for row in rows for ch in row)


#: ``(W, H, rooms, pipes, pipe cells, rounds, sum(k), ticks run, froze on a wall)``.
EXPECTED_STATS = {
    "first steps": (4, 4, 1, 0, 0, 4, 3, 3, False),
    "countdown relay": (13, 11, 2, 1, 2, 16, 82, 41, False),
    "hello neighbor": (16, 5, 2, 1, 3, 8, 7, 7, False),
    "bucket brigade": (9, 13, 3, 2, 4, 5, 9, 9, False),
    "ping pong": (16, 5, 2, 2, 13, 11, 45, 16, False),
    "switchboard": (16, 16, 3, 2, 6, 9, 12, 12, False),
    "traffic jam": (16, 8, 2, 1, 2, 15, 14, 14, False),
    "coin toss": (11, 15, 3, 2, 4, 5, 48, 4, True),
    "pileup": (16, 5, 2, 0, 0, 7, 52, 6, True),
    "long haul": (11, 10, 2, 1, 12, 13, 48, 20, False),
    "cliffhanger": (9, 10, 2, 1, 8, 4, 60, 13, True),
    "bounce house": (14, 16, 2, 0, 0, 9, 32, 32, False),
    "grand tour": (10, 12, 2, 1, 3, 24, 120, 58, True),
    "below zero": (10, 10, 2, 1, 2, 5, 49, 9, False),
}


@pytest.mark.parametrize("name", list(CASES))
def test_measured_statistics(name: str) -> None:
    case = CASES[name]
    program = program_of(case)
    ks = [int(r["in"][0]) for r in case["rounds"][1:]]
    state = replay(case)
    got = (
        program.width,
        program.height,
        len(program.rooms),
        len(program.pipes),
        sum(p.length for p in program.pipes),
        len(case["rounds"]),
        sum(ks),
        state.ticks,
        state.frozen_on_wall,
    )
    assert got == EXPECTED_STATS[name]


@pytest.mark.parametrize("name", list(CASES))
def test_the_case_ends_on_the_round_that_halts(name: str) -> None:
    """No step command ever arrives after the program has halted."""
    case = CASES[name]
    state = LLMState(program_of(case))
    for index, rnd in enumerate(case["rounds"][1:], start=1):
        assert not state.halted, f"round {index} arrived after the halt"
        state.run(int(rnd["in"][0]))
    assert state.halted


@pytest.mark.parametrize("name", list(CASES))
def test_a_halted_program_never_changes_again(name: str) -> None:
    case = CASES[name]
    state = replay(case)
    frame = state.render()
    assert frame == case["rounds"][-1]["frames"][0]
    trace = state.trace_state()
    for _ in range(64):
        state.step()
    assert state.render() == frame
    assert state.trace_state() == trace


def test_the_input_guarantees_hold_in_the_public_data() -> None:
    for name, case in CASES.items():
        program = program_of(case)
        assert 4 <= program.width <= 16 and 4 <= program.height <= 16, name
        assert 1 <= len(program.rooms) <= 3, name
        assert len(program.pipes) <= 2, name
        assert sum(p.length for p in program.pipes) <= 20, name
        assert len(case["rounds"]) <= 30, name
        ks = [int(r["in"][0]) for r in case["rounds"][1:]]
        assert all(1 <= k <= 64 for k in ks), name
        assert replay(case).ticks <= 100, name
        # exactly one man per room, and every man starts on his room's ``@``
        assert len(program.starts) == len(program.rooms), name
        assert all(program.char_at(x, y) == "@" for x, y, _ in program.starts), name
        body = "".join(program.rows)
        assert set(body) <= set(" @+-|<>^v0123456789MXHsr"), name


def test_men_start_on_their_at_sign_facing_east_in_reading_order() -> None:
    program = program_of(CASES["bucket brigade"])
    assert program.starts == ((1, 1, 0), (1, 6, 1), (1, 11, 2))
    state = LLMState(program)
    assert [(m.x, m.y, m.room, m.heading, m.a, m.b) for m in state.men] == [
        (1, 1, 0, EAST, 0, 0),
        (1, 6, 1, EAST, 0, 0),
        (1, 11, 2, EAST, 0, 0),
    ]


# ── pipe parsing: flow order, bends, endpoints ───────────────────────────────
def test_pipe_flow_order_source_arrowhead_first() -> None:
    """``long haul``: one 12-cell pipe with three bends, ``v`` then ``>`` then ``v``."""
    program = program_of(CASES["long haul"])
    (pipe,) = program.pipes
    assert (pipe.src, pipe.dst) == (0, 1)
    assert pipe.cells == (
        (5, 3),
        (5, 4),
        (6, 4),
        (7, 4),
        (8, 4),
        (9, 4),
        (10, 4),
        (10, 5),
        (10, 6),
        (10, 7),
        (9, 7),
        (8, 7),
    )
    assert pipe.dirs == (
        (0, 1),
        (1, 0),
        (1, 0),
        (1, 0),
        (1, 0),
        (1, 0),
        (0, 1),
        (0, 1),
        (0, 1),
        (-1, 0),
        (-1, 0),
        (-1, 0),
    )
    # the source arrowhead hangs off room 0's bottom wall, the destination
    # arrowhead points into room 1's right wall
    assert pipe.source_head == (5, 3) and pipe.src_attach == (5, 2)
    assert pipe.dest_head == (8, 7) and pipe.dst_attach == (7, 7)
    assert program.is_wall(*pipe.src_attach) and program.is_wall(*pipe.dst_attach)
    # a bend is where the flow direction changes; the glyph names the *new* direction
    bends = [c for i, c in enumerate(pipe.cells) if i and pipe.dirs[i] != pipe.dirs[i - 1]]
    assert bends == [(5, 4), (10, 4), (10, 7)]
    assert [program.char_at(*c) for c in bends] == [">", "v", "<"]


def test_two_pipes_run_in_opposite_directions() -> None:
    """``ping pong``: room 0 -> room 1 along row 1, room 1 -> room 0 the long way round."""
    program = program_of(CASES["ping pong"])
    there, back = program.pipes
    assert (there.src, there.dst) == (0, 1)
    assert there.cells == ((8, 1), (9, 1))
    assert (back.src, back.dst) == (1, 0)
    assert back.cells[0] == (12, 3) and back.cells[-1] == (4, 3)
    assert back.length == 11
    assert program.rooms[0].outgoing == (0,) and program.rooms[0].incoming == (1,)
    assert program.rooms[1].outgoing == (1,) and program.rooms[1].incoming == (0,)


def test_a_two_cell_pipe_is_two_arrowheads() -> None:
    """``countdown relay``'s pipe is the minimum length: ``v`` out, ``v`` in."""
    program = program_of(CASES["countdown relay"])
    (pipe,) = program.pipes
    assert pipe.cells == ((7, 5), (7, 6))
    assert [program.char_at(*c) for c in pipe.cells] == ["v", "v"]
    assert pipe.length == 2


def test_pipes_are_indexed_and_ordered_by_their_source_arrowhead() -> None:
    program = program_of(CASES["bucket brigade"])
    assert [(p.id, p.src, p.dst, p.cells) for p in program.pipes] == [
        (0, 0, 1, ((4, 3), (4, 4))),
        (1, 1, 2, ((3, 8), (3, 9))),
    ]
    assert program.pipe_of[(4, 4)] == (0, 1)
    assert program.pipe_of[(3, 8)] == (1, 0)


# ── the wall / operation / pipe disambiguation ───────────────────────────────
def test_v_is_a_heading_inside_a_room_and_a_pipe_cell_outside_one() -> None:
    program = program_of(CASES["coin toss"])
    assert program.char_at(6, 6) == "v" and program.rooms[1].inside(6, 6)
    assert classify(program, 6, 6)[0] == KIND_HEADING
    for cell in ((6, 10), (6, 11)):
        assert program.char_at(*cell) == "v"
        assert classify(program, *cell) == (KIND_PIPE, COLOUR_PIPE)


def test_bar_and_dash_are_walls_on_a_perimeter_and_pipe_bodies_outside() -> None:
    program = program_of(CASES["long haul"])
    # ``|`` down column 10 is a pipe body; ``|`` down column 0 is room wall
    for y in (5, 6):
        assert program.char_at(10, y) == "|"
        assert classify(program, 10, y) == (KIND_PIPE, COLOUR_PIPE)
        assert classify(program, 0, y) == (KIND_WALL, COLOUR_WALL)
    # ``-`` along row 4 is a pipe body; ``-`` along row 0 is room wall
    for x in (6, 7, 8, 9):
        assert program.char_at(x, 4) == "-"
        assert classify(program, x, 4) == (KIND_PIPE, COLOUR_PIPE)
        assert classify(program, x, 0) == (KIND_WALL, COLOUR_WALL)


def test_plus_and_minus_are_walls_only_on_a_room_border() -> None:
    for name, case in CASES.items():
        program = program_of(case)
        for y in range(program.height):
            for x in range(program.width):
                if program.char_at(x, y) not in "+-":
                    continue
                kind, colour = classify(program, x, y)
                if program.is_wall(x, y):
                    expected = (KIND_WALL, COLOUR_WALL)
                elif (x, y) in program.pipe_of:
                    expected = (KIND_PIPE, COLOUR_PIPE)
                else:
                    expected = (KIND_ARITH, COLOUR_ARITH)
                assert (kind, colour) == expected, (name, x, y)

    # ``cliffhanger``'s first row is ``|@1+s3s<|``: the bars are walls, the ``+`` is
    # arithmetic, and an arithmetic ``+`` never fakes a room corner.
    program = program_of(CASES["cliffhanger"])
    assert program.rows[1] == "|@1+s3s<|"
    colours = [classify(program, x, 1)[1] for x in range(program.width)]
    assert colours == [4, 0, 8, 10, 13, 8, 13, 3, 4]
    assert len(program.rooms) == 2


def test_the_at_sign_is_ordinary_empty_space() -> None:
    """``pileup`` walks its second man back over his own ``@``."""
    program = program_of(CASES["pileup"])
    for x, y, _ in program.starts:
        assert classify(program, x, y) == ("empty", 0)
    state = replay(CASES["pileup"])
    back_home = [m for m in state.men if (m.x, m.y) in {(sx, sy) for sx, sy, _ in program.starts}]
    assert back_home, "expected a man standing on his own start cell"
    assert state.render()[back_home[0].y][back_home[0].x] == "9"


# ── the nearest-pipe rule ────────────────────────────────────────────────────
#: Two outgoing pipes on opposite sides of one room, verified against the reference
#: engine's ``lm.mjs route``: the ``s`` at (4, 7) is 3 cells from both arrowheads and
#: the tie goes to (4, 4), the earlier one in reading order.
TIE_SEND = """
+-------+
|@ H    |
+-------+
    ^
    ^
+-------+
|@  s   |
|   s   |
|   s   |
+-------+
    v
    v
+-------+
|@ H    |
+-------+
"""

#: The mirror image: two *incoming* pipes, three ``r`` cells, same tie at (4, 7).
TIE_RECV = """
+-------+
|@ H    |
+-------+
    v
    v
+-------+
|@  r   |
|   r   |
|   r   |
+-------+
    ^
    ^
+-------+
|@ H    |
+-------+
"""

#: A tie across two *different* axes: the ``s`` at (7, 6) is 4 cells from the upward
#: pipe's arrowhead (9, 4) and 4 from the leftward pipe's (4, 7).  Reading order picks
#: (9, 4).  Also verified against ``lm.mjs route``.
TIE_CROSS = """
      +-----+
      |@H   |
      +-----+
         ^
         ^
     +-------+
+-+  |@ss    |
|@|<<|       |
+-+  |s      |
     +-------+
"""


@pytest.mark.parametrize(
    ("cell", "arrowhead"),
    [((4, 6), (4, 4)), ((4, 7), (4, 4)), ((4, 8), (4, 10))],
)
def test_nearest_pipe_tie_break_for_send(cell: tuple[int, int], arrowhead: tuple[int, int]) -> None:
    program = grid(TIE_SEND)
    assert classify(program, *cell)[0] == KIND_SEND
    assert program.rooms[1].outgoing == (0, 1)
    assert program.pipes[program.binding[cell]].source_head == arrowhead


@pytest.mark.parametrize(
    ("cell", "arrowhead"),
    [((4, 6), (4, 4)), ((4, 7), (4, 4)), ((4, 8), (4, 10))],
)
def test_nearest_pipe_tie_break_for_receive(
    cell: tuple[int, int], arrowhead: tuple[int, int]
) -> None:
    program = grid(TIE_RECV)
    assert classify(program, *cell)[0] == KIND_RECV
    assert program.rooms[1].incoming == (0, 1)
    assert program.pipes[program.binding[cell]].dest_head == arrowhead


@pytest.mark.parametrize(
    ("cell", "arrowhead"),
    [((7, 6), (9, 4)), ((8, 6), (9, 4)), ((6, 8), (4, 7))],
)
def test_nearest_pipe_tie_break_across_two_sides(
    cell: tuple[int, int], arrowhead: tuple[int, int]
) -> None:
    program = grid(TIE_CROSS)
    assert program.pipes[program.binding[cell]].source_head == arrowhead


def test_binding_is_static_and_covers_every_send_and_receive_cell() -> None:
    for name, case in CASES.items():
        program = program_of(case)
        for y in range(program.height):
            for x in range(program.width):
                kind = program.kind_at(x, y)
                if kind not in (KIND_SEND, KIND_RECV):
                    continue
                assert (x, y) in program.binding, (name, x, y)
                pipe = program.pipes[program.binding[(x, y)]]
                room = program.rooms[program.room_of[(x, y)]]
                assert (pipe.src if kind == KIND_SEND else pipe.dst) == room.id, (name, x, y)


def test_switchboard_binds_its_two_receives_to_the_two_incoming_pipes() -> None:
    """One room, two pipes arriving on the same wall — and the public data's one real tie."""
    program = program_of(CASES["switchboard"])
    room = program.rooms[2]
    assert room.incoming == (0, 1)
    assert program.pipes[0].dest_head == (2, 5)
    assert program.pipes[1].dest_head == (12, 5)
    # (7, 7) is 7 cells from both arrowheads: the tie goes to (2, 5), earlier in
    # reading order (same row, smaller column).
    assert program.binding[(7, 7)] == 0
    assert program.binding[(10, 7)] == 1  # |10-2| + 2 = 10  vs  |10-12| + 2 = 4


# ── pipe transport ───────────────────────────────────────────────────────────
#: ``|@1ssH|`` sends the same value on two consecutive ticks, so slots 0 and 1 are both
#: full at the end of tick 4 with three free cells ahead.  The receiving room just runs
#: a 4-cell loop and never drains the pipe.
TRAIN = """
+-----+
|@1ssH|
+-----+
   v
   v
   v
   v
+-----+
|@>v  |
| ^<  |
+-----+
"""


def test_a_train_of_values_shifts_together() -> None:
    """Both cells of a solid train advance in one tick, not just the front one.

    The public cases cannot tell the two rules apart (their only adjacent values sit
    in a pipe that is completely full), so this is pinned against the reference
    engine: ``lm.mjs tick train.man 5`` reports values at indices 1 and 2.
    """
    state = LLMState(grid(TRAIN))
    state.run(4)
    assert state.pipe_values[0] == [1, 1, None, None]
    state.step()
    assert state.pipe_values[0] == [None, 1, 1, None]  # not [1, None, 1, None]
    state.step()
    assert state.pipe_values[0] == [None, None, 1, 1]
    state.step()
    assert state.pipe_values[0] == [None, None, 1, 1]  # the front is at the far end


def test_a_send_blocks_on_a_full_source_cell_and_retries_every_tick() -> None:
    """``traffic jam``: a 2-cell pipe, three sends, and a receiver that lags."""
    program = program_of(CASES["traffic jam"])
    state = LLMState(program)
    (pipe,) = program.pipes
    assert pipe.length == 2
    blocked_ticks = []
    full_ticks = []
    for _ in range(14):
        state.step()
        sender = state.men[0]
        if sender.blocked:
            blocked_ticks.append((state.ticks, sender.x, sender.y))
        if all(v is not None for v in state.pipe_values[0]):
            full_ticks.append(state.ticks)
    # The pipe is full from tick 5; the third ``s`` arrives on tick 6 and then retries
    # the same cell on ticks 7-10, moving on only once the receiver has popped.
    assert full_ticks == [5, 6, 7, 8, 9]
    assert [t for t, _, _ in blocked_ticks] == [7, 8, 9, 10]
    assert {(x, y) for _, x, y in blocked_ticks} == {(7, 1)}
    assert program.char_at(7, 1) == "s"
    # ``s`` writes slot 0 while ``r`` pops the last slot: both happen on tick 11.
    assert state.pipe_values[0] == [None, None]


def test_a_receive_blocks_on_an_empty_destination_cell() -> None:
    """``grand tour`` freezes with its second man still waiting on an ``r``."""
    state = replay(CASES["grand tour"])
    assert state.frozen_on_wall
    waiting = state.men[1]
    assert waiting.blocked and not waiting.halted
    assert state.program.char_at(waiting.x, waiting.y) == "r"
    assert state.pipe_values[0][-1] is None


def test_a_value_can_arrive_and_be_received_on_the_same_tick() -> None:
    """Pipes shift *before* the men act, so the last cell can be popped immediately."""
    program = program_of(CASES["hello neighbor"])
    state = LLMState(program)
    (pipe,) = program.pipes
    assert pipe.length == 3
    receiver = state.men[1]
    state.run(3)
    # tick 3 put the 3 into slot 0; the receiver has been blocked on his ``r`` since tick 2
    assert state.pipe_values[0] == [3, None, None]
    assert receiver.blocked and program.char_at(receiver.x, receiver.y) == "r"
    state.step()
    assert state.pipe_values[0] == [None, 3, None] and receiver.blocked and receiver.a == 0
    # Tick 5: the shift walks the 3 into the last cell, and the ``r`` pops it the *same*
    # tick — so the value is never rendered sitting in the destination arrowhead.
    state.step()
    assert state.pipe_values[0] == [None, None, None]
    assert receiver.a == 3 and not receiver.blocked
    assert (receiver.x, receiver.y) == (13, 1)


# ── halting ──────────────────────────────────────────────────────────────────
def test_stepping_onto_a_wall_freezes_the_whole_program_after_a_full_tick() -> None:
    """``pileup``: man 0 walks into the top wall; man 1 still moves on that tick."""
    program = program_of(CASES["pileup"])
    state = LLMState(program)
    state.run(5)
    before = [(m.x, m.y) for m in state.men]
    assert not state.halted
    state.step()
    assert state.frozen_on_wall and state.halted and state.ticks == 6
    assert program.is_wall(state.men[0].x, state.men[0].y)
    assert not program.is_wall(state.men[1].x, state.men[1].y)
    # both men moved on the freezing tick, and the wall man is drawn on the wall
    assert all(new != old for new, old in zip([(m.x, m.y) for m in state.men], before, strict=True))
    assert state.render()[state.men[0].y][state.men[0].x] == f"{COLOUR_MAN:x}"


def test_a_frozen_program_can_leave_a_man_mid_instruction() -> None:
    """``cliffhanger`` freezes on the tick the other man first stands on his ``H``."""
    state = replay(CASES["cliffhanger"])
    assert state.frozen_on_wall
    assert state.program.char_at(state.men[0].x, state.men[0].y) == "|"
    assert state.program.char_at(state.men[1].x, state.men[1].y) == "H"
    assert not state.men[1].halted  # the ``H`` never got the chance to fire


def test_the_program_is_done_once_every_man_stands_halted_on_an_h() -> None:
    for name in ("bucket brigade", "ping pong", "long haul", "below zero"):
        state = replay(CASES[name])
        assert not state.frozen_on_wall, name
        assert state.halted, name
        assert all(m.halted for m in state.men), name
        assert all(state.program.char_at(m.x, m.y) == "H" for m in state.men), name


def test_only_a_wall_freeze_ever_leaves_a_value_in_flight() -> None:
    """Every ``H``-halting public case drains its pipes first.

    That is why "do pipes keep shifting after the last man halts on an ``H``?" never
    comes up in the public data: it cannot, because the case ends at the halting round
    and no later ``k`` arrives.  This interpreter stops the whole machine on the halt.
    """
    for name, case in CASES.items():
        state = replay(case)
        in_flight = [v for slots in state.pipe_values for v in slots if v is not None]
        assert not in_flight or state.frozen_on_wall, name


@pytest.mark.parametrize("name", list(CASES))
def test_the_order_men_execute_in_does_not_matter(name: str) -> None:
    """No two men can ever touch the same pipe end, so the execute phase commutes.

    A pipe has one source room and one destination room, and each room holds exactly
    one man, so ``s`` only ever writes slot 0 and ``r`` only ever pops the last slot —
    and those are distinct cells because a pipe is at least two cells long.  A machine
    implementation is therefore free to sequence its men however it likes.
    """
    case = CASES[name]
    forward = LLMState(program_of(case))
    backward = LLMState(program_of(case))
    backward.men.reverse()
    for rnd in case["rounds"][1:]:
        forward.run(int(rnd["in"][0]))
        backward.run(int(rnd["in"][0]))
        assert backward.render() == forward.render()
    assert sorted(m.id for m in backward.men) == sorted(m.id for m in forward.men)
    assert backward.pipe_values == forward.pipe_values


def test_one_man_halts_while_the_others_keep_running() -> None:
    """``bucket brigade``: man 0 reaches ``H`` on tick 6, men 1 and 2 keep going."""
    state = LLMState(program_of(CASES["bucket brigade"]))
    state.run(6)
    assert [m.halted for m in state.men] == [True, False, False]
    frozen = (state.men[0].x, state.men[0].y)
    state.run(3)
    assert (state.men[0].x, state.men[0].y) == frozen
    assert all(m.halted for m in state.men)


# ── rendering ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", list(CASES))
def test_pipe_cells_are_6_when_empty_and_14_when_full(name: str) -> None:
    case = CASES[name]
    program = program_of(case)
    state = LLMState(program)
    for index, rnd in enumerate(case["rounds"]):
        if index:
            state.run(int(rnd["in"][0]))
        frame = render(state)
        for pipe, slots in zip(program.pipes, state.pipe_values, strict=True):
            for (x, y), value in zip(pipe.cells, slots, strict=True):
                want = COLOUR_PIPE_FULL if value is not None else COLOUR_PIPE
                assert frame[y][x] == f"{want:x}", (name, index, x, y)
        # and the published frame agrees
        assert frame == rnd["frames"][0]


def test_everything_outside_the_program_is_black() -> None:
    frame = render(LLMState(program_of(CASES["first steps"])))
    assert frame[4:] == ["0" * 16] * 12
    assert all(row[4:] == "0" * 12 for row in frame[:4])


def test_a_man_is_drawn_on_top_of_whatever_he_stands_on() -> None:
    program = program_of(CASES["below zero"])
    state = LLMState(program)
    for _ in range(9):
        state.step()
        frame = render(state)
        for man in state.men:
            assert frame[man.y][man.x] == f"{COLOUR_MAN:x}"


# ── the trace API ────────────────────────────────────────────────────────────
def test_trace_state_is_plain_json_data_and_deterministic() -> None:
    state = LLMState(program_of(CASES["long haul"]))
    state.run(9)
    trace = state.trace_state()
    assert json.loads(json.dumps(trace)) == trace
    assert trace["ticks"] == 9
    assert trace["halted"] is False and trace["frozen_on_wall"] is False
    assert [m["id"] for m in trace["men"]] == [0, 1]
    assert set(trace["men"][0]) == {
        "id",
        "room",
        "x",
        "y",
        "heading",
        "a",
        "b",
        "blocked",
        "halted",
        "on",
    }
    assert trace["men"][0]["heading"] in ("N", "E", "S", "W")
    (pipe,) = trace["pipes"]
    assert (pipe["id"], pipe["src"], pipe["dst"]) == (0, 0, 1)
    # cells come in flow order, and carry the value each one holds
    assert [(c["x"], c["y"]) for c in pipe["cells"]] == list(state.program.pipes[0].cells)
    assert [c["value"] for c in pipe["cells"]] == list(state.pipe_values[0])
    assert [c["value"] for c in pipe["cells"] if c["value"] is not None] == [3, 2, 1]

    # replaying the same program reproduces the trace exactly
    twin = LLMState(program_of(CASES["long haul"]))
    twin.run(9)
    assert twin.trace_state() == trace


@pytest.mark.parametrize("name", list(CASES))
def test_trace_state_tracks_the_rendered_frame(name: str) -> None:
    """Every man and every value in the trace shows up in the frame — the diff anchor."""
    case = CASES[name]
    state = LLMState(program_of(case))
    for rnd in case["rounds"][1:]:
        state.run(int(rnd["in"][0]))
        trace = state.trace_state()
        frame = state.render()
        for man in trace["men"]:
            assert frame[man["y"]][man["x"]] == f"{COLOUR_MAN:x}"
        for pipe in trace["pipes"]:
            for cell in pipe["cells"]:
                if cell["value"] is not None and frame[cell["y"]][cell["x"]] != f"{COLOUR_MAN:x}":
                    assert frame[cell["y"]][cell["x"]] == f"{COLOUR_PIPE_FULL:x}"
