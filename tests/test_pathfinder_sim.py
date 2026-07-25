"""The ``pathfinder`` oracle and the bit-parallel model that gets compiled to assembly.

Three things are pinned here, in order of how much the ``.asm`` depends on them:

1. both models reproduce **every** frame of all seven public cases, byte for byte;
2. they agree with each other on a 300-board random sweep (open rooms *and* mazes, so
   both short hops and 40-plus-move crossings are covered);
3. the bit-level invariants the assembly is allowed to assume — bit 63 clear everywhere,
   ``avail -= n`` == ``avail & ~n``, ``D += n`` == ``D | n``, and the fact that the cheap
   single-bit horizontal shifts may lie only about border-wall cells.

The measured statistics are pinned too: they are the tick budget.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from pathlib import Path

import pytest
from randomfun2026solvers.pathfinder_sim import (
    BITS,
    CELLS,
    DELTAS,
    DIRECTION_NAMES,
    FLAG,
    HEIGHT,
    PATH,
    ROBOT,
    SIGN_BIT,
    WALL,
    WIDTH,
    WORDS,
    CaseStats,
    Step,
    bfs_dirs,
    bit_of,
    case_stats,
    cell_of,
    distances_from,
    frame_rows,
    free_words,
    free_words_by_accumulation,
    horizontal_source_masks,
    is_set,
    parse_case_rounds,
    setup_frame,
    shl64,
    shortest_path,
    shr64,
    solve_case,
    solve_case_bitset,
    walk_from_dirs,
    word_of,
    words_to_cells,
    xy_of,
)

REPO = Path(__file__).parents[1]

Case = tuple[list[int], int, int, list[int]]


def public_cases() -> list[dict]:
    problem = REPO / "tasks" / "problems" / "pathfinder.json"
    return json.loads(problem.read_text(encoding="utf-8"))["publicTestData"]


CASES = {case["name"]: case for case in public_cases()}


def case_rounds(case: dict) -> list[list[int]]:
    return [[int(v) for v in r["in"]] for r in case["rounds"]]


def public_case(name: str) -> Case:
    return parse_case_rounds(case_rounds(CASES[name]))


def expected_frames(case: dict) -> list[list[str]]:
    """Every frame the case commits, rounds concatenated, as rows of hex digits."""
    return [list(frame) for r in case["rounds"] for frame in r["frames"]]


# -- the public data ------------------------------------------------------------------


@pytest.mark.parametrize("name", list(CASES))
def test_the_oracle_reproduces_every_public_frame(name: str) -> None:
    board, rx, ry, flags = public_case(name)
    got = [frame_rows(frame) for frame in solve_case(board, rx, ry, flags)]
    assert got == expected_frames(CASES[name])


@pytest.mark.parametrize("name", list(CASES))
def test_the_bitset_model_reproduces_every_public_frame(name: str) -> None:
    board, rx, ry, flags = public_case(name)
    got = [frame_rows(frame) for frame in solve_case_bitset(board, rx, ry, flags)]
    assert got == expected_frames(CASES[name])


@pytest.mark.parametrize("name", list(CASES))
def test_both_models_produce_identical_frames_on_the_public_cases(name: str) -> None:
    board, rx, ry, flags = public_case(name)
    assert solve_case_bitset(board, rx, ry, flags) == solve_case(board, rx, ry, flags)


@pytest.mark.parametrize("name", list(CASES))
def test_the_frame_count_is_one_plus_the_sum_of_the_shortest_paths(name: str) -> None:
    board, rx, ry, flags = public_case(name)
    robot = cell_of(rx, ry)
    lengths = []
    for flag in flags:
        lengths.append(len(shortest_path(board, robot, flag)))
        robot = flag
    assert len(solve_case(board, rx, ry, flags)) == 1 + sum(lengths)
    # ...and that is exactly what the judge asks each round to commit
    assert [1, *lengths] == [len(r["frames"]) for r in CASES[name]["rounds"]]


@pytest.mark.parametrize("name", list(CASES))
def test_the_setup_round_commits_one_frame_of_walls_paths_and_the_robot(name: str) -> None:
    board, rx, ry, flags = public_case(name)
    frame = solve_case(board, rx, ry, flags)[0]
    assert frame == setup_frame(board, cell_of(rx, ry))
    assert frame_rows(frame) == list(CASES[name]["rounds"][0]["frames"][0])
    colours = {colour for row in frame for colour in row}
    assert colours <= {PATH, WALL, ROBOT}
    assert sum(row.count(ROBOT) for row in frame) == 1
    assert frame[ry][rx] == ROBOT


@pytest.mark.parametrize("name", list(CASES))
def test_the_flag_is_drawn_on_every_frame_of_a_round_except_the_last(name: str) -> None:
    """No special case in the model: the robot's last step overwrites the flag pixel."""
    board, rx, ry, flags = public_case(name)
    frames = solve_case(board, rx, ry, flags)
    at = 1
    previous = cell_of(rx, ry)
    for flag in flags:
        fx, fy = xy_of(flag)
        length = len(shortest_path(board, previous, flag))
        round_frames = frames[at : at + length]
        assert [frame[fy][fx] for frame in round_frames] == [FLAG] * (length - 1) + [ROBOT]
        for frame in round_frames:
            assert sum(row.count(FLAG) for row in frame) <= 1
            assert sum(row.count(ROBOT) for row in frame) == 1
        previous = flag
        at += length
    assert at == len(frames)


def test_a_one_move_round_never_draws_the_flag_at_all() -> None:
    """``a straight shot`` ends on a flag one step away, so its only frame shows the robot."""
    board, rx, ry, flags = public_case("a straight shot")
    frames = solve_case(board, rx, ry, flags)
    assert len(frames) == 23
    last = frames[-1]
    assert not any(FLAG in row for row in last)
    fx, fy = xy_of(flags[-1])
    assert last[fy][fx] == ROBOT
    assert len(CASES["a straight shot"]["rounds"][-1]["frames"]) == 1


@pytest.mark.parametrize("name", list(CASES))
def test_the_board_guarantees_hold_in_the_public_data(name: str) -> None:
    """Borders walled, the robot and every flag on a path, every flag reachable and new."""
    board, rx, ry, flags = public_case(name)
    for index in range(WIDTH):
        assert board[cell_of(index, 0)] == board[cell_of(index, HEIGHT - 1)] == 1
        assert board[cell_of(0, index)] == board[cell_of(WIDTH - 1, index)] == 1
    robot = cell_of(rx, ry)
    assert board[robot] == 0
    for flag in flags:
        assert board[flag] == 0
        assert flag != robot
        assert distances_from(board, flag)[robot] is not None
        robot = flag


# -- the tie-break --------------------------------------------------------------------


def open_room(walls: Sequence[tuple[int, int]] = ()) -> list[int]:
    """A board whose 14x14 interior is all path, minus ``walls``."""
    board = [1] * CELLS
    for y in range(1, HEIGHT - 1):
        for x in range(1, WIDTH - 1):
            board[cell_of(x, y)] = 0
    for x, y in walls:
        board[cell_of(x, y)] = 1
    return board


def decreasing_directions(board: Sequence[int], cell: int, flag: int) -> list[str]:
    """The names of the directions from ``cell`` that lie on *some* shortest path to ``flag``."""
    dist = distances_from(board, flag)
    here = dist[cell]
    assert here is not None
    return [
        name
        for name, delta in zip(DIRECTION_NAMES, DELTAS, strict=True)
        if not board[cell + delta] and dist[cell + delta] == here - 1
    ]


#: ``(flag, first step, the directions that tie)`` from ``7,7`` in an empty room.
#: A diagonal flag makes exactly two directions shortest, and the winner is the earlier of
#: the two in ``up, right, down, left``.
TIE_BREAKS = [
    ((9, 5), (7, 6), ["up", "right"]),
    ((9, 9), (8, 7), ["right", "down"]),
    ((5, 9), (7, 8), ["down", "left"]),
]


@pytest.mark.parametrize("flag,first,tied", TIE_BREAKS)
def test_a_tied_first_move_goes_to_the_earliest_of_up_right_down_left(
    flag: tuple[int, int], first: tuple[int, int], tied: list[str]
) -> None:
    board = open_room()
    robot = cell_of(7, 7)
    goal = cell_of(*flag)
    assert decreasing_directions(board, robot, goal) == tied
    path = shortest_path(board, robot, goal)
    assert path[0] == cell_of(*first)
    dn, de, ds, dw, levels = bfs_dirs(free_words(board), goal, robot)
    assert walk_from_dirs(dn, de, ds, dw, robot, goal) == path
    assert levels == len(path) == 4


def test_left_is_taken_only_when_nothing_else_is_shortest() -> None:
    """Left is last, so it can never *win* a tie — it is chosen exactly when it is alone.

    The board is wide open: up, right and down are all walkable from ``7,7``, they are just
    not on a shortest path. So this pins the priority chain rather than wall avoidance.
    """
    board = open_room()
    robot, goal = cell_of(7, 7), cell_of(5, 7)
    for delta in DELTAS:
        assert board[robot + delta] == 0
    assert decreasing_directions(board, robot, goal) == ["left"]
    path = shortest_path(board, robot, goal)
    assert path == [cell_of(6, 7), cell_of(5, 7)]
    dn, de, ds, dw, _ = bfs_dirs(free_words(board), goal, robot)
    assert walk_from_dirs(dn, de, ds, dw, robot, goal) == path
    assert dw[word_of(robot)] & bit_of(robot)
    assert not any(mask[word_of(robot)] & bit_of(robot) for mask in (dn, de, ds))


def test_up_wins_even_when_three_directions_are_open_and_two_tie() -> None:
    """A pillar at ``8,6`` leaves ``up`` and ``right`` tied at 6 moves; ``up`` takes it."""
    board = open_room([(8, 6), (8, 5)])
    robot, goal = cell_of(7, 7), cell_of(9, 4)
    assert decreasing_directions(board, robot, goal) == ["up", "right"]
    assert shortest_path(board, robot, goal)[0] == cell_of(7, 6)


def test_the_walk_around_a_wall_prefers_up_then_right_step_by_step() -> None:
    """A single-file corridor: the tie-break is exercised at every one of the 8 steps."""
    board = open_room([(x, 6) for x in range(1, 8)])
    robot, goal = cell_of(7, 7), cell_of(7, 5)
    path = shortest_path(board, robot, goal)
    assert [xy_of(cell) for cell in path] == [
        (8, 7),
        (8, 6),
        (8, 5),
        (7, 5),
    ]
    dn, de, ds, dw, levels = bfs_dirs(free_words(board), goal, robot)
    assert walk_from_dirs(dn, de, ds, dw, robot, goal) == path
    assert levels == 4


# -- the 64-bit helpers ---------------------------------------------------------------


def test_the_shifts_truncate_to_64_bits_and_never_sign_extend() -> None:
    assert shl64(1 << 63, 1) == 0
    assert shl64(1 << 62, 2) == 0
    assert shl64(1, 63) == SIGN_BIT
    assert shr64(SIGN_BIT, 63) == 1
    assert shr64((1 << 64) - 1, 48) == 0xFFFF
    assert shr64(shl64(0xDEAD, 48), 48) == 0xDEAD


def test_a_word_holds_four_rows_in_reversed_bit_order() -> None:
    """Bit ``b`` of word ``w`` is cell ``64w + (63 - b)`` — the first cell lands in bit 63."""
    for word in range(WORDS):
        for row in range(4):
            for x in range(WIDTH):
                cell = cell_of(x, 4 * word + row)
                assert word_of(cell) == word
                assert bit_of(cell) == 1 << (BITS - 1 - (row * WIDTH + x))
        assert bit_of(word * BITS) == SIGN_BIT
        assert bit_of(word * BITS + BITS - 1) == 1


def test_bit_63_is_row_4w_column_0_which_is_always_a_border_wall() -> None:
    """Why floor-division is a logical shift: the sign bit can never be set.

    Bit 63 of word ``w`` is cell ``64w + 0`` = ``(x=0, y=4w)``. Column 0 is part of the
    board's border, and the problem guarantees *every* border cell is a wall, so that bit is
    clear in ``free`` — and therefore in ``avail``, ``prev`` and every direction mask, all of
    which are subsets of ``free``.
    """
    for word in range(WORDS):
        cell = word * BITS
        assert bit_of(cell) == SIGN_BIT
        assert xy_of(cell) == (0, 4 * word)
    for name in CASES:
        board, *_ = public_case(name)
        for word in range(WORDS):
            assert board[word * BITS] == 1, name
            assert not free_words(board)[word] & SIGN_BIT, name


def test_the_setup_accumulation_builds_exactly_the_free_words() -> None:
    """``acc = 2 * acc + (1 - value)`` over each group of 64 row-major values.

    This is the setup loop the assembly runs, so it is pinned against the declarative
    packing on every public case and the whole random sweep. The accumulator also stays
    non-negative throughout, because a group's first cell is in column 0 and thus a wall.
    """
    for name in CASES:
        board, *_ = public_case(name)
        assert free_words_by_accumulation(board) == free_words(board), name
    for board, *_ in SWEEP:
        assert free_words_by_accumulation(board) == free_words(board)

    board, *_ = public_case("a cluttered field")
    for word in range(WORDS):
        acc = 0
        for cell in range(word * BITS, (word + 1) * BITS):
            acc = 2 * acc + (1 - board[cell])
            assert 0 <= acc < SIGN_BIT
        assert acc == free_words(board)[word]


def test_free_words_round_trips_through_the_cell_set() -> None:
    board, rx, ry, _ = public_case("rooms and doors")
    words = free_words(board)
    assert words_to_cells(words) == {cell for cell in range(CELLS) if not board[cell]}
    assert is_set(words, cell_of(rx, ry))


# -- the bit-level invariants ---------------------------------------------------------

#: bits whose cell sits in column 0 or column 15 — the only places a 1-bit horizontal
#: shift can fabricate or drop a neighbour relation. Every one of them is a border wall.
#: The pattern repeats in all four words, so one word's worth of bits is enough.
EDGE_COLUMN_BITS = sum(bit_of(cell) for cell in range(BITS) if xy_of(cell)[0] in (0, WIDTH - 1))


def check_step_invariants(step: Step) -> None:
    """Re-derive, from the trace alone, everything the assembly is allowed to assume."""
    for word in step.prev:
        assert not word & SIGN_BIT
    assert not step.avail_before & SIGN_BIT
    assert not step.avail_after & SIGN_BIT

    nsrc, esrc, ssrc, wsrc = step.sources
    # Only the east source can carry a set bit 63: it is ``p << 1``, so its bit 63 is
    # ``p``'s bit 62 = cell ``64w+1`` = row ``4w`` column 1, a genuine interior cell. The
    # other three are right shifts or shift in border-wall bits, so they stay non-negative.
    assert not nsrc & SIGN_BIT and not ssrc & SIGN_BIT and not wsrc & SIGN_BIT
    assert not (esrc & step.avail_before) & SIGN_BIT  # ...and it dies at the mask anyway

    avail = step.avail_before
    union = 0
    for src, got in zip(step.sources, step.claimed, strict=True):
        assert got == src & avail
        assert avail - got == avail & ~got  # the -= is a mask-off
        assert not union & got  # the four directions are pairwise disjoint
        avail -= got
        union |= got
    assert avail == step.avail_after
    assert step.avail_after == step.avail_before & ~union

    # ``prev`` never holds an edge-column cell, so the cheap p<<1 / p>>1 are not merely
    # harmless: they are exactly the geometric masks.
    exact_e, exact_w = horizontal_source_masks(step.prev, step.word)
    assert all(not word & EDGE_COLUMN_BITS for word in step.prev)
    assert esrc == exact_e
    assert wsrc == exact_w


def trace_case(case: Case) -> list[Step]:
    """Run a whole case through :func:`bfs_dirs`, collecting every ``(level, word)`` slice."""
    board, rx, ry, flags = case
    freew = free_words(board)
    robot = cell_of(rx, ry)
    trace: list[Step] = []
    for flag in flags:
        dn, de, ds, dw, _ = bfs_dirs(freew, flag, robot, trace=trace)
        robot = walk_from_dirs(dn, de, ds, dw, robot, flag)[-1]
    return trace


def test_no_path_cell_ever_sits_in_the_edge_columns() -> None:
    """Why the horizontal bleed is harmless: those bits are border walls in every case."""
    for name in CASES:
        board, *_ = public_case(name)
        for word in free_words(board):
            assert not word & EDGE_COLUMN_BITS, name
    for case in SWEEP:
        for word in free_words(case[0]):
            assert not word & EDGE_COLUMN_BITS


@pytest.mark.parametrize("name", list(CASES))
def test_every_bfs_step_of_the_public_cases_keeps_the_invariants(name: str) -> None:
    trace = trace_case(public_case(name))
    assert trace
    for step in trace:
        check_step_invariants(step)


@pytest.mark.parametrize("name", list(CASES))
def test_the_direction_masks_stay_disjoint_and_bit_63_stays_clear(name: str) -> None:
    board, rx, ry, flags = public_case(name)
    freew = free_words(board)
    robot = cell_of(rx, ry)
    for flag in flags:
        masks = bfs_dirs(freew, flag, robot)[:4]
        for word in range(WORDS):
            seen = 0
            for mask in masks:
                assert not mask[word] & SIGN_BIT
                assert not mask[word] & seen
                seen |= mask[word]
            # only free cells ever get a direction, and never the flag itself
            assert not seen & ~freew[word]
        flagged = masks[0][word_of(flag)] | masks[1][word_of(flag)]
        flagged |= masks[2][word_of(flag)] | masks[3][word_of(flag)]
        assert not flagged & bit_of(flag)
        robot = walk_from_dirs(*masks, robot, flag)[-1]


def test_the_cheap_horizontal_shifts_are_exactly_the_geometric_masks() -> None:
    """A row-major word ignores row boundaries; only edge-column cells notice.

    ``Wsrc = p >> 1`` says "the cell one index earlier is in ``prev``", which is the real
    west neighbour unless the target is in column 0 — and then it needs ``prev`` to hold a
    column-15 cell to say anything at all. Column 15 is border wall, so it never does. The
    two boards below show the fabrication when the rule is broken by hand, and that the
    fabricated bit is always an edge-column cell.
    """
    prev = [0] * WORDS
    prev[0] = bit_of(31)  # cell 31 = 15,1 — a border wall, so this never really happens
    _, wsrc = horizontal_source_masks(prev, 0)
    assert wsrc == 0  # cell 32 = 0,2 has no west neighbour at all
    assert shr64(prev[0], 1) == bit_of(32)  # ...but the shift claims cell 31 is one
    assert bit_of(32) & EDGE_COLUMN_BITS

    prev = [0] * WORDS
    prev[0] = bit_of(32)  # cell 32 = 0,2 — also a border wall
    esrc, _ = horizontal_source_masks(prev, 0)
    assert esrc == 0  # cell 31 = 15,1 has no east neighbour at all
    assert shl64(prev[0], 1) == bit_of(31)
    assert bit_of(31) & EDGE_COLUMN_BITS

    # the truncation at the word edges only ever drops an edge-column cell's relation
    prev = [0] * WORDS
    prev[1] = bit_of(64)  # cell 64 = 0,4, in bit 63 of word 1
    assert shl64(prev[1], 1) == 0  # cell 63 = 15,3 would be its west neighbour: dropped
    assert horizontal_source_masks(prev, 0) == (0, 0)  # and geometry agrees it is not one


def test_no_public_or_random_prev_set_ever_holds_an_edge_column_cell() -> None:
    """The precondition that makes the shifts exact, checked rather than assumed."""
    for name in list(CASES)[:3]:
        for step in trace_case(public_case(name)):
            assert all(not word & EDGE_COLUMN_BITS for word in step.prev), name
    for case in SWEEP[:8]:
        for step in trace_case(case):
            assert all(not word & EDGE_COLUMN_BITS for word in step.prev)


def test_bfs_dirs_rejects_an_unreachable_flag() -> None:
    board = open_room([(x, 8) for x in range(1, WIDTH - 1)])
    robot, goal = cell_of(7, 7), cell_of(7, 9)
    with pytest.raises(ValueError):
        bfs_dirs(free_words(board), goal, robot)
    with pytest.raises(ValueError):
        shortest_path(board, robot, goal)


# -- the random sweep -----------------------------------------------------------------

SWEEP_SEED = 20260725
SWEEP_SIZE = 300


def walled_board() -> list[int]:
    return [1] * CELLS


def largest_component(board: Sequence[int]) -> set[int]:
    seen: set[int] = set()
    best: set[int] = set()
    for start in range(CELLS):
        if board[start] or start in seen:
            continue
        stack, component = [start], set()
        seen.add(start)
        while stack:
            cell = stack.pop()
            component.add(cell)
            for neighbour in (cell - WIDTH, cell + 1, cell + WIDTH, cell - 1):
                if not board[neighbour] and neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        if len(component) > len(best):
            best = component
    return best


def random_open_board(rng: random.Random) -> list[int]:
    """Random interior walls, then everything outside the largest component walled off.

    Guarantees a connected path region, so every flag is reachable from every robot.
    """
    while True:
        density = rng.uniform(0.05, 0.45)
        board = walled_board()
        for y in range(1, HEIGHT - 1):
            for x in range(1, WIDTH - 1):
                if rng.random() >= density:
                    board[cell_of(x, y)] = 0
        component = largest_component(board)
        if len(component) < 6:
            continue
        return [0 if cell in component else 1 for cell in range(CELLS)]


def random_maze_board(rng: random.Random) -> list[int]:
    """A recursive-backtracker maze on the odd cells of ``1..13`` — connected, and long."""
    board = walled_board()
    start = (1, 1)
    board[cell_of(*start)] = 0
    stack = [start]
    while stack:
        x, y = stack[-1]
        options = [
            (nx, ny)
            for nx, ny in ((x, y - 2), (x + 2, y), (x, y + 2), (x - 2, y))
            if 1 <= nx <= 13 and 1 <= ny <= 13 and board[cell_of(nx, ny)]
        ]
        if not options:
            stack.pop()
            continue
        nx, ny = rng.choice(options)
        board[cell_of((x + nx) // 2, (y + ny) // 2)] = 0
        board[cell_of(nx, ny)] = 0
        stack.append((nx, ny))
    if rng.random() < 0.5:  # knock a few walls out for genuine ties
        for _ in range(rng.randint(1, 12)):
            board[cell_of(rng.randint(1, 13), rng.randint(1, 13))] = 0
    return board


def random_case(rng: random.Random) -> Case:
    board = random_open_board(rng) if rng.random() < 0.5 else random_maze_board(rng)
    cells = [cell for cell in range(CELLS) if not board[cell]]
    robot = rng.choice(cells)
    start = robot
    flags: list[int] = []
    for _ in range(rng.randint(1, 6)):
        choices = [cell for cell in cells if cell != robot]
        flag = rng.choice(choices)
        flags.append(flag)
        robot = flag
    rx, ry = xy_of(start)
    return board, rx, ry, flags


def build_sweep(size: int = SWEEP_SIZE) -> list[Case]:
    rng = random.Random(SWEEP_SEED)
    return [random_case(rng) for _ in range(size)]


SWEEP = build_sweep()


def test_the_sweep_is_varied_enough_to_be_worth_running() -> None:
    assert len(SWEEP) == SWEEP_SIZE
    rounds = [len(flags) for *_, flags in SWEEP]
    assert min(rounds) == 1
    assert max(rounds) == 6
    assert sum(1 for count in rounds if count >= 2) > SWEEP_SIZE // 2
    longest = 0
    for board, rx, ry, flags in SWEEP:
        robot = cell_of(rx, ry)
        for flag in flags:
            longest = max(longest, len(shortest_path(board, robot, flag)))
            robot = flag
    assert longest >= 40, longest


def test_both_models_agree_on_every_board_of_the_random_sweep() -> None:
    for index, (board, rx, ry, flags) in enumerate(SWEEP):
        assert solve_case_bitset(board, rx, ry, flags) == solve_case(board, rx, ry, flags), index


def test_the_frame_count_is_one_plus_the_moves_on_the_random_sweep() -> None:
    for board, rx, ry, flags in SWEEP:
        robot = cell_of(rx, ry)
        moves = 0
        for flag in flags:
            moves += len(shortest_path(board, robot, flag))
            robot = flag
        assert len(solve_case_bitset(board, rx, ry, flags)) == 1 + moves


def test_the_level_count_is_the_shortest_path_length_everywhere() -> None:
    for name in CASES:
        board, rx, ry, flags = public_case(name)
        robot = cell_of(rx, ry)
        for flag in flags:
            levels = bfs_dirs(free_words(board), flag, robot)[4]
            assert levels == len(shortest_path(board, robot, flag)), name
            robot = flag
    for board, rx, ry, flags in SWEEP:
        freew = free_words(board)
        robot = cell_of(rx, ry)
        for flag in flags:
            levels = bfs_dirs(freew, flag, robot)[4]
            assert levels == len(shortest_path(board, robot, flag))
            robot = flag


def test_every_bfs_step_of_the_random_sweep_keeps_the_invariants() -> None:
    """The full per-step check, including the exact horizontal masks, on a slice of boards.

    ``bfs_dirs`` asserts the cheap half of this on *every* case it is ever run with — this
    test re-derives all of it from the trace, independently of the code under test.
    """
    for case in SWEEP[:40]:
        for step in trace_case(case):
            check_step_invariants(step)


def test_the_cheap_invariants_hold_on_the_whole_sweep() -> None:
    for board, rx, ry, flags in SWEEP:
        freew = free_words(board)
        robot = cell_of(rx, ry)
        for flag in flags:
            masks = bfs_dirs(freew, flag, robot)[:4]
            for word in range(WORDS):
                seen = 0
                for mask in masks:
                    assert not mask[word] & SIGN_BIT
                    assert not mask[word] & seen
                    seen |= mask[word]
                assert not seen & ~freew[word]
            robot = walk_from_dirs(*masks, robot, flag)[-1]


def test_left_never_wins_a_tie_anywhere_in_the_sweep() -> None:
    """The asymmetry that makes a "tie resolved to left" board impossible to build."""
    lefts = 0
    for board, rx, ry, flags in SWEEP[:40]:
        robot = cell_of(rx, ry)
        for flag in flags:
            cell = robot
            for step in shortest_path(board, robot, flag):
                chosen = DIRECTION_NAMES[DELTAS.index(step - cell)]
                options = decreasing_directions(board, cell, flag)
                assert chosen == options[0]
                if chosen == "left":
                    lefts += 1
                    assert options == ["left"]
                cell = step
            robot = flag
    assert lefts > 0


# -- the measured envelope ------------------------------------------------------------

#: ``(rounds, frames, total levels, max levels in one round)`` per public case.
#: ``rounds`` counts the setup round; ``frames`` is ``1 + total levels``.
EXPECTED_STATS = {
    "a straight shot": (4, 23, 22, 12),
    "around the pillars": (4, 37, 36, 17),
    "the long way": (3, 90, 89, 49),
    "rooms and doors": (5, 57, 56, 17),
    "a cluttered field": (5, 45, 44, 13),
    "running errands": (7, 57, 56, 13),
    "there and back again": (3, 78, 77, 42),
}


@pytest.mark.parametrize("name", list(CASES))
def test_the_measured_statistics(name: str) -> None:
    stats = case_stats(*public_case(name))
    assert stats[:4] == EXPECTED_STATS[name]
    assert stats.frames == 1 + stats.total_levels
    assert stats.frames == sum(len(r["frames"]) for r in CASES[name]["rounds"])
    assert stats.rounds == len(CASES[name]["rounds"])


def test_the_sizing_envelope() -> None:
    assert set(EXPECTED_STATS) == set(CASES)
    assert max(frames for _, frames, *_ in EXPECTED_STATS.values()) == 90
    assert max(total for *_, total, _ in EXPECTED_STATS.values()) == 89
    assert max(peak for *_, peak in EXPECTED_STATS.values()) == 49
    # the problem promises at most 64 moves per round, and 49 is the public worst
    assert all(peak <= 64 for *_, peak in EXPECTED_STATS.values())


def test_prev_rarely_spans_all_four_words() -> None:
    """Whether skipping empty words is worth branching on: pooled, 1.78 of the 4 are live.

    Per-case means run 1.36 - 2.73, and only 5.5% of the 380 public levels light up all
    four words — so a "skip the empty words" branch would do 44.5% of the flat word-slices.
    """
    means = []
    histogram = dict.fromkeys(range(1, WORDS + 1), 0)
    for name in CASES:
        board, rx, ry, flags = public_case(name)
        means.append(case_stats(board, rx, ry, flags).prev_words_mean)
        for step in trace_case((board, rx, ry, flags)):
            if step.word == 0:
                histogram[sum(1 for word in step.prev if word)] += 1
    assert min(means) > 1.0  # a level always touches at least one word
    assert round(min(means), 3) == 1.364
    assert round(max(means), 3) == 2.727
    levels = sum(histogram.values())
    assert levels == sum(stats[2] for stats in EXPECTED_STATS.values()) == 380
    assert histogram == {1: 167, 2: 151, 3: 41, 4: 21}
    pooled = sum(count * hits for count, hits in histogram.items()) / levels
    assert round(pooled, 3) == 1.779
    assert round(pooled / WORDS, 3) == 0.445


def test_the_random_sweep_is_harsher_than_the_public_cases() -> None:
    """The sweep's worst case, pinned. Note the mazes beat the problem's own promise:

    a single round can want 73 moves here, while the judge guarantees at most 64 — so the
    sweep is a strictly harder stress test than anything the real input can be.
    """
    worst_total = worst_round = 0
    worst_prev_words = 0
    for case in SWEEP:
        stats = case_stats(*case)
        assert isinstance(stats, CaseStats)
        worst_total = max(worst_total, stats.total_levels)
        worst_round = max(worst_round, stats.max_levels)
        worst_prev_words = max(worst_prev_words, stats.prev_words_max)
    assert (worst_total, worst_round) == (194, 73)
    assert worst_round > 64
    assert worst_prev_words == WORDS
