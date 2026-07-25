#!/usr/bin/env python3
"""`pathfinder` as a dataflow ring machine -- floorplan and pipe binding.

The machine itself is :mod:`pathfinder_prog`: 50 blocks of straight glyph runs
over one man, an 18-word ring, a spill FIFO ``F`` and a scratch FIFO ``G``.
That program is validated on all seven public cases at the op level.  This
module turns it into a grid.

Everything below is the *layout* problem, which for this program is dominated
by one thing:

## Nearest-pipe binding is the whole floorplan

`s`/`r` bind to the **nearest** pipe by Manhattan distance -- nearest, not
nearest-ready -- so every one of the 327 pipe ops has to be standing in the
right place.  Measured over the program (``scratch/pf/zones.py``):

    pipe ops   R 92   F 66   G 125   painter 39   input 5      (327 total)
    runs of consecutive same-pipe ops: 160, **mean length 2.0**
    zone transitions: **122**   (R-G 36, G-F 31, G-P 32, F-R 14, F-P 3, I-F 6)

Mean run length 2 is the number that decides the layout.  A one-dimensional
band scheme -- all anchors on one wall, so binding depends only on the column
-- forces the zones into a fixed left-to-right order, and then at least one
hot pair is non-adjacent: the best linear order still costs ~175 band-steps of
pure travel.  Worse, `G` is a hub (adjacent to R, F *and* the painter in the
transition graph) and no linear order gives one node three neighbours.

## So the zones are quadrants, not bands

Put each pipe pair's two anchors on **opposite walls at the same column**:

    outgoing   sg -> north @ XW    sr -> north @ XE
               sf -> south @ XW    sp -> south @ XE
    incoming   rg -> north @ YW    rr -> north @ YE
               rf -> south @ YW    ri -> south @ YE

For a send at (x, y) the four distances are

    d(sg) = |x-XW| + (y+1)      d(sr) = |x-XE| + (y+1)
    d(sf) = |x-XW| + (IH-y)     d(sp) = |x-XE| + (IH-y)

and because the two anchor columns are shared between the walls, the ``x``
term and the ``y`` term separate exactly:

    winner = (west if |x-XW| < |x-XE| else east)
             x (north if y+1 < IH-y else south)

-- a clean quadrant partition of the room, with **all four quadrants meeting
at one point**.  Near that point every transition is short, including the two
diagonal ones.  That is what a linear band scheme cannot buy.

Ties must not exist, or `s` falls back on reading order and a one-cell edit
can silently rebind:

* ``IH`` **even** => ``y+1 == IH-y`` has no integer solution;
* ``XW + XE`` **odd** => ``|x-XW| == |x-XE|`` has no integer solution;
* likewise ``YW + YE`` odd.

The incoming partition is independent of the outgoing one, so ``YW``/``YE``
may sit one column away from ``XW``/``XE``; the *clean* region for a pipe pair
is the intersection of its send quadrant and its receive quadrant, and keeping
the two midlines within a column of each other keeps those regions large.

## The rest of the box

* the panel harness is :mod:`pathfinder_panel` -- a verified 20x24 block
  (painter room flush on the panel's top wall, ADDR/DATA/SWAP leaving
  sideways, no two-row band).  Its incoming pipe terminates at block-relative
  (1, 1) heading east, so the worker's painter pipe arrives from the west.
* three pipe loops need turnaround rooms: ring (>= 19 cells of capacity, for
  18 words plus one), F (>= 7) and G (>= 8).  ``value_ring.RELAY_NORTH`` is
  the proven one at 6 ticks/word.  **``snake_ring.FLAT_RELAY`` is broken** --
  its man arrives at the bottom-right `.` heading south and steps into the
  wall; do not copy it without fixing the turn.
* the ring is the throughput floor: 18 words a lap, four laps per move, so a
  6-tick relay costs 432 ticks/move of pure turnaround.  A wider relay with
  more `r`/`s` pairs per walking cycle buys that back directly.
"""
from __future__ import annotations

__all__ = ["Binding"]


class Binding:
    """The quadrant partition, as an assertable predicate.

    ``send(x, y)`` and ``recv(x, y)`` name the pipe a glyph at that interior
    cell would bind to, computed from the same Manhattan rule the engine uses.
    The generator places every pipe op through these, so a mis-bound send is a
    build error rather than a wrong answer.
    """

    def __init__(self, iw: int, ih: int, xw: int, xe: int, yw: int, ye: int):
        if ih % 2:
            raise ValueError("IH must be even or the north/south split ties")
        if (xw + xe) % 2 == 0:
            raise ValueError("XW + XE must be odd or the east/west split ties")
        if (yw + ye) % 2 == 0:
            raise ValueError("YW + YE must be odd or the east/west split ties")
        if not (0 <= xw < xe < iw and 0 <= yw < ye < iw):
            raise ValueError("anchor columns must be inside the room, west < east")
        self.iw, self.ih = iw, ih
        self.xw, self.xe, self.yw, self.ye = xw, xe, yw, ye

    def send(self, x: int, y: int) -> str:
        west = abs(x - self.xw) < abs(x - self.xe)
        north = (y + 1) < (self.ih - y)
        return {(True, True): "sg", (False, True): "sr",
                (True, False): "sf", (False, False): "sp"}[(west, north)]

    def recv(self, x: int, y: int) -> str:
        west = abs(x - self.yw) < abs(x - self.ye)
        north = (y + 1) < (self.ih - y)
        return {(True, True): "rg", (False, True): "rr",
                (True, False): "rf", (False, False): "ri"}[(west, north)]

    def ok(self, x: int, y: int, tok: str) -> bool:
        if tok in ("sr", "sf", "sg", "sp"):
            return self.send(x, y) == tok
        if tok in ("rr", "rf", "rg", "ri"):
            return self.recv(x, y) == tok
        return True
