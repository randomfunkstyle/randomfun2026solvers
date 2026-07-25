"""Pure-Python reference interpreter for the ICFP 2026 ``little-little-little-man``
(LLLM) problem.

The task: read an LLLM program (a single rectangular room containing a single
``@``), simulate the little man tick by tick, and render the 16x16 display after
each round.

Semantics (from the problem statement)
--------------------------------------
* Operations: ``^ > v <`` set heading N/E/S/W, ``0``-``9`` sets ``A = n``,
  ``M`` does ``B = A``, ``+`` does ``A = A + B``, ``-`` does ``A = A - B``,
  ``X`` turns clockwise when ``A > 0`` / counter-clockwise when ``A < 0`` /
  not at all when ``A == 0``, and ``H`` halts.
* Each tick: execute the op under the man, *then* advance one cell along the
  current heading.
* The man starts on the ``@`` heading **East**.  The ``@`` cell itself is
  ordinary empty space (colour 0) and does nothing when walked over.
* Hitting a wall halts him: he *moves onto* the wall cell and stays there
  forever, drawn on top of the wall.

Wall vs. operator disambiguation for ``+`` / ``-``
-------------------------------------------------
``+`` and ``-`` are both room-border glyphs and arithmetic operations.  The
disambiguation is purely positional: we locate the room rectangle and treat a
``+``/``-`` as a wall **iff it lies on that rectangle's perimeter**.  The
rectangle is found from the ``|`` glyphs, which are unambiguous (``|`` is never
an operation): the left/right wall columns are the min/max column holding a
``|``, and the top/bottom wall rows are one above/below the min/max row holding
a ``|``.  Every LLLM room is at least 4x4, so at least two rows contain ``|``
and the rectangle is always well defined.  Anything off the perimeter (and any
``+``/``-`` inside the room) is an arithmetic operation.

Palette
-------
walls 4, ``< > ^ v X H`` 3, digits 8, ``M`` 12 (``c``), ``+``/``-`` as
operations 10 (``a``), space (and ``@``, and everything outside the program) 0,
the little man 9.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

__all__ = [
    "DISPLAY_W",
    "DISPLAY_H",
    "Program",
    "LLLMState",
    "parse_program",
    "classify",
    "render",
    "run_case",
    "run_rounds_from_inputs",
]

DISPLAY_W = 16
DISPLAY_H = 16

# Headings, index 0..3 == N, E, S, W (clockwise order, so ``X`` is +-1 mod 4).
NORTH, EAST, SOUTH, WEST = 0, 1, 2, 3
_DELTA = {
    NORTH: (0, -1),
    EAST: (1, 0),
    SOUTH: (0, 1),
    WEST: (-1, 0),
}
_HEADING_OF_CHAR = {"^": NORTH, ">": EAST, "v": SOUTH, "<": WEST}

# Opcode kinds returned by :func:`classify`.
KIND_WALL = "wall"
KIND_HEADING = "heading"
KIND_DIGIT = "digit"
KIND_MOVE = "move"  # ``M``
KIND_ARITH = "arith"  # ``+`` / ``-`` used as operations
KIND_TURN = "turn"  # ``X``
KIND_HALT = "halt"  # ``H``
KIND_EMPTY = "empty"  # space, ``@``, anything outside the program

COLOUR_WALL = 4
COLOUR_HEADING = 3
COLOUR_DIGIT = 8
COLOUR_M = 12
COLOUR_ARITH = 10
COLOUR_EMPTY = 0
COLOUR_MAN = 9


@dataclass(frozen=True)
class Program:
    """A parsed LLLM program: the raw character grid plus the room rectangle."""

    width: int
    height: int
    rows: tuple[str, ...]
    #: inclusive perimeter of the room: (x0, y0, x1, y1)
    room: tuple[int, int, int, int]
    #: starting position of the little man (the ``@`` cell)
    start: tuple[int, int]

    def char_at(self, x: int, y: int) -> str:
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.rows[y][x]
        return " "

    def is_wall(self, x: int, y: int) -> bool:
        """True iff (x, y) sits on the room's border rectangle."""
        x0, y0, x1, y1 = self.room
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        return x in (x0, x1) or y in (y0, y1)


def parse_program(codes: Sequence[int] | str, w: int, h: int) -> Program:
    """Build a :class:`Program` from ``W*H`` ASCII codes in row-major order.

    ``codes`` may also be given as a plain string of ``w * h`` characters.
    """
    if isinstance(codes, str):
        chars = list(codes)
    else:
        chars = [chr(int(c)) for c in codes]
    if len(chars) != w * h:
        raise ValueError(f"expected {w * h} cells, got {len(chars)}")
    rows = tuple("".join(chars[y * w : (y + 1) * w]) for y in range(h))

    # Locate the room rectangle from the unambiguous ``|`` glyphs.
    pipe_cols = [x for y in range(h) for x in range(w) if rows[y][x] == "|"]
    pipe_rows = [y for y in range(h) for x in range(w) if rows[y][x] == "|"]
    if not pipe_cols:
        raise ValueError("no room walls (`|`) found in program")
    x0, x1 = min(pipe_cols), max(pipe_cols)
    y0, y1 = min(pipe_rows) - 1, max(pipe_rows) + 1
    if not (0 <= y0 and y1 < h and x0 < x1 and y0 < y1):
        raise ValueError("malformed room rectangle")

    start = None
    for y in range(h):
        for x in range(w):
            if rows[y][x] == "@":
                if start is not None:
                    raise ValueError("multiple `@` in program")
                start = (x, y)
    if start is None:
        raise ValueError("no `@` in program")

    return Program(width=w, height=h, rows=rows, room=(x0, y0, x1, y1), start=start)


def classify(program: Program, x: int, y: int) -> tuple[str, int]:
    """Return ``(opcode_kind, palette_colour)`` for the cell at ``(x, y)``."""
    if program.is_wall(x, y):
        return KIND_WALL, COLOUR_WALL
    ch = program.char_at(x, y)
    if ch in _HEADING_OF_CHAR:
        return KIND_HEADING, COLOUR_HEADING
    if ch == "X":
        return KIND_TURN, COLOUR_HEADING
    if ch == "H":
        return KIND_HALT, COLOUR_HEADING
    if ch.isdigit():
        return KIND_DIGIT, COLOUR_DIGIT
    if ch == "M":
        return KIND_MOVE, COLOUR_M
    if ch in "+-":
        return KIND_ARITH, COLOUR_ARITH
    # space, ``@`` and anything outside the program body
    return KIND_EMPTY, COLOUR_EMPTY


@dataclass
class LLLMState:
    """Mutable interpreter state for one LLLM program."""

    program: Program
    x: int = 0
    y: int = 0
    heading: int = EAST
    a: int = 0
    b: int = 0
    halted: bool = False
    ticks: int = 0
    _palette: list[list[int]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self._palette:
            self.x, self.y = self.program.start
            prog = self.program
            self._palette = [
                [classify(prog, x, y)[1] for x in range(prog.width)]
                for y in range(prog.height)
            ]

    # -- simulation ------------------------------------------------------

    def step(self) -> None:
        """Execute one tick: run the op under the man, then advance."""
        if self.halted:
            return
        kind, _ = classify(self.program, self.x, self.y)
        ch = self.program.char_at(self.x, self.y)

        if kind == KIND_HALT:
            self.halted = True
            return
        if kind == KIND_HEADING:
            self.heading = _HEADING_OF_CHAR[ch]
        elif kind == KIND_DIGIT:
            self.a = int(ch)
        elif kind == KIND_MOVE:
            self.b = self.a
        elif kind == KIND_ARITH:
            self.a = self.a + self.b if ch == "+" else self.a - self.b
        elif kind == KIND_TURN:
            if self.a > 0:
                self.heading = (self.heading + 1) % 4
            elif self.a < 0:
                self.heading = (self.heading - 1) % 4
        # KIND_EMPTY / KIND_WALL: no-op (a halted man never gets here anyway)

        dx, dy = _DELTA[self.heading]
        self.x += dx
        self.y += dy
        self.ticks += 1
        if self.program.is_wall(self.x, self.y):
            # He walks *onto* the wall cell and stays there forever.
            self.halted = True

    def run(self, k: int) -> int:
        """Step up to ``k`` ticks, stopping early on halt.  Returns ticks run."""
        done = 0
        for _ in range(k):
            if self.halted:
                break
            self.step()
            done += 1
        return done


def render(state: LLLMState) -> list[str]:
    """Render the 16x16 display as 16 rows of 16 lowercase hex digits."""
    prog = state.program
    out = []
    for y in range(DISPLAY_H):
        row = []
        for x in range(DISPLAY_W):
            if x == state.x and y == state.y:
                colour = COLOUR_MAN
            elif y < prog.height and x < prog.width:
                colour = state._palette[y][x]
            else:
                colour = COLOUR_EMPTY
            row.append("%x" % colour)
        out.append("".join(row))
    return out


def run_case(rounds: Iterable[Sequence[str]]) -> list[list[list[str]]]:
    """Drive the round protocol.

    ``rounds`` is the per-round input token lists: the first round is
    ``[W, H, *ascii_codes]``, every later round is a single ``[k]``.  Returns one
    entry per round, each a list of frames (always exactly one frame).
    """
    rounds = [list(r) for r in rounds]
    if not rounds:
        return []
    head = [int(t) for t in rounds[0]]
    w, h = head[0], head[1]
    program = parse_program(head[2:], w, h)
    state = LLLMState(program)
    frames = [[render(state)]]
    for rnd in rounds[1:]:
        state.run(int(rnd[0]))
        frames.append([render(state)])
    return frames


def run_rounds_from_inputs(rounds: Iterable[Sequence[str]]) -> list[list[str]]:
    """Like :func:`run_case` but flattened to one frame per round."""
    return [frames[0] for frames in run_case(rounds)]


if __name__ == "__main__":  # pragma: no cover - self-check convenience
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    spec = json.loads(
        (root / "tasks" / "problems" / "little-little-little-man.json").read_text()
    )
    ok = True
    for case in spec["publicTestData"]:
        got = run_case([r["in"] for r in case["rounds"]])
        want = [r["frames"] for r in case["rounds"]]
        status = "ok" if got == want else "FAIL"
        ok &= got == want
        print(f"{status:>4}  {case['name']}")
    print("all pass" if ok else "MISMATCH")
