"""Is a grid's corridor bill set by its constraints, or just by lack of space?

Two numbers, and the second is the one that matters:

``free-direction floor``  shortest path per edge with entry/exit directions free
                          and *no other corridor in the way*.  Optimistic.
``replanned from empty``  every corridor re-laid hottest-first with the
                          directions left exactly as they are.

If the replan is not cheaper than the grid already is, the layout is already
near-optimal for the space and no constraint you relax will pay — the grid is
space-limited.  That check would have saved a day's work on snake, where the
floor promised 15.5% and rotation delivered 0.46%.  See littleman/REROUTING.md.

    uv run python scratch/corridor_bound.py GRID.man SLUG [TICK_CAP]
"""
from __future__ import annotations

import pathlib
import sys
from collections import deque

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.fast_littleman import DIRS, FastLittleman, _add  # noqa: E402
from randomfun2026solvers.manflow import build_flow_graph  # noqa: E402
from randomfun2026solvers.manprofile import profile_program  # noqa: E402
from randomfun2026solvers.manreroute import Layout, route_edge  # noqa: E402


def main() -> None:
    path, slug = sys.argv[1], sys.argv[2]
    cap = int(sys.argv[3]) if len(sys.argv) > 3 else 5_000_000

    prog = FastLittleman(path)
    graph = build_flow_graph(prog)
    profile = profile_program(prog, slug, graph=graph, tick_cap=cap)
    if profile.mismatches:
        sys.exit(f"cannot profile: {profile.mismatches[0]}")
    layout = Layout(graph)
    lead = profile.bottleneck_men()[0]

    def bfs(start, target, blocked):
        seen = {start: 0}
        queue = deque([start])
        while queue:
            cell = queue.popleft()
            distance = seen[cell]
            for direction in DIRS:
                nxt = _add(cell, direction)
                if nxt == target:
                    return distance
                if nxt in blocked or nxt in seen:
                    continue
                if nxt not in layout.paveable and nxt not in layout.use:
                    continue
                seen[nxt] = distance + 1
                queue.append(nxt)
        return None

    rows = []
    for edge in graph.edges:
        if edge.dst is None or edge.length == 0:
            continue
        traffic = profile.edge_traffic(edge.id)
        if not traffic:
            continue
        src = graph.nodes[edge.src]
        start = src.literal_run[-1] if src.literal_run else src.pos
        floor = bfs(start, graph.nodes[edge.dst].pos, layout.pinned)
        rows.append((traffic, edge.length, edge.length if floor is None else floor,
                     profile.edge_men.get(edge.id, set())))

    order = [e for e, _ in profile.hot_edges() if graph.edges[e].dst is not None]
    rest = [e.id for e in graph.edges if e.id not in set(order) and e.dst is not None]
    empty = Layout(graph, empty=True)
    for edge_id in order + rest:
        edge = graph.edges[edge_id]
        found = route_edge(empty, edge, limit=edge.length + 40)
        empty.place(edge_id, found if found is not None else edge.cells)

    now = sum(t * length for t, length, _, _ in rows)
    floor = sum(t * f for t, _, f, _ in rows)
    replan = sum(len(c) * profile.edge_traffic(e) for e, c in empty.paths.items())
    lead_now = sum(t * length for t, length, _, men in rows if lead in men)
    lead_floor = sum(t * f for t, _, f, men in rows if lead in men)

    print(f"{slug}: {profile.wall_ticks:,} wall ticks; man {lead} paces at "
          f"{100 * profile.men[lead].corridor / profile.wall_ticks:.1f}% corridor")
    print(f"  corridor now                    {now:>10,} man-ticks")
    print(f"  free-direction floor            {floor:>10,}  "
          f"({100 * (now - floor) / now:.1f}% — optimistic, ignores congestion)")
    print(f"  pacing man's share of that      {lead_now - lead_floor:>10,}")
    print(f"  replanned from empty, same dirs {replan:>10,}  "
          f"({100 * (now - replan) / now:+.1f}%)")
    if replan >= now:
        print("\n  -> space-limited: the layout is already near-optimal for the room.")
        print("     Relaxing directions or placement will not pay; look at footprint.")
    else:
        print("\n  -> routing-limited: a better plan exists within the current rules.")


if __name__ == "__main__":
    main()
