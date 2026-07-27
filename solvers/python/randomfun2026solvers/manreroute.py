"""Deterministic corridor rerouting.

The flow graph says *what* each man does; this module only changes *where he
walks between doing it*.  Nodes keep their cell, their glyph and the direction
they are entered from, and every edge keeps its ``(source, arm, destination)``.
The only thing that moves is the corridor in between — and since a corridor cell
costs exactly one tick, a shorter corridor is a faster program with, by
construction, the same meaning.

The search is a rip-up-and-reroute in the FPGA sense, run hottest corridor
first:

1. Start from the layout the grid already has.  It is feasible by definition.
2. Take the corridor with the largest ``traffic x length`` bill.  Lift it off
   the grid, then look for the shortest legal path between the same two nodes
   through whatever space is now free.
3. Keep the new path only if it is strictly shorter.  Otherwise put the old one
   back.

Every step is monotone and every tie is broken on coordinates, so the whole
thing is deterministic: same grid and same profile in, same grid out.

The 2D rules a corridor has to obey are entirely local, which is what makes the
search cheap:

* A neutral cell (``' '``) passes a man through unchanged, so two corridors may
  *cross* on one — they just have to cross, not turn.
* An arrow cell re-aims everyone who steps on it.  Corridors may therefore share
  an arrow only if they all want to leave it in the same direction.
"""

from __future__ import annotations

import heapq
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import scoring
from .fast_littleman import (
    ARROW_DIR,
    DIRS,
    Cell,
    Dir,
    FastLittleman,
    _add,
)
from .manflow import CORRIDOR_GLYPHS, NEUTRAL_GLYPHS, Edge, FlowGraph, build_flow_graph
from .manprofile import Profile, profile_program

__all__ = [
    "Layout",
    "Move",
    "RerouteResult",
    "graph_signature",
    "reroute",
]

#: Glyph to draw for each direction.  ``'v'`` (lowercase) matches house style.
DIR_GLYPH: dict[Dir, str] = {d: g for g, d in ARROW_DIR.items() if g != "V"}


@dataclass(slots=True)
class CellUse:
    """What the corridors currently ask of one cell.

    ``turn_dir`` is set once some corridor turns here, which forces an arrow and
    so forces *everyone* through the cell to leave that way.  ``through`` are
    the directions of corridors that pass straight over it.
    """

    turn_dir: Dir | None = None
    through: set[Dir] = field(default_factory=set)
    edges: set[int] = field(default_factory=set)

    def glyph(self) -> str:
        if self.turn_dir is not None:
            return DIR_GLYPH[self.turn_dir]
        return " "

    def accepts(self, in_dir: Dir, out_dir: Dir) -> bool:
        if out_dir == in_dir:
            # Passing straight is fine unless an arrow here points elsewhere.
            return self.turn_dir is None or self.turn_dir == in_dir
        # Turning needs an arrow, and an arrow re-aims every other corridor on
        # this cell — so they must all already be leaving the same way.
        if self.turn_dir is not None and self.turn_dir != out_dir:
            return False
        return all(d == out_dir for d in self.through)

    def add(self, in_dir: Dir, out_dir: Dir, edge_id: int) -> None:
        if out_dir != in_dir:
            self.turn_dir = out_dir
        else:
            self.through.add(in_dir)
        self.edges.add(edge_id)

    def is_empty(self) -> bool:
        return not self.edges


@dataclass(slots=True)
class Move:
    """One accepted reroute, kept so a run can be replayed or truncated."""

    edge_id: int
    before: tuple[Cell, ...]
    after: tuple[Cell, ...]
    traffic: int

    @property
    def saved_cells(self) -> int:
        return len(self.before) - len(self.after)

    @property
    def saved_ticks(self) -> int:
        return self.saved_cells * self.traffic


class Layout:
    """The 2D state of every corridor on the grid, plus what may be touched."""

    def __init__(self, graph: FlowGraph, *, empty: bool = False) -> None:
        """``empty`` clears every corridor but keeps the ground they stood on.

        Rotating a node invalidates the corridors either side of it, so a
        rotated layout has to be routed from scratch — but the cells the old
        corridors occupied are still ours to pave.
        """
        self.graph = graph
        self.prog = graph.program
        #: Per-edge direction overrides; see :meth:`dirs_for`.
        self._dirs: dict[int, tuple[Dir, Dir]] = {}
        #: Where the corridors were before anything moved.  Kept even in empty
        #: mode: it is the set :meth:`render` has to blank before repainting.
        self.original_cells: set[Cell] = {
            cell for edge in graph.edges for cell in edge.cells
        }
        self.paths: dict[int, tuple[Cell, ...]] = {
            edge.id: () if empty else edge.cells for edge in graph.edges
        }
        #: Cells nothing may be written to: node glyphs, literal runs, walls,
        #: pipes, displays, and any non-blank cell the router did not create.
        self.pinned: set[Cell] = set()
        for node in graph.nodes:
            self.pinned.add(node.pos)
            self.pinned.update(node.literal_run)
        self.use: dict[Cell, CellUse] = {}
        if not empty:
            for edge in graph.edges:
                self._lay(edge.id, edge.cells, edge.exit_dir, edge.entry_dir)
        #: Blank cells the router may pave over.  A non-blank cell that is not
        #: corridor is left alone even when unreachable: it may be a backtick
        #: that another literal pairs with on its column.
        self.paveable: set[Cell] = set()
        for room in self.prog.rooms:
            if room.kind != "compute":
                continue
            for y in range(room.min[1] + 1, room.max[1]):
                for x in range(room.min[0] + 1, room.max[0]):
                    cell = (x, y)
                    if cell in self.pinned:
                        continue
                    if (
                        self.prog._char(x, y) in NEUTRAL_GLYPHS
                        or cell in self.use
                        or cell in self.original_cells
                    ):
                        self.paveable.add(cell)

    # -- laying and lifting -------------------------------------------------
    def dirs_for(self, edge_id: int) -> tuple[Dir, Dir]:
        """The ``(exit, entry)`` this edge is currently laid with.

        Rotating a node re-aims the corridors either side of it, so a layout has
        to be able to hold directions that differ from the graph it was built
        from — otherwise every trial rotation would need a whole new layout.
        """
        override = self._dirs.get(edge_id)
        if override is not None:
            return override
        edge = self.graph.edges[edge_id]
        return (edge.exit_dir, edge.entry_dir)

    def set_dirs(self, edge_id: int, exit_dir: Dir, entry_dir: Dir) -> None:
        self._dirs[edge_id] = (exit_dir, entry_dir)

    def _transitions(
        self, cells: Sequence[Cell], exit_dir: Dir, entry_dir: Dir
    ) -> list[tuple[Cell, Dir, Dir]]:
        """``(cell, in_dir, out_dir)`` for every corridor cell of a path."""
        out: list[tuple[Cell, Dir, Dir]] = []
        in_dir = exit_dir
        for i, cell in enumerate(cells):
            nxt = cells[i + 1] if i + 1 < len(cells) else None
            out_dir = _direction_between(cell, nxt) if nxt is not None else entry_dir
            out.append((cell, in_dir, out_dir))
            in_dir = out_dir
        return out

    def _lay(self, edge_id: int, cells: Sequence[Cell], exit_dir: Dir, entry_dir: Dir) -> None:
        for cell, in_dir, out_dir in self._transitions(cells, exit_dir, entry_dir):
            self.use.setdefault(cell, CellUse()).add(in_dir, out_dir, edge_id)

    def lift(self, edge_id: int) -> tuple[Cell, ...]:
        """Remove one edge's corridor and rebuild the cells it shared."""
        old = self.paths[edge_id]
        touched = {cell for cell in old}
        for cell in touched:
            use = self.use.get(cell)
            if use is not None:
                use.edges.discard(edge_id)
        # A cell's arrow/straight state is the union of its remaining edges, so
        # recompute it from them rather than trying to subtract.
        for cell in touched:
            use = self.use.get(cell)
            if use is None:
                continue
            if use.is_empty():
                del self.use[cell]
                self.paveable.add(cell)
                continue
            use.turn_dir = None
            use.through = set()
        for other_id, cells in self.paths.items():
            if other_id == edge_id:
                continue
            exit_dir, entry_dir = self.dirs_for(other_id)
            for cell, in_dir, out_dir in self._transitions(cells, exit_dir, entry_dir):
                if cell in touched and cell in self.use:
                    self.use[cell].add(in_dir, out_dir, other_id)
        self.paths[edge_id] = ()
        return old

    def place(self, edge_id: int, cells: Sequence[Cell]) -> None:
        exit_dir, entry_dir = self.dirs_for(edge_id)
        self.paths[edge_id] = tuple(cells)
        for cell in cells:
            self.paveable.discard(cell)
        self._lay(edge_id, cells, exit_dir, entry_dir)

    # -- rendering ----------------------------------------------------------
    def _keeps_original(self, cell: Cell, use: CellUse) -> bool:
        """Is the glyph already there good enough for what this cell now does?

        Redundant arrows are common — a ``>`` a man only ever walks eastward
        over is the same as a space.  Leaving them be keeps the diff to the
        cells that actually moved, which is what makes a round-trip with no
        moves byte-identical and therefore worth testing.
        """
        original = self.prog._char(*cell)
        if original not in CORRIDOR_GLYPHS:
            return False
        if use.turn_dir is not None:
            return ARROW_DIR.get(original) == use.turn_dir
        if original in NEUTRAL_GLYPHS:
            return True
        # An arrow with no turn asked of it is fine only if it points the way
        # every corridor over this cell is already going.
        return all(d == ARROW_DIR[original] for d in use.through)

    def render(self) -> list[str]:
        """Paint the layout back onto the grid."""
        rows = [list(row) for row in self.prog.grid]
        # Clear everything the old corridors owned, then repaint from `use`.
        for cell in self.original_cells:
            if cell in self.pinned:
                continue
            x, y = cell
            rows[y][x] = " "
        for cell, use in self.use.items():
            x, y = cell
            rows[y][x] = self.prog._char(x, y) if self._keeps_original(cell, use) else use.glyph()
        return ["".join(row).rstrip() for row in rows]


def _direction_between(a: Cell, b: Cell) -> Dir:
    dx, dy = b[0] - a[0], b[1] - a[1]
    step = (0 if dx == 0 else (1 if dx > 0 else -1), 0 if dy == 0 else (1 if dy > 0 else -1))
    if step not in DIRS or (dx != 0 and dy != 0):
        raise ValueError(f"cells {a} and {b} are not orthogonally adjacent")
    return step  # type: ignore[return-value]


def _manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def route_edge(
    layout: Layout,
    edge: Edge,
    *,
    limit: int,
    exit_dir: Dir | None = None,
    entry_dir: Dir | None = None,
) -> tuple[Cell, ...] | None:
    """Shortest legal corridor for ``edge``, or ``None`` if there is none.

    ``limit`` caps the path length, so passing the current length searches only
    for a strict improvement.  Ties break on ``(cell, direction)`` order, which
    is what makes repeated runs identical.  The direction arguments override the
    edge's own, which is how a trial rotation is priced without rebuilding the
    graph it belongs to.
    """
    graph = layout.graph
    src = graph.nodes[edge.src]
    if edge.dst is None:
        return None
    dst = graph.nodes[edge.dst]
    exit_dir = edge.exit_dir if exit_dir is None else exit_dir
    entry_dir = edge.entry_dir if entry_dir is None else entry_dir
    start_cell = src.literal_run[-1] if src.literal_run else src.pos
    target = dst.pos

    first = _add(start_cell, exit_dir)
    # A zero-cell corridor: the nodes are already adjacent the right way round.
    if first == target:
        return () if exit_dir == entry_dir else None
    if first not in layout.paveable and first not in layout.use:
        return None

    start_state = (first, exit_dir)
    best: dict[tuple[Cell, Dir], int] = {start_state: 1}
    parent: dict[tuple[Cell, Dir], tuple[Cell, Dir] | None] = {start_state: None}
    heap: list[tuple[int, int, Cell, Dir]] = [
        (1 + _manhattan(first, target) - 1, 1, first, exit_dir)
    ]

    goal: tuple[Cell, Dir] | None = None
    while heap:
        _, cost, cell, in_dir = heapq.heappop(heap)
        if cost > best.get((cell, in_dir), 1 << 30):
            continue
        if cost > limit:
            continue
        use = layout.use.get(cell)
        # Arrival: step from here into the destination with the required
        # entry direction.
        if _add(cell, entry_dir) == target:
            if use is None or use.accepts(in_dir, entry_dir):
                goal = (cell, in_dir)
                break
        for out_dir in DIRS:
            nxt = _add(cell, out_dir)
            if nxt == target:
                continue  # only the entry_dir approach may touch the node
            if nxt not in layout.paveable and nxt not in layout.use:
                continue
            if use is not None and not use.accepts(in_dir, out_dir):
                continue
            state = (nxt, out_dir)
            new_cost = cost + 1
            if new_cost >= best.get(state, 1 << 30) or new_cost > limit:
                continue
            best[state] = new_cost
            parent[state] = (cell, in_dir)
            heapq.heappush(
                heap,
                (new_cost + max(_manhattan(nxt, target) - 1, 0), new_cost, nxt, out_dir),
            )

    if goal is None:
        return None
    path: list[Cell] = []
    state: tuple[Cell, Dir] | None = goal
    while state is not None:
        path.append(state[0])
        state = parent[state]
    path.reverse()
    if len(set(path)) != len(path):
        # A self-crossing path cannot be painted consistently; skip it rather
        # than paint a grid that means something else.
        return None
    return tuple(path)


def graph_signature(graph: FlowGraph) -> tuple:
    """A canonical fingerprint of everything a reroute must not change.

    Cells, glyphs, entry directions, and every ``(source, arm, destination)``.
    Corridor geometry is deliberately absent — that is the part we are moving.
    """
    nodes = {
        node.id: (node.pos, node.in_dir, node.glyph, node.literal_run)
        for node in graph.nodes
    }
    edges = sorted(
        (
            nodes[edge.src],
            edge.arm,
            edge.exit_dir,
            edge.entry_dir,
            nodes[edge.dst] if edge.dst is not None else edge.dead,
        )
        for edge in graph.edges
    )
    starts = tuple(nodes[s] for s in graph.starts)
    return (starts, tuple(edges))


@dataclass(slots=True)
class RerouteResult:
    grid: list[str]
    moves: list[Move]
    layout: Layout
    #: corridor cells before and after, over the whole grid
    cells_before: int
    cells_after: int

    @property
    def saved_ticks(self) -> int:
        """Predicted man-ticks saved, from the measured traffic."""
        return sum(move.saved_ticks for move in self.moves)

    @property
    def saved_cells(self) -> int:
        return self.cells_before - self.cells_after


def _rank_edges(
    graph: FlowGraph, profile: Profile, only_men: Iterable[int] | None
) -> list[int]:
    """Edges worth touching, hottest first.  Ties break on id, so this is stable."""
    wanted = set(only_men) if only_men is not None else None
    return [
        edge_id
        for edge_id, _ in profile.hot_edges()
        if graph.edges[edge_id].dst is not None
        and graph.edges[edge_id].length > 0
        and (wanted is None or (profile.edge_men.get(edge_id, set()) & wanted))
    ]


def _reroute_incremental(
    graph: FlowGraph, profile: Profile, ranked: Sequence[int], max_moves: int | None
) -> tuple[Layout, list[Move]]:
    """Lift one corridor at a time and better it against everything else."""
    layout = Layout(graph)
    moves: list[Move] = []
    for edge_id in ranked:
        if max_moves is not None and len(moves) >= max_moves:
            break
        edge = graph.edges[edge_id]
        old = layout.lift(edge_id)
        new = route_edge(layout, edge, limit=len(old) - 1)
        if new is None or len(new) >= len(old):
            layout.place(edge_id, old)
            continue
        layout.place(edge_id, new)
        moves.append(
            Move(edge_id, old, new, profile.edge_traffic(edge_id))
        )
    return layout, moves


def _reroute_global(
    graph: FlowGraph, profile: Profile, ranked: Sequence[int]
) -> tuple[Layout, list[Move]] | None:
    """Lift *every* corridor, then lay them back down hottest first.

    Rerouting one corridor at a time is pessimistic: the hottest one is bettered
    against a grid still cluttered with all the cold ones.  Clearing the field
    first lets the corridors that carry the most ticks take the straight runs
    and pushes the detours onto corridors nobody walks.

    Some corridor may then have nowhere to go.  Rather than accept a worse
    layout, that edge is pinned to the path it already had and the whole pass is
    retried, until either everything fits or the pass gives up — so the result
    is the original layout or a better one, never a worse one.
    """
    order = list(ranked)
    ranked_set = set(order)
    # Corridors we are not ranking (cold, or another man's) still have to go
    # somewhere; lay them after the ranked ones, in a stable order.
    rest = [
        edge.id
        for edge in graph.edges
        if edge.id not in ranked_set and edge.dst is not None and edge.length > 0
    ]
    pinned: set[int] = set()

    for _ in range(len(order) + len(rest) + 1):
        layout = Layout(graph)
        originals = {eid: layout.paths[eid] for eid in order + rest}
        for edge_id in order + rest:
            if edge_id not in pinned:
                layout.lift(edge_id)
        failed = None
        for edge_id in order + rest:
            if edge_id in pinned:
                continue
            edge = graph.edges[edge_id]
            old = originals[edge_id]
            new = route_edge(layout, edge, limit=len(old))
            if new is None:
                failed = edge_id
                break
            layout.place(edge_id, new)
        if failed is None:
            moves = [
                Move(eid, originals[eid], layout.paths[eid], profile.edge_traffic(eid))
                for eid in order + rest
                if eid not in pinned and layout.paths[eid] != originals[eid]
            ]
            return layout, moves
        pinned.add(failed)
    return None


def reroute(
    program: str | Path | Sequence[str] | FastLittleman,
    profile: Profile,
    *,
    graph: FlowGraph | None = None,
    only_men: Iterable[int] | None = None,
    max_moves: int | None = None,
    strategy: str = "best",
) -> RerouteResult:
    """Rip up and re-route corridors hottest first.

    ``only_men`` restricts the work to the corridors of particular runners —
    normally the ones the profile shows are pacing the program, since a man who
    is mostly blocked gains nothing from a shorter walk.  ``strategy`` is
    ``"incremental"``, ``"global"``, or ``"best"`` to run both and keep whichever
    saves more measured ticks.
    """
    graph = graph or profile.graph
    ranked = _rank_edges(graph, profile, only_men)

    candidates: list[tuple[Layout, list[Move]]] = []
    if strategy in ("incremental", "best"):
        candidates.append(_reroute_incremental(graph, profile, ranked, max_moves))
    if strategy in ("global", "best") and max_moves is None:
        found = _reroute_global(graph, profile, ranked)
        if found is not None:
            candidates.append(found)

    def saving(item: tuple[Layout, list[Move]]) -> tuple[int, int]:
        moves = item[1]
        return (
            sum(m.saved_ticks for m in moves),
            sum(m.saved_cells for m in moves),
        )

    layout, moves = max(candidates, key=saving)
    base = Layout(graph)
    return RerouteResult(
        grid=layout.render(),
        moves=moves,
        layout=layout,
        cells_before=sum(len(p) for p in base.paths.values()),
        cells_after=sum(len(p) for p in layout.paths.values()),
    )


@dataclass(slots=True)
class SearchResult:
    grid: list[str]
    accepted: list[Move]
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


def _apply(graph: FlowGraph, moves: Sequence[Move]) -> list[str]:
    layout = Layout(graph)
    for move in moves:
        layout.lift(move.edge_id)
        layout.place(move.edge_id, move.after)
    return layout.render()


def search(
    program: str | Path | Sequence[str] | FastLittleman,
    problem: str | os.PathLike[str] | dict,
    *,
    tick_cap: int = scoring.DEFAULT_TICK_CAP,
    rounds: int = 2,
    only_men: Iterable[int] | None = None,
    verbose: bool = False,
) -> SearchResult:
    """Reroute under a verify gate, keeping only what actually pays.

    The graph check guarantees a reroute means the same thing; it cannot
    guarantee the program still *works*, because ticks are also a schedule.
    Men move in lockstep and two who touch both halt, so making one man faster
    can make him collide with another, and a shorter walk to a pipe is only a
    longer wait at it.  So every batch is run against the public cases and kept
    only if it passes and is not slower.

    Batches start large and halve on failure.  That finds a good subset in a
    handful of runs when most moves are harmless, and degrades to one-at-a-time
    exactly where the schedule is delicate — without ever consulting anything
    but the measured result, so the run is reproducible.
    """
    from .optimize import verify  # local: optimize imports manprofile

    prog = program if isinstance(program, FastLittleman) else FastLittleman(program)
    base = verify(prog.grid, problem, tick_cap=tick_cap)
    if not base.passed:
        raise ValueError(f"the input grid does not pass: {base.n_passed}/{len(base.cases)}")

    best_grid = list(prog.grid)
    best_avg = base.avg_ticks
    accepted: list[Move] = []
    rejected = 0
    verifications = 1

    for round_index in range(rounds):
        current = FastLittleman(best_grid)
        graph = build_flow_graph(current)
        profile = profile_program(current, problem, graph=graph, tick_cap=tick_cap)
        if profile.mismatches:
            break
        proposal = reroute(current, profile, graph=graph, only_men=only_men)
        if not proposal.moves:
            break
        signature = graph_signature(graph)

        queue = list(proposal.moves)
        kept: list[Move] = []
        chunk = len(queue)
        index = 0
        while index < len(queue):
            batch = queue[index : index + chunk]
            trial = kept + batch
            grid = _apply(graph, trial)
            ok = graph_signature(build_flow_graph(FastLittleman(grid))) == signature
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
                chunk = min(len(queue) - index, max(chunk, 1) * 2) or 1
                if verbose:
                    print(
                        f"  round {round_index}: kept {len(batch)} move(s), "
                        f"avg {best_avg:.2f}"
                    )
            elif chunk > 1:
                chunk = chunk // 2
            else:
                rejected += 1
                index += 1
                chunk = 1
        accepted.extend(kept)
        if not kept:
            break

    return SearchResult(
        grid=best_grid,
        accepted=accepted,
        rejected=rejected,
        base_avg=base.avg_ticks,
        best_avg=best_avg,
        verifications=verifications,
    )


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m randomfun2026solvers.manreroute",
        description="Shorten the corridors of a .man grid without changing its graph.",
    )
    parser.add_argument("program", type=Path)
    parser.add_argument("problem", help="problem slug or tasks/problems/*.json")
    parser.add_argument("--out", type=Path, help="write the rerouted grid here")
    parser.add_argument("--tick-cap", type=int, default=scoring.DEFAULT_TICK_CAP)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="propose reroutes and check the graph, but do not run the cases",
    )
    parser.add_argument(
        "--pacing-men-only",
        action="store_true",
        help="only reroute corridors walked by men who are not mostly idle",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    prog = FastLittleman(args.program)
    graph = build_flow_graph(prog)
    profile = profile_program(prog, args.problem, graph=graph, tick_cap=args.tick_cap)
    if profile.mismatches:
        print(f"refusing to reroute: {len(profile.mismatches)} trace mismatch(es)")
        for line in profile.mismatches[:5]:
            print(f"  {line}")
        return 1

    only = None
    if args.pacing_men_only and profile.men:
        busiest = max(profile.men[m].busy for m in profile.men)
        only = [m for m in profile.men if profile.men[m].busy >= busiest // 2]

    if args.dry_run:
        proposal = reroute(prog, profile, graph=graph, only_men=only)
        print(
            f"{len(proposal.moves)} corridor(s) rerouted, "
            f"{proposal.saved_cells} cells removed, "
            f"{proposal.saved_ticks} man-ticks predicted saved"
        )
        same = graph_signature(build_flow_graph(FastLittleman(proposal.grid))) == graph_signature(
            graph
        )
        print("flow graph preserved exactly" if same else "REJECTED: flow graph changed")
        if args.out and same:
            args.out.write_text("\n".join(proposal.grid) + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
        return 0 if same else 1

    result = search(
        prog,
        args.problem,
        tick_cap=args.tick_cap,
        rounds=args.rounds,
        only_men=only,
        verbose=args.verbose,
    )
    base = result.base_avg or 0.0
    best = result.best_avg or 0.0
    delta = (100.0 * (base - best) / base) if base else 0.0
    print(
        f"{len(result.accepted)} move(s) kept, {result.rejected} rejected, "
        f"{result.verifications} verification(s)"
    )
    print(f"avg ticks {base:.2f} -> {best:.2f}  ({delta:+.2f}%)")
    if args.out and result.improved:
        args.out.write_text("\n".join(result.grid) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    elif args.out:
        print("nothing to write: no accepted improvement")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
