"""Parse a littleman ``.man`` grid into structural blocks — the inverse of
:mod:`layout`.

:func:`layout.layout_graph` goes *graph → ASCII*; this module goes *ASCII →
graph* so an optimizer can re-place rooms and re-route pipes. It does not parse
the grid itself — the reference engine's ``analyze``/``route`` (surfaced by
:class:`~randomfun2026solvers.littleman.Littleman`) is the source of truth for
room boxes, pipe geometry + connectivity, and which pipe a send/recv binds to.
This module overlays the raw grid text to recover room *contents* and classifies
each room (compute / input / output / display).

The resulting :class:`Program`:

* round-trips: :meth:`Program.to_grid` reproduces the source (canonicalised —
  trailing whitespace, which is semantically inert outside a room, is dropped);
* converts to a :class:`layout.Graph` via :meth:`Program.to_graph`, with every
  pipe's room-attach cell exposed as a container port, so the existing
  placement/routing strategies can re-synthesise a tighter grid.

The one semantic hazard a re-layout must respect — a send/recv binding to a
*different* pipe (SPEC "nearest, not nearest-ready") — is captured per
instruction cell in :attr:`Room.pipe_ops` (from the ``route`` oracle) so the
optimizer can verify bindings are preserved.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .layout import Cell, Container, Edge, Graph
from .littleman import Littleman

__all__ = [
    "SEND_GLYPHS",
    "RECV_GLYPHS",
    "PIPE_OP_GLYPHS",
    "PipeOp",
    "Room",
    "Pipe",
    "Program",
    "parse_program",
]

# Instruction glyphs that bind to a pipe (SPEC §"Pipes"): s/S send over an
# outgoing pipe, r/R/U/q operate on an incoming pipe.
SEND_GLYPHS = frozenset("sS")
RECV_GLYPHS = frozenset("rRUq")
PIPE_OP_GLYPHS = SEND_GLYPHS | RECV_GLYPHS

# Room classification by interior label / wall style.
_DISPLAY_WALLS = frozenset("=:")


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


# ── models ────────────────────────────────────────────────────────────────────
class PipeOp(_Model):
    """A pipe-binding instruction cell and the pipe it resolves to.

    ``pipe_id`` indexes :attr:`Program.pipes` (``-1`` if it binds to no pipe —
    e.g. a ``no-pipe`` fatal waiting to happen, preserved verbatim).
    """

    cell: Cell
    glyph: str
    pipe_id: int = -1


class Room(_Model):
    """A room block: its box, exact char content, class, spawn, and pipe-op cells."""

    id: int
    min_: Cell = Field(alias="min")
    max_: Cell = Field(alias="max")
    content: list[str] = Field(default_factory=list)
    kind: str = "compute"  # compute | input | output | display
    spawn: Cell | None = None
    pipe_ops: list[PipeOp] = Field(default_factory=list)

    @property
    def width(self) -> int:
        return self.max_[0] - self.min_[0] + 1

    @property
    def height(self) -> int:
        return self.max_[1] - self.min_[1] + 1


class Pipe(_Model):
    """A pipe: its ordered cells + glyphs, flow dirs, and the rooms it links."""

    id: int
    cells: list[Cell] = Field(default_factory=list)
    glyphs: list[str] = Field(default_factory=list)
    dirs: list[Cell] = Field(default_factory=list)
    src: int  # source room id
    dst: int  # destination room id

    @property
    def src_attach(self) -> Cell:
        """The source room's border cell this pipe leaves from."""
        c0, d0 = self.cells[0], self.dirs[0]
        return (c0[0] - d0[0], c0[1] - d0[1])

    @property
    def dst_attach(self) -> Cell:
        """The destination room's border cell this pipe enters."""
        cn, dn = self.cells[-1], self.dirs[-1]
        return (cn[0] + dn[0], cn[1] + dn[1])


class Program(_Model):
    """A parsed program: rooms + pipes over a ``width × height`` grid."""

    width: int
    height: int
    rooms: list[Room] = Field(default_factory=list)
    pipes: list[Pipe] = Field(default_factory=list)
    # Best-effort passthrough of any display boxes analyze reports (rare).
    displays: list[dict] = Field(default_factory=list)

    @property
    def has_display(self) -> bool:
        return bool(self.displays) or any(r.kind == "display" for r in self.rooms)

    def to_grid(self) -> list[str]:
        """Render back to grid rows (trailing whitespace dropped — inert padding)."""
        cells: dict[Cell, str] = {}
        for room in self.rooms:
            ox, oy = room.min_
            for dy, row in enumerate(room.content):
                for dx, ch in enumerate(row):
                    if ch != " ":
                        cells[(ox + dx, oy + dy)] = ch
        for pipe in self.pipes:
            for (x, y), ch in zip(pipe.cells, pipe.glyphs, strict=True):
                cells[(x, y)] = ch
        if not cells:
            return []
        max_y = max(y for _, y in cells)
        max_x = max(x for x, _ in cells)
        rows: list[str] = []
        for y in range(max_y + 1):
            line = "".join(cells.get((x, y), " ") for x in range(max_x + 1))
            rows.append(line.rstrip())
        return rows

    def render(self) -> str:
        return "\n".join(self.to_grid())

    def to_graph(self) -> Graph:
        """Convert to a :class:`layout.Graph` for re-placement / re-routing.

        Each room becomes a :class:`layout.Container` whose ports are the pipe
        **attach cells** (source borders → outputs, destination borders →
        inputs), in local coordinates. Each pipe becomes an :class:`layout.Edge`
        wiring the matching output→input port indices.
        """
        outs: dict[int, list[Cell]] = {r.id: [] for r in self.rooms}
        ins: dict[int, list[Cell]] = {r.id: [] for r in self.rooms}
        out_idx: dict[int, int] = {}
        in_idx: dict[int, int] = {}
        for pipe in self.pipes:
            sx, sy = pipe.src_attach
            smin = self.rooms_by_id[pipe.src].min_
            out_idx[pipe.id] = len(outs[pipe.src])
            outs[pipe.src].append((sx - smin[0], sy - smin[1]))
            dx, dy = pipe.dst_attach
            dmin = self.rooms_by_id[pipe.dst].min_
            in_idx[pipe.id] = len(ins[pipe.dst])
            ins[pipe.dst].append((dx - dmin[0], dy - dmin[1]))

        containers = [
            Container(
                id=str(r.id),
                width=r.width,
                height=r.height,
                content=r.content,
                inputs=ins[r.id],
                outputs=outs[r.id],
            )
            for r in self.rooms
        ]
        edges = [
            Edge(
                id=f"p{pipe.id}",
                src=str(pipe.src),
                src_output=out_idx[pipe.id],
                dst=str(pipe.dst),
                dst_input=in_idx[pipe.id],
            )
            for pipe in self.pipes
        ]
        return Graph(containers=containers, edges=edges)

    @property
    def rooms_by_id(self) -> dict[int, Room]:
        return {r.id: r for r in self.rooms}


# ── parsing ─────────────────────────────────────────────────────────────────
def _read_rows(program: str | os.PathLike[str]) -> list[str]:
    """Grid rows from a path or inline source (drop one trailing newline)."""
    if isinstance(program, os.PathLike):
        text = Path(os.fspath(program)).read_text(encoding="utf-8")
    elif "\n" not in program and Path(program).is_file():
        text = Path(program).read_text(encoding="utf-8")
    else:
        text = program
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


def _char(rows: list[str], x: int, y: int) -> str:
    if 0 <= y < len(rows) and 0 <= x < len(rows[y]):
        return rows[y][x]
    return " "


def _classify(rows: list[str], mn: Cell, mx: Cell) -> str:
    """Room kind from its interior label / wall glyphs."""
    # Display walls use '='/':'; a normal room uses '-'/'|'.
    for x in range(mn[0], mx[0] + 1):
        if _char(rows, x, mn[1]) in _DISPLAY_WALLS or _char(rows, x, mx[1]) in _DISPLAY_WALLS:
            return "display"
    for y in range(mn[1] + 1, mx[1]):
        for x in range(mn[0] + 1, mx[0]):
            ch = _char(rows, x, y)
            if ch == "I":
                return "input"
            if ch == "O":
                return "output"
    return "compute"


def parse_program(
    program: str | os.PathLike[str], *, lm: Littleman | None = None
) -> Program:
    """Parse a ``.man`` grid into a :class:`Program` using the engine's analysis."""
    lm = lm or Littleman()
    rows = _read_rows(program)
    # analyze/route need a concrete file; pass the rendered source as inline text
    # only once (Littleman writes a temp file per call), so hand it a Path when we
    # already have one.
    src: str | os.PathLike[str]
    if isinstance(program, os.PathLike) or ("\n" not in program and Path(program).is_file()):
        src = Path(os.fspath(program))
    else:
        src = "\n".join(rows)

    info = lm.analyze(src)

    # Cell → owning room id, for binding pipe-op cells and validating routes.
    rooms: list[Room] = []
    for rid, box in enumerate(info.rooms):
        mn = box.min_.as_tuple()
        mx = box.max_.as_tuple()
        content = [
            "".join(_char(rows, x, y) for x in range(mn[0], mx[0] + 1))
            for y in range(mn[1], mx[1] + 1)
        ]
        spawn: Cell | None = None
        for y in range(mn[1], mx[1] + 1):
            for x in range(mn[0], mx[0] + 1):
                if _char(rows, x, y) == "@":
                    spawn = (x, y)
        rooms.append(
            Room(
                id=rid,
                min=mn,
                max=mx,
                content=content,
                kind=_classify(rows, mn, mx),
                spawn=spawn,
            )
        )

    pipes: list[Pipe] = []
    pipe_cell_owner: dict[Cell, int] = {}
    for pid, geom in enumerate(info.pipes):
        cells = [seg.pos.as_tuple() for seg in geom.path]
        dirs = [seg.dir.as_tuple() for seg in geom.path]
        glyphs = [_char(rows, x, y) for (x, y) in cells]
        pipes.append(
            Pipe(
                id=pid,
                cells=cells,
                glyphs=glyphs,
                dirs=dirs,
                src=geom.src if geom.src is not None else -1,
                dst=geom.dst if geom.dst is not None else -1,
            )
        )
        for c in cells:
            pipe_cell_owner[c] = pid

    # Bind each send/recv instruction cell to its target pipe via route().
    for room in rooms:
        mn, mx = room.min_, room.max_
        for y in range(mn[1] + 1, mx[1]):
            for x in range(mn[0] + 1, mx[0]):
                glyph = _char(rows, x, y)
                if glyph not in PIPE_OP_GLYPHS:
                    continue
                targeted = lm.route(src, x, y)
                pipe_id = -1
                for c in targeted:
                    owner = pipe_cell_owner.get(c.as_tuple())
                    if owner is not None:
                        pipe_id = owner
                        break
                room.pipe_ops.append(PipeOp(cell=(x, y), glyph=glyph, pipe_id=pipe_id))

    width = max((len(r) for r in rows), default=0)
    height = len(rows)
    return Program(
        width=width,
        height=height,
        rooms=rooms,
        pipes=pipes,
        displays=list(info.displays),
    )
