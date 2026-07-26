#!/usr/bin/env python3
"""Pipe-family rewrite rules (stream P5's file) — the most conservative catalog.

Pipes are the highest-hazard family: a send/recv binds to the *nearest attached
segment* of a pipe (SPEC "nearest, not nearest-ready", ties by reading order), so a
reshape that moves any pipe's wall-adjacent cell can silently re-bind an ``s``/``r``
to a **different** pipe without touching a single room glyph. This module therefore
ships exactly one rewrite, and it is deliberately timid:

``pipe.shorten_conduit`` — a *conduit* pipe (acyclic room-to-room latency, per
:func:`manfree.pipe_roles`) that is laid out longer than it needs to be is re-routed
to its shortest legal path between the **same two attach cells**, shrinking footprint
and latency. It fires only when every one of these holds:

* the pipe is a :class:`~manfree.PipeRole.CONDUIT` — never a ``RING`` (whose length
  *is* capacity — dropping it deadlocks the ring) and never a ``FEED`` (whose terminal
  cell *selects* a display/output port — reshaping it destroys meaning no glyph shows);
* neither endpoint room is a display panel (belt-and-suspenders over the FEED guard);
* the shorter route is *reachable at the same endpoints* — :meth:`manroute.Plan.can_reroute`
  proves free space and parity — and is **strictly shorter** than today's path;
* the target length respects the capacity floor: the declared minimum if the pipe has
  one, else :data:`manfree.PIPE_FLOOR` (2, the SPEC minimum that is still a pipe);
* **no re-bind** — the set of pipe cells orthogonally adjacent to a room wall is
  *identical* before and after (see :func:`_wall_touch`). Endpoints are held fixed by
  the reroute, so this catches the only remaining way a reshape could change a binding:
  the pipe body sweeping toward, or away from, a room wall it used to (or did not) hug.

**How we avoid a nearest-pipe re-bind without ``bindings_preserved``.** The rewrite
engine (:mod:`manrewrite`) emits ``placement=None`` for every candidate and gates only
on ``optimize.verify`` — and ``optimize.bindings_preserved`` is unusable here anyway, as
it keys a pipe's identity on *room interior content* and false-positives on any edit.
So correctness rests on two independent legs: (a) the ``_wall_touch`` invariant makes a
re-bind structurally impossible for an *accepted* candidate — endpoints are fixed and no
wall-adjacent segment is added or removed, so every ``s``/``r`` sees the same nearest
segment of every pipe; and (b) ``verify`` re-runs every public case on the real engine,
where any re-bind would change the output and reject the candidate. Either leg alone
would refuse a bad reshape; together they are the whole safety story.

**Deferred (needs a reworked binding gate).** Rewrites that *move a pipe-op cell* — a
horizontal-loop recv flipping which wall it reads, splicing a relay room onto a pipe,
re-siding an attach — are out of scope: they change which segment is nearest by
construction, and proving them safe needs a binding gate that keys on geometry, not
room content, which ``optimize`` does not yet have. The ``U``-turn / recv simplification
sketched in the plan is likewise deferred: it is rarely a footprint win and every safe
case is already reachable by shortening, so it is not worth the hazard here.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from .manast import Port
from .manfree import PIPE_FLOOR, PipeRole, pipe_roles, ring_groups
from .manroute import Plan
from .manrules import Cell, CostDelta, MatchSite, RewriteRule, register

if TYPE_CHECKING:
    from .manast import Ast, PipeNode, RoomNode

__all__ = ["SHORTEN_CONDUIT", "proposed_shortening"]


def _room_wall_cells(room: RoomNode) -> set[Cell]:
    """Every border (wall/corner) cell of a room box, absolute.

    The pipe body must not gain or lose adjacency to any of these — that is what
    keeps a shortening from re-siding which segment an ``s``/``r`` sees as nearest.
    """
    bw, bh = room.size
    cells: set[Cell] = set()
    for dx in range(bw):
        cells.add((room.x + dx, room.y))
        cells.add((room.x + dx, room.y + bh - 1))
    for dy in range(bh):
        cells.add((room.x, room.y + dy))
        cells.add((room.x + bw - 1, room.y + dy))
    return cells


def _wall_touch(ast: Ast, path: list[Cell]) -> frozenset[Cell]:
    """Path cells orthogonally adjacent to *any* room wall — the attach signature.

    A pipe's ``s``/``r`` binding is decided by which pipe segment sits nearest a
    room wall; holding this set invariant across a reshape (with endpoints pinned)
    is a structural proof that no binding moved.
    """
    walls: set[Cell] = set()
    for room in ast.rooms:
        walls |= _room_wall_cells(room)
    touch: set[Cell] = set()
    for x, y in path:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (x + dx, y + dy) in walls:
                touch.add((x, y))
                break
    return frozenset(touch)


def _target_capacity(pipe: PipeNode) -> int:
    """The floor to shorten toward: the declared minimum, else the SPEC pipe floor."""
    declared = pipe.min_capacity
    if declared is not None and declared >= PIPE_FLOOR:
        return declared
    return PIPE_FLOOR


def proposed_shortening(ast: Ast, pipe_id: int) -> PipeNode | None:
    """Re-route ``pipe_id`` to its shortest legal path on a copy, or ``None``.

    Runs the real :meth:`manroute.Plan.reroute` (transactional, endpoint-pinned) at
    the pipe's capacity floor and returns the rerouted :class:`~manast.PipeNode`.
    ``None`` when the pipe is missing, the reroute is refused (no free route / bad
    parity), or the result is **not strictly shorter** — the caller must never emit a
    site that would not shrink the pipe.
    """
    pipe = next((p for p in ast.pipes if p.id == pipe_id), None)
    if pipe is None or pipe.entry_dir is None or pipe.exit_dir is None:
        return None
    need = _target_capacity(pipe)
    trial = copy.deepcopy(ast)
    plan = Plan(trial, caps=ring_groups(trial))
    verdict = plan.reroute(pipe_id, min_capacity=need)
    if not verdict:
        return None
    new_pipe = next((p for p in plan.ast.pipes if p.id == pipe_id), None)
    if new_pipe is None or new_pipe.capacity >= pipe.capacity:
        return None
    return new_pipe


def _shortenable(ast: Ast, pipe: PipeNode, roles: dict[int, PipeRole]) -> PipeNode | None:
    """The rerouted pipe if every precondition holds, else ``None`` (a clean miss)."""
    if roles.get(pipe.id) is not PipeRole.CONDUIT:
        return None  # RING: length is capacity; FEED: terminal cell selects a port
    rooms = {r.id: r for r in ast.rooms}
    for rid in (pipe.src, pipe.dst):
        room = rooms.get(rid)
        if room is None or room.kind == "display":
            return None  # a display feed's terminus is semantic — never reshape it
    new_pipe = proposed_shortening(ast, pipe.id)
    if new_pipe is None:
        return None
    # No re-bind: the wall-adjacency signature must be byte-for-byte the same.
    if _wall_touch(ast, pipe.path) != _wall_touch(ast, new_pipe.path):
        return None
    return new_pipe


def _recognize(ast: Ast, room: RoomNode) -> list[MatchSite]:
    """Every shortenable conduit whose **source** is `room` (dedup: once per pipe).

    Called by ``manrewrite.rule_pass`` with the parsed ``Ast`` and one room. Keying on
    ``pipe.src`` means each pipe is offered exactly once across the room sweep, never
    per touching room.
    """
    roles = pipe_roles(ast)
    sites: list[MatchSite] = []
    for pipe in ast.pipes:
        if pipe.src != room.id:
            continue
        new_pipe = _shortenable(ast, pipe, roles)
        if new_pipe is None:
            continue
        sites.append(
            MatchSite(
                rule=SHORTEN_CONDUIT,
                room_id=room.id,
                cells=frozenset(pipe.path),
                entry=Port(0, 0, (0, 0)),
                exits=(),
                env={
                    "side": "conduit",
                    "capacity": _target_capacity(pipe),
                    "pipe_id": pipe.id,
                    "old_cap": pipe.capacity,
                    "new_cap": new_pipe.capacity,
                },
            )
        )
    return sites


def _apply(trial: Ast, site: MatchSite) -> None:
    """Re-route the matched conduit in `trial` (a private deep copy) to its floor.

    Re-runs the transactional :meth:`manroute.Plan.reroute` and copies the shorter
    path/glyphs back onto the pipe the engine will render. Raises (→ candidate skipped)
    if the reroute is refused on this copy — the recogniser already proved it, but a
    guard here keeps a stale site from ever mutating the tree with a longer path.
    """
    pipe_id = int(site.env["pipe_id"])  # type: ignore[call-overload]
    need = int(site.env["capacity"])  # type: ignore[call-overload]
    target = next((p for p in trial.pipes if p.id == pipe_id), None)
    if target is None:
        raise KeyError(f"pipe {pipe_id} not in AST")
    plan = Plan(trial, caps=ring_groups(trial))
    verdict = plan.reroute(pipe_id, min_capacity=need)
    if not verdict:
        raise ValueError(f"reroute refused: {verdict.reason}")
    new_pipe = next(p for p in plan.ast.pipes if p.id == pipe_id)
    if new_pipe.capacity >= target.capacity:
        raise ValueError("reroute did not shorten the pipe")
    target.path = list(new_pipe.path)
    target.glyphs = list(new_pipe.glyphs)
    target.x = new_pipe.x
    target.y = new_pipe.y


def _cost_delta(site: MatchSite) -> CostDelta:
    """Cells lost (negative) and latency shed per value (negative) by the reroute."""
    old_cap = int(site.env["old_cap"])  # type: ignore[call-overload]
    new_cap = int(site.env["new_cap"])  # type: ignore[call-overload]
    d_cells = new_cap - old_cap
    # Each cell of a conduit is one tick of pure latency a value walks through.
    return CostDelta(d_cells=d_cells, d_ticks_per_value=float(d_cells))


def _preconditions(site: MatchSite) -> bool:
    """Re-assert the site still shrinks the pipe and respects the floor (≥2)."""
    old_cap = int(site.env.get("old_cap", 0))  # type: ignore[call-overload]
    new_cap = int(site.env.get("new_cap", 0))  # type: ignore[call-overload]
    need = int(site.env.get("capacity", 0))  # type: ignore[call-overload]
    return need >= PIPE_FLOOR and PIPE_FLOOR <= new_cap < old_cap


#: Over-provisioned conduit → shortest endpoint-pinned route (footprint + latency win).
SHORTEN_CONDUIT = RewriteRule(
    name="pipe.shorten_conduit",
    family="pipe",
    recognize=_recognize,
    build=lambda _site: [],  # unused: this rule mutates via `apply`, not a gadget swap
    cost_delta=_cost_delta,
    preconditions=_preconditions,
    clobbers=frozenset(),  # a reshape touches no register
    resizes_room=False,  # content stays put; only the pipe body moves (see module doc)
    mirrorable=False,
    apply=_apply,
)

register(SHORTEN_CONDUIT)
