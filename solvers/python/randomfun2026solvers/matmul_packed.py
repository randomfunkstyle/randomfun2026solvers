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

**`MAC`'s self-loop return leg.**  It costs 19 cells against a 15-cell body --
39% of the whole machine at 16x16x16 -- and that looked like pure corridor to
reclaim.  It is not.  A self-loop laid on one east-walked row has a return leg
equal to its own column span, and `MAC`'s span is fixed by how close the `s` and
`c` bands can be brought:

    span = (c.recv_lo - s.recv_lo) + 5 = 11 + 5 = 16   for 12 glyphs

`s` cannot be narrower than 7 -- it hosts a stacked turnaround room, and it is
the one-word spill the `MAC` re-reads every lap, so it cannot be moved out to
the strip either -- and `b`, which sits between them, is already at its floor of
4.  **It did not fall, and it is a floor rather than slack.**

**`LOADA_GO`'s 56-cell walk for four glyphs.**  ``ri sx`` reads the input pipe
and sends to the `x` ring, and the two bands sit 25 columns apart.  The block is
a self-loop, so it must fit one *east-walked* row, which forces `x` **east** of
`io` -- the fix is exactly to make them adjacent, and the geometry search can
express it.  All 5,040 band orders were enumerated.  Every order that puts them
adjacent either fails to lay or scores worse: `io` is also read by `HEAD`,
`BL1_R`, `BL2_R` and `GRP_GO`, all of which pair it with `k` and `q`, and moving
`io` past them costs more than `LOADA_GO` saves.  **It fell by two cells a lap
(56 -> 54), as a side effect of the wider `q`, and not by the 48 the adjacency
would have bought.**

So neither band-spread cost was the lever it looked like.  The 464 ticks that
did come off are spread thinly -- `GRP_GO` 61 -> 59 body cells, `LOADA_GO` one
cell off each leg -- and are rows unwrapped by the wider `q` rather than bands
brought together.
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
