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

from .manast import Ast, Atom, Corridor, PaintError, render

__all__ = [
    "MoveError",
    "DropReport",
    "drop_row",
    "drop_col",
    "try_drop",
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


