#!/usr/bin/env python3
"""A dataflow ring machine for `snake` — no CPU, no ISA, no ROM.

`snake` is scored `max(w,h)^2 x avg_ticks` on a 16x16 display, so the 18x18 panel
room is most of the bounding box and the panel harness is the dominant term.  The
game state is tiny (the longest snake across the public cases is **6** cells), so
the body lives as a handful of values circulating in a pipe ring rather than in a
tape or a bitmap: a ring *is* a FIFO, which is exactly what a snake body is.

This module is built bottom-up and each stage is a runnable, engine-checked grid:

* :func:`build_panel_probe` — display + painter + the three port pipes, driven
  straight from the input room.  Pins the panel footprint floor and proves the
  port geometry and pipe-length timing.
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, W
from randomfun2026solvers.plotter_block import build_display, pipe
from randomfun2026solvers.value_ring import stamp, walls

__all__ = [
    "FLAT_RELAY",
    "PANEL_H",
    "PANEL_W",
    "WORKER",
    "build_panel_probe",
    "painter",
    "simulate_worker",
    "worker_glyph_cells",
]

PANEL_W = PANEL_H = 16

# ── the painter ───────────────────────────────────────────────────────────────
#
# Protocol on its single incoming pipe: `n, (addr, colour) x n`, then it commits
# the frame itself with `SWAP 1`.  `SWAP 1` copies next -> current *preserving*
# both buffers and the cursor (`lm1/display.py`), so a frame is a **delta**: after
# the first paint the worker only ever repaints the pixels that changed — two per
# tick (tail black, head green), one per fruit spawn, and the body on death.
#
# Interior 11x3.  The pixel loop is `counted_loop_horizontal(0, 0, "rsrs")`,
# entered heading west at (5,0):
#
#       > . . . m v . b r < <
#       ^ s r s r d . . @ . ^
#       . . . . . > . 1 s ^ .
#
# 12 cells per pixel.  The two loop sends must sit in **different columns**: all
# three port pipes leave the south wall and `s` binds by Manhattan distance to the
# pipe's source segment, so two sends in one column would bind to the same pipe.
# Walking the body westward puts `s@ADDR` at column 3 and `s@DATA` at column 1,
# and `s@SWAP` is pushed out to column 8 on the row below.
#
# The spawn sits at (8,1) heading east — into `^` at (10,1), up and back west into
# the `r b` preamble — so the man's first act is to read `n`, not to commit a black
# frame (which would fail the streaming compare on its first frame).
PAINTER_IW, PAINTER_IH = 11, 3
P_DATA, P_ADDR, P_SWAP = 1, 3, 8  # interior columns of the three sends


def painter() -> Circuit:
    c = Circuit(PAINTER_IW, PAINTER_IH)
    exit_ = c.counted_loop_horizontal(0, 0, "rsrs")
    assert exit_ == (5, 2), exit_

    # commit: `1` then s@SWAP, far east of both loop sends so it binds SWAP
    c.set(5, 2, ">")
    c.run(7, 2, "1s")
    c.set(9, 2, "^")
    c.set(9, 1, " ")

    # preamble, walked west into the loop entry at (5,0)
    c.set(9, 0, "<")
    c.run(8, 0, "rb", d=W)
    c.set(6, 0, " ")

    # spawn: east into the riser, then west over itself into `r b`
    c.set(8, 1, "@")
    c.set(10, 1, "^")
    c.set(10, 0, "<")
    return c


# ── the panel probe ───────────────────────────────────────────────────────────
#
# Geometry, and why it is what it is:
#
# * the display's **top** wall is ADDR, its **left** wall DATA, its **bottom**
#   SWAP; the right wall and every corner are load errors.  So the panel needs a
#   free row above, a free column west and a free row below.
# * a pipe leaving a room's south wall has a *forced* first cell pointing south,
#   so it can only bend on the second row below the wall — hence the two-row band
#   between the painter and the panel.
# * ADDR must not arrive after its own DATA, and SWAP must not overtake the DATA
#   writes still in flight, so ADDR is the shortest pipe and SWAP the longest.
#   With ADDR = 2 and the sends 2 ticks apart that is satisfied with slack.
PROBE_PAINTER = (2, 1)  # painter interior origin
PROBE_PANEL = (3, 7)    # panel *wall* origin


def build_panel_probe() -> tuple[list[str], dict[str, int]]:
    """Display + painter + the three port pipes, fed by the input room.

    The input pipe stands in for the worker, so the probe speaks the painter's
    exact protocol and a correct frame here means the panel harness is right.
    """
    g = Circuit(22, 26)
    px, py = PROBE_PAINTER
    dx, dy = PROBE_PANEL

    stamp(g, px, py, painter().rows())
    walls(g, px, py, PAINTER_IW, PAINTER_IH)
    build_display(g, dx, dy, panel_w=PANEL_W, panel_h=PANEL_H)

    south = py + PAINTER_IH + 1  # first free row below the painter's south wall
    l_addr = pipe(g, [(px + P_ADDR, south), (px + P_ADDR, south + 1)],
                  into=(px + P_ADDR, dy))
    l_data = pipe(g, [(px + P_DATA, south), (px + P_DATA, south + 1),
                      (dx - 1, south + 1), (dx - 1, dy + 2)], into=(dx, dy + 2))
    l_swap = pipe(g, [(px + P_SWAP, south), (px + P_SWAP, south + 1),
                      (dx + PANEL_W + 2, south + 1),
                      (dx + PANEL_W + 2, dy + PANEL_H + 2),
                      (px + P_SWAP, dy + PANEL_H + 2)],
                  into=(px + P_SWAP, dy + PANEL_H + 1))

    stamp(g, 16, 0, ["+-+", "|I|", "+-+"])
    pipe(g, [(15, 1), (14, 1)], into=(px + PAINTER_IW, 1))

    lens = {"addr": l_addr, "data": l_data, "swap": l_swap}
    if not (l_addr - 2 <= l_data and l_swap > l_data - 12):
        raise ValueError(f"pipe lengths deliver out of order: {lens}")
    return [r.rstrip() for r in g.rows()], lens


# ── the flat relay, and the ring probe that measures it ───────────────────────
#
# `value_ring.RELAY_NORTH` turns one word round per 6-cell walking cycle, which
# caps any ring built on it at 6.0 ticks/word no matter how fast the worker is.
# The cycle length is not the constraint though — the number of `r`/`s` *pairs*
# inside one cycle is.  A flat two-row relay walks east along the top row and west
# along the bottom, and every cell that is not a turn can be half of a pair:
#
#       @rsrsrsrsv
#       ^srsrsrs<
#
# 20 cells, 8 pairs, so 2.5 ticks/word — the spawn is a nop at the cycle's start
# so the man's first act is `r`, not an `s` that would inject a spurious 0 into
# the ring.  One incoming pipe and one outgoing, so no `r`/`s` here needs a
# binding argument and both ports may sit on whichever wall the routing wants.
FLAT_RELAY = [
    "+----------+",
    "|@rsrsrsrsv|",
    "|^srsrsrs<.|",
    "+----------+",
]
RELAY_IW, RELAY_IH = 10, 2


# ── the worker's program, as a control-flow graph of straight glyph runs ──────
#
# Ring order: ``[d, x, y16, f, b_1..b_L, END]`` — d in {1,-1,16,-16}, x in 0..15,
# y16 = 16*y, f a display address or 256 for "no fruit", b_i display addresses,
# END = -1.  The body is *variable length*: no pad slots and no length counter,
# because END is the **only** negative value the ring ever holds, so the body
# loops find their end with a bare `X`.
#
# The head is a **pair** (x, y16) rather than an address.  That is what makes the
# off-grid test one mask per axis with no per-direction constants: a horizontal
# move dies iff ``(x+d) & 16``, a vertical one iff ``(x + y16 + d) & 256``, and
# ``n = nx + y16`` is a single `+`.  Direction is one slot, and ``b`` then ``x``
# splits horizontal from vertical in two glyphs off its low bit.
#
# **One lap per round.**  The obstacle was that a move must paint the vacated tail
# black, but the tail is the first body value popped and a self-collision is only
# discovered at the end of the scan.  The painter's protocol is (addr, colour)
# pairs, so the worker sends the tail's *address* immediately and withholds its
# *colour* until the scan finishes — 0 if the move was legal, 9 if it was not.
# The painter simply blocks on its second `r`.  Nothing has to be undone and no
# second lap is needed.
#
# The scan always skips the tail.  That is safe even when growing: the new head
# is the fruit's cell and the problem guarantees fruit only appears on an empty
# cell, so no body value can equal it.
#
# ``16*fy + fx`` needs three live values and a man has two hands.  The fruit round
# instead parks fx in the ring **past the END sentinel** and pops it back one lap
# later, when 16*fy is sitting in B — the ring as a one-slot scratch.
#
# Tokens: `ri` read input, `rr` read ring, `sr` send ring, `sp` send painter,
# `Lnnn` a literal, and the plain glyphs.  A block ending in `X` names its three
# lanes (`zero` straight, `pos` clockwise, `neg` counter-clockwise); `x` names
# `one`/`zero` off BP's low bit; `d` names `pos`/`zero`.
WORKER: dict[str, tuple[list[str], dict[str, str] | str]] = {
    # read sx sy, build the ring, paint the head, commit
    "INIT": ([
        "ri", "M", "L1", "sr", "W", "sr", "M",        # d=1, x=sx  (B=sx)
        "ri", "M", "L4", "W", "{", "sr", "M",         # y16=16*sy  (B=y16)
        "L256", "sr",                                 # f=256, no fruit
        "rr", "sr",                                   # rotate d
        "rr", "sr", "+", "M",                         # rotate x; B = sx+y16 = h
        "rr", "sr", "rr", "sr",                       # rotate y16, f
        "W", "M", "sr", "L1", "N", "sr",              # push b_1 = h, push END
        "W", "sp", "L10", "sp", "L1", "N", "sp",      # paint (h,10), commit
    ], "MAIN"),

    # one input value dispatches the round: A = v-1, so a single `X` splits
    # tick (-1) from fruit (0) from a direction change (>0).
    "MAIN": (["ri", "M", "L1", "W", "-", "X"],
             {"neg": "TICK", "zero": "FRUIT", "pos": "DIR"}),

    # v-1 in 1..4; low bit picks the axis, the next bit picks the sign
    "DIR": (["b", "x"], {"one": "DIR_V", "zero": "DIR_H"}),
    "DIR_H": (["L1", "]", "x"], {"one": "DIR_SET", "zero": "DIR_NEG"}),
    "DIR_V": (["L16", "]", "x"], {"one": "DIR_SET", "zero": "DIR_NEG"}),
    "DIR_NEG": (["N"], "DIR_SET"),
    "DIR_SET": (["M", "rr", "W", "sr",
                 "rr", "sr", "rr", "sr", "rr", "sr"], "ROT_BODY"),
    "ROT_BODY": (["rr", "X"], {"neg": "ROT_END", "zero": "ROT_PUSH", "pos": "ROT_PUSH"}),
    "ROT_PUSH": (["sr"], "ROT_BODY"),
    "ROT_END": (["sr"], "MAIN"),

    # `1 fx fy`: two laps, because the f slot precedes the body
    "FRUIT": ([
        "ri", "sr",                                   # park fx past END
        "ri", "M", "L4", "W", "{", "M",               # B = 16*fy
        "rr", "sr", "rr", "sr", "rr", "sr", "rr", "sr",
    ], "FR_BODY"),
    "FR_BODY": (["rr", "X"], {"neg": "FR_END", "zero": "FR_PUSH", "pos": "FR_PUSH"}),
    "FR_PUSH": (["sr"], "FR_BODY"),
    "FR_END": (["sr", "rr", "+", "M",                 # fx arrives; B = f'
                "rr", "sr", "rr", "sr", "rr", "sr",
                "rr", "W", "sr", "M"], "FR2_BODY"),
    "FR2_BODY": (["rr", "X"], {"neg": "FR2_END", "zero": "FR2_PUSH", "pos": "FR2_PUSH"}),
    "FR2_PUSH": (["sr"], "FR2_BODY"),
    "FR2_END": (["sr", "W", "sp", "L9", "sp", "L1", "N", "sp"], "MAIN"),

    # one lap; B carries n, the new head's display address, throughout
    "TICK": (["rr", "sr", "b", "x"], {"one": "T_H", "zero": "T_V"}),
    "T_H": (["M", "rr", "+", "M", "sr", "L16", "&", "X"],
            {"zero": "T_H_OK", "pos": "DEAD_HV", "neg": "DEAD_HV"}),
    "T_H_OK": (["rr", "sr", "+", "M"], "T_F"),
    "T_V": (["M", "rr", "sr", "+", "M", "rr", "+", "M", "L256", "&", "X"],
            {"zero": "T_V_OK", "pos": "DEAD_V", "neg": "DEAD_V"}),
    "T_V_OK": (["L240", "&", "sr", "W", "M"], "T_F"),
    "T_F": (["rr", "-", "X"], {"zero": "T_GROW", "pos": "T_MOVE", "neg": "T_MOVE"}),

    "T_GROW": (["L256", "sr",
                "W", "M", "sp", "L10", "sp", "L1", "N", "sp"], "G_BODY"),
    "G_BODY": (["rr", "X"], {"neg": "G_END", "zero": "G_PUSH", "pos": "G_PUSH"}),
    "G_PUSH": (["sr"], "G_BODY"),
    "G_END": (["W", "sr", "W", "sr"], "MAIN"),

    "T_MOVE": (["+", "sr", "rr", "sp"], "M_BODY"),
    "M_BODY": (["rr", "X"], {"neg": "M_END", "zero": "M_CMP", "pos": "M_CMP"}),
    "M_CMP": (["-", "X"], {"zero": "DEAD_C", "pos": "M_KEEP", "neg": "M_KEEP"}),
    "M_KEEP": (["+", "sr"], "M_BODY"),
    "M_END": (["L0", "sp",
               "W", "M", "sp", "L10", "sp", "L1", "N", "sp",
               "W", "sr", "W", "sr"], "MAIN"),

    # death: the body is repainted red and the frame committed; the ring may be
    # left in any state because the case ends the moment that frame lands.
    "DEAD_HV": (["rr", "sr"], "DEAD_V"),
    "DEAD_V": (["rr"], "DEAD_PAINT"),
    "DEAD_PAINT": (["rr", "X"], {"neg": "DEAD_DONE", "zero": "DEAD_PIX", "pos": "DEAD_PIX"}),
    "DEAD_PIX": (["sp", "L9", "sp"], "DEAD_PAINT"),
    "DEAD_DONE": (["L1", "N", "sp"], "HALT"),

    # a self-collision owes the tail its colour, and the survivors already pushed
    # sit *behind* END, so a fresh marker goes in and the ring is walked twice.
    "DEAD_C": (["L9", "sp", "W", "sp", "L9", "sp"], "DC_A"),
    "DC_A": (["rr", "X"], {"neg": "DC_MARK", "zero": "DC_A_PIX", "pos": "DC_A_PIX"}),
    "DC_A_PIX": (["sp", "L9", "sp"], "DC_A"),
    "DC_MARK": (["sr", "rr", "rr", "rr", "rr"], "DC_B"),
    "DC_B": (["rr", "X"], {"neg": "DEAD_DONE", "zero": "DC_B_PIX", "pos": "DC_B_PIX"}),
    "DC_B_PIX": (["sp", "L9", "sp"], "DC_B"),
}


def worker_glyph_cells() -> int:
    """How many grid cells the program's glyphs occupy (literals expanded)."""
    total = 0
    for toks, _ in WORKER.values():
        for t in toks:
            total += 1 if not t.startswith("L") or int(t[1:]) <= 9 else len(t[1:]) + 2
    return total


def simulate_worker(rounds: list[dict]) -> list[list[str]]:
    """Run :data:`WORKER` over one test case and return the frames it commits.

    An op-level model: `A`, `B`, `BP`, the ring as a deque, the input as a queue
    and the LM-75 as a next-buffer.  Running dry on either pipe is how a round
    ends in the real machine, so it ends the simulation rather than failing.
    """
    inp: deque[int] = deque(int(v) for r in rounds for v in r["in"])
    ring: deque[int] = deque()
    a = b = bp = 0
    frames: list[list[str]] = []
    nxt = [[0] * PANEL_W for _ in range(PANEL_H)]
    pend: list[int] = []

    def paint(v: int) -> None:
        if v < 0:
            frames.append(["".join(f"{p:x}" for p in row) for row in nxt])
            pend.clear()
            return
        pend.append(v)
        if len(pend) == 2:
            addr, colour = pend
            nxt[addr // PANEL_W][addr % PANEL_W] = colour
            pend.clear()

    block = "INIT"
    while block != "HALT":
        toks, succ = WORKER[block]
        branch = None
        for t in toks:
            if t.startswith("L"):
                a = int(t[1:])
            elif t == "ri":
                if not inp:
                    return frames
                a = inp.popleft()
            elif t == "rr":
                if not ring:
                    return frames
                a = ring.popleft()
            elif t == "sr":
                ring.append(a)
            elif t == "sp":
                paint(a)
            elif t == "M":
                b = a
            elif t == "W":
                a, b = b, a
            elif t == "N":
                a = -a
            elif t == "+":
                a += b
            elif t == "-":
                a -= b
            elif t == "&":
                a &= b
            elif t == "{":
                a <<= b
            elif t == "b":
                bp = a
            elif t == "]":
                bp >>= 1
            elif t == "X":
                branch = "zero" if a == 0 else ("pos" if a > 0 else "neg")
            elif t == "x":
                branch = "one" if bp & 1 else "zero"
            elif t == "d":
                branch = "pos" if bp > 0 else "zero"
            else:  # pragma: no cover - the token table is closed
                raise AssertionError(t)
        block = succ if isinstance(succ, str) else succ[branch]
    return frames


def _cli() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", nargs="?", default="panel-probe",
                    choices=["panel-probe", "painter"])
    ap.add_argument("--out", type=Path, help="write the grid here")
    args = ap.parse_args()
    if args.what == "painter":
        print(painter().ruler())
        return
    rows, lens = build_panel_probe()
    print(f"# ADDR {lens['addr']} / DATA {lens['data']} / SWAP {lens['swap']} cells",
          file=sys.stderr)
    text = "\n".join(rows) + "\n"
    if args.out:
        args.out.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    _cli()
