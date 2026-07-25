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

from randomfun2026solvers.circuit import Circuit, E, N, S, W
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
        elif op == "SHR":   self.A = self.A >> self.B      # `}`
        elif op == "SHL":   self.A = self.A << self.B      # `{`
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
    # No backtick literals anywhere in the worker: they pair *vertically* across the
    # whole grid, so a multi-digit literal would make every column a bookkeeping
    # problem. y0*32 is y0<<5 (one digit), and 32 itself is 1<<5.
    m.run([("LIT", 5), "M", "POP", "SHL", "M", "POP", "ADD", "PAINT"])    # base
    # Each lane pushes its **own** sign literal. Pushing them after the merge would
    # mean the merged code had to remember which lane ran, and a lane's identity
    # lives only in the man's position — so it cannot survive a merge.
    m.run(["POP", "M", "POP", "SUB"])                       # A = x1 - x0, B = x0
    if m.A < 0:                                             # X
        m.run(["NEG", "M", "ADD", "PUSH", ("LIT", 1), "NEG", "PUSH"])   # 2D, sx=-1
    else:
        m.run(["M", "ADD", "PUSH", ("LIT", 1), "PUSH"])                 # 2D, sx=+1
    m.run(["POP", "M", "POP", "SUB"])                       # A = y1 - y0
    if m.A < 0:                                             # X
        m.run(["NEG", "M", "ADD", "PUSH", ("LIT", 5), "M", ("LIT", 1),
               "SHL", "NEG", "PUSH"])                    # 2Dy, psy = -32
    else:
        m.run(["M", "ADD", "PUSH", ("LIT", 5), "M", ("LIT", 1),
               "SHL", "PUSH"])                           # 2Dy, psy = +32
    # ring: [2D, sx, 2Dy, psy]. Rotate sx to the tail so 2D and 2Dy can meet in
    # the two hands — the compare then only swaps them.
    m.run(["POP", "M", "POP", "PUSH", "POP", "SUB"])        # A = 2Dy - 2D, B = 2D
    # `SUB` is the compare, and it consumes 2Dy -- but each lane can recover it as
    # diff + 2D, so no copy has to be parked anywhere.
    # U and V depend on the major axis as well, so the whole of it lives inside the
    # lanes: a lane's identity is the man's position and cannot survive the merge.
    if m.A <= 0:                                            # X: x-major
        m.run(["ADD", "PUSH", "W", "PUSH"])                 # step = 2Dy, den = 2D
        m.run(["POP", "M", "POP"])                           # A = sx, B = psy
        m.run(["PUSH", "ADD", "PUSH"])                       # U = sx, UV = sx + psy
    else:                                                   # y-major
        m.run(["W", "PUSH", "ADD", "PUSH"])                 # step = 2D,  den = 2Dy
        m.run(["POP", "M", "POP"])                           # A = sx, B = psy
        m.run(["W", "PUSH", "ADD", "PUSH"])                  # U = psy, UV = psy + sx
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


# ── the relay: the ring's turnaround ──────────────────────────────────────────
#
# Interior 3x6. One circuit of 14 cells carrying **four** values — column 2 walked
# south and column 0 walked north, each holding two (r,s) pairs — so 3.5
# ticks/value, which keeps up with the worker's ~19-tick lap. A single-pair relay
# would forward one value per circuit and throttle the whole machine.
#
# `>` at (0,0) rather than the spawn: the returning man arrives heading north and
# needs turning east, and `@` is only a nop, so it cannot do that. The spawn sits
# at (1,0) instead, where heading east is already correct.
#
# One incoming pipe and one outgoing, so no `s`/`r` here needs a binding argument.
RELAY = [">@v", "s.r", "r.s", "s.r", "r.s", "^.<"]
RELAY_W, RELAY_H = 3, 6


def relay_rows() -> list[str]:
    return [row.replace(".", " ") for row in RELAY]


# ── the painter probe: everything verified so far, as a runnable grid ─────────
def build_probe() -> tuple[list[str], object]:
    """Display + painter + the three port pipes, driven straight from the input room.

    The input pipe stands in for the worker, so the probe speaks the worker's exact
    protocol — `base, n, inc...` — and a correct frame here means the painter, all
    three LM-75 ports and the pipe-length timing are right. This is the artifact
    that drew a matching main diagonal on the reference interpreter.
    """
    from randomfun2026solvers.man_debug import DebugMap

    w, h = 37, 40
    dx, dy = 2, 1                       # display walls: cols 2..35, rows 1..26
    px, py = 14, 33                     # painter interior origin
    data_row, addr_col, swap_col = 2, 3, 3

    g = Circuit(w, h)
    build_display(g, dx, dy)
    stamp(g, px, py, painter_rows())
    walls(g, px, py, PAINTER_W, PAINTER_H)

    l_addr = pipe(g, [(px + S_ADDR, 31), (px + S_ADDR, 30), (0, 30), (0, 0),
                      (addr_col, 0)], into=(addr_col, dy))
    l_data = pipe(g, [(px + S_DATA, 31), (px + S_DATA, 29), (1, 29), (1, data_row)],
                  into=(dx, data_row))
    l_swap = pipe(g, [(px + S_SWAP, 31), (px + S_SWAP, 28), (w - 1, 28), (w - 1, 27),
                      (swap_col, 27)], into=(swap_col, dy + DISPLAY_H + 1))
    if not timing_ok(l_addr, l_data, l_swap):
        raise ValueError(f"pipe lengths deliver out of order: ADDR {l_addr}, "
                         f"DATA {l_data}, SWAP {l_swap}")
    stamp(g, 33, 33, ["+-+", "|I|", "+-+"])
    pipe(g, [(32, 34), (30, 34)], into=(29, 34))

    d = DebugMap(f"plotter block — painter probe (ADDR {l_addr} / DATA {l_data} / "
                 f"SWAP {l_swap} cells)")
    d.region("display", dx, dy, DISPLAY_W + 2, DISPLAY_H + 2,
             note="LM-75 32x24. Top wall = ADDR, left = DATA, bottom = SWAP.",
             color="#334155")
    d.region("painter", px - 1, py - 1, PAINTER_W + 2, PAINTER_H + 2,
             note="owns addr in B; one lap per pixel", color="#0ea5e9")
    d.region("painter:lap", px, py + 1, 7, 2,
             note="14 cells = 14 ticks/pixel: d s `15` v / < s r + M m ^. "
                  "`+` then `M` leave A=B=addr', so the lap re-enters s@ADDR loaded.",
             color="#22c55e")
    d.region("painter:commit", px, py + 4, PAINTER_W, 1,
             note="BP hits 0 -> the long way round to `0 s@SWAP`, then r M reads the "
                  "next base. The detour exists so s@SWAP lands far from the ADDR and "
                  "DATA pipe columns — all three leave the north wall and s binds by "
                  "distance.",
             color="#f59e0b")
    d.region("painter:spawn", px + 10, py + 3, 2, 1,
             note="@ then v, merging into the preamble *after* s@SWAP — spawning "
                  "before it would commit a black frame and fail the streaming compare.",
             color="#a855f7")
    d.region("pipe:ADDR", 0, 0, 2, 31,
             note=f"{l_addr} cells, up the west side and over the top. Length is part "
                  "of the program: ADDR must not arrive after its own DATA.",
             color="#ef4444")
    d.region("pipe:SWAP", w - 2, 27, 2, 2,
             note=f"{l_swap} cells, deliberately long. A short SWAP overtakes the DATA "
                  "writes still in flight and commits with the last pixels missing.",
             color="#ec4899")
    d.region("input", 33, 33, 3, 3,
             note="stands in for the worker: base, n, inc x n", color="#64748b")
    return [r.rstrip() for r in g.rows()], d


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write a labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args()
    rows, dbg = build_probe()
    if args.man:
        args.man.write_text("\n".join(rows) + "\n")
    if args.html:
        dbg.write_html(rows, args.html)
    if args.json:
        dbg.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(rows))


# ── laying the worker out: the four binding regions, enforced while drawing ───
#
# Two pipes on the *same wall* a few columns apart give a boundary that depends
# only on x, because the row term is identical in both Manhattan distances. That
# is what makes the worker layable at all, and it turns four global rules into
# four column ranges:
#
#     input  @ N col 0   ring-return @ N col 24  ->  RIN: x <= 11   POP: x >= 13
#     ring-fwd @ S col 28  painter   @ S col 38  ->  PUSH: x <= 32  PAINT: x >= 34
#
# `Cur` checks each one as the glyph is placed, so a mis-bound send is a build
# error rather than a grid that loads and quietly reads the wrong pipe.

GLYPH = {E: ">", W: "<", N: "^", S: "v"}
WW, WH = 40, 18                      # worker interior
IN_COL, RET_COL = 0, 24              # north wall: input, ring-return
FWD_COL, PNT_COL = 28, 38            # south wall: ring-forward, painter
# binding regions that follow from those four columns
RIN_MAX, POP_MIN, PUSH_MAX, PAINT_MIN = 11, 13, 32, 34


class Cur:
    """A cursor that lays glyphs along its heading and checks the pipe regions."""

    def __init__(self, c, x, y, d):
        self.c, self.x, self.y, self.d = c, x, y, d

    def op(self, g, kind=None):
        if kind == "RIN" and self.x > RIN_MAX:
            raise ValueError(f"RIN at x={self.x} binds to the ring, not the input")
        if kind == "POP" and self.x < POP_MIN:
            raise ValueError(f"POP at x={self.x} binds to the input, not the ring")
        if kind == "PUSH" and self.x > PUSH_MAX:
            raise ValueError(f"PUSH at x={self.x} binds to the painter, not the ring")
        if kind == "PAINT" and self.x < PAINT_MIN:
            raise ValueError(f"PAINT at x={self.x} binds to the ring, not the painter")
        self.c.set(self.x, self.y, g)
        self.step()
        return self

    def step(self):
        self.x += self.d[0]
        self.y += self.d[1]

    def seq(self, items):
        for g, kind in items:
            self.op(g, kind)
        return self

    def to(self, col):
        while self.x != col:
            self.c.set(self.x, self.y, " ")
            self.step()
        return self

    def turn(self, d):
        self.c.set(self.x, self.y, GLYPH[d])
        self.d = d
        self.step()
        return self


# op -> (glyph, binding kind)
def P(o):
    return {
        "RIN": ("r", "RIN"), "POP": ("r", "POP"), "PUSH": ("s", "PUSH"),
        "PAINT": ("s", "PAINT"), "M": ("M", None), "W": ("W", None),
        "ADD": ("+", None), "SUB": ("-", None), "NEG": ("N", None),
        "SHL": ("{", None), "SHR": ("}", None), "BP": ("b", None),
    }[o] if isinstance(o, str) else (str(o[1]), None)


CWD = {E: S, S: W, W: N, N: E}
CCWD = {v: k for k, v in CWD.items()}


def branch(c, cur, d, neg, pos, neg_is_le=False):
    """`X` at the cursor. A<0 takes the CCW exit, A>0 the CW exit, A==0 straight.

    Two of the three exits always share code, so the straight row carries it and the
    turning lane is routed onto that row *before* the first op — arriving on a code
    glyph would leave the man's heading unchanged and walk him off the lane.

    `neg_is_le` flips which pair shares: the compare needs A<=0 (x-major) together.
    """
    x, y = cur.x, cur.y
    c.set(x, y, "X")
    cw, ccw = CWD[d], CCWD[d]
    shared, single = (neg, pos) if neg_is_le else (pos, neg)
    join, solo = (ccw, cw) if neg_is_le else (cw, ccw)

    back = (-join[0], -join[1])
    c.set(x + join[0], y + join[1], GLYPH[d])              # step out, head along d
    c.set(x + join[0] + d[0], y + join[1] + d[1], GLYPH[back])   # and back onto the row
    c.set(x + d[0], y + d[1], GLYPH[d])                    # the merge cell itself
    sc = Cur(c, x + 2 * d[0], y + 2 * d[1], d)
    sc.seq([P(o) for o in shared])

    c.set(x + solo[0], y + solo[1], GLYPH[d])
    so = Cur(c, x + solo[0] + d[0], y + solo[1] + d[1], d)
    so.seq([P(o) for o in single])

    far = max(sc.x, so.x) if d == E else min(sc.x, so.x)
    sc.to(far)
    so.to(far)
    c.set(so.x, so.y, GLYPH[(-solo[0], -solo[1])])         # solo turns back
    c.set(sc.x, sc.y, GLYPH[d])                            # shared passes through
    cur.x, cur.y, cur.d = sc.x + d[0], sc.y + d[1], d
    return cur



