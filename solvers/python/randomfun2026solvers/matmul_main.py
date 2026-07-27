#!/usr/bin/env python3
"""MAIN for the bespoke matmul: control and the multiply loop in one room.

Merging the controller into the worker is what makes this small. The worker's MAC
loop needs the scalar parked in B for its whole run, and the loop count can still
come from a register ring, because `r`, `s` and `b` all leave B alone:

    r(a_ret) M          scalar into B, where it survives the inner loop
    b ]x7 d             is it ring A's 128 end marker?  (touches A and BP only)
    r(rK) s(rK) b       BP = K, K put straight back, B untouched
    K x { r(b_ret) s(b_fwd) * s(prod) }

So there is no scalar pipe, no K gauge and no separate worker room. The ADDER
accumulates and emits, so MAIN never touches the output either.

And because *every* incoming pipe is on the west wall and every outgoing on the
east, there is no wall competing with itself: an `r` and an `s` each bind purely
by row, anywhere in the room. That removes the west-half/east-half constraint that
was forcing the program to be reordered.

    west   in@2  b_ret@4  a_ret@8  rn@9  rm@11  rk@13
    east   a_fwd@3  b_fwd@5  prod@7  rn@10  rm@12  rk@14  cmd@15

Row order is the program, because `counted_loop` walks a body down a column one
glyph per row:

    fill A   `rs`     rows 2,3      r(in)   s(a_fwd)
    fill B   `r  s`   rows 2..5     r(in)          s(b_fwd)
    the MAC  `rs*s`   rows 4..7     r(b_ret) s(b_fwd) * s(prod)
    counts   `sWsWs` horizontal on row 15

The MAC's order is forced — `b` has to go back into ring B before `*` overwrites
it — which is what pins b_ret below b_fwd below prod with a spare row for the `*`.
"""

from __future__ import annotations

from .circuit import Circuit, N, S
from .matmul_y import Serpentine

__all__ = ["EAST", "WEST", "main_room"]

IN, A_FWD, B_RET, B_FWD, SPARE, PROD, A_RET = 2, 3, 4, 5, 6, 7, 8
RN_RET, RN_FWD, RM_RET, RM_FWD, RK_RET, RK_FWD, CMD = 9, 10, 11, 12, 13, 14, 15
BAND_T, BAND_B = 1, 16
IW, IH = 92, 17
SENTINEL_BUILD = "2M******"          # A = 128 with no backtick literal

WEST = {"in": IN, "b_ret": B_RET, "a_ret": A_RET,
        "rn_ret": RN_RET, "rm_ret": RM_RET, "rk_ret": RK_RET}
EAST = {"a_fwd": A_FWD, "b_fwd": B_FWD, "prod": PROD, "cmd": CMD,
        "rn_fwd": RN_FWD, "rm_fwd": RM_FWD, "rk_fwd": RK_FWD}


def main_room() -> tuple[Circuit, int]:
    c = Circuit(IW + 2, IH + 2)
    c.set(1, BAND_T, "@")
    w = Serpentine(c, 2, BAND_T, BAND_T, BAND_B, S)

    # ── read N, M, K into their rings ────────────────────────────────────────
    w.op("r", IN); w.op("s", RN_FWD)
    w.op("r", IN); w.op("s", RM_FWD)
    w.op("r", IN); w.op("s", RK_FWD)

    # ── the ADDER's count words: BP = N, then A = K and B = K1 = (M-1)*K ─────
    w.op("r", RN_RET); w.op("b"); w.op("s", RN_FWD)
    w.ops("1M")
    w.op("r", RM_RET); w.op("s", RM_FWD); w.ops("-M")
    w.op("r", RK_RET); w.op("s", RK_FWD); w.ops("*M")
    w.op("r", RK_RET); w.op("s", RK_FWD)
    x, y = w.park(CMD - 1)
    c.set(x, y, ">")
    ex, ey = c.counted_loop_horizontal(x + 1, y, "sWsWs")
    c.turn(ex, ey, N)                      # exits south; turn back up the band
    w.x, w.y, w.d = ex, ey, N

    # ── fill ring A with N*M scalars, then its end marker ────────────────────
    w.op("r", RN_RET); w.op("M")
    w.op("r", RM_RET); w.op("s", RM_FWD); w.ops("*b")
    x, y = w.park(BAND_T + 1)
    ax, ay = c.counted_loop(x, BAND_T, "rs")
    c.turn(ax, ay, S)
    w.x, w.y, w.d = ax, ay, S
    w.ops(SENTINEL_BUILD)
    w.op("s", A_FWD)

    # ── fill ring B with M*K values ──────────────────────────────────────────
    w.op("r", RM_RET); w.op("M")
    w.op("r", RK_RET); w.op("s", RK_FWD); w.ops("*b")
    x, y = w.park(BAND_T + 1)
    bx, by = c.counted_loop(x, BAND_T, "r  s")
    c.turn(bx, by, S)
    w.x, w.y, w.d = bx, by, S

    # ── the drive loop: one scalar per (i, t) until ring A's end marker ───────
    # The marker test may only touch A and BP, because B is holding the scalar the
    # MAC loop is about to multiply by. `b` copies A into BP and seven `]` shift it
    # arithmetically: 128>>7 is 1 while 99>>7 is 0 and -99>>7 is -1, so `d` turns
    # for the marker and nothing else. The shifts ride a horizontal run because
    # nine glyphs will not fit below row 8 inside the band.
    d0 = w.x + 2
    c.set(d0, BAND_T, "v")
    for y in range(BAND_T + 1, A_RET):
        c.set(d0, y, " ")
    c.set(d0, A_RET, "r")                      # the scalar
    c.set(d0, A_RET + 1, "M")                  # ... parked in B
    c.set(d0, A_RET + 2, "b")                  # BP = scalar
    for y in range(A_RET + 3, CMD):
        c.set(d0, y, " ")
    c.set(d0, CMD, ">")
    c.run(d0 + 1, CMD, "]]]]]]]", d=(1, 0))
    test = d0 + 8
    c.set(test, CMD, "d")                      # marker -> south into H
    c.set(test, BAND_B, "H")

    # not the marker: BP = K off its ring, then the MAC loop
    c.set(test + 1, CMD, "^")
    c.set(test + 1, CMD - 1, " ")
    c.set(test + 1, RK_RET, "r")
    c.set(test + 1, RK_RET - 1, ">")
    c.set(test + 2, RK_RET - 1, "v")
    c.set(test + 2, RK_RET, " ")
    c.set(test + 2, RK_FWD, "s")
    c.set(test + 2, CMD, "b")
    c.set(test + 2, BAND_B, ">")
    c.set(test + 3, BAND_B, "^")
    for y in range(B_RET, BAND_B):
        c.set(test + 3, y, " ")
    c.set(test + 3, B_RET - 1, ">")
    mx, my = c.counted_loop(test + 4, B_RET - 1, "rs*s")

    # back-edge: over the top of everything, west, and down to the fetch again
    c.set(mx, my, "^")
    c.set(mx, my - 1, "<")
    for x in range(d0 + 1, mx):
        c.set(x, BAND_T, " ")
    return c, mx + 2
