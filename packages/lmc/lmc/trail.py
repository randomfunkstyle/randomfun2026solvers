"""Trail builder: a linear instruction list -> walkable cells in the CPU interior.

Coordinates are relative to the CPU interior: the top-left interior cell is (0,0),
x grows east, y grows south. The man spawns at the `@` cell facing east.

R0 uses a single row (fits easily). `max_width` triggers boustrophedon wrapping;
that path is stubbed until trails get long (sort/reverse) and will insert `v`/`^`
turn glyphs at row ends.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .blockspec import Instr


@dataclass
class PlacedCell:
    x: int
    y: int
    char: str
    pipe: str | None = None  # target pipe id if this is a pipe op


@dataclass
class TrailLayout:
    width: int
    height: int
    cells: list[PlacedCell] = field(default_factory=list)
    spawn: tuple[int, int] = (0, 0)

    def pipe_op_cells(self) -> list[PlacedCell]:
        return [c for c in self.cells if c.pipe is not None]


def build_trail(trail: list[Instr], max_width: int = 200) -> TrailLayout:
    if len(trail) > max_width:
        raise NotImplementedError("serpentine wrapping not implemented yet")
    cells = [PlacedCell(i, 0, ins.char, ins.pipe) for i, ins in enumerate(trail)]
    return TrailLayout(width=len(trail), height=1, cells=cells, spawn=(0, 0))
