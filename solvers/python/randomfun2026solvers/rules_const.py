#!/usr/bin/env python3
"""Constant-family rewrite rules (stream P3's file).

These rules touch the *literals* a room loads into hand ``A`` — the bare digits
``0``-``9`` and the backtick-delimited numbers ``` `12` ``` — and shrink the code
that writes them without changing what it computes. They register into
:data:`~randomfun2026solvers.manrules.CATALOG` under the ``"const"`` family. Three
rewrites live here:

* **``const.dead_literal``** — a literal load into ``A`` whose value is overwritten
  before it is ever read is *dead*; delete it. Deadness is proved conservatively by
  walking the run's glyphs forward from the literal
  (:func:`_a_status_after`): the very next glyph that touches ``A`` must **write** it
  without **reading** it (``r`` / ``R`` / ``U`` / another literal). A glyph that reads
  ``A`` first (``M`` / ``+`` / ``s`` / ``X`` / …) proves the value *live* and refuses
  the match; reaching the run's end is *unknown* (the value may be read downstream, in
  a later run or at a branch/halt) and also refuses. So a match only fires when the
  clobber is visible **inside the same straight run**.

* **``const.shrink_backtick``** — a single-digit backtick literal ``` `5` ``` costs
  three cells for a value a bare ``5`` writes in one. Re-encode it to the bare digit.
  This is the footprint-reducing direction only (never bare→backtick).

* **``const.hoist_reload``** — the same literal loaded twice on one straight run with
  no ``A``-write between the two loads: the second is a redundant reload (``A`` still
  holds the value), so drop it and keep the first.

**The backtick-reversal hazard (plan hazard #3) is the sharp edge.** A backtick
literal loads on its *closing* tick with the digits reversed L→R (and vertically), so
``` `12` ``` read backwards would load ``21`` — a different number. Two guards keep
every rewrite value-exact:

* ``const.shrink_backtick`` re-encodes **only a single-digit** interior. A one-digit
  number is its own reverse, so bare ``5`` and ``` `5` ``` load the identical value; a
  multi-digit interior — even a palindrome like ``` `55` ``` (which loads ``55``, while
  the bare digits ``55`` would load ``5``) — is refused. The check is enforced by
  ``len(interior) == 1`` **and** :func:`~randomfun2026solvers.manstruct._mirror_safe`.
* Every rewrite here only **deletes or shrinks in place** inside one run; it never
  mirrors, relocates, or reorders a literal, so no load is ever read in a new
  orientation.

The transforms edit a room's glyph string, which shifts the following glyphs one or
more cells earlier along the man's path — usually reaching the value-producing send a
tick sooner, the tick win. They are pure content edits: the applier leaves the
driver's ``placement`` at ``None``, and correctness rests on ``optimize.verify``
running every public case on the engine (a mis-proved deadness or a rebound pipe would
change the output and be rejected). See :mod:`randomfun2026solvers.manrewrite`.
"""

from __future__ import annotations

from .manast import Ast, Port, RoomNode, Run
from .manrules import CostDelta, MatchSite, RewriteRule, register
from .mansem import glyph_effect
from .manstruct import _mirror_safe

__all__ = [
    "DEAD_LITERAL",
    "SHRINK_BACKTICK",
    "HOIST_RELOAD",
    "Literal",
    "literals",
]

#: The dummy entry port every const site carries. The applier is an in-place glyph
#: edit (:attr:`~manrules.RewriteRule.apply`), so it never reads the port geometry —
#: it is present only to satisfy the :class:`MatchSite` schema.
_ENTRY = Port(dx=0, dy=0, heading=(1, 0))

#: A literal token found in a run: ``(start, end, value, is_backtick, interior)``.
#: ``start``/``end`` are the half-open glyph-string slice; ``value`` is the integer
#: it loads (``None`` for a malformed backtick span); ``interior`` is the digits.
Literal = tuple[int, int, "int | None", bool, str]


# ── tokenising a run into its literal loads ───────────────────────────────────
def literals(glyphs: str) -> list[Literal]:
    """Every literal load in `glyphs`, left to right.

    A bare digit is one token (each digit is its own ``A``-write — ``45`` loads
    ``4`` then ``5``, ending on ``5``); a backtick span ``` `NN` ``` is one token
    whose value is the enclosed integer. An unbalanced trailing backtick stops the
    scan (nothing after it can be trusted), so the result stays conservative.
    """
    out: list[Literal] = []
    i = 0
    n = len(glyphs)
    while i < n:
        c = glyphs[i]
        if c == "`":
            j = glyphs.find("`", i + 1)
            if j == -1:
                break  # unbalanced: refuse to reason past it
            inner = glyphs[i + 1 : j]
            value = int(inner) if inner.isdigit() and inner else None
            out.append((i, j + 1, value, True, inner))
            i = j + 1
        elif c.isdigit():
            out.append((i, i + 1, int(c), False, c))
            i += 1
        else:
            i += 1
    return out


def _a_status_after(glyphs: str, pos: int) -> str:
    """Is hand ``A`` dead at `pos`? ``"dead"`` / ``"live"`` / ``"unknown"``.

    Walks forward from `pos`. The first glyph that **writes** ``A`` without needing
    it (a literal, ``r`` / ``R`` / ``U``) proves the value dead; the first that
    **reads** ``A`` (``M`` / ``+`` / ``s`` / ``X`` / …) proves it live. Glyphs that
    touch only ``B`` / ``BP`` (``m`` ``]`` ``q``) or nothing (``.`` space) are
    skipped. An opaque glyph the ISA table does not know (e.g. the spawn ``@``) is
    treated as a possible read — conservatively ``"live"``. Reaching the end without
    a verdict is ``"unknown"`` (the value may be read in a later run or at a branch).
    """
    for i in range(pos, len(glyphs)):
        c = glyphs[i]
        if c == "`" or c.isdigit():
            return "dead"  # a following literal overwrites A before any read
        try:
            eff = glyph_effect(c)
        except ValueError:
            return "live"  # opaque glyph: assume it may read A
        if "A" in eff.needs:
            return "live"
        if "A" in eff.writes:
            return "dead"
    return "unknown"


def _no_a_write_between(glyphs: str, start: int, end: int) -> bool:
    """True when nothing in ``glyphs[start:end]`` writes ``A``.

    Used by the hoist rule: if ``A`` is never re-assigned between two identical
    literal loads then the value still stands at the second, so the second load is
    redundant. Reads of ``A`` are fine (they leave the value intact); any write — a
    literal, an arithmetic op, ``r`` — or an opaque glyph refuses the match.
    """
    for i in range(start, end):
        c = glyphs[i]
        if c == "`" or c.isdigit():
            return False
        try:
            eff = glyph_effect(c)
        except ValueError:
            return False
        if "A" in eff.writes:
            return False
    return True


def _token_cells(run: Run, start: int, end: int) -> list[tuple[int, int]]:
    """Absolute cells a run's ``glyphs[start:end]`` occupies, for the MatchSite."""
    dx, dy = (1, 0) if run.heading == "E" else (0, 1)
    return [(run.x + i * dx, run.y + i * dy) for i in range(start, end)]


def _find_room(ast: Ast, room_id: int) -> RoomNode | None:
    return next((r for r in ast.rooms if r.id == room_id), None)


# ── the shared in-place applier ───────────────────────────────────────────────
def _apply_span(ast: Ast, site: MatchSite) -> None:
    """Replace ``run.glyphs[start:end]`` with ``env["replacement"]`` in place.

    Every const rule is a single-run glyph splice, so they share one applier. It
    locates the run by ``(room_id, run_id)`` — stable across the deep copy the engine
    hands us — and rewrites its glyph string; following glyphs shift earlier along the
    man's path, which is the whole point. Raises if the run is gone (caught upstream:
    the candidate is simply dropped).
    """
    env = site.env
    room = _find_room(ast, site.room_id)
    if room is None:
        raise KeyError(f"room {site.room_id} not in AST")
    run_id = env["run_id"]
    start, end = env["span"]  # type: ignore[misc]
    replacement = env["replacement"]
    for child in room.children:
        if isinstance(child, Run) and child.id == run_id:
            g = child.glyphs
            child.glyphs = g[:start] + replacement + g[end:]  # type: ignore[operator]
            return
    raise KeyError(f"run {run_id} not in room {site.room_id}")


def _no_build(_site: MatchSite) -> list:
    """Const rules apply in place; the gadget-swap ``build`` path is never taken."""
    raise NotImplementedError("const rules use apply(), not build()")


# ── const.dead_literal ────────────────────────────────────────────────────────
def _recognize_dead(_ast: object, room: RoomNode) -> list[MatchSite]:
    """Every dead literal load in a compute room, as deletion sites."""
    if room.kind != "compute":
        return []
    sites: list[MatchSite] = []
    for child in room.children:
        if not isinstance(child, Run):
            continue
        g = child.glyphs
        for start, end, value, _is_bt, _inner in literals(g):
            if value is None:
                continue
            if _a_status_after(g, end) != "dead":
                continue  # value is read (or unknown downstream) → keep it
            sites.append(
                MatchSite(
                    rule=DEAD_LITERAL,
                    room_id=room.id,
                    cells=frozenset(_token_cells(child, start, end)),
                    entry=_ENTRY,
                    exits=(),
                    env={
                        "value": value,
                        "run_id": child.id,
                        "span": (start, end),
                        "replacement": "",
                        "dead": True,
                    },
                )
            )
    return sites


def _cost_dead(site: MatchSite) -> CostDelta:
    """Fewer cells and (when the clobber precedes the send) fewer ticks — both wins."""
    start, end = site.env["span"]  # type: ignore[misc]
    width = end - start
    return CostDelta(d_cells=-width, d_ticks_per_value=-float(width))


def _pre_dead(site: MatchSite) -> bool:
    """Re-assert the recogniser's deadness flag and a real integer value."""
    return bool(site.env.get("dead")) and isinstance(site.env.get("value"), int)


#: A literal whose ``A`` is overwritten before any read → delete it.
DEAD_LITERAL = RewriteRule(
    name="const.dead_literal",
    family="const",
    recognize=_recognize_dead,
    build=_no_build,
    cost_delta=_cost_dead,
    preconditions=_pre_dead,
    clobbers=frozenset(),  # removes a write; never introduces a new one
    resizes_room=False,
    mirrorable=False,
    apply=_apply_span,
)


# ── const.shrink_backtick ─────────────────────────────────────────────────────
def _recognize_shrink(_ast: object, room: RoomNode) -> list[MatchSite]:
    """Every single-digit backtick literal, as a shrink-to-bare-digit site."""
    if room.kind != "compute":
        return []
    sites: list[MatchSite] = []
    for child in room.children:
        if not isinstance(child, Run):
            continue
        g = child.glyphs
        for start, end, value, is_bt, inner in literals(g):
            if not is_bt or value is None:
                continue
            # Value-exactness (hazard #3): only a one-digit interior loads the same
            # number bare as delimited; a longer span (even palindromic `55`, which
            # loads 55 while bare `55` loads 5) is refused. `_mirror_safe` is the
            # belt to `len == 1`'s braces.
            if len(inner) != 1 or not inner.isdigit():
                continue
            if not _mirror_safe(g[start:end]):
                continue
            sites.append(
                MatchSite(
                    rule=SHRINK_BACKTICK,
                    room_id=room.id,
                    cells=frozenset(_token_cells(child, start, end)),
                    entry=_ENTRY,
                    exits=(),
                    env={
                        "value": value,
                        "run_id": child.id,
                        "span": (start, end),
                        "replacement": inner,
                        "single_digit": True,
                    },
                )
            )
    return sites


def _cost_shrink(_site: MatchSite) -> CostDelta:
    """``` `d` ``` (3 cells) → ``d`` (1 cell): two cells gone, two ticks shifted out."""
    return CostDelta(d_cells=-2, d_ticks_per_value=-2.0)


def _pre_shrink(site: MatchSite) -> bool:
    """Re-assert a single-digit value in ``0``-``9`` before the applier runs."""
    value = site.env.get("value")
    return bool(site.env.get("single_digit")) and isinstance(value, int) and 0 <= value <= 9


#: A single-digit backtick literal ``` `5` ``` → the bare digit ``5``.
SHRINK_BACKTICK = RewriteRule(
    name="const.shrink_backtick",
    family="const",
    recognize=_recognize_shrink,
    build=_no_build,
    cost_delta=_cost_shrink,
    preconditions=_pre_shrink,
    clobbers=frozenset(),
    resizes_room=False,
    mirrorable=False,
    apply=_apply_span,
)


# ── const.hoist_reload ────────────────────────────────────────────────────────
def _recognize_hoist(_ast: object, room: RoomNode) -> list[MatchSite]:
    """Redundant reloads of a literal on one straight run, as drop-the-second sites."""
    if room.kind != "compute":
        return []
    sites: list[MatchSite] = []
    for child in room.children:
        if not isinstance(child, Run):
            continue
        g = child.glyphs
        lits = [tok for tok in literals(g) if tok[2] is not None]
        for a in range(len(lits)):
            s1, e1, v1, _b1, _i1 = lits[a]
            for b in range(a + 1, len(lits)):
                s2, e2, v2, _b2, _i2 = lits[b]
                if v1 != v2:
                    continue
                if not _no_a_write_between(g, e1, s2):
                    break  # A reassigned before the reload → not redundant
                sites.append(
                    MatchSite(
                        rule=HOIST_RELOAD,
                        room_id=room.id,
                        cells=frozenset(_token_cells(child, s2, e2)),
                        entry=_ENTRY,
                        exits=(),
                        env={
                            "value": v1,
                            "run_id": child.id,
                            "span": (s2, e2),
                            "replacement": "",
                            "redundant": True,
                        },
                    )
                )
                break  # first redundant reload after this load is enough
    return sites


def _cost_hoist(site: MatchSite) -> CostDelta:
    """Drops the redundant load's cells (and the ticks the man spent on them)."""
    start, end = site.env["span"]  # type: ignore[misc]
    width = end - start
    return CostDelta(d_cells=-width, d_ticks_per_value=-float(width))


def _pre_hoist(site: MatchSite) -> bool:
    """Re-assert the redundant-reload flag and a real integer value."""
    return bool(site.env.get("redundant")) and isinstance(site.env.get("value"), int)


#: The same literal loaded twice with no ``A``-write between → drop the second.
HOIST_RELOAD = RewriteRule(
    name="const.hoist_reload",
    family="const",
    recognize=_recognize_hoist,
    build=_no_build,
    cost_delta=_cost_hoist,
    preconditions=_pre_hoist,
    clobbers=frozenset(),
    resizes_room=False,
    mirrorable=False,
    apply=_apply_span,
)


register(DEAD_LITERAL)
register(SHRINK_BACKTICK)
register(HOIST_RELOAD)
