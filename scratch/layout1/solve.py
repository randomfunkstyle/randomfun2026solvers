"""The search: enumerate placements, verify every one, price the survivors.

The loop is deliberately dumb — exhaustive over a declared candidate set — because
Phase 1 is about whether the *formulation* is right, not whether the search is
fast.  What is not dumb is the order of the checks:

    for every candidate placement:
        check_bindings(...)      <- the real one, before anything is routed
        route every pipe
        render onto lm1.machine._Grid   <- the real collision primitive
        price it

``check_bindings`` runs on **every** candidate, not on the winner
(``LAYOUT-MANAGER.md`` property 1).  :attr:`Report.rejected_by_bindings` counts
how often that mattered; if it is ever zero on a real problem, the whole design
is a packing solver wearing a hat.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .bind import MachineError, Unexpressible, audit_segments, check_layout
from .geom import Layout, Placed, free_offsets
from .model import FORWARDER_CELLS, MIN_PIPE, SIDES, Block, Leg, Problem, Route, Solution
from .route import Field, NoRoute, room_route, route

_SIDE_GROW = {"N": 0, "S": 1, "E": 2, "W": 3}


@dataclass
class Report:
    """What the search saw, not just what it chose."""

    candidates: int = 0
    rejected_overlap: int = 0
    rejected_bounds: int = 0
    rejected_by_bindings: int = 0
    rejected_no_route: int = 0
    rejected_collision: int = 0
    feasible: int = 0
    best: Solution | None = None
    best_cost: float = float("inf")
    segment_warnings: list[str] = field(default_factory=list)
    #: Every feasible candidate as ``(weighted cells, total drawn cells)``.  Rung 3
    #: of the ladder needs both to show that a length objective is *blind* where a
    #: length-x-frequency one is not.
    samples: list[tuple[float, int]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.candidates} candidates: {self.feasible} feasible, "
            f"{self.rejected_by_bindings} rebound a glyph, "
            f"{self.rejected_no_route} unroutable, "
            f"{self.rejected_overlap + self.rejected_bounds} geometrically dead, "
            f"{self.rejected_collision} collided"
        )


def corners(cells: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
    """Cell path -> the polyline ``_Grid.draw_pipe`` wants."""
    if len(cells) < 2:
        return list(cells)
    pts = [cells[0]]
    d = (cells[1][0] - cells[0][0], cells[1][1] - cells[0][1])
    for a, b in zip(cells[1:], cells[2:], strict=False):
        nd = (b[0] - a[0], b[1] - a[1])
        if nd != d:
            pts.append(a)
            d = nd
    pts.append(cells[-1])
    return pts


def render(layout: Layout, routes: dict[str, Route]):
    """Draw the whole candidate on the production ``_Grid``.

    Any two things wanting the same cell raise ``MachineError`` from the same code
    the generator uses, so a candidate that renders is a candidate whose geometry
    a real build would accept.
    """
    from randomfun2026solvers.lm1.machine import _Grid

    g = _Grid()
    for pl in layout.placed.values():
        x0, y0, w, h = pl.rect
        g.room(x0, y0, x0 + w - 1, y0 + h - 1)
    for r in routes.values():
        if r.room is not None:
            x0, y0, w, h = r.room
            g.room(x0, y0, x0 + w - 1, y0 + h - 1)
        for leg in r.legs:
            g.draw_pipe(corners(leg.cells))
    return g


# ── candidate generation ─────────────────────────────────────────────────────
def _headroom(block: Block, side: str, base: dict[str, tuple[int, int]]) -> int:
    """How far this side can grow before it shares a cell with another block.

    ``TAPED_CHAIN_REACH``'s phrasing is exactly this: the gate grows west "until
    its wall stands beside the previous gate's" — *a hop over the gap the previous
    bank's feed riser needs, and no more*.  Rooms may abut with no gap column
    (``ARCH.md`` §7.4b), so the wall may land flush against an obstacle.
    """
    px, py = base[block.name]
    room = 10**6
    for other, (ox, oy) in base.items():
        if other == block.name:
            continue
        # NOTE: obstacles are measured against the *ungrown* rect of the other
        # block, which is what the real registries do — nothing else moves.
        b = _BLOCKS[other]
        if side in ("E", "W"):
            if oy + b.h <= py or py + block.h <= oy:
                continue  # different rows: cannot be in the way
            gap = px - (ox + b.w) if side == "W" else ox - (px + block.w)
        else:
            if ox + b.w <= px or px + block.w <= ox:
                continue
            gap = py - (oy + b.h) if side == "N" else oy - (py + block.h)
        if gap >= 0:  # a block on the *other* side is not an obstacle
            room = min(room, gap)
    return max(0, room)


_BLOCKS: dict[str, Block] = {}


def _growths(
    block: Block,
    problem: Problem,
    touches: dict[tuple[str, str], tuple[int, int]],
    base: dict[str, tuple[int, int]],
):
    """The growth amounts worth trying for one block: none, *reach*, or *flush*.

    A grown side is only ever worth one of three amounts: nothing; exactly enough
    to stand ``MIN_PIPE`` from the pipe it is reaching for; or as far as the next
    block lets it, when that is nearer.  Anything between leaves pipe on the table
    and anything beyond collides — so the search space stays small because the
    physics is narrow, which is the useful half of the ``REACH`` result.
    """
    if not block.grow:
        yield (0, 0, 0, 0)
        return
    per_side: dict[str, list[int]] = {}
    for side in sorted(block.grow):
        want = {0, _headroom(block, side, base)}
        for pipe in problem.pipes:
            for near, far in ((pipe.dst, pipe.src), (pipe.src, pipe.dst)):
                if near[0] != block.name or far[0] == block.name:
                    continue
                if block.port(near[1]).side != side:
                    continue
                want.add(_reach_amount(block, side, touches[far]))
        per_side[side] = sorted(a for a in want if 0 <= a <= block.grow_max)
    sides = sorted(per_side)
    for combo in itertools.product(*(per_side[s] for s in sides)):
        g = [0, 0, 0, 0]
        for s, a in zip(sides, combo, strict=False):
            g[_SIDE_GROW[s]] = a
        yield tuple(g)


def _reach_amount(block: Block, side: str, far: tuple[int, int]) -> int:
    """How far this side must grow for its wall to sit ``MIN_PIPE`` from ``far``."""
    fx, fy = far
    ref = getattr(block, "_ref", (0, 0))
    px, py = ref
    if side == "N":
        return py - (fy + MIN_PIPE)
    if side == "S":
        return (fy - MIN_PIPE) - (py + block.h - 1)
    if side == "W":
        return px - (fx + MIN_PIPE)
    return (fx - MIN_PIPE) - (px + block.w - 1)


def room_candidates(
    fld: Field,
    start: tuple[int, int],
    goal: tuple[int, int],
    limit: int = 40,
) -> list[tuple[int, int, int, int]]:
    """Rectangles a forwarder could occupy between two touch cells.

    Coordinate-compressed: the interesting edges are the two endpoints and every
    block's own edges, because a room that pays is a room that *spans* something.
    """
    # The endpoints +/- MIN_PIPE are the interesting edges: a room placed there
    # takes the 2-cell stub, which is the floor (ARCH §7.4b).
    xs = {start[0] + d for d in (-2, -1, 0, 1, 2)} | {goal[0] + d for d in (-2, -1, 0, 1, 2)}
    ys = {start[1] + d for d in (-2, -1, 0, 1, 2)} | {goal[1] + d for d in (-2, -1, 0, 1, 2)}
    for x0, y0, w, h in fld.rects:
        xs |= {x0 - 2, x0, x0 + w - 1, x0 + w + 1}
        ys |= {y0 - 2, y0, y0 + h - 1, y0 + h + 1}
    lo_x, hi_x = min(start[0], goal[0]), max(start[0], goal[0])
    lo_y, hi_y = min(start[1], goal[1]), max(start[1], goal[1])
    xs = {x for x in xs if lo_x - 4 <= x <= hi_x + 4}
    ys = {y for y in ys if lo_y - 4 <= y <= hi_y + 4}
    sx, sy = sorted(xs), sorted(ys)
    # A blocked-cell prefix sum over the window, so "is this rectangle empty?" is
    # four lookups rather than an area scan.
    x_lo, x_hi, y_lo, y_hi = sx[0], sx[-1], sy[0], sy[-1]
    wgrid, hgrid = x_hi - x_lo + 2, y_hi - y_lo + 2
    pre = [[0] * wgrid for _ in range(hgrid)]
    for j in range(1, hgrid):
        rowp, prevp = pre[j], pre[j - 1]
        for i in range(1, wgrid):
            hit = 0 if fld.free((x_lo + i - 1, y_lo + j - 1)) else 1
            rowp[i] = hit + rowp[i - 1] + prevp[i] - prevp[i - 1]

    def empty(x0: int, y0: int, x1: int, y1: int) -> bool:
        a, b = x0 - x_lo, y0 - y_lo
        c, d = x1 - x_lo + 1, y1 - y_lo + 1
        return pre[d][c] - pre[b][c] - pre[d][a] + pre[b][a] == 0

    out: list[tuple[int, int, int, int]] = []
    for x0, x1 in itertools.combinations(sx, 2):
        if x1 - x0 + 1 < 3:
            continue
        for y0, y1 in itertools.combinations(sy, 2):
            if y1 - y0 + 1 < 3 or not empty(x0, y0, x1, y1):
                continue
            out.append((x0, y0, x1 - x0 + 1, y1 - y0 + 1))
    # Biggest first: a room that spans the corridor is the one that pays.
    out.sort(key=lambda r: -(r[2] * r[3]))
    return out[:limit]


def _mid(rect: tuple[int, int, int, int], side: str) -> tuple[int, int]:
    x0, y0, w, h = rect
    return {
        "N": (x0 + w // 2, y0 - 1),
        "S": (x0 + w // 2, y0 + h),
        "E": (x0 + w, y0 + h // 2),
        "W": (x0 - 1, y0 + h // 2),
    }[side]


def _room_plan(
    rect: tuple[int, int, int, int],
    start: tuple[int, int],
    goal: tuple[int, int],
) -> tuple[float, list[tuple[str, str]]]:
    """A lower bound on this room's charged cells, and the side pairs worth trying.

    The bound is Manhattan-to-the-nearest-attachment on each side plus the
    forwarder floor, and it is admissible, so sorting by it and stopping at the
    floor is exact rather than merely quick.
    """
    din = sorted(SIDES, key=lambda s: abs(_mid(rect, s)[0] - start[0])
                 + abs(_mid(rect, s)[1] - start[1]))
    dout = sorted(SIDES, key=lambda s: abs(_mid(rect, s)[0] - goal[0])
                  + abs(_mid(rect, s)[1] - goal[1]))
    a = _mid(rect, din[0])
    b = _mid(rect, dout[0])
    bound = (
        max(MIN_PIPE, abs(a[0] - start[0]) + abs(a[1] - start[1]) + 1)
        + max(MIN_PIPE, abs(b[0] - goal[0]) + abs(b[1] - goal[1]) + 1)
        + FORWARDER_CELLS
    )
    pairs = [(i, o) for i in din[:2] for o in dout[:2] if i != o]
    return bound, pairs


# ── the loop ─────────────────────────────────────────────────────────────────
def solve(problem: Problem, *, verbose: bool = False, room_limit: int = 40) -> Report:
    rep = Report()
    blocks = list(problem.blocks)
    _BLOCKS.clear()
    _BLOCKS.update({b.name: b for b in blocks})
    port_refs = [ref for p in problem.pipes for ref in (p.src, p.dst)]
    free_choices: dict[tuple[str, str], tuple[int, ...]] = {}
    for ref in port_refs:
        b = problem.block(ref[0])
        free_choices[ref] = free_offsets(b, b.port(ref[1]))

    pos_options = [
        [(b.name, (x, y)) for x in b.xs for y in b.ys] for b in blocks
    ]
    off_keys = sorted(free_choices)
    off_options = [free_choices[k] for k in off_keys]

    for pos in itertools.product(*pos_options):
        base = dict(pos)
        for b in blocks:
            b._ref = base[b.name]  # noqa: SLF001 - used by _reach_amount
        for offs in itertools.product(*off_options):
            offsets = dict(zip(off_keys, offs, strict=False))
            touches0 = {}
            for ref in off_keys:
                b = problem.block(ref[0])
                pl0 = Placed(b, *base[b.name], (0, 0, 0, 0))
                touches0[ref] = pl0.touch(b.port(ref[1]), offsets[ref])
            grow_options = [list(_growths(b, problem, touches0, base)) for b in blocks]
            for grows in itertools.product(*grow_options):
                rep.candidates += 1
                placed = {
                    b.name: Placed(b, *base[b.name], g)
                    for b, g in zip(blocks, grows, strict=False)
                }
                layout = Layout(problem, placed, offsets)
                if not layout.in_bounds():
                    rep.rejected_bounds += 1
                    continue
                if layout.overlaps():
                    rep.rejected_overlap += 1
                    continue
                try:
                    check_layout(layout)
                except MachineError:
                    rep.rejected_by_bindings += 1
                    continue
                except Unexpressible:
                    raise
                sol = _route_all(problem, layout, rep, room_limit=room_limit)
                if sol is None:
                    continue
                rep.feasible += 1
                cost = sol.weighted_cells
                rep.samples.append((cost, sum(r.drawn for r in sol.routes.values())))
                if cost < rep.best_cost - 1e-9:
                    rep.best_cost, rep.best = cost, sol
    if rep.best is not None:
        rep.segment_warnings = audit_segments(
            _layout_of(rep.best), rep.best.routes
        )
    if verbose:
        print(rep.summary())
    return rep


def _layout_of(sol: Solution) -> Layout:
    placed = {}
    for name, (px, py) in sol.placement.items():
        b = sol.problem.block(name)
        g = tuple(sol.growth.get((name, s), 0) for s in ("N", "S", "E", "W"))
        placed[name] = Placed(b, px, py, g)
    return Layout(sol.problem, placed, sol.offsets)


def _route_all(
    problem: Problem, layout: Layout, rep: Report, *, room_limit: int
) -> Solution | None:
    """Route heaviest-first; try each leg as a pipe and, if allowed, as a room."""
    rects = [pl.rect for pl in layout.placed.values()]
    taken: set[tuple[int, int]] = set()
    routes: dict[str, Route] = {}
    order = sorted(problem.pipes, key=lambda p: -p.weight)
    for pipe in order:
        fld = Field(problem.bounds, rects, taken)
        s, sd = layout.touch(pipe.src), layout.heading(pipe.src)
        g = layout.touch(pipe.dst)
        gd = tuple(-c for c in layout.heading(pipe.dst))
        best: Route | None = None
        try:
            cells = route(fld, s, sd, g, gd, min_length=pipe.min_length)
            best = Route(pipe.name, (Leg(cells),))
        except NoRoute:
            pass
        # A forwarder can never cost less than two 2-cell stubs plus its own
        # re-serialisation floor, so a leg already at or under that bound cannot be
        # improved by one and the whole room search is skipped.  That bound is what
        # keeps an exhaustive search affordable on a 250-row corridor.
        floor = 2 * MIN_PIPE + FORWARDER_CELLS
        if (
            pipe.allow_room
            and pipe.min_length <= MIN_PIPE
            and (best is None or best.cells > floor)
        ):
            plans = [
                (*_room_plan(rect, s, g), rect)
                for rect in room_candidates(fld, s, g, limit=room_limit)
            ]
            for bound, pairs, rect in sorted(plans, key=lambda p: p[0]):
                if best is not None and bound >= best.cells:
                    break  # the bound is admissible, so everything after is worse
                for in_side, out_side in pairs:
                    try:
                        a, b = room_route(
                            fld, s, sd, g, gd, rect, in_side, out_side,
                            node_cap=40_000,
                            budget=None if best is None
                            else int(best.cells - FORWARDER_CELLS),
                        )
                    except NoRoute:
                        continue
                    cand = Route(pipe.name, (Leg(a), Leg(b)), room=rect)
                    if best is None or cand.cells < best.cells:
                        best = cand
                if best is not None and best.cells <= floor + 1e-9:
                    break
        if best is None:
            rep.rejected_no_route += 1
            return None
        routes[pipe.name] = best
        for leg in best.legs:
            taken |= set(leg.cells)
        if best.room is not None:
            rects = [*rects, best.room]
    sol = Solution(
        placement={n: (pl.px, pl.py) for n, pl in layout.placed.items()},
        growth={
            (n, s): pl.grow[_SIDE_GROW[s]]
            for n, pl in layout.placed.items()
            for s in ("N", "S", "E", "W")
        },
        offsets=dict(layout.offsets),
        routes=routes,
        problem=problem,
    )
    try:
        render(layout, routes)
    except MachineError:
        rep.rejected_collision += 1
        return None
    return sol


__all__ = ["Report", "corners", "render", "room_candidates", "solve"]
