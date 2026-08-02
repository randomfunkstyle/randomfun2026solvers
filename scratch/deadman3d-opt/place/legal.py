#!/usr/bin/env python3
"""Is this placement legal?  Four checks, three of which reuse validated models.

A wrong answer here is the expensive kind.  A bad binding **does not fail a
build** -- the man reads from the wrong pipe and the machine answers from the
wrong place in silence, and you find out from a readback three hours later.  So
every check runs on every candidate, inside the search loop, and none of them is
allowed to be an approximation in the optimistic direction.

The four checks
---------------
1. **Pipe binding, ARCH 7.1.**  Delegated verbatim to ``z3/bind.decide``, which
   is the validated model (``z3/regress.py`` prints MODEL OK).  The rule is
   ``min(candidates, key=(distance, attach_y, attach_x))`` -- the engines' key,
   character for character.  We additionally report **margins**, because a
   binding decided by a distance tie has a one-cell margin and any later move
   flips it silently.

2. **Transparency.**  Walking a cell *executes* it, so a man crossing another
   node's glyph must not be harmed by it.  Delegated to the liveness model in
   ``live/`` -- write-sets differential-tested against the reference interpreter,
   0 mismatches over 44 glyphs.  The rule: a glyph is transparent to a passing
   man iff it does not steer, branch, split, halt or touch a pipe, **and** every
   register it writes is dead at that point on his path.

3. **Overlap.**  Two nodes may share a cell only if the shared glyph is
   identical *and* transparent to both.  Anything else is two glyphs wanting one
   cell, which is not a layout, it is a bug.

4. **Room extents and walls.**  A man may never leave his room -- stepping on a
   wall ends the whole program -- so every body cell and every walked path cell
   must lie in the room's strict interior.

What is deliberately *not* checked here
---------------------------------------
Man-vs-man collision.  Two men can never occupy a cell and every same-cell event
kills both silently, which makes it a real hazard -- but it is a *dynamic*
property of two schedules, not of a placement, and the legs this framework
places have one man each.  :func:`check` says so out loud rather than implying
coverage it does not have.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent, HERE.parent / "z3", HERE.parent / "live"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bind as _bind  # noqa: E402  (scratch/deadman3d-opt/z3/bind.py)

from ir import Placement, manhattan  # noqa: E402

__all__ = ["Violation", "check", "binding_violations", "margins", "transparent", "OPAQUE"]


# ── glyph classes ────────────────────────────────────────────────────────────
#: Glyphs that can never be crossed by a foreign man, whatever the register
#: state: they steer, branch, split, halt, or move a value through a pipe.
#: Verbatim from ``live/cpu_live.py``'s reasoning -- crossing a pipe glyph is
#: fatal even when its register writes are dead, because ``s``/``S`` inject a
#: word another block is protocol-synchronised with, ``r``/``R``/``U`` consume
#: one, and any of them can block the man indefinitely.  A blocked man is a wall
#: that kills whoever walks into him.
OPAQUE = set("<>^vVXxdaYHsSrRU")

#: ``q`` is deliberately absent from :data:`OPAQUE`: it writes BP and never
#: blocks, so it is transparent exactly when BP is dead.  ``live/`` makes the
#: same call and for the same reason.

#: Registers each glyph writes.  Sourced from :mod:`randomfun2026solvers.mansem`,
#: which is transcribed from SPEC's glyph tables and is what ``live/`` uses.
try:
    from randomfun2026solvers.mansem import glyph_effect as _effect
except Exception:  # pragma: no cover - only if the package is unavailable
    _effect = None


def writes_of(glyph: str) -> frozenset[str]:
    if _effect is None:
        raise RuntimeError("randomfun2026solvers.mansem unavailable; cannot judge liveness")
    return _effect(glyph).writes


def transparent(glyph: str, dead: frozenset[str] | set[str]) -> bool:
    """May a man whose *live* registers are the complement of ``dead`` cross this?

    ``dead`` is the set of registers that are dead at this point on the crossing
    man's path -- i.e. that he will overwrite before reading.  The glyph is
    transparent iff it is not structurally opaque and every register it writes
    is in ``dead``.
    """
    if glyph in OPAQUE:
        return False
    if glyph in (".", " ", "@"):
        return True
    try:
        w = writes_of(glyph)
    except ValueError:
        return False  # not an instruction glyph at all
    return w <= frozenset(dead)


# ── violations ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Violation:
    kind: str
    where: tuple[int, int] | None
    detail: str

    def __str__(self) -> str:
        w = f" at {self.where}" if self.where else ""
        return f"[{self.kind}]{w} {self.detail}"


# ── 1. binding ───────────────────────────────────────────────────────────────
def _glyph_records(p: Placement):
    """``(gx, gy, glyph, band)`` for every pipe-touching node, as bind.py wants."""
    out = []
    for name, n in p.leg.nodes.items():
        if n.pipe is None:
            continue
        gx, gy = n.pipe_abs(p.pos_of(name))
        g = n.body[n.pipe_at]
        out.append((gx, gy, g, n.pipe))
    return out


def binding_violations(p: Placement) -> list[Violation]:
    """ARCH 7.1, delegated verbatim to the validated ``z3/bind.decide``."""
    glyphs = _glyph_records(p)
    if not glyphs:
        return []
    touches = p.touches()
    out = []
    for rec in _bind.decide(glyphs, touches):
        gx, gy, gl, want = rec[0], rec[1], rec[2], rec[3]
        out.append(Violation("bind", (gx, gy), f"{gl!r} wants {want}: {rec[4]}"))
    return out


def margins(p: Placement):
    """Per-glyph binding slack.  0 means the binding is decided by a tie.

    A tie is legal -- the engine decides it, it does not refuse -- but it is a
    one-cell margin, and the failure mode is a wrong frame rather than an
    exception.  The search treats slack 0 as legal-but-fragile and reports it.
    """
    glyphs = _glyph_records(p)
    if not glyphs:
        return []
    return _bind.margins(glyphs, p.touches())


# ── 2 & 3. overlap and transparency ──────────────────────────────────────────
def overlap_violations(p: Placement, dead: dict[str, frozenset[str]] | None = None):
    """Two nodes on one cell is legal only if the glyph is identical and shared.

    ``dead`` maps node name -> registers dead for a man crossing *that* node's
    cells.  Absent an entry, the crossing man is assumed to need everything,
    which is the sound direction: we refuse a share we cannot prove safe.
    """
    dead = dead or {}
    out = []
    for cell, names in p.glyph_map().items():
        if len(names) < 2:
            continue
        glyphs = {p.leg.nodes[nm].occupied(p.pos_of(nm))[cell] for nm in names}
        if len(glyphs) > 1:
            out.append(Violation("overlap", cell,
                                 f"{sorted(names)} disagree: {sorted(glyphs)}"))
            continue
        g = glyphs.pop()
        opaque_owners = [nm for nm in names if p.leg.nodes[nm].opaque]
        if len(opaque_owners) > 1 and not all(
            transparent(g, dead.get(nm, frozenset())) for nm in names[1:]
        ):
            out.append(Violation("overlap", cell,
                                 f"{sorted(names)} share opaque {g!r}"))
    return out


# ── 4. room extents ──────────────────────────────────────────────────────────
def extent_violations(p: Placement) -> list[Violation]:
    """Every body cell must be in the room's strict interior.

    ``room.contains`` in the engine is ``min < pos < max`` -- the wall ring is
    *not* interior, and stepping on it is a fatal ``wall`` that ends the whole
    program, not just the man.
    """
    room = p.leg.room
    if room is None:
        return []
    x0, y0, x1, y1 = room
    out = []
    for cell in p.cells():
        if not (x0 <= cell[0] <= x1 and y0 <= cell[1] <= y1):
            out.append(Violation("extent", cell, f"outside room {room}"))
    return out


def path_violations(p: Placement) -> list[Violation]:
    """Walked edges must stay in the room too, and must be walkable.

    An edge of Manhattan distance ``d`` is walked in ``d`` ticks along an
    L-shaped path with one corner; the corner needs a steer glyph, which is
    *free* in ticks (a direction glyph sets the heading during execution, so the
    man moves the new way on the same tick) but not free in cells -- it must
    land somewhere legal.  We check the two L-paths and require at least one to
    be clear; if neither is, the Manhattan cost is a lie and the score is wrong.
    """
    room = p.leg.room
    out = []
    occupied = p.cells()
    for e in p.leg.edges:
        if e.free:
            continue
        a = p.leg.nodes[e.src].exit_abs(p.pos_of(e.src))
        b = p.leg.nodes[e.dst].entry_abs(p.pos_of(e.dst))
        if manhattan(a, b) == 0:
            continue
        ok = False
        for corner in ((b[0], a[1]), (a[0], b[1])):
            cells = _l_path(a, corner, b)
            if room is not None:
                x0, y0, x1, y1 = room
                if any(not (x0 <= c[0] <= x1 and y0 <= c[1] <= y1) for c in cells):
                    continue
            # every intermediate cell must be free or transparently crossable
            blocked = [c for c in cells[1:-1]
                       if c in occupied and occupied[c] in OPAQUE]
            if not blocked:
                ok = True
                break
        if not ok:
            out.append(Violation("path", a,
                                 f"{e.src}->{e.dst}: no clear L-path to {b}; "
                                 "the Manhattan cost is not achievable"))
    return out


def _l_path(a, corner, b):
    cells = [a]
    cur = a
    for tgt in (corner, b):
        while cur != tgt:
            dx = (tgt[0] > cur[0]) - (tgt[0] < cur[0])
            dy = (tgt[1] > cur[1]) - (tgt[1] < cur[1])
            cur = (cur[0] + dx, cur[1] + dy) if dx else (cur[0], cur[1] + dy)
            cells.append(cur)
    return cells


# ── the gate ─────────────────────────────────────────────────────────────────
def check(p: Placement, dead=None, strict_paths: bool = True) -> list[Violation]:
    """Every check, in the order that fails cheapest first.

    Returns [] for a legal placement.  **Not covered**: man-vs-man collision,
    which is a property of two schedules rather than of a placement; the legs
    placed here have one man each.
    """
    out: list[Violation] = []
    out += extent_violations(p)
    out += overlap_violations(p, dead)
    out += binding_violations(p)
    if strict_paths:
        out += path_violations(p)
    return out


def fragile(p: Placement) -> list[str]:
    """Bindings decided by a distance tie -- legal, but a one-cell margin."""
    return [
        f"({gx},{gy}) {gl!r}->{w} ties with {rn} at d={dw}"
        for slack, gx, gy, gl, w, rn, dw, rd in margins(p)
        if slack <= 0
    ]
