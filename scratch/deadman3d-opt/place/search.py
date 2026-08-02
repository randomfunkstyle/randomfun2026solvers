#!/usr/bin/env python3
"""Placement search: emit ``FAST`` or ``COMPACT`` on demand.

Two modes, one objective each, both subject to full legality on every candidate
(LAYOUT-MANAGER.md is emphatic and correct: the check must run *inside* the
loop, because moving block A silently rebinds block B's ``s``).

``FAST``
    minimise ticks subject to an extent ceiling.
``COMPACT``
    minimise extent subject to a tick ceiling.

Three engines, chosen by size:

``exhaustive``
    Cartesian product over each free node's candidate cells.  Used for the small
    rooms, where it proves the optimum rather than finding it, which is the
    whole point of validating there first.
``anneal``
    Simulated annealing over single-node moves, for rooms where the product is
    astronomical.
``rows``
    The row-assignment special case: when the structure is a stack of bands
    whose only freedom is vertical order, the problem is an assignment problem
    and is solved exactly by Hungarian, not searched.

A note on move granularity
--------------------------
The annealer moves **nodes**, never glyphs.  Measured: relocating a drop column
alone was worth exactly zero, because the man walked back east to a stationary
send; only when the send and the landing moved with it did it pay.  A glyph-level
annealer would spend its whole budget discovering that, then report the
discovery as a win.  :func:`place.score.structure_check` re-asserts this on every
result that leaves the module.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass

from ir import Placement
from legal import check
from score import Score, Workload, score

__all__ = ["Result", "candidates_in_room", "exhaustive", "anneal", "solve", "FAST", "COMPACT"]

FAST = "FAST"
COMPACT = "COMPACT"


@dataclass
class Result:
    mode: str
    placement: Placement | None
    score: Score | None
    considered: int = 0
    legal: int = 0
    note: str = ""

    def __bool__(self) -> bool:
        return self.placement is not None

    def explain(self) -> str:
        if not self:
            return f"{self.mode}: no legal placement ({self.considered} considered). {self.note}"
        return f"{self.mode}: {self.score.explain()}\n    ({self.legal}/{self.considered} legal)"


def candidates_in_room(leg, node_name: str, room=None) -> list[tuple[int, int]]:
    """Every origin at which the node's whole body fits in the room's interior."""
    room = room or leg.room
    if room is None:
        raise ValueError("free node needs a room to be placed in")
    x0, y0, x1, y1 = room
    n = leg.nodes[node_name]
    dxs = [c[0] for c in n.body] or [0]
    dys = [c[1] for c in n.body] or [0]
    lo_x, hi_x = min(dxs), max(dxs)
    lo_y, hi_y = min(dys), max(dys)
    return [
        (x, y)
        for x in range(x0 - lo_x, x1 - hi_x + 1)
        for y in range(y0 - lo_y, y1 - hi_y + 1)
    ]


def _objective(mode: str, s: Score, tick_ceiling, extent_ceiling):
    """Lower is better; ``None`` means the candidate violates its ceiling."""
    w, h = s.extent
    if mode == FAST:
        if extent_ceiling is not None and (w > extent_ceiling[0] or h > extent_ceiling[1]):
            return None
        return (s.ticks, s.footprint)
    if mode == COMPACT:
        if tick_ceiling is not None and s.ticks > tick_ceiling:
            return None
        return (s.footprint, s.ticks)
    raise ValueError(f"unknown mode {mode!r}")


def exhaustive(
    leg,
    mode: str = FAST,
    wl: Workload | None = None,
    tick_ceiling: float | None = None,
    extent_ceiling: tuple[int, int] | None = None,
    limit: int = 2_000_000,
    dead=None,
) -> Result:
    """Enumerate every placement of every free node.  Proves the optimum.

    Used on the small rooms deliberately: a framework that cannot reproduce a
    known-good hand layout on a 4x8 has no business being pointed at a bank.
    """
    free = leg.free_nodes()
    pools = [candidates_in_room(leg, nm) for nm in free]
    total = math.prod(len(pl) for pl in pools) if pools else 1
    if total > limit:
        return Result(mode, None, None, 0, 0,
                      note=f"{total:,} combinations exceeds limit {limit:,}; use anneal")

    best = None
    best_key = None
    considered = legal_n = 0
    for combo in itertools.product(*pools) if pools else [()]:
        considered += 1
        p = Placement(leg, node_pos=dict(zip(free, combo)))
        if check(p, dead):
            continue
        legal_n += 1
        s = score(p, wl)
        key = _objective(mode, s, tick_ceiling, extent_ceiling)
        if key is None:
            continue
        if best_key is None or key < best_key:
            best_key, best = key, (p, s)
    if best is None:
        return Result(mode, None, None, considered, legal_n,
                      note="no candidate met the ceiling")
    return Result(mode, best[0], best[1], considered, legal_n)


def anneal(
    leg,
    mode: str = FAST,
    wl: Workload | None = None,
    tick_ceiling: float | None = None,
    extent_ceiling: tuple[int, int] | None = None,
    iters: int = 40_000,
    seed: int = 0,
    dead=None,
    start: Placement | None = None,
) -> Result:
    """Simulated annealing over **node** moves.  For rooms too big to enumerate."""
    rng = random.Random(seed)
    free = leg.free_nodes()
    if not free:
        p = Placement(leg)
        v = check(p, dead)
        return Result(mode, None if v else p, None if v else score(p, wl), 1, 0 if v else 1,
                      note="; ".join(map(str, v[:3])))
    pools = {nm: candidates_in_room(leg, nm) for nm in free}

    def rand_placement():
        return Placement(leg, node_pos={nm: rng.choice(pools[nm]) for nm in free})

    cur = start
    if cur is None:
        for _ in range(4000):
            cand = rand_placement()
            if not check(cand, dead):
                cur = cand
                break
    if cur is None:
        return Result(mode, None, None, 4000, 0, note="no legal seed found")

    cur_s = score(cur, wl)
    cur_key = _objective(mode, cur_s, tick_ceiling, extent_ceiling)
    cur_cost = _flatten(cur_key, cur_s, mode)
    best, best_s, best_key = cur, cur_s, cur_key
    considered = legal_n = 1

    for i in range(iters):
        t = 1.0 - i / iters
        temp = max(1e-6, 6.0 * t * t)
        nm = rng.choice(free)
        cand = cur.with_node(nm, rng.choice(pools[nm]))
        considered += 1
        if check(cand, dead):
            continue
        legal_n += 1
        s = score(cand, wl)
        key = _objective(mode, s, tick_ceiling, extent_ceiling)
        cost = _flatten(key, s, mode)
        if cost <= cur_cost or rng.random() < math.exp((cur_cost - cost) / temp):
            cur, cur_s, cur_key, cur_cost = cand, s, key, cost
            if key is not None and (best_key is None or key < best_key):
                best, best_s, best_key = cand, s, key
    if best_key is None:
        return Result(mode, None, None, considered, legal_n,
                      note="no candidate met the ceiling")
    return Result(mode, best, best_s, considered, legal_n)


def _flatten(key, s: Score, mode: str) -> float:
    """A scalar for the Metropolis test; ceiling violations get a large penalty."""
    if key is not None:
        return float(key[0]) + 1e-6 * float(key[1])
    return 1e9 + (s.ticks if mode == COMPACT else s.footprint)


def solve(leg, mode: str = FAST, **kw) -> Result:
    """Pick an engine by size and run it.  This is the front door."""
    free = leg.free_nodes()
    pools = [candidates_in_room(leg, nm) for nm in free] if free else []
    total = math.prod(len(pl) for pl in pools) if pools else 1
    limit = kw.pop("exhaustive_limit", 400_000)
    if total <= limit:
        r = exhaustive(leg, mode, limit=limit,
                       **{k: v for k, v in kw.items() if k != "iters" and k != "seed"})
        if r:
            r.note = f"exhaustive over {total:,} combinations -- this is the optimum"
            return r
    return anneal(leg, mode, **kw)
