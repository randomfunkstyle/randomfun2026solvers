"""The LM-75 display, as a model over the emulator's port writes.

:class:`~randomfun2026solvers.lm1.emulator.Emulator` records ``(port, value)``
pairs and deliberately does not simulate the panel — STORE is abstract for the
same reason (``ARCH.md`` §4.1). But a display problem is graded on *committed
frames*, not on program output, so without this a program like ``plotter`` passes
the generic "output matches" test trivially while drawing nothing.

The panel, per ``SPEC.md``:

* **left / DATA** — a value 0–15 paints that colour into ``next`` at the cursor and
  advances it (left→right, top→bottom);
* **top / ADDR** — a value ``row * width + column`` repositions the cursor;
* **bottom / SWAP** — ``0`` copies ``next`` into ``current``, clears ``next`` and
  resets the cursor; ``1`` copies but keeps both.

``0`` is the interesting one: it is what makes "lines do not persist between
rounds" free, so a program only has to write its own pixels rather than repaint
the whole panel.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ADDR",
    "DATA",
    "SWAP",
    "TILE_H",
    "TILE_W",
    "WALL_H",
    "WALL_W",
    "Display",
    "frames_from_writes",
    "tile_addr",
    "tile_of",
    "tiled_frames_from_writes",
]

ADDR, DATA, SWAP = 0, 1, 2


class Display(BaseModel):
    """A panel with a cursor and the two buffers ``SPEC.md`` describes."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    width: int
    height: int
    cursor: int = 0
    next: list[list[int]] = []
    current: list[list[int]] = []
    committed: list[list[str]] = []

    def model_post_init(self, _: object) -> None:
        self.next = self._blank()
        self.current = self._blank()
        self.committed = []

    def _blank(self) -> list[list[int]]:
        return [[0] * self.width for _ in range(self.height)]

    def write(self, port: int, value: int) -> None:
        if port == ADDR:
            if not 0 <= value < self.width * self.height:
                raise ValueError(f"ADDR {value} is out of bounds for {self.width}x{self.height}")
            self.cursor = value
        elif port == DATA:
            if not 0 <= value <= 15:
                raise ValueError(f"DATA must be a colour 0-15; got {value}")
            row, col = divmod(self.cursor, self.width)
            self.next[row][col] = value
            # "next column, else next row, else back to the upper-left"
            self.cursor = (self.cursor + 1) % (self.width * self.height)
        elif port == SWAP:
            if value not in (0, 1):
                raise ValueError(f"SWAP must be 0 or 1; got {value}")
            self.current = [row[:] for row in self.next]
            self.committed.append(["".join(f"{p:x}" for p in row) for row in self.current])
            if value == 0:
                self.next = self._blank()
                self.cursor = 0
        else:
            raise ValueError(f"display port must be 0, 1 or 2; got {port}")


def frames_from_writes(
    writes: Iterable[tuple[int, int]], *, width: int, height: int
) -> list[list[str]]:
    """Replay port writes and return the committed frames, as rows of hex digits."""
    panel = Display(width=width, height=height)
    for port, value in writes:
        panel.write(port, value)
    return panel.committed


#: The tiled wall (``lm1/d3_router.py``): 128x96 as four 64x48 panels.
#:
#:     tile = (x >= 64) + 2 * (y >= 48)      in-tile = (x % 64, y % 48)
TILE_W, TILE_H = 64, 48
TILE_COLS, TILE_ROWS = 2, 2
WALL_W, WALL_H = TILE_W * TILE_COLS, TILE_H * TILE_ROWS


def tile_of(x: int, y: int) -> int:
    """Which panel a logical 128x96 pixel lands on."""
    if not (0 <= x < WALL_W and 0 <= y < WALL_H):
        raise ValueError(f"({x}, {y}) is outside the {WALL_W}x{WALL_H} wall")
    return (x >= TILE_W) + 2 * (y >= TILE_H)


def tile_addr(x: int, y: int) -> int:
    """The panel-local ADDR (``row * 64 + column``) for a logical pixel."""
    return (y % TILE_H) * TILE_W + (x % TILE_W)


def tiled_frames_from_writes(
    writes: Iterable[tuple[int, int, int]],
) -> list[list[str]]:
    """Replay ``(tile, port, value)`` writes and compose the 128x96 frames.

    **Composition is by frame index, not by tick**, and that is the whole reason
    the router commits with ``S`` (send to *every* outgoing pipe at once).  Four
    panels swap on four separate pipes, so their SWAPs can never be guaranteed to
    land on the same tick — but ``S`` does guarantee every panel sees the same
    COMMIT sequence, so tile frame *N* is always a piece of logical frame *N* and
    the composed image is never half-old.  A panel that has committed a different
    number of frames from its neighbours means a COMMIT went out on a tile
    selector instead of the broadcast, and that is an error worth raising.
    """
    panels = [Display(width=TILE_W, height=TILE_H) for _ in range(TILE_COLS * TILE_ROWS)]
    for tile, port, value in writes:
        panels[tile].write(port, value)
    counts = {len(p.committed) for p in panels}
    if len(counts) != 1:
        raise ValueError(
            f"the four panels committed {sorted(counts)} frames: a tiled frame is "
            "composed by index, so every COMMIT must go out on the router's "
            "broadcast leaf"
        )
    out: list[list[str]] = []
    for n in range(counts.pop()):
        out.append(
            [
                "".join(panels[2 * (r // TILE_H) + c].committed[n][r % TILE_H] for c in range(2))
                for r in range(WALL_H)
            ]
        )
    return out
