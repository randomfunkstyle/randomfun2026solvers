"""Z3 layout router: BlockGraph -> concrete ASCII grid.

Two problems:
  (a) LOCAL (Z3): choose each CPU pipe's attach cell on its side so that every
      pipe op's *nearest* pipe (Manhattan, reading-order tiebreak) is its target.
      This is origin-independent, so it's solved in CPU-relative coordinates.
  (b) GLOBAL (deterministic): place I/O/BUF rooms around the CPU and route straight
      pipes from the solved attach cells.

R0 topology handled: CPU + input on W + output on E + BUF on N (up + down pipes).
"""

from __future__ import annotations

import z3

from .blockspec import BlockGraph, E, N, W
from .trail import TrailLayout, build_trail

# CPU-relative attach *segment* cell (the pipe cell touching the CPU wall) per side.
# interior is [0,Wi) x [0,Hi); walls at x=-1, x=Wi, y=-1, y=Hi.
#   W: (-2, row)   E: (Wi+1, row)   N: (col, -2)   S: (col, Hi+1)


def _abs(e):
    return z3.If(e >= 0, e, -e)


def solve_attachments(graph: BlockGraph, trail: TrailLayout) -> dict[str, tuple[int, int]]:
    """Return {pipe_id: (segx, segy)} in CPU-relative coords s.t. nearest holds."""
    Wi, Hi = trail.width, trail.height
    cpu_pipes = [p for p in graph.pipes if p.cpu_side(graph.cpu)]
    s = z3.Solver()

    pos: dict[str, z3.ArithRef] = {}
    segx: dict[str, object] = {}
    segy: dict[str, object] = {}
    for p in cpu_pipes:
        side = p.cpu_side(graph.cpu)
        v = z3.Int(f"pos_{p.id}")
        pos[p.id] = v
        if side in (W, E):
            s.add(v >= 0, v <= Hi - 1)
            segx[p.id] = -2 if side == W else Wi + 1
            segy[p.id] = v
        else:  # N or S
            s.add(v >= 0, v <= Wi - 1)
            segx[p.id] = v
            segy[p.id] = -2 if side == N else Hi + 1

    # distinct positions among pipes on the same side
    by_side: dict[str, list[str]] = {}
    for p in cpu_pipes:
        by_side.setdefault(p.cpu_side(graph.cpu), []).append(p.id)
    for ids in by_side.values():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                s.add(pos[ids[a]] != pos[ids[b]])

    BIG = 10_000

    def dist(cx, cy, pid):
        return _abs(cx - segx[pid]) + _abs(cy - segy[pid])

    def order(pid):
        return segy[pid] * BIG + segx[pid]

    # nearest constraints
    for cell in trail.pipe_op_cells():
        target = cell.pipe
        tp = graph.pipe(target)
        direction = tp.cpu_dir(graph.cpu)
        rivals = [
            p for p in cpu_pipes if p.cpu_dir(graph.cpu) == direction and p.id != target
        ]
        dp = dist(cell.x, cell.y, target)
        for q in rivals:
            dq = dist(cell.x, cell.y, q.id)
            s.add(z3.Or(dp < dq, z3.And(dp == dq, order(target) < order(q.id))))

    if s.check() != z3.sat:
        raise RuntimeError("router: no attachment placement satisfies nearest-pipe")
    m = s.model()
    out: dict[str, tuple[int, int]] = {}
    for p in cpu_pipes:
        sx = segx[p.id]
        sy = segy[p.id]
        sx = m[sx].as_long() if isinstance(sx, z3.ArithRef) else sx
        sy = m[sy].as_long() if isinstance(sy, z3.ArithRef) else sy
        out[p.id] = (sx, sy)
    return out


class Canvas:
    def __init__(self):
        self.cells: dict[tuple[int, int], str] = {}

    def put(self, x, y, ch):
        self.cells[(x, y)] = ch

    def rect(self, x0, y0, x1, y1):
        for x in range(x0, x1 + 1):
            self.put(x, y0, "-" if x not in (x0, x1) else "+")
            self.put(x, y1, "-" if x not in (x0, x1) else "+")
        for y in range(y0 + 1, y1):
            self.put(x0, y, "|")
            self.put(x1, y, "|")

    def text(self, s, x, y):
        for i, ch in enumerate(s):
            self.put(x + i, y, ch)

    def render(self) -> str:
        xs = [x for x, _ in self.cells]
        ys = [y for _, y in self.cells]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        rows = []
        for y in range(miny, maxy + 1):
            row = "".join(
                self.cells.get((x, y), " ") for x in range(minx, maxx + 1)
            )
            rows.append(row.rstrip())
        width = max((len(r) for r in rows), default=0)
        return "\n".join(r.ljust(width) for r in rows) + "\n"


def render(
    graph: BlockGraph, trail: TrailLayout | None = None, buf_loop: bool = False
) -> str:
    """Render the R0 topology (CPU + input W + output E + BUF N up/down).

    buf_loop=True makes BUF a forwarder *loop* (for a circulating ring that is
    rotated many times) instead of a one-shot @rsH (single round trip)."""
    if trail is None:
        trail = build_trail(graph.trail)
    seg = solve_attachments(graph, trail)  # CPU-relative segment cells
    Wi, Hi = trail.width, trail.height

    c = Canvas()
    # CPU room: interior (0,0)..(Wi-1,Hi-1); walls at -1..Wi
    c.rect(-1, -1, Wi, Hi)
    for cell in trail.cells:
        c.put(cell.x, cell.y, cell.char)

    # classify CPU pipes by side
    by_side: dict[str, list] = {}
    for p in graph.pipes:
        side = p.cpu_side(graph.cpu)
        if side:
            by_side.setdefault(side, []).append(p)

    # --- West: input room + straight pipe ---
    for p in by_side.get(W, []):
        _, row = seg[p.id]  # seg at (-2, row)
        # pipe cells (-3,row) source, (-2,row) terminal ; I room to the left
        c.put(-3, row, ">")
        c.put(-2, row, ">")
        c.rect(-6, row - 1, -4, row + 1)
        c.put(-5, row, "I")

    # --- East: output room + straight pipe ---
    for p in by_side.get(E, []):
        _, row = seg[p.id]  # seg at (Wi+1, row)
        c.put(Wi + 1, row, ">")
        c.put(Wi + 2, row, ">")
        c.rect(Wi + 3, row - 1, Wi + 5, row + 1)
        c.put(Wi + 4, row, "O")

    # --- North: BUF forwarder with up + down pipes ---
    north = by_side.get(N, [])
    if north:
        if buf_loop:
            # BUF as a forwarder loop: bottom wall -4, interior -5/-6, top wall -7
            c.rect(-1, -7, max(Wi, 5), -4)
            c.text("@>rsv", 0, -6)
            c.text(".^..<", 0, -5)
        else:
            # one-shot BUF: bottom wall -4, interior -5, top wall -6
            c.rect(-1, -6, max(Wi, 4), -4)
            c.text("@rsH", 0, -5)
        for p in north:
            col, _ = seg[p.id]  # seg at (col, -2)
            if p.cpu_dir(graph.cpu) == "out":  # up: CPU -> BUF
                c.put(col, -2, "^")
                c.put(col, -3, "^")
            else:  # down: BUF -> CPU
                c.put(col, -2, "v")
                c.put(col, -3, "v")

    return c.render()
