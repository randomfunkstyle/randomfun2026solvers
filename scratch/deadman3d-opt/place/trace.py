#!/usr/bin/env python3
"""Lift a *shipped* layout into the IR by walking it, so validation is not a retype.

The temptation, when validating a framework against a hand-built structure, is to
read the structure, decide what it costs, and type that number in as the
baseline.  That validates nothing: the framework and the baseline then share an
author and an opinion.

This module instead **walks the real grid**.  It applies the engine's own
movement rules -- execute the glyph under the man, then step one cell along the
resulting heading -- and counts the cells he stands on.  That count *is* the tick
cost (every glyph is one tick; ``fast_littleman.py:1057-1066``), so the baseline
comes out of the geometry rather than out of a claim about it.

Branches are the only thing a static walk cannot resolve.  ``X d a x`` turn on
register state this module does not model, so the caller supplies a
:class:`Choices` oracle -- "at this cell, take this exit" -- and the walk records
which branches it consulted.  A trace that consulted no branches is exact.  A
trace that consulted branches is exact *for that path*, which is what a
per-path tick cost means anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ir import Leg, Node, Placement

__all__ = ["Grid", "Choices", "Walk", "walk", "leg_from_walk", "load_man"]

#: heading -> (dx, dy), screen coordinates: +y is south.
DIRS = {"E": (1, 0), "S": (0, 1), "W": (-1, 0), "N": (0, -1)}
CW = ("E", "S", "W", "N")
_IDX = {h: i for i, h in enumerate(CW)}
STEER = {">": "E", "<": "W", "^": "N", "v": "S", "V": "S"}
BRANCH = set("Xdax")
#: Glyphs that end a walk for tracing purposes.
STOP = set("H")
#: Pipe glyphs.  They may block, which costs ticks this static walk cannot see;
#: :class:`Walk` records them so the caller knows where the count is a lower bound.
PIPEOP = set("sSrRUq")

WALLS = set("+-|=:")


def cw(h: str) -> str:
    return CW[(_IDX[h] + 1) % 4]


def ccw(h: str) -> str:
    return CW[(_IDX[h] - 1) % 4]


@dataclass
class Grid:
    rows: list[str]

    @classmethod
    def from_text(cls, text: str) -> "Grid":
        return cls(text.rstrip("\n").split("\n"))

    def at(self, x: int, y: int) -> str:
        if 0 <= y < len(self.rows) and 0 <= x < len(self.rows[y]):
            return self.rows[y][x]
        return " "

    def find(self, ch: str) -> list[tuple[int, int]]:
        return [(x, y) for y, r in enumerate(self.rows)
                for x, c in enumerate(r) if c == ch]

    def sub(self, x0: int, y0: int, x1: int, y1: int) -> "Grid":
        return Grid([self.rows[y][x0:x1 + 1] if y < len(self.rows) else ""
                     for y in range(y0, y1 + 1)])

    def render(self) -> str:
        return "\n".join(self.rows)


def load_man(path) -> Grid:
    from pathlib import Path
    return Grid.from_text(Path(path).read_text())


@dataclass
class Choices:
    """Which exit a branch takes, keyed by cell.

    ``{(x, y): "cw" | "ccw" | "straight"}``.  A cell absent from the map raises,
    rather than guessing -- guessing here silently produces a tick count for a
    path the machine never walks.
    """

    table: dict[tuple[int, int], str] = field(default_factory=dict)
    default: str | None = None

    def exit(self, cell, glyph, heading) -> str:
        how = self.table.get(cell, self.default)
        if how is None:
            raise KeyError(
                f"branch {glyph!r} at {cell} has no choice; supply one "
                "(a guess here would price a path the machine never walks)")
        return {"cw": cw(heading), "ccw": ccw(heading), "straight": heading}[how]


@dataclass
class Walk:
    """The cells a man stands on, in order.  ``len(cells)`` is the tick count."""

    cells: list[tuple[int, int]] = field(default_factory=list)
    glyphs: str = ""
    branches: list[tuple[int, int]] = field(default_factory=list)
    pipe_ops: list[tuple[int, int]] = field(default_factory=list)
    stopped: str = ""

    @property
    def ticks(self) -> int:
        """Cells stood on = ticks.  A lower bound where ``pipe_ops`` blocked."""
        return len(self.cells)

    @property
    def exact(self) -> bool:
        """True when nothing in the walk could have blocked or been guessed."""
        return not self.pipe_ops

    def bbox(self):
        xs = [c[0] for c in self.cells]
        ys = [c[1] for c in self.cells]
        return (min(xs), min(ys), max(xs), max(ys))

    def extent(self):
        x0, y0, x1, y1 = self.bbox()
        return (x1 - x0 + 1, y1 - y0 + 1)

    def explain(self) -> str:
        w, h = self.extent()
        return (f"walk {self.ticks} ticks over {len(set(self.cells))} distinct cells, "
                f"{w}x{h}, stopped={self.stopped}, "
                f"branches={len(self.branches)}, pipe_ops={len(self.pipe_ops)}\n"
                f"    {self.glyphs}")


def walk(
    grid: Grid,
    start: tuple[int, int],
    heading: str = "E",
    choices: Choices | None = None,
    max_steps: int = 100_000,
    stop_at: set | None = None,
) -> Walk:
    """Walk the grid from ``start``, counting cells stood on.

    The engine's rule, applied literally: execute the glyph under the man (which
    may change his heading), then advance one cell along the *new* heading.  A
    direction glyph is therefore free -- the turn cell is also a step cell.
    """
    choices = choices or Choices()
    stop_at = stop_at or set()
    w = Walk()
    x, y = start
    for _ in range(max_steps):
        g = grid.at(x, y)
        if g in WALLS:
            w.stopped = f"wall {g!r} at {(x, y)}"
            return w
        w.cells.append((x, y))
        w.glyphs += g
        if (x, y) in stop_at:
            w.stopped = f"stop_at {(x, y)}"
            return w
        if g in STOP:
            w.stopped = "halt"
            return w
        if g in STEER:
            heading = STEER[g]
        elif g in BRANCH:
            w.branches.append((x, y))
            heading = choices.exit((x, y), g, heading)
        elif g in PIPEOP:
            w.pipe_ops.append((x, y))
        dx, dy = DIRS[heading]
        x, y = x + dx, y + dy
    w.stopped = "max_steps"
    return w


def leg_from_walk(name: str, w: Walk, grid: Grid, room=None,
                  send_cell=None, weight: float = 1.0) -> Leg:
    """Turn a traced walk into a :class:`Leg` of one node per cell.

    The result scores to exactly ``w.ticks`` -- every cell is a one-glyph node
    and every edge is adjacent, so the transit terms are all zero.  That is the
    point: the lifted leg is the shipped structure, priced by the framework's own
    score function, so a comparison against a searched layout is like for like.

    ``send_cell`` splits the phases: cells up to and including it are pre-send,
    the rest post-send.
    """
    from ir import POST_SEND, PRE_SEND

    leg = Leg(name, room=room, weight=weight)
    phase = PRE_SEND
    prev = None
    for i, c in enumerate(w.cells):
        g = grid.at(*c)
        nm = f"c{i:04d}"
        leg.add(Node(nm, body={(0, 0): g}, pos=c, phase=phase))
        if prev is not None:
            leg.connect(prev, nm)
        prev = nm
        if send_cell is not None and c == send_cell:
            phase = POST_SEND
    if send_cell is not None:
        leg.send_node = None
    return leg


def placement_of(leg: Leg) -> Placement:
    """A :class:`Placement` for a leg whose nodes are all pinned."""
    return Placement(leg)
