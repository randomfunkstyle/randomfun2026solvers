#!/usr/bin/env python3
"""Structural moves over the grid AST — the compactor's actual edits.

:mod:`mancompact` deletes a grid line by *string surgery*, which forces it to
refuse anything it cannot prove safe from glyphs alone. In particular it refuses
to touch a pipe arrowhead, because deleting a bend would change a route.

Working on the AST removes that restriction, because a pipe here is a **path**
plus a rule for drawing it rather than a picture. Drop a cell from the path and
the glyphs are simply recomputed: arrowheads land on the first cell, the last
cell and every bend, with ``-``/``|`` on the straights. So a terminal arrowhead
whose predecessor already carries the same heading is removable — the pipe just
arrives one cell earlier — which string surgery has no way to know.

What still may not change:

* **capacity** — a pipe's length is the number of values it can hold, so every
  drop is charged against a declared minimum and refused without one;
* **topology** — the same rooms, the same pipes, connected the same way;
* **bindings** — ``s``/``r`` bind to the *nearest* pipe, so a move that shifts a
  wall can silently re-bind an op. Only the engine can settle that, so the caller
  re-parses and diffs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .manast import Ast, Atom, Corridor, PaintError, PipeNode, RoomNode, render

__all__ = [
    "MoveError",
    "DropReport",
    "drop_row",
    "drop_col",
    "try_drop",
    "try_squash",
    "stretch_col",
    "stretch_row",
    "try_shift_content",
    "hug_violations",
    "reglyph",
    "ring_capacity",
]

_GLYPH = {(1, 0): ">", (-1, 0): "<", (0, -1): "^", (0, 1): "v"}


class MoveError(RuntimeError):
    """A move would break something it is not allowed to break."""


@dataclass
class DropReport:
    axis: str
    index: int
    rooms_shrunk: list[int] = field(default_factory=list)
    rooms_moved: list[int] = field(default_factory=list)
    pipes_shortened: dict[int, int] = field(default_factory=dict)  # id -> cells lost
    #: id -> change in cell count from a re-route (negative means it got shorter)
    pipes_rerouted: dict[int, int] = field(default_factory=dict)
    note: str = ""


def reglyph(
    path: list[tuple[int, int]],
    entry_dir: tuple[int, int] | None = None,
    exit_dir: tuple[int, int] | None = None,
) -> list[str]:
    """Redraw a pipe from its path, the way the engine expects to read it.

    Every cell shows the heading the value *leaves* it with: an arrowhead on the
    ends and on every bend, ``-``/``|`` on the straights.

    `exit_dir` is not optional in practice, and getting this wrong is subtle. The
    final cell's outgoing heading is the one that carries the value into the
    destination room, and that is **not** derivable from the path — the path stops
    at the last cell. Infer it from the incoming heading instead and a terminus
    that is *also* a bend gets drawn pointing along its own last leg: shortening a
    pipe by one cell then turned ``^`` (into the room) into ``>`` (along the run),
    which still loads, still analyses as a pipe, and silently computes nothing.
    """
    if not path:
        return []
    if len(path) == 1 and not (entry_dir or exit_dir):
        raise MoveError("a one-cell pipe has no direction to draw")
    out: list[str] = []
    last = len(path) - 1
    for i, (x, y) in enumerate(path):
        din = entry_dir if i == 0 else (x - path[i - 1][0], y - path[i - 1][1])
        dout = exit_dir or din if i == last else (path[i + 1][0] - x, path[i + 1][1] - y)
        if dout is None or dout not in _GLYPH:
            raise MoveError(f"pipe path has no usable heading at {(x, y)}")
        if i == 0 or i == last:
            # Both ends are always arrowheads, never `-`/`|`. The first cell is
            # where the engine picks the pipe up off the source wall and the last
            # is where it hands the value over, so each must state a heading; a
            # straight body glyph there detaches the pipe from its room, which
            # still loads and analyses as a pipe belonging to nobody.
            out.append(_GLYPH[dout])
        elif din == dout:
            out.append("-" if dout[0] else "|")
        else:
            out.append(_GLYPH[dout])  # a bend shows where it is going
    return out


def hug_violations(ast: Ast) -> list[str]:
    """Cells where a pipe bends **away** from a wall it is hugging.

    A loader rule, and one that costs a whole afternoon to find by symptom: the
    engine decides where a pipe *starts* from the cell behind its arrowhead, so a
    cell whose arrowhead points away from an adjacent wall is read as a **new pipe
    leaving that room**. The grid then either refuses to load or turns a legitimate
    pipe into a room-to-itself loop.

    It matters here because nothing else catches it. Re-routing with
    :func:`~randomfun2026solvers.manroute.route_like` optimises for deviation and
    length; it has no idea that a bend one cell from a wall is illegal, and the
    result still renders, still analyses as a pipe, and computes the wrong thing.
    So every generated route is checked against this before it is believed.
    """
    walls: dict[tuple[int, int], int] = {}
    for room in ast.rooms:
        bw, bh = room.size
        for dx in range(bw):
            for dy in range(bh):
                if dx in (0, bw - 1) or dy in (0, bh - 1):
                    walls[(room.x + dx, room.y + dy)] = room.id
    out: list[str] = []
    for pipe in ast.pipes:
        for i, (cell, glyph) in enumerate(zip(pipe.path, pipe.glyphs, strict=False)):
            d = {">": (1, 0), "<": (-1, 0), "^": (0, -1), "v": (0, 1)}.get(glyph)
            if d is None:
                continue  # `-`/`|` carry no heading of their own
            behind = (cell[0] - d[0], cell[1] - d[1])
            if behind in walls and i != 0:
                # An arrowhead whose *predecessor* is a wall is exactly the shape
                # the loader reads as "a pipe starts here".
                out.append(
                    f"pipe{pipe.id} cell {i} at {cell} shows {glyph!r} with room"
                    f"{walls[behind]}'s wall at {behind} behind it: the loader will read "
                    f"this as a new pipe leaving that room"
                )
    return out


def _contiguous(path: list[tuple[int, int]]) -> bool:
    return all(
        abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(path, path[1:], strict=False)
    )


def ring_capacity(ast: Ast, group: tuple[int, ...]) -> int:
    return sum(p.capacity for p in ast.pipes if p.id in group)


def _drop(ast: Ast, axis: str, index: int, capacity: dict[tuple[int, ...], int]) -> DropReport:
    """Remove one grid line, then repair every node that crossed it."""
    ax = 0 if axis == "col" else 1
    rep = DropReport(axis=axis, index=index)

    def before(v: int) -> bool:
        return v > index

    # ── rooms ────────────────────────────────────────────────────────────────
    for room in ast.rooms:
        lo = room.y if ax else room.x
        span = room.h + 1 if ax else room.w + 1  # interior + far wall
        hi = lo + span
        if index < lo:
            # strictly outside and after the cut: slide it back by one
            if room.pinned:
                raise MoveError(f"room{room.id} is pinned but must move: {room.note}")
            room.translate(0, -1) if ax else room.translate(-1, 0)
            rep.rooms_moved.append(room.id)
            continue
        if index > hi:
            continue  # strictly before the cut: untouched
        # `lo` and `hi` are the box's own walls, not outside it. Treating `lo` as
        # "after the cut" slid the room to a negative coordinate instead of
        # refusing, which is a breached box rather than a moved one.
        if index in (lo, hi):
            raise MoveError(f"{axis} {index} is a wall of room{room.id}")
        # `pinned` was only being honoured on the branch that *moves* a room, so a
        # cut straight through a pinned one shrank it instead. That is the single
        # case the flag exists for: a display's interior is legitimately blank, its
        # size IS the pixel resolution, and no glyph check will ever refuse the cut.
        if room.rigid_size:
            raise MoveError(
                f"room{room.id}'s size is fixed but {axis} {index} would shrink it: {room.note}"
            )
        # A Corridor holds `.` cells, which SPEC calls a nop -- the same as a
        # blank. They are erasable, so they must not count as content here;
        # treating them as live is what made this sweep refuse cuts that plain
        # string surgery had already proved safe on gradebook and sudoku.
        occupied = [
            c
            for child in room.children
            if not isinstance(child, Corridor)
            for c in child.paint()
            if c[ax] == index
        ]
        if occupied:
            raise MoveError(
                f"{axis} {index} holds {len(occupied)} live glyph(s) in room{room.id}"
            )
        if ax:
            room.h -= 1
        else:
            room.w -= 1
        rep.rooms_shrunk.append(room.id)
        for child in room.children:
            _shift_child(child, ax, index)
        room.ports = [
            (x - (1 if not ax and before(x) else 0), y - (1 if ax and before(y) else 0))
            for x, y in room.ports
        ]

    # ── pipes ────────────────────────────────────────────────────────────────
    for pipe in ast.pipes:
        crossing = [c for c in pipe.path if c[ax] == index]
        if not crossing:
            pipe.path = [
                (x - (1 if not ax and before(x) else 0), y - (1 if ax and before(y) else 0))
                for x, y in pipe.path
            ]
            continue
        if pipe.min_capacity is None:
            raise MoveError(
                f"pipe{pipe.id} crosses {axis} {index} but has no declared capacity"
            )
        kept = [c for c in pipe.path if c[ax] != index]
        kept = [
            (x - (1 if not ax and before(x) else 0), y - (1 if ax and before(y) else 0))
            for x, y in kept
        ]
        # A dropped cell must leave the remainder a single connected run: taking a
        # cell out of the *middle* of a perpendicular leg would tear the pipe.
        if not _contiguous(kept):
            raise MoveError(
                f"pipe{pipe.id} would be torn by {axis} {index} — the cut crosses it "
                "sideways rather than along its run"
            )
        if len(kept) < pipe.min_capacity:
            raise MoveError(
                f"pipe{pipe.id} would fall to {len(kept)} cells, below its declared "
                f"minimum of {pipe.min_capacity}"
            )
        rep.pipes_shortened[pipe.id] = len(pipe.path) - len(kept)
        pipe.path = kept
        pipe.glyphs = reglyph(kept, pipe.entry_dir, pipe.exit_dir)
        pipe.x = min((x for x, _ in kept), default=0)
        pipe.y = min((y for _, y in kept), default=0)

    for stray in ast.strays:
        cells = [c for c in stray.paint() if c[ax] == index]
        if cells:
            raise MoveError(f"{axis} {index} holds an unclaimed glyph at {cells[0]}")
        _shift_child(stray, ax, index)

    # ── group capacity, after the fact ───────────────────────────────────────
    for group, need in capacity.items():
        have = ring_capacity(ast, group)
        if have < need:
            raise MoveError(
                f"pipe group {group} fell to {have} cells against a need of {need}"
            )
    return rep


def _shift_child(child, ax: int, index: int) -> None:
    """Slide a room child back by one if it sits after the cut."""
    if isinstance(child, Corridor):
        # Nops on the cut line are erased, not shifted: a `.` is a blank, and one
        # left sitting at `index` would land on top of whatever slid up into it.
        child.dots = [
            (x - (1 if not ax and x > index else 0), y - (1 if ax and y > index else 0))
            for x, y in child.dots
            if (x if not ax else y) != index
        ]
        if child.dots:
            child.x = min(x for x, _ in child.dots)
            child.y = min(y for _, y in child.dots)
        return
    if isinstance(child, Atom):
        # an atom spanning the cut would have been caught as a live glyph unless
        # its row there is blank, in which case drop that row from the rectangle
        if ax and child.y <= index < child.y + len(child.rows):
            local = index - child.y
            if child.rows[local].strip():
                raise MoveError(f"atom {child.id} has content on row {index}")
            child.rows = child.rows[:local] + child.rows[local + 1 :]
            return
        if not ax and child.x <= index < child.x + child.size[0]:
            local = index - child.x
            if any(len(r) > local and r[local] != " " for r in child.rows):
                raise MoveError(f"atom {child.id} has content on col {index}")
            child.rows = [r[:local] + r[local + 1 :] for r in child.rows]
            return
    if ax:
        if child.y > index:
            child.y -= 1
    elif child.x > index:
        child.x -= 1


def drop_row(ast: Ast, y: int, *, capacity: dict[tuple[int, ...], int] | None = None) -> DropReport:
    """Delete grid row `y`, repairing rooms, pipes and glyphs."""
    return _drop(ast, "row", y, capacity or {})


def drop_col(ast: Ast, x: int, *, capacity: dict[tuple[int, ...], int] | None = None) -> DropReport:
    """Delete grid column `x`, repairing rooms, pipes and glyphs."""
    return _drop(ast, "col", x, capacity or {})


def try_drop(
    ast: Ast, axis: str, index: int, *, capacity: dict[tuple[int, ...], int] | None = None
) -> tuple[Ast | None, DropReport | str]:
    """Attempt a drop on a *copy*; return the new AST or the reason it failed."""
    import copy

    trial = copy.deepcopy(ast)
    try:
        rep = _drop(trial, axis, index, capacity or {})
        render(trial)  # a move that cannot be painted is not a move
    except (MoveError, PaintError) as exc:
        return None, str(exc)
    return trial, rep


# ── the inverse move: insert a line ──────────────────────────────────────────
def _stretch(ast: Ast, axis: str, index: int) -> DropReport:
    """Insert one blank grid line at `index`, repairing everything that spanned it.

    The inverse of :func:`_drop`, and it exists to make the value of a cut
    *measurable*. A squash cannot be run on a grid that has no slack, so there is
    no before-and-after to compare — but stretching always can, and inserting a
    blank cell into a horizontal run of ops is semantically identical (the man
    walks a nop) while costing exactly one tick per pass. Measure what the
    insertion costs and you have measured what the removal would save, on the real
    grid rather than by argument.
    """
    ax = 0 if axis == "col" else 1
    rep = DropReport(axis=axis, index=index, note="stretch")

    for room in ast.rooms:
        lo = room.y if ax else room.x
        hi = lo + (room.h + 1 if ax else room.w + 1)
        if index <= lo:
            room.translate(0, 1) if ax else room.translate(1, 0)
            rep.rooms_moved.append(room.id)
            continue
        if index > hi:
            continue
        if room.rigid_size:
            raise MoveError(
                f"room{room.id}'s size is fixed but {axis} {index} would widen it: {room.note}"
            )
        if ax:
            room.h += 1
        else:
            room.w += 1
        rep.rooms_shrunk.append(room.id)
        for child in room.children:
            _grow_child(child, ax, index)
        room.ports = [
            (x + (1 if not ax and x >= index else 0), y + (1 if ax and y >= index else 0))
            for x, y in room.ports
        ]

    for pipe in ast.pipes:
        moved = [
            (x + (1 if not ax and x >= index else 0), y + (1 if ax and y >= index else 0))
            for x, y in pipe.path
        ]
        # Shifting one side opens a one-cell gap wherever the run crossed the line.
        # Bridging it is what keeps the pipe a pipe; without this the leg silently
        # detaches and the grid still loads.
        bridged: list[tuple[int, int]] = [moved[0]]
        for prev, cur in zip(moved, moved[1:], strict=False):
            if abs(prev[0] - cur[0]) + abs(prev[1] - cur[1]) == 2:
                bridged.append(((prev[0] + cur[0]) // 2, (prev[1] + cur[1]) // 2))
                rep.pipes_shortened[pipe.id] = rep.pipes_shortened.get(pipe.id, 0) - 1
            bridged.append(cur)
        pipe.path = bridged
        pipe.glyphs = reglyph(bridged, pipe.entry_dir, pipe.exit_dir)
        pipe.x = min(x for x, _ in bridged)
        pipe.y = min(y for _, y in bridged)

    for stray in ast.strays:
        if ax:
            if stray.y >= index:
                stray.y += 1
        elif stray.x >= index:
            stray.x += 1
    return rep


def _grow_child(child, ax: int, index: int) -> None:
    """Slide a room child forward by one, or open a blank line inside an atom."""
    if isinstance(child, Corridor):
        child.dots = [
            (x + (1 if not ax and x >= index else 0), y + (1 if ax and y >= index else 0))
            for x, y in child.dots
        ]
        if child.dots:
            child.x = min(x for x, _ in child.dots)
            child.y = min(y for _, y in child.dots)
        return
    if isinstance(child, Atom):
        if ax and child.y <= index < child.y + len(child.rows):
            local = index - child.y
            w = child.size[0]
            child.rows = child.rows[:local] + [" " * w] + child.rows[local:]
            return
        if not ax and child.x <= index < child.x + child.size[0]:
            local = index - child.x
            child.rows = [r[:local] + " " + r[local:] for r in child.rows]
            return
    if ax:
        if child.y >= index:
            child.y += 1
    elif child.x >= index:
        child.x += 1


def stretch_col(ast: Ast, x: int) -> DropReport:
    """Insert a blank grid column at `x`."""
    return _stretch(ast, "col", x)


def stretch_row(ast: Ast, y: int) -> DropReport:
    """Insert a blank grid row at `y`."""
    return _stretch(ast, "row", y)


# ── squashing one room, without cutting the grid ─────────────────────────────
def _side(room: RoomNode, cell: tuple[int, int]) -> str:
    """Which wall of `room` a cell sits on: N, S, W, E — or "" if none."""
    x, y = cell
    if y == room.y:
        return "N"
    if y == room.y + room.h + 1:
        return "S"
    if x == room.x:
        return "W"
    if x == room.x + room.w + 1:
        return "E"
    return ""


def _attachments(ast: Ast, pipe: PipeNode, room: RoomNode) -> list[tuple[str, tuple[int, int]]]:
    """``(end, wall_cell)`` for each way this pipe touches this room.

    Read off the geometry rather than trusted from ``room.ports``: a squash moves
    walls, and a stale port list is exactly how a pipe ends up attached to where a
    wall used to be — which still parses, and computes nothing.
    """
    out = []
    wall = {
        (room.x + dx, room.y + dy)
        for dx in range(room.w + 2)
        for dy in range(room.h + 2)
        if dx in (0, room.w + 1) or dy in (0, room.h + 1)
    }
    for end, term in (("src", pipe.path[0]), ("dst", pipe.path[-1])):
        if (end == "src" and pipe.src != room.id) or (end == "dst" and pipe.dst != room.id):
            continue
        for n in ((term[0] + 1, term[1]), (term[0] - 1, term[1]),
                  (term[0], term[1] + 1), (term[0], term[1] - 1)):
            if n in wall:
                out.append((end, n))
                break
    return out


def _squash(ast: Ast, room_id: int, axis: str, index: int,
            capacity: dict[tuple[int, ...], int], reroute: bool = True) -> DropReport:
    """Remove one interior line of ONE room, leaving the rest of the grid alone.

    This is the move that matters once a room spans the full width of the grid: no
    global cut exists, but the room can still be pulled in. It does **not** shrink
    the bounding box — the freed column becomes blank space east of the new wall —
    so it pays in *ticks*, by shortening every loop that was coasting across that
    column, and in free space a router can then use.

    The far wall moves inward (east for a column, south for a row) so the room's
    origin stays put and its west/north attachments are untouched. Two consequences
    have to be repaired rather than ignored:

    * a pipe on the wall that moved must grow one cell to still reach it;
    * a pipe attached to a *perpendicular* wall beyond the cut has its attach cell
      slide, and moving where a pipe lands is a re-route, not a squash. That case
      is refused by name instead of being silently mis-wired.
    """
    ax = 0 if axis == "col" else 1
    room = next((r for r in ast.rooms if r.id == room_id), None)
    if room is None:
        raise MoveError(f"no room{room_id}")
    if room.rigid_size:
        raise MoveError(f"room{room_id}'s size is fixed: {room.note}")
    if room.pinned:
        raise MoveError(f"room{room_id} is pinned: {room.note}")
    lo = room.y if ax else room.x
    hi = lo + (room.h + 1 if ax else room.w + 1)
    if not lo < index < hi:
        raise MoveError(f"{axis} {index} is not interior to room{room_id} ({lo}..{hi})")

    rep = DropReport(axis=axis, index=index, note=f"squash room{room_id}")
    occupied = [
        c for child in room.children if not isinstance(child, Corridor)
        for c in child.paint() if c[ax] == index
    ]
    if occupied:
        raise MoveError(f"{axis} {index} holds {len(occupied)} live glyph(s) in room{room_id}")
    crossing = [
        (p.id, c) for p in ast.pipes for c in p.path
        if c[ax] == index and room.x < c[0] < room.x + room.w + 1
        and room.y < c[1] < room.y + room.h + 1
    ]
    if crossing:
        raise MoveError(
            f"{axis} {index} carries pipe{crossing[0][0]} through room{room_id}'s interior"
        )

    far = "S" if ax else "E"
    perpendicular = ("W", "E") if ax else ("N", "S")
    grow: list[tuple[PipeNode, str]] = []
    move: list[tuple[PipeNode, str, str]] = []
    for pipe in ast.pipes:
        for end, wall in _attachments(ast, pipe, room):
            side = _side(room, wall)
            if side == far:
                grow.append((pipe, end))
            elif side in perpendicular and wall[ax] > index:
                # The attach cell slides, so the pipe has to land one cell over.
                # Refusing here was the wrong answer: the route is usually still
                # there, just one column across. Ask the router instead of
                # assuming — a cut "deletes" a route far less often than it looks.
                if not reroute:
                    raise MoveError(
                        f"pipe{pipe.id} attaches to room{room_id}'s {side} wall at {wall}, "
                        f"which slides when {axis} {index} goes (reroute disabled)"
                    )
                move.append((pipe, end, side))

    if ax:
        room.h -= 1
    else:
        room.w -= 1
    rep.rooms_shrunk.append(room_id)
    for child in room.children:
        _shift_child(child, ax, index)
    room.ports = [
        (x - (1 if not ax and x > index else 0), y - (1 if ax and y > index else 0))
        for x, y in room.ports
    ]

    # The wall these land on has moved one cell inward, so each pipe needs one more
    # cell to touch it. The cell it grows into is the one the old wall just left.
    for pipe, end in grow:
        term = pipe.path[0] if end == "src" else pipe.path[-1]
        step = -1 if ax else -1  # inward is north for a row, west for a column
        add = (term[0], term[1] + step) if ax else (term[0] + step, term[1])
        if end == "src":
            pipe.path = [add, *pipe.path]
        else:
            pipe.path = [*pipe.path, add]
        pipe.glyphs = reglyph(pipe.path, pipe.entry_dir, pipe.exit_dir)
        pipe.x = min(x for x, _ in pipe.path)
        pipe.y = min(y for _, y in pipe.path)
        rep.pipes_shortened[pipe.id] = -1  # grew by one, charged as negative shrink

    # Re-route the pipes whose landing cell moved. Deferred to here so the router
    # sees the room at its NEW size: routed against the old walls, every path would
    # be one cell short of the surface it is meant to touch.
    # Imported here, not at module scope: manroute depends on this module for
    # reglyph, so a top-level import is a cycle.
    from .manroute import Occupancy, route_like

    for pipe, end, side in move:
        step = -1
        old = list(pipe.path)
        term = old[0] if end == "src" else old[-1]
        want = (term[0] + step, term[1]) if not ax else (term[0], term[1] + step)
        occ = Occupancy.of(ast, ignore=frozenset({pipe.id}))
        fixed = old[-1] if end == "src" else old[0]
        # Minimal *deviation*, not minimal length: `s`/`r` bind to the nearest
        # pipe, so the shortest path is usually the wrong one — it lands somewhere
        # else and silently re-binds ops in rooms this move never touched.
        found = route_like(fixed, want, occ, prefer=set(old))
        if found is None:
            raise MoveError(
                f"pipe{pipe.id} must land on room{room_id}'s {side} wall at {want} after "
                f"{axis} {index} goes, and no route reaches it — this cut really does "
                f"delete that route"
            )
        pipe.path = found[::-1] if end == "src" else found
        pipe.glyphs = reglyph(pipe.path, pipe.entry_dir, pipe.exit_dir)
        pipe.x = min(x for x, _ in pipe.path)
        pipe.y = min(y for _, y in pipe.path)
        rep.pipes_rerouted[pipe.id] = len(pipe.path) - len(old)

    for group, need in capacity.items():
        have = ring_capacity(ast, group)
        if have < need:
            raise MoveError(f"pipe group {group} fell to {have} cells against a need of {need}")
    return rep


def _shift_content(ast: Ast, room_id: int, dx: int, dy: int) -> DropReport:
    """Slide a room's **contents** inside its walls, leaving the box where it is.

    The move that shortens a hot loop without removing anything. A room's ports sit
    on its walls at fixed columns, and ``s``/``r`` bind to the nearest pipe — so the
    boundary between two same-wall pipes falls at their midpoint, and any op that
    must bind to the far one has to sit beyond it. The lap then walks out to that
    column and back **every time round**, which makes the distance between the code
    and the boundary a per-lap cost rather than a one-off.

    Sliding the whole band one or two columns moves its pushes without changing
    their order, which is exactly how ``plotter`` took its two south send ports from
    30/38 to 30/34 and its pixel loop from 78.6 to 74.7 ticks. Nothing is added or
    deleted; only the distance to the boundary changes.

    Bindings are the thing this move puts at risk — it changes what is nearest to
    what, by design — so the caller must re-parse and diff. Geometry alone cannot
    tell you whether it worked.
    """
    room = next((r for r in ast.rooms if r.id == room_id), None)
    if room is None:
        raise MoveError(f"no room{room_id}")
    inner = {
        (x, y)
        for x in range(room.x + 1, room.x + room.w + 1)
        for y in range(room.y + 1, room.y + room.h + 1)
    }
    moved: set[tuple[int, int]] = set()
    for child in room.children:
        for cx, cy in child.paint():
            moved.add((cx + dx, cy + dy))
    outside = sorted(moved - inner)
    if outside:
        raise MoveError(
            f"shifting room{room_id} by ({dx},{dy}) puts {len(outside)} glyph(s) into or "
            f"through its wall, first at {outside[0]}"
        )
    for child in room.children:
        child.translate(dx, dy) if isinstance(child, Corridor) else _move_child(child, dx, dy)
    return DropReport(
        axis="content", index=0, rooms_shrunk=[room_id],
        note=f"shift room{room_id} contents by ({dx},{dy})",
    )


def _move_child(child, dx: int, dy: int) -> None:
    child.x += dx
    child.y += dy


def try_shift_content(
    ast: Ast, room_id: int, dx: int, dy: int
) -> tuple[Ast | None, DropReport | str]:
    """Attempt a content shift on a *copy*; return it, or why it was refused."""
    import copy

    trial = copy.deepcopy(ast)
    try:
        rep = _shift_content(trial, room_id, dx, dy)
        render(trial)
    except (MoveError, PaintError) as exc:
        return None, str(exc)
    return trial, rep


def try_squash(
    ast: Ast,
    room_id: int,
    axis: str,
    index: int,
    *,
    capacity: dict[tuple[int, ...], int] | None = None,
    reroute: bool = True,
) -> tuple[Ast | None, DropReport | str]:
    """Attempt a room-local squash on a *copy*; return it, or why it was refused.

    `reroute` lets the router move a pipe whose landing cell the squash slides.
    Pass ``False`` for the strict reading — only squashes that disturb no pipe at
    all — which is the right setting when the routing is load-bearing and you
    would rather be told than have it quietly redrawn.
    """
    import copy

    trial = copy.deepcopy(ast)
    try:
        rep = _squash(trial, room_id, axis, index, capacity or {}, reroute)
        render(trial)
    except (MoveError, PaintError) as exc:
        return None, str(exc)
    return trial, rep


