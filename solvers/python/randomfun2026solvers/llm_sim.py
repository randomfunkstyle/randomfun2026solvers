"""Pure-Python reference interpreter for the ICFP 2026 ``little-little-man`` (LLM)
problem.

The task: read an LLM program (1..3 rooms, up to 2 pipes, exactly one ``@`` per
room), simulate every little man tick by tick, and render the 16x16 display after
each round.  This is the multi-room, piped big brother of
:mod:`randomfun2026solvers.lllm_sim`, and it keeps that module's shape on purpose
(``parse_program`` / ``classify`` / ``render`` / ``run_case``) so the same
machine-side generators can drive either one.

Semantics (from the problem statement, cross-checked glyph by glyph against
``littleman/SPEC.md`` and, tick by tick, against the bundled reference engine
``littleman/lm.mjs`` + ``littleman.wasm``)
--------------------------------------------------------------------------------
Every tick runs in three phases, in this order:

1. **Pipes shift.**  Each pipe is a FIFO of ``len(cells)`` slots.  Every value
   advances one cell toward the destination if the next cell is free.  The shift
   is resolved **from the destination end backwards** (slot ``n-1`` first, slot
   ``0`` last), so a solid train of adjacent values advances *together* in a
   single tick — only a train whose front is genuinely stuck stays put.  The
   public cases cannot tell this apart from a simultaneous "front value only"
   shift, so it was settled directly against the reference engine: send the same
   value on two consecutive ticks into a 4-cell pipe and after five ticks the
   engine reports the two values at slots 1 and 2, not 0 and 2.
2. **Execution.**  Every non-halted man executes the glyph he is standing on.
   ``s`` on a full source cell and ``r`` on an empty destination cell **block**:
   the man does nothing, does not move, and retries the same cell next tick.
   Because phase 1 already ran, a value that lands in the last cell this tick is
   receivable by an ``r`` on the *same* tick.  The phase is order-independent: a
   pipe has one source room and one destination room, each room holds one man, and
   a pipe is at least two cells long, so ``s`` (slot 0) and ``r`` (last slot) can
   never contend.
3. **Movement.**  Every man who is neither halted nor blocked advances one cell
   along his heading.

Operations: ``^ > v <`` set heading N/E/S/W, ``0``-``9`` sets ``A = n``, ``M``
does ``B = A``, ``+`` does ``A = A + B``, ``-`` does ``A = A - B``, ``X`` turns
clockwise when ``A > 0`` / counter-clockwise when ``A < 0`` / not at all when
``A == 0``, ``s`` sends ``A`` into the nearest outgoing pipe, ``r`` receives into
``A`` from the nearest incoming pipe, and ``H`` halts that one man.

Every man starts on his room's ``@`` heading **East**.  The ``@`` cell is
ordinary empty space (colour 0) and does nothing when walked over.

Halting
-------
The whole program freezes **the moment any man steps onto a wall** — but the tick
completes in full first: every other man still executes and moves, and the man on
the wall is drawn *on* the wall cell.  Failing that, the program is done once
every man has halted on an ``H``, and nothing moves after that (there is no output
room in an LLM program, so there is nothing left to drain).  Note that a frozen
program can leave a man standing on an ``H`` he never got to execute, and a man
still blocked on an ``s``/``r`` — both happen in the public data.

Pipe / room / wall disambiguation
---------------------------------
Which glyph means what is entirely positional, and rooms are found first:

* A **room** is a rectangle with ``+`` at all four corners, ``-`` along the whole
  top and bottom edge and ``|`` down the whole left and right edge.  Candidates
  are enumerated from every ``+``, validated edge by edge, then scanned in
  ``(y0, x0, y1, x1)`` order with anything overlapping an already-accepted room
  dropped.  This is exactly what the reference engine does, and it is why an
  arithmetic ``+`` inside a room can never masquerade as a corner.
* Every cell **on** an accepted room's perimeter is a wall (colour 4) — so a
  border ``+``/``-``/``|`` is a wall no matter what else it could mean.
* Every cell **strictly inside** a room is an operation: ``v`` is a heading,
  ``+``/``-`` are arithmetic, ``|`` would be a bitwise OR (not in the LLM subset).
* Every cell **outside** every room can only be pipe.  Pipes are traced from the
  rooms outward: for each non-corner border cell, if the cell just outside holds
  an arrowhead pointing *away* from the room, that is a pipe source.  Walk
  forward; ``-``/``|`` are straight body glyphs, an arrowhead re-aims the flow,
  and the pipe ends at the first cell whose forward neighbour is another room's
  border.  Every cell of that walk is a pipe cell (colour 6, or 14 while it holds
  a value).  ``V`` is *not* a structural arrowhead (the engine rejects a pipe drawn
  with it), even though it is a valid southward heading inside a room.
* Anything else outside a room is inert space (colour 0).  No public case has a
  non-space glyph there, so this is the one colour choice the data does not pin.

Nearest pipe for ``s`` / ``r``
------------------------------
The candidate set is the room's outgoing pipes for ``s`` and its incoming pipes
for ``r``.  The winner is the pipe whose **arrowhead at this room** (first cell
for outgoing, last cell for incoming) has the smallest Manhattan distance to the
man's cell, ties broken by that arrowhead's reading order (top-to-bottom, then
left-to-right).  Binding is static: it is *not* nearest-that-can-proceed, so a
man can block on a busy pipe while an idle one sits further away.

(The reference engine measures to the *wall* cell the pipe attaches to rather
than to the arrowhead one step outside it.  Since the man is always strictly
inside the room and the arrowhead is always one step further out, every distance
is exactly one larger and the tie-break order is unchanged — the two rules pick
the same pipe.)

Palette
-------
walls 4, ``< > ^ v X H`` 3, digits 8, ``M`` 12, ``+``/``-`` as operations 10,
``s``/``r`` 13, empty pipe cell 6, pipe cell holding a value 14, space (and
``@``, and everything outside the program) 0, a little man 9 drawn on top of
whatever he stands on.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "DISPLAY_W",
    "DISPLAY_H",
    "NORTH",
    "EAST",
    "SOUTH",
    "WEST",
    "HEADING_NAMES",
    "KIND_WALL",
    "KIND_HEADING",
    "KIND_DIGIT",
    "KIND_MOVE",
    "KIND_ARITH",
    "KIND_TURN",
    "KIND_HALT",
    "KIND_SEND",
    "KIND_RECV",
    "KIND_PIPE",
    "KIND_EMPTY",
    "COLOUR_WALL",
    "COLOUR_HEADING",
    "COLOUR_DIGIT",
    "COLOUR_M",
    "COLOUR_ARITH",
    "COLOUR_PIPE_OP",
    "COLOUR_PIPE",
    "COLOUR_PIPE_FULL",
    "COLOUR_EMPTY",
    "COLOUR_MAN",
    "LLMProgramError",
    "Room",
    "Pipe",
    "Program",
    "Man",
    "LLMState",
    "parse_program",
    "classify",
    "render",
    "run_case",
    "run_rounds_from_inputs",
    "self_check",
]

DISPLAY_W = 16
DISPLAY_H = 16

# Headings, index 0..3 == N, E, S, W (clockwise order, so ``X`` is +-1 mod 4).
NORTH, EAST, SOUTH, WEST = 0, 1, 2, 3
HEADING_NAMES = ("N", "E", "S", "W")
_DELTA = {
    NORTH: (0, -1),
    EAST: (1, 0),
    SOUTH: (0, 1),
    WEST: (-1, 0),
}
_HEADING_OF_CHAR = {"^": NORTH, ">": EAST, "v": SOUTH, "V": SOUTH, "<": WEST}
#: Flow direction of each arrowhead glyph, as ``(dx, dy)``.  ``V`` is deliberately
#: absent: the reference engine takes it as a heading but *not* as a structural
#: arrowhead, so ``V`` outside a room does not start or bend a pipe.
_ARROW_DELTA = {
    "^": (0, -1),
    ">": (1, 0),
    "v": (0, 1),
    "<": (-1, 0),
}

# Opcode kinds returned by :func:`classify`.
KIND_WALL = "wall"
KIND_HEADING = "heading"
KIND_DIGIT = "digit"
KIND_MOVE = "move"  # ``M``
KIND_ARITH = "arith"  # ``+`` / ``-`` used as operations
KIND_TURN = "turn"  # ``X``
KIND_HALT = "halt"  # ``H``
KIND_SEND = "send"  # ``s``
KIND_RECV = "recv"  # ``r``
KIND_PIPE = "pipe"  # a pipe body or arrowhead cell
KIND_EMPTY = "empty"  # space, ``@``, anything outside the program

COLOUR_WALL = 4
COLOUR_HEADING = 3
COLOUR_DIGIT = 8
COLOUR_M = 12
COLOUR_ARITH = 10
COLOUR_PIPE_OP = 13
COLOUR_PIPE = 6
COLOUR_PIPE_FULL = 14
COLOUR_EMPTY = 0
COLOUR_MAN = 9


class LLMProgramError(ValueError):
    """The grid does not parse as an LLM program."""


@dataclass(frozen=True)
class Room:
    """One room: an inclusive wall rectangle plus the ``@`` inside it."""

    id: int
    x0: int
    y0: int
    x1: int
    y1: int
    #: the ``@`` cell, i.e. where this room's man starts
    spawn: tuple[int, int]
    #: pipe ids leaving / entering this room, in arrowhead reading order
    outgoing: tuple[int, ...] = ()
    incoming: tuple[int, ...] = ()

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def contains(self, x: int, y: int) -> bool:
        """True for the walls *and* the interior."""
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def on_border(self, x: int, y: int) -> bool:
        return self.contains(x, y) and (x in (self.x0, self.x1) or y in (self.y0, self.y1))

    def inside(self, x: int, y: int) -> bool:
        return self.x0 < x < self.x1 and self.y0 < y < self.y1


@dataclass(frozen=True)
class Pipe:
    """One one-way pipe, described in **flow order**.

    ``cells[0]`` is the source arrowhead (the cell just outside ``src``'s wall)
    and ``cells[-1]`` is the destination arrowhead (the cell just outside
    ``dst``'s wall, pointing into it).  ``dirs[i]`` is the direction a value
    leaves ``cells[i]`` along, so a bend shows up as ``dirs[i] != dirs[i - 1]``.
    """

    id: int
    cells: tuple[tuple[int, int], ...]
    dirs: tuple[tuple[int, int], ...]
    src: int
    dst: int
    #: the wall cells the two ends attach to
    src_attach: tuple[int, int]
    dst_attach: tuple[int, int]

    @property
    def length(self) -> int:
        return len(self.cells)

    @property
    def source_head(self) -> tuple[int, int]:
        return self.cells[0]

    @property
    def dest_head(self) -> tuple[int, int]:
        return self.cells[-1]


@dataclass(frozen=True)
class Program:
    """A parsed LLM program: the character grid plus all of its structure."""

    width: int
    height: int
    rows: tuple[str, ...]
    rooms: tuple[Room, ...]
    pipes: tuple[Pipe, ...]
    #: per-cell opcode kind, row-major
    kinds: tuple[tuple[str, ...], ...]
    #: per-cell base colour (ignoring men and pipe contents), row-major
    colours: tuple[tuple[int, ...], ...]
    #: ``(x, y) -> room id`` for wall cells and interior cells alike
    room_of: dict[tuple[int, int], int]
    #: ``(x, y) -> (pipe id, index along the flow)``
    pipe_of: dict[tuple[int, int], tuple[int, int]]
    #: ``(x, y) -> pipe id`` for every ``s`` / ``r`` cell (the nearest-pipe binding)
    binding: dict[tuple[int, int], int]

    def char_at(self, x: int, y: int) -> str:
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.rows[y][x]
        return " "

    def kind_at(self, x: int, y: int) -> str:
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.kinds[y][x]
        return KIND_EMPTY

    def colour_at(self, x: int, y: int) -> int:
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.colours[y][x]
        return COLOUR_EMPTY

    def is_wall(self, x: int, y: int) -> bool:
        return self.kind_at(x, y) == KIND_WALL

    @property
    def starts(self) -> tuple[tuple[int, int, int], ...]:
        """``(x, y, room_id)`` per man, in spawn reading order."""
        return tuple(
            (room.spawn[0], room.spawn[1], room.id)
            for room in sorted(self.rooms, key=lambda r: (r.spawn[1], r.spawn[0]))
        )


# ── parsing ──────────────────────────────────────────────────────────────────
def _grid_rows(codes: Sequence[int] | str, w: int, h: int) -> tuple[str, ...]:
    chars = list(codes) if isinstance(codes, str) else [chr(int(c)) for c in codes]
    if len(chars) != w * h:
        raise LLMProgramError(f"expected {w * h} cells, got {len(chars)}")
    return tuple("".join(chars[y * w : (y + 1) * w]) for y in range(h))


def _find_rooms(rows: tuple[str, ...], w: int, h: int) -> list[tuple[int, int, int, int]]:
    """Every ``+``-cornered ``-``/``|`` rectangle, with overlaps dropped."""

    def ch(x: int, y: int) -> str:
        return rows[y][x] if 0 <= y < h and 0 <= x < w else " "

    def is_box(x0: int, y0: int, x1: int, y1: int) -> bool:
        if x1 - x0 < 2 or y1 - y0 < 2:
            return False
        if ch(x1, y0) != "+" or ch(x0, y1) != "+" or ch(x1, y1) != "+":
            return False
        if any(ch(x, y0) != "-" or ch(x, y1) != "-" for x in range(x0 + 1, x1)):
            return False
        return not any(ch(x0, y) != "|" or ch(x1, y) != "|" for y in range(y0 + 1, y1))

    found: list[tuple[int, int, int, int]] = []
    for y0 in range(h):
        for x0 in range(w):
            if ch(x0, y0) != "+":
                continue
            for x1 in range(x0 + 2, w):
                if ch(x1, y0) != "+":
                    continue
                for y1 in range(y0 + 2, h):
                    if is_box(x0, y0, x1, y1):
                        found.append((x0, y0, x1, y1))
                        break

    accepted: list[tuple[int, int, int, int]] = []
    for x0, y0, x1, y1 in sorted(found, key=lambda b: (b[1], b[0], b[3], b[2])):
        if any(not (x1 < a0 or a1 < x0 or y1 < b0 or b1 < y0) for a0, b0, a1, b1 in accepted):
            continue
        accepted.append((x0, y0, x1, y1))
    if not accepted:
        raise LLMProgramError("no rooms found in program")
    return accepted


def _trace_pipes(
    rows: tuple[str, ...],
    w: int,
    h: int,
    rects: list[tuple[int, int, int, int]],
    border_owner: dict[tuple[int, int], int],
) -> list[Pipe]:
    """Follow every arrowhead leaving a room wall to the room it feeds."""

    def ch(x: int, y: int) -> str:
        return rows[y][x] if 0 <= y < h and 0 <= x < w else " "

    starts: list[tuple[int, tuple[int, int], tuple[int, int], tuple[int, int]]] = []
    for rid, (x0, y0, x1, y1) in enumerate(rects):
        sides: list[tuple[tuple[int, int], tuple[int, int]]] = []
        for x in range(x0 + 1, x1):
            sides.append(((x, y0), (0, -1)))
            sides.append(((x, y1), (0, 1)))
        for y in range(y0 + 1, y1):
            sides.append(((x0, y), (-1, 0)))
            sides.append(((x1, y), (1, 0)))
        for attach, direction in sides:
            head = (attach[0] + direction[0], attach[1] + direction[1])
            if _ARROW_DELTA.get(ch(*head)) == direction:
                starts.append((rid, head, direction, attach))

    pipes: list[Pipe] = []
    claimed: dict[tuple[int, int], int] = {}
    for src, head, initial_dir, src_attach in sorted(starts, key=lambda s: (s[1][1], s[1][0])):
        if head in claimed:
            continue
        cells: list[tuple[int, int]] = []
        dirs: list[tuple[int, int]] = []
        pos, direction = head, initial_dir
        while True:
            glyph = ch(*pos)
            arrow = _ARROW_DELTA.get(glyph)
            if arrow is not None:
                if arrow == (-direction[0], -direction[1]):
                    raise LLMProgramError(f"pipe arrow points backward at {pos}")
                direction = arrow
            elif glyph != ("-" if direction[1] == 0 else "|"):
                raise LLMProgramError(f"invalid pipe body {glyph!r} at {pos}")
            cells.append(pos)
            dirs.append(direction)
            forward = (pos[0] + direction[0], pos[1] + direction[1])
            dst = border_owner.get(forward)
            if dst is not None:
                if dst == src or len(cells) < 2 or arrow is None:
                    raise LLMProgramError(f"malformed pipe ending at {pos}")
                pipes.append(
                    Pipe(
                        id=len(pipes),
                        cells=tuple(cells),
                        dirs=tuple(dirs),
                        src=src,
                        dst=dst,
                        src_attach=src_attach,
                        dst_attach=forward,
                    )
                )
                for cell in cells:
                    if cell in claimed:
                        raise LLMProgramError(f"pipes overlap at {cell}")
                    claimed[cell] = pipes[-1].id
                break
            if not (0 <= forward[0] < w and 0 <= forward[1] < h):
                raise LLMProgramError(f"pipe runs off the grid at {pos}")
            if forward in cells:
                raise LLMProgramError(f"pipe loops at {forward}")
            pos = forward
    return pipes


def _classify_char(ch: str) -> tuple[str, int]:
    """Kind/colour of a glyph *known to sit inside a room*."""
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
    if ch == "s":
        return KIND_SEND, COLOUR_PIPE_OP
    if ch == "r":
        return KIND_RECV, COLOUR_PIPE_OP
    # space, ``@``, and anything else the statement promises is inert
    return KIND_EMPTY, COLOUR_EMPTY


def _nearest_pipe(
    pipes: Sequence[Pipe], candidates: Sequence[int], x: int, y: int, outgoing: bool
) -> int:
    """The nearest pipe's id: Manhattan distance to its arrowhead, then reading order."""

    def key(pid: int) -> tuple[int, int, int]:
        hx, hy = pipes[pid].source_head if outgoing else pipes[pid].dest_head
        return (abs(x - hx) + abs(y - hy), hy, hx)

    return min(candidates, key=key)


def parse_program(codes: Sequence[int] | str, w: int, h: int) -> Program:
    """Build a :class:`Program` from ``W*H`` ASCII codes in row-major order.

    ``codes`` may also be given as a plain string of ``w * h`` characters.
    """
    rows = _grid_rows(codes, w, h)
    rects = _find_rooms(rows, w, h)

    room_of: dict[tuple[int, int], int] = {}
    border_owner: dict[tuple[int, int], int] = {}
    for rid, (x0, y0, x1, y1) in enumerate(rects):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                room_of[(x, y)] = rid
                if x in (x0, x1) or y in (y0, y1):
                    border_owner[(x, y)] = rid

    pipes = _trace_pipes(rows, w, h, rects, border_owner)
    pipe_of: dict[tuple[int, int], tuple[int, int]] = {}
    for pipe in pipes:
        for index, cell in enumerate(pipe.cells):
            pipe_of[cell] = (pipe.id, index)

    rooms: list[Room] = []
    for rid, (x0, y0, x1, y1) in enumerate(rects):
        spawn = None
        for y in range(y0 + 1, y1):
            for x in range(x0 + 1, x1):
                if rows[y][x] == "@":
                    if spawn is not None:
                        raise LLMProgramError(f"room {rid} has more than one @")
                    spawn = (x, y)
        if spawn is None:
            raise LLMProgramError(f"room {rid} has no @")
        outgoing = tuple(
            p.id
            for p in sorted(
                (p for p in pipes if p.src == rid),
                key=lambda p: (p.source_head[1], p.source_head[0]),
            )
        )
        incoming = tuple(
            p.id
            for p in sorted(
                (p for p in pipes if p.dst == rid),
                key=lambda p: (p.dest_head[1], p.dest_head[0]),
            )
        )
        rooms.append(Room(rid, x0, y0, x1, y1, spawn, outgoing, incoming))

    kinds: list[tuple[str, ...]] = []
    colours: list[tuple[int, ...]] = []
    binding: dict[tuple[int, int], int] = {}
    for y in range(h):
        krow: list[str] = []
        crow: list[int] = []
        for x in range(w):
            if (x, y) in border_owner:
                kind, colour = KIND_WALL, COLOUR_WALL
            elif (x, y) in pipe_of:
                kind, colour = KIND_PIPE, COLOUR_PIPE
            elif (x, y) in room_of:
                kind, colour = _classify_char(rows[y][x])
                if kind in (KIND_SEND, KIND_RECV):
                    room = rooms[room_of[(x, y)]]
                    pool = room.outgoing if kind == KIND_SEND else room.incoming
                    if pool:
                        binding[(x, y)] = _nearest_pipe(
                            pipes, pool, x, y, outgoing=kind == KIND_SEND
                        )
            else:
                kind, colour = KIND_EMPTY, COLOUR_EMPTY
            krow.append(kind)
            crow.append(colour)
        kinds.append(tuple(krow))
        colours.append(tuple(crow))

    return Program(
        width=w,
        height=h,
        rows=rows,
        rooms=tuple(rooms),
        pipes=tuple(pipes),
        kinds=tuple(kinds),
        colours=tuple(colours),
        room_of=room_of,
        pipe_of=pipe_of,
        binding=binding,
    )


def classify(program: Program, x: int, y: int) -> tuple[str, int]:
    """Return ``(opcode_kind, palette_colour)`` for the cell at ``(x, y)``."""
    return program.kind_at(x, y), program.colour_at(x, y)


# ── simulation ───────────────────────────────────────────────────────────────
@dataclass
class Man:
    """One little man."""

    id: int
    room: int
    x: int
    y: int
    heading: int = EAST
    a: int = 0
    b: int = 0
    #: True while he is stuck on an ``s``/``r`` that could not proceed this tick
    blocked: bool = False
    #: True once he has executed an ``H`` (he never moves again)
    halted: bool = False


@dataclass
class LLMState:
    """Mutable interpreter state for one LLM program."""

    program: Program
    men: list[Man] = field(default_factory=list)
    #: per pipe, one slot per cell in flow order; ``None`` means empty
    pipe_values: list[list[int | None]] = field(default_factory=list)
    ticks: int = 0
    #: True once the program can never change again
    halted: bool = False
    #: True when the halt was a man walking into a wall (everything froze)
    frozen_on_wall: bool = False

    def __post_init__(self) -> None:
        if not self.men:
            self.men = [
                Man(id=index, room=rid, x=x, y=y)
                for index, (x, y, rid) in enumerate(self.program.starts)
            ]
        if not self.pipe_values:
            self.pipe_values = [[None] * pipe.length for pipe in self.program.pipes]

    # -- one tick --------------------------------------------------------
    def step(self) -> None:
        """Run one tick: shift the pipes, execute every man, then move them."""
        if self.halted:
            return
        self._shift_pipes()
        for man in self.men:
            self._execute(man)
        for man in self.men:
            if not man.halted and not man.blocked:
                dx, dy = _DELTA[man.heading]
                man.x += dx
                man.y += dy
        self.ticks += 1
        if any(self.program.is_wall(man.x, man.y) for man in self.men):
            # The tick completed in full; now everything freezes forever.
            self.halted = True
            self.frozen_on_wall = True
        elif all(man.halted for man in self.men):
            self.halted = True

    def _shift_pipes(self) -> None:
        """Advance every value one cell if the next cell is free.

        Resolved from the destination end backwards, which is what makes a train
        of adjacent values move together instead of one cell per tick.
        """
        for slots in self.pipe_values:
            for i in range(len(slots) - 1, 0, -1):
                if slots[i] is None and slots[i - 1] is not None:
                    slots[i] = slots[i - 1]
                    slots[i - 1] = None

    def _execute(self, man: Man) -> None:
        if man.halted:
            return
        man.blocked = False
        kind = self.program.kind_at(man.x, man.y)
        ch = self.program.char_at(man.x, man.y)

        if kind == KIND_HALT:
            man.halted = True
        elif kind == KIND_HEADING:
            man.heading = _HEADING_OF_CHAR[ch]
        elif kind == KIND_DIGIT:
            man.a = int(ch)
        elif kind == KIND_MOVE:
            man.b = man.a
        elif kind == KIND_ARITH:
            man.a = man.a + man.b if ch == "+" else man.a - man.b
        elif kind == KIND_TURN:
            if man.a > 0:
                man.heading = (man.heading + 1) % 4
            elif man.a < 0:
                man.heading = (man.heading - 1) % 4
        elif kind == KIND_SEND:
            slots = self.pipe_values[self._bound_pipe(man, "s")]
            if slots[0] is None:
                slots[0] = man.a
            else:
                man.blocked = True
        elif kind == KIND_RECV:
            slots = self.pipe_values[self._bound_pipe(man, "r")]
            if slots[-1] is None:
                man.blocked = True
            else:
                man.a = slots[-1]
                slots[-1] = None
        # KIND_EMPTY / KIND_PIPE / KIND_WALL: nothing to do

    def _bound_pipe(self, man: Man, glyph: str) -> int:
        """The pipe this ``s``/``r`` cell binds to.  The statement guarantees one exists."""
        pid = self.program.binding.get((man.x, man.y))
        if pid is None:
            raise LLMProgramError(
                f"{glyph!r} at ({man.x}, {man.y}) is in a room with no pipe on that side"
            )
        return pid

    def run(self, k: int) -> int:
        """Step up to ``k`` ticks, stopping early on halt.  Returns ticks run."""
        done = 0
        for _ in range(k):
            if self.halted:
                break
            self.step()
            done += 1
        return done

    # -- observation -----------------------------------------------------
    def render(self) -> list[str]:
        """Render the 16x16 display as 16 rows of 16 lowercase hex digits."""
        prog = self.program
        overlay: dict[tuple[int, int], int] = {}
        for pipe, slots in zip(prog.pipes, self.pipe_values, strict=True):
            for cell, value in zip(pipe.cells, slots, strict=True):
                if value is not None:
                    overlay[cell] = COLOUR_PIPE_FULL
        for man in self.men:
            overlay[(man.x, man.y)] = COLOUR_MAN
        out = []
        for y in range(DISPLAY_H):
            row = []
            for x in range(DISPLAY_W):
                colour = overlay.get((x, y))
                if colour is None:
                    colour = prog.colour_at(x, y)
                row.append(f"{colour:x}")
            out.append("".join(row))
        return out

    def trace_state(self) -> dict:
        """A plain-data snapshot, for diffing another implementation tick by tick.

        Everything is JSON-serialisable and ordered deterministically: men by id
        (spawn reading order), pipes by id, pipe cells in flow order.
        """
        return {
            "ticks": self.ticks,
            "halted": self.halted,
            "frozen_on_wall": self.frozen_on_wall,
            "men": [
                {
                    "id": man.id,
                    "room": man.room,
                    "x": man.x,
                    "y": man.y,
                    "heading": HEADING_NAMES[man.heading],
                    "a": man.a,
                    "b": man.b,
                    "blocked": man.blocked,
                    "halted": man.halted,
                    "on": self.program.char_at(man.x, man.y),
                }
                for man in self.men
            ],
            "pipes": [
                {
                    "id": pipe.id,
                    "src": pipe.src,
                    "dst": pipe.dst,
                    "cells": [
                        {"x": cell[0], "y": cell[1], "value": value}
                        for cell, value in zip(pipe.cells, slots, strict=True)
                    ],
                }
                for pipe, slots in zip(self.program.pipes, self.pipe_values, strict=True)
            ],
        }


def render(state: LLMState) -> list[str]:
    """Module-level alias of :meth:`LLMState.render` (mirrors ``lllm_sim``)."""
    return state.render()


# ── the round protocol ───────────────────────────────────────────────────────
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
    state = LLMState(parse_program(head[2:], w, h))
    frames = [[state.render()]]
    for rnd in rounds[1:]:
        state.run(int(rnd[0]))
        frames.append([state.render()])
    return frames


def run_rounds_from_inputs(rounds: Iterable[Sequence[str]]) -> list[list[str]]:
    """Like :func:`run_case` but flattened to one frame per round."""
    return [frames[0] for frames in run_case(rounds)]


def self_check(verbose: bool = True) -> bool:
    """Replay every public case and compare frames byte-for-byte."""
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    spec = json.loads(
        (root / "tasks" / "problems" / "little-little-man.json").read_text(encoding="utf-8")
    )
    ok = True
    for case in spec["publicTestData"]:
        got = run_case([r["in"] for r in case["rounds"]])
        want = [r["frames"] for r in case["rounds"]]
        ok &= got == want
        if verbose:
            print(f"{'ok' if got == want else 'FAIL':>4}  {case['name']}")
    if verbose:
        print("all pass" if ok else "MISMATCH")
    return ok


if __name__ == "__main__":  # pragma: no cover - self-check convenience
    raise SystemExit(0 if self_check() else 1)
