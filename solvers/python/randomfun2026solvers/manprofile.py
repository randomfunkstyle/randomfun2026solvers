"""Where the ticks actually go: per-edge traffic on the flow graph.

The router needs one number per corridor — how many ticks flow through it — so
that shortening a corridor by one cell can be priced as "traffic ticks saved".
This module produces that number exactly, by tracing every man's walk and
replaying it against :mod:`manflow`.

The trace runs on the pure-Python engine because it has to be observable.  That
engine ships without display support, so this module adds it (mirroring the
native backend tick-for-tick) — otherwise the display problems, which are the
ones with the most corridor to win back, could not be profiled at all.

Two costs are reported per node and edge:

``traffic``  how many times a man walked it.  Multiplied by a corridor's length
             this is the corridor's tick bill, and the router's objective.
``blocked``  ticks a man stood still on a node waiting for a pipe.  These are
             *not* recoverable by rerouting — shortening the path to a man's
             wait only makes him wait longer — so the router uses them to
             discount edges that feed a blocked node.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import scoring
from .fast_littleman import (
    Cell,
    Dir,
    FastLittleman,
    FastLittlemanError,
    FastResult,
    _Machine,
)
from .manflow import FlowGraph, NodeKind, build_flow_graph

__all__ = [
    "EdgeCost",
    "NodeCost",
    "Profile",
    "TraceMachine",
    "profile_program",
    "resume_index",
    "trace_case",
]


# ── the tracing engine ────────────────────────────────────────────────────────
class TraceMachine(_Machine):
    """``_Machine`` plus displays, frame gating, and a per-tick walk trace.

    ``trace[runner_id]`` is the list of ``(pos, direction)`` states that runner
    executed, one entry per tick — so a repeated entry is a blocked tick.  That
    is all the profiler needs; the graph supplies the structure.
    """

    def __init__(
        self,
        program: FastLittleman,
        input_rounds: list[list[int]],
        expected_rounds: list[list[int]] | None,
        frame_rounds: list[list[list[int]]] | None = None,
    ) -> None:
        self.frame_rounds = frame_rounds
        self.expected_frames: list[list[int]] = []
        self.matched_frames = 0
        if frame_rounds is not None:
            for round_frames in frame_rounds:
                self.expected_frames.extend(round_frames)
        super().__init__(program, input_rounds, expected_rounds)
        if frame_rounds is not None:
            # The base class built its cumulative table from expected *output*.
            # Frame problems gate on committed frames instead.
            self.expected_cumulative = []
            total = 0
            for round_frames in frame_rounds:
                total += len(round_frames)
                self.expected_cumulative.append(total)
            self.released_round = 0
            self.input_queue.clear()
            self.input_queue.extend(input_rounds[0] if input_rounds else [])
            self._release_satisfied_rounds()

        self.displays: list[dict[str, Any]] = []
        for room in program.rooms:
            if room.kind != "display":
                continue
            width = room.max[0] - room.min[0] - 1
            height = room.max[1] - room.min[1] - 1
            self.displays.append(
                {
                    "room": room.id,
                    "width": width,
                    "height": height,
                    "cursor": 0,
                    "current": [0] * (width * height),
                    "next": [0] * (width * height),
                }
            )
        self.trace: dict[int, list[tuple[Cell, Dir]]] = defaultdict(list)

    # -- gating -------------------------------------------------------------
    def _progress(self) -> int:
        if self.frame_rounds is not None:
            return self.matched_frames
        return len(self.output)

    def _release_satisfied_rounds(self) -> None:
        while (
            self.released_round < len(self.expected_cumulative)
            and self._progress() >= self.expected_cumulative[self.released_round]
        ):
            self.released_round += 1
            if self.released_round < len(self.input_rounds):
                self.input_queue.extend(self.input_rounds[self.released_round])

    def run(self, max_ticks: int) -> FastResult:
        if self.frame_rounds is None:
            return super().run(max_ticks)
        while self.step < max_ticks:
            if self.fatal is not None:
                return self._result(self.fatal, passed=False)
            if self.matched_frames >= len(self.expected_frames):
                return self._result("output-settled", passed=not self.output)
            active = any(not runner.halted for runner in self.runners)
            if not active and not self._output_in_flight():
                return self._result("done", passed=False)
            self._tick()
        return self._result("tick-cap", passed=False)

    # -- tick ---------------------------------------------------------------
    def _tick(self) -> None:
        self.step += 1
        self._shift_pipes()
        self._io()
        if self.fatal is not None:
            return
        for runner in self.runners:
            if not runner.halted:
                self.trace[runner.id].append((runner.pos, runner.direction))
        self._execute()
        if self.fatal is not None:
            return
        self._execute_displays()
        if self.fatal is not None:
            return
        self._move()

    # -- displays -----------------------------------------------------------
    def _display_pipe(self, room: int, side: int) -> int:
        """``side`` 0 = top/ADDR, 1 = left/DATA, 2 = bottom/SWAP."""
        want = {0: (0, 1), 1: (1, 0), 2: (0, -1)}[side]
        for pipe in self.p.pipes:
            if pipe.dst == room:
                if tuple(pipe.dst_side) == want:
                    return pipe.id
        return -1

    def _take_display(self, pid: int) -> int | None:
        if pid < 0:
            return None
        values = self.pipe_values[pid]
        value = values[-1]
        if value is None:
            return None
        values[-1] = None
        return value

    def _execute_displays(self) -> None:
        for display in self.displays:
            room = display["room"]
            size = display["width"] * display["height"]

            value = self._take_display(self._display_pipe(room, 0))
            if value is not None:
                if value < 0 or value >= size:
                    self._fatal("display-address", (-1, -1))
                    return
                display["cursor"] = value

            value = self._take_display(self._display_pipe(room, 1))
            if value is not None:
                if not 0 <= value <= 15:
                    self._fatal("display-color", (-1, -1))
                    return
                display["next"][display["cursor"]] = value
                display["cursor"] = (display["cursor"] + 1) % size

            value = self._take_display(self._display_pipe(room, 2))
            if value is not None:
                if value not in (0, 1):
                    self._fatal("display-swap", (-1, -1))
                    return
                display["current"] = list(display["next"])
                if value == 0:
                    display["next"] = [0] * size
                    display["cursor"] = 0
                if self.frame_rounds is not None:
                    if (
                        self.matched_frames >= len(self.expected_frames)
                        or display["current"] != self.expected_frames[self.matched_frames]
                    ):
                        self._fatal("wrong-frame", (-1, -1))
                        return
                    self.matched_frames += 1
                    self._release_satisfied_rounds()


def trace_case(
    prog: FastLittleman,
    *,
    input: str | Sequence[int] | None = None,
    expected: str | Sequence[int] | None = None,
    frames: Sequence[Sequence[Sequence[str]]] | None = None,
    max_ticks: int = 5_000_000,
) -> tuple[FastResult, dict[int, list[tuple[Cell, Dir]]]]:
    """Run one case on the tracing engine and return its result and trace."""
    input_rounds = prog._parse_round_values(input)
    expected_rounds = prog._parse_round_values(expected) if expected is not None else None
    frame_rounds = prog._parse_frame_rounds(frames)
    machine = TraceMachine(prog, input_rounds, expected_rounds, frame_rounds)
    result = machine.run(max_ticks)
    return result, dict(machine.trace)


# ── costs ─────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class EdgeCost:
    traffic: int = 0  # walks along this corridor
    ticks: int = 0  # traffic * length, i.e. what it costs the program

    def add(self, walks: int, length: int) -> None:
        self.traffic += walks
        self.ticks += walks * length


@dataclass(slots=True)
class NodeCost:
    visits: int = 0
    blocked: int = 0  # extra ticks stood still, over and above the one visit tick


@dataclass(slots=True)
class ManCost:
    """One man's own budget, summed over cases.

    This is the number that decides whether rerouting can pay.  Ticks are wall
    clock and men run in lockstep, so the program's length is set by whichever
    man is never idle.  A man who spends most of his ticks blocked is waiting on
    someone else and shortening his corridors buys nothing — it only makes him
    wait longer at the same pipe.
    """

    corridor: int = 0
    node: int = 0
    blocked: int = 0

    @property
    def total(self) -> int:
        return self.corridor + self.node

    @property
    def busy(self) -> int:
        """Ticks actually spent doing something rather than standing still."""
        return self.total - self.blocked


@dataclass(slots=True)
class Profile:
    graph: FlowGraph
    edges: dict[int, EdgeCost]
    nodes: dict[int, NodeCost]
    #: per case, ``(name, ticks, passed)``
    cases: list[tuple[str, int, bool]] = field(default_factory=list)
    #: ticks spent walking corridor, summed over every case
    corridor_ticks: int = 0
    #: ticks spent executing nodes (including blocked waits), summed
    node_ticks: int = 0
    #: per runner id, its own split of the above
    men: dict[int, ManCost] = field(default_factory=dict)
    #: edge id -> the runner ids that ever walked it
    edge_men: dict[int, set[int]] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)

    @property
    def wall_ticks(self) -> int:
        """Ticks the program is actually scored on, summed over cases."""
        return sum(ticks for _, ticks, _ in self.cases)

    def bottleneck_men(self) -> list[int]:
        """Men ranked by how little they idle — the reroute's real targets."""
        return sorted(self.men, key=lambda mid: (-self.men[mid].busy, mid))

    @property
    def total_ticks(self) -> int:
        return self.corridor_ticks + self.node_ticks

    def edge_traffic(self, edge_id: int) -> int:
        cost = self.edges.get(edge_id)
        return cost.traffic if cost else 0

    def hot_edges(self, limit: int | None = None) -> list[tuple[int, EdgeCost]]:
        """Edges by tick cost, hottest first.  Deterministic: ties break on id."""
        ranked = sorted(
            self.edges.items(),
            key=lambda item: (-item[1].ticks, item[0]),
        )
        return ranked[:limit] if limit else ranked

    def report(self, limit: int = 25) -> str:  # pragma: no cover - reporting
        lines = [
            f"{self.wall_ticks} wall ticks over {len(self.cases)} case(s); "
            f"man-ticks: corridor {self.corridor_ticks} + nodes {self.node_ticks} "
            f"= {self.total_ticks}",
            "",
            "per man (the program is only as fast as its least idle man):",
        ]
        for mid in self.bottleneck_men():
            man = self.men[mid]
            share = 100.0 * man.corridor / man.total if man.total else 0.0
            idle = 100.0 * man.blocked / man.total if man.total else 0.0
            lines.append(
                f"  man {mid}: {man.total} ticks — corridor {man.corridor} ({share:.1f}%), "
                f"blocked {man.blocked} ({idle:.1f}% idle)"
            )
        lead = self.bottleneck_men()[0] if self.men else None
        if lead is not None:
            man = self.men[lead]
            ceiling = 100.0 * man.corridor / self.wall_ticks if self.wall_ticks else 0.0
            lines.append(
                f"  -> man {lead} paces the program; his corridor is {ceiling:.1f}% "
                "of wall ticks, the honest reroute ceiling"
            )
        lines.append("")
        lines.append(f"hottest {limit} corridors:")
        for edge_id, cost in self.hot_edges(limit):
            edge = self.graph.edges[edge_id]
            src = self.graph.nodes[edge.src]
            dst = "dead" if edge.dst is None else repr(self.graph.nodes[edge.dst].glyph)
            walkers = sorted(self.edge_men.get(edge_id, ()))
            men = ",".join(str(m) for m in walkers[:3]) + (
                f"+{len(walkers) - 3}" if len(walkers) > 3 else ""
            )
            lines.append(
                f"  e{edge_id:<4d} man{men} {src.glyph!r}@{src.pos[0]},{src.pos[1]}"
                f" arm={edge.arm:+d} -> {dst}"
                f"  len={edge.length:<3d} x{cost.traffic:<7d} = {cost.ticks} ticks"
            )
        return "\n".join(lines)


def resume_index(graph: FlowGraph) -> dict[tuple[Cell, Dir], tuple[int, int]]:
    """Where in the graph a man standing on ``(cell, direction)`` is.

    A ``Y`` fork puts its child down in the middle of a corridor rather than on
    an instruction, so a child runner's trace does not begin at a node.  This
    maps every corridor cell back to the edge and offset it belongs to, which is
    what lets those runners be profiled at all — and they are not a curiosity,
    they are how the matmul and reverse-a-list designs work.
    """
    index: dict[tuple[Cell, Dir], tuple[int, int]] = {}
    for edge in graph.edges:
        in_dir = edge.exit_dir
        for i, cell in enumerate(edge.cells):
            nxt = edge.cells[i + 1] if i + 1 < len(edge.cells) else None
            index.setdefault((cell, in_dir), (edge.id, i))
            if nxt is not None:
                in_dir = (
                    1 if nxt[0] > cell[0] else -1 if nxt[0] < cell[0] else 0,
                    1 if nxt[1] > cell[1] else -1 if nxt[1] < cell[1] else 0,
                )  # type: ignore[assignment]
            else:
                in_dir = edge.entry_dir
    return index


def _walk_trace(
    graph: FlowGraph,
    start_node: int,
    states: Sequence[tuple[Cell, Dir]],
    edge_walks: dict[int, int],
    node_cost: dict[int, NodeCost],
    mismatches: list[str],
    man: ManCost,
    runner_id: int,
    edge_men: dict[int, set[int]],
    *,
    start_edge: tuple[int, int] | None = None,
) -> None:
    """Replay one runner's trace against the graph, folding it into ``man``.

    Every tick is attributed to exactly one of corridor or node, so the split is
    exhaustive by construction — which is also how a bad graph gets caught: the
    replay stops matching and says where.
    """
    nodes = graph.nodes
    edges = graph.edges
    i = 0
    n = len(states)
    node_id = start_node

    if start_edge is not None:
        # A forked child begins part-way along a corridor: walk out the rest of
        # it before the normal node-to-node loop can take over.
        edge_id, offset = start_edge
        edge = edges[edge_id]
        for cell in edge.cells[offset:]:
            if i >= n:
                break
            if states[i][0] != cell:
                mismatches.append(
                    f"runner {runner_id} joined edge #{edge_id} at {offset} "
                    f"but tick {i} is {states[i][0]}, not {cell}"
                )
                return
            man.corridor += 1
            i += 1
            while i < n and states[i][0] == cell:
                man.corridor += 1
                man.blocked += 1
                i += 1
        edge_walks[edge_id] = edge_walks.get(edge_id, 0) + 1
        edge_men.setdefault(edge_id, set()).add(runner_id)
        if edge.dst is None:
            return
        node_id = edge.dst

    while i < n:
        node = nodes[node_id]
        run = node.literal_run or (node.pos,)
        # The node's own cells, plus any blocked repeats on them.
        for expect in run:
            if i >= n:
                break
            if states[i][0] != expect:
                mismatches.append(
                    f"node #{node_id} expected {expect} at tick {i}, saw {states[i][0]}"
                )
                return
            cost = node_cost.setdefault(node_id, NodeCost())
            cost.visits += 1
            man.node += 1
            i += 1
            while i < n and states[i][0] == expect:
                cost.blocked += 1
                man.node += 1
                man.blocked += 1
                i += 1
        if i >= n:
            break

        # Which arm did he leave on?  The next cell tells us, and that is the
        # only runtime fact the graph needs: everything else is geometry.
        nxt = states[i]
        chosen = None
        for edge_id in node.out_edges:
            edge = edges[edge_id]
            first = edge.cells[0] if edge.cells else (
                nodes[edge.dst].pos if edge.dst is not None else None
            )
            if first == nxt[0]:
                chosen = edge
                break
        if chosen is None:
            mismatches.append(
                f"node #{node_id} ({node.glyph!r}@{node.pos}) has no arm reaching {nxt[0]}"
            )
            return

        for cell in chosen.cells:
            if i >= n:
                break
            if states[i][0] != cell:
                mismatches.append(
                    f"edge #{chosen.id} expected corridor {cell} at tick {i}, saw {states[i][0]}"
                )
                return
            man.corridor += 1
            i += 1
            # Corridor cells cannot block, but a man can be held by a Y fork.
            while i < n and states[i][0] == cell:
                man.corridor += 1
                man.blocked += 1
                i += 1
        edge_walks[chosen.id] = edge_walks.get(chosen.id, 0) + 1
        edge_men.setdefault(chosen.id, set()).add(runner_id)
        if chosen.dst is None:
            break
        node_id = chosen.dst


def profile_program(
    program: str | Path | Sequence[str] | FastLittleman,
    problem: str | os.PathLike[str] | dict[str, Any],
    *,
    graph: FlowGraph | None = None,
    tick_cap: int = scoring.DEFAULT_TICK_CAP,
    case_names: Iterable[str] | None = None,
) -> Profile:
    """Trace every public case and fold the walks onto the flow graph.

    The result is deterministic: the engine is deterministic and the cases come
    from the problem file in order.
    """
    prog = program if isinstance(program, FastLittleman) else FastLittleman(program)
    graph = graph or build_flow_graph(prog)
    prob = scoring.load_problem(problem)
    cases = prob.get("publicTestData") or []
    if case_names is not None:
        wanted = set(case_names)
        cases = [c for c in cases if c.get("name") in wanted]

    from .optimize import _expected_frames  # local: optimize imports us back

    edge_walks: dict[int, int] = {}
    node_cost: dict[int, NodeCost] = {}
    profile = Profile(graph=graph, edges={}, nodes={})
    resume = resume_index(graph)

    for index, case in enumerate(cases):
        name = case.get("name") or f"case-{index}"
        inp = scoring._case_input(case)
        display = scoring._is_display_case(case)
        frames = _expected_frames(case) if display else None
        expected = None
        if not display:
            expected = " / ".join(
                " ".join(str(v) for v in (r.get("out") or []))
                for r in scoring._rounds(case)
            )
        try:
            result, trace = trace_case(
                prog,
                input=inp,
                expected=expected,
                frames=frames,
                max_ticks=tick_cap,
            )
        except FastLittlemanError as exc:
            profile.mismatches.append(f"{name}: {exc}")
            continue

        passed = result.passed is not False and result.fatal is None
        profile.cases.append((name, result.step, passed))

        for runner_id, states in sorted(trace.items()):
            if not states:
                continue
            start = graph.node_at(states[0][0], states[0][1])
            start_edge = None
            if start is None:
                start_edge = resume.get((states[0][0], states[0][1]))
                if start_edge is None:
                    profile.mismatches.append(
                        f"{name}: runner {runner_id} starts off-graph at {states[0]}"
                    )
                    continue
            man = profile.men.setdefault(runner_id, ManCost())
            before = (man.corridor, man.node)
            _walk_trace(
                graph,
                start.id if start is not None else -1,
                states,
                edge_walks,
                node_cost,
                profile.mismatches,
                man,
                runner_id,
                profile.edge_men,
                start_edge=start_edge,
            )
            profile.corridor_ticks += man.corridor - before[0]
            profile.node_ticks += man.node - before[1]

    for edge_id, walks in edge_walks.items():
        cost = EdgeCost()
        cost.add(walks, graph.edges[edge_id].length)
        profile.edges[edge_id] = cost
    profile.nodes = node_cost
    return profile


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m randomfun2026solvers.manprofile",
        description="Report how many ticks flow through each corridor of a .man grid.",
    )
    parser.add_argument("program", type=Path)
    parser.add_argument("problem", help="problem slug or tasks/problems/*.json")
    parser.add_argument("--tick-cap", type=int, default=scoring.DEFAULT_TICK_CAP)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args(argv)

    profile = profile_program(args.program, args.problem, tick_cap=args.tick_cap)
    for name, ticks, passed in profile.cases:
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {ticks} ticks")
    print()
    print(profile.report(args.top))
    if profile.mismatches:
        print()
        print(f"{len(profile.mismatches)} trace mismatch(es):")
        for line in profile.mismatches[:10]:
            print(f"  {line}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
