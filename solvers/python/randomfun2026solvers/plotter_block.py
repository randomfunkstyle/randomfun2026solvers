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
    # ── drain: the round has to leave the ring as it found it ────────────────
    # Every lap pops all four constants and pushes all four back to stay aligned, so
    # when BP runs out `[step, den, U, UV]` are still circulating. The next round's
    # prologue would pop *those* instead of its own x0/y0 — so a round is only
    # re-entrant if it consumes them first. Four rounds of the block ran perfectly in
    # isolation and drew garbage in sequence before this went in.
    m.run(["POP"] * 4)
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
WW, WH = 39, 19                      # worker interior
IN_COL, RET_COL = 0, 28              # north wall: input, ring-return
FWD_COL, PNT_COL = 30, 38            # south wall: ring-forward, painter
# binding regions that follow from those four columns: the boundary is the midpoint,
# and a glyph exactly on it would be a reading-order tie, so both sides exclude it.
RIN_MAX, POP_MIN, PUSH_MAX, PAINT_MIN = 13, 15, 33, 35


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


# ── the worker's grid ────────────────────────────────────────────────────────
def _riser(c, x, y0, y1, g="^"):
    """Fill a column of `g` from y0 down to y1 inclusive (order does not matter)."""
    for y in range(min(y0, y1), max(y0, y1) + 1):
        c.set(x, y, g)


def build_worker():
    c = Circuit(WW, WH)
    # ── the round is a loop, so the spawn and the return leg must merge ────────
    # The returning man arrives heading north up column 0 and is turned east by `>`
    # at (0,0). `@` sits on the prologue row itself rather than costing a row of its
    # own: it spawns heading east — already the right way — and is *otherwise a nop*,
    # so the returning man simply walks over it.
    c.set(0, 0, ">")
    c.set(1, 0, "@")

    # ── prologue, row 0: all four RINs sit at x <= RIN_MAX ────────────────────
    cur = Cur(c, 2, 0, E)
    cur.seq([P(o) for o in ["RIN", "M", "RIN", "PUSH", "W", "PUSH", "PUSH",
                            "RIN", "PUSH", "W", "PUSH", "RIN", "PUSH"]])
    cur.turn(S)                                   # (15,0) -> down
    c.set(15, 1, ">")                             # turn east into the base block

    # ── base, row 1 (east): 5 M r { M r +   ... s@PAINT ──────────────────────
    cur = Cur(c, 16, 1, E)
    cur.seq([P(o) for o in [("LIT", 5), "M", "POP", "SHL", "M", "POP", "ADD"]])
    cur.to(PAINT_MIN).op(*P("PAINT"))
    cur.turn(S)
    _riser(c, cur.x, 2, 3, "v")
    c.set(cur.x, 4, "<")                          # band B is walked WEST

    # ── band B: row 5 — TWO branches share it (disjoint column ranges) ────────
    cur = Cur(c, cur.x - 1, 4, W)
    cur.seq([P(o) for o in ["POP", "M", "POP", "SUB"]])        # A = x1-x0, B = x0
    branch(c, cur, W,
           neg=["NEG", "M", "ADD", "PUSH", ("LIT", 1), "NEG", "PUSH"],
           pos=["M", "ADD", "PUSH", ("LIT", 1), "PUSH"])
    cur.seq([P(o) for o in ["POP", "M", "POP", "SUB"]])        # A = y1-y0
    branch(c, cur, W,
           neg=["NEG", "M", "ADD", "PUSH", ("LIT", 5), "M", ("LIT", 1), "SHL",
                "NEG", "PUSH"],
           pos=["M", "ADD", "PUSH", ("LIT", 5), "M", ("LIT", 1), "SHL", "PUSH"])
    cur.to(2).turn(S)                             # down column 2 into band C
    _riser(c, 2, 5, 6, "v")
    c.set(2, 7, ">")

    # ── band C: row 7 — the major-axis compare ───────────────────────────────
    cur = Cur(c, 3, 7, E)
    cur.to(POP_MIN)
    cur.seq([P(o) for o in ["POP", "M", "POP", "PUSH", "POP", "SUB"]])
    branch(c, cur, E,
           neg=["ADD", "PUSH", "W", "PUSH", "POP", "M", "POP", "PUSH", "ADD", "PUSH"],
           pos=["W", "PUSH", "ADD", "PUSH", "POP", "M", "POP", "W", "PUSH", "ADD",
                "PUSH"],
           neg_is_le=True)
    # Band C's merge already lands east of the lane on the row below, so the exit can
    # drop one row and turn straight back west — no need to run out to the east wall
    # first and descend twice, which is what this used to do.
    cur.turn(S)
    c.set(cur.x, 9, "<")

    # ── preamble: recover M as den>>1, send n, set BP, leave B = f ────────────
    # Row 9 is only a corridor: the block needs a POP first and a PAINT last, so it
    # has to run west-to-east, and band C left the man on the east side.
    Cur(c, cur.x - 1, 9, W).to(15)
    c.set(15, 9, "v")
    c.set(15, 10, ">")
    cur = Cur(c, 16, 10, E)
    cur.seq([P(o) for o in ["POP", "PUSH", ("LIT", 1), "M", "POP", "PUSH", "SHR",
                            "ADD"]])
    cur.to(PAINT_MIN).op(*P("PAINT"))             # n -> painter
    cur.turn(S)                                   # the tail rides the return leg,
    c.set(cur.x, 11, "<")                         # which keeps the room 40 wide
    cur = Cur(c, cur.x - 1, 11, W)
    cur.seq([P(o) for o in ["BP", "SUB", "NEG", "M"]])         # BP = n, B = f
    cur.seq([P(o) for o in ["POP", "PUSH", "POP", "PUSH"]])    # ring back in order
    cur.to(4).turn(S)                             # column 4 down into the loop
    c.set(4, 12, "v")
    c.set(4, 13, ">")

    # ── the pixel loop: five rows, because four paths must not cross ─────────
    #
    #   12 |         > M r s r s ......... s@P r  v |   no-carry code (CCW exit)
    #   13 | > ... r s + X > M r s W - M r s r s .. s@P v |   shared + carry (straight+CW)
    #   14 |         > ^                        v      |   the CW exit's two-cell hop
    #   15 |   ^   < < < < ...................  <      |   carry's return leg
    #   16 | ^ d m < ................ s ......  <      |   no-carry's return leg
    #
    # A >= 0 is the carry, so the carry lane owns *both* the straight and the CW exit
    # and the CW exit needs a two-cell hop back onto the shared row. That hop is why
    # row 14 cannot also be a return corridor, and the two lanes cannot share one
    # return row either: the no-carry lane still owes a PUSH after its PAINT, and the
    # carry lane must not execute it.
    cur = Cur(c, 5, 13, E)
    cur.to(POP_MIN)
    cur.seq([P(o) for o in ["POP", "PUSH", "ADD"]])            # A = f + step, B = f
    x, y = cur.x, cur.y
    c.set(x, y, "X")
    c.set(x, y + 1, ">")                          # CW exit: out, along, and back up
    c.set(x + 1, y + 1, "^")
    c.set(x + 1, y, ">")                          # the merge cell
    lane = Cur(c, x + 2, y, E)
    lane.seq([P(o) for o in ["M", "POP", "PUSH", "W", "SUB", "M",
                             "POP", "PUSH", "POP", "PUSH"]])   # B = f' - den; UV
    lane.to(PAINT_MIN).op(*P("PAINT"))            # the increment is U + V
    lane.turn(S)                                  # down to its return leg
    c.set(lane.x, 14, "v")
    c.set(lane.x, 15, "<")
    Cur(c, lane.x - 1, 15, W).to(6)
    c.set(5, 15, "v")                             # drop onto the shared merge row

    # ── the no-carry lane: PAINT cannot be last ──────────────────────────────
    # `A` is clobbered by every `r`, so the increment must be sent *before* UV is
    # recycled — hence one crossing east to the painter and a return leg west.
    c.set(x, y - 1, ">")                          # CCW exit
    nc = Cur(c, x + 1, y - 1, E)
    nc.seq([P(o) for o in ["M", "POP", "PUSH", "POP", "PUSH"]])
    nc.to(PAINT_MIN).op(*P("PAINT"))              # the increment is U
    nc.seq([P(o) for o in ["POP"]])               # UV, read here...
    nc.to(nc.x + 1).turn(S)
    _riser(c, nc.x, 13, 15, "v")
    c.set(nc.x, 16, "<")
    nc = Cur(c, nc.x - 1, 16, W)
    nc.to(PUSH_MAX).op(*P("PUSH"))                # ...and pushed back, west of 34
    nc.to(5)

    # ── one `m` per lap, then the BP test ────────────────────────────────────
    c.set(5, 16, "<")                             # carry arrives south, no-carry west
    c.set(4, 16, "m")                             # BP -= 1
    c.set(3, 16, "d")                             # BP > 0 ? north into the loop : on
    _riser(c, 3, 14, 15)
    c.set(3, 13, ">")                             # ...back to the loop head

    # ── BP == 0: drain the ring, then round again ────────────────────────────
    # The four constants are still circulating (see `worker_round`), and the next
    # round's prologue would pop them instead of its own inputs. Two extra rows buy
    # the four POPs: east to the POP region, drain, then west and up column 0.
    c.set(2, 16, "v")
    c.set(2, 17, ">")
    drain = Cur(c, 3, 17, E)
    drain.to(POP_MIN)
    drain.seq([P("POP")] * 4)
    drain.turn(S)
    c.set(drain.x, 18, "<")
    Cur(c, drain.x - 1, 18, W).to(1)
    c.set(0, 18, "^")                             # up column 0 into the prologue
    _riser(c, 0, 1, 17)
    return c


# ── the worker probe: the worker alone, its increment pipe pointed at an O room ─
#
# Program output is then exactly the `base, n, inc...` stream that `worker_round` is
# proved to produce over all 589,824 segments, so one string compare on the
# reference interpreter checks the whole grid — every branch, the ring's FIFO order,
# the four pipe bindings and the BP-counted loop — without the painter or display in
# the way. Verified on (0,0,31,23), its reverse, (0,23,31,0), (5,5,5,20), (3,7,29,7)
# and the single point (0,0,0,0).
PROBE_WX, PROBE_WY = 1, 4                      # worker interior origin -> walls rows 3..23
IN_C, RET_C, FWD_C, PNT_C = 1, 29, 31, 39     # grid columns of the four ports


# ── the assembled box ─────────────────────────────────────────────────────────
#
# Four rooms and seven pipes. The room *order* is forced (see PLOTTER-BLOCK.md); what
# took the longest was the pipes, because the worker spans nearly the full width, so
# every pipe crossing between the halves needs a column clear of it — a *channel* —
# and they compete for a handful. Three placements settle it:
#
# * **The increment descends straight down.** It may not bend before the row two
#   below the worker's south wall, and any sideways leg there cuts a channel; going
#   straight down leaves both sides clear and makes it a plain L round the bottom into
#   the painter's *west* wall (its north wall is the three display sends).
# * **The relay talks east and north.** One pipe each way means no `s`/`r` in it needs
#   a binding argument, so either wall is free: in on the east makes ring-forward a
#   single drop, out on the north puts the return in the open band above the painter
#   rather than the fenced-in bottom.
# * **The input room sits west of the worker.** The return has to cross the band over
#   the worker's north wall to reach its port; the input has to land at the west end of
#   the same band. From above they cross; rising from the west the input owns one row
#   of the band and the return another.
#
# The paths are *declared*, not searched. A shortest-path router solves each pipe
# locally and eats the one row a later pipe needed, so the collision only ever moves;
# stating all seven and allocating the shared rows and columns up front is what closes
# the box. `_PATHS` is checked against the ports as it is drawn.
BLOCK_W, BLOCK_H = 49, 64
_DX, _DY = 9, 1                  # display walls: cols 9..42, rows 1..26
_WX, _WY = 9, 31                 # worker walls:  cols 8..49, rows 30..50
_PX, _PY = 9, 57                 # painter walls: cols 8..24, rows 56..62
_RX, _RY = 34, 56                # relay walls:   cols 33..37, rows 55..62
_IX, _IY = 4, 31                 # the input room, west of the worker

# Shared resources, allocated before anything is drawn:
#   band over the worker's north wall   row 27 SWAP, row 28 ring-return, row 29 input
#   west channels (painter -> display)  col 0 ADDR, col 1 DATA, col 2 SWAP
#   row each display pipe bends west    54 ADDR, 53 DATA, 52 SWAP
# Channels and bend rows nest opposite ways (0<1<2 against 54>53>52), which is what
# keeps the three display pipes from crossing; the band rows are one each.
#
# The channels are *adjacent*, not spaced. Two parallel pipes may touch — a pipe is
# followed by its own arrowheads, so neighbouring flows never merge (the painter probe
# ran two touching channels for 28 rows and drew a correct frame). Closing those gaps,
# and bringing the increment's return leg up under the relay instead of three rows
# below it, took the box from 57x70 to 50x67.
_PATHS = {
    "fwd":   ([(39, 51), (39, 58), (38, 58)], "r_in"),
    "pnt":   ([(47, 51), (47, 63), (7, 63), (7, 59)], "p_in"),
    "r_out": ([(35, 54), (35, 51), (3, 51), (3, 28), (37, 28), (37, 29)], "ret"),
    "i_out": ([(5, 30), (5, 29), (9, 29)], "in"),
    "addr":  ([(10, 55), (10, 54), (0, 54), (0, 0), (10, 0)], "d_addr"),
    "data":  ([(14, 55), (14, 53), (1, 53), (1, 3), (8, 3)], "d_data"),
    "swap":  ([(21, 55), (21, 52), (2, 52), (2, 27), (39, 27)], "d_swap"),
}


def _block_ports():
    """Each port is a wall cell plus its outward normal, which forces the two cells
    the pipe must start (or end) with — the stub. A first cell that runs *along* the
    wall instead leaves the analyser unable to name the source room (`src: -1`), and
    then every `s` in it silently binds to nothing."""
    wn, ws = _WY - 1, _WY + WH
    pn, pw = _PY - 1, _PX - 1
    return {
        "in":     ((_WX + 0, wn), N),          # worker: input, ring-return
        "ret":    ((_WX + 28, wn), N),
        "fwd":    ((_WX + 30, ws), S),         # worker: ring-forward, increment
        "pnt":    ((_WX + 38, ws), S),
        # The relay's own north wall, not the painter's: it rides one row higher so
        # both rooms' south walls line up and the increment's return leg can come up
        # under them instead of clearing the lower of the two.
        "r_in":   ((_RX + RELAY_W, _RY + 2), E),
        "r_out":  ((_RX + 1, _RY - 1), N),
        "p_in":   ((pw, _PY + 2), W),
        "addr":   ((_PX + S_ADDR, pn), N),     # painter: the three display sends
        "data":   ((_PX + S_DATA, pn), N),
        "swap":   ((_PX + S_SWAP, pn), N),
        "d_addr": ((_DX + 1, _DY), N),         # display: top, left, bottom
        "d_data": ((_DX, 3), W),
        # SWAP enters the bottom wall near its *east* end, past where the ring-return
        # turns in: nearer the west end its own stub sits across the return's row, the
        # return detours onto row 27 to get round it, and row 27 is SWAP's only way in.
        "d_swap": ((_DX + 30, _DY + DISPLAY_H + 1), S),
        "i_out":  ((_IX + 1, _IY), N),
    }


def build_block() -> list[str]:
    """The whole plotter block: display, worker, painter, relay and input room."""
    g = Circuit(BLOCK_W, BLOCK_H)
    build_display(g, _DX, _DY)
    stamp(g, _WX, _WY, build_worker().rows())
    walls(g, _WX, _WY, WW, WH)
    stamp(g, _PX, _PY, painter_rows())
    walls(g, _PX, _PY, PAINTER_W, PAINTER_H)
    stamp(g, _RX, _RY, relay_rows())
    walls(g, _RX, _RY, RELAY_W, RELAY_H)
    stamp(g, _IX, _IY, ["+-+", "|I|", "+-+"])

    ports, lens = _block_ports(), {}
    for src, (cells, dst) in _PATHS.items():
        (sp, sn), (dp, dn) = ports[src], ports[dst]
        if cells[0] != (sp[0] + sn[0], sp[1] + sn[1]):
            raise ValueError(f"{src}: {cells[0]} is not the stub outside {sp}")
        if cells[-1] != (dp[0] + dn[0], dp[1] + dn[1]):
            raise ValueError(f"{src}: {cells[-1]} is not beside {dst} at {dp}")
        lens[src] = pipe(g, cells, into=dp)
    if not timing_ok(lens["addr"], lens["data"], lens["swap"]):
        raise ValueError(f"display pipes deliver out of order: {lens}")
    return [r.rstrip() for r in g.rows()]


def block_debug():
    """A labelled overlay of the assembled block, for reading the layout by eye.

    Every rectangle is derived from the same constants `build_block` draws from, so it
    cannot drift out of step with the grid the way hand-written coordinates did.
    """
    from randomfun2026solvers.man_debug import DebugMap

    wi, pn = _WY, _PY - 1                    # worker interior row 0; painter north wall
    d = DebugMap("plotter block — 20/20, 49x64, score 53,693,850 "
                 "(CPU version: 7,760,316,749)")
    d.region("display", _DX, _DY, DISPLAY_W + 2, DISPLAY_H + 2,
             note="LM-75 32x24. Top wall = ADDR, left = DATA, bottom = SWAP. The panel "
                  "alone is 34x26, which is why a CPU here costs almost everything.",
             color="#334155")
    d.region("worker", _WX - 1, _WY - 1, WW + 2, WH + 2,
             note=f"owns f in B; reads {{step, den, U, U+V}} off the ring each lap and "
                  f"sends the painter one increment per pixel. {WW}x{WH} interior, and "
                  "it spans nearly the full width — which is what makes channels scarce.",
             color="#0ea5e9")
    d.region("worker:prologue", _WX, wi, 16, 1,
             note="`>` turns the returning man east and `@` sits on this row rather than "
                  "costing one of its own: it spawns heading east already, and is "
                  "otherwise a nop, so the returner walks over it. All four input reads "
                  "live here with only pushes between them — `r` takes the *nearest* "
                  "incoming pipe, so an input read must never sit between two ring pops.",
             color="#22c55e")
    d.region("worker:bandB", _WX, wi + 4, WW, 2,
             note="the two sign branches, sharing one row on disjoint column ranges. "
                  "Each lane pushes its own sign literal: a lane's identity is the man's "
                  "position, so it cannot survive the merge.",
             color="#84cc16")
    d.region("worker:bandC", _WX, wi + 6, WW, 3,
             note="the major-axis compare. Its merge lands east of the lane on the row "
                  "below, so the exit drops one row and turns straight back west.",
             color="#06b6d4")
    d.region("worker:loop", _WX + 3, wi + 12, WW - 3, 5,
             note="the pixel loop, five rows because four paths must not cross. `X` "
                  "branches on the sign of f+step; the carry lane owns the straight and "
                  "the CW exit, so its two-cell hop back cannot double as a corridor.",
             color="#f59e0b")
    d.region("worker:drain", _WX, wi + 17, 20, 2,
             note="four POPs. Each lap pushes all four constants back, so at BP==0 they "
                  "are still circulating and the next round would pop them instead of "
                  "its own x0/y0. Every segment alone was perfect and sequences were "
                  "garbage until this went in.",
             color="#ef4444")
    d.region("painter", _PX - 1, pn, PAINTER_W + 2, PAINTER_H + 2,
             note="owns addr in B; one lap per pixel, 14 ticks. Commits with SWAP<-0, "
                  "which also clears `next`, so each round starts black.",
             color="#a855f7")
    d.region("relay", _RX - 1, _RY - 1, RELAY_W + 2, RELAY_H + 2,
             note="the ring's turnaround, four values per 14-cell circuit. One pipe each "
                  "way, so nothing in it needs a binding argument — which is why it can "
                  "talk out of whichever walls the routing needs. It rides a row above "
                  "the painter so both south walls line up.",
             color="#ec4899")
    d.region("input", _IX, _IY, 3, 3,
             note="west of the worker, not above it: from above, the input pipe would "
                  "cross the ring-return in the band over the north wall.",
             color="#64748b")
    d.region("band", 0, _WY - 4, BLOCK_W, 3,
             note="the contended band: SWAP, then the ring-return, then the input — one "
                  "row each, allocated before any pipe was drawn.",
             color="#eab308")
    d.region("channels", 0, 0, 4, pn - 1,
             note="cols 0/1/2 carry ADDR/DATA/SWAP up past the worker, and col 3 the "
                  "ring-return. Adjacent, not spaced: a pipe carries its own arrowheads, "
                  "so neighbouring flows never merge. They nest against the bend rows "
                  "the opposite way round, which keeps the three from crossing.",
             color="#14b8a6")
    return d


def build_worker_probe():
    south, north = PROBE_WY + WH, PROBE_WY - 1
    g = Circuit(58, south + 18)
    stamp(g, PROBE_WX, PROBE_WY, build_worker().rows())
    walls(g, PROBE_WX, PROBE_WY, WW, WH)

    rx, ry = 6, south + 4                        # relay, clear of the worker's wall
    stamp(g, rx, ry, relay_rows())
    walls(g, rx, ry, RELAY_W, RELAY_H)

    # ring: worker south -> relay north, relay south -> the long way round -> worker
    #
    # A pipe's first cell must point *away* from the wall it leaves: the analyser
    # derives the source room from the cell behind the flow, so a first leg that runs
    # along the wall gets `src: -1` and no `s` in the room can ever bind to it.
    n_fwd = pipe(g, [(FWD_C, south + 1), (FWD_C, ry - 2), (rx + 1, ry - 2)],
                 into=(rx + 1, ry - 1))
    n_ret = pipe(g, [(rx, ry + RELAY_H + 1), (rx, g.h - 2), (50, g.h - 2), (50, 2),
                     (RET_C, 2)], into=(RET_C, north))

    # the increment pipe, standing in for the painter
    stamp(g, 43, ry - 1, ["+-+", "|O|", "+-+"])
    pipe(g, [(PNT_C, south + 1), (PNT_C, ry), (42, ry)], into=(43, ry))
    # and the input
    stamp(g, 55, 0, ["+-+", "|I|", "+-+"])
    pipe(g, [(54, 1), (IN_C, 1), (IN_C, 2)], into=(IN_C, north))

    return [r.rstrip() for r in g.rows()]
