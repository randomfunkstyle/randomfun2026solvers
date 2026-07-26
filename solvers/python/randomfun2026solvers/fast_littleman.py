"""In-process Little Man interpreter optimized for repeated validation.

``Littleman`` in :mod:`randomfun2026solvers.littleman` is the reference
backend: it starts Node, boots the bundled Go/WASM interpreter, and exchanges a
JSON snapshot for every call.  That is ideal as an oracle but expensive when a
test or optimizer validates the same grid against many cases.

This module is an independent implementation of the language described in
``littleman/SPEC.md``.  :class:`FastLittleman` parses a grid once and then runs
cases entirely in memory.  It intentionally exposes a small validation-focused
API rather than imitating the reference engine's analysis/debugging endpoints.
The reference backend remains the differential-test oracle.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

__all__ = [
    "FastLittleman",
    "FastLittlemanError",
    "FastResult",
]

MASK64 = (1 << 64) - 1
MIN_I64 = -(1 << 63)
MAX_I64 = (1 << 63) - 1
MAX_RUNNERS = 65_536

Cell = tuple[int, int]
Dir = tuple[int, int]
EAST: Dir = (1, 0)
SOUTH: Dir = (0, 1)
WEST: Dir = (-1, 0)
NORTH: Dir = (0, -1)
DIRS: tuple[Dir, ...] = (EAST, SOUTH, WEST, NORTH)
ARROW_DIR: dict[str, Dir] = {">": EAST, "v": SOUTH, "V": SOUTH, "<": WEST, "^": NORTH}
VALID_OPS = frozenset("0123456789` .MWN+-*/%&|~{}<>^vVXYxdabmq]sSrRUH")
_NATIVE_SOURCE = Path(__file__).with_name("fast_littleman_native.cpp")
_NATIVE_LOCK = threading.Lock()
_NATIVE_LIB: ctypes.CDLL | None = None
_NATIVE_FAILED: Exception | None = None


class FastLittlemanError(RuntimeError):
    """A load-time error in an in-memory Little Man program."""

    def __init__(self, message: str, *, pos: Cell | None = None) -> None:
        super().__init__(message)
        self.pos = pos


def _i64(value: int) -> int:
    value &= MASK64
    return value if value <= MAX_I64 else value - (1 << 64)


def _cw(direction: Dir) -> Dir:
    return (-direction[1], direction[0])


def _ccw(direction: Dir) -> Dir:
    return (direction[1], -direction[0])


def _add(pos: Cell, direction: Dir) -> Cell:
    return pos[0] + direction[0], pos[1] + direction[1]


@dataclass(slots=True)
class _Room:
    id: int
    min: Cell
    max: Cell
    kind: Literal["compute", "input", "output", "display"]
    spawn: Cell | None = None
    incoming: list[int] = field(default_factory=list)
    outgoing: list[int] = field(default_factory=list)

    def contains(self, pos: Cell) -> bool:
        return self.min[0] < pos[0] < self.max[0] and self.min[1] < pos[1] < self.max[1]

    def on_border(self, pos: Cell) -> bool:
        x, y = pos
        return (
            self.min[0] <= x <= self.max[0]
            and self.min[1] <= y <= self.max[1]
            and (x in (self.min[0], self.max[0]) or y in (self.min[1], self.max[1]))
        )


@dataclass(slots=True)
class _Pipe:
    id: int
    path: list[Cell]
    dirs: list[Dir]
    src: int
    dst: int
    src_attach: Cell
    dst_attach: Cell
    dst_side: Dir


@dataclass(slots=True)
class _Runner:
    id: int
    room: int
    pos: Cell
    direction: Dir = EAST
    a: int = 0
    b: int = 0
    bp: int = 0
    halted: bool = False
    blocked: bool = False


@dataclass(slots=True)
class _DisplayState:
    room: int
    width: int
    height: int
    current: list[int]
    next: list[int]
    cursor: int = 0


@dataclass(slots=True)
class FastResult:
    """Validation-oriented result returned by :class:`FastLittleman`."""

    output: list[int]
    step: int
    halted: bool
    reason: str | None = None
    fatal: str | None = None
    fatal_pos: Cell | None = None
    passed: bool | None = None
    frames: list[list[str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.fatal is None and self.passed is not False


class FastLittleman:
    """Parse one grid and execute any number of cases in memory.

    ``program`` may be source text, rows, or a path.  Parsing is performed once
    in ``__init__``; :meth:`run` creates only mutable machine state.
    """

    def __init__(self, program: str | Path | Sequence[str]) -> None:
        self.rows = self._read_rows(program)
        self.height = len(self.rows)
        self.width = max((len(row) for row in self.rows), default=0)
        self.grid = [row.ljust(self.width) for row in self.rows]
        self.rooms = self._parse_rooms()
        if not self.rooms:
            raise FastLittlemanError("program has no rooms — draw a room around your little men")
        self._border_owner: dict[Cell, int] = {}
        self._interior_owner: dict[Cell, int] = {}
        for room in self.rooms:
            for y in range(room.min[1], room.max[1] + 1):
                for x in range(room.min[0], room.max[0] + 1):
                    pos = (x, y)
                    if room.on_border(pos):
                        self._border_owner[pos] = room.id
                    elif room.contains(pos):
                        self._interior_owner[pos] = room.id
        self.pipes = self._parse_pipes()
        self.input_room = self._unique_room("input")
        self.output_room = self._unique_room("output")
        self.display_rooms = [r.id for r in self.rooms if r.kind == "display"]
        self._literal_closers = self._parse_literals()
        self._bindings = self._bind_pipe_ops()

    @staticmethod
    def _read_rows(program: str | Path | Sequence[str]) -> list[str]:
        if isinstance(program, Path):
            text = program.read_text(encoding="utf-8")
            if text.endswith("\n"):
                text = text[:-1]
            return text.split("\n")
        if isinstance(program, str):
            if "\n" not in program and Path(program).is_file():
                text = Path(program).read_text(encoding="utf-8")
                if text.endswith("\n"):
                    text = text[:-1]
                return text.split("\n")
            text = program[:-1] if program.endswith("\n") else program
            return text.split("\n")
        return [str(row) for row in program]

    def _char(self, x: int, y: int) -> str:
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.grid[y][x]
        return " "

    def _valid_box(self, x1: int, y1: int, x2: int, y2: int, *, display: bool) -> bool:
        if x2 - x1 < 2 or y2 - y1 < 2:
            return False
        hwall, vwall = ("=", ":") if display else ("-", "|")
        if self._char(x2, y1) != "+" or self._char(x1, y2) != "+" or self._char(x2, y2) != "+":
            return False
        if any(self._char(x, y1) != hwall or self._char(x, y2) != hwall for x in range(x1 + 1, x2)):
            return False
        return not any(
            self._char(x1, y) != vwall or self._char(x2, y) != vwall
            for y in range(y1 + 1, y2)
        )

    def _parse_rooms(self) -> list[_Room]:
        found: list[tuple[int, int, int, int, bool]] = []
        for y1 in range(self.height):
            for x1 in range(self.width):
                if self._char(x1, y1) != "+":
                    continue
                for x2 in range(x1 + 2, self.width):
                    top = self._char(x2, y1)
                    if top != "+":
                        continue
                    display_hint = self._char(x1 + 1, y1) == "="
                    for y2 in range(y1 + 2, self.height):
                        if self._valid_box(x1, y1, x2, y2, display=display_hint):
                            found.append((x1, y1, x2, y2, display_hint))
                            break
        # Arithmetic '+' inside a room can form apparent boxes only if surrounded
        # by perfect walls.  Reject nested/overlapping candidates deterministically.
        accepted: list[tuple[int, int, int, int, bool]] = []
        for box in sorted(found, key=lambda b: (b[1], b[0], b[3], b[2])):
            x1, y1, x2, y2, _ = box
            if any(
                not (x2 < a1 or a2 < x1 or y2 < b1 or b2 < y1)
                for a1, b1, a2, b2, _ in accepted
            ):
                continue
            accepted.append(box)

        rooms: list[_Room] = []
        for rid, (x1, y1, x2, y2, display) in enumerate(accepted):
            kind: Literal["compute", "input", "output", "display"] = (
                "display" if display else "compute"
            )
            spawn = None
            for y in range(y1 + 1, y2):
                for x in range(x1 + 1, x2):
                    ch = self._char(x, y)
                    if ch == "@":
                        if spawn is not None:
                            raise FastLittlemanError("room has more than one @", pos=(x, y))
                        spawn = (x, y)
                    elif ch == "I":
                        kind = "input"
                    elif ch == "O":
                        kind = "output"
            if kind in ("input", "output") and (x2 - x1 != 2 or y2 - y1 != 2):
                raise FastLittlemanError(f"{kind} room must be 3x3", pos=(x1, y1))
            rooms.append(_Room(rid, (x1, y1), (x2, y2), kind, spawn))
        return rooms

    def _unique_room(self, kind: str) -> int | None:
        ids = [r.id for r in self.rooms if r.kind == kind]
        if len(ids) > 1:
            raise FastLittlemanError(f"program has more than one {kind} room")
        return ids[0] if ids else None

    def _room_at_border(self, pos: Cell) -> int | None:
        return self._border_owner.get(pos)

    def _flowed_into(self, cell: Cell, backward: Cell) -> bool:
        """True if another pipe arrowhead points *into* ``cell``.

        Such a cell is a mid-pipe continuation (typically a bend that happens to
        sit next to a wall), not a fresh start. ``backward`` is skipped because a
        start's backward cell is the source-room border, never a feeding pipe.
        Arrowheads inside a room are steer instructions, so neighbours on/in a
        room are ignored.
        """
        x, y = cell
        for dx, dy in DIRS:
            neighbor = (x + dx, y + dy)
            if neighbor == backward:
                continue
            if neighbor in self._border_owner or neighbor in self._interior_owner:
                continue
            ndir = ARROW_DIR.get(self._char(*neighbor))
            if ndir is not None and _add(neighbor, ndir) == cell:
                return True
        return False

    def _parse_pipes(self) -> list[_Pipe]:
        # A pipe starts at an arrowhead whose backward cell (opposite the arrow)
        # lies on a room's border — corners included — with the arrow pointing
        # away from the room, and which no other pipe arrowhead flows into. Arrow
        # glyphs inside a room are steer instructions, so a pipe cell must lie
        # strictly outside every room.
        starts: list[tuple[int, Cell, Dir, Cell]] = []
        for y in range(self.height):
            for x in range(self.width):
                cell = (x, y)
                direction = ARROW_DIR.get(self._char(x, y))
                if direction is None:
                    continue
                if cell in self._border_owner or cell in self._interior_owner:
                    continue
                backward = (x - direction[0], y - direction[1])
                src = self._border_owner.get(backward)
                if src is None or self.rooms[src].kind == "display":
                    continue
                if self._flowed_into(cell, backward):
                    continue
                starts.append((src, cell, direction, backward))

        pipes: list[_Pipe] = []
        occupied: dict[Cell, int] = {}
        for src, start, initial_dir, src_attach in sorted(
            starts, key=lambda item: (item[1][1], item[1][0])
        ):
            if start in occupied:
                continue
            path: list[Cell] = []
            dirs: list[Dir] = []
            pos, direction = start, initial_dir
            seen: set[Cell] = set()
            while True:
                if pos in seen:
                    raise FastLittlemanError("pipe loops", pos=pos)
                seen.add(pos)
                ch = self._char(*pos)
                arrow = ARROW_DIR.get(ch)
                body = "-" if direction[1] == 0 else "|"
                if arrow is not None:
                    if arrow == (-direction[0], -direction[1]):
                        raise FastLittlemanError("pipe arrow points backward", pos=pos)
                    direction = arrow
                elif ch != body:
                    raise FastLittlemanError("invalid pipe body", pos=pos)
                path.append(pos)
                dirs.append(direction)
                forward = _add(pos, direction)
                dst = self._room_at_border(forward)
                if dst is not None:
                    if dst == src:
                        raise FastLittlemanError("pipe loops back to its source room", pos=pos)
                    if len(path) < 2 or arrow is None:
                        raise FastLittlemanError(
                            "pipe must end with an arrow and have length >= 2", pos=pos
                        )
                    pipe = _Pipe(
                        len(pipes),
                        path,
                        dirs,
                        src,
                        dst,
                        src_attach,
                        forward,
                        direction,
                    )
                    pipes.append(pipe)
                    for cell in path:
                        if cell in occupied:
                            raise FastLittlemanError("pipes overlap", pos=cell)
                        occupied[cell] = pipe.id
                    self.rooms[src].outgoing.append(pipe.id)
                    self.rooms[dst].incoming.append(pipe.id)
                    break
                if not (0 <= forward[0] < self.width and 0 <= forward[1] < self.height):
                    raise FastLittlemanError("pipe runs off the grid", pos=pos)
                pos = forward

        for room in self.rooms:
            room.incoming.sort(key=lambda pid: (pipes[pid].dst_attach[1], pipes[pid].dst_attach[0]))
            room.outgoing.sort(key=lambda pid: (pipes[pid].src_attach[1], pipes[pid].src_attach[0]))
            if room.kind == "input" and len(room.outgoing) > 1:
                raise FastLittlemanError("input room has more than one outgoing pipe")
            if room.kind == "output" and len(room.incoming) > 1:
                raise FastLittlemanError("output room has more than one incoming pipe")
            if room.kind == "input" and room.incoming:
                raise FastLittlemanError("pipe flows into input room")
            if room.kind == "output" and room.outgoing:
                raise FastLittlemanError("pipe flows out of output room")
        return pipes

    def _parse_literals(self) -> dict[tuple[Cell, Dir], int]:
        closers: dict[tuple[Cell, Dir], int] = {}

        def pair(points: list[Cell], direction: Dir) -> None:
            # Pair independently on each axis.  With an odd count the last mark
            # is simply unpaired on this axis and may still pair on the other.
            for i in range(0, len(points) - 1, 2):
                begin, end = points[i], points[i + 1]
                dx, dy = direction
                chars: list[str] = []
                pos = _add(begin, direction)
                while pos != end:
                    chars.append(self._char(*pos))
                    pos = _add(pos, direction)
                raw = "".join(chars).replace(" ", "")
                if any(not ch.isdigit() for ch in raw):
                    raise FastLittlemanError("numeric literal contains a non-digit", pos=begin)
                forward = int(raw or "0")
                reverse = int(raw[::-1] or "0")
                if forward > MAX_I64 or reverse > MAX_I64:
                    raise FastLittlemanError(
                        "numeric literal does not fit signed 64 bits", pos=begin
                    )
                closers[(end, direction)] = forward
                closers[(begin, (-dx, -dy))] = reverse
                paired.update((begin, end))

        paired: set[Cell] = set()
        # Literal pairing is local to a compute room. Pairing across the whole
        # canvas makes aligned delimiters in separate rooms see the intervening
        # walls as non-digits, even though the reference interpreter accepts
        # each room's literal independently.
        for room in self.rooms:
            if room.kind != "compute":
                continue
            x0, y0 = room.min[0] + 1, room.min[1] + 1
            x1, y1 = room.max[0], room.max[1]
            for y in range(y0, y1):
                pts = [(x, y) for x in range(x0, x1) if self._char(x, y) == "`"]
                if pts:
                    pair(pts, EAST)
            for x in range(x0, x1):
                pts = [(x, y) for y in range(y0, y1) if self._char(x, y) == "`"]
                if pts:
                    pair(pts, SOUTH)
            for y in range(y0, y1):
                for x in range(x0, x1):
                    if self._char(x, y) == "`" and (x, y) not in paired:
                        raise FastLittlemanError("unmatched backtick", pos=(x, y))
        return closers

    def _bind_pipe_ops(self) -> dict[Cell, int | tuple[int, ...]]:
        bindings: dict[Cell, int | tuple[int, ...]] = {}
        for room in self.rooms:
            if room.kind != "compute":
                continue
            for y in range(room.min[1] + 1, room.max[1]):
                for x in range(room.min[0] + 1, room.max[0]):
                    ch = self._char(x, y)
                    candidates = room.outgoing if ch in "sS" else room.incoming
                    if ch not in "sSrRUq":
                        continue
                    if ch in "SRU":
                        bindings[(x, y)] = tuple(candidates)
                    elif candidates:
                        attach = (
                            (lambda p: p.src_attach) if ch == "s" else (lambda p: p.dst_attach)
                        )
                        bindings[(x, y)] = min(
                            candidates,
                            key=lambda pid: (
                                abs(x - attach(self.pipes[pid])[0])
                                + abs(y - attach(self.pipes[pid])[1]),
                                attach(self.pipes[pid])[1],
                                attach(self.pipes[pid])[0],
                            ),
                        )
                    else:
                        bindings[(x, y)] = -1
        return bindings

    def run(
        self,
        input: str | Sequence[int] | None = None,
        *,
        expected: str | Sequence[int] | None = None,
        frames: Sequence[Sequence[Sequence[str]]] | None = None,
        max_ticks: int = 5_000_000,
        native: bool = True,
    ) -> FastResult:
        """Execute from a fresh state.

        Slash-separated strings implement judge round gating.  Plain sequences
        are a single round.  When ``expected`` is supplied, later input rounds
        are released only after the preceding expected round has been emitted.
        """
        input_rounds = self._parse_round_values(input)
        expected_rounds = self._parse_round_values(expected) if expected is not None else None
        frame_rounds = self._parse_frame_rounds(frames)
        if native:
            try:
                return self._run_native(input_rounds, expected_rounds, frame_rounds, max_ticks)
            except (OSError, subprocess.SubprocessError):
                # A compiler is optional for portability.  The independent
                # Python engine remains a correct (but slower) fallback.
                pass
        if frame_rounds is not None:
            raise FastLittlemanError("display judging requires the native backend")
        machine = _Machine(self, input_rounds, expected_rounds)
        return machine.run(max_ticks)

    def _run_native(
        self,
        input_rounds: list[list[int]],
        expected_rounds: list[list[int]] | None,
        frame_rounds: list[list[list[int]]] | None,
        max_ticks: int,
    ) -> FastResult:
        lib = _native_library()
        request = self._native_request(input_rounds, expected_rounds, frame_rounds, max_ticks)
        ptr = lib.flm_run(request.encode("ascii"))
        if not ptr:
            raise OSError("native Little Man runner could not allocate its result")
        try:
            raw = ctypes.string_at(ptr).decode("ascii")
        finally:
            lib.flm_free(ptr)
        if raw.startswith("FLME1 "):
            raise FastLittlemanError(raw[6:])
        fields = raw.split()
        if not fields or fields[0] != "FLMR1" or len(fields) < 10:
            raise FastLittlemanError(f"invalid native result: {raw[:200]}")
        step = int(fields[1])
        halted = bool(int(fields[2]))
        passed_known = bool(int(fields[3]))
        passed = bool(int(fields[4])) if passed_known else None
        reason = fields[5]
        fatal = None if fields[6] == "-" else fields[6]
        fatal_pos_raw = (int(fields[7]), int(fields[8]))
        count = int(fields[9])
        output = [int(value) for value in fields[10:]]
        if len(output) != count:
            raise FastLittlemanError("native runner returned a truncated output list")
        return FastResult(
            output=output,
            step=step,
            halted=halted,
            reason=reason,
            fatal=fatal,
            fatal_pos=None if fatal_pos_raw == (-1, -1) else fatal_pos_raw,
            passed=passed,
        )

    def _native_request(
        self,
        input_rounds: list[list[int]],
        expected_rounds: list[list[int]] | None,
        frame_rounds: list[list[list[int]]] | None,
        max_ticks: int,
    ) -> str:
        values: list[int | str] = ["FLM1", self.width, self.height]
        values.extend(ord(ch) for row in self.grid for ch in row)
        kind_ids = {"compute": 0, "input": 1, "output": 2, "display": 3}
        values.append(len(self.rooms))
        for room in self.rooms:
            values.extend(
                (
                    room.min[0],
                    room.min[1],
                    room.max[0],
                    room.max[1],
                    kind_ids[room.kind],
                    room.spawn[0] if room.spawn else -1,
                    room.spawn[1] if room.spawn else -1,
                )
            )
        values.append(len(self.pipes))
        for pipe in self.pipes:
            values.extend((len(pipe.path), pipe.src, pipe.dst, pipe.dst_side[0], pipe.dst_side[1]))
        values.append(len(self._bindings))
        for (x, y), binding in sorted(
            self._bindings.items(), key=lambda item: (item[0][1], item[0][0])
        ):
            ids = list(binding) if isinstance(binding, tuple) else [binding]
            values.extend((x, y, len(ids), *ids))
        values.append(len(self._literal_closers))
        for ((x, y), (dx, dy)), value in sorted(
            self._literal_closers.items(),
            key=lambda item: (item[0][0][1], item[0][0][0], item[0][1][1], item[0][1][0]),
        ):
            values.extend((x, y, dx, dy, value))
        values.extend(
            (
                self.input_room if self.input_room is not None else -1,
                self.output_room if self.output_room is not None else -1,
                len(input_rounds),
            )
        )
        for round_values in input_rounds:
            values.extend((len(round_values), *round_values))
        values.append(1 if expected_rounds is not None else 0)
        if expected_rounds is not None:
            values.append(len(expected_rounds))
            for round_values in expected_rounds:
                values.extend((len(round_values), *round_values))
        values.append(1 if frame_rounds is not None else 0)
        if frame_rounds is not None:
            values.append(len(frame_rounds))
            for round_frames in frame_rounds:
                values.append(len(round_frames))
                for frame in round_frames:
                    values.extend((len(frame), *frame))
        values.append(max_ticks)
        return " ".join(str(value) for value in values)

    @staticmethod
    def _parse_round_values(value: str | Sequence[int] | None) -> list[list[int]]:
        if value is None:
            return [[]]
        if isinstance(value, str):
            return [
                [int(token) for token in part.split()]
                for part in value.split("/")
            ]
        return [[int(item) for item in value]]

    def _parse_frame_rounds(
        self,
        frames: Sequence[Sequence[Sequence[str]]] | None,
    ) -> list[list[list[int]]] | None:
        if frames is None:
            return None
        display_ids = self.display_rooms
        if len(display_ids) != 1:
            raise FastLittlemanError(
                f"display judging needs exactly one display, found {len(display_ids)}"
            )
        room = self.rooms[display_ids[0]]
        width = room.max[0] - room.min[0] - 1
        height = room.max[1] - room.min[1] - 1
        parsed: list[list[list[int]]] = []
        for round_frames in frames:
            parsed_round: list[list[int]] = []
            for frame in round_frames:
                if len(frame) != height or any(len(str(row)) != width for row in frame):
                    raise FastLittlemanError(
                        f"expected frame is not {width}x{height}"
                    )
                try:
                    pixels = [int(ch, 16) for row in frame for ch in str(row)]
                except ValueError as exc:
                    raise FastLittlemanError("expected frame contains a non-hex color") from exc
                parsed_round.append(pixels)
            parsed.append(parsed_round)
        return parsed


def _native_library() -> ctypes.CDLL:
    """Build/load the tiny native tick loop, cached by source content."""
    global _NATIVE_LIB, _NATIVE_FAILED
    if _NATIVE_LIB is not None:
        return _NATIVE_LIB
    if _NATIVE_FAILED is not None:
        raise OSError(f"native Little Man backend unavailable: {_NATIVE_FAILED}")
    with _NATIVE_LOCK:
        if _NATIVE_LIB is not None:
            return _NATIVE_LIB
        digest = hashlib.sha256(_NATIVE_SOURCE.read_bytes()).hexdigest()[:16]
        suffix = ".dylib" if sys.platform == "darwin" else ".so"
        target = Path(tempfile.gettempdir()) / f"randomfun-fast-littleman-{digest}{suffix}"
        try:
            if not target.exists():
                compiler = os.environ.get("CXX", "c++")
                temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
                command = [
                    compiler,
                    "-std=c++17",
                    "-O3",
                    "-DNDEBUG",
                    "-fPIC",
                    "-shared",
                    str(_NATIVE_SOURCE),
                    "-o",
                    str(temporary),
                ]
                subprocess.run(command, check=True, capture_output=True, text=True)
                os.replace(temporary, target)
            lib = ctypes.CDLL(str(target))
            lib.flm_run.argtypes = [ctypes.c_char_p]
            lib.flm_run.restype = ctypes.c_void_p
            lib.flm_free.argtypes = [ctypes.c_void_p]
            lib.flm_free.restype = None
            _NATIVE_LIB = lib
            return lib
        except (OSError, subprocess.SubprocessError) as exc:
            _NATIVE_FAILED = exc
            raise OSError(f"native Little Man backend unavailable: {exc}") from exc


class _Machine:
    """Mutable state for one run; kept separate so ``FastLittleman`` is reusable."""

    def __init__(
        self,
        program: FastLittleman,
        input_rounds: list[list[int]],
        expected_rounds: list[list[int]] | None,
    ) -> None:
        self.p = program
        self.pipe_values: list[list[int | None]] = [
            [None] * len(pipe.path) for pipe in program.pipes
        ]
        # The reference creates runners in row-major order of the @ cells,
        # independently of the order in which enclosing rooms were discovered.
        # Y makes this observable because the right child retains this slot.
        spawns = sorted(
            (
                (room.spawn, room.id)
                for room in program.rooms
                if room.kind == "compute" and room.spawn is not None
            ),
            key=lambda item: (item[0][1], item[0][0]),
        )
        self.runners = [
            _Runner(i, room_id, spawn)
            for i, (spawn, room_id) in enumerate(spawns)
        ]
        self.next_runner_id = len(self.runners)
        self.input_rounds = input_rounds
        self.expected_rounds = expected_rounds
        self.output: list[int] = []
        self.released_round = 0
        initial_input = (
            [value for round_values in input_rounds for value in round_values]
            if expected_rounds is None
            else (input_rounds[0] if input_rounds else [])
        )
        self.input_queue: deque[int] = deque(initial_input)
        self.expected_flat = (
            [v for values in expected_rounds for v in values]
            if expected_rounds is not None
            else None
        )
        self.expected_cumulative: list[int] = []
        if expected_rounds is not None:
            total = 0
            for values in expected_rounds:
                total += len(values)
                self.expected_cumulative.append(total)
            self._release_satisfied_rounds()
        self.step = 0
        self.fatal: str | None = None
        self.fatal_pos: Cell | None = None

    def run(self, max_ticks: int) -> FastResult:
        while self.step < max_ticks:
            if self.expected_flat is not None and len(self.output) >= len(self.expected_flat):
                return self._result("output-settled", passed=True)
            if self.fatal is not None:
                return self._result(self.fatal, passed=False)
            active = any(not runner.halted for runner in self.runners)
            output_in_flight = self._output_in_flight()
            if not active and not output_in_flight:
                passed = (
                    None
                    if self.expected_flat is None
                    else self.output == self.expected_flat
                )
                return self._result("done", passed=passed)
            self._tick()
        passed = None if self.expected_flat is None else False
        return self._result("tick-cap", passed=passed)

    def _result(self, reason: str, *, passed: bool | None) -> FastResult:
        return FastResult(
            output=list(self.output),
            step=self.step,
            halted=not any(not r.halted for r in self.runners),
            reason=reason,
            fatal=self.fatal,
            fatal_pos=self.fatal_pos,
            passed=passed,
        )

    def _output_in_flight(self) -> bool:
        rid = self.p.output_room
        if rid is None or not self.p.rooms[rid].incoming:
            return False
        return any(v is not None for v in self.pipe_values[self.p.rooms[rid].incoming[0]])

    def _tick(self) -> None:
        self.step += 1
        self._shift_pipes()
        self._io()
        if self.fatal is not None:
            return
        self._execute()
        if self.fatal is not None:
            return
        self._move()

    def _shift_pipes(self) -> None:
        for values in self.pipe_values:
            for i in range(len(values) - 1, 0, -1):
                if values[i] is None and values[i - 1] is not None:
                    values[i] = values[i - 1]
                    values[i - 1] = None

    def _io(self) -> None:
        if self.p.output_room is not None:
            incoming = self.p.rooms[self.p.output_room].incoming
            if incoming:
                values = self.pipe_values[incoming[0]]
                if values[-1] is not None:
                    value = values[-1]
                    values[-1] = None
                    assert value is not None
                    self.output.append(value)
                    if self.expected_flat is not None:
                        index = len(self.output) - 1
                        if index >= len(self.expected_flat) or value != self.expected_flat[index]:
                            self.fatal = "wrong-output"
                            return
                        self._release_satisfied_rounds()

        if self.p.input_room is not None:
            outgoing = self.p.rooms[self.p.input_room].outgoing
            if outgoing and self.input_queue:
                values = self.pipe_values[outgoing[0]]
                if values[0] is None:
                    values[0] = self.input_queue.popleft()

    def _release_satisfied_rounds(self) -> None:
        """Release through zero-output rounds without waiting for a future emit."""
        while (
            self.released_round < len(self.expected_cumulative)
            and len(self.output) >= self.expected_cumulative[self.released_round]
        ):
            self.released_round += 1
            if self.released_round < len(self.input_rounds):
                self.input_queue.extend(self.input_rounds[self.released_round])

    def _fatal(self, reason: str, pos: Cell) -> None:
        self.fatal = reason
        self.fatal_pos = pos

    def _execute(self) -> None:
        spawned: list[_Runner] = []
        occupied = {r.pos: r for r in self.runners if not r.halted}
        live = len(occupied)

        def birth(child: _Runner) -> None:
            nonlocal live
            room = self.p.rooms[child.room]
            if room.on_border(child.pos) or not room.contains(child.pos):
                self._fatal("wall", child.pos)
                return
            occupant = occupied.get(child.pos)
            if occupant is not None and not occupant.halted:
                occupant.halted = True
                child.halted = True
                occupied.pop(child.pos, None)
                live -= 2
            else:
                occupied[child.pos] = child

        for runner in self.runners:
            runner.blocked = False
            if runner.halted:
                continue
            x, y = runner.pos
            ch = self.p._char(x, y)
            if ch == "@":
                ch = " "
            if ch not in VALID_OPS:
                self._fatal("bad-op", runner.pos)
                return
            if ch.isdigit():
                runner.a = ord(ch) - ord("0")
            elif ch == "`":
                value = self.p._literal_closers.get((runner.pos, runner.direction))
                if value is not None:
                    runner.a = value
            elif ch == "M":
                runner.b = runner.a
            elif ch == "W":
                runner.a, runner.b = runner.b, runner.a
            elif ch == "N":
                runner.a = _i64(-runner.a)
            elif ch == "+":
                runner.a = _i64(runner.a + runner.b)
            elif ch == "-":
                runner.a = _i64(runner.a - runner.b)
            elif ch == "*":
                runner.a = _i64(runner.a * runner.b)
            elif ch == "%":
                runner.a = 0 if runner.b == 0 else runner.a % runner.b
            elif ch == "/":
                dividend, divisor = runner.a, runner.b
                if divisor == 0:
                    runner.a, runner.b = 0, dividend
                elif dividend == MIN_I64 and divisor == -1:
                    runner.a, runner.b = MIN_I64, 0
                else:
                    runner.a = dividend // divisor
                    runner.b = dividend - runner.a * divisor
            elif ch == "&":
                runner.a = _i64(runner.a & runner.b)
            elif ch == "|":
                runner.a = _i64(runner.a | runner.b)
            elif ch == "~":
                runner.a = _i64(runner.a ^ runner.b)
            elif ch == "{":
                runner.a = _i64(runner.a << runner.b) if 0 <= runner.b <= 63 else 0
            elif ch == "}":
                if runner.b < 0:
                    runner.a = 0
                elif runner.b > 63:
                    runner.a = -1 if runner.a < 0 else 0
                else:
                    runner.a = runner.a >> runner.b
            elif ch in ARROW_DIR:
                runner.direction = ARROW_DIR[ch]
            elif ch == "X":
                if runner.a > 0:
                    runner.direction = _cw(runner.direction)
                elif runner.a < 0:
                    runner.direction = _ccw(runner.direction)
            elif ch == "b":
                runner.bp = runner.a
            elif ch == "m":
                runner.bp = _i64(runner.bp - 1)
            elif ch == "d" and runner.bp > 0:
                runner.direction = _cw(runner.direction)
            elif ch == "a" and runner.bp > 0:
                runner.direction = _ccw(runner.direction)
            elif ch == "]":
                runner.bp >>= 1
            elif ch == "x":
                runner.direction = (
                    _cw(runner.direction) if runner.bp & 1 else _ccw(runner.direction)
                )
            elif ch == "Y":
                old = runner.direction
                origin = runner.pos
                occupied.pop(origin, None)
                runner.direction = _cw(old)
                runner.pos = _add(origin, runner.direction)
                runner.blocked = True
                direction = _ccw(old)
                left = _Runner(
                    self.next_runner_id,
                    runner.room,
                    _add(origin, direction),
                    direction,
                    runner.a,
                    runner.b,
                    runner.bp,
                    blocked=True,
                )
                self.next_runner_id += 1
                spawned.append(left)
                live += 1
                # The reference reports the left/CCW birth first when both
                # sides are invalid, even though the right child keeps the
                # parent's execution-order slot.
                birth(left)
                if self.fatal is not None:
                    return
                birth(runner)
                if self.fatal is not None:
                    return
                if live > MAX_RUNNERS:
                    self._fatal("too-many-runners", origin)
                    return
            elif ch == "H":
                runner.halted = True
                occupied.pop(runner.pos, None)
                live -= 1
            elif ch in "sSrRUq":
                self._pipe_op(runner, ch)
                if self.fatal is not None:
                    return
        self.runners.extend(spawned)

    def _pipe_op(self, runner: _Runner, ch: str) -> None:
        binding = self.p._bindings.get(runner.pos, -1)
        if binding == -1 or binding == ():
            self._fatal("no-pipe", runner.pos)
            return
        if ch == "q":
            assert isinstance(binding, int)
            runner.bp = sum(v is not None for v in self.pipe_values[binding])
            return
        if ch == "s":
            assert isinstance(binding, int)
            values = self.pipe_values[binding]
            if values[0] is not None:
                runner.blocked = True
            else:
                values[0] = runner.a
            return
        if ch == "S":
            assert isinstance(binding, tuple)
            if any(self.pipe_values[pid][0] is not None for pid in binding):
                runner.blocked = True
            else:
                for pid in binding:
                    self.pipe_values[pid][0] = runner.a
            return
        if ch == "r":
            assert isinstance(binding, int)
            values = self.pipe_values[binding]
            if values[-1] is None:
                runner.blocked = True
            else:
                runner.a = values[-1]  # type: ignore[assignment]
                values[-1] = None
            return
        assert isinstance(binding, tuple)
        ready = next((pid for pid in binding if self.pipe_values[pid][-1] is not None), None)
        if ready is None:
            runner.blocked = True
            return
        values = self.pipe_values[ready]
        runner.a = values[-1]  # type: ignore[assignment]
        values[-1] = None
        if ch == "U":
            runner.direction = self.p.pipes[ready].dst_side

    def _move(self) -> None:
        moving = [r for r in self.runners if not r.halted and not r.blocked]
        destinations = {r.id: _add(r.pos, r.direction) for r in moving}
        moving_ids = set(destinations)
        by_dest: dict[Cell, list[_Runner]] = {}
        for runner in moving:
            by_dest.setdefault(destinations[runner.id], []).append(runner)
        current = {r.pos: r for r in self.runners if not r.halted}
        touching: set[int] = set()
        for dest, arrivals in by_dest.items():
            if len(arrivals) > 1:
                touching.update(r.id for r in arrivals)
            occupant = current.get(dest)
            if occupant is None:
                continue
            if occupant.id not in moving_ids:
                touching.add(occupant.id)
                touching.update(r.id for r in arrivals)
                continue
            for runner in arrivals:
                if destinations.get(occupant.id) == runner.pos:
                    touching.add(occupant.id)
                    touching.add(runner.id)
        for runner in self.runners:
            if runner.id in touching:
                runner.halted = True
        for runner in moving:
            runner.pos = destinations[runner.id]
            if runner.halted:
                continue
            room = self.p.rooms[runner.room]
            if room.on_border(runner.pos) or not room.contains(runner.pos):
                self._fatal("wall", runner.pos)
                return


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a program against every public case of a problem."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m randomfun2026solvers.fast_littleman",
        description="Validate a .man program in memory without Node/WASM.",
    )
    parser.add_argument("program", type=Path)
    parser.add_argument("problem", help="problem slug or tasks/problems/*.json")
    parser.add_argument("--tick-cap", type=int, default=5_000_000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    # Import here to avoid a module cycle: optimize.verify imports this class.
    from .optimize import verify

    result = verify(args.program, args.problem, tick_cap=args.tick_cap)
    if args.json:
        print(
            json.dumps(
                {
                    "passed": result.passed,
                    "passedCases": result.n_passed,
                    "totalCases": len(result.cases),
                    "avgTicks": result.avg_ticks,
                    "cases": [
                        {
                            "name": case.name,
                            "passed": case.passed,
                            "ticks": case.ticks,
                            "detail": case.detail,
                        }
                        for case in result.cases
                    ],
                },
                indent=2,
            )
        )
    else:
        for case in result.cases:
            status = "PASS" if case.passed else "FAIL"
            suffix = f" — {case.detail}" if case.detail else ""
            print(f"{status}  {case.name}: {case.ticks} ticks{suffix}")
        print(
            f"{result.n_passed}/{len(result.cases)} passed"
            + (
                f", average {result.avg_ticks:.2f} ticks"
                if result.avg_ticks is not None
                else ""
            )
        )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
