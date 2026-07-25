#!/usr/bin/env python3
"""Author a program as an AST, then *plate* it — lay it out onto the grid.

Everything so far has read an existing grid and edited it. This goes the other
way: you declare what the program *is* and a placer decides where it goes.

The unit of authoring is a :class:`Fragment`, and its defining property is that
its **internals are fixed**. A fragment is a run of glyphs already known to be
correct — ``\\`104\\`s`` sends 104, full stop. The placer may put it anywhere; it
may never rewrite inside it or split it across a turn. That is what makes
authoring composable: a fragment can be reasoned about, tested, and reused
without knowing where it will land, and a layout bug can never corrupt one.

Two properties decide what a placer is allowed to do with a fragment:

``width``
    how many cells it occupies along its heading. A fragment is atomic, so a row
    with fewer cells left than this cannot take it — no splitting.
``mirror_safe``
    whether it may be laid *backwards*. Almost nothing is. The man executes cells
    in the order he walks them, so a run laid westward runs in reverse; worse, a
    backtick literal loads at its closing tick, so ``\\`104\\`` walked west loads
    401. Only a fragment whose glyph sequence is a palindrome *and* whose literals
    are palindromic survives, which is why the serpentine below keeps every code
    row eastbound and spends the return row on pure corridor.

The plate itself is a boustrophedon: code eastbound, then down, back west across
blanks, down again, and east once more. That costs a row of corridor per row of
code, and the alternative — packing code both ways — is unavailable for exactly
the mirror reason above. Given a fixed cell budget the placer searches the band
width, because the score is ``max(w,h)**2`` and a tall thin band and a short wide
one both lose to a square-ish one.

Output is a :class:`~randomfun2026solvers.manast.Ast`, so a plated program drops
straight into the existing render, round-trip and move machinery.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from .manast import Ast, Joint, Node, PipeNode, RoomNode, Run, render
from .manstruct import _mirror_safe

__all__ = [
    "Port",
    "Contract",
    "Fragment",
    "lit",
    "emit",
    "HALT",
    "Band",
    "pack",
    "plate",
    "best_plate",
    "hello_world",
]

E = (1, 0)


def lit(n: int) -> str:
    """The shortest literal that loads `n` into the main hand.

    A single digit needs no delimiters — SPEC copies a walked-over number into A.
    Anything longer needs backticks, because ``104`` unquoted is three separate
    one-digit loads and leaves 4 behind, not 104.
    """
    if n < 0:
        raise ValueError("a literal is non-negative; negate with N")
    return str(n) if n < 10 else f"`{n}`"


Dir = tuple[int, int]
N, S, W = (0, -1), (0, 1), (-1, 0)


@dataclass(frozen=True)
class Port:
    """Where the man crosses a fragment's boundary, and with what heading.

    A fragment is not just a bag of glyphs — it is a bag of glyphs *with a way in
    and a way out*, and a placer cannot connect anything without knowing both. The
    offsets are relative to the fragment's own origin, so they survive placement.
    """

    dx: int
    dy: int
    heading: Dir
    note: str = ""


@dataclass(frozen=True)
class Contract:
    """What a fragment needs on the way in and leaves behind on the way out.

    The machine has three places to keep a number — the main hand ``A``, the off
    hand ``B``, and the backpack ``BP`` — and a fragment that clobbers one another
    fragment was relying on is a bug no amount of correct *layout* can prevent.
    Recording it lets composition be checked rather than remembered.
    """

    needs: frozenset[str] = frozenset()  # must already hold something meaningful
    writes: frozenset[str] = frozenset()  # is left changed
    note: str = ""


@dataclass(frozen=True)
class Fragment:
    """A run of glyphs whose internals are fixed. Placeable, never rewritable.

    ``entry`` and ``exits`` are the fragment's contract with the *placer*;
    ``contract`` is its contract with the fragments around it. A fragment with no
    exits never returns — ``H`` is the honest example, and a placer that assumed
    one exit per fragment would happily route a corridor out of a halt.
    """

    name: str
    glyphs: str
    note: str = ""
    entry: Port | None = None
    exits: tuple[Port, ...] | None = None
    contract: Contract = field(default_factory=Contract)

    @property
    def width(self) -> int:
        return len(self.glyphs)

    @property
    def in_port(self) -> Port:
        """Default: enter the leftmost cell heading east."""
        return self.entry or Port(0, 0, E, "enter at the left, heading east")

    @property
    def out_ports(self) -> tuple[Port, ...]:
        """Default: leave past the rightmost cell, still heading east."""
        if self.exits is not None:
            return self.exits
        return (Port(self.width - 1, 0, E, "leave the right end, heading east"),)

    @property
    def is_linear(self) -> bool:
        """One way in on the left, one way out on the right, both eastbound.

        The serpentine placer can only lay this shape. A gadget with a second exit
        (a conditional turn) or a vertical run needs a placer that routes rather
        than packs, so it is rejected up front instead of being packed wrongly.
        """
        ins = self.in_port
        outs = self.out_ports
        return (
            (ins.dx, ins.dy, ins.heading) == (0, 0, E)
            and len(outs) == 1
            and (outs[0].dx, outs[0].dy, outs[0].heading) == (self.width - 1, 0, E)
        )

    @property
    def terminal(self) -> bool:
        """Does the man never come back out? Then nothing may follow it."""
        return self.exits is not None and len(self.exits) == 0

    @property
    def mirror_safe(self) -> bool:
        """Would walking this backwards compute the same thing? Usually no."""
        return self.glyphs == self.glyphs[::-1] and _mirror_safe(self.glyphs)


def emit(n: int) -> Fragment:
    """Load `n` and send it to the nearest outgoing pipe.

    Clobbers the main hand: the literal lands in ``A`` and ``s`` sends it from
    there, so nothing upstream may be relying on ``A`` surviving.
    """
    return Fragment(
        f"emit-{n}",
        lit(n) + "s",
        note=f"send {n}",
        contract=Contract(writes=frozenset({"A"}), note="literal -> A, then send A"),
    )


#: ``H`` halts forever, so it has **no exit port** at all. That is the whole point
#: of making exits explicit: a placer must not route a corridor out of a halt, and
#: nothing may be packed after it.
HALT = Fragment("halt", "H", note="stop forever", exits=())


@dataclass
class Band:
    """A serpentine layout of fragments inside a room interior."""

    rows: list[list[Fragment]] = field(default_factory=list)
    width: int = 0  # interior width

    @property
    def height(self) -> int:
        """One corridor row between each pair of code rows."""
        return max(2 * len(self.rows) - 1, 1)

    @property
    def cells(self) -> int:
        return sum(f.width for row in self.rows for f in row)


def pack(fragments: list[Fragment], width: int) -> Band | None:
    """Greedily fill rows of a given interior width. ``None`` if it cannot fit.

    Column 0 carries the row's entry glyph (``@`` or ``>``) and the last column
    carries the turn down, so a row holds ``width - 2`` cells of code. A fragment
    is atomic: if it does not fit in what is left, it starts the next row.
    """
    # Ports first: this placer packs eastbound rows, so it can only take fragments
    # that are entered on the left and left on the right. Anything else -- a
    # gadget with a second exit, a vertical run -- would be laid down in a shape
    # its own ports contradict, so it is refused here rather than mis-placed.
    for frag in fragments:
        if not frag.is_linear and not frag.terminal:
            raise ValueError(
                f"fragment {frag.name!r} is not linear (in {frag.in_port}, "
                f"out {frag.out_ports}); the serpentine placer packs eastbound "
                "runs only and cannot route a gadget"
            )
    for frag in fragments[:-1]:
        if frag.terminal:
            raise ValueError(
                f"fragment {frag.name!r} never returns, so nothing may follow it"
            )

    cap = width - 2
    if cap <= 0 or any(f.width > cap for f in fragments):
        return None
    rows: list[list[Fragment]] = [[]]
    used = 0
    for frag in fragments:
        if used + frag.width > cap:
            rows.append([])
            used = 0
        rows[-1].append(frag)
        used += frag.width
    return Band(rows=rows, width=width)


def _lay(band: Band, ox: int, oy: int) -> list[Node]:
    """Turn a band into room children: spawn, code runs, and the turns between.

    `ox`/`oy` are the **interior** origin, not the box origin: a room's walls
    occupy the box edge, so interior cell (0,0) is one in and one down from it.
    Laying children at the box origin puts the first code row on the top wall.

    Row `i` of code sits on interior row `2i`; the odd rows are corridor. The man
    enters each code row at column 0 heading east, runs the row, turns south at
    the last column, turns west, crosses the corridor, and turns south again into
    the next row's column 0.
    """
    out: list[Node] = []
    last_x = band.width - 1
    for i, row in enumerate(band.rows):
        y = 2 * i
        # The spawn glyph doubles as the first row's entry: a man starts moving
        # right, so `@` at column 0 already heads him into the code.
        out.append(Joint(id=len(out), x=ox, y=oy + y, glyph="@" if i == 0 else ">"))
        x = 1
        for frag in row:
            out.append(
                Run(
                    id=len(out), x=ox + x, y=oy + y, glyphs=frag.glyphs,
                    heading="E", note=frag.name,
                )
            )
            x += frag.width
        if i < len(band.rows) - 1:
            # end of a code row: down, west along the corridor, down again
            out.append(Joint(id=len(out), x=ox + last_x, y=oy + y, glyph="v"))
            out.append(Joint(id=len(out), x=ox + last_x, y=oy + y + 1, glyph="<"))
            out.append(Joint(id=len(out), x=ox, y=oy + y + 1, glyph="v"))
    return out


def plate(fragments: list[Fragment], width: int) -> Ast | None:
    """Lay `fragments` into a worker room, wire an output room, return the AST.

    The output room goes **below** the worker rather than beside it: the worker is
    wider than tall for any sensible band, so growing height costs less than
    growing width when the score is ``max(w,h)**2``.
    """
    band = pack(fragments, width)
    if band is None:
        return None
    children = _lay(band, 1, 1)
    worker = RoomNode(
        id=0, x=0, y=0, w=band.width, h=band.height, children=children, kind="compute"
    )
    bottom_wall = band.height + 1  # the worker box spans rows 0 .. height+1

    # One pipe straight down out of the worker's bottom wall into the O room.
    # TWO cells, not one: measured against the engine, a single-cell pipe reports
    # dst=-1 and connects to nothing, because that cell would have to be both the
    # exit from the source room and the entry to the destination. Two is the
    # minimum that links a pair of rooms.
    port_x = 1  # an interior column of the worker
    pipe_y = bottom_wall + 1
    pipe_cells = [(port_x, pipe_y), (port_x, pipe_y + 1)]
    room_top = pipe_cells[-1][1] + 1
    out_room = RoomNode(
        id=1,
        x=port_x - 1,
        y=room_top,
        w=1,
        h=1,
        kind="output",
        children=[Run(id=0, x=port_x, y=room_top + 1, glyphs="O", heading="E")],
    )
    pipe = PipeNode(
        id=0,
        x=port_x,
        y=pipe_y,
        path=pipe_cells,
        glyphs=["v", "v"],
        src=0,
        dst=1,
        entry_dir=(0, 1),
        exit_dir=(0, 1),
        min_capacity=2,
    )
    worker.ports = [(port_x, bottom_wall)]
    out_room.ports = [(port_x, room_top)]
    return Ast(rooms=[worker, out_room], pipes=[pipe], source=[])


def best_plate(fragments: list[Fragment], *, lo: int = 4, hi: int = 40) -> tuple[Ast, int]:
    """Search the band width for the smallest ``max(w,h)**2``.

    Worth searching rather than picking: a wide band is short but the footprint is
    square-bounded, and a narrow one is tall for the same reason, so the optimum
    sits in the middle and moves with the fragment sizes.
    """
    best: tuple[Ast, int] | None = None
    for w in range(lo, hi + 1):
        ast = plate(fragments, w)
        if ast is None:
            continue
        if best is None or ast.geometry_factor < best[0].geometry_factor:
            best = (ast, w)
    if best is None:
        raise ValueError("no band width fits these fragments")
    return best


# ── programs ─────────────────────────────────────────────────────────────────
def hello_world() -> list[Fragment]:
    """`hello world` as ASCII codes, then stop. No input at all."""
    text = "hello world"
    return [emit(ord(c)) for c in text] + [HALT]


PROGRAMS = {"hello-world": hello_world}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("program", choices=sorted(PROGRAMS))
    ap.add_argument("--width", type=int, help="fix the band width instead of searching")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    frags = PROGRAMS[args.program]()
    if args.width:
        ast = plate(frags, args.width)
        if ast is None:
            raise SystemExit(f"width {args.width} cannot hold these fragments")
        width = args.width
    else:
        ast, width = best_plate(frags)
    rows = render(ast)
    w, h = ast.bbox
    print(
        f"{args.program}: {len(frags)} fragments, {sum(f.width for f in frags)} code cells"
    )
    print(f"  band width {width} -> grid {w}x{h}, factor {ast.geometry_factor:,}")
    print("\n".join(rows))
    if args.out:
        args.out.write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
