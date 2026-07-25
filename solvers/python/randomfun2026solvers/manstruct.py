#!/usr/bin/env python3
"""Structural model of a ``.man`` grid — the substrate a *compactor* rewrites.

:mod:`manparse` recovers the **outer** structure (rooms, pipes, and which pipe
each send/recv binds to) using the reference engine as the oracle. This module
adds the two layers a compactor actually needs:

**Layer 0 — the cell lattice with directional passability.** Every cell gets a
:class:`Kind` and a transit table saying, for each of the four entry headings,
which heading the man *leaves* with (or ``None`` for "state-dependent" /
"blocked"). Two properties fall out of that table and they are what licenses
compaction:

* ``transparent`` — the man's heading survives the cell, so the cell is pure
  latency and may be shortened or lengthened at will;
* ``shareable`` — another transit lane may cross the cell **without changing any
  semantics**.

Only bare floor is both. That is exactly the observation that ``>    >`` is four
shareable floor cells between two unshareable steer glyphs: a north-south
corridor may cross the middle, but it may not cross either ``>`` — it would be
silently re-steered east, which is the single nastiest bug class in this
language, because the grid still loads and still runs.

**Layer 1 — blocks.** A block is a maximal 4-connected clump of live in-room
glyphs: one clump is one rigid body, because the man executes its cells in a
fixed order and adjacency *is* that order. Corridors (runs of floor between
blocks) are not blocks — they are the **slack** a compactor spends.

**Layer 2 — freedom.** Each block and pipe is labelled with how far it may be
disturbed, and *why*:

* :attr:`Freedom.FROZEN` — do not touch. The default for anything whose
  invariant we cannot prove, which is the conservative direction: an unprovable
  pipe keeps its length rather than deadlocking a ring.
* :attr:`Freedom.RIGID` — contents fixed, whole body may translate.
* :attr:`Freedom.ELASTIC` — length is free latency (corridors).
* :attr:`Freedom.SHRINKABLE` — has a computable floor above zero; a pipe holding
  299 values against a declared need of 101 may give back 198.

Nothing here mutates a grid. This module *describes*; the moves that rewrite are
validated against it by re-rendering and re-running the engine's ``analyze`` and
``route``, since a translated ``r`` can silently re-bind to a different pipe.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from html import escape
from pathlib import Path

from .manparse import PIPE_OP_GLYPHS, Program, parse_program

__all__ = [
    "Kind",
    "Freedom",
    "DIRS",
    "CellInfo",
    "Block",
    "PipeInfo",
    "RoomSlack",
    "CapacityHint",
    "Structure",
    "analyze_structure",
]

# Headings, y growing down, named so a transit table reads like the grid.
DIRS: dict[str, tuple[int, int]] = {"E": (1, 0), "W": (-1, 0), "N": (0, -1), "S": (0, 1)}
_STEER_EXIT = {">": "E", "<": "W", "^": "N", "v": "S"}

# SPEC §"Instruction set". `-` and `|` are *also* room walls and `> < ^ v` are
# *also* pipe glyphs, so classification is only sound with the room boxes in
# hand — which is why this module consumes a parsed Program rather than text.
_ARITH = set("+-*/N%&|~{}MW")
_PIPE_OPS = set("sSrRUq")
_BACKPACK = set("bm]")
_BRANCH = set("Xadx")  # direction depends on A or BP: unknowable statically
_HALT = set("H")
_WALLS = set("+-|:=")


class Kind(StrEnum):
    """What a cell *is*, once its room membership is known."""

    VOID = "void"  # outside every room and pipe: free space
    WALL = "wall"  # room border; entering it is fatal
    FLOOR = "floor"  # blank inside a room: transparent AND shareable
    OP = "op"  # executes and returns the man to his heading
    LITERAL = "literal"  # a backtick span (or a bare digit)
    STEER = "steer"  # > < ^ v : forces a heading unconditionally
    BRANCH = "branch"  # X a d x : heading depends on machine state
    HALT = "halt"
    PIPE = "pipe"  # a pipe cell: carries values, not the man
    IO = "io"  # the I / O marker of an IO room
    SPAWN = "spawn"  # @


class Freedom(StrEnum):
    """How far a compactor may disturb something, cheapest guarantee first."""

    FROZEN = "frozen"  # not safe to touch at all
    RIGID = "rigid"  # contents fixed; may translate as one body
    SHRINKABLE = "shrinkable"  # has a provable minimum above zero
    ELASTIC = "elastic"  # pure latency; any length


@dataclass(frozen=True)
class CellInfo:
    """One cell, classified, with its transit table."""

    x: int
    y: int
    glyph: str
    kind: Kind
    room: int | None = None
    pipe: int | None = None
    #: entry heading -> exit heading. A missing key is *blocked* (fatal to
    #: enter); a ``None`` value is "enterable but the exit is state-dependent".
    exits: dict[str, str | None] = field(default_factory=dict)

    @property
    def transparent(self) -> bool:
        """The man's heading survives every entry — so the cell is pure latency."""
        return bool(self.exits) and all(self.exits.get(d) == d for d in DIRS)

    @property
    def shareable(self) -> bool:
        """Another transit lane may cross without perturbing any semantics.

        True only for bare floor. An :attr:`Kind.OP` cell is *transparent* — the
        man keeps his heading — but crossing it would **execute** it, so it is
        not shareable. That distinction is the whole game.
        """
        return self.kind is Kind.FLOOR

    def crossable_by(self, heading: str) -> bool:
        """Could a lane heading `heading` pass through and come out unchanged?"""
        return self.exits.get(heading) == heading


@dataclass
class Block:
    """A 4-connected clump of live in-room glyphs: one rigid body.

    Adjacency is execution order, so a clump cannot be split, only moved. The
    shape tells us which kind of body it is: a single row or column is a
    straight code run, anything else is a gadget (a counted loop occupies two
    columns and is therefore never a line).
    """

    id: int
    room: int
    cells: list[tuple[int, int]]
    text: str
    shape: str  # "run-h" | "run-v" | "cell" | "gadget"
    freedom: Freedom
    reason: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        xs = [x for x, _ in self.cells]
        ys = [y for _, y in self.cells]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def size(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self.bbox
        return x1 - x0 + 1, y1 - y0 + 1


@dataclass(frozen=True)
class CapacityHint:
    """A declared minimum number of values a set of pipes must jointly hold.

    Capacity is *program semantics*, not geometry: only the author knows that a
    100-cell tape ring needs 101 slots to rotate without deadlocking. Pipes with
    no hint stay :attr:`Freedom.FROZEN`, so silence never shortens a ring.
    """

    pipes: tuple[int, ...]
    need: int
    note: str = ""


@dataclass
class PipeInfo:
    """A pipe with its capacity budget."""

    id: int
    length: int
    src: int
    dst: int
    freedom: Freedom
    need: int | None = None
    group: tuple[int, ...] = ()
    group_have: int | None = None
    reason: str = ""

    @property
    def slack(self) -> int | None:
        """Slots the pipe's **group** could give back, or ``None`` if undeclared.

        Group-level on purpose: capacity is a property of the whole ring, so a
        two-pipe ring of 113 + 186 against a need of 101 has *one* surplus of
        198, not a +12 and a +85 that would double-count if summed.
        """
        if self.need is None or self.group_have is None:
            return None
        return self.group_have - self.need


@dataclass
class RoomSlack:
    """Interior rows and columns of a room that hold no live glyph at all.

    Deleting one is always sound *for the man*: nothing occupies it, so no block
    is split, and the corridors threading it merely get shorter — a corridor's
    length is latency, never meaning. The risk is entirely outside the room:
    every wall port shifts, so pipe geometry and nearest-pipe bindings have to be
    re-checked after the cut. That is a validation step, not a reason to skip it.
    """

    room: int
    width: int
    height: int
    live: int
    blank_rows: list[int]  # room-local interior coordinates
    blank_cols: list[int]

    @property
    def occupancy(self) -> float:
        return self.live / (self.width * self.height) if self.width and self.height else 0.0

    @property
    def tight(self) -> tuple[int, int]:
        """Interior size with every fully blank row and column removed."""
        return self.width - len(self.blank_cols), self.height - len(self.blank_rows)


@dataclass
class Structure:
    """The full structural read of one grid."""

    program: Program
    cells: dict[tuple[int, int], CellInfo]
    blocks: list[Block]
    pipes: list[PipeInfo]
    slack: list[RoomSlack]

    @property
    def bbox(self) -> tuple[int, int]:
        return self.program.width, self.program.height

    @property
    def geometry_factor(self) -> int:
        """``max(w,h)**2`` — the multiplier the contest applies to average ticks."""
        w, h = self.bbox
        return max(w, h) ** 2

    def frozen(self) -> list[Block]:
        return [b for b in self.blocks if b.freedom is Freedom.FROZEN]

    def movable(self) -> list[Block]:
        return [b for b in self.blocks if b.freedom is not Freedom.FROZEN]

    def pipe_slack(self) -> int:
        """Total pipe cells provably surplus to the declared capacity needs."""
        seen: set[tuple[int, ...]] = set()
        total = 0
        for p in self.pipes:
            if p.need is None or p.group in seen:
                continue
            seen.add(p.group)
            have = sum(q.length for q in self.pipes if q.id in p.group)
            total += max(0, have - p.need)
        return total


# ── layer 0: classify every cell ─────────────────────────────────────────────
def _exits_for(kind: Kind, glyph: str) -> dict[str, str | None]:
    """The transit table: entry heading -> exit heading.

    Absent key = blocked (fatal). ``None`` = enterable, exit state-dependent.
    """
    if kind in (Kind.WALL, Kind.PIPE):
        return {}  # the man may never be here
    if kind is Kind.HALT:
        return dict.fromkeys(DIRS)  # enterable, never leaves
    if kind is Kind.STEER:
        out = _STEER_EXIT[glyph]
        return {d: out for d in DIRS}
    if kind is Kind.BRANCH:
        return dict.fromkeys(DIRS)
    # floor, op, literal, spawn, io, void: heading survives
    return {d: d for d in DIRS}


def _classify_glyph(glyph: str, *, on_border: bool) -> Kind:
    if on_border:
        return Kind.IO if glyph in "IO" else Kind.WALL
    if glyph == " ":
        return Kind.FLOOR
    if glyph == "@":
        return Kind.SPAWN
    if glyph in "IO":
        return Kind.IO
    if glyph in _STEER_EXIT:
        return Kind.STEER
    if glyph in _BRANCH:
        return Kind.BRANCH
    if glyph in _HALT:
        return Kind.HALT
    if glyph.isdigit() or glyph == "`":
        return Kind.LITERAL
    if glyph in _ARITH | _PIPE_OPS | _BACKPACK:
        return Kind.OP
    return Kind.OP  # unknown-but-live: treat as executing, never as free space


def _build_cells(program: Program) -> dict[tuple[int, int], CellInfo]:
    cells: dict[tuple[int, int], CellInfo] = {}
    pipe_of: dict[tuple[int, int], int] = {}
    pipe_glyph: dict[tuple[int, int], str] = {}
    for p in program.pipes:
        for c, g in zip(p.cells, p.glyphs, strict=True):
            pipe_of[c] = p.id
            pipe_glyph[c] = g

    for room in program.rooms:
        ox, oy = room.min_
        for dy, row in enumerate(room.content):
            for dx, glyph in enumerate(row):
                x, y = ox + dx, oy + dy
                border = dx in (0, room.width - 1) or dy in (0, room.height - 1)
                kind = _classify_glyph(glyph, on_border=border)
                cells[(x, y)] = CellInfo(
                    x, y, glyph, kind, room=room.id, exits=_exits_for(kind, glyph)
                )

    for c, pid in pipe_of.items():
        cells[c] = CellInfo(
            c[0], c[1], pipe_glyph[c], Kind.PIPE, pipe=pid, exits=_exits_for(Kind.PIPE, "")
        )
    return cells


# ── layer 1: blocks ──────────────────────────────────────────────────────────
def _live_in_room(cells: dict[tuple[int, int], CellInfo]) -> set[tuple[int, int]]:
    """In-room cells the man executes: everything but floor and the walls."""
    dead = {Kind.FLOOR, Kind.WALL, Kind.PIPE, Kind.VOID}
    return {c for c, info in cells.items() if info.room is not None and info.kind not in dead}


def _components(live: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """4-connected clumps, each returned in reading order."""
    seen: set[tuple[int, int]] = set()
    out: list[list[tuple[int, int]]] = []
    for start in sorted(live, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        clump: list[tuple[int, int]] = []
        q = deque([start])
        seen.add(start)
        while q:
            x, y = q.popleft()
            clump.append((x, y))
            for dx, dy in DIRS.values():
                n = (x + dx, y + dy)
                if n in live and n not in seen:
                    seen.add(n)
                    q.append(n)
        out.append(sorted(clump, key=lambda c: (c[1], c[0])))
    return out


def _shape_of(clump: list[tuple[int, int]]) -> str:
    if len(clump) == 1:
        return "cell"
    if len({y for _, y in clump}) == 1:
        return "run-h"
    if len({x for x, _ in clump}) == 1:
        return "run-v"
    return "gadget"


def _text_of(
    clump: list[tuple[int, int]], cells: dict[tuple[int, int], CellInfo], shape: str
) -> str:
    if shape == "run-v":
        ordered = sorted(clump, key=lambda c: c[1])
    else:
        ordered = sorted(clump, key=lambda c: (c[1], c[0]))
    return "".join(cells[c].glyph for c in ordered)


def _mirror_safe(text: str) -> bool:
    """Would walking this run backwards compute the same thing?

    A backtick literal is read at its closing tick, so ```100``` walked
    westward loads 001 — a different number. Only a palindromic literal survives
    a mirror, so any run carrying a non-palindromic one is not mirror-safe.
    """
    spans = text.split("`")
    if len(spans) == 1:
        return True
    # odd indices are literal interiors
    return all(s.replace(" ", "") == s.replace(" ", "")[::-1] for s in spans[1::2])


def _block_freedom(text: str, shape: str) -> tuple[Freedom, str, list[str]]:
    tags: list[str] = []
    reasons: list[str] = []
    if any(g in PIPE_OP_GLYPHS for g in text):
        tags.append("binding-critical")
        reasons.append(
            "carries a pipe op: `s`/`r` bind to the *nearest* pipe, so any move "
            "can silently re-bind it — re-run route() and diff"
        )
    if not _mirror_safe(text):
        tags.append("no-mirror")
        reasons.append("holds a non-palindromic literal: reversing it changes the value")
    if shape == "gadget":
        tags.append("gadget")
        reasons.append("multi-column body (a counted loop): rigid, translate only")
    return Freedom.RIGID, "; ".join(reasons) or "straight code run: translate freely", tags


def _build_blocks(program: Program, cells: dict[tuple[int, int], CellInfo]) -> list[Block]:
    blocks: list[Block] = []
    room_of = {r.id: r for r in program.rooms}
    for i, clump in enumerate(_components(_live_in_room(cells))):
        shape = _shape_of(clump)
        text = _text_of(clump, cells, shape)
        rid = cells[clump[0]].room
        assert rid is not None
        freedom, reason, tags = _block_freedom(text, shape)
        # An IO room is mandated 3x3 and holds only its marker; a display's
        # geometry is the problem's, not the program's. Neither may be reshaped.
        kind = room_of[rid].kind
        if kind in ("input", "output", "display"):
            freedom = Freedom.FROZEN
            reason = f"{kind} room: shape is fixed by SPEC, not by us"
            tags = [*tags, kind]
        blocks.append(Block(i, rid, clump, text, shape, freedom, reason, tags))
    return blocks


# ── layer 2: pipes and room slack ────────────────────────────────────────────
def _build_pipes(program: Program, hints: list[CapacityHint]) -> list[PipeInfo]:
    by_pipe: dict[int, CapacityHint] = {}
    for h in hints:
        for pid in h.pipes:
            by_pipe[pid] = h
    out: list[PipeInfo] = []
    for p in program.pipes:
        h = by_pipe.get(p.id)
        if h is None:
            out.append(
                PipeInfo(
                    p.id, len(p.cells), p.src, p.dst, Freedom.FROZEN,
                    reason="no declared capacity: shortening it could deadlock a ring",
                )
            )
            continue
        have = sum(len(q.cells) for q in program.pipes if q.id in h.pipes)
        surplus = have - h.need
        out.append(
            PipeInfo(
                p.id, len(p.cells), p.src, p.dst,
                Freedom.SHRINKABLE if surplus > 0 else Freedom.FROZEN,
                need=h.need, group=h.pipes, group_have=have,
                reason=(
                    f"group {h.pipes} holds {have} against a need of {h.need}: "
                    f"{surplus} slots surplus. {h.note}".strip()
                    if surplus > 0
                    else f"group {h.pipes} is at its {h.need}-slot minimum. {h.note}".strip()
                ),
            )
        )
    return out


def _build_slack(program: Program, cells: dict[tuple[int, int], CellInfo]) -> list[RoomSlack]:
    out: list[RoomSlack] = []
    for room in program.rooms:
        inner = [row[1:-1] for row in room.content[1:-1]]
        if not inner or not inner[0]:
            continue
        h, w = len(inner), len(inner[0])
        live = sum(1 for row in inner for ch in row if ch != " ")
        blank_rows = [y for y in range(h) if not inner[y].strip()]
        blank_cols = [x for x in range(w) if all(inner[y][x] == " " for y in range(h))]
        out.append(RoomSlack(room.id, w, h, live, blank_rows, blank_cols))
    return out


def analyze_structure(
    program: str | os.PathLike[str] | Program,
    *,
    capacity: list[CapacityHint] | None = None,
) -> Structure:
    """Build the three-layer structural model of a grid.

    `capacity` declares, per pipe group, how many values must stay in flight.
    Anything undeclared is reported :attr:`Freedom.FROZEN`.
    """
    prog = program if isinstance(program, Program) else parse_program(program)
    cells = _build_cells(prog)
    return Structure(
        program=prog,
        cells=cells,
        blocks=_build_blocks(prog, cells),
        pipes=_build_pipes(prog, capacity or []),
        slack=_build_slack(prog, cells),
    )


# ── reporting ────────────────────────────────────────────────────────────────
def to_text(s: Structure) -> str:
    w, h = s.bbox
    out = [
        f"grid {w}x{h}   geometry factor max(w,h)^2 = {s.geometry_factor:,}",
        f"rooms {len(s.program.rooms)}  pipes {len(s.program.pipes)}  blocks {len(s.blocks)}",
        "",
        "ROOM SLACK  (fully blank interior rows/cols hold nothing: deletable)",
    ]
    for r in s.slack:
        room = s.program.rooms_by_id[r.room]
        tw, th = r.tight
        out.append(
            f"  room{r.room} {room.kind:7s} {r.width:3d}x{r.height:<3d} "
            f"live {r.live:4d} ({r.occupancy*100:4.1f}%)  "
            f"blank rows {len(r.blank_rows):3d} cols {len(r.blank_cols):3d}  "
            f"-> tight {tw}x{th}"
        )
    out += ["", "PIPES  (length IS capacity: a value occupies one cell per tick)"]
    for p in s.pipes:
        slack = "" if p.slack is None else f" group slack {p.slack:+d}"
        out.append(
            f"  pipe{p.id:<2d} len {p.length:3d}  room{p.src}->room{p.dst}  "
            f"{p.freedom.value:11s}{slack}  {p.reason}"
        )
    out += ["", "BLOCKS  (4-connected clump = one rigid body; adjacency is execution order)"]
    for b in sorted(s.blocks, key=lambda b: (b.room, b.bbox[1], b.bbox[0])):
        x0, y0, _, _ = b.bbox
        bw, bh = b.size
        tags = f" [{','.join(b.tags)}]" if b.tags else ""
        out.append(
            f"  b{b.id:<3d} room{b.room} ({x0:3d},{y0:3d}) {bw:2d}x{bh:<2d} "
            f"{b.shape:7s} {b.freedom.value:11s} {b.text!r}{tags}"
        )
    frozen = len(s.frozen())
    out += [
        "",
        f"summary: {len(s.blocks) - frozen} movable blocks, {frozen} frozen, "
        f"{s.pipe_slack()} surplus pipe cells",
    ]
    return "\n".join(out)


_KIND_COLOR = {
    Kind.VOID: "#f8fafc",
    Kind.WALL: "#475569",
    Kind.FLOOR: "#e2e8f0",
    Kind.OP: "#2563eb",
    Kind.LITERAL: "#7c3aed",
    Kind.STEER: "#ea580c",
    Kind.BRANCH: "#db2777",
    Kind.HALT: "#111827",
    Kind.PIPE: "#059669",
    Kind.IO: "#0891b2",
    Kind.SPAWN: "#ca8a04",
}
_FREEDOM_COLOR = {
    Freedom.FROZEN: "#dc2626",
    Freedom.RIGID: "#2563eb",
    Freedom.SHRINKABLE: "#d97706",
    Freedom.ELASTIC: "#059669",
}


def to_html(s: Structure, *, title: str = "structural map") -> str:
    """A cell-accurate map: kind by colour, freedom by overlay, both labelled."""
    w, h = s.bbox
    block_at: dict[tuple[int, int], Block] = {}
    for b in s.blocks:
        for c in b.cells:
            block_at[c] = b

    rows = []
    for y in range(h):
        tds = []
        for x in range(w):
            info = s.cells.get((x, y))
            if info is None:
                info = CellInfo(x, y, " ", Kind.VOID, exits=_exits_for(Kind.VOID, " "))
            b = block_at.get((x, y))
            color = _KIND_COLOR[info.kind]
            ring = ""
            if b is not None:
                ring = f"box-shadow:inset 0 0 0 1.5px {_FREEDOM_COLOR[b.freedom]};"
            elif info.kind is Kind.PIPE:
                pi = next((p for p in s.pipes if p.id == info.pipe), None)
                if pi is not None:
                    ring = f"box-shadow:inset 0 0 0 1.5px {_FREEDOM_COLOR[pi.freedom]};"
            glyph = escape(info.glyph) if info.glyph != " " else "&nbsp;"
            fg = "#f8fafc" if info.kind in (Kind.WALL, Kind.HALT) else "#0f172a"
            tip = f"({x},{y}) {info.kind.value}"
            if info.kind is Kind.FLOOR:
                tip += " · shareable: a crossing lane is safe here"
            elif info.kind is Kind.STEER:
                tip += f" · forces {_STEER_EXIT[info.glyph]}: NOT crossable off-axis"
            if b is not None:
                tip += f" · b{b.id} {b.freedom.value} {b.text!r}"
            if info.kind is Kind.PIPE:
                tip += f" · pipe{info.pipe}"
            tds.append(
                f'<i style="background:{color};color:{fg};{ring}" title="{escape(tip)}">{glyph}</i>'
            )
        rows.append("".join(tds))

    legend = "".join(
        f'<span><i style="background:{c}"></i>{k.value}</span>' for k, c in _KIND_COLOR.items()
    )
    freedom_legend = "".join(
        f'<span><i style="box-shadow:inset 0 0 0 2px {c};background:#fff"></i>{f.value}</span>'
        for f, c in _FREEDOM_COLOR.items()
    )
    slack_rows = "".join(
        f"<tr><td>room{r.room}</td><td>{s.program.rooms_by_id[r.room].kind}</td>"
        f"<td>{r.width}×{r.height}</td><td>{r.live}</td><td>{r.occupancy*100:.1f}%</td>"
        f"<td>{len(r.blank_rows)}</td><td>{len(r.blank_cols)}</td>"
        f"<td><b>{r.tight[0]}×{r.tight[1]}</b></td></tr>"
        for r in s.slack
    )
    pipe_rows = "".join(
        f"<tr><td>pipe{p.id}</td><td>{p.length}</td>"
        f"<td>{'' if p.need is None else p.need}</td>"
        f"<td>{'' if p.slack is None else format(p.slack, '+d')}</td>"
        f'<td style="color:{_FREEDOM_COLOR[p.freedom]}"><b>{p.freedom.value}</b></td>'
        f"<td>{escape(p.reason)}</td></tr>"
        for p in s.pipes
    )
    block_rows = "".join(
        f"<tr><td>b{b.id}</td><td>room{b.room}</td><td>{b.bbox[0]},{b.bbox[1]}</td>"
        f"<td>{b.size[0]}×{b.size[1]}</td><td>{b.shape}</td>"
        f'<td style="color:{_FREEDOM_COLOR[b.freedom]}"><b>{b.freedom.value}</b></td>'
        f"<td><code>{escape(b.text)}</code></td><td>{escape(', '.join(b.tags))}</td>"
        f"<td>{escape(b.reason)}</td></tr>"
        for b in sorted(s.blocks, key=lambda b: (b.room, b.bbox[1], b.bbox[0]))
    )
    grid = "\n".join(f"<div>{r}</div>" for r in rows)
    return f"""<!doctype html>
<meta charset="utf-8"><title>{escape(title)}</title>
<style>
 :root{{color-scheme:light dark}}
 body{{font:14px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0;padding:24px;
   background:#fff;color:#0f172a}}
 h1{{font-size:19px;margin:0 0 4px}} h2{{font-size:15px;margin:28px 0 8px}}
 .sub{{color:#64748b;margin:0 0 18px}}
 #grid{{font:11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre;
   overflow:auto;border:1px solid #cbd5e1;border-radius:8px;padding:8px;background:#fff}}
 #grid i{{display:inline-block;width:11px;height:14px;text-align:center;font-style:normal}}
 .legend{{display:flex;flex-wrap:wrap;gap:14px;margin:10px 0 0;color:#475569;font-size:12px}}
 .legend span{{display:flex;align-items:center;gap:5px}}
 .legend i{{width:12px;height:12px;border-radius:3px;display:inline-block}}
 table{{border-collapse:collapse;font-size:12px;width:100%}}
 th,td{{text-align:left;padding:4px 8px;border-bottom:1px solid #e2e8f0;vertical-align:top}}
 th{{color:#64748b;font-weight:600}}
 code{{background:#f1f5f9;padding:1px 4px;border-radius:3px}}
 @media(prefers-color-scheme:dark){{
   body{{background:#0b1120;color:#e2e8f0}} #grid{{background:#0f172a;border-color:#334155}}
   th{{color:#94a3b8}} td{{border-color:#1e293b}} code{{background:#1e293b}}
   .sub,.legend{{color:#94a3b8}}}}
</style>
<h1>{escape(title)}</h1>
<p class="sub">grid <b>{w}×{h}</b> · geometry factor max(w,h)² =
 <b>{s.geometry_factor:,}</b> · {len(s.blocks)} blocks
 ({len(s.movable())} movable, {len(s.frozen())} frozen) ·
 <b>{s.pipe_slack()}</b> surplus pipe cells</p>
<div id="grid">{grid}</div>
<div class="legend">{legend}</div>
<div class="legend">{freedom_legend}</div>
<h2>Room slack — blank interior rows and columns hold nothing and are deletable</h2>
<table><tr><th>room</th><th>kind</th><th>interior</th><th>live</th><th>occupancy</th>
<th>blank rows</th><th>blank cols</th><th>tight</th></tr>{slack_rows}</table>
<h2>Pipes — length <em>is</em> capacity</h2>
<table><tr><th>pipe</th><th>len</th><th>need</th><th>slack</th><th>freedom</th>
<th>why</th></tr>{pipe_rows}</table>
<h2>Blocks — a 4-connected clump is one rigid body</h2>
<table><tr><th>id</th><th>room</th><th>at</th><th>size</th><th>shape</th><th>freedom</th>
<th>glyphs</th><th>tags</th><th>why</th></tr>{block_rows}</table>
"""


def to_json(s: Structure) -> str:
    return json.dumps(
        {
            "width": s.bbox[0],
            "height": s.bbox[1],
            "geometry_factor": s.geometry_factor,
            "pipe_slack": s.pipe_slack(),
            "rooms": [
                {
                    "id": r.room,
                    "kind": s.program.rooms_by_id[r.room].kind,
                    "interior": [r.width, r.height],
                    "live": r.live,
                    "blank_rows": r.blank_rows,
                    "blank_cols": r.blank_cols,
                    "tight": list(r.tight),
                }
                for r in s.slack
            ],
            "pipes": [
                {
                    "id": p.id, "length": p.length, "src": p.src, "dst": p.dst,
                    "freedom": p.freedom.value, "need": p.need, "slack": p.slack,
                    "group": list(p.group), "reason": p.reason,
                }
                for p in s.pipes
            ],
            "blocks": [
                {
                    "id": b.id, "room": b.room, "bbox": list(b.bbox), "size": list(b.size),
                    "shape": b.shape, "text": b.text, "freedom": b.freedom.value,
                    "tags": b.tags, "reason": b.reason,
                }
                for b in s.blocks
            ],
        },
        indent=2,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path)
    ap.add_argument(
        "--capacity",
        action="append",
        default=[],
        metavar="PIPES=NEED",
        help="declare a pipe group's minimum, e.g. --capacity 4,5=101",
    )
    ap.add_argument("--html", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    hints = []
    for spec in args.capacity:
        pipes, need = spec.split("=")
        hints.append(CapacityHint(tuple(int(p) for p in pipes.split(",")), int(need)))

    s = analyze_structure(args.grid, capacity=hints)
    if args.html:
        args.html.write_text(to_html(s, title=args.grid.name), encoding="utf-8")
    if args.json:
        args.json.write_text(to_json(s), encoding="utf-8")
    print(to_text(s))


if __name__ == "__main__":
    main()
