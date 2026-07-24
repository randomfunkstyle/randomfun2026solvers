"""Lay a directed graph of ASCII "containers" out on a 2D character grid.

A :class:`Container` is a fixed-size rectangular block of ASCII text with a set
of numbered *input* and *output* port locations (local cell coordinates). A
:class:`Graph` wires specific outputs to specific inputs. :func:`layout_graph`
places the containers so none overlap and draws every wire as a *pipe*: a run of
``-``/``|`` with ``^v<>`` glyphs marking the outward start, each bend, and the
final step into the target.

Port semantics (important):
    A container's port *locations* are fixed, but which port a pipe actually
    connects to is decided by the engine as the port **closest (manhattan) to
    the cell where the pipe touches the container**. With a single input/output
    the touch cell is irrelevant; with several, the layout must make each pipe
    land nearest its intended port. The router enforces and validates this.

The module is intentionally self-contained and built around swappable
strategies so later iterations can drop in:
    * container **variants** (equivalent alternative layouts) — see
      :attr:`Container.variants`;
    * a **scoring** objective — see :func:`score`;
    * smarter **placement / routing** (e.g. SAT or wave-function-collapse) by
      implementing :class:`PlacementStrategy` / :class:`RoutingStrategy`.

Everything downstream of a strategy (the data model, :class:`Canvas`, glyph
rendering and port validation) is reused unchanged.
"""

from __future__ import annotations

import heapq
from typing import Protocol

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Cell",
    "Container",
    "Edge",
    "Graph",
    "Placed",
    "Placement",
    "Route",
    "Layout",
    "Canvas",
    "PlacementStrategy",
    "RoutingStrategy",
    "LayeredPlacement",
    "AStarRouter",
    "LayoutEngine",
    "LayoutError",
    "score",
    "layout_graph",
    "annotate_ports",
]

# A grid coordinate, ``(x, y)`` — x is the column, y is the row (y grows down).
# Used both for global grid cells and for a container's local port coordinates.
Cell = tuple[int, int]

# Unit steps and their pipe glyphs (y grows downward, so "up" is (0, -1)).
_ARROW: dict[Cell, str] = {(1, 0): ">", (-1, 0): "<", (0, 1): "v", (0, -1): "^"}
_STEPS: tuple[Cell, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))


class LayoutError(RuntimeError):
    """A graph cannot be laid out validly (overlap, unroutable, bad ports…)."""


def _manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


# ── data model ────────────────────────────────────────────────────────────────
class _Model(BaseModel):
    """Base: allow snake_case attrs with camelCase aliases, ignore unknown keys."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Container(_Model):
    """A fixed-size ASCII block with numbered input/output port locations.

    ``inputs``/``outputs`` are lists of **local** coordinates; the list index is
    the port number (input ``0`` is ``inputs[0]``). ``content`` is the block's
    ascii art, one string per row.
    """

    id: str
    width: int
    height: int
    content: list[str] = Field(default_factory=list)
    inputs: list[Cell] = Field(default_factory=list)
    outputs: list[Cell] = Field(default_factory=list)
    # Reserved for iteration 2: equivalent alternative layouts of the same
    # container. Iteration 1 always uses variant index 0 (this container).
    variants: list[Container] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Container:
        if self.width <= 0 or self.height <= 0:
            raise LayoutError(f"container {self.id!r}: width/height must be positive")
        if self.content:
            if len(self.content) != self.height:
                raise LayoutError(
                    f"container {self.id!r}: content has {len(self.content)} rows, "
                    f"expected {self.height}"
                )
            for y, row in enumerate(self.content):
                if len(row) != self.width:
                    raise LayoutError(
                        f"container {self.id!r}: content row {y} has width {len(row)}, "
                        f"expected {self.width}"
                    )
        for kind, ports in (("input", self.inputs), ("output", self.outputs)):
            for i, (lx, ly) in enumerate(ports):
                if not (0 <= lx < self.width and 0 <= ly < self.height):
                    raise LayoutError(
                        f"container {self.id!r}: {kind} {i} at {(lx, ly)} is outside "
                        f"the {self.width}x{self.height} block"
                    )
        return self

    def variant(self, index: int) -> Container:
        """The chosen layout variant (index 0 == this container itself)."""
        if index == 0:
            return self
        try:
            return self.variants[index - 1]
        except IndexError as exc:
            raise LayoutError(f"container {self.id!r}: no variant {index}") from exc


class Edge(_Model):
    """A wire from ``src``'s output ``src_output`` to ``dst``'s input ``dst_input``."""

    id: str
    src: str
    src_output: int = 0
    dst: str
    dst_input: int = 0


class Graph(_Model):
    """A directed graph of containers wired output→input."""

    containers: list[Container] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Graph:
        ids = [c.id for c in self.containers]
        if len(set(ids)) != len(ids):
            raise LayoutError("duplicate container id")
        by_id = {c.id: c for c in self.containers}
        seen: set[str] = set()
        for e in self.edges:
            if e.id in seen:
                raise LayoutError(f"duplicate edge id {e.id!r}")
            seen.add(e.id)
            for role, cid, port, ports_attr in (
                ("src", e.src, e.src_output, "outputs"),
                ("dst", e.dst, e.dst_input, "inputs"),
            ):
                c = by_id.get(cid)
                if c is None:
                    raise LayoutError(f"edge {e.id!r}: unknown {role} container {cid!r}")
                n = len(getattr(c, ports_attr))
                if not (0 <= port < n):
                    raise LayoutError(
                        f"edge {e.id!r}: {role} port {port} out of range "
                        f"(container {cid!r} has {n} {ports_attr})"
                    )
        return self

    @property
    def by_id(self) -> dict[str, Container]:
        return {c.id: c for c in self.containers}


class Placed(_Model):
    """Where a container was placed: its top-left ``offset`` and chosen variant."""

    offset: Cell
    variant_index: int = 0


# Mapping container id → placement.
Placement = dict[str, Placed]


class Route(_Model):
    """A routed pipe: the ordered cells it occupies and the ports it resolves to."""

    edge_id: str
    path: list[Cell]
    resolved_output: int
    resolved_input: int


class Layout(_Model):
    """The result of laying out a graph: the ascii ``grid`` plus its metadata."""

    grid: list[str]
    width: int
    height: int
    # Global coordinate of the grid's top-left cell (``grid[0][0]``); lets callers
    # map global container/port coordinates back onto the (trimmed) grid.
    origin: Cell = (0, 0)
    placement: Placement
    routes: list[Route]

    def render(self) -> str:
        return "\n".join(self.grid)


# ── canvas ──────────────────────────────────────────────────────────────────
class Canvas:
    """A sparse, mutable 2D character buffer that forbids cell collisions.

    A container occupies its *whole* bounding box (blank cells included) so pipes
    never route through it; only non-blank characters are rendered.
    """

    def __init__(self) -> None:
        self._cells: dict[Cell, str] = {}
        # Cells that block routing (container area + already-laid pipes).
        self._blocked: set[Cell] = set()

    def is_free(self, cell: Cell) -> bool:
        return cell not in self._blocked

    def set(self, cell: Cell, ch: str, *, block: bool = True) -> None:
        prev = self._cells.get(cell)
        if prev is not None and prev != ch:
            raise LayoutError(f"cell {cell} already holds {prev!r}, cannot write {ch!r}")
        self._cells[cell] = ch
        if block:
            self._blocked.add(cell)

    def block(self, cell: Cell) -> None:
        self._blocked.add(cell)

    def blit(self, container: Container, offset: Cell) -> None:
        ox, oy = offset
        for y in range(container.height):
            row = container.content[y] if y < len(container.content) else ""
            for x in range(container.width):
                ch = row[x] if x < len(row) else " "
                self.set((ox + x, oy + y), ch)

    def bbox(self) -> tuple[int, int, int, int]:
        if not self._cells:
            return (0, 0, 0, 0)
        xs = [c[0] for c in self._cells]
        ys = [c[1] for c in self._cells]
        return (min(xs), min(ys), max(xs), max(ys))

    def render(self) -> list[str]:
        if not self._cells:
            return []
        min_x, min_y, max_x, max_y = self.bbox()
        rows: list[str] = []
        for y in range(min_y, max_y + 1):
            row = [self._cells.get((x, y), " ") for x in range(min_x, max_x + 1)]
            rows.append("".join(row).rstrip())
        return rows


# ── strategy interfaces ───────────────────────────────────────────────────────
class PlacementStrategy(Protocol):
    """Chooses where each container (and which variant) sits on the grid."""

    def place(self, graph: Graph) -> Placement: ...


class RoutingStrategy(Protocol):
    """Draws every edge as a pipe onto a canvas already holding the containers."""

    def route(self, canvas: Canvas, placement: Placement, graph: Graph) -> list[Route]: ...


# ── placement: layered (Sugiyama-style columns) ───────────────────────────────
class LayeredPlacement:
    """Topological layers become columns (left→right); containers stack in rows.

    Gaps between columns and rows leave the router room to lay pipes. Requires a
    DAG (iteration 1); a cyclic graph raises :class:`LayoutError`.
    """

    def __init__(self, *, h_gap: int = 6, v_gap: int = 3) -> None:
        self.h_gap = h_gap
        self.v_gap = v_gap

    def place(self, graph: Graph) -> Placement:
        by_id = graph.by_id
        g = nx.DiGraph()
        g.add_nodes_from(by_id)
        for e in graph.edges:
            g.add_edge(e.src, e.dst)
        try:
            layers = list(nx.topological_generations(g))
        except nx.NetworkXUnfeasible as exc:
            raise LayoutError("graph has a cycle; layered placement needs a DAG") from exc

        placement: Placement = {}
        x = 0
        for layer in layers:
            # Deterministic order within a column.
            layer = sorted(layer)
            col_width = max((by_id[cid].width for cid in layer), default=0)
            y = 0
            for cid in layer:
                placement[cid] = Placed(offset=(x, y), variant_index=0)
                y += by_id[cid].height + self.v_gap
            x += col_width + self.h_gap
        return placement


# ── routing: grid A* with turn penalty ────────────────────────────────────────
class AStarRouter:
    """Routes each edge with A* over free cells, preferring straight runs.

    For each edge it projects the intended output/input onto the nearest border
    edge facing the other container, steps one cell out to start/end the pipe,
    then searches for a low-bend path between the two stubs. It validates that
    the manhattan-closest port to each touch cell is the intended one.
    """

    def __init__(self, *, turn_penalty: int = 3, margin: int = 6) -> None:
        self.turn_penalty = turn_penalty
        self.margin = margin

    def route(self, canvas: Canvas, placement: Placement, graph: Graph) -> list[Route]:
        by_id = graph.by_id
        bounds = self._search_bounds(canvas, by_id, placement)
        routes: list[Route] = []
        for e in graph.edges:
            routes.append(self._route_edge(e, canvas, placement, by_id, bounds))
        return routes

    # -- geometry helpers ------------------------------------------------------
    def _ports_global(self, c: Container, offset: Cell, attr: str) -> list[Cell]:
        ox, oy = offset
        return [(ox + lx, oy + ly) for (lx, ly) in getattr(c, attr)]

    def _exit(self, c: Container, offset: Cell, local: Cell, toward: Cell) -> tuple[Cell, Cell]:
        """Border touch cell + outward step for a port, facing ``toward``.

        Projects the port onto its nearest border edge (so the touch cell stays
        closest to that port); ties broken toward the other container.
        """
        ox, oy = offset
        lx, ly = local
        w, h = c.width, c.height
        # (distance-to-edge, outward-dir, touch-local)
        opts = [
            (lx, (-1, 0), (0, ly)),
            (w - 1 - lx, (1, 0), (w - 1, ly)),
            (ly, (0, -1), (lx, 0)),
            (h - 1 - ly, (0, 1), (lx, h - 1)),
        ]
        best_d = min(o[0] for o in opts)
        cand = [o for o in opts if o[0] == best_d]
        port_g = (ox + lx, oy + ly)
        vec = (toward[0] - port_g[0], toward[1] - port_g[1])
        _, out_dir, touch_l = max(cand, key=lambda o: o[1][0] * vec[0] + o[1][1] * vec[1])
        touch = (ox + touch_l[0], oy + touch_l[1])
        return touch, out_dir

    def _search_bounds(
        self, canvas: Canvas, by_id: dict[str, Container], placement: Placement
    ) -> tuple[int, int, int, int]:
        min_x, min_y, max_x, max_y = canvas.bbox()
        m = self.margin
        return (min_x - m, min_y - m, max_x + m, max_y + m)

    # -- per edge --------------------------------------------------------------
    def _route_edge(
        self,
        e: Edge,
        canvas: Canvas,
        placement: Placement,
        by_id: dict[str, Container],
        bounds: tuple[int, int, int, int],
    ) -> Route:
        src, dst = by_id[e.src], by_id[e.dst]
        src_off, dst_off = placement[e.src].offset, placement[e.dst].offset
        src_center = (src_off[0] + src.width // 2, src_off[1] + src.height // 2)
        dst_center = (dst_off[0] + dst.width // 2, dst_off[1] + dst.height // 2)

        out_local = src.outputs[e.src_output]
        in_local = dst.inputs[e.dst_input]
        src_touch, src_dir = self._exit(src, src_off, out_local, dst_center)
        dst_touch, dst_dir = self._exit(dst, dst_off, in_local, src_center)

        # Pipe endpoints: one cell outside each container, then a forced straight
        # stub so the first glyph points outward and the last steps inward.
        c0 = (src_touch[0] + src_dir[0], src_touch[1] + src_dir[1])
        c1 = (c0[0] + src_dir[0], c0[1] + src_dir[1])
        cn = (dst_touch[0] + dst_dir[0], dst_touch[1] + dst_dir[1])
        cn_1 = (cn[0] + dst_dir[0], cn[1] + dst_dir[1])

        for cell in (c0, c1, cn, cn_1):
            if not canvas.is_free(cell):
                raise LayoutError(f"edge {e.id!r}: no room to attach pipe at {cell}")

        blocked_extra = {c0, cn}
        inner = self._astar(c1, cn_1, canvas, bounds, blocked_extra)
        if inner is None:
            raise LayoutError(f"edge {e.id!r}: no free path from {c1} to {cn_1}")
        path = [c0, *inner, cn]

        # Validate the proximity rule from each touch (first/last pipe cell).
        outs = self._ports_global(src, src_off, "outputs")
        ins = self._ports_global(dst, dst_off, "inputs")
        got_out = _resolve_port(c0, outs)
        got_in = _resolve_port(cn, ins)
        if got_out != e.src_output:
            raise LayoutError(
                f"edge {e.id!r}: pipe touches src nearest output {got_out}, "
                f"not intended {e.src_output}"
            )
        if got_in != e.dst_input:
            raise LayoutError(
                f"edge {e.id!r}: pipe touches dst nearest input {got_in}, "
                f"not intended {e.dst_input}"
            )

        self._draw(path, canvas)
        return Route(edge_id=e.id, path=path, resolved_output=got_out, resolved_input=got_in)

    def _astar(
        self,
        start: Cell,
        goal: Cell,
        canvas: Canvas,
        bounds: tuple[int, int, int, int],
        blocked_extra: set[Cell],
    ) -> list[Cell] | None:
        min_x, min_y, max_x, max_y = bounds

        def passable(cell: Cell) -> bool:
            if cell in blocked_extra:
                return False
            if not (min_x <= cell[0] <= max_x and min_y <= cell[1] <= max_y):
                return False
            return canvas.is_free(cell)

        # State includes the incoming direction so turns can be penalized.
        start_state = (start, (0, 0))
        g_score: dict[tuple[Cell, Cell], int] = {start_state: 0}
        came: dict[tuple[Cell, Cell], tuple[Cell, Cell]] = {}
        heap: list[tuple[int, int, Cell, Cell]] = [(_manhattan(start, goal), 0, start, (0, 0))]
        while heap:
            _, g, cell, cdir = heapq.heappop(heap)
            if cell == goal:
                return self._reconstruct(came, (cell, cdir), start_state)
            if g > g_score.get((cell, cdir), 1 << 30):
                continue
            for step in _STEPS:
                nxt = (cell[0] + step[0], cell[1] + step[1])
                if nxt != goal and not passable(nxt):
                    continue
                if nxt == goal and nxt in blocked_extra:
                    continue
                turn = self.turn_penalty if cdir != (0, 0) and step != cdir else 0
                ng = g + 1 + turn
                nstate = (nxt, step)
                if ng < g_score.get(nstate, 1 << 30):
                    g_score[nstate] = ng
                    came[nstate] = (cell, cdir)
                    heapq.heappush(heap, (ng + _manhattan(nxt, goal), ng, nxt, step))
        return None

    @staticmethod
    def _reconstruct(
        came: dict[tuple[Cell, Cell], tuple[Cell, Cell]],
        end: tuple[Cell, Cell],
        start_state: tuple[Cell, Cell],
    ) -> list[Cell]:
        cells: list[Cell] = []
        state = end
        while True:
            cells.append(state[0])
            if state == start_state:
                break
            state = came[state]
        cells.reverse()
        return cells

    def _draw(self, path: list[Cell], canvas: Canvas) -> None:
        n = len(path) - 1
        for i, cell in enumerate(path):
            d_in = _sub(cell, path[i - 1]) if i > 0 else None
            d_out = _sub(path[i + 1], cell) if i < n else None
            if i == 0:
                glyph = _ARROW[d_out]  # points outward from the source
            elif i == n:
                glyph = _ARROW[d_in]  # points into the destination
            elif d_in == d_out:
                glyph = "-" if d_out[0] != 0 else "|"
            else:
                glyph = _ARROW[d_out]  # bend: new direction
            canvas.set(cell, glyph)


def _sub(a: Cell, b: Cell) -> Cell:
    return (a[0] - b[0], a[1] - b[1])


def _resolve_port(touch: Cell, ports: list[Cell]) -> int:
    """Index of the port closest (manhattan) to ``touch``; ties → lowest index."""
    best_i, best_d = 0, 1 << 30
    for i, p in enumerate(ports):
        d = _manhattan(touch, p)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


# ── engine + convenience ──────────────────────────────────────────────────────
class LayoutEngine:
    """Orchestrates placement → blit → routing → assembly.

    Swap ``placement``/``router`` for other strategies (SAT, wave-function-
    collapse) without touching the data model, canvas or rendering.
    """

    def __init__(
        self,
        placement: PlacementStrategy | None = None,
        router: RoutingStrategy | None = None,
    ) -> None:
        self.placement = placement or LayeredPlacement()
        self.router = router or AStarRouter()

    def run(self, graph: Graph) -> Layout:
        placement = self.placement.place(graph)
        by_id = graph.by_id
        canvas = Canvas()
        for cid, placed in placement.items():
            canvas.blit(by_id[cid].variant(placed.variant_index), placed.offset)
        routes = self.router.route(canvas, placement, graph)
        grid = canvas.render()
        min_x, min_y, _, _ = canvas.bbox()
        return Layout(
            grid=grid,
            width=max((len(r) for r in grid), default=0),
            height=len(grid),
            origin=(min_x, min_y),
            placement=placement,
            routes=routes,
        )


def layout_graph(
    graph: Graph,
    *,
    placement: PlacementStrategy | None = None,
    router: RoutingStrategy | None = None,
) -> Layout:
    """Lay ``graph`` out and return the resulting :class:`Layout`."""
    return LayoutEngine(placement, router).run(graph)


def score(layout: Layout) -> float:
    """Heuristic layout cost — **lower is better** (iteration-2 objective).

    Sums total pipe length, the grid's bounding-box area, and a per-bend penalty.
    Present so future optimizers (SAT / wave-function-collapse, variant/placement
    search) have a target to minimize; weights are a starting point, not tuned.
    """
    pipe_len = sum(max(0, len(r.path) - 1) for r in layout.routes)
    bends = 0
    for r in layout.routes:
        for i in range(1, len(r.path) - 1):
            if _sub(r.path[i], r.path[i - 1]) != _sub(r.path[i + 1], r.path[i]):
                bends += 1
    area = layout.width * layout.height
    return float(pipe_len) + 0.1 * float(area) + 2.0 * float(bends)


def annotate_ports(layout: Layout, graph: Graph) -> list[str]:
    """Overlay per-edge markers on ``layout.grid`` for debugging.

    Each edge gets a letter (``a``-``z``, by edge order); it is stamped at both of
    its endpoints so they can be matched by eye: **uppercase at the source
    output**, **lowercase at the destination input**. So ``A``→``a`` traces one
    edge and its direction. The container's own glyph at a port cell is replaced;
    edges beyond 26 are marked ``#``.
    """
    ox0, oy0 = layout.origin
    rows: list[list[str]] = [list(r) for r in layout.grid]
    by_id = graph.by_id

    def stamp(cell: Cell, ch: str) -> None:
        x, y = cell[0] - ox0, cell[1] - oy0
        if y < 0 or x < 0:
            return
        while y >= len(rows):
            rows.append([])
        while len(rows[y]) <= x:
            rows[y].append(" ")
        rows[y][x] = ch

    def port_cell(cid: str, ports_attr: str, index: int) -> Cell:
        px, py = layout.placement[cid].offset
        lx, ly = getattr(by_id[cid], ports_attr)[index]
        return (px + lx, py + ly)

    for i, e in enumerate(graph.edges):
        letter = chr(ord("a") + i) if i < 26 else "#"
        stamp(port_cell(e.src, "outputs", e.src_output), letter.upper())
        stamp(port_cell(e.dst, "inputs", e.dst_input), letter.lower())
    return ["".join(r) for r in rows]
