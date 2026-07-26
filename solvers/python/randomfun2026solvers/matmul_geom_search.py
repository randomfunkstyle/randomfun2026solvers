#!/usr/bin/env python3
"""Search where the fourteen pipes attach, priced by the score they produce.

The obvious objective -- fewest walked rows -- is the wrong one, and expensively
so.  Rows fall as the room gets wider, but a wider room *spreads the bands*, and
the man walks every blank between them: the first geometry found this way put
`s` and `c` thirty-five columns apart, so ``MAC`` -- 1,536 laps at full size --
walked seventy cells for its twelve glyphs and the machine measured 71,622 ticks
a case against the CFG's 6,569-cell model.

So the objective here is the contest's own: ``max(w, h)^2 * mean ticks``, with
ticks from :func:`~randomfun2026solvers.matmul_grid.estimate_ticks`, which walks
the drawn grid block by block and agrees with the engine to better than 1%.

Run it as ``python -m randomfun2026solvers.matmul_geom_search``; it prints a
``Geometry`` literal to paste into :mod:`randomfun2026solvers.matmul_grid`.
"""

from __future__ import annotations

import random

from randomfun2026solvers import matmul_grid as G
from randomfun2026solvers.circuit import Collision

__all__ = ["evaluate", "search"]

#: A band narrower than this cannot host its ring's turnaround room, which
#: straddles both attach columns and needs three clear columns either side.
MIN_WIDTH = 7


def geometry(order: tuple[str, ...], widths: tuple[int, ...]) -> G.Geometry:
    w = dict(zip(order, widths))
    return G.Geometry(order, order, w, dict(w))


def evaluate(geom: G.Geometry, traces) -> tuple[float, int, int, float] | None:
    """``(score, width, height, mean ticks)`` for one geometry, or None if it fails."""
    try:
        room = G.build_room(G.plan(geom))
        G.check_room(room)
    except (Collision, RecursionError):
        return None
    w = room.iw + 3 + G.STRIP_W * 3 + G.WX
    h = room.ih + G.NB + 2
    ticks = sum(G.estimate_ticks(room, r, ln) for r, ln in traces) / len(traces)
    return max(w, h) ** 2 * ticks, w, h, ticks


def search(iters: int = 4000, seed: int = 0, total: int = 52) -> G.Geometry:
    """Anneal over band order and band widths, keeping the best score seen."""
    rng = random.Random(seed)
    traces = G.public_traces()
    order = list(G.BANDS)
    widths = [MIN_WIDTH] * 7
    for _ in range(total - MIN_WIDTH * 7):
        widths[rng.randrange(7)] += 1

    cur = evaluate(geometry(tuple(order), tuple(widths)), traces)
    best = (cur, list(order), list(widths)) if cur else None
    for _ in range(iters):
        no, nw = list(order), list(widths)
        move = rng.random()
        if move < 0.4:
            i, j = rng.sample(range(7), 2)
            no[i], no[j] = no[j], no[i]
            nw[i], nw[j] = nw[j], nw[i]
        elif move < 0.8:
            i, j = rng.sample(range(7), 2)
            if nw[i] > MIN_WIDTH:
                nw[i] -= 1
                nw[j] += 1
        else:
            i = rng.randrange(7)
            nw[i] += rng.choice((-1, 1))
            if nw[i] < MIN_WIDTH:
                continue
        cand = evaluate(geometry(tuple(no), tuple(nw)), traces)
        if cand is None:
            continue
        if cur is None or cand[0] <= cur[0] or rng.random() < 0.03:
            order, widths, cur = no, nw, cand
        if best is None or cand[0] < best[0][0]:
            best = (cand, list(no), list(nw))
    if best is None:  # pragma: no cover - the seed geometry always lays
        raise Collision("no geometry lays the CFG")
    return geometry(tuple(best[1]), tuple(best[2]))


if __name__ == "__main__":  # pragma: no cover - the search CLI
    import sys

    traces = G.public_traces()
    best = None
    for total in range(35, 70, 3):
        for seed in range(2):
            geom = search(iters=int(sys.argv[1]) if len(sys.argv) > 1 else 1500,
                          seed=seed, total=total)
            got = evaluate(geom, traces)
            print(f"total={total:3d} seed={seed} score={got[0]:.3e} "
                  f"{got[1]}x{got[2]} ticks={got[3]:.0f} "
                  f"{[ (b, geom.recv_w[b]) for b in geom.recv_order ]}", flush=True)
            if best is None or got[0] < best[0][0]:
                best = (got, geom)
    got, geom = best
    print(f"\nbest score={got[0]:.3e}  {got[1]}x{got[2]}  ticks={got[3]:.0f}")
    print(f"_W = {geom.recv_w}")
    print(f"GEOMETRY = Geometry({geom.recv_order}, {geom.send_order}, _W, dict(_W))")
