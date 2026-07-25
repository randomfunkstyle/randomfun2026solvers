#!/usr/bin/env python3
"""A mutable AST for ``.man`` grids — what a compactor actually rewrites.

:mod:`manstruct` *describes* a grid. This module gives it a tree that can be
**edited and rendered back**, which is the part a compactor needs.

The design rule is that the AST is **total**: every grid parses, including the
parts we do not understand, because anything unrecognised becomes an
:class:`Atom` — a verbatim rectangle of cells with a do-not-rewrite contract.
That single escape hatch is what makes the whole approach safe:

* nothing is ever silently reinterpreted; an unparsed region is *explicitly*
  marked opaque rather than guessed at;
* compaction can start before understanding is complete. At
  :data:`Refine.ROOMS` every room interior is one atom, and even then the big
  geometric wins — dropping dead rows and columns, reflowing an
  over-provisioned pipe, moving a room to another side — are all still available;
* refinement is monotone. Splitting an atom into runs and joints
  (:data:`Refine.BLOCKS`) unlocks *more* moves and can never invalidate a move
  that already worked, because correctness is enforced by re-rendering, not by
  the parse being clever.

**Two flags, not one.** "Do not touch this 2×2" conflates two independent
things, and keeping them apart is what lets a frozen gadget still be *moved*:

``rigid_content``
    Never rewrite what is inside. True for every :class:`Atom` by construction.
``pinned``
    May not even translate. Reserved for things whose absolute position is load
    bearing — an IO room's attach side, a display panel, a pipe whose exact
    distance decides a nearest-pipe tie-break.

So the default atom is *rigid but mobile*: its bytes are sacred, its address is
not. A pinned atom is the true "do not touch at all".

Correctness gate: :func:`render` is checked against the source by
:func:`round_trip_ok`. A parse that cannot reproduce its own input byte for byte
is rejected outright, so no move is ever built on a lossy read.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from .manparse import Program, parse_program
from .manstruct import Kind, _build_cells, _components, _live_in_room

__all__ = [
    "Refine",
    "Node",
    "Atom",
    "Corridor",
    "Run",
    "Joint",
    "RoomNode",
    "PipeNode",
    "Ast",
    "parse_ast",
    "render",
    "round_trip_ok",
    "PaintError",
]


class PaintError(RuntimeError):
    """Two nodes want the same cell with different glyphs."""


class Refine(IntEnum):
    """How finely to break a grid down. Higher unlocks more moves, never fewer."""

    ROOMS = 0  # every room interior is one Atom: safest, still allows room+pipe moves
    BLOCKS = 1  # interiors split into runs, joints, and gadget atoms


# ── nodes ────────────────────────────────────────────────────────────────────
@dataclass
class Node:
    """Anything occupying cells at an absolute top-left position.

    Subclasses supply :meth:`paint`, which is the *only* place glyphs are
    produced — so rendering has one code path and round-tripping proves it.
    """

    id: int
    x: int
    y: int
    rigid_content: bool = True
    pinned: bool = False
    note: str = ""

    @property
    def size(self) -> tuple[int, int]:
        raise NotImplementedError

    def paint(self) -> dict[tuple[int, int], str]:
        raise NotImplementedError

    def translate(self, dx: int, dy: int) -> None:
        if self.pinned:
            raise PaintError(f"{type(self).__name__} {self.id} is pinned: {self.note}")
        self.x += dx
        self.y += dy

    @property
    def cells(self) -> set[tuple[int, int]]:
        return set(self.paint())


@dataclass
class Atom(Node):
    """A verbatim rectangle: the escape hatch, and the do-not-touch node.

    Holds its glyphs exactly as they were read. Blanks inside are kept, because
    an atom's *shape* is part of its contract — a gadget's blank cell may be a
    corridor the man walks, and we do not presume to know which.
    """

    rows: list[str] = field(default_factory=list)

    @property
    def size(self) -> tuple[int, int]:
        return (max((len(r) for r in self.rows), default=0), len(self.rows))

    def paint(self) -> dict[tuple[int, int], str]:
        out: dict[tuple[int, int], str] = {}
        for dy, row in enumerate(self.rows):
            for dx, ch in enumerate(row):
                if ch != " ":
                    out[(self.x + dx, self.y + dy)] = ch
        return out

    def blank_rows(self) -> list[int]:
        return [i for i, r in enumerate(self.rows) if not r.strip()]

    def blank_cols(self) -> list[int]:
        w, _ = self.size
        pad = [r.ljust(w) for r in self.rows]
        return [x for x in range(w) if all(r[x] == " " for r in pad)]


@dataclass
class Run(Node):
    """A collinear chain of executing glyphs. Rigid: adjacency is execution order."""

    glyphs: str = ""
    heading: str = "E"  # "E" or "S"; runs are stored left-to-right / top-to-bottom

    @property
    def size(self) -> tuple[int, int]:
        n = len(self.glyphs)
        return (n, 1) if self.heading == "E" else (1, n)

    def paint(self) -> dict[tuple[int, int], str]:
        dx, dy = (1, 0) if self.heading == "E" else (0, 1)
        return {
            (self.x + i * dx, self.y + i * dy): ch
            for i, ch in enumerate(self.glyphs)
            if ch != " "
        }


@dataclass
class Joint(Node):
    """One steer or branch glyph: the pivot a corridor turns on."""

    glyph: str = ">"

    @property
    def size(self) -> tuple[int, int]:
        return (1, 1)

    def paint(self) -> dict[tuple[int, int], str]:
        return {(self.x, self.y): self.glyph}


@dataclass
class Corridor(Node):
    """``.`` cells: SPEC nops, so pure latency the man walks over.

    The most elastic thing in a grid. A compactor may move them, erase them
    (a blank is the same nop), or redraw the path somewhere shorter — none of
    which changes what the program computes. They are kept as a node rather than
    dropped only so the AST still round-trips byte for byte.
    """

    dots: list[tuple[int, int]] = field(default_factory=list)
    rigid_content: bool = False

    @property
    def size(self) -> tuple[int, int]:
        if not self.dots:
            return (0, 0)
        xs = [x for x, _ in self.dots]
        ys = [y for _, y in self.dots]
        return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)

    def paint(self) -> dict[tuple[int, int], str]:
        return dict.fromkeys(self.dots, ".")

    def translate(self, dx: int, dy: int) -> None:
        if self.pinned:
            raise PaintError(f"corridor {self.id} is pinned: {self.note}")
        self.dots = [(x + dx, y + dy) for x, y in self.dots]
        self.x += dx
        self.y += dy


@dataclass
class RoomNode(Node):
    """A room box. Children are absolute-positioned and move with it."""

    kind: str = "compute"
    w: int = 0  # interior width
    h: int = 0  # interior height
    children: list[Node] = field(default_factory=list)
    #: wall cells a pipe attaches to, absolute. Moving the room moves these.
    ports: list[tuple[int, int]] = field(default_factory=list)

    @property
    def size(self) -> tuple[int, int]:
        return (self.w + 2, self.h + 2)

    def paint(self) -> dict[tuple[int, int], str]:
        out: dict[tuple[int, int], str] = {}
        bw, bh = self.size
        horiz, vert, corner = ("=", ":", "+") if self.kind == "display" else ("-", "|", "+")
        for dx in range(bw):
            out[(self.x + dx, self.y)] = horiz
            out[(self.x + dx, self.y + bh - 1)] = horiz
        for dy in range(bh):
            out[(self.x, self.y + dy)] = vert
            out[(self.x + bw - 1, self.y + dy)] = vert
        for cx, cy in ((0, 0), (bw - 1, 0), (0, bh - 1), (bw - 1, bh - 1)):
            out[(self.x + cx, self.y + cy)] = corner
        for child in self.children:
            for c, ch in child.paint().items():
                out[c] = ch
        return out

    def translate(self, dx: int, dy: int) -> None:
        super().translate(dx, dy)
        for child in self.children:
            child.x += dx
            child.y += dy
        self.ports = [(x + dx, y + dy) for x, y in self.ports]


@dataclass
class PipeNode(Node):
    """A pipe. **Its length is its capacity** — one value per cell per tick.

    ``min_capacity`` is a declared program invariant, never inferred: only the
    author knows a 100-cell tape ring needs 101 slots. ``None`` means unknown,
    which pins the pipe — silence must never shorten a ring.
    """

    path: list[tuple[int, int]] = field(default_factory=list)
    glyphs: list[str] = field(default_factory=list)
    src: int = -1
    dst: int = -1
    min_capacity: int | None = None

    @property
    def size(self) -> tuple[int, int]:
        xs = [x for x, _ in self.path] or [0]
        ys = [y for _, y in self.path] or [0]
        return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)

    @property
    def capacity(self) -> int:
        return len(self.path)

    def paint(self) -> dict[tuple[int, int], str]:
        return dict(zip(self.path, self.glyphs, strict=True))

    def translate(self, dx: int, dy: int) -> None:
        if self.pinned:
            raise PaintError(f"pipe {self.id} is pinned: {self.note}")
        self.path = [(x + dx, y + dy) for x, y in self.path]
        self.x += dx
        self.y += dy


@dataclass
class Ast:
    """A whole program: rooms, pipes, and any stray cells, all mutable."""

    rooms: list[RoomNode] = field(default_factory=list)
    pipes: list[PipeNode] = field(default_factory=list)
    #: Non-blank cells the engine attributed to neither a room nor a pipe. Kept
    #: verbatim so the parse stays total and lossless.
    strays: list[Atom] = field(default_factory=list)
    source: list[str] = field(default_factory=list)
    refine: Refine = Refine.ROOMS

    @property
    def nodes(self) -> list[Node]:
        return [*self.rooms, *self.pipes, *self.strays]

    @property
    def bbox(self) -> tuple[int, int]:
        cells: set[tuple[int, int]] = set()
        for n in self.nodes:
            cells |= n.cells
        if not cells:
            return (0, 0)
        return max(x for x, _ in cells) + 1, max(y for _, y in cells) + 1

    @property
    def geometry_factor(self) -> int:
        w, h = self.bbox
        return max(w, h) ** 2

    def movable(self) -> list[Node]:
        return [n for n in self.nodes if not n.pinned]


# ── parse ────────────────────────────────────────────────────────────────────
def _room_children(program: Program, room, refine: Refine) -> list[Node]:
    """Interior as one atom, or split into runs, joints, and gadget atoms."""
    ox, oy = room.min_
    interior = [row[1:-1] for row in room.content[1:-1]]
    if refine is Refine.ROOMS or not interior:
        return [
            Atom(
                id=0,
                x=ox + 1,
                y=oy + 1,
                rows=list(interior),
                note=f"unrefined interior of room{room.id}",
            )
        ]

    cells = _build_cells(program)
    live = {c for c in _live_in_room(cells) if cells[c].room == room.id}
    out: list[Node] = []

    # `.` is a nop, so it never joins a clump -- otherwise the corridors between
    # two unrelated bodies would glue them into one immovable atom. It is carried
    # separately, as the elastic node it is.
    dots = sorted(
        c for c, i in cells.items() if i.room == room.id and i.kind is Kind.NOP
    )
    if dots:
        out.append(
            Corridor(
                id=-1,
                x=min(x for x, _ in dots),
                y=min(y for _, y in dots),
                dots=dots,
                note="nop corridor: erasable, redrawable",
            )
        )

    for i, clump in enumerate(_components(live)):
        xs = {x for x, _ in clump}
        ys = {y for _, y in clump}
        x0, y0 = min(xs), min(ys)
        if len(clump) == 1:
            glyph = cells[clump[0]].glyph
            kind = cells[clump[0]].kind
            if kind in (Kind.STEER, Kind.BRANCH):
                out.append(Joint(id=i, x=x0, y=y0, glyph=glyph))
            else:
                out.append(Run(id=i, x=x0, y=y0, glyphs=glyph, heading="E"))
            continue
        if len(ys) == 1 or len(xs) == 1:
            heading = "E" if len(ys) == 1 else "S"
            key = (lambda c: c[0]) if heading == "E" else (lambda c: c[1])
            glyphs = "".join(cells[c].glyph for c in sorted(clump, key=key))
            out.append(Run(id=i, x=x0, y=y0, glyphs=glyphs, heading=heading))
            continue
        # A gadget: multi-column body, so it stays an atom -- rigid, still mobile.
        w, h = max(xs) - x0 + 1, max(ys) - y0 + 1
        rows = [
            "".join(cells[(x, y)].glyph if (x, y) in live else " " for x in range(x0, x0 + w))
            for y in range(y0, y0 + h)
        ]
        out.append(Atom(id=i, x=x0, y=y0, rows=rows, note="gadget: rigid multi-column body"))
    return out


def parse_ast(
    program: str | os.PathLike[str] | Program,
    *,
    refine: Refine = Refine.ROOMS,
    capacity: dict[int, int] | None = None,
) -> Ast:
    """Parse a grid into a mutable AST.

    `capacity` maps pipe id -> minimum values in flight. An undeclared pipe is
    :attr:`Node.pinned`, because shortening it could deadlock a ring.
    """
    prog = program if isinstance(program, Program) else parse_program(program)
    caps = capacity or {}

    rooms: list[RoomNode] = []
    attach: dict[int, list[tuple[int, int]]] = {r.id: [] for r in prog.rooms}
    for p in prog.pipes:
        if p.src in attach:
            attach[p.src].append(p.src_attach)
        if p.dst in attach:
            attach[p.dst].append(p.dst_attach)

    for room in prog.rooms:
        # An IO room's 3x3 shape and a display's resolution are the problem's,
        # not ours; pin them so no move can reshape or relocate them.
        pinned = room.kind in ("input", "output", "display")
        rooms.append(
            RoomNode(
                id=room.id,
                x=room.min_[0],
                y=room.min_[1],
                kind=room.kind,
                w=room.width - 2,
                h=room.height - 2,
                children=_room_children(prog, room, refine),
                ports=attach[room.id],
                pinned=pinned,
                note=f"{room.kind} room: shape fixed by SPEC" if pinned else "",
            )
        )

    pipes = [
        PipeNode(
            id=p.id,
            x=min((x for x, _ in p.cells), default=0),
            y=min((y for _, y in p.cells), default=0),
            path=list(p.cells),
            glyphs=list(p.glyphs),
            src=p.src,
            dst=p.dst,
            min_capacity=caps.get(p.id),
            pinned=p.id not in caps,
            note=(
                "" if p.id in caps else "no declared capacity: shortening could deadlock a ring"
            ),
        )
        for p in prog.pipes
    ]

    # Anything the engine claimed for neither a room nor a pipe, kept verbatim.
    src_rows = prog.to_grid()
    claimed: set[tuple[int, int]] = set()
    for n in [*rooms, *pipes]:
        claimed |= n.cells
    strays: list[Atom] = []
    for y, row in enumerate(src_rows):
        for x, ch in enumerate(row):
            if ch != " " and (x, y) not in claimed:
                strays.append(
                    Atom(id=len(strays), x=x, y=y, rows=[ch], note="unclaimed cell")
                )

    return Ast(rooms=rooms, pipes=pipes, strays=strays, source=src_rows, refine=refine)


# ── render ───────────────────────────────────────────────────────────────────
def render(ast: Ast, *, check: bool = True) -> list[str]:
    """Paint every node onto one canvas. The single place glyphs are produced."""
    canvas: dict[tuple[int, int], str] = {}
    owner: dict[tuple[int, int], str] = {}
    for node in ast.nodes:
        tag = f"{type(node).__name__}{node.id}"
        for c, ch in node.paint().items():
            if check and c in canvas and canvas[c] != ch:
                raise PaintError(
                    f"cell {c}: {owner[c]} painted {canvas[c]!r}, {tag} wants {ch!r}"
                )
            canvas[c] = ch
            owner[c] = tag
    if not canvas:
        return []
    w = max(x for x, _ in canvas) + 1
    h = max(y for _, y in canvas) + 1
    return ["".join(canvas.get((x, y), " ") for x in range(w)).rstrip() for y in range(h)]


def round_trip_ok(ast: Ast) -> bool:
    """Does the AST reproduce its source byte for byte?

    The gate every move sits behind: a parse that cannot rebuild its own input
    has misunderstood something, and no rewrite built on it can be trusted.
    """
    return render(ast) == [r.rstrip() for r in ast.source]


def diff_source(ast: Ast) -> list[str]:
    """Human-readable round-trip failures, for when the gate trips."""
    got = render(ast)
    want = [r.rstrip() for r in ast.source]
    out: list[str] = []
    for i in range(max(len(got), len(want))):
        g = got[i] if i < len(got) else "<missing>"
        w = want[i] if i < len(want) else "<missing>"
        if g != w:
            out.append(f"row {i}:\n  want {w!r}\n  got  {g!r}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path)
    ap.add_argument("--refine", type=int, choices=(0, 1), default=0)
    ap.add_argument("--out", type=Path, help="write the rendered grid back out")
    args = ap.parse_args()

    ast = parse_ast(args.grid, refine=Refine(args.refine))
    w, h = ast.bbox
    kinds: dict[str, int] = {}
    for r in ast.rooms:
        for c in r.children:
            kinds[type(c).__name__] = kinds.get(type(c).__name__, 0) + 1
    print(f"{args.grid.name}: {w}x{h}  factor {ast.geometry_factor:,}  refine={args.refine}")
    print(f"  rooms {len(ast.rooms)}  pipes {len(ast.pipes)}  strays {len(ast.strays)}")
    print(f"  room children: {kinds}")
    print(f"  movable nodes: {len(ast.movable())}/{len(ast.nodes)}")
    ok = round_trip_ok(ast)
    print(f"  round-trip: {'OK' if ok else 'FAILED'}")
    if not ok:
        for line in diff_source(ast)[:10]:
            print("   ", line)
    if args.out:
        args.out.write_text("\n".join(render(ast)) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
