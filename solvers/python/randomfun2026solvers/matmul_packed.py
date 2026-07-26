#!/usr/bin/env python3
"""The `matmul` machine, laid as tight as it has been measured to go.

This is the generator for ``tasks/solutions/matmul_packed.man``.  It is the same
one-room dataflow machine as ``matmul_ring.man`` -- same CFG, same six rings,
same fourteen pipes -- with the two things that were measured to be worth
changing, and none of the things that were measured not to be.

## What changed

**The channel columns the router never reached are gone.**  `matmul_grid` sizes
the west margin so that every routed lane *could* have a corridor column of its
own; the router almost never needs one, and five of the nine columns held
nothing at all.  Each is a column of the whole machine, paid once in
``max(w, h)`` and again in the square.

**The band geometry is re-searched against the trimmed room.**  With five
columns back, width stops binding and the search can spend them on rows: the
objective is still ``max(w, h)^2 * ticks``, still priced by walking the drawn
grid, but the frontier it is searching along has moved.

## What did not change, and why

**The block placer.**  :mod:`matmul_place` lays this CFG with
:mod:`blockplace`, the packer that won 2.48x on `snake`.  It was built, run and
priced, and it **loses**: 3.47e8 against 3.03e8.  Packing does shrink the room,
64x81 to 55x78, but the machine is 25 columns wider than its worker -- the strip
that coils the `x` and `b` rings -- so rows saved stop buying anything once
height meets width.  And `blockplace` routes every edge, where `matmul_grid`
groups its blocks into 17 fall-through chains whose internal edges cost nothing:
+21% ticks against -3% on the side.  The measurement is kept in that module.

**`MAC`'s self-loop return leg.**  It costs 19 cells against a 15-cell body, and
that looked like pure corridor to reclaim.  It is not: the return leg of a
self-loop laid on one row *is* the block's column span, and `MAC`'s span is 16
columns for 12 glyphs -- four cells of slack in the whole loop.  Nothing was
recovered here and nothing could be without splitting the loop across two rows,
which `matmul_grid` forbids for a self-loop because the return has to come up
into the cell west of the block's first glyph.

**`LOADA_GO`'s 56-cell walk for four glyphs.**  ``ri sx`` reads the input pipe
and sends to the `x` ring, and the two bands sit 25 columns apart.  Since the
block is a self-loop it must fit one *east-walked* row, which forces `x` east of
`io` -- so the fix is to make them adjacent, and the geometry search can express
it.  It was searched over all 5,040 band orders.  Every order that puts them
adjacent either fails to lay or scores worse: `io` is also read by `HEAD`,
`BL1_R`, `BL2_R` and `GRP_GO`, all of which pair it with `k` and `q`, and moving
`io` east of them costs more than `LOADA_GO` saves.
"""

from __future__ import annotations

from randomfun2026solvers import matmul_grid as G

__all__ = ["GEOMETRY", "build_grid", "build_room"]

#: The band geometry, re-searched against the *trimmed* room.
#:
#: All 5,040 band orders were enumerated and the best 24 annealed on widths; the
#: shipped order survives every one of them.  What moves is the widths, and in a
#: direction the untrimmed search could not take: `q` goes from 11 columns to 18.
#: Width used to bind at 89 against a height of 98, so a column spent on `q` was
#: a column off the score; with five margin columns back it is free until 96, and
#: `q` is the band `HEAD`, `LOADB_HEAD`, `BROW_GO`, `QBUILD`, `ROW` and `ROW_GO`
#: all stand in.  Widening it unwraps their rows: the room loses two rows and the
#: walk loses 464 ticks at the same time, which is the rare case of both terms of
#: ``max(w, h)^2 * ticks`` moving the right way at once.
_W = {"q": 18, "k": 7, "io": 4, "s": 7, "b": 4, "c": 8, "x": 9}
GEOMETRY = G.Geometry(
    recv_order=("q", "k", "io", "s", "b", "c", "x"),
    send_order=("q", "k", "io", "s", "b", "c", "x"),
    recv_w=_W,
    send_w=dict(_W),
)


def build_room() -> G.Room:
    """The worker room: tuned bands, and no channel column that is never used."""
    return G.build_room(G.plan(GEOMETRY), trim=True)


def build_grid() -> tuple[list[str], object, dict[str, object]]:
    """The whole machine, its debug overlay and its measured footprint."""
    art, dbg, info = G.build_grid(build_room())
    info["geometry"] = {b: GEOMETRY.recv_w[b] for b in GEOMETRY.recv_order}
    return art, dbg, info


if __name__ == "__main__":  # pragma: no cover - the generator's CLI
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path,
                    help="write the grid here")
    ap.add_argument("--html", type=Path,
                    help="write a labelled debug overlay here")
    ap.add_argument("--json", type=Path,
                    help="write the DebugMap region sidecar here")
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
