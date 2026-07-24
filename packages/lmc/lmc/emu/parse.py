"""Structural parser: grid text -> rooms, pipes, I/O rooms, displays, men.

We only need to recognise the shapes our own codegen emits plus hand-written test
grids, but we follow task_docs/language.md closely so the emulator stays honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Directions as (dx, dy); y grows downward.
E, W, N, S = (1, 0), (-1, 0), (0, -1), (0, 1)
ARROW = {">": E, "<": W, "^": N, "v": S, "V": S}


class ParseError(Exception):
    pass


@dataclass
class Room:
    idx: int
    kind: str  # "room" | "input" | "output" | "display"
    x0: int
    y0: int
    x1: int
    y1: int  # inclusive corner coords
    interior: set[tuple[int, int]] = field(default_factory=set)
    io_char: str | None = None  # 'I' or 'O' for io rooms
    spawn: tuple[int, int] | None = None  # '@' cell if any

    def contains(self, x: int, y: int) -> bool:
        return (x, y) in self.interior


@dataclass
class Pipe:
    idx: int
    cells: list[tuple[int, int]]  # source end at [0], dest end at [-1]
    src_room: int
    dst_room: int

    @property
    def source_cell(self) -> tuple[int, int]:
        return self.cells[0]

    @property
    def dest_cell(self) -> tuple[int, int]:
        return self.cells[-1]


@dataclass
class Grid:
    lines: list[str]
    width: int
    height: int
    rooms: list[Room]
    pipes: list[Pipe]
    walls: set[tuple[int, int]]  # room/display border cells
    border_owner: dict[tuple[int, int], int]  # border cell -> room idx

    def char(self, x: int, y: int) -> str:
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.lines[y][x]
        return " "

    def room_of(self, x: int, y: int) -> int | None:
        for r in self.rooms:
            if r.contains(x, y):
                return r.idx
        return None


def _pad(text: str) -> list[str]:
    raw = text.split("\n")
    # drop a single trailing empty line from a final newline
    if raw and raw[-1] == "":
        raw = raw[:-1]
    w = max((len(line) for line in raw), default=0)
    return [line.ljust(w) for line in raw]


def _detect_rooms(lines: list[str], width: int, height: int) -> list[Room]:
    def ch(x: int, y: int) -> str:
        if 0 <= y < height and 0 <= x < width:
            return lines[y][x]
        return " "

    rooms: list[Room] = []
    for y in range(height):
        for x in range(width):
            if ch(x, y) != "+":
                continue
            # candidate top-left corner: need a horizontal border right, vertical border down
            right = ch(x + 1, y)
            down = ch(x, y + 1)
            if right in "-=" and down in "|:":
                hset = "-" if right == "-" else "="
                vset = "|" if down == "|" else ":"
                # walk top edge to the top-right '+'
                x1 = x + 1
                while x1 < width and ch(x1, y) == hset:
                    x1 += 1
                if x1 >= width or ch(x1, y) != "+":
                    continue
                # walk left edge to the bottom-left '+'
                y1 = y + 1
                while y1 < height and ch(x, y1) == vset:
                    y1 += 1
                if y1 >= height or ch(x, y1) != "+":
                    continue
                # verify bottom and right edges + far corner
                if ch(x1, y1) != "+":
                    continue
                ok = all(ch(xx, y1) == hset for xx in range(x + 1, x1))
                ok = ok and all(ch(x1, yy) == vset for yy in range(y + 1, y1))
                if not ok:
                    continue
                kind = "display" if hset == "=" else "room"
                interior = {
                    (cx, cy)
                    for cy in range(y + 1, y1)
                    for cx in range(x + 1, x1)
                }
                room = Room(len(rooms), kind, x, y, x1, y1, interior=interior)
                # classify I/O + find spawn
                io_cells = [(cx, cy) for (cx, cy) in interior if ch(cx, cy) in "IO"]
                if kind == "room" and len(interior) == 1 and io_cells:
                    cx, cy = io_cells[0]
                    room.io_char = ch(cx, cy)
                    room.kind = "input" if room.io_char == "I" else "output"
                spawns = [(cx, cy) for (cx, cy) in interior if ch(cx, cy) == "@"]
                if spawns:
                    room.spawn = spawns[0]
                rooms.append(room)
    return rooms


def _border_cells(room: Room) -> set[tuple[int, int]]:
    cells = set()
    for x in range(room.x0, room.x1 + 1):
        cells.add((x, room.y0))
        cells.add((x, room.y1))
    for y in range(room.y0, room.y1 + 1):
        cells.add((room.x0, y))
        cells.add((room.x1, y))
    return cells


def _detect_pipes(
    lines: list[str],
    width: int,
    height: int,
    border_owner: dict[tuple[int, int], int],
) -> list[Pipe]:
    def ch(x: int, y: int) -> str:
        if 0 <= y < height and 0 <= x < width:
            return lines[y][x]
        return " "

    pipes: list[Pipe] = []
    seen: set[frozenset[tuple[int, int]]] = set()

    for y in range(height):
        for x in range(width):
            c = ch(x, y)
            if c not in ARROW:
                continue
            d = ARROW[c]
            back = (x - d[0], y - d[1])
            if back not in border_owner:
                continue  # not a pipe start
            src_room = border_owner[back]
            pipe = _follow_pipe(ch, x, y, d, border_owner, src_room)
            if pipe is None:
                continue
            cells, dst_room = pipe
            key = frozenset(cells)
            if key in seen:
                continue
            seen.add(key)
            pipes.append(Pipe(len(pipes), cells, src_room, dst_room))
    return pipes


def _follow_pipe(ch, x, y, d, border_owner, src_room):
    cells = [(x, y)]
    cur = (x, y)
    guard = 0
    while guard < 100000:
        guard += 1
        nxt = (cur[0] + d[0], cur[1] + d[1])
        nc = ch(*nxt)
        horizontal = d in (E, W)
        body = "-" if horizontal else "|"
        if nc == body:
            cells.append(nxt)
            cur = nxt
            continue
        if nc in ARROW:
            cells.append(nxt)
            nd = ARROW[nc]
            fwd = (nxt[0] + nd[0], nxt[1] + nd[1])
            if fwd in border_owner and border_owner[fwd] != src_room:
                return cells, border_owner[fwd]
            cur = nxt
            d = nd
            continue
        # anything else = malformed / dead-end; give up on this start
        return None
    return None


def parse_grid(text: str) -> Grid:
    lines = _pad(text)
    height = len(lines)
    width = len(lines[0]) if lines else 0
    rooms = _detect_rooms(lines, width, height)

    walls: set[tuple[int, int]] = set()
    border_owner: dict[tuple[int, int], int] = {}
    for r in rooms:
        for cell in _border_cells(r):
            walls.add(cell)
            # first owner wins if two rooms share a border cell (rare in our grids)
            border_owner.setdefault(cell, r.idx)

    pipes = _detect_pipes(lines, width, height, border_owner)
    return Grid(lines, width, height, rooms, pipes, walls, border_owner)
