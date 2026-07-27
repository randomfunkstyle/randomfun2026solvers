"""Let each instruction be approached from whichever side is nearest.

:mod:`manreroute` keeps every node's *entry direction* exactly as it found it,
so a corridor has to bend around and come in the one way the man originally
arrived.  Measured on snake, that costs 15.5% of the pacing man's corridor — and
almost all of it on short corridors walked hundreds of times, not on the long
ones.

Freeing the direction is sound because of the same property that makes the whole
approach work: an instruction's effect does not depend on the direction it is
walked.  Two glyphs are exceptions and stay pinned here — a numeric literal
reads backwards when walked backwards, and ``U`` turns to an absolute side of the
room.  A conditional turn is *not* an exception: its arms are relative, so
``-1/0/+1`` keep their meaning and simply point elsewhere on the compass.

Node cells never move, so ``s``/``r`` nearest-pipe binding is untouched.  That is
what makes rotating much cheaper to get right than re-placing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Sequence

from . import scoring
from .fast_littleman import DIRS, Cell, Dir, FastLittleman
from .manflow import (
    Edge,
    FlowGraph,
    Node,
    NodeKind,
    build_flow_graph,
    canonical_signature,
    exits_for,
)
from .manprofile import Profile, profile_program
from .manreroute import Layout, route_edge

__all__ = [
    "RotationMove",
    "RotationResult",
    "apply_moves",
    "apply_rotation",
    "initial_rotation",
    "propose_rotations",
    "rotatable_nodes",
    "search",
]

#: A generous ceiling so a single awkward corridor cannot wander the whole room.
DETOUR_SLACK = 24


def rotatable_nodes(graph: FlowGraph) -> set[int]:
    """Nodes whose entry direction is free to change.

    Pinned: the men's start cells (a man always begins facing right), numeric
    literals (the value depends on the direction), ``U`` (it turns to an
    absolute side, so its arms are not relative), and anything standing on a
    literal's cells.
    """
    literal_cells: set[Cell] = set()
    for node in graph.nodes:
        literal_cells.update(node.literal_run)

    free: set[int] = set()
    for node in graph.nodes:
        if node.kind in (NodeKind.START, NodeKind.RECEIVE_TURN):
            continue
        if node.literal_run or node.pos in literal_cells:
            continue
        free.add(node.id)
    return free


def initial_rotation(graph: FlowGraph) -> dict[int, Dir]:
    return {node.id: node.in_dir for node in graph.nodes}


def apply_rotation(graph: FlowGraph, rotation: dict[int, Dir]) -> FlowGraph | None:
    """Rebuild the graph with new entry directions, or ``None`` if illegal.

    Illegal means two nodes would collapse onto the same ``(cell, direction)``:
    they are separate steps of the program today and merging them would change
    what runs.
    """
    seen: dict[tuple[Cell, Dir], int] = {}
    nodes: list[Node] = []
    for node in graph.nodes:
        in_dir = rotation.get(node.id, node.in_dir)
        key = (node.pos, in_dir)
        if key in seen:
            return None
        seen[key] = node.id
        nodes.append(replace(node, in_dir=in_dir, out_edges=[], in_edges=[]))

    edges: list[Edge] = []
    for edge in graph.edges:
        src = nodes[edge.src]
        arms = {arm.turn: arm.direction for arm in exits_for(graph.program, src, src.in_dir)}
        exit_dir = arms.get(edge.arm)
        if exit_dir is None:
            return None
        entry_dir = nodes[edge.dst].in_dir if edge.dst is not None else edge.entry_dir
        edges.append(replace(edge, exit_dir=exit_dir, entry_dir=entry_dir))
        nodes[edge.src].out_edges.append(edge.id)
        if edge.dst is not None:
            nodes[edge.dst].in_edges.append(edge.id)

    return FlowGraph(
        program=graph.program,
        nodes=nodes,
        edges=edges,
        starts=list(graph.starts),
        node_cells=dict(graph.node_cells),
    )


@dataclass(slots=True)
class RotationMove:
    """Turning one node, and the corridors that had to move with it.

    A rotation is a *coupled* change: it re-aims every corridor arriving at the
    node and every corridor leaving it.  Keeping the whole bundle together is
    what lets the verify gate accept or reject rotations one at a time — which
    matters, because a whole-grid replan perturbs the schedule so hard that the
    cases reject it wholesale even when the program is provably unchanged.
    """

    node_id: int
    direction: Dir
    before: dict[int, tuple[tuple[Cell, ...], Dir, Dir]] = field(default_factory=dict)
    after: dict[int, tuple[tuple[Cell, ...], Dir, Dir]] = field(default_factory=dict)
    saved_ticks: int = 0


def _incident(graph: FlowGraph, node_id: int) -> list[int]:
    node = graph.nodes[node_id]
    return sorted(set(node.in_edges) | set(node.out_edges))


def _legal(graph: FlowGraph, rotation: dict[int, Dir], node_id: int, direction: Dir) -> bool:
    """Would this turn collide with another node standing on the same cell?"""
    node = graph.nodes[node_id]
    for other in graph.node_cells.get(node.pos, ()):
        if other != node_id and rotation.get(other, graph.nodes[other].in_dir) == direction:
            return False
    return True


def _dirs_under(
    graph: FlowGraph, rotation: dict[int, Dir], edge_id: int
) -> tuple[Dir, Dir] | None:
    """This edge's ``(exit, entry)`` given the current rotation of both ends."""
    edge = graph.edges[edge_id]
    src = graph.nodes[edge.src]
    src_dir = rotation.get(edge.src, src.in_dir)
    arms = {a.turn: a.direction for a in exits_for(graph.program, src, src_dir)}
    exit_dir = arms.get(edge.arm)
    if exit_dir is None:
        return None
    if edge.dst is None:
        return None
    entry_dir = rotation.get(edge.dst, graph.nodes[edge.dst].in_dir)
    return (exit_dir, entry_dir)


def propose_rotations(
    graph: FlowGraph,
    profile: Profile,
    *,
    only_men: Iterable[int] | None = None,
    slack: int = 4,
) -> tuple[list[RotationMove], Layout, dict[int, Dir]]:
    """Turn nodes one at a time against the layout the grid already has.

    Nodes are tried in order of the traffic on their incident corridors, and each
    is offered the four headings in compass order, so the walk is fixed and the
    result reproducible.  A turn is kept only when the corridors around it get
    cheaper in measured ticks; ``slack`` lets one of them grow a little if the
    others more than pay for it.
    """
    layout = Layout(graph)
    rotation = initial_rotation(graph)
    wanted = set(only_men) if only_men is not None else None

    free = rotatable_nodes(graph)

    def heat(nid: int) -> int:
        return sum(
            profile.edge_traffic(eid)
            for eid in _incident(graph, nid)
            if wanted is None or (profile.edge_men.get(eid, set()) & wanted)
        )

    ranked = sorted(((heat(n), n) for n in free), key=lambda item: (-item[0], item[1]))
    moves: list[RotationMove] = []

    for hot, node_id in ranked:
        if hot <= 0:
            break
        edges = [eid for eid in _incident(graph, node_id) if graph.edges[eid].dst is not None]
        if not edges:
            continue
        edges.sort(key=lambda eid: (-profile.edge_traffic(eid), eid))

        snapshot = {
            eid: (layout.paths[eid], *layout.dirs_for(eid)) for eid in edges
        }
        base_cost = sum(
            len(layout.paths[eid]) * profile.edge_traffic(eid) for eid in edges
        )

        def restore() -> None:
            for eid in edges:
                layout.lift(eid)
            for eid in edges:
                cells, ex, en = snapshot[eid]
                layout.set_dirs(eid, ex, en)
                layout.place(eid, cells)

        best: tuple[int, Dir, dict] | None = None
        for candidate in DIRS:
            if candidate == rotation[node_id]:
                continue
            if not _legal(graph, rotation, node_id, candidate):
                continue
            trial_rotation = dict(rotation)
            trial_rotation[node_id] = candidate
            wanted_dirs = {}
            for eid in edges:
                found = _dirs_under(graph, trial_rotation, eid)
                if found is None:
                    wanted_dirs = None
                    break
                wanted_dirs[eid] = found
            if not wanted_dirs:
                continue

            for eid in edges:
                layout.lift(eid)
            placed: dict[int, tuple[Cell, ...]] = {}
            ok = True
            for eid in edges:
                ex, en = wanted_dirs[eid]
                layout.set_dirs(eid, ex, en)
                found = route_edge(
                    layout,
                    graph.edges[eid],
                    limit=len(snapshot[eid][0]) + slack,
                    exit_dir=ex,
                    entry_dir=en,
                )
                if found is None:
                    ok = False
                    break
                layout.place(eid, found)
                placed[eid] = found
            cost = (
                sum(len(placed[eid]) * profile.edge_traffic(eid) for eid in edges)
                if ok
                else None
            )
            if ok and cost is not None and cost < base_cost:
                if best is None or cost < best[0]:
                    best = (cost, candidate, dict(placed))
            restore()

        if best is None:
            continue
        cost, candidate, placed = best
        trial_rotation = dict(rotation)
        trial_rotation[node_id] = candidate
        for eid in edges:
            layout.lift(eid)
        for eid in edges:
            ex, en = _dirs_under(graph, trial_rotation, eid)  # type: ignore[misc]
            layout.set_dirs(eid, ex, en)
            layout.place(eid, placed[eid])
        rotation = trial_rotation
        moves.append(
            RotationMove(
                node_id=node_id,
                direction=candidate,
                before={eid: snapshot[eid] for eid in edges},
                after={
                    eid: (placed[eid], *layout.dirs_for(eid)) for eid in edges
                },
                saved_ticks=base_cost - cost,
            )
        )

    return moves, layout, rotation


def apply_moves(graph: FlowGraph, moves: Sequence[RotationMove]) -> list[str]:
    """Rebuild the grid with just these turns applied."""
    layout = Layout(graph)
    for move in moves:
        for eid in move.after:
            layout.lift(eid)
        for eid, (cells, ex, en) in move.after.items():
            layout.set_dirs(eid, ex, en)
            layout.place(eid, cells)
    return layout.render()


@dataclass(slots=True)
class RotationResult:
    grid: list[str]
    accepted: list[RotationMove]
    rejected: int
    base_avg: float | None
    best_avg: float | None
    verifications: int

    @property
    def improved(self) -> bool:
        return (
            self.best_avg is not None
            and self.base_avg is not None
            and self.best_avg < self.base_avg
        )


def search(
    program: str | Path | Sequence[str] | FastLittleman,
    problem: str | os.PathLike[str] | dict,
    *,
    tick_cap: int = scoring.DEFAULT_TICK_CAP,
    only_men: Iterable[int] | None = None,
    verbose: bool = False,
) -> RotationResult:
    """Propose turns, then keep only the ones the public cases agree with.

    Same two gates as :func:`manreroute.search`.  The canonical signature proves
    the turned grid still runs the same program — that check has to be the
    rotation-proof one, because turning a node changes its ``(cell, direction)``
    key and the cheaper equality check would reject every legal turn.
    """
    from .optimize import verify

    prog = program if isinstance(program, FastLittleman) else FastLittleman(program)
    graph = build_flow_graph(prog)
    profile = profile_program(prog, problem, graph=graph, tick_cap=tick_cap)
    if profile.mismatches:
        raise ValueError(f"cannot profile: {profile.mismatches[0]}")

    base = verify(prog.grid, problem, tick_cap=tick_cap)
    if not base.passed:
        raise ValueError(f"the input grid does not pass: {base.n_passed}/{len(base.cases)}")

    moves, _, _ = propose_rotations(graph, profile, only_men=only_men)
    moves.sort(key=lambda m: (-m.saved_ticks, m.node_id))

    signature = canonical_signature(graph)
    best_grid = list(prog.grid)
    best_avg = base.avg_ticks
    kept: list[RotationMove] = []
    rejected = 0
    verifications = 1

    index = 0
    chunk = len(moves)
    while index < len(moves):
        batch = moves[index : index + chunk]
        trial = kept + batch
        grid = apply_moves(graph, trial)
        ok = canonical_signature(build_flow_graph(FastLittleman(grid))) == signature
        result = None
        if ok:
            result = verify(grid, problem, tick_cap=tick_cap)
            verifications += 1
            ok = result.passed and (
                best_avg is None
                or result.avg_ticks is None
                or result.avg_ticks <= best_avg
            )
        if ok and result is not None:
            kept = trial
            best_grid = grid
            best_avg = result.avg_ticks
            index += chunk
            chunk = min(len(moves) - index, max(chunk, 1) * 2) or 1
            if verbose:
                print(f"  kept {len(batch)} turn(s), avg {best_avg:.2f}")
        elif chunk > 1:
            chunk = chunk // 2
        else:
            rejected += 1
            index += 1
            chunk = 1

    return RotationResult(
        grid=best_grid,
        accepted=kept,
        rejected=rejected,
        base_avg=base.avg_ticks,
        best_avg=best_avg,
        verifications=verifications,
    )


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m randomfun2026solvers.manrotate",
        description="Free each instruction's approach direction, then reroute.",
    )
    parser.add_argument("program", type=Path)
    parser.add_argument("problem", help="problem slug or tasks/problems/*.json")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--tick-cap", type=int, default=scoring.DEFAULT_TICK_CAP)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pacing-men-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    prog = FastLittleman(args.program)
    graph = build_flow_graph(prog)
    profile = profile_program(prog, args.problem, graph=graph, tick_cap=args.tick_cap)
    if profile.mismatches:
        print(f"refusing: {len(profile.mismatches)} trace mismatch(es)")
        return 1

    only = None
    if args.pacing_men_only and profile.men:
        busiest = max(profile.men[m].busy for m in profile.men)
        only = [m for m in profile.men if profile.men[m].busy >= busiest // 2]

    print(f"{len(rotatable_nodes(graph))}/{len(graph.nodes)} nodes may be turned")

    if args.dry_run:
        moves, _, _ = propose_rotations(graph, profile, only_men=only)
        print(
            f"{len(moves)} turn(s) proposed, "
            f"{sum(m.saved_ticks for m in moves):,} man-ticks predicted"
        )
        grid = apply_moves(graph, moves)
        same = canonical_signature(build_flow_graph(FastLittleman(grid))) == canonical_signature(
            graph
        )
        print("program preserved" if same else "REJECTED: program changed")
        if args.out and same:
            args.out.write_text("\n".join(grid) + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
        return 0 if same else 1

    result = search(
        prog, args.problem, tick_cap=args.tick_cap, only_men=only, verbose=args.verbose
    )
    base, best = result.base_avg or 0.0, result.best_avg or 0.0
    delta = (100.0 * (base - best) / base) if base else 0.0
    print(
        f"{len(result.accepted)} turn(s) kept, {result.rejected} rejected, "
        f"{result.verifications} verification(s)"
    )
    print(f"avg ticks {base:.2f} -> {best:.2f}  ({delta:+.2f}%)")
    if args.out and result.improved:
        args.out.write_text("\n".join(result.grid) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
