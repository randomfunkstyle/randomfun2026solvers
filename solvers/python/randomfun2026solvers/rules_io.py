#!/usr/bin/env python3
"""IO / display / split rewrite rules (stream P6's file).

This is the **lowest-frequency** rule family, and the one whose single unsafe edit
fails the hardest: a display is judged by a *streaming frame compare* (``SPEC.md`` §
The LM-75 display), so any geometry slip — a misplaced attach, a changed address —
does not merely score worse, it fails ``verify`` outright. So this module ships a
small, heavily-guarded set and is candid about what it defers.

What it provides
================

* :func:`display_facts` — the reusable **attach analyser** for a display panel:
  which pipe attaches to which side (Top=ADDR, Left=DATA, Bottom=SWAP), and whether
  the three-attach invariant holds (exactly one pipe per used side, nothing on the
  right, nothing at a corner, and — trivially, since displays have no output ports —
  no pipe *originating* at the panel). Every display hazard in ``SPEC.md`` is a flag
  on the returned :class:`DisplayFacts`. The pipe family (P5) and the integration
  stream (S2) reuse it rather than re-deriving the sides.
* :data:`IO_DISPLAY_DEAD_PANEL` — the one **registered** rule. It shrinks a display
  panel that is drawn but **never driven** (zero pipes attach to it) down to the
  minimum 1×1 interior. An undriven panel addresses nothing and commits no frame, so
  its size is pure wasted footprint and shrinking it cannot change any committed
  frame — the safest display edit there is. It is *deliberately* inert on every real
  solution: a display-judged problem needs all three pipes, so a passing archive
  never carries an undriven panel (see :func:`display_tighten_rule` and the module
  test for why archive matches are absent).
* :func:`display_tighten_rule` — a **factory** for the resolution-aware height
  tightener a problem-aware driver (S2) would instantiate with the panel's stated
  resolution. It is surgery-free: it only fires on a display whose *shrunk* side
  (the bottom) carries no attach, and it never touches the width, because
  ``ADDR = row * width + column`` means a width change silently re-addresses every
  pixel (hazard: addressing). It is exported and unit-tested but **not registered**,
  because ``PassFn`` hands a rule only the :class:`~manparse.Program`, never the
  problem, so the target resolution is not available at match time (see *Schema
  friction* below).

Deferred, with reasons
======================

* **Resolution-aware panel shrink of a *live* 3-pipe panel.** The genuine footprint
  lever, but not safe in isolation. The SWAP pipe attaches *below* the bottom wall,
  so shrinking the panel's height requires moving that wall up **and rerouting the
  SWAP pipe** to reach it — a pipe-op relocation that belongs to the pipe family
  (P5) behind ``bindings_preserved``, not to a content rewrite. And it wins nothing
  alone: because the SWAP pipe already sits below the bottom wall, the panel's bottom
  is never the grid's lowest row, so trimming its height cannot shrink ``max(w, h)``
  until the whole south fan is re-packed with it. Left to S2 / P5.
* **``Y`` split fan-out equivalence.** Folding two rooms into one room + a ``Y``
  split (or the reverse) to save footprint needs the full inner-logic graph to prove
  the two branches are independent and side-effect-compatible, and a placement search
  to show the fold is actually smaller. No self-contained fixture proves a win, so it
  is deferred to P7 (logic graph) + S2 (placement).
* **IO passthrough elision.** Removing a relay room that only ``r`` then ``s`` with
  no transform requires re-routing the two pipes it bridged into one, preserving
  Manhattan binding, min-length-2, and every ring's declared capacity — again a pipe
  relocation (P5) gated by ``bindings_preserved``, not a content edit. Deferred.

Schema friction (reported upward)
=================================

``manrewrite.PassFn`` is ``Callable[[Program], list[Candidate]]`` — a pass sees the
grid but **not the problem**, so a rule cannot read the display's stated resolution
(``problem["io"]["display"]``) at match time. Every genuinely useful display shrink
needs that target, so display tightening cannot be a self-contained catalog rule; it
must be a problem-aware pass S2 wires with the resolution in hand. The
:func:`display_tighten_rule` factory is shaped for exactly that call. The ``io`` row
of ``manrules``' ``env`` table (``env["port"]``) also has no slot for a shrink
target; this module additionally stores ``env["target"]`` and documents it here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .manast import Ast, Port, RoomNode
from .manatom import Gadget
from .manrules import Cell, CostDelta, MatchSite, RewriteRule, register

__all__ = [
    "DisplayFacts",
    "display_facts",
    "is_oversized",
    "shrink_display",
    "display_tighten_rule",
    "IO_DISPLAY_DEAD_PANEL",
]

#: The three legal display sides and the port each drives (``SPEC.md`` § LM-75).
SIDE_FUNCTION = {"top": "ADDR", "left": "DATA", "bottom": "SWAP"}
#: The minimum interior a shrink may leave: one pixel, borders excluded.
MIN_INTERIOR = 1


def _box(room: RoomNode) -> tuple[int, int, int, int]:
    """The panel's border rectangle ``(x0, y0, x1, y1)`` — corners included."""
    bw, bh = room.size  # interior + 2 on each axis
    return room.x, room.y, room.x + bw - 1, room.y + bh - 1


def _attach_side(cell: Cell, box: tuple[int, int, int, int]) -> str | None:
    """Which display side a pipe endpoint at `cell` attaches to, or ``None``.

    Returns ``"top"`` / ``"left"`` / ``"bottom"`` for a clean interior-column (or
    interior-row) attach, ``"right"`` for the forbidden right side, ``"corner"`` for
    an endpoint that touches the panel at a corner column/row (also a load error), and
    ``None`` when the cell does not border the panel at all. The strict inequalities
    are the whole point: an attach at the corner column is a ``SPEC.md`` load error,
    not a valid top/bottom attach.
    """
    px, py = cell
    x0, y0, x1, y1 = box
    if py == y0 - 1 and x0 <= px <= x1:
        return "top" if x0 < px < x1 else "corner"
    if py == y1 + 1 and x0 <= px <= x1:
        return "bottom" if x0 < px < x1 else "corner"
    if px == x0 - 1 and y0 <= py <= y1:
        return "left" if y0 < py < y1 else "corner"
    if px == x1 + 1 and y0 <= py <= y1:
        return "right" if y0 < py < y1 else "corner"
    return None


@dataclass(frozen=True)
class DisplayFacts:
    """What is provable about a display panel's geometry and its pipe attaches.

    :param room_id: the display :class:`~manast.RoomNode` this describes.
    :param interior: the panel's ``(width, height)`` — its pixel resolution.
    :param box: the border rectangle ``(x0, y0, x1, y1)``.
    :param attaches: side name → list of ``(pipe_id, endpoint_cell)`` that attach
        there. A well-formed live panel has exactly one entry each under ``"top"``,
        ``"left"``, ``"bottom"``.
    :param hazards: every ``SPEC.md`` display violation found — a right-side attach, a
        corner attach, or two pipes on one side. Empty means the attaches are legal.
    """

    room_id: int
    interior: tuple[int, int]
    box: tuple[int, int, int, int]
    attaches: dict[str, list[tuple[int, Cell]]] = field(default_factory=dict)
    hazards: tuple[str, ...] = ()

    @property
    def well_formed(self) -> bool:
        """No display hazard: legal to reason about (may still be undriven)."""
        return not self.hazards

    @property
    def driven(self) -> bool:
        """All three ports attached exactly once — a live, display-judged panel."""
        return self.well_formed and all(len(self.attaches.get(s, [])) == 1 for s in SIDE_FUNCTION)

    @property
    def n_attaches(self) -> int:
        return sum(len(v) for v in self.attaches.values())


def display_facts(ast: Ast, room: RoomNode) -> DisplayFacts | None:
    """Analyse a display panel's pipe attaches, or ``None`` if `room` is not a display.

    Walks every pipe in `ast` and classifies whichever of its two endpoints borders
    the panel (a pipe may not originate at a display, so the attaching end is the one
    that feeds *into* it; we accept either endpoint and let the side decide). Records
    each legal top/left/bottom attach and flags every hazard — a right-side or corner
    attach, or a second pipe on a side — exactly as ``SPEC.md`` defines a load error.
    """
    if room.kind != "display":
        return None
    box = _box(room)
    attaches: dict[str, list[tuple[int, Cell]]] = {}
    hazards: list[str] = []
    for pipe in ast.pipes:
        if not pipe.path:
            continue
        for cell in {pipe.path[0], pipe.path[-1]}:
            side = _attach_side(cell, box)
            if side is None:
                continue
            if side in ("right", "corner"):
                hazards.append(f"{side}-attach@{cell}")
                continue
            attaches.setdefault(side, []).append((pipe.id, cell))
            break  # one endpoint per pipe is the attach
    for side, hits in attaches.items():
        if len(hits) > 1:
            hazards.append(f"double-attach:{side}")
    return DisplayFacts(
        room_id=room.id,
        interior=(room.w, room.h),
        box=box,
        attaches=attaches,
        hazards=tuple(hazards),
    )


def is_oversized(facts: DisplayFacts, target_w: int, target_h: int) -> bool:
    """Is the panel drawn taller than the target while keeping the target width?

    Width must match exactly: ``ADDR = row * width + column`` is computed against the
    panel's own width, so shrinking it re-addresses every pixel (hazard: addressing).
    Only a strictly-taller panel at the right width is a candidate for a safe,
    width-preserving height trim.
    """
    w, h = facts.interior
    return w == target_w and h > target_h and target_h >= MIN_INTERIOR


def shrink_display(room: RoomNode, new_w: int, new_h: int) -> None:
    """Shrink a display panel's interior to ``new_w × new_h`` in place.

    Only the bottom and right walls move (the top-left corner is fixed), so a
    :meth:`~manast.RoomNode.paint` redraw simply frees the cells the old, larger panel
    covered — nothing new is claimed, so it can never collide. The caller owns the
    safety argument that no attach or addressed pixel depended on the removed extent;
    :func:`render` with ``check=True`` is the backstop. Refuses a grow or a sub-1×1
    interior — this primitive only tightens.
    """
    if new_w < MIN_INTERIOR or new_h < MIN_INTERIOR:
        raise ValueError(f"interior {new_w}x{new_h} below the 1x1 minimum")
    if new_w > room.w or new_h > room.h:
        raise ValueError(f"{new_w}x{new_h} is not a shrink of {room.w}x{room.h}")
    room.w, room.h = new_w, new_h


def _dead_panel_gadget(_site: MatchSite) -> list[Gadget]:
    """Unused: the dead-panel rule edits geometry via :attr:`RewriteRule.apply`."""
    return []


def _dead_panel_recognize(ast: Ast, room: RoomNode) -> list[MatchSite]:
    """A display panel with **no** pipes attached and an interior larger than 1×1.

    An undriven panel addresses nothing and commits no frame, so its resolution is
    dead footprint and any size is observably identical — the one display shrink that
    needs neither the problem's resolution nor any pipe surgery. Real display-judged
    solutions drive all three ports, so this never fires on an archive (by design).
    """
    facts = display_facts(ast, room)
    if facts is None or facts.n_attaches != 0:
        return []
    w, h = facts.interior
    if w <= MIN_INTERIOR and h <= MIN_INTERIOR:
        return []
    return [
        MatchSite(
            rule=IO_DISPLAY_DEAD_PANEL,
            room_id=room.id,
            cells=frozenset(),
            entry=_DISPLAY_ENTRY,
            exits=(),
            env={"port": "display", "target": (MIN_INTERIOR, MIN_INTERIOR)},
        )
    ]


def _dead_panel_cost(site: MatchSite) -> CostDelta:
    """Cells lost by shrinking the border box to the 1×1 minimum (always negative)."""
    tw, th = _target(site)
    # The recogniser only fires when the interior exceeds 1x1, so the border box
    # ((w+2)(h+2)) strictly shrinks: d_cells < 0. Ticks per value are untouched.
    return CostDelta(d_cells=_border_cells(tw, th) - _CURRENT_MARKER, d_ticks_per_value=0.0)


def _apply_shrink(ast: Ast, site: MatchSite) -> None:
    """Shrink the matched panel to ``env["target"]`` on a (deep-copied) trial AST."""
    room = next((r for r in ast.rooms if r.id == site.room_id), None)
    if room is None:
        raise KeyError(f"room {site.room_id} not in AST")
    tw, th = _target(site)
    shrink_display(room, tw, th)


def _target(site: MatchSite) -> tuple[int, int]:
    target = site.env.get("target")
    if not (isinstance(target, tuple) and len(target) == 2):
        raise ValueError("MatchSite.env['target'] must be a (w, h) tuple")
    return int(target[0]), int(target[1])


def _border_cells(w: int, h: int) -> int:
    """Perimeter cell count of a ``w × h`` interior panel — its border draw cost."""
    bw, bh = w + 2, h + 2
    return 2 * bw + 2 * bh - 4


#: A stand-in for the pre-shrink border count in the dead-panel cost sign. The real
#: figure depends on the matched room, but the rule only fires on an interior larger
#: than 1x1, so the delta is provably negative regardless; this keeps ``d_cells`` a
#: pure, room-independent negative sentinel (the driver reads only its sign).
_CURRENT_MARKER = 1 << 20

#: The dead panel has no man walking it; a nominal entry keeps :class:`MatchSite`
#: well-typed.
_DISPLAY_ENTRY = Port(dx=0, dy=0, heading=(0, 0), note="display panel: no man")


#: The one registered ``io`` rule: shrink an **undriven** display panel to 1×1. Safe
#: without the problem (an undriven panel commits no frame), and inert on every real
#: solution (which drives all three ports). See the module docstring.
IO_DISPLAY_DEAD_PANEL = RewriteRule(
    name="io.display_dead_panel",
    family="io",
    recognize=_dead_panel_recognize,
    build=_dead_panel_gadget,
    cost_delta=_dead_panel_cost,
    clobbers=frozenset(),
    resizes_room=True,  # the panel's footprint changes
    mirrorable=False,
    apply=_apply_shrink,
)

register(IO_DISPLAY_DEAD_PANEL)


def display_tighten_rule(target_w: int | None, target_h: int | None) -> RewriteRule:
    """A resolution-aware, **surgery-free height** tightener for a display panel.

    The rule a problem-aware driver (S2) instantiates with a panel's stated
    resolution. It fires only when every safety condition holds at once:

    * the panel is well-formed (no right/corner/double attach — :func:`display_facts`);
    * its width already equals ``target_w`` (a width change re-addresses every pixel);
    * its height strictly exceeds ``target_h`` (there is something to trim);
    * **no pipe attaches to the bottom** — moving the bottom wall up would otherwise
      strand the SWAP pipe, which is pipe-family surgery, not a content edit; and
    * no left/right attach sits in a row the shrink would delete.

    ``target_w is None`` (or ``target_h``) yields a rule that never matches — the
    honest state when no resolution is available, as under the bare ``PassFn`` seam
    (see the module docstring's *Schema friction*). Not registered; exported for S2.
    """

    def recognize(ast: Ast, room: RoomNode) -> list[MatchSite]:
        if target_w is None or target_h is None:
            return []
        facts = display_facts(ast, room)
        if facts is None or not facts.well_formed:
            return []
        if not is_oversized(facts, target_w, target_h):
            return []
        if facts.attaches.get("bottom"):
            return []  # bottom attach ⇒ height trim needs SWAP-pipe surgery (deferred)
        new_y1 = room.y + (target_h + 2) - 1  # the shrunk box's bottom border row
        for side in ("left", "right"):
            for _pid, (_ax, ay) in facts.attaches.get(side, []):
                if ay >= new_y1:  # a side attach in a deleted row
                    return []
        return [
            MatchSite(
                rule=rule,
                room_id=room.id,
                cells=frozenset(),
                entry=_DISPLAY_ENTRY,
                exits=(),
                env={"port": "display", "target": (target_w, target_h)},
            )
        ]

    def cost(site: MatchSite) -> CostDelta:
        tw, th = _target(site)
        return CostDelta(d_cells=_border_cells(tw, th) - _CURRENT_MARKER, d_ticks_per_value=0.0)

    rule = RewriteRule(
        name="io.display_tighten",
        family="io",
        recognize=recognize,
        build=_dead_panel_gadget,
        cost_delta=cost,
        clobbers=frozenset(),
        resizes_room=True,
        mirrorable=False,
        apply=_apply_shrink,
    )
    return rule
