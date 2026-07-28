"""How close can two LM-75 panels stand?

``deadman-3d_hires``'s four 64x48 panels sit in a 2x2 with 177 free columns and
59 free rows between them, because each one is embedded in a 235x101 DOOM block
and the block's own logic sets the pitch.  Before rebuilding the wall it is
worth knowing what the *panels* actually allow, and that is a question about
``SPEC.md``'s pipe rules rather than about this machine:

    ADDR attaches on the top wall, DATA on the left, SWAP on the bottom;
    two pipes on one side, a pipe on the right side, or a pipe at a corner
    are all load errors, and pipes may not originate at a display.

So the experiment is two panels, fully wired, at a shrinking gap, handed to the
real engine (``lm.mjs analyze``).  Run it as::

    python scratch/deadman3d-opt/panel_pack.py

and it prints, per axis, the smallest gap that loads.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1.machine import _Grid  # noqa: E402

PW, PH = 64, 48          # the panel's interior; 66x50 with its walls
LM = REPO / "littleman" / "lm.mjs"


def feeder(g: _Grid, x: int, y: int, n: int) -> tuple[int, int]:
    """A tiny room that recites ``n`` and halts; returns its east exit cell."""
    cells = {(1, 1): "@", (2, 1): "v"}
    row = 2
    for ch in f"`{n}`s":
        cells[(2, row)] = ch
        row += 1
    cells[(2, row)] = "H"
    g.room(x, y, x + 3, y + row + 1)
    g.blit(x, y, cells)
    return x + 4, y + 1


def wire(g: _Grid, px: int, py: int, feed_x: int, feed_y: int,
         corridor: int, under: int) -> None:
    """One panel's three pipes.

    ``corridor`` is the column DATA descends in — the free column immediately
    west of the panel's left wall, which is the whole horizontal cost of a
    neighbour.  ``under`` is the row SWAP runs back along under the panel.
    """
    # ADDR: down into the top wall, from a column strictly inside the span
    g.draw_pipe([(feed_x, feed_y), (px + 3, feed_y), (px + 3, py - 1)])
    # DATA: down the corridor and east into the left wall
    g.draw_pipe([(feed_x, feed_y + 2), (corridor, feed_y + 2),
                 (corridor, py + 4)])
    # SWAP: under the panel and north into the bottom wall
    g.draw_pipe([(feed_x, feed_y + 4), (corridor - 1, feed_y + 4),
                 (corridor - 1, under), (px + 5, under), (px + 5, py + PH + 2)])


def two_panels(gap_x: int | None = None, gap_y: int | None = None) -> list[str]:
    """Two fully-wired panels, side by side or stacked, with ``gap`` free cells
    of grid between their facing walls."""
    g = _Grid()
    feeds = [feeder(g, 0, 0, 3), feeder(g, 0, 40, 5)]
    if gap_x is not None:
        # west panel, then east panel `gap_x` free columns beyond its right wall
        corners = [(20, 4), (20 + PW + 2 + gap_x, 4)]
    else:
        corners = [(20, 4), (20, 4 + PH + 2 + gap_y)]
    for i, (px, py) in enumerate(corners):
        g.room(px, py, px + PW + 1, py + PH + 1, h="=", v=":")
    for i, ((px, py), (fx, fy)) in enumerate(zip(corners, feeds, strict=True)):
        wire(g, px, py, fx, fy, px - 1, py + PH + 3 + 2 * i)
    return g.rows()


def analyze(rows: list[str]) -> dict:
    out = subprocess.run(  # noqa: S603
        ["node", str(LM), "analyze", "-"], input="\n".join(rows) + "\n",
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return {"error": (out.stderr or out.stdout).strip().splitlines()[-1:]}
    return json.loads(out.stdout)


def sweep(axis: str) -> None:
    print(f"── {axis} gap ──")
    for gap in range(0, 6):
        kw = {"gap_x": gap} if axis == "horizontal" else {"gap_y": gap}
        try:
            rows = two_panels(**kw)
        except Exception as exc:  # noqa: BLE001 — a collision IS the answer
            print(f"  gap {gap}: grid collision — {exc}")
            continue
        info = analyze(rows)
        if "error" in info:
            print(f"  gap {gap}: LOAD ERROR {info['error']}")
            continue
        n = len(info.get("displays") or [])
        pipes = len(info.get("pipes") or [])
        print(f"  gap {gap}: loads, {n} displays, {pipes} pipes, "
              f"{max(len(r) for r in rows)}x{len(rows)}")


if __name__ == "__main__":
    sweep("horizontal")
    sweep("vertical")
