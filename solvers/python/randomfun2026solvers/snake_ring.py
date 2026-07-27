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
    "HARNESS_H",
    "HARNESS_W",
    "PANEL_H",
    "PANEL_W",
    "WORKER",
    "build_panel_probe",
    "painter",
    "stamp_harness",
    "simulate_worker",
    "worker_glyph_cells",
]

PANEL_W = PANEL_H = 16

# ── the painter ───────────────────────────────────────────────────────────────
#
# Protocol on its single incoming pipe: `(addr, colour) x n` then a **negative
# terminator**, on which the painter commits the frame itself with `SWAP 1`.
# `SWAP 1` copies next -> current *preserving* both buffers and the cursor
# (`lm1/display.py`), so a frame is a **delta**: after the first paint the worker
# only ever repaints the pixels that changed — two per tick (tail black, head
# green), one per fruit spawn, and the body on death.
#
# A terminator rather than a leading count, because the worker cannot know the
# count in advance: a move's second pixel is only owed a *colour* once the
# self-collision scan resolves, and a death repaints a body whose length is
# whatever the ring turns out to hold.  So the painter carries the test instead:
#
#        col: 0  1  2  3  4  5  6  7
#     row 0:  .  .  .  .  v  <  .  .      addr > 0 rejoins the pixel path
#     row 1:  v  s  r  s  <  X  r  <      the pixel loop, walked west
#     row 2:  >  .  .  @  .  .  .  ^      the return leg, walked east
#     row 3:  .  .  .  .  .  1  .  .
#     row 4:  .  .  .  .  .  s  .  .      s@SWAP: the commit
#     row 5:  .  .  .  .  .  >  .  ^
#
# `X` entered heading **west** turns clockwise (north) on positive and
# counter-clockwise (south) on negative, so the terminator peels off downward
# and an address peels off upward — and `v` at (4,0) drops it back onto `<` at
# (4,1), which the zero case (address 0 is a real cell) walks straight through.
# One test, one merge, 16 cells per pixel.
#
# The three sends must sit in **different columns**: all three port pipes leave
# the south wall and `s` binds by Manhattan distance, so two sends in one column
# would bind the same pipe.  Walking the pixel westward puts `s@ADDR` at column 3
# and `s@DATA` at column 1 — DATA west of ADDR, which is also what lets DATA's
# pipe hug the panel's west wall while SWAP's, at column 5, sweeps east around it
# without either crossing ADDR's two-cell drop.
#
# The spawn sits at (3,2) on the return leg, so the man's first act is the `r` at
# (6,1) rather than a commit that would black out the display's first frame.
PAINTER_IW, PAINTER_IH = 8, 6
P_DATA, P_ADDR, P_SWAP = 1, 3, 5  # interior columns of the three sends


def painter() -> Circuit:
    c = Circuit(PAINTER_IW, PAINTER_IH)
    c.set(5, 0, "<")
    c.set(4, 0, "v")
    c.run(7, 1, "<rX<srs", d=W)     # entry, read, test, merge, ADDR, read, DATA
    c.set(0, 1, "v")
    c.set(0, 2, ">")
    c.set(3, 2, "@")
    c.set(7, 2, "^")
    c.set(5, 3, "1")                # the terminator's lane, walked south
    c.set(5, 4, "s")
    c.set(5, 5, ">")
    c.set(7, 5, "^")
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
PROBE_PAINTER = (2, 1)   # painter interior origin
PROBE_PANEL = (3, 10)    # panel *wall* origin


#: The harness's bounding box, so a caller can reserve columns for it.
HARNESS_W, HARNESS_H = 22, 29


def stamp_harness(g: Circuit, ox: int = 0, oy: int = 0) -> dict[str, int]:
    """Painter + LM-75 + the three port pipes, with `(ox, oy)` as its origin.

    Everything the engine has already signed off on lives here; the caller only
    supplies the feed pipe, which may enter any non-corner cell of the painter's
    wall because the painter has exactly one incoming pipe and so binds `r`
    unambiguously wherever it arrives.
    """
    px, py = ox + PROBE_PAINTER[0], oy + PROBE_PAINTER[1]
    dx, dy = ox + PROBE_PANEL[0], oy + PROBE_PANEL[1]

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

    lens = {"addr": l_addr, "data": l_data, "swap": l_swap}
    if not (l_addr - 2 <= l_data and l_swap > l_data - 12):
        raise ValueError(f"pipe lengths deliver out of order: {lens}")
    return lens


def build_panel_probe() -> tuple[list[str], dict[str, int]]:
    """Display + painter + the three port pipes, fed by the input room.

    The input pipe stands in for the worker, so the probe speaks the painter's
    exact protocol and a correct frame here means the panel harness is right.
    """
    g = Circuit(HARNESS_W, HARNESS_H)
    lens = stamp_harness(g)
    east = PROBE_PAINTER[0] + PAINTER_IW    # the painter's east wall column
    stamp(g, east + 3, 0, ["+-+", "|I|", "+-+"])
    pipe(g, [(east + 2, 1), (east + 1, 1)], into=(east, 1))
    return [r.rstrip() for r in g.rows()], lens


# ── the flat relay, and the ring probe that measures it ───────────────────────
#
# `value_ring.RELAY_NORTH` turns one word round per 6-cell walking cycle, which
# caps any ring built on it at 6.0 ticks/word no matter how fast the worker is.
# The cycle length is not the constraint though — the number of `r`/`s` *pairs*
# inside one cycle is.  A flat two-row relay walks east along the top row and west
# along the bottom, and every cell that is not a turn can be half of a pair:
#
#       > @ r s r s r s _ v
#       ^ s r s r s r s r <
#
# 20 cells, 7 pairs, so 2.9 ticks/word.  Two cells are spent on shape rather than
# work, and both are load-bearing:
#
# * `>` at the north-west corner, not the spawn: the returning man arrives from
#   the south heading **north** and has to be turned east, and `@` is only a nop,
#   so a spawn in that corner would walk him straight out through the wall.
# * the spawn at (1,0) instead, where heading east is already correct — and where
#   the man's first act is the `r` at (2,0), not an `s` that would inject a
#   spurious 0 into the ring.
#
# The single blank before `v` is what makes the pair count odd-safe: the walking
# cycle is `r s` throughout, so every `r` is followed by its own `s`.
#
# One incoming pipe and one outgoing, so no `r`/`s` here needs a binding
# argument and both ports may sit on whichever wall the routing wants — but a
# port's first pipe cell must still point *away* from this room.
FLAT_RELAY = [
    "+----------+",
    "|>@rsrsrs v|",
    "|^srsrsrsr<|",
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
    # One block, not three.  `s` sends A and *leaves* it, and both arms of the
    # old test pushed the word back, so `rr sr X` is the whole lap: the sentinel
    # goes home on the way out exactly as `ROT_END` used to send it.  A lap is a
    # block visit a word instead of two, and a block visit is ~33 ticks of
    # corridor against the 2 ticks the glyphs cost.
    "ROT_BODY": (["rr", "sr", "X"],
                 {"neg": "MAIN", "zero": "ROT_BODY", "pos": "ROT_BODY"}),

    # `1 fx fy`: two laps, because the f slot precedes the body
    "FRUIT": ([
        "ri", "sr",                                   # park fx past END
        "ri", "M", "L4", "W", "{", "M",               # B = 16*fy
        "rr", "sr", "rr", "sr", "rr", "sr", "rr", "sr",
    ], "FR_BODY"),
    "FR_BODY": (["rr", "sr", "X"],
                {"neg": "FR_END", "zero": "FR_BODY", "pos": "FR_BODY"}),
    "FR_END": (["rr", "+", "M",                       # fx arrives; B = f'
                "rr", "sr", "rr", "sr", "rr", "sr",
                "rr", "W", "sr", "M"], "FR2_BODY"),
    "FR2_BODY": (["rr", "sr", "X"],
                 {"neg": "FR2_END", "zero": "FR2_BODY", "pos": "FR2_BODY"}),
    "FR2_END": (["W", "sp", "L9", "sp", "L1", "N", "sp"], "MAIN"),

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
    # `G_END` pushes a word in *front* of the sentinel, so the send cannot move
    # ahead of the branch the way `ROT_BODY`'s did.  Rotated instead: the entry
    # test keeps a block of its own and the loop carries the previous word's send.
    "G_BODY": (["rr", "X"], {"neg": "G_END", "zero": "G_LOOP", "pos": "G_LOOP"}),
    "G_LOOP": (["sr", "rr", "X"],
               {"neg": "G_END", "zero": "G_LOOP", "pos": "G_LOOP"}),
    "G_END": (["W", "sr", "W", "sr"], "MAIN"),

    "T_MOVE": (["+", "sr", "rr", "sp"], "M_BODY"),
    "M_BODY": (["rr", "X"], {"neg": "M_END", "zero": "M_CMP", "pos": "M_CMP"}),
    "M_CMP": (["-", "X"], {"zero": "DEAD_C", "pos": "M_LOOP", "neg": "M_LOOP"}),
    # `M_KEEP` and `M_BODY` fused.  The move lap is **32% of every block visit
    # the machine makes**, and it was costing three a body segment; this makes it
    # two.  The entry test stays separate because `T_MOVE` arrives mid-lap.
    "M_LOOP": (["+", "sr", "rr", "X"],
               {"neg": "M_END", "zero": "M_CMP", "pos": "M_CMP"}),
    "M_END": (["L0", "sp",
               "W", "M", "sp", "L10", "sp", "L1", "N", "sp",
               "W", "sr", "W", "sr"], "MAIN"),

    # death: the body is repainted red and the frame committed; the ring may be
    # left in any state because the case ends the moment that frame lands.
    "DEAD_HV": (["rr", "sr"], "DEAD_V"),
    "DEAD_V": (["rr"], "DEAD_PAINT"),
    "DEAD_PAINT": (["rr", "X"], {"neg": "DEAD_DONE", "zero": "DEAD_PIX", "pos": "DEAD_PIX"}),
    # Each paint lap retests for itself; the guard block is entered once, not
    # once a pixel.  Guarded rather than rotated because a one-segment snake may
    # have no pixel to paint at all.
    "DEAD_PIX": (["sp", "L9", "sp", "rr", "X"],
                 {"neg": "DEAD_DONE", "zero": "DEAD_PIX", "pos": "DEAD_PIX"}),
    "DEAD_DONE": (["L1", "N", "sp"], "HALT"),

    # a self-collision owes the tail its colour, and the survivors already pushed
    # sit *behind* END, so a fresh marker goes in and the ring is walked twice.
    "DEAD_C": (["L9", "sp", "W", "sp", "L9", "sp"], "DC_A"),
    "DC_A": (["rr", "X"], {"neg": "DC_MARK", "zero": "DC_A_PIX", "pos": "DC_A_PIX"}),
    "DC_A_PIX": (["sp", "L9", "sp", "rr", "X"],
                 {"neg": "DC_MARK", "zero": "DC_A_PIX", "pos": "DC_A_PIX"}),
    "DC_MARK": (["sr", "rr", "rr", "rr", "rr"], "DC_B"),
    "DC_B": (["rr", "X"], {"neg": "DEAD_DONE", "zero": "DC_B_PIX", "pos": "DC_B_PIX"}),
    "DC_B_PIX": (["sp", "L9", "sp", "rr", "X"],
                 {"neg": "DEAD_DONE", "zero": "DC_B_PIX", "pos": "DC_B_PIX"}),
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
