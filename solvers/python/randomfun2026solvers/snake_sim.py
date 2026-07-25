"""Reference simulator for the ``snake`` problem — the semantics an ``.asm`` must match.

The problem is round-based and display-scored: the judge compares the frames a round
*commits* against ``publicTestData``. Everything below is pinned by
``tasks/problems/snake.json`` (``description``) and verified frame-for-frame against all
five public cases by ``tests/test_snake_sim.py``.

The grid is 16x16, ``0,0`` top-left, ``x`` right, ``y`` down. Rounds:

* **round 0** — ``sx sy``: the snake starts as the single cell ``sx,sy`` moving *right*.
  Commits one frame.
* ``1 fx fy`` — **fruit spawn**. No tick. Commits one frame. At most one fruit is on the
  board at a time and it always appears in an empty cell, so a fruit cell is never also a
  snake cell (:meth:`Game.spawn_fruit` rejects it rather than picking a colour).
* ``2 3 4 5`` — **direction change** to up/right/down/left, effective from the next tick.
  No tick, and — the one round kind that draws nothing — **commits no frame**.
* ``0`` — **tick**. Commits one frame.

A tick, in exactly this order (:meth:`Game.tick`):

1. ``next = head + direction``.
2. If ``next`` is the fruit: **grow** — push ``next`` as the new head, keep the tail,
   clear the fruit. No bounds/self check is needed (the fruit is on the grid, in an empty
   cell), and the length goes up by one.
3. Otherwise pop the tail **first**: the tail moves before the head, so stepping into the
   cell the tail has just vacated is legal.
4. Then validate ``next``: off the grid, or still occupied by the shortened body -> the
   player **loses**. The snake does not move (the popped tail is put back) and the frame
   shows the pre-tick shape. A losing round *does* still commit its frame — that frame is
   the last one of the case.
5. Otherwise push ``next`` as the new head.

Colours: the snake is green (10, drawn ``a``) while the game is on and red (9) once it has
ended; fruit is red (9); everything else black (0). Note a lost game repaints the *whole*
snake red in that same frame.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

__all__ = [
    "BLACK",
    "DIRECTIONS",
    "GREEN",
    "HEIGHT",
    "RED",
    "RIGHT",
    "WIDTH",
    "Cell",
    "Frame",
    "Game",
    "simulate",
    "simulate_game",
]

WIDTH = 16
HEIGHT = 16

BLACK = "0"
RED = "9"
GREEN = "a"

UP = 2
RIGHT = 3
DOWN = 4
LEFT = 5

#: round opcode -> ``(dx, dy)``. ``y`` grows *down*, so ``up`` is ``-1``.
DIRECTIONS: dict[int, tuple[int, int]] = {
    UP: (0, -1),
    RIGHT: (1, 0),
    DOWN: (0, 1),
    LEFT: (-1, 0),
}

FRUIT = 1
TICK = 0

Cell = tuple[int, int]
#: One committed frame: ``HEIGHT`` rows of ``WIDTH`` hex digits, one digit per pixel.
Frame = list[str]


def on_grid(cell: Cell) -> bool:
    """Is ``cell`` inside the 16x16 display?"""
    x, y = cell
    return 0 <= x < WIDTH and 0 <= y < HEIGHT


class Game:
    """The whole game state, with one method per round kind.

    ``body[0]`` is the head and ``body[-1]`` the tail, so a tick is
    ``pop()`` then ``appendleft()`` — the order an ``.asm`` has to follow.
    """

    def __init__(self) -> None:
        self.body: deque[Cell] = deque()
        self.direction: tuple[int, int] = DIRECTIONS[RIGHT]
        self.fruit: Cell | None = None
        self.alive = True
        self.started = False
        #: ticks attempted, including the losing one
        self.ticks = 0
        #: ticks that ate a fruit
        self.growths = 0

    @property
    def head(self) -> Cell:
        return self.body[0]

    def __len__(self) -> int:
        return len(self.body)

    # -- round kinds ------------------------------------------------------------------

    def start(self, sx: int, sy: int) -> Frame:
        """Round 0, ``sx sy``: one cell, moving right. Commits one frame."""
        self.body = deque([(sx, sy)])
        self.direction = DIRECTIONS[RIGHT]
        self.started = True
        return self.render()

    def spawn_fruit(self, fx: int, fy: int) -> Frame:
        """``1 fx fy``: put a fruit down. No tick. Commits one frame."""
        cell = (fx, fy)
        if cell in self.body:
            raise ValueError(f"fruit {cell} spawned on the snake; the problem forbids it")
        self.fruit = cell
        return self.render()

    def turn(self, code: int) -> None:
        """``2/3/4/5``: aim the snake from the next tick on. Commits nothing."""
        self.direction = DIRECTIONS[code]

    def tick(self) -> Frame:
        """``0``: advance one tick. Commits one frame (even the losing one)."""
        if not self.alive:
            # The case ends at the loss, so the public data never gets here.
            return self.render()
        self.ticks += 1
        hx, hy = self.body[0]
        dx, dy = self.direction
        nxt = (hx + dx, hy + dy)

        if nxt == self.fruit:
            # Grow: the tail stays put and the fruit is gone. A fruit cell is on the grid
            # and empty by construction, so there is nothing to validate.
            self.body.appendleft(nxt)
            self.fruit = None
            self.growths += 1
            return self.render()

        tail = self.body.pop()  # the tail moves *before* the head
        if not on_grid(nxt) or nxt in self.body:
            self.body.append(tail)  # the snake does not move
            self.alive = False
            return self.render()
        self.body.appendleft(nxt)
        return self.render()

    # -- dispatch and drawing ---------------------------------------------------------

    def play_round(self, values: Sequence[int]) -> list[Frame]:
        """Run one round's input ints; return the frames it commits (0 or 1)."""
        if not self.started:
            sx, sy = values
            return [self.start(sx, sy)]
        opcode = values[0]
        if opcode == TICK:
            return [self.tick()]
        if opcode == FRUIT:
            _, fx, fy = values
            return [self.spawn_fruit(fx, fy)]
        if opcode in DIRECTIONS:
            self.turn(opcode)
            return []
        raise ValueError(f"unknown round {list(values)!r}")

    def render(self) -> Frame:
        """Draw the board: fruit red, snake green while alive and red once it has lost."""
        grid = [[BLACK] * WIDTH for _ in range(HEIGHT)]
        if self.fruit is not None:
            fx, fy = self.fruit
            grid[fy][fx] = RED
        colour = GREEN if self.alive else RED
        for x, y in self.body:
            grid[y][x] = colour
        return ["".join(row) for row in grid]


def simulate_game(rounds: Sequence[Sequence[int]]) -> tuple[Game, list[list[Frame]]]:
    """:func:`simulate`, but also hand back the final :class:`Game` (for statistics)."""
    game = Game()
    return game, [game.play_round(values) for values in rounds]


def simulate(rounds: Sequence[Sequence[int]]) -> list[list[Frame]]:
    """Per round, the frames that round commits — exactly the judge's ``frames``."""
    return simulate_game(rounds)[1]
