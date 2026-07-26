#!/usr/bin/env python3
"""`snake` laid out by :mod:`blockplace`: several banks of code, not one band each.

`snake_layout` gives every block a row band of its own, so 44 blocks became 118
rows in a 44-column room that was 5% full and the score paid `max(w,h)^2` for it.
This lays the same CFG — byte for byte the same `snake_ring.WORKER` — into
**four column banks standing side by side**, so the rows of one bank are the rows
of all of them.

## Where the split goes

`r` binds whichever of `ring_in` and `input` is the nearer *column*, so there is
exactly one split column and it is their midpoint.  What `snake_layout` assumed,
and what is not true, is that the pipes have to stand over the code they serve:
they do not.  Putting the ring pair as far **east** as it can go while staying
west of the split, and `input` at the room's east edge, moves the split from the
middle of the room to a few columns from its east wall — so the RING region gets
three quarters of the width, which is the ratio snake's blocks actually want
(26 RING-only against 7 IO-only).

## The banks

    | chP | code P  | chQ | code Q  | chR | code R |
      RING-only blocks   RING-only    IO-only, and the split sits in chR

`W` is bank Q's channels with its window run out to the east wall: the six blocks
that need a RING op *and* an IO op live there.  Their rows are exclusive over Q
and R, but bank P is west of them and keeps its own rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from randomfun2026solvers import blockplace as B
from randomfun2026solvers.blockorder import anneal, edges_of
from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.lllm_layout import Geometry, block_order
from randomfun2026solvers.snake_layout import RING_MIN, TOKEN_ZONE, WORKER_L, WX

if TYPE_CHECKING:  # pragma: no cover
    from randomfun2026solvers.man_debug import DebugMap

__all__ = ["BANKS", "build_grid", "build_room", "layout"]

#: Rows above the room: the relay, the input room and the risers.
#:
#: **The ring's capacity is bought sideways, not downwards.**  A straight pair of
#: risers holds ``2*(BAND_H - 4)`` cells, so the 14 words snake needs at rest used
#: to force ``BAND_H = 12``.  Folding the outgoing riser along the row under the
#: relay -- `gradebook_place.ring_legs`' trick, which snake never got -- buys
#: :data:`REACH` cells for nothing, and the relay is 10 columns of empty north
#: band wide already, so those columns cost the grid nothing at all.
#:
#: ``6`` is what a joint search over band, reach, bank widths and block order
#: settles on: rows 0-3 are the relay, 4-5 are the risers, and the pair holds
#: ``2*(6-4) + 10 = 14`` -- exactly :data:`~snake_layout.RING_MIN` and not one
#: cell of latency more.  Six rows handed back, and the room spends them on
#: being four columns narrower: 79x76 was **width**-bound.
BAND_H = 6

#: How far the outgoing riser reaches west along the row under the relay.
#:
#: West rather than east, and only the outgoing one.  Reaching *east* with the
#: incoming riser is the obvious symmetric move and it is wrong: it drags `RING`'s
#: ``r`` anchor toward the `IO` band and steals the split, which
#: :func:`audit_bindings` then refuses.  West is free -- the columns there are
#: north band nobody uses -- and it leaves both anchors exactly where the
#: straight risers had them, so the bands are unchanged.
#:
#: 10 buys the six rows :data:`BAND_H` gave up.  It is free in columns -- the fold
#: runs under the relay through north band nobody uses -- and free in latency,
#: because the ring still holds exactly the 14 words it must.
REACH = 10

#: The relay's interior width.  Its ports are the outgoing riser's fold end and
#: the incoming riser's foot, ``SPREAD + REACH`` apart, so ``REACH + SPREAD + 1``
#: is the floor, and `dataflow_relay.flat_relay` builds it at any width.  14 is
#: that floor at ``REACH = 10``; it also carries 11 words a lap against
#: `snake_ring.FLAT_RELAY`'s 7, so the turnaround stopped being the ring's cap.
RELAY_W = 14

#: Columns between the two pipes of a pair.  Both midpoints still land on the
#: split, and the gap is what the input room stands in.
SPREAD = 3

#: channel columns and code columns of each bank, west to east
BANKS = ((4, 11), (5, 11), (4, 9))


def layout(banks=BANKS):
    """Bank columns, pipe columns and the zone split, all from the bank widths."""
    (cp, wp), (cq, wq), (cr, wr) = banks
    P = B.Bank("P", 0, cp, cp + wp, ("RING",))
    Q = B.Bank("Q", P.code_hi + 1, cq, P.code_hi + 1 + cq + wq, ("RING",))
    r0 = Q.code_hi + 1
    R = B.Bank("R", r0, cr, r0 + cr + wr, ("IO",))
    mid = r0 + cr - 1                    # the split lands inside R's channels
    iw = R.code_hi + 3
    # The two pipes of a pair need not be adjacent: `r` splits on the midpoint of
    # (ring_in, input) and `s` on the midpoint of (ring_out, painter), and both
    # midpoints land on `mid` as long as the pairs are spread symmetrically.  The
    # spread is what leaves the painter's riser a column of its own east of the
    # input room, which is otherwise sitting on top of it.
    paint, inp = iw - 2, iw - 2 - SPREAD
    ring_in, ring_out = 2 * mid - inp, 2 * mid - paint
    if not Q.ch0 < ring_out or max(inp, paint) >= iw:
        raise Collision(f"pipe columns {ring_out},{ring_in},{inp},{paint} do not fit")
    geo = Geometry(TOKEN_ZONE,
                   {"RING": (P.code0, mid - 1), "IO": (mid + 2, iw - 2)},
                   {"RING": ring_in, "IO": inp}, {"RING": ring_out, "IO": paint},
                   P.code0, iw, lit_slack=0)
    return geo, {"P": P, "Q": Q, "R": R,
                 "W": B.Bank("W", Q.ch0, Q.nch, iw - 1, ("RING", "IO"))}


def assign(banks, first: dict[str, str]):
    """Candidate banks per block: its own, then its twin, then the wide window."""
    out = {}
    for n in WORKER_L:
        z = B.block_zones(WORKER_L, n, TOKEN_ZONE)
        if z == {"RING", "IO"}:
            out[n] = (banks["W"],)
        elif z == {"IO"}:
            out[n] = (banks["R"], banks["W"])
        else:
            side = first.get(n, "P")
            out[n] = (banks[side], banks["Q" if side == "P" else "P"], banks["W"])
    return out


def default_split(order: list[str], frac: float = 0.7) -> dict[str, str]:
    """The first `frac` of the RING-only blocks go west, the rest into bank Q."""
    ring = [n for n in order if B.block_zones(WORKER_L, n, TOKEN_ZONE) <= {"RING"}]
    k = int(len(ring) * frac)
    return {n: ("P" if i < k else "Q") for i, n in enumerate(ring)}


#: The block order, pinned so the generator is deterministic and pays for no
#: search.  **Found on measured ticks, jointly with the bank widths and the north
#: band** -- not by :func:`tuned_order`, whose unweighted MinLA this beats.
#:
#: That the objective can be the real one is the whole trick: `build_room` costs
#: ~18s, but `optimize.verify` on the fast engine costs **0.08s**, so ranking
#: candidates on a proxy was shortlisting for a decider worth 0.4% of the price.
ORDER = [
    "INIT", "FR_BODY", "FR_PUSH", "FR2_PUSH", "FR_END", "FR2_BODY",
    "FR2_END", "G_PUSH", "G_BODY", "G_END", "T_GROW", "MAIN", "TICK",
    "T_H", "T_V_OK", "T_H_OK", "DEAD_PAINT", "DC_A_PIX", "DEAD_HV",
    "DEAD_V", "T_V", "DEAD_DONE", "M_END", "M_BODY", "M_KEEP", "M_CMP",
    "DEAD_C", "DC_A", "FRUIT", "DC_MARK", "DC_B_PIX", "DC_B", "T_MOVE",
    "HALT", "DEAD_PIX", "T_F", "DIR", "DIR_V", "DIR_NEG", "DIR_H",
    "DIR_SET", "ROT_END", "ROT_PUSH", "ROT_BODY",
]


def build_room(banks=BANKS, order=None, frac: float = 0.7, seed: int = 0,
               attempts: int = 40) -> B.Room:
    geo, bk = layout(banks)
    order = list(order or ORDER)
    return B.build(WORKER_L, "INIT", assign(bk, default_split(order, frac)), geo,
                   order=order, attempts=attempts, seed=seed)


def tuned_order(banks=BANKS, steps: int = 40_000) -> list[str]:
    """A minimum-linear-arrangement order, unweighted.

    Weighting the edges by loop nesting depth was tried and measured *worse* on
    snake, exactly as weighting them by a profile of the public cases had been:
    both fold the cold death paths onto the hot loop and lengthen the corridors
    that carry it.
    """
    base = block_order(WORKER_L, "INIT")
    room = build_room(banks, base)
    rows = {n: len(p.plan.rows) + (2 if p.plan.branch else 0)
            for n, p in room.placed.items()}
    return anneal(base, rows, dict.fromkeys(edges_of(WORKER_L), 1), steps=steps)


# ── the whole machine ─────────────────────────────────────────────────────────
def build_grid(banks=BANKS, order=None, frac: float = 0.7, seed: int = 0,
               band_h: int = BAND_H, reach: int = REACH,
               relay_w: int = RELAY_W
               ) -> tuple[list[str], DebugMap, dict[str, object]]:
    """Worker + ring + input + the proven painter/LM-75 harness, as one grid."""
    from randomfun2026solvers.dataflow_relay import flat_relay
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.plotter_block import pipe
    from randomfun2026solvers.snake_layout import _cells
    from randomfun2026solvers.snake_ring import (
        HARNESS_H,
        HARNESS_W,
        PROBE_PAINTER,
        stamp_harness,
    )
    from randomfun2026solvers.value_ring import stamp, walls

    room = build_room(banks, order, frac, seed)
    walked_cells_all_hold_a_glyph(room)
    B.walks_are_the_program(room, WORKER_L)
    geo, _bk = layout(banks)
    iw, ih = geo.iw, room.height
    wy = band_h + 1
    ring_in, inp = WX + geo.pipe_in["RING"], WX + geo.pipe_in["IO"]
    ring_out, painter_col = WX + geo.pipe_out["RING"], WX + geo.pipe_out["IO"]

    hx = WX + iw + 3                       # two clear columns for the feed pipe
    g = Circuit(hx + HARNESS_W, wy + ih + 1)
    for y, line in enumerate(room.rows()):
        for x, ch in enumerate(line):
            if ch != " ":
                g.set(WX + x, wy + y, ch)
    walls(g, WX, wy, iw, ih)
    north = wy - 1

    # ── the ring: the relay over the pair, the outgoing riser folded west ──────
    #
    # Ports: the outgoing riser turns west along the row under the relay and ends
    # `reach` columns away, the incoming one drops straight from the relay's wall.
    # So the two ports are `reach + SPREAD` apart and the relay is placed to cover
    # both.  Only the outgoing one folds -- see :data:`REACH`.
    relay_s = 3                            # the relay's south wall row
    top, bot = relay_s + 1, north - 1
    if bot < top:
        raise Collision(f"band {band_h} leaves no row for a riser")
    out_port, in_port = ring_out - reach, ring_in
    relay_x = out_port - 1
    if in_port > relay_x + relay_w:
        raise Collision(f"relay interior {relay_w} cannot span ports "
                        f"{out_port}..{in_port}")
    if relay_x < 0:
        raise Collision(f"reach {reach} pushes the relay off the west wall")
    stamp(g, relay_x, 0, flat_relay(relay_w))
    feed_y = PROBE_PAINTER[1] + 1
    legs = {
        # A pipe's first cell must point *away* from its room or it parses as a
        # loose pipe the relay's `s` cannot see, and the machine still loads.
        "out_RING": [(ring_out, bot), (ring_out, top), (out_port, top)],
        "in_RING": [(in_port, top), (in_port, bot)],
        "in_IO": [(inp, 3), (inp, north - 1)],
        "out_IO": [(painter_col, north - 1), (painter_col, 1),
                   (hx, 1), (hx, feed_y)],
    }
    pipes = {k: _cells(v) for k, v in legs.items()}
    if relay_x + relay_w + 1 >= inp - 1:
        raise Collision(f"relay reaches {relay_x + relay_w + 1}, the input room "
                        f"stands at {inp - 1}")
    fwd = pipe(g, legs["out_RING"], into=(out_port, relay_s))
    ret = pipe(g, legs["in_RING"], into=(ring_in, north))
    if fwd + ret < RING_MIN:
        raise Collision(f"ring holds {fwd + ret} words, wanted {RING_MIN}")

    stamp(g, inp - 1, 0, ["+-+", "|I|", "+-+"])
    pipe(g, legs["in_IO"], into=(inp, north))
    stamp_harness(g, hx, 0)
    pipe(g, legs["out_IO"], into=(hx + PROBE_PAINTER[0] - 1, feed_y))

    rows = [r.rstrip() for r in g.rows()]
    while rows and not rows[-1]:
        rows.pop()
    audit_bindings(room, geo, pipes, wy)

    d = DebugMap("snake — a dataflow ring machine, banked")
    d.region("harness", hx + 1, 0, HARNESS_W - 1, HARNESS_H, color="#a855f7",
             note="painter + LM-75, engine-verified on all 129 public frames")
    d.region("relay", relay_x, 0, relay_w + 2, 4, color="#0ea5e9",
             note=f"turnaround of the {fwd + ret}-cell body ring")
    d.region("input", inp - 1, 0, 3, 3, color="#64748b")
    for band, (lo, hi) in geo.zone_cols.items():
        d.region(f"band:{band}", WX + lo, wy, hi - lo + 1, ih, color="#1f2937",
                 note=f"{band} pipe ops stand here; nearest column binds")
    for name, p in room.placed.items():
        d.region(f"block:{name}", WX + p.bank.entry, wy + p.ys[0],
                 p.bank.iw - p.bank.entry, p.ys[-1] - p.ys[0] + 1,
                 note=f"{p.bank.name}: " + " ".join(WORKER_L[name][0]),
                 color="#f59e0b", tags=["block"])

    info = {
        "worker": (iw, ih),
        "grid": (max(len(r) for r in rows), len(rows)),
        "ring": fwd + ret,
        "blocks": len(room.placed),
        "corridor_cells": room.corridor_cells,
        "banks": {n: p.bank.name for n, p in room.placed.items()},
    }
    return rows, d, info


def audit_bindings(room: B.Room, geo, pipes: dict[str, list[tuple[int, int]]],
                   wy: int = BAND_H + 1) -> None:
    """Every `r`/`s` in the worker must reach the pipe its band promised it.

    Measured over *every* cell of each pipe, which is stricter than the spec's
    "segment attached to the room": if this passes, no reading of "nearest" can
    make the machine bind the wrong pipe.
    """
    for name, p in room.placed.items():
        pipe_toks = [t for t in WORKER_L[name][0] if t in TOKEN_ZONE]
        placed = [(c, p.ys[i]) for i, r in enumerate(p.plan.rows)
                  for c, gl in r.cells if gl in ("r", "s")]
        if len(placed) != len(pipe_toks):
            raise Collision(f"{name}: {len(placed)} r/s glyphs, {len(pipe_toks)} tokens")
        for (cx, cy), tok in zip(placed, pipe_toks, strict=True):
            side = "in" if tok[0] == "r" else "out"
            gx, gy = WX + cx, wy + cy
            best = min(("RING", "IO"),
                       key=lambda z: min(abs(gx - px) + abs(gy - py)
                                         for px, py in pipes[f"{side}_{z}"]))
            if best != TOKEN_ZONE[tok]:
                raise Collision(f"{name}: {tok!r} at ({gx},{gy}) reaches {best}, "
                                f"not {TOKEN_ZONE[tok]}")


def walked_cells_all_hold_a_glyph(room: B.Room) -> None:
    """Every cell a block walks must hold the op it was compiled from.

    A block whose row lost a glyph still loads and still runs; it just computes
    something else.  This is `subset_sum_grid`'s static check, restated.
    """
    for name, p in room.placed.items():
        for i, row in enumerate(p.plan.rows):
            for col, glyph in row.cells:
                got = room.field.get(col, p.ys[i])
                if got != glyph:
                    raise Collision(f"{name} row {i}: ({col},{p.ys[i]}) holds "
                                    f"{got!r}, compiled {glyph!r}")


if __name__ == "__main__":  # pragma: no cover - the generator's CLI
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path)
    ap.add_argument("--html", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--order", action="store_true", help="re-derive ORDER")
    args = ap.parse_args()
    if args.order:
        print(tuned_order())
        raise SystemExit
    grid, dbg, meta = build_grid()
    if args.man:
        args.man.write_text("\n".join(grid) + "\n")
    if args.html:
        dbg.write_html(grid, args.html)
    if args.json:
        dbg.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(grid))
    else:
        print(meta)
