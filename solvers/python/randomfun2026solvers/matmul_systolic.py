#!/usr/bin/env python3
"""GPU-like systolic matmul for littleman.

Output-stationary systolic array of MAC processing-elements (PEs). `a` flows
west->east across rows, `b` flows north->south down columns. PE(i,j) sees
a=A[i][t], b=B[t][j] at contraction step t, does acc += a*b, and forwards a east
and b south. After M steps PE(i,j) holds C[i][j].

Key facts this design leans on (verified on the engine, see ARCH/memory):
  * The 3-register wall: a MAC needs 3 live values (a, b, acc) but only A/B are
    usable (BP is write-only / branch-only). We split each PE into two rooms so
    no room ever holds 3 live values, with ZERO per-PE spill cells:
      - COMPUTE (stateless per cycle): r a, s a-east, M, r b, s b-south, *, s prod
      - ACCHOLD (holds sum in B): counted_loop body `r+M`, then `W s` to drain
  * Pipes are FIFO queues, not clocked broadcast wires, so correctness needs only
    the right *order* of a's and b's per PE, never the right *tick* -- the classic
    systolic skew is a non-issue. Forwarding preserves order.

Build status: forwarding COMPUTE cell under validation (this file's __main__
`--probe-cell`). Array assembly to follow.
"""
from __future__ import annotations

import sys

from randomfun2026solvers.circuit import GLYPH, Circuit, Collision, E, W, N, S


# ── COMPUTE cell ──────────────────────────────────────────────────────────────
# A systolic PE's stateless multiply front-end. Ports (local interior coords) are
# returned so the assembler can attach pipes at the right walls. `fwd_a`/`fwd_b`
# drop the east / south forward when the PE sits on the last column / row.
#
# Register sequence per lap (B is free scratch here; acc lives in ACCHOLD):
#   r(a,W) -> A=a ; s(a,E) forward ; M -> B=a ; r(b,N) -> A=b ; s(b,S) forward ;
#   * -> A=a*b ; s(prod) -> product to ACCHOLD.  Loop.

CELL_IW, CELL_IH = 9, 9


def compute_cell(fwd_a: bool = True, fwd_b: bool = True) -> tuple[Circuit, dict]:
    c = Circuit(CELL_IW, CELL_IH)
    # Ports = interior edge cells; the pipe attaches on the wall just outside.
    p = {
        "a_in": (0, 4),             # west wall, row 4
        "b_in": (2, 0),             # north wall, col 2
        "a_out": (CELL_IW - 1, 4),  # east wall, row 4
        "b_out": (2, CELL_IH - 1),  # south wall, col 2
        "prod": (6, CELL_IH - 1),   # south wall, col 6
    }
    S_ = c.set
    # ── the man loop, explicit glyphs (blanks between are nops, walkable) ──
    # Rejoin pattern `>@`: the return column comes up into `>` (turn E), `@` is a
    # walkable nop, then the first real op -- so spawn and re-entry both hit r(a)
    # heading E.
    # a-row: `>` `@` r(a) M(B=a) ... s(a) east forward, `^`.
    S_(0, 4, ">"); S_(1, 4, "@"); S_(2, 4, "r"); S_(3, 4, "M"); S_(8, 4, "^")
    S_(7, 4, "s" if fwd_a else " ")     # forward a east only if there is an east neighbour
    # up the east col, turn W on row1, transit W to r(b), continue W, turn S.
    S_(8, 1, "<"); S_(2, 1, "r"); S_(1, 1, "v")
    # down col1, turn E on the bottom row, s(b) south, *, s(prod).
    S_(1, 7, ">"); S_(4, 7, "*"); S_(6, 7, "s")
    S_(2, 7, "s" if fwd_b else " ")     # forward b south only if there is a south neighbour
    # return: continue E, drop to row8, run W, up the west col, into the rejoin.
    S_(8, 7, "v"); S_(8, 8, "<"); S_(0, 8, "^")
    if not fwd_a:
        del p["a_out"]
    if not fwd_b:
        del p["b_out"]
    return c, p


def _box(c: Circuit) -> list[str]:
    """Wrap a Circuit interior in a +--+ / |..| / +--+ room border."""
    body = c.rows()
    w = c.w
    top = "+" + "-" * w + "+"
    return [top] + ["|" + r + "|" for r in body] + [top]


def lit(n: int) -> str:
    return str(n) if 0 <= n < 10 else f"`{n}`"


def acchold_cell(m: int) -> tuple[Circuit, dict]:
    """Sum m incoming products (nearest-in pipe) into B, then drain result to the
    nearest-out pipe. Ports: in=prod (local (0,0) west-ish), out=result."""
    c = Circuit(11, 5)
    # setup BP=m, counted loop body r+M, then W s H
    x0, _ = c.run(0, 0, "@" + lit(m) + "b")   # x0 = column just after `@ <m> b`
    ex, _ = c.counted_loop(x0, 0, "r+M")
    c.run(ex, 0, "WsH")
    p = {"prod_in": (0, 0), "res_out": (8, 0)}
    return c, p


def _room(rows: list[str]) -> tuple[int, int]:
    return len(rows[0]), len(rows)


def acchold_loop(m: int) -> Circuit:
    """Loops forever: reset acc, sum m products (nearest-in), emit (nearest-out),
    repeat. One emit per output row. B holds the accumulator; it is reset to 0
    each lap so consecutive rows don't bleed."""
    c = Circuit(22, 11)
    # rejoin `>@`, then per-lap setup: BP=m (b), acc=0 (0 M)
    c.set(0, 3, ">"); c.set(1, 3, "@")
    x, _ = c.run(2, 3, lit(m) + "b0M")        # A=m,BP=m ; A=0 ; B=0
    ex, _ = c.counted_loop(x, 3, "r+M")        # sum m products into B (occupies rows 3..7)
    x2, _ = c.run(ex, 3, "Ws")                 # A<->B, emit acc
    # return along a clear low row (below the counted loop) back to the rejoin
    c.route((x2, 3), E, [(x2, 9), (0, 9), (0, 3)], (0, 3), E)
    return c


def source_container(cid: str, vals: list[int], wall: str) -> Container:
    """A room that emits `vals` in order into one out-pipe on `wall`, then halts."""
    from randomfun2026solvers.layout import Container

    body = "@" + "".join(lit(v) + "s" for v in vals) + "H"
    rows = _box(Circuit_of(body))
    w, h = _room(rows)
    return Container(id=cid, width=w, height=h, content=rows,
                     outputs=[_wall_cell(w, h, wall)])


def drain_container(cid: str, wall: str) -> Container:
    from randomfun2026solvers.layout import Container

    rows = ["+----+", "|>@rv|", "|^..<|", "+----+"]   # loops forever, discards
    w, h = _room(rows)
    return Container(id=cid, width=w, height=h, content=rows,
                     inputs=[_wall_cell(w, h, wall)])


def Circuit_of(row: str) -> Circuit:
    c = Circuit(len(row), 1)
    for x, ch in enumerate(row):
        c.set(x, 0, ch)
    return c


def _wall_cell(w: int, h: int, wall: str) -> tuple[int, int]:
    return {"W": (0, h // 2), "E": (w - 1, h // 2),
            "N": (w // 2, 0), "S": (w // 2, h - 1)}[wall]


def compute_container(cid: str, fwd_a=True, fwd_b=True) -> Container:
    from randomfun2026solvers.layout import Container

    cell, cp = compute_cell(fwd_a, fwd_b)
    rows = _box(cell)
    w, h = _room(rows)
    # local ports -> boxed border coords (interior (lx,ly) -> (lx+1,ly+1))
    def edge(name):
        lx, ly = cp[name]
        return (lx + 1, ly + 1)
    return Container(
        id=cid, width=w, height=h, content=rows,
        inputs=[edge("a_in"), edge("b_in")],           # 0=a_in, 1=b_in
        outputs=[edge("a_out"), edge("b_out"), edge("prod")],  # 0,1,2
    )


def acchold_container(cid: str, m: int, in_wall="W") -> Container:
    from randomfun2026solvers.layout import Container

    c, _ = acchold_cell(m)
    rows = _box(c)
    w, h = _room(rows)
    return Container(id=cid, width=w, height=h, content=rows,
                     inputs=[_wall_cell(w, h, in_wall)],
                     outputs=[_wall_cell(w, h, "E")])


def _blit(g: Circuit, rows: list[str], x0: int, y0: int) -> None:
    for dy, r in enumerate(rows):
        for dx, ch in enumerate(r):
            if ch != " ":
                g.set(x0 + dx, y0 + dy, ch)


def probe_cell() -> str:
    """Standalone harness, hand-placed with straight 2-cell pipes. Feed a=[1,2]
    (west) & b=[5,7] (north); drain a_out (east) & b_out (south); ACCHOLD sums the
    products (south) -> O. Expect 1*5 + 2*7 = 19."""
    from randomfun2026solvers.memory_tape import _draw_pipe

    g = Circuit(120, 80)
    CX, CY = 24, 12
    cell, _cp = compute_cell(True, True)
    _blit(g, _box(cell), CX, CY)
    # boxed port wall-cells (interior (lx,ly) -> boxed (lx+1,ly+1)):
    a_in  = (CX + 0,  CY + 5)
    b_in  = (CX + 3,  CY + 0)
    a_out = (CX + 10, CY + 5)
    b_out = (CX + 3,  CY + 10)
    prod  = (CX + 7,  CY + 10)

    # SA: emits 1,2 east into a_in. Box mid-row aligned to a_in row.
    sa = _box(Circuit_of("@1s2sH"))                 # 3 rows x 8 cols
    _blit(g, sa, CX - 12, CY + 5 - 1)               # east wall at col CX-5
    _draw_pipe(g, [(CX - 4, CY + 5), (CX - 1, CY + 5)])   # >>-> into a_in wall

    # SB: emits 5,7 south into b_in. Box spanning col b_in.
    sb = _box(Circuit_of("@5s7sH"))
    _blit(g, sb, CX + 3 - 1, CY - 6)                # south wall at row CY-4
    _draw_pipe(g, [(CX + 3, CY - 3), (CX + 3, CY - 1)])

    # DA drain: east of a_out.
    _blit(g, ["+----+", "|>@rv|", "|^..<|", "+----+"], CX + 16, CY + 5 - 2)
    _draw_pipe(g, [(CX + 11, CY + 5), (CX + 15, CY + 5)])

    # DB drain: south of b_out.
    _blit(g, ["+----+", "|>@rv|", "|^..<|", "+----+"], CX + 3 - 2, CY + 16)
    _draw_pipe(g, [(CX + 3, CY + 11), (CX + 3, CY + 15)])

    # AC acchold: south of prod, catches on its north wall; result east -> O.
    ac, _ap = acchold_cell(2)
    acbox = _box(ac)                                 # 7 rows x 13 cols
    ACX, ACY = CX + 7 - 6, CY + 20                   # north wall col at CX+7
    _blit(g, acbox, ACX, ACY)
    _draw_pipe(g, [(CX + 7, CY + 11), (CX + 7, ACY - 1)])
    # O east of AC (AC result on its east wall, mid row)
    ac_e = ACX + len(acbox[0]) - 1
    ac_mid = ACY + len(acbox) // 2
    _blit(g, ["+-+", "|O|", "+-+"], ac_e + 3, ac_mid - 1)
    _draw_pipe(g, [(ac_e + 1, ac_mid), (ac_e + 2, ac_mid)])

    rows = g.rows()
    while rows and not rows[-1].strip():
        rows.pop()
    text = "\n".join(r.rstrip() for r in rows)
    print(text)
    return text


def minimal_ring_countdown() -> tuple[Circuit, dict]:
    """Isolate the novel mechanism: a countdown ring + X-branch loop.
    Prime ring TOTAL=6, B=M=2; each lap: r(ring)->TOTAL; X(>0 continue, ==0 halt);
    continue: - (TOTAL-=M, B preserved), s(ring), emit 7. Expect 7 7 7.
    Ports: out(east), ring_out(south), ring_in(south)."""
    IW, IH = 32, 20
    c = Circuit(IW, IH)
    # ring on SOUTH: ring_out col4, ring_in col24 (r binds it anywhere). out EAST
    # row9. s near south-col4 -> ring_out ; s near east -> out.
    p = {"ring_out": (4, IH - 1), "ring_in": (24, IH - 1), "out": (IW - 1, 9)}
    S = c.set

    # Fully hand-traced single path (only glyphs/turns set; blanks are walkable nops).
    # ---- INIT: prime ring=6, B=2 ----
    S(1, 1, "@"); S(2, 1, "6"); S(3, 1, "v")            # A=6, turn S (down col3)
    S(3, 16, ">")                                       # row16: turn E
    S(4, 16, "s")                                       # s -> ring_out (send 6)
    S(5, 16, "2"); S(6, 16, "M")                        # A=2, B=2
    S(7, 16, "^")                                       # turn N (up col7)
    S(7, 9, ">")                                        # row9: turn E toward rejoin
    # ---- LOOP header (rejoin at (9,9)) ----
    S(9, 9, ">")                                        # rejoin (turn E)
    S(10, 9, "r")                                       # r(ring) -> TOTAL
    S(11, 9, "X")                                       # branch on sign(TOTAL)
    S(12, 9, "H")                                       # ==0 -> straight E -> halt
    # >0 -> CW (south): continue
    S(11, 10, "-")                                      # A = TOTAL - M (B preserved)
    S(11, 17, "<")                                      # row17: turn W
    S(4, 17, "s")                                       # s -> ring_out (write TOTAL-M)
    S(3, 17, "7")                                       # A=7
    S(2, 17, "v"); S(2, 18, ">")                        # drop to row18, turn E
    S(29, 18, "^")                                      # east: turn N
    S(29, 10, ">"); S(30, 10, "^")                      # step to col30, turn N
    S(30, 9, "s")                                       # s -> out (emit 7)  approached heading N
    S(30, 8, "^")                                       # continue N (return)
    S(30, 5, "<")                                       # row5: turn W
    S(9, 5, "v")                                        # col9: turn S back into the rejoin
    return c, p


def input_countdown() -> tuple[Circuit, dict]:
    """Like minimal_ring_countdown but TOTAL=N*M read from input (N,M,K), and
    B=M from input. Emit 7 once per row. Input `N M K` -> 7 repeated N times.
    Adds input-in on the NORTH (r(input) near north; r(ring) near south)."""
    IW, IH = 36, 22
    c = Circuit(IW, IH)
    p = {"in": (18, 0), "ring_out": (4, IH - 1), "ring_in": (26, IH - 1),
         "out": (IW - 1, 10)}
    S = c.set

    # ---- INIT: read N (send to ring), M (->B), K (discard); TOTAL=N*M ----
    S(1, 1, "@")
    # go east to the input column (18), read N
    S(18, 1, "r")                                      # r(N) (north input)
    # N -> ring_out (south col4): drop to row18, west to s
    S(19, 1, "v"); S(19, 18, "<"); S(4, 18, "s")       # s(N) -> ring_out
    # back up to input col18, read M then K
    S(3, 18, "^"); S(3, 3, ">"); S(18, 3, "r"); S(19, 3, "M")   # r(M); B=M
    S(20, 3, "v"); S(20, 5, "<"); S(18, 5, "r")        # r(K) discard  (r@(18,5))
    # read N back from ring (south col26), *, send TOTAL to ring_out (col4)
    S(17, 5, "v"); S(17, 19, ">"); S(26, 19, "r"); S(27, 19, "*")  # r(ringN); A=N*M (B=M)
    S(28, 19, "v"); S(28, 20, "<"); S(4, 20, "s")      # s(TOTAL) -> ring_out
    # flow to the loop rejoin `>` at (9,10)
    S(3, 20, "^"); S(3, 10, ">")

    # ---- LOOP (rejoin at (9,10)) ----
    S(9, 10, ">"); S(10, 10, "r"); S(11, 10, "X"); S(12, 10, "H")
    S(11, 11, "-")                                     # A=TOTAL-M (B preserved)
    S(11, 17, "<"); S(4, 17, "s")                      # s(TOTAL-M) -> ring_out
    S(3, 17, "7"); S(2, 17, "v"); S(2, 18, ">")         # A=7, drop, turn E
    S(31, 18, "^"); S(31, 11, ">"); S(32, 11, "^")      # east, step, turn N
    S(32, 10, "s"); S(32, 9, "^")                       # s(7) -> out; continue N
    S(32, 6, "<"); S(9, 6, "v")                         # row6 W, col9 S back to rejoin
    return c, p


def probe_input_countdown() -> str:
    return _wire_countdown(input_countdown())


def _wire_countdown(built) -> str:
    loader, lp = built
    lb = _box(loader)
    g = Circuit(140, 80)
    CX, CY = 20, 6
    _blit(g, lb, CX, CY)
    bh, bw = len(lb), len(lb[0])
    south_y = CY + bh - 1
    ring_out_x = CX + 1 + lp["ring_out"][0]
    ring_in_x = CX + 1 + lp["ring_in"][0]
    out_y = CY + 1 + lp["out"][1]
    east_x = CX + bw - 1
    # TURN below (relay r->s)
    TX, TY = CX + 2, south_y + 5
    _blit(g, ["+-----+", "|>@rsv|", "|^...<|", "+-----+"], TX, TY)
    turn_in_x, turn_out_x = TX + 3, TX + 4
    _pipe(g, [(ring_out_x, south_y + 1), (ring_out_x, TY - 2),
              (turn_in_x, TY - 2), (turn_in_x, TY - 1)])
    _pipe(g, [(turn_out_x, TY - 1), (turn_out_x, TY - 3),
              (ring_in_x, TY - 3), (ring_in_x, south_y + 1)])
    # I room north (over the in port)
    if "in" in lp:
        in_x = CX + 1 + lp["in"][0]
        _blit(g, ["+-+", "|I|", "+-+"], in_x - 1, CY - 5)   # bottom border at CY-3
        _pipe(g, [(in_x, CY - 2), (in_x, CY - 1)])          # 2-cell pipe into loader north
    # O east
    ox = east_x + 4
    _blit(g, ["+-+", "|O|", "+-+"], ox, out_y - 1)
    _pipe(g, [(east_x + 1, out_y), (ox - 1, out_y)])
    rows = g.rows()
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(r.rstrip() for r in rows)


def probe_countdown(wire: bool = True) -> str:
    """Wire minimal_ring_countdown to a south turnaround (ring) + east O.
    No input. Expect 7 7 7."""
    loader, lp = minimal_ring_countdown()
    lb = _box(loader)
    if not wire:
        return "\n".join(lb)
    g = Circuit(120, 70)
    CX, CY = 20, 4
    _blit(g, lb, CX, CY)
    bh, bw = len(lb), len(lb[0])
    south_y = CY + bh - 1                              # box bottom border row
    ring_out_x = CX + 1 + lp["ring_out"][0]           # boxed col of s(ring_out)
    ring_in_x = CX + 1 + lp["ring_in"][0]
    out_y = CY + 1 + lp["out"][1]
    east_x = CX + bw - 1

    # TURN room below-left; relay: r(north in) -> s(north out).
    TX, TY = CX + 2, south_y + 5
    _blit(g, ["+-----+", "|>@rsv|", "|^...<|", "+-----+"], TX, TY)
    # TURN north wall: in-pipe over the r at (TX+3), out-pipe over the s at (TX+4)
    turn_in_x, turn_out_x = TX + 3, TX + 4
    # ring_out (loader south) -> down, across, up into TURN in
    _pipe(g, [(ring_out_x, south_y + 1), (ring_out_x, TY - 2),
              (turn_in_x, TY - 2), (turn_in_x, TY - 1)])
    # TURN out -> ring_in (loader south, col24)
    _pipe(g, [(turn_out_x, TY - 1), (turn_out_x, TY - 3),
              (ring_in_x, TY - 3), (ring_in_x, south_y + 1)])
    # out -> O (east)
    ox = east_x + 4
    _blit(g, ["+-+", "|O|", "+-+"], ox, out_y - 1)
    _pipe(g, [(east_x + 1, out_y), (ox - 1, out_y)])
    rows = g.rows()
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(r.rstrip() for r in rows)


def row_padder(padw: int) -> Circuit:
    """Read a count m (first input), then m real values, emitting each; then emit
    padw-m zeros. B holds m across the inner loop so no spill is needed.
      r M b        A=m, B=m, BP=m
      [rs]*m       forward m real values
      padw - b     A=padw ; A=padw-m=Z ; BP=Z   (B=m untouched by the loop)
      [0s]*Z       emit Z zeros
    """
    c = Circuit(40, 12)
    x, _ = c.run(0, 0, "@rMb")               # read m; B=m; BP=m
    ex, _ = c.counted_loop(x, 0, "rs")        # m real values
    x2, _ = c.run(ex, 0, lit(padw) + "-b")    # A=padw-m=Z ; BP=Z
    ex2, _ = c.counted_loop(x2, 0, "0s")      # Z zeros
    c.run(ex2, 0, "H")
    return c


def probe_padder(padw: int = 4) -> str:
    """I -> row_padder -> O. Input `m v0 v1 .. v{m-1}` -> `v0..v{m-1}` then padw-m zeros."""
    g = Circuit(80, 20)
    pad = _box(row_padder(padw))
    PX, PY = 8, 2
    _blit(g, pad, PX, PY)
    prow = PY + 1                              # padder interior row 0 -> boxed row 1
    _blit(g, ["+-+", "|I|", "+-+"], 2, prow - 1)
    _pipe(g, [(5, prow), (PX - 1, prow)])       # start just east of I's wall (col 4)
    ox = PX + len(pad[0]) + 3
    _blit(g, ["+-+", "|O|", "+-+"], ox, prow - 1)
    _pipe(g, [(PX + len(pad[0]), prow), (ox - 1, prow)])
    rows = g.rows()
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(r.rstrip() for r in rows)


def probe_ring() -> str:
    """B-ring: TURN (R-any: loader OR ring) -> FEED (r, then S to {consumer, ring}).
    Load [5,7]; a consumer reads 4 -> expect 5 7 5 7 (recirculation)."""
    g = Circuit(80, 30)
    TX, TY = 12, 10
    FX = TX + 12
    # TURN: R-any -> s to FEED
    _blit(g, ["+----+", "|>@Rv|", "|^.s<|", "+----+"], TX, TY)
    # FEED: r (ring-in) -> S to all outs {ring-out, pej-out}
    _blit(g, ["+----+", "|>@rv|", "|^.S<|", "+----+"], FX, TY)
    # ring pipes: TURN.to-FEED (east,row1) -> FEED.ring-in (west,row1)
    _pipe(g, [(TX + 6, TY + 1), (FX - 1, TY + 1)])
    #           FEED.ring-out (west,row2) -> TURN.ring-in (east,row2)
    _pipe(g, [(FX - 1, TY + 2), (TX + 6, TY + 2)])
    # loader source [5,7] -> TURN loader-in (west,row1)
    src = _box(Circuit_of("@5s7sH"))
    _blit(g, src, TX - len(src[0]) - 4, TY)
    _pipe(g, [(TX - 4, TY + 1), (TX - 1, TY + 1)])
    # consumer reads 4 -> O ; FEED.pej-out (east,row1) -> consumer
    con = _box(Circuit_of("@rsrsrsrsH"))
    CXc = FX + 8
    _blit(g, con, CXc, TY)
    _pipe(g, [(FX + 6, TY + 1), (CXc - 1, TY + 1)])
    ox = CXc + len(con[0]) + 3
    _blit(g, ["+-+", "|O|", "+-+"], ox, TY)
    _pipe(g, [(CXc + len(con[0]), TY + 1), (ox - 1, TY + 1)])
    rows = g.rows()
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(r.rstrip() for r in rows)


def collector_loop(res_cols: list[int], reader_x0: int) -> tuple[list[str], int]:
    """Reader that loops forever: read acc0..accK-1 (nearest-in, one r directly
    under each drop column in `res_cols`, left-to-right) and emit each to the one
    O out-pipe. Returns (boxed rows, O-column). `res_cols` are GLOBAL columns; the
    room is placed at global x=reader_x0."""
    # interior col that renders (after _box adds a border) at GLOBAL column rx is
    # rx - reader_x0 - 1.
    local = [rx - reader_x0 - 1 for rx in res_cols]
    width = max(local) + 3
    c = Circuit(width, 6)
    c.set(0, 1, ">"); c.set(1, 1, "@")              # rejoin (interior cols 0,1)
    for lc in local:
        c.set(lc, 1, "r")
        c.set(lc + 1, 1, "s")                        # emit to O (single out-pipe)
    # return: from east end down to row3, west to col0, up into the rejoin
    endx = max(local) + 2
    c.route((endx, 1), E, [(endx, 3), (0, 3), (0, 1)], (0, 1), E)
    o_col = res_cols[0]                              # O sits under the first r
    return _box(c), o_col


def assemble_1xk(m: int, kk: int, a_stream, b_streams) -> str:
    """1xK row-streaming matmul. Physical: one row of kk compute cells (a forwarded
    east, b from north), an acchold_loop under each, and a looping collector -> O.
    Feed row-major A to cell 0's west; B[*][j] repeated N times to cell j's north.
    Each acchold emits one C value per output row; the collector reads acc0..accK-1
    each lap, so O receives C in row-major order."""
    g = Circuit(500, 140)
    PX, X0, Y0 = 30, 34, 16
    boxes = [(X0 + j * PX, Y0) for j in range(kk)]
    for j in range(kk):
        cell, _p = compute_cell(fwd_a=(j < kk - 1), fwd_b=False)
        _blit(g, _box(cell), *boxes[j])
    # a forwarded east between cells (straight, row Y0+5)
    for j in range(kk - 1):
        P, Q = _cell_ports(*boxes[j]), _cell_ports(*boxes[j + 1])
        _pipe(g, [(P["a_out"][0] + 1, Y0 + 5), (Q["a_in"][0] - 1, Y0 + 5)])
    # a-source -> cell 0 west
    bx0, by0 = boxes[0]
    sa = _box(Circuit_of("@" + "".join(lit(v) + "s" for v in a_stream) + "H"))
    _blit(g, sa, bx0 - len(sa[0]) - 4, Y0 + 5 - 1)
    _pipe(g, [(bx0 - 4, Y0 + 5), (bx0 - 1, Y0 + 5)])
    # b-source j -> cell j north (straight, col bx+3)
    for j in range(kk):
        bx, by = boxes[j]
        sb = _box(Circuit_of("@" + "".join(lit(v) + "s" for v in b_streams[j]) + "H"))
        _blit(g, sb, bx + 3 - len(sb[0]) // 2, by - 6)
        _pipe(g, [(bx + 3, by - 3), (bx + 3, by - 1)])
    # acchold_loop under each cell; prod drops in (north), result exits south
    ay = Y0 + 13
    ach = len(_box(acchold_loop(m)))
    coly = ay + ach + 4
    res_cols = []
    for j in range(kk):
        bx, by = boxes[j]
        acbox = _box(acchold_loop(m))
        acw = len(acbox[0])
        ax = bx + 7 - acw // 2
        _blit(g, acbox, ax, ay)
        _pipe(g, [(bx + 7, by + 11), (bx + 7, ay - 1)])       # prod -> acc north
        res_cols.append(bx + 7)
        _pipe(g, [(bx + 7, ay + ach), (bx + 7, coly - 1)])    # result -> collector north
    # collector loop -> O  (reader_x0 leaves room for the `>@` rejoin before the
    # first r: first interior r col = res_cols[0]-reader_x0-1 must be >= 2)
    reader_x0 = res_cols[0] - 3
    reader, o_col = collector_loop(res_cols, reader_x0)
    _blit(g, reader, reader_x0, coly)
    rbot = coly + len(reader) - 1                             # reader south border
    _blit(g, ["+-+", "|O|", "+-+"], o_col - 1, rbot + 3)      # O two cells below
    _pipe(g, [(o_col, rbot + 1), (o_col, rbot + 2)])
    rows = g.rows()
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(r.rstrip() for r in rows)


def _pipe(g, pts):
    from randomfun2026solvers.memory_tape import _draw_pipe
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 != x1 and y0 != y1:
            raise ValueError(f"non-rectilinear pipe leg {(x0, y0)}->{(x1, y1)} "
                             "(would make _draw_pipe loop forever)")
    _draw_pipe(g, pts)


# Cell boxed port wall-cells, relative to the box top-left (bx,by):
#   a_in W (0,5) · b_in N (3,0) · a_out E (10,5) · b_out S (3,10) · prod S (7,10)
def _cell_ports(bx, by):
    return {
        "a_in": (bx + 0, by + 5), "b_in": (bx + 3, by + 0),
        "a_out": (bx + 10, by + 5), "b_out": (bx + 3, by + 10),
        "prod": (bx + 7, by + 10),
    }


def assemble_test(n: int, m: int, k: int, a_rows, b_cols) -> str:
    """Prove the ARRAY with direct per-row/col source rooms (no input LOADER yet).
    a_rows[i] feeds row i's west edge; b_cols[j] feeds col j's north edge.
    Output = C row-major."""
    g = Circuit(300, 300)
    PX, PY = 30, 26
    X0, Y0 = 30, 14

    boxes = {}
    for i in range(n):
        for j in range(k):
            cell, _p = compute_cell(fwd_a=(j < k - 1), fwd_b=(i < n - 1))
            bx, by = X0 + j * PX, Y0 + i * PY
            _blit(g, _box(cell), bx, by)
            boxes[(i, j)] = (bx, by)

    # inter-cell forward pipes
    for i in range(n):
        for j in range(k):
            P = _cell_ports(*boxes[(i, j)])
            if j < k - 1:
                Q = _cell_ports(*boxes[(i, j + 1)])
                y = P["a_out"][1]
                _pipe(g, [(P["a_out"][0] + 1, y), (Q["a_in"][0] - 1, y)])
            if i < n - 1:
                Q = _cell_ports(*boxes[(i + 1, j)])
                x = P["b_out"][0]
                _pipe(g, [(x, P["b_out"][1] + 1), (x, Q["b_in"][1] - 1)])

    # sources: row west edges, col north edges
    for i in range(n):
        bx, by = boxes[(i, 0)]
        sa = _box(Circuit_of("@" + "".join(lit(v) + "s" for v in a_rows[i]) + "H"))
        sw = len(sa[0])
        _blit(g, sa, bx - sw - 4, by + 5 - 1)
        _pipe(g, [(bx - 4, by + 5), (bx - 1, by + 5)])
    for j in range(k):
        bx, by = boxes[(0, j)]
        sb = _box(Circuit_of("@" + "".join(lit(v) + "s" for v in b_cols[j]) + "H"))
        _blit(g, sb, bx + 3 - 3, by - 6)
        _pipe(g, [(bx + 3, by - 3), (bx + 3, by - 1)])

    # accholds below each cell (prod straight down); result exits SOUTH.
    accs = {}
    for i in range(n):
        for j in range(k):
            bx, by = boxes[(i, j)]
            ac, _ap = acchold_cell(m)
            acbox = _box(ac)
            aw, ah = len(acbox[0]), len(acbox)
            ax, ay = bx + 5, by + 13
            _blit(g, acbox, ax, ay)
            accs[(i, j)] = (ax, ay, aw, ah)
            P = _cell_ports(bx, by)
            _pipe(g, [(P["prod"][0], P["prod"][1] + 1), (P["prod"][0], ay - 1)])

    # collector: one wide reader with an `r` directly under each acc, visited in
    # row-major order -> O. Result-pipes drop straight down (no channels, no
    # crossings) provided the acc x-coordinates are strictly increasing in
    # row-major order -- true for a single row; multi-row needs x-staggered accs.
    coly = Y0 + n * PY + 8
    order = [(i, j) for i in range(n) for j in range(k)]
    res_cols = []
    for (i, j) in order:
        ax, ay, aw, ah = accs[(i, j)]
        rx = ax + aw // 2                 # acc south-exit column
        res_cols.append(rx)
        _pipe(g, [(rx, ay + ah), (rx, coly - 1)])   # straight drop into reader north
    # build reader interior spanning the result columns: `r` under each, `s` after.
    lo, hi = min(res_cols), max(res_cols)
    width = hi - lo + 3
    inter = [" "] * width
    inter[0] = "@"
    for rx in res_cols:
        c = rx - lo + 1                    # +1 for reader box border added by _box
        inter[c] = "r"
        if c + 1 < width:
            inter[c + 1] = "s"
    reader = _box(Circuit_of("".join(inter)))
    rdx = lo - 1                           # box left border sits one col left of first r col
    _blit(g, reader, rdx, coly)
    # O to the south of the reader (single out-pipe, so every `s` binds it)
    ox = res_cols[0]
    ry_bottom = coly + len(reader) - 1        # reader's south border row
    _blit(g, ["+-+", "|O|", "+-+"], ox - 1, ry_bottom + 3)
    _pipe(g, [(ox, ry_bottom + 1), (ox, ry_bottom + 2)])

    rows = g.rows()
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(r.rstrip() for r in rows)


def probe_acc() -> str:
    """Feed products 5,14,15,28 into acchold_loop(2); expect emits 19, 43."""
    g = Circuit(120, 30)
    src = _box(Circuit_of("@" + "".join(lit(v) + "s" for v in [5, 14, 15, 28]) + "H"))
    _blit(g, src, 2, 6)
    ac = acchold_loop(2)
    acbox = _box(ac)
    AX, AY = 2 + len(src[0]) + 5, 3          # acc west port row = AY+4 = 7 = src mid row
    _blit(g, acbox, AX, AY)
    # src east -> acc west (acc reads nearest-in), straight on row 7.
    _pipe(g, [(2 + len(src[0]), 7), (AX - 1, 7)])
    # acc emit (nearest-out) -> O on the east, straight on row 7.
    ox = AX + len(acbox[0]) + 3
    _blit(g, ["+-+", "|O|", "+-+"], ox, 6)
    _pipe(g, [(AX + len(acbox[0]), 7), (ox - 1, 7)])
    rows = g.rows()
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(r.rstrip() for r in rows)


if __name__ == "__main__":
    if "--probe-cell" in sys.argv:
        probe_cell()
    elif "--probe-acc" in sys.argv:
        print(probe_acc())
    elif "--probe-ring" in sys.argv:
        print(probe_ring())
    elif "--probe-padder" in sys.argv:
        print(probe_padder(4))
    elif "--probe-countdown" in sys.argv:
        print(probe_countdown())
    elif "--probe-input-countdown" in sys.argv:
        print(probe_input_countdown())
    elif "--test-1xk" in sys.argv:
        # N=2,M=2,K=2  A=[[1,2],[3,4]] B=[[5,6],[7,8]] -> C row-major 19 22 43 50
        # a_stream = row-major A ; b_streams[j] = B[*][j] repeated N times
        print(assemble_1xk(2, 2,
                           a_stream=[1, 2, 3, 4],
                           b_streams=[[5, 7, 5, 7], [6, 8, 6, 8]]))
    elif "--test-1x2" in sys.argv:
        # A=[[1,2]] B=[[5,6],[7,8]] -> C=[[19,22]]
        print(assemble_test(1, 2, 2, a_rows=[[1, 2]], b_cols=[[5, 7], [6, 8]]))
    elif "--test-2x2" in sys.argv:
        # A=[[1,2],[3,4]] B=[[5,6],[7,8]] -> C=[[19,22],[43,50]]
        # row i west stream = A[i][*]; col j north stream = B[*][j]
        print(assemble_test(2, 2, 2,
                            a_rows=[[1, 2], [3, 4]],
                            b_cols=[[5, 7], [6, 8]]))
