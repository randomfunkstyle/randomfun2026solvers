"""The pure-Python ``snake`` reference, against every expected frame of the public data.

This is the oracle an ``.asm`` is written against, so the bar is byte-for-byte on *both*
axes: the frames themselves, and *which round commits them* (direction changes commit
nothing, so a simulator that got the count right per case could still be wrong per round).

The measured statistics are pinned too — they are what the hardware is sized for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers.snake_sim import DIRECTIONS, Game, simulate, simulate_game

REPO = Path(__file__).parents[1]


def public_cases() -> list[dict]:
    problem = REPO / "tasks" / "problems" / "snake.json"
    return json.loads(problem.read_text(encoding="utf-8"))["publicTestData"]


CASES = {case["name"]: case for case in public_cases()}


def case_rounds(case: dict) -> list[list[int]]:
    return [[int(v) for v in r["in"]] for r in case["rounds"]]


def expected_frames(case: dict) -> list[list[list[str]]]:
    return [list(r["frames"]) for r in case["rounds"]]


@pytest.mark.parametrize("name", list(CASES))
def test_every_public_frame_matches(name: str) -> None:
    case = CASES[name]
    assert simulate(case_rounds(case)) == expected_frames(case)


@pytest.mark.parametrize("name", list(CASES))
def test_only_direction_changes_commit_nothing(name: str) -> None:
    """The per-round commit pattern, read off the data: ``2/3/4/5`` -> 0 frames, else 1."""
    case = CASES[name]
    rounds = case_rounds(case)
    want = [0 if index and values[0] in (2, 3, 4, 5) else 1 for index, values in enumerate(rounds)]
    assert [len(r["frames"]) for r in case["rounds"]] == want
    assert [len(frames) for frames in simulate(rounds)] == want


#: ``(rounds, ticks, growths, final length, died)`` per case — the sizing envelope.
#: ``ticks`` counts the losing tick too; the final length is also the peak length, since
#: the snake only ever grows.
EXPECTED_STATS = {
    "first bites": (13, 8, 2, 3, False),
    "game over at the wall": (5, 4, 0, 1, True),
    "full circle": (23, 11, 4, 5, True),
    "second course": (22, 13, 2, 3, False),
    "the long game": (92, 73, 5, 6, False),
}


@pytest.mark.parametrize("name", list(CASES))
def test_measured_statistics(name: str) -> None:
    case = CASES[name]
    rounds = case_rounds(case)
    game, _ = simulate_game(rounds)
    assert (len(rounds), game.ticks, game.growths, len(game), not game.alive) == EXPECTED_STATS[
        name
    ]


def test_the_sizing_envelope() -> None:
    assert set(EXPECTED_STATS) == set(CASES)
    assert max(rounds for rounds, *_ in EXPECTED_STATS.values()) == 92
    assert max(ticks for _, ticks, *_ in EXPECTED_STATS.values()) == 73
    assert max(length for *_, length, _ in EXPECTED_STATS.values()) == 6


@pytest.mark.parametrize("name", list(CASES))
def test_the_snake_never_shrinks(name: str) -> None:
    game = Game()
    lengths = []
    for values in case_rounds(CASES[name]):
        game.play_round(values)
        lengths.append(len(game))
    assert lengths == sorted(lengths)


def test_round_zero_is_one_green_cell_that_already_moves_right() -> None:
    """Three cases tick before their first turn, so the data fixes the initial direction."""
    ticks_first = ["first bites", "game over at the wall", "full circle"]
    for name in ticks_first:
        case = CASES[name]
        sx, sy = case_rounds(case)[0]
        start = case["rounds"][0]["frames"][0]
        assert start[sy] == "0" * sx + "a" + "0" * (15 - sx)
        assert sum(row.count("a") + row.count("9") for row in start) == 1
        after = case["rounds"][1]["frames"][0]
        assert after[sy][sx + 1] in "a9"  # the head moved +x with no direction round


def test_a_lost_game_commits_its_frame_and_repaints_the_snake_red() -> None:
    """``game over at the wall``: 12,3 walks right to 15,3, then the 4th tick hits the wall."""
    case = CASES["game over at the wall"]
    frames = simulate(case_rounds(case))
    assert [len(f) for f in frames] == [1, 1, 1, 1, 1]
    alive, dead = frames[-2][0], frames[-1][0]
    assert alive[3] == "0" * 15 + "a"
    assert dead[3] == "0" * 15 + "9"
    assert alive[:3] == dead[:3] and alive[4:] == dead[4:]


def test_the_snake_does_not_move_on_the_losing_tick() -> None:
    game = Game()
    game.start(14, 0)
    game.tick()
    before = list(game.body)
    game.tick()
    assert not game.alive
    assert list(game.body) == before == [(15, 0)]


def test_stepping_into_the_cell_the_tail_just_vacated_is_legal() -> None:
    """``full circle`` coils a 4-long snake, so the real data pins the tail-first order.

    A head-first implementation would call these ticks a loss and paint the snake red;
    the expected frames say green, so the order is proved, not assumed.
    """
    case = CASES["full circle"]
    game = Game()
    onto_the_old_tail = []
    for values in case_rounds(case):
        target = None
        if game.started and values[0] == 0:
            (hx, hy), (dx, dy) = game.head, game.direction
            target = (hx + dx, hy + dy)
            tail = game.body[-1]
        game.play_round(values)
        if target is not None and target == tail and target != game.body[-1]:
            onto_the_old_tail.append(target)
            assert game.alive
            assert game.head == target
    assert onto_the_old_tail == [(5, 5), (6, 5)]


def test_the_loss_at_the_wall_is_off_grid_and_the_loss_in_the_coil_is_self_contact() -> None:
    """The two public deaths cover both halves of the rule."""
    wall, _ = simulate_game(case_rounds(CASES["game over at the wall"]))
    assert not wall.alive
    assert wall.head == (15, 3)
    assert wall.direction == (1, 0)  # next would be x=16: off the grid

    coil, _ = simulate_game(case_rounds(CASES["full circle"]))
    assert not coil.alive
    assert coil.head == (6, 6)
    assert coil.direction == (0, -1)
    assert (6, 5) in coil.body  # on the grid, but occupied by a cell that is not the tail
    assert coil.body[-1] == (5, 5)


def test_the_input_guarantees_hold_in_the_public_data() -> None:
    """What the hardware may assume — and the one thing it may *not*.

    No reversals and at most one turn between ticks, as promised. But a turn is allowed to
    repeat the current direction (``first bites`` round 3 says ``right`` while already
    going right), so a turn is not necessarily a change.
    """
    reversals = redundant = crowded = fruit_on_fruit = 0
    for case in CASES.values():
        game = Game()
        turns_since_tick = 0
        for index, values in enumerate(case_rounds(case)):
            if index:
                if values[0] == 0:
                    turns_since_tick = 0
                elif values[0] == 1:
                    fruit_on_fruit += game.fruit is not None
                else:
                    dx, dy = DIRECTIONS[values[0]]
                    cx, cy = game.direction
                    reversals += (dx, dy) == (-cx, -cy)
                    redundant += (dx, dy) == (cx, cy)
                    turns_since_tick += 1
                    crowded += turns_since_tick > 1
            game.play_round(values)
    assert (reversals, crowded, fruit_on_fruit) == (0, 0, 0)
    assert redundant == 1


def test_growing_keeps_the_tail_and_clears_the_fruit() -> None:
    game = Game()
    game.start(0, 0)
    game.spawn_fruit(1, 0)
    assert len(game) == 1
    game.tick()
    assert list(game.body) == [(1, 0), (0, 0)]
    assert game.fruit is None
    assert game.growths == 1


def test_no_public_case_has_a_round_after_the_loss() -> None:
    """The description says the case ends at the loss; the data agrees."""
    for name, case in CASES.items():
        rounds = case_rounds(case)
        game = Game()
        for index, values in enumerate(rounds):
            game.play_round(values)
            assert game.alive or index == len(rounds) - 1, name


def test_the_board_is_empty_of_fruit_whenever_a_case_ends_in_a_loss() -> None:
    """So no public frame ever shows a red fruit *and* a red dead snake at once."""
    for name, case in CASES.items():
        game, _ = simulate_game(case_rounds(case))
        if not game.alive:
            assert game.fruit is None, name


def test_a_fruit_never_lands_on_the_snake() -> None:
    """Guaranteed by the problem, so a cell is never both fruit and snake."""
    for name, case in CASES.items():
        game = Game()
        for values in case_rounds(case):
            game.play_round(values)  # spawn_fruit raises if the cell is occupied
        assert game is not None, name
