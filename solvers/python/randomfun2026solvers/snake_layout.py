#!/usr/bin/env python3
"""Compile :data:`snake_ring.WORKER` into one little-man room, and wire the machine.

`snake` is scored `max(w,h)^2 x avg_ticks`, and the submitted LM-1 CPU spends
99.8% of its critical path on fetch, decode and the return walk — `cpu:lane:SND`,
the instruction that actually commands the game, was 0.18% of it. So this drops
the instruction stream entirely: the worker's control-flow graph *is* the room.

`lllm_layout.plan_blocks` already turns a token CFG into glyph rows; what is
snake-specific is the **plumbing** — one incoming pipe (`ri`), one ring pair
(`rr`/`sr`), one painter pipe (`sp`) — and the fact that snake is footprint
sensitive where `lllm` was not, so the row bands and the router here are tighter
than `lllm_layout.build_room`'s.

## Bands

Four pipes stand on the room's north wall, so distance from any interior cell is
`|x - c| + y + 1`: the `y` term is common and "nearest pipe" is "nearest column"
on every row.  With one ring and one I/O pair that is a single split:

    ring_out ring_in   ....... RING band ....... | ... IO band ...  input painter

`r` binds `ring_in` west of the midpoint and `input` east of it; `s` binds
`ring_out` west and `painter` east.  Both midpoints are placed on the same
column, so one gap in the middle serves both and a block only pays a row when it
crosses between the ring and the outside world.

## Rows

A block owns a horizontal run walked east, wrapping west when a band lies behind
the pen.  Its exits leave west into a bank of vertical channels and re-enter
every target at the same cell, `(NCH, first glyph row)`.  Three savings over
`lllm_layout`, which spent rows freely because its field was weak:

* a run whose **last row already walks west** needs no turnaround row — the man
  just keeps going west into the channels;
* an `x` branch has no straight lane, so it needs no straight row;
* the north and south lanes of a branch are the rows either side of the branch
  glyph, and a west-walking branch puts its straight lane on the glyph row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.lllm_layout import (
    Geometry,
    Plan,
    _straight_key,
    _turn_keys,
    block_order,
    plan_blocks,
)
from randomfun2026solvers.snake_ring import WORKER

if TYPE_CHECKING:  # pragma: no cover
    from randomfun2026solvers.man_debug import DebugMap

__all__ = ["WORKER_L", "Room", "build_grid", "build_room", "geometry"]

#: The worker plus the one block its CFG names but does not define.  `DEAD_DONE`
#: hands off to `HALT` once the death frame is committed; the case is over the
#: moment that frame lands, so all `HALT` has to do is never send anything again.
#: A bare `ri` self-loop does that and blocks the moment the input runs dry.
WORKER_L: dict[str, tuple[list[str], dict[str, str] | str]] = {
    **WORKER,
    "HALT": (["ri"], "HALT"),
}

TOKEN_ZONE = {"rr": "RING", "sr": "RING", "ri": "IO", "sp": "IO"}


def geometry(nch: int, code_w: int = 34) -> Geometry:
    """Bands and pipe columns for a room with `nch` channel columns.

    `nch` is searched over by :func:`build_room`, and every column here is
    relative to `code0`, so the whole room slides with it.
    """
    code0 = nch + 1
    ring_out, ring_in = code0, code0 + 1
    inp, paint = code0 + code_w - 12, code0 + code_w - 11
    mid = (ring_in + inp) // 2          # == (ring_out + paint) // 2
    return Geometry(
        token_zone=TOKEN_ZONE,
        zone_cols={"RING": (code0, mid - 1), "IO": (mid + 2, code0 + code_w - 2)},
        pipe_in={"RING": ring_in, "IO": inp},
        pipe_out={"RING": ring_out, "IO": paint},
        code0=code0,
        iw=code0 + code_w,
    )


# ── the room ──────────────────────────────────────────────────────────────────
@dataclass
class Room:
    circuit: Circuit
    geo: Geometry
    nch: int
    order: list[str]
    plans: dict[str, Plan]
    glyph_ys: dict[str, list[int]]
    lane_ys: dict[str, dict[str, int]]
    channels: int
    edges: list[tuple[str, str, str, int, int]] = field(default_factory=list)


@dataclass
class _Band:
    """Where one block's rows and lane rows ended up."""

    ys: list[int]                 # glyph rows, in walking order
    lanes: dict[str, int]         # "straight" | "cw" | "ccw" -> lane row


def _lanes_of(worker, name: str, plan: Plan) -> list[tuple[str, str]]:
    """(lane kind, target) for every edge leaving a block."""
    succ = worker[name][1]
    if isinstance(succ, str):
        return [("straight", succ)]
    out: list[tuple[str, str]] = []
    key = _straight_key(plan.branch)
    if key is not None:
        out.append(("straight", succ[key]))
    for lane, turn in _turn_keys(plan.branch).items():
        out.append((turn, succ[lane]))
    return out


def _twin_lane(worker, name: str, plan: Plan) -> str | None:
    """The turn lane a branch's straight lane may share a corridor with.

    `X` over a body value asks "is this the END sentinel", and both answers that
    are not the sentinel go to the same block — eight of snake's seventeen
    branches are that shape.  Two lanes with one target need one corridor.
    """
    succ = worker[name][1]
    key = _straight_key(plan.branch)
    if not isinstance(succ, dict) or key is None:
        return None
    for lane, turn in _turn_keys(plan.branch).items():
        if succ[lane] == succ[key]:
            return turn
    return None


def _bands(worker, order: list[str], plans: dict[str, Plan]) -> tuple[dict[str, _Band], int]:
    """Assign every block its glyph rows and its lane rows, top to bottom."""
    bands: dict[str, _Band] = {}
    y = 0
    for name in order:
        p = plans[name]
        kinds = {k for k, _ in _lanes_of(worker, name, p)}
        east = p.rows[-1].east
        if p.branch:
            ys = list(range(y, y + len(p.rows) - 1))
            y += len(p.rows) - 1
            north, y = y, y + 1                     # free row above the branch
            ys.append(y)
            last, y = y, y + 1
            south, y = y, y + 1                     # free row below the branch
            # `X` entered heading east turns clockwise (south) on positive and
            # counter-clockwise (north) on negative; entered west, the two swap.
            lanes = {"cw": south if east else north, "ccw": north if east else south}
            if "straight" in kinds:
                if not east:
                    lanes["straight"] = last       # keeps walking west, for free
                elif (twin := _twin_lane(worker, name, p)) is not None:
                    # `pos` and `zero` of a body loop are the same block: one
                    # corridor can carry both, so the straight lane drops onto
                    # the turn's row instead of buying a row of its own.
                    lanes["straight"] = lanes[twin]
                else:
                    lanes["straight"], y = y, y + 1
        else:
            ys = list(range(y, y + len(p.rows)))
            y += len(p.rows)
            if east:
                lanes = {"straight": y}
                y += 1
            else:
                lanes = {"straight": ys[-1]}
        bands[name] = _Band(ys, lanes)
    return bands, y


# ── block order ───────────────────────────────────────────────────────────────
#
# A corridor's *length is a tick cost*, paid every time the edge is taken, and
# with 44 blocks stacked one to a band the vertical leg is most of it — a walk
# from `M_CMP` to `M_KEEP` cost 23 rows each way under depth-first order, twice
# per body cell.  So the order is chosen by descending on the total corridor
# length rather than by walking the graph.
#
# The objective is **unweighted**: every CFG edge counts once.  A profile taken
# from the public cases scores better on paper and measured *worse* on the
# engine (17.7k ticks against 15.8k), because weighting collapses the cold death
# paths onto the hot loop and lengthens the corridors that carry it.
#: The descent plateaus long before 20,000 moves and then sticks, so the search
#: is restarted rather than lengthened; a fixed seed list keeps it reproducible.
ORDER_STEPS = 20_000
ORDER_SEEDS = (1, 5, 11, 23, 42, 99)


def _metrics(worker, plans: dict[str, Plan]) -> dict[str, tuple[int, int, dict[str, int]]]:
    """Per block: how many rows it needs, and where its rows sit inside them."""
    out: dict[str, tuple[int, int, dict[str, int]]] = {}
    for name, p in plans.items():
        band, height = _bands(worker, [name], {name: p})
        out[name] = (height, band[name].ys[0], band[name].lanes)
    return out


def tuned_order(worker, plans: dict[str, Plan], base: list[str]) -> list[str]:
    """Shuffle blocks, keeping the entry first, to shorten every corridor."""
    import random

    metric = _metrics(worker, plans)
    lanes = {n: [(metric[n][2][k], t) for k, t in _lanes_of(worker, n, plans[n])]
             for n in base}

    def cost(order: list[str]) -> int:
        y, top = {}, 0
        for name in order:
            y[name] = top
            top += metric[name][0]
        return sum(abs(y[t] + metric[t][1] - y[n] - off)
                   for n in order for off, t in lanes[n])

    best, best_c = list(base), cost(base)
    for seed in ORDER_SEEDS:
        rng = random.Random(seed)
        cur = list(base)
        cur_c = cost(cur)
        for _ in range(ORDER_STEPS):
            i, j = rng.randrange(1, len(cur)), rng.randrange(1, len(cur))
            if i == j:
                continue
            cand = list(cur)
            cand.insert(j, cand.pop(i))
            c = cost(cand)
            if c <= cur_c:                 # plateau moves keep it from sticking
                cur, cur_c = cand, c
                if c < best_c:
                    best, best_c = list(cand), c
    return best


def _exit_col(plan: Plan, kind: str) -> int:
    """The column a lane leaves the code from."""
    if kind == "straight":
        return plan.rows[-1].end if not plan.branch or plan.rows[-1].east else plan.branch_col
    return plan.branch_col


class _Claims:
    """Which cells a routed man walks over, and in which direction.

    Corridors may cross at a blank but **never at a turn glyph**, and that is not
    a property of the cells at the moment a corridor is drawn: a later edge that
    drops its `v` inside an earlier edge's *upward* vertical hijacks the earlier
    man, and the grid still loads.  Directions are tracked rather than mere
    occupancy because a same-direction glyph is harmless — a man walking south
    over a `v` keeps walking south — and that relaxation is most of the bank's
    capacity.
    """

    def __init__(self) -> None:
        self.down: set[tuple[int, int]] = set()
        self.up: set[tuple[int, int]] = set()
        self.flat: set[tuple[int, int]] = set()

    def spine(self, cells, step: int) -> None:
        (self.down if step > 0 else self.up).update(cells)

    def may_turn(self, cell, glyph: str) -> bool:
        if glyph == "v":
            return cell not in self.up and cell not in self.flat
        if glyph == "^":
            return cell not in self.down and cell not in self.flat
        return cell not in self.down and cell not in self.up   # ">"


def _route(c: Circuit, nch: int, edges, glyph_ys, claims: _Claims) -> int:
    """Give every routed edge a channel column, checking real cells as we go."""
    used = 0
    for src, dst, kind, lane_row, start in edges:
        target_y = glyph_ys[dst][0]
        step = 1 if target_y > lane_row else -1
        drop = "v" if step > 0 else "^"
        # East first: a man pays `(start - ch) + (nch - ch)` cells to get out of
        # the code and back in again, so the *closest* channel to the code is the
        # cheapest one, and the walk is what the tick count is made of.
        for ch in reversed(range(nch)):
            leg = [(x, lane_row) for x in range(ch + 1, start)]
            spine = [(ch, yy) for yy in range(lane_row + step, target_y, step)]
            run = [(x, target_y) for x in range(ch + 1, nch)]
            if any(not c.free(x, y) for x, y in leg):
                continue
            if any(not (c.free(x, y) or c.get(x, y) == drop) for x, y in spine):
                continue
            if any(not (c.free(x, y) or c.get(x, y) == ">") for x, y in run):
                continue
            if not (c.free(ch, lane_row) and claims.may_turn((ch, lane_row), drop)):
                continue
            if not (c.free(ch, target_y) or c.get(ch, target_y) == ">"):
                continue
            if not claims.may_turn((ch, target_y), ">"):
                continue
            c.set(ch, lane_row, drop)
            c.set(ch, target_y, ">")
            claims.spine(spine + [(ch, lane_row), (ch, target_y)], step)
            claims.flat.update(leg + run)
            used = max(used, ch + 1)
            break
        else:
            raise Collision(f"no channel free for {src} -{kind}-> {dst}")
    return used


def build_room(worker=WORKER_L, code_w: int = 34, entry: str = "INIT",
               order: list[str] | None = None) -> Room:
    """Lay every block out, then route every edge; widen the bank until it fits."""
    last: Exception | None = None
    chosen = order
    for nch in range(4, 25):
        geo = geometry(nch, code_w)
        if chosen is None:
            base = block_order(worker, entry)
            chosen = tuned_order(worker, plan_blocks(base, worker, geo), base)
        order = chosen
        plans = plan_blocks(order, worker, geo)
        bands, ih = _bands(worker, order, plans)
        glyph_ys = {n: b.ys for n, b in bands.items()}

        c = Circuit(geo.iw, ih)
        for name in order:
            p, b = plans[name], bands[name]
            c.set(nch, b.ys[0], "@" if name == order[0] else ">")
            for i, row in enumerate(p.rows):
                for col, glyph in row.cells:
                    c.set(col, b.ys[i], glyph)
                if i + 1 < len(p.rows):              # wrap link, straight down
                    c.set(row.end, b.ys[i], "v")
                    c.set(row.end, b.ys[i + 1], ">" if p.rows[i + 1].east else "<")

        edges: list[tuple[str, str, str, int, int]] = []
        claims = _Claims()
        for name in order:
            p, b = plans[name], bands[name]
            corridors: dict[int, tuple[str, str, int]] = {}
            for kind, target in _lanes_of(worker, name, p):
                row, start = b.lanes[kind], _exit_col(p, kind)
                if kind != "straight":               # turn out of the branch
                    c.set(p.branch_col, row, "<")
                    start = p.branch_col
                elif row != b.ys[-1]:                # drop onto the turnaround row
                    c.set(start, b.ys[-1], "v")
                    c.set(start, row, "<")
                    # the drop crosses whatever rows lie between; keep it clear
                    claims.spine([(start, yy) for yy in range(b.ys[-1], row + 1)], 1)
                # two lanes sharing a row share a target, hence one corridor,
                # which starts at whichever of their turns stands furthest west
                had = corridors.get(row)
                if had is not None and had[1] != target:
                    raise Collision(f"{name}: lanes on row {row} disagree on target")
                corridors[row] = (kind if had is None else had[0], target,
                                  start if had is None else min(had[2], start))
            edges += [(name, t, k, row, s) for row, (k, t, s) in corridors.items()]

        # Longest hops first: they are the ones a crowded bank cannot place.
        edges.sort(key=lambda e: -abs(glyph_ys[e[1]][0] - e[3]))
        try:
            used = _route(c, nch, edges, glyph_ys, claims)
        except Collision as exc:
            last = exc
            continue
        lane_ys = {n: b.lanes for n, b in bands.items()}
        return Room(c, geo, nch, order, plans, glyph_ys, lane_ys, used, edges)
    raise Collision(f"routing never fit: {last}")


# ── the whole machine ─────────────────────────────────────────────────────────
#
# Only two comparisons can go wrong, and they are the reason the plumbing looks
# the way it does.  `s` ranges over the room's *outgoing* pipes and `r` over its
# *incoming* ones, so `sp`-versus-`sr` never involves the input pipe and
# `ri`-versus-`rr` never involves the painter's.  What is left is:
#
#   * the ring pipes must stay west, hugging the RING band's columns — hence the
#     relay directly above them and no leg that reaches east;
#   * the input and painter pipes must stay east, which they do by leaving the
#     room's north wall on a riser and only ever travelling *east* from it.
#
# `audit_bindings` then checks the finished grid rather than the intention.
BAND_H = 9           # rows above the worker: the relay, and the risers
WX, WY = 1, BAND_H + 1
RELAY_X, RELAY_Y = 4, 0
RING_MIN = 14        # the ring holds 12 words at rest; leave real slack


def build_grid(code_w: int = 34, order: list[str] | None = None) -> tuple[list[str], DebugMap, dict[str, object]]:
    """Worker + ring + input + the proven painter/LM-75 harness, as one grid."""
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.plotter_block import pipe
    from randomfun2026solvers.snake_ring import (
        FLAT_RELAY,
        HARNESS_H,
        HARNESS_W,
        PAINTER_IW,
        PROBE_PAINTER,
        stamp_harness,
    )
    from randomfun2026solvers.value_ring import stamp, walls

    room = build_room(code_w=code_w, order=order)
    geo, iw, ih = room.geo, room.geo.iw, room.circuit.h
    col = {k: WX + v for k, v in
           {**geo.pipe_in, **{f"out_{k}": v for k, v in geo.pipe_out.items()}}.items()}
    ring_in, inp = col["RING"], col["IO"]
    ring_out, painter_col = col["out_RING"], col["out_IO"]

    hx = WX + iw + 3                       # two clear columns for the feed pipe
    g = Circuit(hx + HARNESS_W, WY + ih + 1)

    for y, line in enumerate(room.circuit.rows()):
        for x, ch in enumerate(line):
            if ch != " ":
                g.set(WX + x, WY + y, ch)
    walls(g, WX, WY, iw, ih)
    north = WY - 1                          # the worker's north wall row

    # ── the ring: relay above the RING band, both pipes kept west ─────────────
    # the relay sits directly over the ring columns, so neither of its pipes has
    # a leg that reaches east into the IO band and steals an `sp`
    relay_x = ring_out - 2
    stamp(g, relay_x, RELAY_Y, FLAT_RELAY)
    relay_s = RELAY_Y + 3                   # the relay's south wall row
    feed_y = PROBE_PAINTER[1] + 1           # the painter's middle interior row
    legs = {
        "out_RING": [(ring_out, north - 1), (ring_out, relay_s + 1)],
        # A pipe's first cell must point *away* from its room or it parses as a
        # loose pipe the relay's `s` cannot see, and the machine still loads.
        # ...and its eastward reach is bounded by the IO band: a return leg that
        # got any closer would be the nearest incoming pipe to `ri` in INIT.
        "in_RING": [(ring_in + 5, relay_s + 1), (ring_in + 5, relay_s + 2),
                    (ring_in, relay_s + 2), (ring_in, north - 1)],
        "in_IO": [(inp, 3), (inp, north - 1)],
        "out_IO": [(painter_col, north - 1), (painter_col, relay_s + 1),
                   (hx, relay_s + 1), (hx, feed_y)],
    }
    pipes = {k: _cells(v) for k, v in legs.items()}
    fwd = pipe(g, legs["out_RING"], into=(ring_out, relay_s))
    ret = pipe(g, legs["in_RING"], into=(ring_in, north))
    if fwd + ret < RING_MIN:
        raise Collision(f"ring holds {fwd + ret} words, wanted {RING_MIN}")

    # ── input: a 3x3 room sitting on its own riser, so the pipe is one column ─
    stamp(g, inp - 1, 0, ["+-+", "|I|", "+-+"])
    pipe(g, legs["in_IO"], into=(inp, north))

    # ── the painter feed, and the harness it drives ──────────────────────────
    stamp_harness(g, hx, 0)
    pipe(g, legs["out_IO"], into=(hx + PROBE_PAINTER[0] - 1, feed_y))

    rows = [r.rstrip() for r in g.rows()]
    while rows and not rows[-1]:
        rows.pop()

    audit_bindings(room, pipes)

    # ── the sidecar ──────────────────────────────────────────────────────────
    d = DebugMap("snake — a dataflow ring machine, no CPU")
    d.region("harness", hx + 1, 0, HARNESS_W - 1, HARNESS_H,
             note="painter + LM-75, engine-verified on all 129 public frames",
             color="#a855f7")
    d.region("relay", relay_x, RELAY_Y, 12, 4, color="#0ea5e9",
             note=f"turnaround of the {fwd + ret}-cell body ring")
    d.region("input", inp - 1, 0, 3, 3, color="#64748b",
             note="one round of `sx sy` / `1 fx fy` / `2-5` / `0` at a time")
    d.region("channels", WX, WY, room.nch, ih, color="#94a3b8",
             note=f"{room.channels} vertical corridors carrying every routed edge")
    for band, (lo, hi) in geo.zone_cols.items():
        d.region(f"band:{band}", WX + lo, WY, hi - lo + 1, ih, color="#1f2937",
                 note=f"{band} pipe ops stand here; nearest column binds")
    for name in room.order:
        ys = room.glyph_ys[name]
        d.region(f"block:{name}", WX + geo.code0, WY + ys[0], iw - geo.code0,
                 ys[-1] - ys[0] + 1, note=" ".join(WORKER_L[name][0]),
                 color="#f59e0b", tags=["block"])

    info = {
        "worker": (iw, ih),
        "grid": (max(len(r) for r in rows), len(rows)),
        "channels": room.channels,
        "ring": fwd + ret,
        "blocks": len(room.order),
        "glyph_cells": sum(len(r.cells) for p in room.plans.values() for r in p.rows),
    }
    return rows, d, info


# ── the binding audit ─────────────────────────────────────────────────────────
def _cells(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """A rectilinear polyline as the cells it occupies, in flow order."""
    out = [points[0]]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):  # noqa: B905 - a polyline
        sx, sy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x, y = x + sx, y + sy
            out.append((x, y))
    return out


def audit_bindings(room: Room, pipes: dict[str, list[tuple[int, int]]]) -> None:
    """Every `r`/`s` in the worker must reach the pipe its band promised it.

    Measured over *every* cell of each pipe, which is stricter than the spec's
    "segment attached to the room" — if this passes, no reading of "nearest"
    can make the machine bind the wrong pipe.  `s` ranges over outgoing pipes
    only and `r` over incoming ones, so the two comparisons are independent.
    """
    for name in room.order:
        plan, ys = room.plans[name], room.glyph_ys[name]
        pipe_toks = [t for t in WORKER_L[name][0] if t in TOKEN_ZONE]
        placed = [(c, ys[i]) for i, r in enumerate(plan.rows)
                  for c, gl in r.cells if gl in ("r", "s")]
        if len(placed) != len(pipe_toks):
            raise Collision(f"{name}: {len(placed)} r/s glyphs, {len(pipe_toks)} tokens")
        for (cx, cy), tok in zip(placed, pipe_toks, strict=True):
            side = "in" if tok[0] == "r" else "out"
            gx, gy = WX + cx, WY + cy
            best = min(
                ("RING", "IO"),
                key=lambda z: min(abs(gx - px) + abs(gy - py)
                                  for px, py in pipes[f"{side}_{z}"]),
            )
            if best != TOKEN_ZONE[tok]:
                raise Collision(
                    f"{name}: {tok!r} at ({gx},{gy}) reaches {best}, not "
                    f"{TOKEN_ZONE[tok]}")


if __name__ == "__main__":  # pragma: no cover - the generator's CLI
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write a labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    ap.add_argument("--code-w", type=int, default=34, help="code columns per row")
    args = ap.parse_args()
    grid, dbg, meta = build_grid(args.code_w)
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
