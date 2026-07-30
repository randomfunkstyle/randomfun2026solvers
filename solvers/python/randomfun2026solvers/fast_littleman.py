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
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

__all__ = [
    "FastLittleman",
    "FastLittlemanError",
    "FastOpProfile",
    "FastProfile",
    "FastResult",
    "OpcodeTags",
]

MASK64 = (1 << 64) - 1
MIN_I64 = -(1 << 63)
MAX_I64 = (1 << 63) - 1
MAX_RUNNERS = 65_536

Cell = tuple[int, int]
Dir = tuple[int, int]
# A single display's own judged content: rounds of frames of hex-digit rows.
FrameSpec = Sequence[Sequence[Sequence[str]]]
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
class FastProfile:
    """Where a run spent itself, when :meth:`FastLittleman.run` was asked.

    ``heat`` is a per-cell count of runner *samples*: every ``stride`` ticks each
    live runner's cell is recorded, so a man parked on an ``r`` waiting for a
    pipe is counted every sample.  Blocked time is time, and this is the metric
    that shows it — an instruction counter would hide exactly the men who cost
    the most.  ``wait`` is the sleeping-on-a-pipe subset of the same samples.

    The pipe counters are exact (not sampled): every value that entered or left
    each pipe, plus the retries that found the pipe full/empty.  A block is
    counted once per park, not once per tick, because the engine sleeps a
    blocked runner rather than re-testing him — use ``pipe_wait`` (sampled) for
    blocked *duration* and ``recv_blocked``/``send_blocked`` for how often.
    """

    width: int
    height: int
    samples: int
    stride: int
    heat: dict[Cell, int] = field(default_factory=dict)
    wait: dict[Cell, int] = field(default_factory=dict)
    send: list[int] = field(default_factory=list)
    recv: list[int] = field(default_factory=list)
    send_blocked: list[int] = field(default_factory=list)
    recv_blocked: list[int] = field(default_factory=list)
    query: list[int] = field(default_factory=list)
    pipe_wait: list[int] = field(default_factory=list)


@dataclass(slots=True)
class OpcodeTags:
    """What the caller must say before the engine can attribute ticks to opcodes.

    ``classes`` names what a runner is *doing* on a cell (dispatch walk, memory
    lane, slab, …) and ``ops`` names the instructions.  ``tags`` maps
    ``(x, y, arrival direction)`` — 0 east, 1 south, 2 west, 3 north — to
    ``(class index, opcode index or -1)``: a cell that identifies an instruction
    carries its opcode, every other cell carries only a class.

    The direction is part of the key because one cell can belong to two
    structures at once: a lane row walked east is also, at the columns where
    other lanes descend, somebody else's drop column walked south.  Tagging the
    cell alone would charge every instruction's descent to whichever lanes it
    happens to cross.

    The engine cuts the focus runner's timeline whenever he *enters* the
    ``boundary`` class (the instruction fetch) and folds each resulting segment
    into whichever opcode's cells that segment touched, so the trie descent and
    the return walk that surround a lane are charged to the instruction that
    caused them rather than to a shared bucket.

    ``hist_pipe`` asks for an exact histogram of how long each blocked run on
    that pipe lasted; ``value_pipe`` asks for a census of the values the focus
    runner sent into it (the store address stream, in practice).  Both are
    ``-1`` for "do not collect".
    """

    classes: list[str]
    ops: list[str]
    tags: dict[tuple[int, int, int], tuple[int, int]]
    boundary: int
    hist_pipe: int = -1
    value_pipe: int = -1


@dataclass(slots=True)
class FastOpProfile:
    """Per-opcode tick attribution: exact, every tick, no stride.

    ``ticks[op][cls]`` and ``blocked[op][cls]`` are runner-ticks; ``execs[op]``
    counts the segments folded into that opcode.  Index ``len(ops)`` is the
    *unattributed* slot — a segment that never touched an opcode-bearing cell —
    and it is reported rather than dropped.  ``outside`` counts ticks where no
    runner stood on a tagged cell at all, and ``multi`` counts ticks where more
    than one did (which would make the focus ambiguous; it should be zero).
    """

    classes: list[str]
    ops: list[str]
    samples: int
    outside: int
    multi: int
    execs: list[int] = field(default_factory=list)
    ticks: list[list[int]] = field(default_factory=list)
    blocked: list[list[int]] = field(default_factory=list)
    pipe_ticks: dict[tuple[int, int], int] = field(default_factory=dict)
    pipe_runs: dict[tuple[int, int], int] = field(default_factory=dict)
    block_hist: dict[int, dict[int, int]] = field(default_factory=dict)
    values: dict[int, dict[int, int]] = field(default_factory=dict)


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
    #: Tick stamps, one per *logical* frame accepted by the display judge — the
    #: tick the slowest panel committed it on.  Filled only when ``frames=`` was
    #: supplied; the cost of frame *n* is ``frame_ticks[n] - frame_ticks[n-1]``.
    #: On a tiled wall (``frame_tiles=``) a logical frame spans every panel, so
    #: the stamp is the slowest tile's; with independent per-display specs each
    #: display advances its own judge, and the stamp is likewise the slowest
    #: judged display's *n*-th commit.
    frame_ticks: list[int] = field(default_factory=list)
    profile: FastProfile | None = None
    opcodes: FastOpProfile | None = None
    _frames_by_display: list[list[list[str]]] = field(default_factory=list)
    _native: bool = True

    @property
    def ok(self) -> bool:
        return self.fatal is None and self.passed is not False

    def frames_per_display(self) -> list[list[list[str]]]:
        """Every frame each display committed, one list per display room, reading order.

        Populated regardless of whether ``frames=`` was passed to :meth:`FastLittleman.run`
        for judging: this reports what was actually drawn, not what was expected.
        ``frames`` above is the single-display convenience — the same data, unwrapped —
        for the (only previously supported) case of exactly one display room.

        Two things this does **not** promise:

        * It requires the native backend. A result from the pure-Python fallback
          (``run(..., native=False)``, or a native build that failed and fell back
          silently) has no captured content at all, and raises here rather than
          returning an empty list that would be indistinguishable from "nothing was
          drawn."
        * It can under-report by a commit. The engine treats a run as finished once
          every runner has halted with no *output* in flight (``live == 0 and not
          output_in_flight()``) — display pipes are not drained for that check. A
          SWAP value still travelling its pipe at that instant never reaches
          ``execute_displays()`` and is not recorded, even though the reference wasm
          engine (which keeps stepping until nothing changes) does commit it. This is
          pre-existing engine behaviour, not something this accessor changes; see
          ``littleman/examples/panel-latency-swap-equal.man`` and the test pinning it
          in ``tests/test_mnist_display.py``.
        """
        if not self._native:
            raise FastLittlemanError(
                "frames_per_display() requires the native backend; this result came "
                "from the pure-Python fallback (native=False), which does not "
                "capture display frames"
            )
        return self._frames_by_display


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

    def _trace_cells(self, start: Cell, initial_dir: Dir) -> tuple[list[Cell], bool]:
        """The cells a pipe from ``start`` would occupy and whether it reaches a
        room border (a valid destination).

        No validation — used only to decide which candidate starts are actually
        mid-pipe continuations of some other pipe. Stops at the first border it
        flows into, off the grid, or on a loop.
        """
        path: list[Cell] = []
        pos, direction = start, initial_dir
        seen: set[Cell] = set()
        while pos not in seen:
            seen.add(pos)
            arrow = ARROW_DIR.get(self._char(*pos))
            if arrow is not None:
                direction = arrow
            path.append(pos)
            forward = _add(pos, direction)
            if self._room_at_border(forward) is not None:
                return path, True
            if not (0 <= forward[0] < self.width and 0 <= forward[1] < self.height):
                break
            pos = forward
        return path, False

    def _parse_pipes(self) -> list[_Pipe]:
        # A pipe starts at an arrowhead whose backward cell (opposite the arrow)
        # lies on a room's border — corners included — with the arrow pointing
        # away from the room. Arrow glyphs inside a room are steer instructions,
        # so a pipe cell must lie strictly outside every room.
        candidates: list[tuple[int, Cell, Dir, Cell]] = []
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
                candidates.append((src, cell, direction, backward))

        # A candidate is a real start unless another candidate's pipe flows
        # through it — i.e. it is a mid-pipe cell (typically a bend sitting next
        # to a wall). Only pipes that reach a destination claim their interior,
        # so a stray arrowhead that merely points at a genuine start cannot
        # suppress it (that arrowhead is not itself a room-attached pipe).
        claimed: set[Cell] = set()
        for _src, cell, direction, _backward in candidates:
            cells, reached = self._trace_cells(cell, direction)
            if reached:
                claimed.update(cells[1:])
        starts = [c for c in candidates if c[1] not in claimed]

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
        frames: FrameSpec | Sequence[FrameSpec | None] | None = None,
        frame_tiles: tuple[int, int] | None = None,
        max_ticks: int = 5_000_000,
        native: bool = True,
        profile: bool = False,
        profile_stride: int = 1,
        opcodes: OpcodeTags | None = None,
    ) -> FastResult:
        """Execute from a fresh state.

        Slash-separated strings implement judge round gating.  Plain sequences
        are a single round.  When ``expected`` is supplied, later input rounds
        are released only after the preceding expected round has been emitted.

        ``frames`` judges committed display frames against expected content.
        There are two shapes, for the two things more than one panel can mean,
        and they are chosen by ``frame_tiles``:

        * **Independent panels** (``frame_tiles`` omitted).  A program with
          exactly one display room takes that display's own sequence of rounds
          directly (the original, single-display calling convention).  A program
          with more display rooms takes one such sequence per room, in reading
          order; ``None`` in a slot leaves that display unjudged.
        * **One tiled wall** (``frame_tiles=(cols, rows)``, below).  Every
          display paints one tile of a single logical frame, so ``frames`` stays
          one frame stream however many panels there are.

        The two are not interchangeable and a spec of the wrong depth raises
        rather than being reinterpreted.  Regardless of ``frames``, every
        display's actually committed frames are available afterwards via
        :meth:`FastResult.frames_per_display`.

        ``profile=True`` additionally fills :attr:`FastResult.profile` with a
        per-cell occupancy heatmap (sampled every ``profile_stride`` ticks) and
        exact per-pipe traffic counters.  It is off by default and adds nothing
        to the request when off, so an ordinary run is unchanged; it requires
        the native backend.

        ``opcodes=`` additionally fills :attr:`FastResult.opcodes` with a
        per-opcode tick attribution (see :class:`OpcodeTags`).  It also requires
        ``profile=True`` — it is the second, likewise trailing, section of the
        same reply — and it is likewise absent from a request that omits it.

        ``frame_tiles=(cols, rows)`` judges a **tiled wall**: a machine whose
        ``cols * rows`` displays each paint one tile of the expected frame, in
        display (reading) order.  Each panel is checked against its own tile of
        expected frame *n* on its *n*-th COMMIT, and a round is released only
        once the slowest panel has committed — composition is by frame index,
        exactly as :func:`lm1.display.tiled_frames_from_writes` does it.
        """
        input_rounds = self._parse_round_values(input)
        expected_rounds = self._parse_round_values(expected) if expected is not None else None
        frame_specs, tiled = self._frame_specs(frames, frame_tiles)
        if profile and not native:
            raise FastLittlemanError("profiling requires the native backend")
        if opcodes is not None and not profile:
            raise FastLittlemanError("opcode attribution requires profile=True")
        if native:
            try:
                return self._run_native(
                    input_rounds,
                    expected_rounds,
                    frame_specs,
                    max_ticks,
                    tiled=tiled,
                    profile=profile,
                    profile_stride=profile_stride,
                    opcodes=opcodes,
                )
            except (OSError, subprocess.SubprocessError):
                # A compiler is optional for portability.  The independent
                # Python engine remains a correct (but slower) fallback.
                if profile:
                    raise
        if frame_specs is not None:
            raise FastLittlemanError("display judging requires the native backend")
        machine = _Machine(self, input_rounds, expected_rounds)
        return machine.run(max_ticks)

    def _run_native(
        self,
        input_rounds: list[list[int]],
        expected_rounds: list[list[int]] | None,
        frame_specs: list[list[list[list[int]]] | None] | None,
        max_ticks: int,
        *,
        tiled: bool = False,
        profile: bool = False,
        profile_stride: int = 1,
        opcodes: OpcodeTags | None = None,
    ) -> FastResult:
        lib = _native_library()
        request = self._native_request(
            input_rounds,
            expected_rounds,
            frame_specs,
            max_ticks,
            tiled=tiled,
            profile=profile,
            profile_stride=profile_stride,
            opcodes=opcodes,
        )
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
        output_end = 10 + count
        output = [int(value) for value in fields[10:output_end]]
        if len(output) != count:
            raise FastLittlemanError("native runner returned a truncated output list")
        # The committed-frame section is unconditional and positional, so it comes
        # first; the opt-in sections after it are self-describing (``P``/``Q``/``F``)
        # and are read in the order the native side appends them.
        frames_by_display, tail_start = self._decode_committed_frames(fields, output_end)
        tail = iter(fields[tail_start:])
        prof = self._parse_profile(tail) if profile else None
        ops = self._parse_opcodes(tail, opcodes) if opcodes is not None else None
        judged = frame_specs is not None and any(spec is not None for spec in frame_specs)
        ticks = self._parse_frame_ticks(tail) if judged else []
        return FastResult(
            output=output,
            step=step,
            halted=halted,
            reason=reason,
            fatal=fatal,
            fatal_pos=None if fatal_pos_raw == (-1, -1) else fatal_pos_raw,
            passed=passed,
            profile=prof,
            opcodes=ops,
            frame_ticks=ticks,
            # The single-display convenience: unambiguous only when there is
            # exactly one display room, which is every existing caller.
            frames=frames_by_display[0] if len(frames_by_display) == 1 else [],
            _frames_by_display=frames_by_display,
        )

    def _decode_committed_frames(
        self, fields: list[str], start: int
    ) -> tuple[list[list[list[str]]], int]:
        """Parse the response's per-display committed-frame section.

        Returns the frames and the index one past the section, so the opt-in
        profile/opcode/frame-tick sections after it can be read from there.
        """
        idx = start
        n_displays = int(fields[idx])
        idx += 1
        result: list[list[list[str]]] = []
        for room_id in self.display_rooms[:n_displays]:
            room = self.rooms[room_id]
            width = room.max[0] - room.min[0] - 1
            height = room.max[1] - room.min[1] - 1
            n_frames = int(fields[idx])
            idx += 1
            frames: list[list[str]] = []
            for _ in range(n_frames):
                n_pixels = int(fields[idx])
                idx += 1
                pixels = fields[idx : idx + n_pixels]
                idx += n_pixels
                rows = [
                    "".join(format(int(p), "x") for p in pixels[row * width : (row + 1) * width])
                    for row in range(height)
                ]
                frames.append(rows)
            result.append(frames)
        return result, idx

    @staticmethod
    def _parse_frame_ticks(it: Iterator[str]) -> list[int]:
        if next(it, None) != "F":
            raise FastLittlemanError("native runner returned no frame-tick section")
        count = int(next(it))
        return [int(next(it)) for _ in range(count)]

    def _parse_profile(self, it: Iterator[str]) -> FastProfile:
        if next(it, None) != "P":
            raise FastLittlemanError("native runner returned no profile section")
        take = lambda: int(next(it))  # noqa: E731
        samples, stride, npipes = take(), take(), take()
        prof = FastProfile(width=self.width, height=self.height, samples=samples, stride=stride)
        for _ in range(npipes):
            prof.send.append(take())
            prof.recv.append(take())
            prof.send_blocked.append(take())
            prof.recv_blocked.append(take())
            prof.query.append(take())
            prof.pipe_wait.append(take())
        for _ in range(take()):
            x, y, hot, waiting = take(), take(), take(), take()
            prof.heat[(x, y)] = hot
            if waiting:
                prof.wait[(x, y)] = waiting
        return prof

    def _parse_opcodes(self, it: Iterator[str], spec: OpcodeTags) -> FastOpProfile:
        if next(it, None) != "Q":
            raise FastLittlemanError("native runner returned no opcode section")
        take = lambda: int(next(it))  # noqa: E731
        nops, nclass, npipes, samples, outside, multi = (take() for _ in range(6))
        prof = FastOpProfile(
            classes=list(spec.classes),
            ops=[*spec.ops, "(unattributed)"],
            samples=samples,
            outside=outside,
            multi=multi,
        )
        if nops != len(spec.ops) + 1 or nclass != len(spec.classes):
            raise FastLittlemanError("native runner returned a mismatched opcode section")
        prof.execs = [take() for _ in range(nops)]
        prof.ticks = [[take() for _ in range(nclass)] for _ in range(nops)]
        prof.blocked = [[take() for _ in range(nclass)] for _ in range(nops)]
        for _ in range(take()):
            op, pid, ticks, runs = take(), take(), take(), take()
            prof.pipe_ticks[(op, pid)] = ticks
            prof.pipe_runs[(op, pid)] = runs
        for _ in range(take()):
            op, length, n = take(), take(), take()
            prof.block_hist.setdefault(op, {})[length] = n
        for _ in range(take()):
            op, value, n = take(), take(), take()
            prof.values.setdefault(op, {})[value] = n
        del npipes
        return prof

    def _native_request(
        self,
        input_rounds: list[list[int]],
        expected_rounds: list[list[int]] | None,
        frame_specs: list[list[list[list[int]]] | None] | None,
        max_ticks: int,
        *,
        tiled: bool = False,
        profile: bool = False,
        profile_stride: int = 1,
        opcodes: OpcodeTags | None = None,
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
        # Do the judged panels form one logical screen?  It is the one thing the
        # engine cannot infer from the frame lists themselves, and it decides two
        # things: whether "logical frame *n* is complete" exists as an event (it
        # does for a wall — the slowest tile's *n*-th commit; it does not for
        # independent charts, whose *n*-th frames are unrelated), and therefore
        # whether round-gated input has a stream to gate against.
        values.append(1 if tiled else 0)
        # One block per display room, in reading order; the count itself is
        # not sent because the native side derives it from the room list
        # above, so it cannot desync from it.
        display_specs = frame_specs
        if display_specs is None:
            display_specs = [None] * len(self.display_rooms)
        for spec in display_specs:
            values.append(1 if spec is not None else 0)
            if spec is not None:
                values.append(len(spec))
                for round_frames in spec:
                    values.append(len(round_frames))
                    for frame in round_frames:
                        values.extend((len(frame), *frame))
        values.append(max_ticks)
        # Trailing and omitted when off, so a non-profiling request is exactly
        # the string every existing caller already sends.
        if profile:
            values.extend((1, max(1, profile_stride)))
            # Trailing again, for the same reason: the heatmap-only profiler's
            # request is unchanged by the existence of this section.
            if opcodes is not None:
                values.extend(
                    (
                        1,
                        len(opcodes.classes),
                        len(opcodes.ops),
                        opcodes.boundary,
                        opcodes.hist_pipe,
                        opcodes.value_pipe,
                        len(opcodes.tags),
                    )
                )
                for (x, y, direction), (cls, op) in opcodes.tags.items():
                    values.extend((y * self.width + x, direction, cls, op))
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

    def _frame_specs(
        self,
        frames: FrameSpec | Sequence[FrameSpec | None] | None,
        frame_tiles: tuple[int, int] | None,
    ) -> tuple[list[list[list[list[int]]] | None] | None, bool]:
        """Normalise ``frames`` to one optional round list per display, plus ``tiled``.

        Two different things can be behind more than one panel, and the engine is
        given the same shape for both — a frame list per display room, in reading
        order — because judging is per panel either way.  What differs is only
        whether the panels' *n*-th frames belong together:

        * ``frame_tiles=(cols, rows)`` — one logical screen.  The expected frame
          is cut into ``cols * rows`` tiles and dealt out to the panels that paint
          them, so logical frame *n* is complete when the slowest tile commits its
          *n*-th.  ``tiled`` is ``True`` and that event gates input rounds.
        * ``frame_tiles`` omitted — independent panels (or the single display every
          caller before them had).  Panel A's third frame and panel B's third
          frame are unrelated events, so there is no logical frame to gate on and
          ``tiled`` is ``False``.

        Neither subsumes the other, so a spec of the wrong depth raises here rather
        than being reinterpreted: a silent misread of this seam is the failure both
        conventions were written to prevent.
        """
        if frames is None:
            return None, False
        n_displays = len(self.display_rooms)
        if frame_tiles is not None or n_displays <= 1:
            if frame_tiles is not None and self._looks_per_display(frames):
                raise FastLittlemanError(
                    "frames= is nested one level too deep for frame_tiles=: a tiled "
                    "wall takes one logical frame stream (rounds, of frames, of row "
                    "strings) which is then cut into tiles, whereas a sequence of "
                    "per-display specs describes independent panels. The two are "
                    "different machines, not two spellings of one — drop frame_tiles= "
                    "to judge the panels independently, or compose one logical frame "
                    "per round to judge them as a single wall."
                )
            logical = self._parse_frame_rounds(frames, frame_tiles)
            assert logical is not None  # frames is not None
            return self._spread_tiles(logical, frame_tiles or (1, 1)), True
        return self._parse_frame_rounds_by_display(frames), False

    @staticmethod
    def _looks_per_display(frames: object) -> bool:
        """Is ``frames`` a sequence of per-display specs rather than one stream?

        A logical frame stream bottoms out in a row string three levels down
        (``frames[round][frame][row]``); a per-display sequence puts a whole spec
        where a round belongs, so the same descent lands one level short of the
        string.  ``None`` in a slot is decisive on its own — a round is never
        ``None``, an unjudged display is.
        """
        if isinstance(frames, str) or not isinstance(frames, Sequence):
            return False
        if any(item is None for item in frames):
            return True
        node: object = frames
        for _ in range(3):
            if isinstance(node, str) or not isinstance(node, Sequence) or not node:
                return False
            node = node[0]
        return not isinstance(node, str)

    @staticmethod
    def _spread_tiles(
        logical: list[list[list[int]]], frame_tiles: tuple[int, int]
    ) -> list[list[list[list[int]]] | None]:
        """Deal a tiled wall's logical frames out to the panels that paint them.

        :meth:`_parse_frame_rounds` has already cut each expected frame into
        ``cols * rows`` equal tiles laid end to end in reading order, which is the
        order the displays are discovered in, so this is a pure re-slicing: panel
        *i* is judged against tile *i* of every frame, on its own *i*-th commit.

        This is what lets one engine representation serve both abstractions. The
        native side never needs to know the panels form a screen, because tiling
        is entirely a statement about *expected content* — which pixels a panel
        owns — and not about the judging rule, which is per panel regardless. The
        one thing it does need is :meth:`_frame_specs`' ``tiled`` flag, for the
        slowest-tile event that has no counterpart on independent panels.
        """
        cols, rows = frame_tiles
        count = cols * rows
        per_display: list[list[list[list[int]]] | None] = []
        for tile in range(count):
            tile_rounds: list[list[list[int]]] = []
            for round_frames in logical:
                tile_frames: list[list[int]] = []
                for frame in round_frames:
                    size = len(frame) // count
                    tile_frames.append(frame[tile * size : (tile + 1) * size])
                tile_rounds.append(tile_frames)
            per_display.append(tile_rounds)
        return per_display

    def _parse_frame_rounds(
        self,
        frames: Sequence[Sequence[Sequence[str]]] | None,
        frame_tiles: tuple[int, int] | None = None,
    ) -> list[list[list[int]]] | None:
        """Expected frames as flat pixel lists, one tile after another.

        A single display is the ``(1, 1)`` case and the list is just the frame.
        A tiled wall is cut into ``cols * rows`` tiles in reading order and the
        tiles concatenated, which is the order the native runner indexes its
        displays in — the panels are discovered top-to-bottom, left-to-right.
        :meth:`_spread_tiles` then deals those tiles out to their panels.
        """
        if frames is None:
            return None
        display_ids = self.display_rooms
        cols, rows = frame_tiles or (1, 1)
        if cols < 1 or rows < 1:
            raise FastLittlemanError(f"frame_tiles must be positive, got {(cols, rows)}")
        if len(display_ids) != cols * rows:
            raise FastLittlemanError(
                f"display judging needs exactly {cols * rows} display(s) for "
                f"frame_tiles={(cols, rows)}, found {len(display_ids)}"
            )
        boxes = [self.rooms[rid] for rid in display_ids]
        sizes = {
            (room.max[0] - room.min[0] - 1, room.max[1] - room.min[1] - 1) for room in boxes
        }
        if len(sizes) != 1:
            raise FastLittlemanError(
                f"a tiled wall needs displays of one size, found {sorted(sizes)}"
            )
        tile_w, tile_h = sizes.pop()
        width, height = tile_w * cols, tile_h * rows
        parsed: list[list[list[int]]] = []
        for round_frames in frames:
            parsed_round: list[list[int]] = []
            for frame in round_frames:
                # ``isinstance(row, str)`` rather than ``str(row)``: stringifying a
                # row that is really a nested list is exactly how a mis-nested spec
                # gets judged against the wrong data instead of refused.
                if len(frame) != height or any(
                    not isinstance(row, str) or len(row) != width for row in frame
                ):
                    raise FastLittlemanError(f"expected frame is not {width}x{height}")
                try:
                    grid = [[int(ch, 16) for ch in row] for row in frame]
                except ValueError as exc:
                    raise FastLittlemanError("expected frame contains a non-hex color") from exc
                pixels: list[int] = []
                for tile in range(cols * rows):
                    y0, x0 = (tile // cols) * tile_h, (tile % cols) * tile_w
                    for y in range(y0, y0 + tile_h):
                        pixels.extend(grid[y][x0 : x0 + tile_w])
                parsed_round.append(pixels)
            parsed.append(parsed_round)
        return parsed

    def _parse_frame_rounds_by_display(
        self,
        frames: FrameSpec | Sequence[FrameSpec | None],
    ) -> list[list[list[list[int]]] | None]:
        """Parse ``frames`` into one optional round list per display, in reading order.

        A program with exactly one display keeps the original calling
        convention: ``frames`` is that display's own sequence of rounds
        directly, not wrapped in an extra list. A program with more display
        rooms takes one such sequence per room instead (``None`` for an
        unjudged display).

        Which shape applies is decided by ``len(self.display_rooms)`` — a static
        property of the parsed program — never by the shape or length of ``frames``
        itself. That still leaves one real hazard: for more than one display, a
        caller who passes the *old* single-display shape by mistake (a flat
        sequence of rounds meant for one display) is silently one level too
        shallow, and if that sequence happens to have exactly as many rounds as
        there are displays, the length check alone would not catch it — each round
        would be accepted as if it were a whole display's spec. Every level below
        is explicitly typed (frame rows are always ``str``; rounds and frames are
        never ``str``), so a mis-nested shape is caught here instead of quietly
        judging the wrong data.
        """
        display_ids = self.display_rooms
        if not display_ids:
            raise FastLittlemanError("display judging requires a display room")
        if len(display_ids) == 1:
            per_display: list[FrameSpec | None] = [frames]  # type: ignore[list-item]
        else:
            if isinstance(frames, str):
                raise FastLittlemanError(
                    "frames= must be one spec per display for a multi-display "
                    "program, not a single sequence"
                )
            per_display = list(frames)  # type: ignore[arg-type]
            if len(per_display) != len(display_ids):
                raise FastLittlemanError(
                    f"expected one frame spec per display ({len(display_ids)}), "
                    f"got {len(per_display)}"
                )
        parsed: list[list[list[list[int]]] | None] = []
        for room_id, spec in zip(display_ids, per_display, strict=True):
            if spec is None:
                parsed.append(None)
                continue
            if isinstance(spec, str):
                raise FastLittlemanError(
                    f"frame spec for display {room_id} is a string, not a sequence of rounds"
                )
            room = self.rooms[room_id]
            width = room.max[0] - room.min[0] - 1
            height = room.max[1] - room.min[1] - 1
            parsed_rounds: list[list[list[int]]] = []
            for round_frames in spec:
                if isinstance(round_frames, str):
                    raise FastLittlemanError(
                        f"frame spec for display {room_id} is nested one level too "
                        "shallow: expected a sequence of rounds, each a sequence of "
                        "frames, but found a bare row string where a round belongs "
                        "(the classic sign of passing a single-display spec to a "
                        "multi-display program whose round count happens to match "
                        "the display count)"
                    )
                parsed_round: list[list[int]] = []
                for frame in round_frames:
                    if isinstance(frame, str):
                        raise FastLittlemanError(
                            f"frame spec for display {room_id} is nested one level "
                            "too shallow: expected each round to be a sequence of "
                            "frames, each frame a sequence of row strings, but found "
                            "a bare row string where a frame belongs"
                        )
                    if len(frame) != height or any(
                        not isinstance(row, str) or len(row) != width for row in frame
                    ):
                        raise FastLittlemanError(f"expected frame is not {width}x{height}")
                    try:
                        pixels = [int(ch, 16) for row in frame for ch in row]
                    except ValueError as exc:
                        raise FastLittlemanError(
                            "expected frame contains a non-hex color"
                        ) from exc
                    parsed_round.append(pixels)
                parsed_rounds.append(parsed_round)
            parsed.append(parsed_rounds)
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
            _native=False,
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
    parser.add_argument("--tick-cap", type=int, default=None)
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
