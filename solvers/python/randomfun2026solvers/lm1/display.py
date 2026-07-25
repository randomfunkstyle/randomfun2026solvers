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

__all__ = ["ADDR", "DATA", "SWAP", "Display", "frames_from_writes"]

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
