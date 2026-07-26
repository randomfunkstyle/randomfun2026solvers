#!/usr/bin/env python3
"""Heading/branch rewrite rules (stream P4's file) — the ``steer`` catalog slice.

Three families of edit, all registered into :data:`~randomfun2026solvers.manrules.CATALOG`
under ``"steer"`` and all expressed as in-place :attr:`~manrules.RewriteRule.apply`
callbacks (never gadget swaps), so :mod:`manrewrite` runs each on a private deep copy and
gates it on the engine:

* **``steer.trim_corridor`` — path straightening / corridor trim.** A straight run of
  ``.``/floor *nop* cells that the man merely coasts over on his way east between two
  executed anchors is pure latency: deleting it and sliding the downstream block toward
  the anchor keeps the executed-glyph order identical while shortening the walk (fewer
  ticks) inside the same room walls (footprint and pipe geometry untouched). This is the
  one rule here that yields a strict engine-verified score win, and the one the slow test
  proves end to end.
* **``steer.coalesce_resteer`` — redundant re-steer elision.** Two consecutive identical
  steers along the man's line of travel (``>>`` on an eastbound run, ``vv`` on a
  southbound one): the first fixes the heading unconditionally, so every steer after it
  re-asserts a heading the man already holds and is a no-op. It is blanked to ``.``. A
  normalising building block (score-neutral on its own).
* **``steer.branch_const`` — provable ``X`` sign-branch simplification.** ``X`` turns by
  ``sign(A)`` (SPEC: clockwise if ``A>0``, counter-clockwise if ``A<0``, straight if
  ``A==0``, relative to the current heading). When ``A`` is a **provable literal** at the
  ``X`` (read straight off the run it terminates) the branch is unconditional, so ``X`` is
  replaced by the fixed absolute steer the turn resolves to (or a ``.`` nop when ``A==0``,
  which needs no heading at all). Very conservative: if the sign is not provable, or the
  incoming heading is not locally certain, it refuses. It never deletes an arm — the "dead
  arm removal" hazard (plan hazard, "only if provably unreachable & removable") is left to
  a later, footprint-motivated pass.

**How the two hardest guards are implemented.**

* *Execution-order preservation* (plan hazard #5) uses :func:`~manroute.route_man`. For a
  corridor trim the man-walk between the two anchors is routed with every executing glyph
  marked ``code`` (off-limits): the trim fires only when that route is a single straight,
  turn-free eastbound lane over the corridor — so no op or steer hides on it, the ordered
  list of executed turn-cells is empty before *and* after, and the walk is exactly ``L``
  cells shorter. If an op or steer sits on the lane the route detours off-row (or the ticks
  do not shrink) and the rule misses.
* *transparent vs shareable* (plan hazard #4) uses :class:`~manstruct.CellInfo`. Every cell
  the corridor occupies must be :attr:`~manstruct.CellInfo.shareable` (bare floor / a nop),
  and every cell the downstream block slides *onto* must currently be shareable too — so a
  relocated glyph can never cross another lane's exclusive turn (which would execute it).

Pipe-op rebinding (plan hazard #1) is not separately proven here: these are content edits
carrying ``placement=None``, so :func:`optimize.verify` re-runs every public case on the
real engine and a silent ``s``/``r`` rebind changes the output and is rejected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .manast import Corridor, Node, Port, Run
from .manroute import route_man
from .manrules import Cell, CostDelta, MatchSite, RewriteRule, register
from .manstruct import CellInfo, Kind, _classify_glyph, _exits_for

if TYPE_CHECKING:
    from .manast import Ast, RoomNode

__all__ = [
    "TRIM_CORRIDOR",
    "COALESCE_RESTEER",
    "BRANCH_CONST",
    "const_a_terminal_x",
    "steer_after",
]

# ── heading algebra (validated glyph-by-glyph against the reference engine) ─────
_EAST: tuple[int, int] = (1, 0)
_SOUTH: tuple[int, int] = (0, 1)
#: absolute heading -> the steer glyph that forces it.
_STEER_GLYPH: dict[tuple[int, int], str] = {(1, 0): ">", (-1, 0): "<", (0, -1): "^", (0, 1): "v"}
#: run storage orientation -> the man's heading while executing it left-to-right / top-down.
_RUN_HEADING: dict[str, tuple[int, int]] = {"E": _EAST, "S": _SOUTH}


def _cw(d: tuple[int, int]) -> tuple[int, int]:
    """Clockwise quarter-turn in screen coordinates (y grows south)."""
    dx, dy = d
    return (-dy, dx)


def _ccw(d: tuple[int, int]) -> tuple[int, int]:
    """Counter-clockwise quarter-turn in screen coordinates."""
    dx, dy = d
    return (dy, -dx)


def steer_after(heading: tuple[int, int], sign: int) -> str:
    """The glyph an ``X`` with fixed ``sign`` resolves to for a man entering on `heading`.

    SPEC ``X``: clockwise if ``A>0``, counter-clockwise if ``A<0``, straight if ``A==0``.
    The straight case is heading-independent (a nop), so it returns ``"."``; the turning
    cases return the absolute steer for the rotated heading. Confirmed on the engine:
    east + positive turns south (``v``), east + negative turns north (``^``).
    """
    if sign == 0:
        return "."
    turned = _cw(heading) if sign > 0 else _ccw(heading)
    return _STEER_GLYPH[turned]


# ── engine-free per-room cell classification (CellInfo, no wasm oracle) ─────────
def _room_cellinfo(room: RoomNode) -> dict[Cell, CellInfo]:
    """A :class:`~manstruct.CellInfo` map for `room`, built without the engine.

    Mirrors :func:`manstruct._build_cells` for a single room off the AST alone: the
    interior glyph at each cell is whatever the room's children paint there, blank cells
    are floor, and the border is wall/IO — enough for :attr:`CellInfo.shareable` and
    :meth:`CellInfo.crossable_by`, which the corridor guard reads.
    """
    painted = room.paint()
    bw, bh = room.size  # interior + 2 (walls)
    out: dict[Cell, CellInfo] = {}
    for dy in range(bh):
        for dx in range(bw):
            x, y = room.x + dx, room.y + dy
            glyph = painted.get((x, y), " ")
            border = dx in (0, bw - 1) or dy in (0, bh - 1)
            kind = _classify_glyph(glyph, on_border=border)
            out[(x, y)] = CellInfo(x, y, glyph, kind, room=room.id, exits=_exits_for(kind, glyph))
    return out


#: Cell kinds the man may freely coast over — pure latency, never executed as code.
_WALKABLE = frozenset({Kind.FLOOR, Kind.NOP, Kind.VOID})


def _code_cells(cells: dict[Cell, CellInfo]) -> set[Cell]:
    """Every cell that is *not* free floor — an obstacle for :func:`route_man`."""
    return {c for c, info in cells.items() if info.kind not in _WALKABLE}


# ── rule 1: path straightening / corridor trim ─────────────────────────────────
def _horizontal_segments(dots: list[Cell]) -> list[tuple[int, int, int]]:
    """Maximal contiguous horizontal runs of `dots`, each as ``(x0, x1, y)``."""
    by_row: dict[int, list[int]] = {}
    for x, y in dots:
        by_row.setdefault(y, []).append(x)
    segs: list[tuple[int, int, int]] = []
    for y, xs in by_row.items():
        xs.sort()
        run_start = prev = xs[0]
        for x in xs[1:]:
            if x == prev + 1:
                prev = x
                continue
            segs.append((run_start, prev, y))
            run_start = prev = x
        segs.append((run_start, prev, y))
    return segs


def _trim_candidate(room: RoomNode, seg: tuple[int, int, int]) -> tuple[int, list[Node]] | None:
    """Vet one corridor segment; return ``(L, movers)`` if trimmable, else ``None``.

    The downstream block is every non-corridor child whose cells all lie on the segment's
    row, strictly east of it. A child that straddles the row *partly* east (a multi-row
    gadget) would shear, so its presence is a hard miss — conservative by construction.
    """
    x0, x1, y = seg
    length = x1 - x0 + 1
    movers: list[Node] = []
    for child in room.children:
        if isinstance(child, Corridor):
            continue
        cs = child.paint()
        if not cs:
            continue
        row_xs = [cx for (cx, cy) in cs if cy == y]
        east_of_seg = [cx for cx in row_xs if cx > x1]
        if not east_of_seg:
            if any(cy == y and cx > x1 for (cx, cy) in cs):  # unreachable belt-and-braces
                return None
            continue
        # has cells on the row east of the segment: only movable if ENTIRELY on this row.
        if any(cy != y for (_cx, cy) in cs) or min(cx for cx, _ in cs) <= x1:
            return None  # straddles the segment or spans rows -> would shear
        movers.append(child)
    if not movers:
        return None
    return length, movers


def _recognize_trim(ast: Ast, room: RoomNode) -> list[MatchSite]:
    """Every trimmable eastbound nop corridor in `room`, as :class:`MatchSite`s."""
    if room.kind != "compute":
        return []
    cells = _room_cellinfo(room)
    code = _code_cells(cells)
    xs = [x for x, _ in cells]
    ys = [y for _, y in cells]
    bx, by = (max(xs) if xs else 0), (max(ys) if ys else 0)
    sites: list[MatchSite] = []
    for child in room.children:
        if not isinstance(child, Corridor):
            continue
        for seg in _horizontal_segments(child.dots):
            x0, x1, y = seg
            length = x1 - x0 + 1
            # shareable guard (hazard #4): the corridor itself is pure crossable floor.
            if not all(
                (info := cells.get((x, y))) is not None
                and info.shareable
                and info.crossable_by("E")
                for x in range(x0, x1 + 1)
            ):
                continue
            west, east = (x0 - 1, y), (x1 + 1, y)
            # bounded on both sides by an executed anchor -> this is the man's lane.
            wi, ei = cells.get(west), cells.get(east)
            if wi is None or ei is None or wi.kind in _WALKABLE or ei.kind in _WALKABLE:
                continue
            # exec-order guard (hazard #5): a single straight turn-free eastbound walk.
            lane = _straight_east_lane(code, west, east, bx, by)
            if lane is None or lane.ticks <= length:
                continue
            vetted = _trim_candidate(room, seg)
            if vetted is None:
                continue
            _length, movers = vetted
            # destination-shareable guard: every cell a mover slides onto is free floor
            # now (never another lane's exclusive turn).
            moved: set[Cell] = set()
            for m in movers:
                for cx, cy in m.paint():
                    moved.add((cx - length, cy))
            occupied = {
                c
                for c, info in cells.items()
                if info.kind not in _WALKABLE and info.kind is not Kind.SPAWN
            }
            mover_cells = {c for m in movers for c in m.paint()}
            seg_cells = {(x, y) for x in range(x0, x1 + 1)}
            if any(d in occupied and d not in mover_cells and d not in seg_cells for d in moved):
                continue
            sites.append(
                MatchSite(
                    rule=TRIM_CORRIDOR,
                    room_id=room.id,
                    cells=frozenset(seg_cells),
                    entry=Port(dx=west[0] - room.x, dy=y - room.y, heading=_EAST),
                    exits=(Port(dx=east[0] - room.x, dy=y - room.y, heading=_EAST),),
                    env={
                        "headings": [],  # a pure floor lane coalesces no steers
                        "segment": seg,
                        "length": length,
                        "before_ticks": lane.ticks,
                    },
                )
            )
    return sites


def _straight_east_lane(code: set[Cell], west: Cell, east: Cell, bx: int, by: int):
    """route_man from `west` to `east` heading east, minus the two anchors from `code`.

    Returns the :class:`~manroute.ManPath` iff it is a single straight, turn-free lane on
    the anchors' row; ``None`` otherwise (a detour or a bend means an op/steer lies on it).
    """
    obstacles = code - {west, east}
    path = route_man(west, _EAST, east, _EAST, code=obstacles, bound_x=bx, bound_y=by)
    if path is None or path.turns:
        return None
    if any(cy != west[1] for _cx, cy in path.cells):
        return None
    return path


def _apply_trim(trial: Ast, site: MatchSite) -> None:
    """Delete the matched corridor segment and slide the downstream block west by ``L``."""
    room = next((r for r in trial.rooms if r.id == site.room_id), None)
    if room is None:
        raise KeyError(f"room {site.room_id} gone")
    x0, x1, y = site.env["segment"]  # type: ignore[misc]
    length = int(site.env["length"])  # type: ignore[arg-type]
    seg_cells = {(x, y) for x in range(x0, x1 + 1)}

    vetted = _trim_candidate(room, (x0, x1, y))
    if vetted is None:
        raise ValueError("segment no longer trimmable in trial")
    _length, movers = vetted

    for child in room.children:
        if isinstance(child, Corridor):
            child.dots = [d for d in child.dots if d not in seg_cells]
    room.children = [
        c for c in room.children if not (isinstance(c, Corridor) and not c.dots)
    ]
    for mover in movers:
        mover.translate(-length, 0)


def _cost_trim(site: MatchSite) -> CostDelta:
    """``L`` fewer nop cells painted and ``L`` fewer ticks coasted on the man's lane."""
    length = int(site.env["length"])  # type: ignore[arg-type]
    return CostDelta(d_cells=-length, d_ticks_per_value=float(-length))


#: A straight eastbound nop corridor delaying a downstream op -> deleted, block slid west.
TRIM_CORRIDOR = RewriteRule(
    name="steer.trim_corridor",
    family="steer",
    recognize=_recognize_trim,
    build=lambda _site: [],  # unused: this rule edits in place via `apply`
    cost_delta=_cost_trim,
    apply=_apply_trim,
    clobbers=frozenset(),
    resizes_room=False,  # walls unchanged; a pure interior latency trim
    mirrorable=False,
)
register(TRIM_CORRIDOR)


# ── rule 2: redundant re-steer elision ─────────────────────────────────────────
#: For each run orientation, the steer glyph that continues *along* the run — the only
#: one whose duplicate the man actually reaches (others send him off the line).
_INLINE_STEER: dict[str, str] = {"E": ">", "S": "v"}


def _redundant_resteer_index(glyphs: str, heading: str) -> int | None:
    """Index of the first redundant inline re-steer in `glyphs`, or ``None``.

    A steer that continues along the run (``>`` on an eastbound run, ``v`` on a southbound
    one) immediately following the same glyph is a no-op: the previous cell already forced
    that heading. Returns the position of such a second glyph.
    """
    g = _INLINE_STEER.get(heading)
    if g is None:
        return None
    pair = g + g
    idx = glyphs.find(pair)
    return idx + 1 if idx >= 0 else None


def _recognize_resteer(ast: Ast, room: RoomNode) -> list[MatchSite]:
    """Every run carrying a redundant inline re-steer, as :class:`MatchSite`s."""
    sites: list[MatchSite] = []
    for child in room.children:
        if not isinstance(child, Run):
            continue
        idx = _redundant_resteer_index(child.glyphs, child.heading)
        if idx is None:
            continue
        sites.append(
            MatchSite(
                rule=COALESCE_RESTEER,
                room_id=room.id,
                cells=frozenset(child.paint()),
                entry=Port(dx=0, dy=0, heading=_RUN_HEADING[child.heading]),
                exits=(),
                env={"headings": [child.glyphs[idx]], "run_id": child.id, "index": idx},
            )
        )
    return sites


def _apply_resteer(trial: Ast, site: MatchSite) -> None:
    """Blank the redundant steer to a ``.`` nop in the matched run."""
    room = next((r for r in trial.rooms if r.id == site.room_id), None)
    if room is None:
        raise KeyError(f"room {site.room_id} gone")
    run = next(
        (
            c
            for c in room.children
            if isinstance(c, Run) and c.id == site.env["run_id"] and c.heading in _INLINE_STEER
        ),
        None,
    )
    if run is None:
        raise KeyError("target run gone")
    idx = int(site.env["index"])  # type: ignore[arg-type]
    g = _INLINE_STEER[run.heading]
    if idx <= 0 or idx >= len(run.glyphs) or run.glyphs[idx] != g or run.glyphs[idx - 1] != g:
        raise ValueError("re-steer no longer present")
    run.glyphs = run.glyphs[:idx] + "." + run.glyphs[idx + 1 :]


def _cost_resteer(_site: MatchSite) -> CostDelta:
    """One live glyph becomes a nop; ticks and footprint are untouched."""
    return CostDelta(d_cells=-1, d_ticks_per_value=0.0)


#: A duplicated inline steer (``>>`` / ``vv``) -> the redundant one is blanked to ``.``.
COALESCE_RESTEER = RewriteRule(
    name="steer.coalesce_resteer",
    family="steer",
    recognize=_recognize_resteer,
    build=lambda _site: [],
    cost_delta=_cost_resteer,
    apply=_apply_resteer,
    clobbers=frozenset(),
    resizes_room=False,
    mirrorable=False,
)
register(COALESCE_RESTEER)


# ── rule 3: provable X sign-branch simplification ──────────────────────────────
def _literal_number(glyphs: str, i: int) -> tuple[int, int] | None:
    """Parse a backtick literal starting at ``glyphs[i] == '`'``; return ``(value, end)``.

    ``end`` is the index just past the closing backtick. Returns ``None`` for an
    unterminated or non-numeric span (so the prover stays conservative).
    """
    j = glyphs.find("`", i + 1)
    if j < 0:
        return None
    inner = glyphs[i + 1 : j].replace(" ", "")
    if not inner.isdigit():
        return None
    return int(inner), j + 1


def const_a_terminal_x(glyphs: str) -> int | None:
    """The provable value of ``A`` at a trailing ``X``, or ``None`` if not provable.

    Walks a straight run left-to-right (the man's execution order), tracking ``A`` only
    through the glyphs it can prove: bare digits and backtick literals set it, ``N`` negates
    it, and glyphs that leave ``A`` alone (``M``, steers, spawn, nops, ``b``/``m``/``]``,
    sends) pass it through. **Anything that writes ``A`` to an unknown value** — a receive,
    an arithmetic op, ``W`` (swaps in an unknown ``B``) — drops the proof to ``None``. The
    run must end in ``X`` with a proven ``A`` for the branch to be constant.
    """
    if not glyphs.endswith("X"):
        return None
    a: int | None = None
    i = 0
    body = glyphs[:-1]
    while i < len(body):
        ch = body[i]
        if ch == "`":
            parsed = _literal_number(body, i)
            if parsed is None:
                return None
            a, i = parsed
            continue
        if ch.isdigit():
            a = int(ch)
        elif ch == "N":
            if a is None:
                return None
            a = -a
        elif ch in "@ .Mbm]sS><^vV":
            pass  # leaves A untouched (or is a nop / spawn / steer)
        else:
            return None  # r R U + - * / % & | ~ { } W  -> A becomes unknown
        i += 1
    return a


def _recognize_branch(ast: Ast, room: RoomNode) -> list[MatchSite]:
    """Every run ending in a provably-constant ``X``, as :class:`MatchSite`s."""
    sites: list[MatchSite] = []
    for child in room.children:
        if not isinstance(child, Run) or child.heading not in _RUN_HEADING:
            continue
        if not child.glyphs.endswith("X"):
            continue
        value = const_a_terminal_x(child.glyphs)
        if value is None:
            continue  # sign not provable -> skip (very conservative)
        heading = _RUN_HEADING[child.heading]
        replacement = steer_after(heading, _sign(value))
        sites.append(
            MatchSite(
                rule=BRANCH_CONST,
                room_id=room.id,
                cells=frozenset(child.paint()),
                entry=Port(dx=0, dy=0, heading=heading),
                exits=(Port(dx=0, dy=0, heading=heading, note="folded"),),
                env={
                    "headings": [replacement],
                    "run_id": child.id,
                    "heading": heading,
                    "value": value,
                    "replacement": replacement,
                },
            )
        )
    return sites


def _sign(value: int) -> int:
    """``-1`` / ``0`` / ``+1`` — the only thing ``X`` reads."""
    return (value > 0) - (value < 0)


def _apply_branch(trial: Ast, site: MatchSite) -> None:
    """Replace the terminal ``X`` with the fixed steer the constant sign resolves to."""
    room = next((r for r in trial.rooms if r.id == site.room_id), None)
    if room is None:
        raise KeyError(f"room {site.room_id} gone")
    run = next(
        (c for c in room.children if isinstance(c, Run) and c.id == site.env["run_id"]),
        None,
    )
    if run is None or not run.glyphs.endswith("X"):
        raise KeyError("terminal X gone")
    run.glyphs = run.glyphs[:-1] + str(site.env["replacement"])


def _cost_branch(_site: MatchSite) -> CostDelta:
    """One-for-one glyph swap: the branch collapses to a steer, size and ticks unchanged."""
    return CostDelta(d_cells=0, d_ticks_per_value=0.0)


def _precondition_branch(site: MatchSite) -> bool:
    """Re-derive the folded glyph from the stored heading + sign; refuse on any drift."""
    value = site.env.get("value")
    heading = site.env.get("heading")
    repl = site.env.get("replacement")
    if not isinstance(value, int) or not isinstance(heading, tuple) or not isinstance(repl, str):
        return False
    return repl == steer_after(heading, _sign(value))


#: ``X`` with a provable constant ``A`` -> the fixed absolute steer (or ``.`` when ``A==0``).
BRANCH_CONST = RewriteRule(
    name="steer.branch_const",
    family="steer",
    recognize=_recognize_branch,
    build=lambda _site: [],
    cost_delta=_cost_branch,
    apply=_apply_branch,
    preconditions=_precondition_branch,
    clobbers=frozenset(),
    resizes_room=False,
    mirrorable=False,
)
register(BRANCH_CONST)
