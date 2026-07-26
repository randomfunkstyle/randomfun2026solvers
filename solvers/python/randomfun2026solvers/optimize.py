"""Optimize a littleman ``.man`` program: shrink its contest score while keeping
it correct.

Score is ``max(w, h)² × avg_ticks`` (or ``max(w, h)²`` for ``footprint``
problems), so the biggest lever is footprint — squeezing a wide-and-short or
tall-and-narrow grid toward a small square. This module runs a set of
correctness-preserving transformation passes (:data:`PASSES`) under a heuristic
search, keeping the best grid that still passes every public test case.

Two gates protect correctness:

* :func:`verify` — the semantic gate. Runs each public case through the fast
  independent in-memory engine (differentially tested against
  :meth:`Littleman.judge`) and requires the emitted output or display frames to
  match exactly. An explicit ``Littleman`` or ``LM_VALIDATOR=reference`` selects
  the reference Node/WASM engine instead. A candidate that fails verification is
  never accepted (worst case, the optimizer returns the input unchanged).
* :func:`bindings_preserved` — a structural accept gate for re-layout moves.
  Before accepting a moved grid it re-checks, via the ``route`` oracle, that every
  send/recv instruction still binds to the *same* pipe (SPEC "nearest, not
  nearest-ready"), catching a silent re-bind that might pass the public cases but
  fail private ones. It re-parses and re-routes through the Node oracle, so the
  driver runs it only once a candidate has already passed :func:`verify` and beaten
  the score — never on the many candidates rejected earlier and more cheaply.

The cheap inner-loop proxy is :func:`footprint` alone (instant, deterministic);
the full judged score is computed only when a move is otherwise accepted.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

from . import scoring
from .fast_littleman import FastLittleman, FastLittlemanError
from .layout import (
    AStarRouter,
    Canvas,
    Cell,
    Graph,
    LayoutEngine,
    LayoutError,
    Placed,
    Placement,
    Route,
    _resolve_port,
)
from .littleman import Littleman, LittlemanError
from .manparse import Program, parse_program
from .scoring import footprint, load_problem

__all__ = [
    "OptimizeError",
    "CaseVerdict",
    "VerifyResult",
    "verify",
    "score_grid",
    "bindings_preserved",
    "Candidate",
    "CompactPlacement",
    "CapacityRouter",
    "trim_margins",
    "relayout",
    "relayout_keep_capacity",
    "PASSES",
    "semantic_passes",
    "OptimizeResult",
    "optimize",
    "main",
]


class OptimizeError(RuntimeError):
    """A program could not be optimized/verified (load fatal, missing engine)."""


# ── fitness: verify + score ───────────────────────────────────────────────────
def _expected_string(case: dict[str, Any]) -> str:
    """Expected output for the engine (rounds separated by ``/``), mirroring input."""
    parts = [" ".join(str(t) for t in r.get("out", []) or []) for r in scoring._rounds(case)]
    return " / ".join(parts)


def _expected_flat(case: dict[str, Any]) -> list[int]:
    return [int(t) for r in scoring._rounds(case) for t in (r.get("out", []) or [])]


def _expected_frames(case: dict[str, Any]) -> list[list[list[str]]]:
    """Expected display frames, nested per round — the engine's ``--frames`` shape."""
    return [
        [[str(row) for row in frame] for frame in (r.get("frames") or [])]
        for r in scoring._rounds(case)
    ]


@dataclass
class CaseVerdict:
    name: str
    passed: bool
    ticks: int
    detail: str = ""


@dataclass
class VerifyResult:
    passed: bool
    cases: list[CaseVerdict] = field(default_factory=list)
    avg_ticks: float | None = None
    # True when some case is display-judged (frames): the *pass* is exact (the
    # engine compares every committed frame), but the tick figure is the settle
    # tick rather than the tick the final frame matched.
    approx: bool = False

    @property
    def n_passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)


def _grid_source(grid: str | Sequence[str] | os.PathLike[str]) -> str:
    """Coerce a grid (rows, joined string, or path) to inline source text."""
    if isinstance(grid, os.PathLike):
        return Path(os.fspath(grid)).read_text(encoding="utf-8")
    if isinstance(grid, str):
        if "\n" not in grid and Path(grid).is_file():
            return Path(grid).read_text(encoding="utf-8")
        return grid
    return "\n".join(grid)


def verify(
    grid: str | Sequence[str] | os.PathLike[str],
    problem: str | os.PathLike[str] | dict[str, Any],
    *,
    lm: Littleman | None = None,
    tick_cap: int = scoring.DEFAULT_TICK_CAP,
) -> VerifyResult:
    """Run every public case through the engine; require exact output match.

    Display cases (``frames``) are judged by the engine frame for frame: the
    expected frames go in with ``--frames`` and the run passes when every
    committed frame matched *and* nothing was written to program output (a
    display problem emitting output is an error). ``avg_ticks`` averages the
    settle tick over all cases.
    """
    prob = load_problem(problem)
    cases = prob.get("publicTestData") or []
    source = _grid_source(grid)
    # An explicitly supplied Littleman is commonly a test double and always
    # means "use this backend".  Otherwise the independent in-memory engine
    # handles both output and display problems and avoids one Node/WASM boot per
    # case.
    use_fast = (
        lm is None
        and os.environ.get("LM_VALIDATOR", "fast").lower() != "reference"
    )
    fast: FastLittleman | None = None
    fast_load_error: FastLittlemanError | None = None
    if use_fast:
        try:
            fast = FastLittleman(source)
        except FastLittlemanError as exc:
            fast_load_error = exc
    if not use_fast:
        lm = lm or Littleman()

    verdicts: list[CaseVerdict] = []
    total_ticks = 0
    approx = False
    all_passed = bool(cases)
    for case in cases:
        name = case.get("name", "?")
        inp = scoring._case_input(case)
        display = scoring._is_display_case(case)
        if fast_load_error is not None:
            verdicts.append(CaseVerdict(name, False, 0, f"engine: {fast_load_error}"))
            all_passed = False
            continue
        try:
            if fast is not None:
                result = (
                    fast.run(inp, frames=_expected_frames(case), max_ticks=tick_cap)
                    if display
                    else fast.run(
                        inp,
                        expected=_expected_string(case),
                        max_ticks=tick_cap,
                    )
                )
                if result.fatal is not None:
                    verdicts.append(
                        CaseVerdict(name, False, result.step, f"fatal: {result.fatal}")
                    )
                    all_passed = False
                    continue
                want = [] if display else _expected_flat(case)
                ok = result.passed is True and result.output == want
                detail = "" if ok else (
                    f"emitted output on a display problem: {result.output}"
                    if display and result.output
                    else f"output {result.output} != expected {want}"
                )
                verdicts.append(CaseVerdict(name, ok, result.step, detail))
                total_ticks += result.step
                all_passed = all_passed and ok
                continue
            if display:
                assert lm is not None
                snap = lm.judge(
                    source, input=inp, frames=_expected_frames(case), max_ticks=tick_cap
                )
            else:
                assert lm is not None
                snap = lm.judge(
                    source, input=inp, expected=_expected_string(case), max_ticks=tick_cap
                )
        except (LittlemanError, FastLittlemanError) as exc:
            verdicts.append(CaseVerdict(name, False, 0, f"engine: {exc}"))
            all_passed = False
            continue

        if snap.fatal is not None:
            verdicts.append(CaseVerdict(name, False, snap.step, f"fatal: {snap.fatal.reason}"))
            all_passed = False
            continue

        if display:
            approx = True  # the *tick* is the settle tick, not the final-frame tick
            judged = snap.frame_judge
            ok = judged is not None and judged.passed and not snap.output
            if snap.output:
                detail = f"emitted output on a display problem: {snap.output}"
            elif judged is None:
                detail = "engine reported no frame verdict"
            elif not judged.passed:
                detail = f"{judged.matched}/{judged.total} frames matched: {judged.mismatch}"
            else:
                detail = ""
        else:
            want = _expected_flat(case)
            ok = list(snap.output) == want
            detail = "" if ok else f"output {snap.output} != expected {want}"
        verdicts.append(CaseVerdict(name, ok, snap.step, detail))
        total_ticks += snap.step
        all_passed = all_passed and ok

    avg = total_ticks / len(verdicts) if verdicts else None
    return VerifyResult(passed=all_passed, cases=verdicts, avg_ticks=avg, approx=approx)


def score_grid(
    grid: str | Sequence[str],
    problem: str | os.PathLike[str] | dict[str, Any],
    *,
    result: VerifyResult | None = None,
    lm: Littleman | None = None,
) -> float | None:
    """Real contest score for a grid: ``area2 × avg_ticks`` (or ``area2``).

    Returns ``None`` if the grid does not pass verification. Pass a precomputed
    :class:`VerifyResult` to avoid re-running the engine.
    """
    prob = load_problem(problem)
    source = _grid_source(grid)
    _, _, area2 = footprint(source)
    if prob.get("scoring") == "footprint":
        return float(area2)
    result = result or verify(source, prob, lm=lm)
    if not result.passed or result.avg_ticks is None:
        return None
    return area2 * result.avg_ticks


# ── binding oracle ────────────────────────────────────────────────────────────
# A pipe's logical identity must survive re-analysis (which renumbers rooms by
# position). We key it by the *rooms it connects*, and a room by its kind + exact
# interior — invariant under a re-layout, which only moves rooms, never edits them.
Key = tuple[str, tuple[str, ...]]
LogicalPipe = tuple[Key | None, Key | None]


def _room_key(prog: Program, room_id: int) -> Key | None:
    for r in prog.rooms:
        if r.id == room_id:
            return (r.kind, tuple(r.content))
    return None


def _logical_pipe(prog: Program, pipe_id: int) -> LogicalPipe | None:
    for p in prog.pipes:
        if p.id == pipe_id:
            return (_room_key(prog, p.src), _room_key(prog, p.dst))
    return None


def bindings_preserved(
    orig: Program,
    placement: dict[str, Cell],
    cand_grid: str | Sequence[str],
    *,
    lm: Littleman | None = None,
) -> bool:
    """True if every send/recv still binds to the same logical pipe after a move.

    ``placement`` maps a container id (``str(room.id)``) to its new top-left
    offset in the candidate grid; room interiors are unchanged (only
    repositioned), so each pipe-op cell maps by its local offset. Pipe identity
    is compared as the (kind, interior) of the rooms it connects — stable across
    the re-analyze that renumbers rooms.
    """
    lm = lm or Littleman()
    source = _grid_source(cand_grid)
    cand = parse_program(source, lm=lm)
    cand_logical_of_cell: dict[Cell, LogicalPipe] = {}
    for p in cand.pipes:
        lg = (_room_key(cand, p.src), _room_key(cand, p.dst))
        for c in p.cells:
            cand_logical_of_cell[c] = lg

    for room in orig.rooms:
        new_off = placement.get(str(room.id))
        if new_off is None:
            continue
        ox0, oy0 = room.min_
        nx0, ny0 = new_off
        for op in room.pipe_ops:
            want = _logical_pipe(orig, op.pipe_id)
            if want is None:
                continue
            lx, ly = op.cell[0] - ox0, op.cell[1] - oy0
            targeted = lm.route(source, nx0 + lx, ny0 + ly)
            got: LogicalPipe | None = None
            for c in targeted:
                got = cand_logical_of_cell.get(c.as_tuple())
                if got is not None:
                    break
            if got != want:
                return False
    return True


# ── placement: shelf pack toward a target width ───────────────────────────────
class CompactPlacement:
    """Shelf-pack containers into rows, wrapping past ``target_width``.

    Implements :class:`layout.PlacementStrategy`. Containers are ordered by a
    topological sort (so wires mostly flow forward) and laid left→right; when a
    row would exceed ``target_width`` it wraps to a new shelf below. Varying
    ``target_width`` trades width for height — the driver sweeps it to find the
    packing with the smallest ``max(width, height)`` that still routes.
    """

    def __init__(self, target_width: int, *, h_gap: int = 4, v_gap: int = 3) -> None:
        self.target_width = target_width
        self.h_gap = h_gap
        self.v_gap = v_gap

    def _order(self, graph: Graph) -> list[str]:
        g = nx.DiGraph()
        g.add_nodes_from(c.id for c in graph.containers)
        for e in graph.edges:
            g.add_edge(e.src, e.dst)
        try:
            return list(nx.topological_sort(g))
        except nx.NetworkXUnfeasible:
            return [c.id for c in graph.containers]

    def place(self, graph: Graph) -> Placement:
        by_id = graph.by_id
        placement: Placement = {}
        x = y = 0
        shelf_h = 0
        for cid in self._order(graph):
            c = by_id[cid]
            if x > 0 and x + c.width > self.target_width:
                x = 0
                y += shelf_h + self.v_gap
                shelf_h = 0
            placement[cid] = Placed(offset=(x, y), variant_index=0)
            x += c.width + self.h_gap
            shelf_h = max(shelf_h, c.height)
        return placement


# ── routing: capacity-preserving (never shorten a pipe below its original) ─────
class CapacityRouter(AStarRouter):
    r"""A* router that pads each pipe back up to at least its original length.

    A littleman pipe's capacity **is** its length; programs like ``memory`` use
    long pipes as shift-register buffers, so a compacted (shorter) re-route
    deadlocks. This router routes shortest, then folds compact U-detours into
    free cells until each pipe reaches ``min_len[edge_id]`` (overshoot is fine —
    extra buffer never deadlocks a producer). If a pipe cannot be padded (no free
    space), the whole layout is rejected. Room interiors are untouched, so with
    every pipe ≥ its original length the program behaves as before, only smaller.
    """

    def __init__(self, min_len: dict[str, int], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.min_len = min_len

    def _route_edge(self, e, canvas, placement, by_id, bounds):  # type: ignore[override]
        src, dst = by_id[e.src], by_id[e.dst]
        src_off, dst_off = placement[e.src].offset, placement[e.dst].offset
        src_center = (src_off[0] + src.width // 2, src_off[1] + src.height // 2)
        dst_center = (dst_off[0] + dst.width // 2, dst_off[1] + dst.height // 2)

        out_local = src.outputs[e.src_output]
        in_local = dst.inputs[e.dst_input]
        src_touch, src_dir = self._exit(src, src_off, out_local, dst_center)
        dst_touch, dst_dir = self._exit(dst, dst_off, in_local, src_center)

        c0 = (src_touch[0] + src_dir[0], src_touch[1] + src_dir[1])
        c1 = (c0[0] + src_dir[0], c0[1] + src_dir[1])
        cn = (dst_touch[0] + dst_dir[0], dst_touch[1] + dst_dir[1])
        cn_1 = (cn[0] + dst_dir[0], cn[1] + dst_dir[1])
        for cell in (c0, c1, cn, cn_1):
            if not canvas.is_free(cell):
                raise LayoutError(f"edge {e.id!r}: no room to attach pipe at {cell}")

        inner = self._astar(c1, cn_1, canvas, bounds, {c0, cn})
        if inner is None:
            raise LayoutError(f"edge {e.id!r}: no free path from {c1} to {cn_1}")
        path = [c0, *inner, cn]

        target = self.min_len.get(e.id, 0)
        if len(path) < target:
            padded = self._pad_path(path, target, canvas, bounds)
            if padded is None:
                raise LayoutError(f"edge {e.id!r}: cannot preserve capacity (need length {target})")
            path = padded

        outs = self._ports_global(src, src_off, "outputs")
        ins = self._ports_global(dst, dst_off, "inputs")
        got_out = _resolve_port(c0, outs)
        got_in = _resolve_port(cn, ins)
        if got_out != e.src_output or got_in != e.dst_input:
            raise LayoutError(f"edge {e.id!r}: pipe re-bound during routing")

        self._draw(path, canvas)
        return Route(edge_id=e.id, path=path, resolved_output=got_out, resolved_input=got_in)

    def _pad_path(
        self, path: list[Cell], target: int, canvas: Canvas, bounds: tuple[int, int, int, int]
    ) -> list[Cell] | None:
        """Lengthen ``path`` to ≥ ``target`` by folding U-detours into free cells.

        Each detour on a unit step ``b→c`` runs ``k`` cells perpendicular, one
        step across, then ``k`` back — adding ``2k`` cells with valid turns. We
        greedily place the largest feasible detour, so even long buffers are
        reached in a few folds.
        """
        min_x, min_y, max_x, max_y = bounds
        used = set(path)

        def usable(c: Cell) -> bool:
            return (
                min_x <= c[0] <= max_x
                and min_y <= c[1] <= max_y
                and c not in used
                and canvas.is_free(c)
            )

        path = list(path)
        guard = 0
        while len(path) < target and guard < 10_000:
            guard += 1
            need = target - len(path)
            best: tuple[int, int, list[Cell]] | None = None  # (k, insert_at, detour cells)
            for i in range(len(path) - 1):
                b, c = path[i], path[i + 1]
                d = (c[0] - b[0], c[1] - b[1])
                if abs(d[0]) + abs(d[1]) != 1:
                    continue  # b→c must be a unit step
                for p in ((-d[1], d[0]), (d[1], -d[0])):
                    col_b: list[Cell] = []
                    col_c: list[Cell] = []
                    k_max = (need + 1) // 2
                    for kk in range(1, k_max + 1):
                        bc = (b[0] + p[0] * kk, b[1] + p[1] * kk)
                        cc = (c[0] + p[0] * kk, c[1] + p[1] * kk)
                        if usable(bc) and usable(cc):
                            col_b.append(bc)
                            col_c.append(cc)
                        else:
                            break
                    if col_b and (best is None or len(col_b) > best[0]):
                        best = (len(col_b), i, col_b + col_c[::-1])
            if best is None:
                return None
            _, i, detour = best
            path[i + 1 : i + 1] = detour
            used.update(detour)
        return path if len(path) >= target else None


# ── transformation passes ─────────────────────────────────────────────────────
@dataclass
class Candidate:
    """A proposed grid, with the placement map that produced it (for binding check)."""

    grid: list[str]
    placement: dict[str, Cell] | None = None
    label: str = ""


def trim_margins(prog: Program) -> list[Candidate]:
    """Crop fully-blank border rows/columns — a uniform shift, geometry-preserving."""
    rows = prog.to_grid()
    if not rows:
        return []
    cells = [(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch != " "]
    if not cells:
        return []
    min_x = min(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    max_y = max(y for _, y in cells)
    cropped = [rows[y][min_x:].rstrip() for y in range(min_y, max_y + 1)]
    if cropped == rows:
        return []
    return [Candidate(grid=cropped, label="trim")]


def _candidate_widths(graph: Graph) -> list[int]:
    """Target widths to sweep: every width from the widest container to a full row.

    Score is ``max(w, h)²``, so the winning packing is usually a *specific* width
    where the shelves happen to square up -- sampling a handful of widths walks
    right past it. The sweep is exhaustive because each packing is cheap (place +
    route); the expensive engine verification is gated by :func:`_relayout`'s
    footprint filter, not by keeping this list short.
    """
    widths = [c.width for c in graph.containers]
    if not widths:
        return []
    min_w = max(widths)
    row_w = sum(widths) + 4 * max(0, len(widths) - 1)
    return list(range(min_w, max(min_w, row_w) + 1))


# Shelf gaps to sweep, tightest first. The engine permits rooms to abut with no
# gap at all, and a pipe may attach at a room's corner, so a generous gap is pure
# wasted footprint -- but a too-tight gap starves the router of attach room (each
# pipe end needs a 2-cell stub), so the tight packings often fail to route and we
# need the looser ones as fallbacks.
_GAPS: tuple[tuple[int, int], ...] = ((0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (2, 2), (4, 3))

# How many distinct packings survive to engine verification. Verifying one
# candidate means running every public case, so this is the real cost knob.
_MAX_CANDIDATES = 16


def _placed_span_area2(placement: Placement, graph: Graph) -> int:
    """``max(w, h)²`` of the container bounding box of a placement, ignoring pipes.

    Cheap (no routing): just the span of the placed blocks. The routed grid must
    contain every container, so this is a **lower bound** on the final footprint —
    if it already can't beat the base, routing (which only ever grows the box with
    pipes) can't either, so the caller skips the expensive route.
    """
    by_id = graph.by_id
    xs0: list[int] = []
    ys0: list[int] = []
    xs1: list[int] = []
    ys1: list[int] = []
    for cid, placed in placement.items():
        c = by_id[cid].variant(placed.variant_index)
        ox, oy = placed.offset
        xs0.append(ox)
        ys0.append(oy)
        xs1.append(ox + c.width)
        ys1.append(oy + c.height)
    if not xs0:
        return 0
    w = max(xs1) - min(xs0)
    h = max(ys1) - min(ys0)
    return max(w, h) ** 2


def _relayout(prog: Program, router, tag: str) -> list[Candidate]:
    """Sweep gaps × target widths; return the smallest-footprint packings that route.

    Every (gap, width) pair is *placed* (cheap), pruned on its container-span
    footprint lower bound, deduplicated, and only the survivors are *routed* (the
    expensive step). The routed packings are ranked by ``area2`` and cut to
    :data:`_MAX_CANDIDATES`, so engine verification only pays for packings that
    could actually win. Two prunes make this affordable on large archives, where
    the width sweep is hundreds wide: a placement whose bare container span already
    reaches the base footprint can never beat it once pipes are added
    (:func:`_placed_span_area2` — a lower bound, so this is exact, never drops a
    winner), and the many target widths that shelf-pack to the *same* placement are
    routed once. Candidates no smaller than the current grid are dropped: footprint
    is squared, so a bigger box essentially never wins back the difference on ticks.
    """
    graph = prog.to_graph()
    _, _, base_area2 = footprint("\n".join(prog.to_grid()))
    ranked: list[tuple[int, Candidate]] = []
    seen: set[tuple[str, ...]] = set()
    seen_place: set[tuple[tuple[str, Cell], ...]] = set()
    for h_gap, v_gap in _GAPS:
        for w in _candidate_widths(graph):
            strat = CompactPlacement(w, h_gap=h_gap, v_gap=v_gap)
            try:
                placement = strat.place(graph)
            except LayoutError:
                continue
            # Cheap prune #1: container-span footprint is a lower bound on the
            # routed footprint — no point routing a placement that already lost.
            if _placed_span_area2(placement, graph) >= base_area2:
                continue
            # Cheap prune #2: distinct target widths often shelf-pack identically;
            # route each unique placement only once.
            pkey = tuple(sorted((cid, pl.offset) for cid, pl in placement.items()))
            if pkey in seen_place:
                continue
            seen_place.add(pkey)
            engine = LayoutEngine(placement=strat, router=router)
            try:
                layout = engine.run(graph)
            except LayoutError:
                continue
            grid = list(layout.grid)
            key = tuple(grid)
            if key in seen:
                continue
            seen.add(key)
            _, _, area2 = footprint("\n".join(grid))
            if area2 >= base_area2:
                continue
            # placement offsets are relative to the grid origin (layout.origin).
            ox, oy = layout.origin
            placement = {
                cid: (pl.offset[0] - ox, pl.offset[1] - oy) for cid, pl in layout.placement.items()
            }
            label = f"{tag}:w={w},gap={h_gap}/{v_gap},area2={area2}"
            ranked.append((area2, Candidate(grid=grid, placement=placement, label=label)))
    ranked.sort(key=lambda pair: pair[0])
    return [cand for _, cand in ranked[:_MAX_CANDIDATES]]


def relayout(prog: Program) -> list[Candidate]:
    """Re-place rooms + re-route pipes (shortest), sweeping target widths.

    Best for footprint-bound programs (wide-and-short pipelines). Shortens pipes,
    so it breaks programs whose pipe length is load-bearing — those candidates
    just fail verification. Skipped for display programs (pipe *side* semantics
    are not yet constrained by the router).
    """
    if prog.has_display or not prog.pipes:
        return []
    return _relayout(prog, AStarRouter(margin=8), "relayout")


def relayout_keep_capacity(prog: Program) -> list[Candidate]:
    """Re-place rooms but pad every pipe back to ≥ its original length.

    For buffer-bound programs: compacting the *rooms* shrinks the box, while
    :class:`CapacityRouter` keeps each pipe at least as long as before, preserving
    the shift-register capacity so behaviour is unchanged. Note: a room with two
    same-direction pipes (e.g. ``memory``'s main room, 2 out + 2 in) can still be
    re-bound by the move — :func:`bindings_preserved` catches that and the driver
    rejects it. Binding-aware attach placement (forcing each pipe to exit/enter on
    its original side) is the next lever for that case.
    """
    if prog.has_display or not prog.pipes:
        return []
    min_len = {f"p{p.id}": len(p.cells) for p in prog.pipes}
    # Generous working margin so long pipes have free cells to fold coils into;
    # the rendered grid crops to actually-used cells, so unused margin is free.
    margin = max(12, max((len(p.cells) for p in prog.pipes), default=0))
    return _relayout(prog, CapacityRouter(min_len, margin=margin), "relayout-cap")


# Ordered pipeline. Cheap/safe first, then the footprint levers. The list is the
# extension point for further passes (tick/peephole rules) — each just returns
# Candidates; the driver verifies and keeps only score-reducing, correct ones.
PASSES = [trim_margins, relayout, relayout_keep_capacity]


# ── semantic (content-rewrite) passes: OPT-IN ─────────────────────────────────
# The rule-catalog families, ordered for the driver. Geometric passes (PASSES)
# run FIRST — they only move rooms, so they compact the box every content rewrite
# then works inside. The pure content rewrites come next (arith/const/steer/io/pipe:
# constant-fold, identity elision, steer coalescing, dead-panel/pipe reshapes — none
# grow the footprint). The loop family is LAST because its win, unrolling, *enlarges*
# the box (more ticks/lap traded for fewer laps): placing it after the compaction and
# the neutral rewrites lets the strict-`<` driver judge that footprint↔ticks trade
# against an already-minimal grid, and accept the unroll only when the tick saving
# actually pays for the squared footprint growth. ``loop.mirror_horizontal`` is inert
# on its own (it needs a man re-router the rule does not carry); it is left enabled
# because the driver's verify simply rejects the unusable candidate — no regression.
_SEMANTIC_FAMILY_ORDER: tuple[str, ...] = ("arith", "const", "steer", "io", "pipe", "loop")


def semantic_passes() -> list[Any]:
    """The content-rewrite passes, one per rule family, in driver order.

    Each is a :func:`manrewrite.rule_pass` that recognises its family's patterns and
    emits rewritten grids (``placement=None``; gated by ``verify`` alone). Building them
    imports the whole rule catalog (incl. the cross-family macros in ``rules_macros``, so
    the flagship ``loop.const_unroll`` is reachable). Opt-in: pass ``semantic=True`` to
    :func:`optimize`, or splice this list into ``passes`` yourself (as the benchmark does)
    — :data:`PASSES` is unchanged, so default behaviour is untouched.
    """
    from .manrewrite import rule_pass

    return [rule_pass(fam) for fam in _SEMANTIC_FAMILY_ORDER]


# ── driver ────────────────────────────────────────────────────────────────────
@dataclass
class OptimizeResult:
    grid: list[str]
    score: float | None
    base_score: float | None
    base_grid: list[str]
    passed: bool
    log: list[str] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        return (
            self.score is not None and self.base_score is not None and self.score < self.base_score
        )

    def render(self) -> str:
        return "\n".join(self.grid)


def optimize(
    program: str | os.PathLike[str],
    problem: str | os.PathLike[str] | dict[str, Any],
    *,
    lm: Littleman | None = None,
    passes: Sequence[Any] = PASSES,
    semantic: bool = False,
    max_sweeps: int = 3,
    tick_cap: int = scoring.DEFAULT_TICK_CAP,
) -> OptimizeResult:
    """Search for a lower-scoring grid that still passes every public case.

    Greedy local search: each sweep runs every pass over the current best grid,
    verifying candidates with the engine and accepting any that strictly lowers
    the real score. Repeats until a sweep makes no progress (or ``max_sweeps``).
    Never returns a grid that fails verification — worst case, the input.

    ``semantic=True`` appends the content-rewrite passes (:func:`semantic_passes`) after
    ``passes``, opting into the rule catalog. It is off by default so the geometric-only
    behaviour every existing caller relies on is unchanged; the same strict accept gate
    guards the extra candidates, so enabling it can only find wins, never regress.
    """
    # Correctness (verify) runs on the in-process FastLittleman by default — the
    # backend AGENTS.md/`manbench` document, differentially verdict-identical to the
    # reference and 5–239× faster (no Node boot per public case). `lm` (the reference
    # Node/WASM oracle) is still needed for parse/analyze/route and `bindings_preserved`,
    # but forwarding it into `verify` would silently force every candidate through the
    # slow path. So verify gets the caller's `lm` ONLY when they explicitly supplied one
    # (a test double / a deliberate `reference` pin); otherwise `None`, so verify picks
    # FastLittleman (still honouring `LM_VALIDATOR=reference`).
    verify_lm = lm
    lm = lm or Littleman()
    prob = load_problem(problem)

    passes = list(passes)
    if semantic:
        passes = passes + semantic_passes()

    # Memoise verification by grid text (and tick cap): distinct passes and successive
    # sweeps can re-emit an identical grid, and re-running every public case on it is
    # pure waste. A grid's verdict is a pure function of (grid, problem, cap), so this
    # never changes a decision — it only skips repeat engine work within this call.
    verify_cache: dict[tuple[str, int], VerifyResult] = {}

    def _verify(grid: list[str], cap: int) -> VerifyResult:
        key = ("\n".join(grid), cap)
        cached = verify_cache.get(key)
        if cached is None:
            cached = verify(key[0], prob, lm=verify_lm, tick_cap=cap)
            verify_cache[key] = cached
        return cached

    prog = parse_program(program, lm=lm)
    best_grid = prog.to_grid()
    base_res = _verify(best_grid, tick_cap)
    if not base_res.passed:
        raise OptimizeError(
            "input program does not pass its own public cases; refusing to optimize"
        )
    base_score = score_grid(best_grid, prob, result=base_res, lm=verify_lm)
    best_score = base_score
    log: list[str] = [f"baseline score={base_score}"]

    # Candidate tick budget (footprint-tick problems only). The score is
    # ``area2 × avg_ticks``, so a candidate improves iff
    # ``area2 × avg_ticks < best_score`` — i.e. its *total* ticks stay under
    # ``n_cases × best_score / area2``. Since ``area2`` is known before verifying (it
    # is a pure function of the grid), we can cap each candidate's verification at that
    # budget: a run that blows past it can't beat the score no matter how it ends, so
    # capping there only ever marks the candidate FAILED — it never accepts a wrong one
    # and never truncates a run that would have won (a winner settles inside the
    # budget). This turns the 5M-tick runaway a broken/deadlocked candidate would burn
    # into a bounded one, and it is *exact*, not a heuristic: as ``best_score`` drops on
    # each accepted win the budget tightens automatically. ``footprint`` problems have
    # no tick term, so they keep the full ``tick_cap`` (frames still need a full run).
    n_cases = len(base_res.cases)
    footprint_only = prob.get("scoring") == "footprint"

    def _cand_cap(area2: int) -> int:
        if footprint_only or best_score is None or n_cases == 0 or area2 <= 0:
            return tick_cap
        # +1024 cushion so a boundary-settling winner is never lost to rounding; a
        # candidate settling in the cushion still scores >= best_score and is rejected
        # by the strict `<` gate, so the cushion cannot cause a false accept.
        budget = int(n_cases * best_score / area2) + 1024
        return min(tick_cap, max(budget, 1))

    # Parse the current best grid through the Node/WASM oracle at most once per distinct
    # grid: `best_grid` only changes when a candidate is accepted, so re-parsing it for
    # every pass in a sweep (the common no-acceptance case) is redundant. Cache the parse
    # and rebuild it only when the grid text actually changes.
    cur: Program | None = None
    cur_key: str | None = None
    for sweep in range(max_sweeps):
        improved = False
        for pass_fn in passes:
            key = "\n".join(best_grid)
            if cur is None or cur_key != key:
                cur = parse_program(key, lm=lm)
                cur_key = key
            for cand in pass_fn(cur):
                if cand.grid == best_grid:
                    continue
                # The footprint pre-filter lives in the pass (`_relayout` drops
                # anything not strictly smaller); this is just for the log.
                _, _, area2 = footprint("\n".join(cand.grid))
                # Gate order is a cost decision, not a correctness one: a candidate
                # is accepted only when it both verifies AND (for a relayout move)
                # preserves every pipe binding, so the order these are checked never
                # changes the outcome. `verify` runs the in-process FastLittleman
                # (~0.1s); `bindings_preserved` re-parses and re-routes through the
                # Node oracle (seconds). So verify + score FIRST and pay the Node
                # binding gate only on a candidate that would otherwise be accepted —
                # skipping it on the many that fail verify or don't beat the score.
                res = _verify(cand.grid, _cand_cap(area2))
                if not res.passed:
                    log.append(f"sweep{sweep} {cand.label}: rejected (verify failed)")
                    continue
                sc = score_grid(cand.grid, prob, result=res, lm=verify_lm)
                if sc is None or (best_score is not None and sc >= best_score):
                    log.append(f"sweep{sweep} {cand.label}: no improvement (score={sc})")
                    continue
                if cand.placement is not None and not bindings_preserved(
                    cur, cand.placement, cand.grid, lm=lm
                ):
                    log.append(f"sweep{sweep} {cand.label}: rejected (binding changed)")
                    continue
                log.append(
                    f"sweep{sweep} {cand.label}: accept score {best_score} -> {sc} "
                    f"(area2={area2})"
                )
                best_grid, best_score = cand.grid, sc
                improved = True
        if not improved:
            break

    final_res = _verify(best_grid, tick_cap)
    return OptimizeResult(
        grid=best_grid,
        score=best_score,
        base_score=base_score,
        base_grid=prog.to_grid(),
        passed=final_res.passed,
        log=log,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="randomfun2026solvers.optimize",
        description="Optimize a .man program's contest score (footprint + ticks), verified.",
    )
    parser.add_argument("program", help="path to a .man file")
    parser.add_argument("problem", help="problem slug, .json path, or file")
    parser.add_argument("--max-sweeps", type=int, default=3, dest="max_sweeps")
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="also run the content-rewrite rule catalog (opt-in; geometric-only by default)",
    )
    parser.add_argument("--tick-cap", type=int, default=scoring.DEFAULT_TICK_CAP, dest="tick_cap")
    parser.add_argument("--out", help="write the optimized grid here")
    parser.add_argument("--verbose", action="store_true", help="print the search log")
    args = parser.parse_args(argv)

    try:
        res = optimize(
            Path(args.program),
            args.problem,
            semantic=args.semantic,
            max_sweeps=args.max_sweeps,
            tick_cap=args.tick_cap,
        )
    except (OptimizeError, LittlemanError, scoring.ScoringError) as exc:
        print(f"error: {exc}", file=__import__("sys").stderr)
        return 1

    if args.verbose:
        for line in res.log:
            print(f"# {line}", file=__import__("sys").stderr)
    delta = ""
    if res.base_score and res.score is not None:
        pct = 100.0 * (1 - res.score / res.base_score)
        delta = f"  ({pct:+.1f}%)"
    print(
        f"# score {res.base_score} -> {res.score}{delta}  passed={res.passed}",
        file=__import__("sys").stderr,
    )
    if args.out:
        Path(args.out).write_text(res.render() + "\n", encoding="utf-8")
        print(f"# wrote {args.out}", file=__import__("sys").stderr)
    else:
        print(res.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
