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

#: Rows above the room: the relay, the input room and the risers.  One more
#: than the band layout used, because the ring pipes now run straight down
#: instead of doglegging east, and the ring has to hold 14 words at rest.
BAND_H = 12
WY = BAND_H + 1

#: Columns between the two pipes of a pair.  Both midpoints still land on the
#: split, and the gap is what the input room stands in.
SPREAD = 3

#: channel columns and code columns of each bank, west to east
BANKS = ((6, 12), (6, 12), (5, 12))


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


def build_room(banks=BANKS, order=None, frac: float = 0.7, seed: int = 0,
               attempts: int = 40) -> B.Room:
    geo, bk = layout(banks)
    order = order or block_order(WORKER_L, "INIT")
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
    geo, bk = layout(banks)
    room = build_room(banks, base)
    rows = {n: len(p.plan.rows) + (2 if p.plan.branch else 0)
            for n, p in room.placed.items()}
    return anneal(base, rows, dict.fromkeys(edges_of(WORKER_L), 1), steps=steps)


# ── the whole machine ─────────────────────────────────────────────────────────
def build_grid(banks=BANKS, order=None, frac: float = 0.7,
               seed: int = 0) -> tuple[list[str], DebugMap, dict[str, object]]:
    """Worker + ring + input + the proven painter/LM-75 harness, as one grid."""
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.plotter_block import pipe
    from randomfun2026solvers.snake_layout import _cells
    from randomfun2026solvers.snake_ring import (
        FLAT_RELAY,
        HARNESS_H,
        HARNESS_W,
        PROBE_PAINTER,
        stamp_harness,
    )
    from randomfun2026solvers.value_ring import stamp, walls

    room = build_room(banks, order, frac, seed)
    walked_cells_all_hold_a_glyph(room)
    geo, _bk = layout(banks)
    iw, ih = geo.iw, room.height
    ring_in, inp = WX + geo.pipe_in["RING"], WX + geo.pipe_in["IO"]
    ring_out, painter_col = WX + geo.pipe_out["RING"], WX + geo.pipe_out["IO"]

    hx = WX + iw + 3                       # two clear columns for the feed pipe
    g = Circuit(hx + HARNESS_W, WY + ih + 1)
    for y, line in enumerate(room.rows()):
        for x, ch in enumerate(line):
            if ch != " ":
                g.set(WX + x, WY + y, ch)
    walls(g, WX, WY, iw, ih)
    north = WY - 1

    # ── the ring: relay directly over the ring pair, both pipes kept vertical ──
    relay_x = ring_out - 2
    stamp(g, relay_x, 0, FLAT_RELAY)
    relay_s = 3                            # the relay's south wall row
    feed_y = PROBE_PAINTER[1] + 1
    legs = {
        # A pipe's first cell must point *away* from its room or it parses as a
        # loose pipe the relay's `s` cannot see, and the machine still loads.
        "out_RING": [(ring_out, north - 1), (ring_out, relay_s + 1)],
        "in_RING": [(ring_in, relay_s + 1), (ring_in, north - 1)],
        "in_IO": [(inp, 3), (inp, north - 1)],
        "out_IO": [(painter_col, north - 1), (painter_col, 1),
                   (hx, 1), (hx, feed_y)],
    }
    pipes = {k: _cells(v) for k, v in legs.items()}
    fwd = pipe(g, legs["out_RING"], into=(ring_out, relay_s))
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
    audit_bindings(room, geo, pipes)

    d = DebugMap("snake — a dataflow ring machine, banked")
    d.region("harness", hx + 1, 0, HARNESS_W - 1, HARNESS_H, color="#a855f7",
             note="painter + LM-75, engine-verified on all 129 public frames")
    d.region("relay", relay_x, 0, 12, 4, color="#0ea5e9",
             note=f"turnaround of the {fwd + ret}-cell body ring")
    d.region("input", inp - 1, 0, 3, 3, color="#64748b")
    for band, (lo, hi) in geo.zone_cols.items():
        d.region(f"band:{band}", WX + lo, WY, hi - lo + 1, ih, color="#1f2937",
                 note=f"{band} pipe ops stand here; nearest column binds")
    for name, p in room.placed.items():
        d.region(f"block:{name}", WX + p.bank.entry, WY + p.ys[0],
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


def audit_bindings(room: B.Room, geo, pipes: dict[str, list[tuple[int, int]]]) -> None:
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
            gx, gy = WX + cx, WY + cy
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
    args = ap.parse_args()
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
