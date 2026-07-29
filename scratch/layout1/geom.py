"""Placement -> cells.  Where a block's walls, glyphs and touch cells land.

Growth moves a *wall*, never a glyph.  A block is placed by its reference origin
``(px, py)`` and grows outward from it, so ``STORE_REQUEST_REACH``'s gate — whose
room is pulled thirty rows north — keeps every ``r``/``s`` exactly where it was.
That is not a convenience: it is the difference between a reach that works and
one that silently rebinds the north write arms (``TAPED_CHAIN_REACH``'s note).
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Block, Port, Problem


@dataclass(frozen=True)
class Placed:
    """One block, placed and grown, in absolute grid coordinates."""

    block: Block
    px: int
    py: int
    grow: tuple[int, int, int, int]  # N, S, E, W

    @property
    def rect(self) -> tuple[int, int, int, int]:
        gn, gs, ge, gw = self.grow
        return (
            self.px - gw,
            self.py - gn,
            self.block.w + gw + ge,
            self.block.h + gn + gs,
        )

    def wall(self, side: str) -> int:
        """The row or column the named wall sits on."""
        gn, gs, ge, gw = self.grow
        return {
            "N": self.py - gn,
            "S": self.py + self.block.h - 1 + gs,
            "E": self.px + self.block.w - 1 + ge,
            "W": self.px - gw,
        }[side]

    def _along(self, port: Port, offset: int) -> int:
        return (self.py if port.side in ("E", "W") else self.px) + offset

    def touch(self, port: Port, offset: int) -> tuple[int, int]:
        """The cell a pipe occupies one step *outside* the wall."""
        w, a = self.wall(port.side), self._along(port, offset)
        return {
            "N": (a, w - 1),
            "S": (a, w + 1),
            "E": (w + 1, a),
            "W": (w - 1, a),
        }[port.side]

    def glyph(self, port: Port, offset: int) -> tuple[int, int]:
        """The interior cell the ``r``/``s`` sits on, ``port.depth`` cells in.

        Note this is measured from the *reference* wall, not the grown one: growth
        moves a wall, never a glyph.
        """
        gn, gs, ge, gw = self.grow
        a = self._along(port, offset)
        d = port.depth
        return {
            "N": (a, self.py + d),
            "S": (a, self.py + self.block.h - 1 - d),
            "E": (self.px + self.block.w - 1 - d, a),
            "W": (self.px + d, a),
        }[port.side]

    def glyphs(self, port: Port, offset: int) -> tuple[tuple[int, int], ...]:
        """Every glyph that binds this port, absolute, in the reference frame."""
        if port.cells:
            return tuple((self.px + x, self.py + y) for x, y in port.cells)
        return (self.glyph(port, offset),)

    def heading(self, port: Port) -> tuple[int, int]:
        """Which way a pipe leaves this wall.  Part of every port contract (§7.2)."""
        return {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}[port.side]

    def cells(self) -> set[tuple[int, int]]:
        x0, y0, w, h = self.rect
        return {(x, y) for x in range(x0, x0 + w) for y in range(y0, y0 + h)}


def free_offsets(block: Block, port: Port) -> tuple[int, ...]:
    """The offsets a free port may take: interior only, never a corner."""
    if port.offset is not None:
        return (port.offset,)
    span = block.h if port.side in ("E", "W") else block.w
    return tuple(range(1, span - 1))


@dataclass(frozen=True)
class Layout:
    """A candidate: every block placed and grown, every free offset chosen."""

    problem: Problem
    placed: dict[str, Placed]
    offsets: dict[tuple[str, str], int]

    def port_of(self, ref: tuple[str, str]) -> tuple[Placed, Port, int]:
        bname, pname = ref
        pl = self.placed[bname]
        port = pl.block.port(pname)
        return pl, port, self.offsets[ref]

    def touch(self, ref: tuple[str, str]) -> tuple[int, int]:
        pl, port, off = self.port_of(ref)
        return pl.touch(port, off)

    def heading(self, ref: tuple[str, str]) -> tuple[int, int]:
        pl, port, _ = self.port_of(ref)
        return pl.heading(port)

    def occupied(self) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for pl in self.placed.values():
            out |= pl.cells()
        return out

    def overlaps(self) -> bool:
        """Do two blocks share a cell?  Rooms may *abut* (ARCH §7.4b) but not overlap."""
        seen: set[tuple[int, int]] = set()
        for pl in self.placed.values():
            c = pl.cells()
            if c & seen:
                return True
            seen |= c
        return False

    def in_bounds(self) -> bool:
        bw, bh = self.problem.bounds
        for pl in self.placed.values():
            x0, y0, w, h = pl.rect
            if x0 < 1 or y0 < 1 or x0 + w > bw - 1 or y0 + h > bh - 1:
                return False
        return True
