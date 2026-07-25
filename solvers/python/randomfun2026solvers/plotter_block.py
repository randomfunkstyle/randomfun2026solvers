#!/usr/bin/env python3
"""A dedicated line-drawing block for `plotter` — two men and a value ring, no CPU.

`plotter.asm` runs Bresenham on the generated LM-1 CPU, so every `err += dy` pays a
fetch → decode-trie → lane → return-path round trip: ~618k ticks inside a 112×106
footprint. Since the score is `max(w,h)² × avgTicks` and the display alone is 34×26,
almost all of both factors is CPU overhead. This replaces it with hardware.

Design notes and the exhaustive proof are in `littleman/PLOTTER-BLOCK.md`. The
reformulation, on the display *address* rather than on x/y:

    U/V  = major/minor axis address step      den = 2*max|d|   step = 2*min|d|
    f    = -max|d|                            (biased, so the carry test is a sign)
    addr = y0*32 + x0
    M+1 x { ADDR <- addr ; DATA <- 15 ; f += step
            f >= 0 ? (f -= den ; addr += U+V) : (addr += U) }

* **worker** owns `f` in B, reads {step, den, U, U+V} off the ring each lap and
  sends the painter one increment per pixel.
* **painter** owns `addr` in B, drives the three LM-75 ports and commits with
  `SWAP <- 0` — which also clears `next`, so each round starts black and only the
  segment's own pixels are ever written.

Only `B` survives an `r`, so each man has exactly one stable word; the round needs
six, which is why it is two men plus a ring rather than one clever man.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.circuit import Circuit
from randomfun2026solvers.value_ring import stamp, walls

__all__ = [
    "OpModel", "worker_round", "painter_replay", "pipe", "PAINTER", "build_display",
    "timing_ok", "DISPLAY_W", "DISPLAY_H",
]

DISPLAY_W, DISPLAY_H = 32, 24
LAP_TICKS = 14          # painter cells per pixel
GAP_ADDR_DATA = 5       # ticks from s@ADDR to s@DATA inside one lap
GAP_DATA_SWAP = 26      # ticks from the last s@DATA to s@SWAP


class OpModel:
    """One little man plus his pipes, at glyph granularity.

    Deliberately not a grid simulator: it models what each *glyph* does to A, B,
    BP, the ring FIFO and the two outgoing pipes, which is the layer where the
    program can be proved correct cheaply. The grid is checked separately, on the
    reference interpreter.
    """

    __slots__ = ("A", "B", "BP", "ring", "paint", "inp", "ops")

    def __init__(self, inputs) -> None:
        from collections import deque

        self.A = self.B = self.BP = 0
        self.ring: deque = deque()
        self.paint: list[int] = []
        self.inp = deque(inputs)
        self.ops = 0

    def do(self, op, arg=None):
        self.ops += 1
        if op == "LIT":     self.A = arg
        elif op == "RIN":   self.A = self.inp.popleft()
        elif op == "M":     self.B = self.A
        elif op == "W":     self.A, self.B = self.B, self.A
        elif op == "ADD":   self.A = self.A + self.B
        elif op == "SUB":   self.A = self.A - self.B
        elif op == "MUL":   self.A = self.A * self.B
        elif op == "NEG":   self.A = -self.A
        elif op == "SHR":   self.A = self.A >> self.B
        elif op == "BP":    self.BP = self.A
        elif op == "PUSH":  self.ring.append(self.A)
        elif op == "POP":   self.A = self.ring.popleft()
        elif op == "PAINT": self.paint.append(self.A)
        else: raise ValueError(op)
        return self

    def run(self, ops):
        for op in ops:
            self.do(*(op if isinstance(op, tuple) else (op,)))
        return self


def painter_replay(base: int, n: int, incs: list[int]) -> list[int]:
    """What the painter does with the worker's stream: the addresses it sends."""
    out, addr = [], base
    for i in range(n):
        out.append(addr)
        addr += incs[i]
    return out


# ── the worker's program, as executable reference semantics ───────────────────
#
# Every line below is exactly one glyph (or one backtick literal), and every
# branch is an `X` on **A** — no glyph can branch on B. `tests/test_plotter_block.py`
# replays this against the problem statement's pseudocode.
#
# The FIFO is what shapes it. A value read early cannot be re-sent late, so the
# send order *is* the next lap's read order; `step` must therefore be the ring's
# first slot (the only constant that combines directly with `f`), and both lanes
# must read all four slots even though each ignores two.
#
# Two tricks keep setup short: `2D` and `2Dy` are computable before the major axis
# is known, so the compare only swaps two values already in hands; and `M` is
# recovered as `den >> 1` in a one-lap rotation preamble, so it needs no slot.
def worker_round(m):
    """Run one round on an op-level model `m` (see tests). Returns the increments."""
    # ── prologue: read all four inputs, park them in consumption order ───────
    # All four RINs live here with only PUSHes between them. That is a *pipe
    # binding* requirement: `r` takes the nearest incoming pipe, so an input `r`
    # must never sit between two ring `r`s. `s` and `r` never compete, so
    # interleaving PUSHes is free.
    m.run(["RIN", "M", "RIN", "PUSH", "W", "PUSH", "PUSH",
           "RIN", "PUSH", "W", "PUSH", "RIN", "PUSH"])
    # ring: [y0, x0, x0, x1, y0, y1]
    m.run([("LIT", 32), "M", "POP", "MUL", "M", "POP", "ADD", "PAINT"])   # base
    m.run(["POP", "M", "POP", "SUB"])                       # A = x1 - x0
    sx = -1 if m.A < 0 else 1                               # X
    m.run(["NEG", "M"] if sx == -1 else ["M"])
    m.run(["ADD", "PUSH"])                                  # ring: ... 2D
    m.run(["POP", "M", "POP", "SUB"])                       # A = y1 - y0
    psy = -32 if m.A < 0 else 32                            # X
    m.run(["NEG", "M"] if psy == -32 else ["M"])
    m.run(["ADD", "PUSH"])                                  # ring: 2D 2Dy
    m.run([("LIT", 1), "PUSH"] if sx == 1 else [("LIT", 1), "NEG", "PUSH"])
    m.run([("LIT", 32), "PUSH"] if psy == 32 else [("LIT", 32), "NEG", "PUSH"])
    m.run(["POP", "M", "POP"])                              # A = 2Dy, B = 2D
    xmajor = m.B >= m.A                                     # X on 2Dy - 2D
    m.run(["PUSH", "W", "PUSH"] if xmajor else ["W", "PUSH", "W", "PUSH"])
    m.run(["POP", "M", "POP"])                              # A = psy, B = sx
    m.run(["W", "PUSH", "W", "ADD", "PUSH"] if xmajor else ["PUSH", "ADD", "PUSH"])
    # ring: [step, den, U, UV]
    m.run(["POP", "PUSH", ("LIT", 1), "M", "POP", "PUSH", "SHR"])     # A = M
    m.run(["ADD", "PAINT", "BP", "SUB", "NEG", "M"])        # n -> painter, BP, B = f
    m.run(["POP", "PUSH", "POP", "PUSH"])                  # ring back into order
    # ── the pixel loop: BP laps, one increment per lap ──────────────────────
    incs = []
    while m.BP > 0:
        m.run(["POP", "PUSH", "ADD"])                      # A = f + step, B = f
        if m.A < 0:                                        # X: no carry
            m.run(["M", "POP", "PUSH", "POP", "PUSH"])     # B = f'; den ignored; U
            incs.append(m.A)
            m.run(["PAINT", "POP", "PUSH"])                # send U; UV ignored
        else:                                              # carry
            m.run(["M", "POP", "PUSH", "W", "SUB", "M"])   # B = f' - den
            m.run(["POP", "PUSH", "POP", "PUSH"])          # U ignored; UV
            incs.append(m.A)
            m.run(["PAINT"])
        m.BP -= 1                                          # m
    return incs


# ── pipes ─────────────────────────────────────────────────────────────────────
_GLYPH = {(1, 0): ">", (-1, 0): "<", (0, -1): "^", (0, 1): "v"}


def pipe(g: Circuit, cells, into) -> int:
    """Draw a pipe along `cells` (flow order); `into` is the border cell it enters.

    `value_ring.draw_pipe` derives the terminal glyph from the direction the pipe
    *arrived*, so it cannot draw a **terminal bend** — one arrowhead that turns and
    points into the wall. SPEC.md allows it ("the terminal arrowhead may itself be
    the final bend") and the display's top port requires it, there being only one
    row above the display. Returns the capacity in cells.
    """
    pts = [cells[0]]
    for (x0, y0), (x1, y1) in zip(cells, cells[1:]):
        if x0 != x1 and y0 != y1:
            raise ValueError(f"pipe leg {(x0, y0)}->{(x1, y1)} is not rectilinear")
        sx, sy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x, y = x + sx, y + sy
            pts.append((x, y))
    n = len(pts)
    for i, (x, y) in enumerate(pts):
        nxt = pts[i + 1] if i + 1 < n else into
        dout = (nxt[0] - x, nxt[1] - y)
        din = (x - pts[i - 1][0], y - pts[i - 1][1]) if i else None
        if i and i < n - 1 and din == dout:
            g.set(x, y, "-" if dout[0] else "|")
        else:
            g.set(x, y, _GLYPH[dout])
    return n


# ── the painter ───────────────────────────────────────────────────────────────
#
# Interior 15x5. The lap is rows 1-2, columns 0..6 — 14 cells, so 14 ticks per
# pixel:
#
#     d s ` 1 5 ` v          `d` sits on the corner the man enters heading north,
#     ^ m M + r s <          so BP>0 turns him east into the lap and BP==0 goes
#                            straight north, out of it.
#
# `+` then `M` leave A = B = addr', so the next lap re-enters `s@ADDR` with the
# address already in A. The exit takes the long way round (row 0, then down the
# east side) purely so `s@SWAP` lands far from the ADDR/DATA pipe columns — all
# three pipes leave the north wall and `s` binds by distance.
PAINTER = [
    ">.............v",
    "ds`15`v........",
    "^mM+rs<........",
    "..........@v...",
    "^....MWbrMr<s0<",
]
PAINTER_W, PAINTER_H = 15, 5
S_ADDR, S_DATA, S_SWAP = 1, 5, 12       # interior columns of the three sends


def painter_rows() -> list[str]:
    c = Circuit(PAINTER_W, PAINTER_H)
    for y, row in enumerate(PAINTER):
        for x, ch in enumerate(row):
            c.set(x, y, " " if ch == "." else ch)
    return c.rows()


def timing_ok(l_addr: int, l_data: int, l_swap: int) -> bool:
    """Do the three display pipes deliver in the order the display needs?

    Pipe latency is one tick per cell, so the *lengths* are part of the program.
    Three conditions, all found the hard way:

    * `ADDR_i` must not arrive after `DATA_i` — else the pixel lands at the old
      cursor: ``l_addr - GAP_ADDR_DATA <= l_data``.
    * `DATA_i` must arrive before `ADDR_{i+1}`, one lap later:
      ``l_data < l_addr + LAP_TICKS - GAP_ADDR_DATA``.
    * `SWAP` must not overtake the DATA writes still in flight when the lap ends —
      the first version committed with the last two pixels still in the pipe:
      ``l_swap > l_data - GAP_DATA_SWAP``.
    """
    return (l_addr - GAP_ADDR_DATA <= l_data < l_addr + LAP_TICKS - GAP_ADDR_DATA
            and l_swap > l_data - GAP_DATA_SWAP)


def build_display(g: Circuit, dx: int, dy: int) -> None:
    """Stamp the LM-75: '+' corners, '=' horizontal walls, ':' vertical walls."""
    w, h = DISPLAY_W + 2, DISPLAY_H + 2
    for x in range(dx, dx + w):
        for y in (dy, dy + h - 1):
            g.set(x, y, "+" if x in (dx, dx + w - 1) else "=")
    for y in range(dy + 1, dy + h - 1):
        g.set(dx, y, ":")
        g.set(dx + w - 1, y, ":")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path, help="write the grid here")
    args = ap.parse_args()
    raise SystemExit("worker layout not wired up yet — see PLOTTER-BLOCK.md")
