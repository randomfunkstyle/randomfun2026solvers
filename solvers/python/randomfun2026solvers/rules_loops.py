#!/usr/bin/env python3
"""Loop-family rewrite rules (stream P1).

This module registers the loop/backpack rewrites into
:data:`~randomfun2026solvers.manrules.CATALOG`. Stream S1 seeded it with
``loop.unroll2``; stream P1 extends it with the wider unrolls and the horizontal
mirror. The rules are:

``loop.unroll2`` / ``loop.unroll4`` / ``loop.unroll8`` — a **tick lever**.
    Recognise a :func:`~randomfun2026solvers.manatom.counted_loop` with body
    ``"rs"`` (the move-one-value loop, 8 ticks per value) whose backpack count is a
    **provably divisible literal**, and replace it with
    :func:`~randomfun2026solvers.manatom.unrolled` at factor 2/4/8. ``unrolled(v)``
    moves ``v`` values per lap in ``4v + 4`` ticks — ``6``, ``5``, ``4.5`` ticks per
    value respectively — so it is strictly faster, at the price of ``v`` extra rows
    of footprint. The tick win pays for the height whenever the loop's tall side is
    not what binds the bounding box.

``loop.mirror_horizontal`` — a **footprint lever**.
    Recognise a tall ``counted_loop(body)`` (``2`` cols × ``k+2`` rows) and reshape
    it to :func:`~randomfun2026solvers.manatom.counted_loop_horizontal` (``k+2`` cols
    × ``2`` rows). Identical **8 ticks per value**, a different footprint — nothing
    is spent or saved in ticks, only the shape changes. See the extended note on
    :data:`MIRROR` for why this is a *cost-model contribution* the S2 driver pairs
    with a re-router, not a standalone in-pass win.

**The divisibility guard is the whole safety story for the unrolls** (hazard #2 in
the plan). ``unrolled(v)`` moves ``v`` values per lap and decrements ``BP`` by ``v``;
enter with ``BP`` not a multiple of ``v`` and it over-rotates, moving values too
many, and *no later check catches it*. So an unroll fires only when the count is a
literal multiple of its factor — :meth:`~randomfun2026solvers.mansem.BPFacts.source`
is ``"literal"`` and ``const % v == 0``. A ``q``-sourced or otherwise unknown count
refuses to match. The check is enforced twice: the recogniser will not emit a
:class:`MatchSite` it cannot prove, and :attr:`RewriteRule.preconditions` re-proves
it before the applier runs.
"""

from __future__ import annotations

from .manast import Atom, RoomNode, Run
from .manatom import counted_loop, counted_loop_horizontal, unrolled
from .manrecog import match_counted_loop
from .manrules import CostDelta, MatchSite, RewriteRule, register
from .mansem import BPFacts

__all__ = [
    "UNROLL2",
    "UNROLL4",
    "UNROLL8",
    "MIRROR",
    "prove_backpack_even",
]

#: The one body these rules handle. ``unrolled`` and the horizontal mirror are both
#: defined over the canonical ``"rs"`` move-a-value loop.
_BODY = "rs"


def _nonblank_cells(rows: tuple[str, ...]) -> int:
    """Occupied (non-blank) cells of a gadget — the ``d_cells`` the footprint sees."""
    return sum(len(r.replace(" ", "")) for r in rows)


def prove_backpack_even(room: RoomNode) -> BPFacts:
    """Prove the loop's backpack count is an even literal, or return ``unknown``.

    The count reaches the loop from a ``… <literal> b`` prefix: a literal writes
    ``A``, then ``b`` copies it into ``BP``. This walks the room's straight-code
    :class:`~randomfun2026solvers.manast.Run` children for that prefix and returns a
    literal :class:`~randomfun2026solvers.mansem.BPFacts` **only** when it finds
    exactly one such load. Its ``divisible_by`` set records which of ``2 / 4 / 8``
    evenly divide the constant, so the wider unrolls read the same proof. Anything
    ambiguous — no load, two loads, a non-literal source — is reported unknown, so
    unrolling refuses.

    Deliberately conservative (the vertical slice): it recognises a single bare
    digit or a single backtick-delimited integer immediately before ``b``, and
    assumes nothing rewrites ``BP`` between that load and the loop. A fuller prover
    (peeling, derived counts) is stream P7 work.
    """
    found: list[int] = []
    for child in room.children:
        if not isinstance(child, Run):
            continue
        value = _literal_before_b(child.glyphs)
        if value is not None:
            found.append(value)
    if len(found) != 1:
        return BPFacts.unknown()
    const = found[0]
    divisors = frozenset(d for d in (2, 4, 8) if const % d == 0)
    # A proven-*odd* literal is still a proof — of unsafety. Report it as a literal
    # so a caller can see the value, but with an empty divisor set → every unroll
    # refuses.
    return BPFacts(const=const, divisible_by=divisors, source="literal")


def _literal_before_b(glyphs: str) -> int | None:
    """The integer a ``… <literal> b`` prefix loads into ``BP``, or ``None``.

    Handles one bare digit or one backtick literal immediately before ``b``. Any
    other shape (no ``b``, a non-literal glyph before it, several literals) yields
    ``None`` so the prover stays conservative.
    """
    b = glyphs.find("b")
    if b <= 0:
        return None
    prefix = glyphs[:b]
    if prefix.endswith("`"):
        inner = prefix[:-1]
        start = inner.rfind("`")
        if start < 0:
            return None
        digits = inner[start + 1 :]
        return int(digits) if digits.isdigit() else None
    last = prefix[-1]
    return int(last) if last.isdigit() else None


# ── the unroll rules (2 / 4 / 8), one factory ─────────────────────────────────
def _make_unroll(pairs: int) -> RewriteRule:
    """Build and register a ``loop.unroll<pairs>`` rule.

    All three factors share one recogniser/builder/cost: only the divisibility the
    recogniser proves (``const % pairs == 0``) and the replacement gadget
    (``unrolled(pairs)``) differ. ``holder`` closes the forward reference so each
    :class:`MatchSite` can name the rule that produced it before the rule object
    exists.
    """
    holder: dict[str, RewriteRule] = {}

    def _recognize(_ast: object, room: RoomNode) -> list[MatchSite]:
        """Every provable ``counted_loop("rs")`` whose count is a multiple of `pairs`."""
        if room.kind != "compute":
            return []
        facts = prove_backpack_even(room)
        provable = (
            facts.source == "literal" and facts.const is not None and facts.const % pairs == 0
        )
        sites: list[MatchSite] = []
        for child in room.children:
            if not isinstance(child, Atom):
                continue
            m = match_counted_loop(child)
            if m is None or m.body != _BODY:
                continue
            if not provable:
                continue  # cannot prove BP % pairs == 0 → no match (hazard #2)
            sites.append(
                MatchSite(
                    rule=holder["rule"],
                    room_id=room.id,
                    cells=frozenset(child.paint()),
                    entry=m.entry,
                    exits=(m.exit_,),
                    env={"k": facts, "body": m.body, "pairs": [pairs]},
                )
            )
        return sites

    def _build(_site: MatchSite) -> list[object]:
        """The replacement: a single :func:`~randomfun2026solvers.manatom.unrolled` gadget."""
        return [unrolled(pairs)]

    def _cost_delta(_site: MatchSite) -> CostDelta:
        """``8 → 4v+4 over v`` ticks per value; the block gains ``v`` occupied rows."""
        before = counted_loop(_BODY)
        after = unrolled(pairs)
        d_ticks = (after.ticks_per_value or 0.0) - (before.ticks_per_value or 0.0)
        d_cells = _nonblank_cells(after.rows) - _nonblank_cells(before.rows)
        return CostDelta(d_cells=d_cells, d_ticks_per_value=d_ticks)

    def _preconditions(site: MatchSite) -> bool:
        """Re-prove ``BP % pairs == 0`` from a literal source before the applier runs."""
        facts = site.env.get("k")
        return (
            isinstance(facts, BPFacts)
            and facts.source == "literal"
            and facts.const is not None
            and facts.const % pairs == 0
        )

    rule = RewriteRule(
        name=f"loop.unroll{pairs}",
        family="loop",
        recognize=_recognize,
        build=_build,
        cost_delta=_cost_delta,
        preconditions=_preconditions,
        clobbers=frozenset(),  # same registers as the counted loop: A (via r), BP
        resizes_room=True,  # v rows taller: the room must grow to hold it
        mirrorable=False,
    )
    holder["rule"] = rule
    register(rule)
    return rule


#: ``counted_loop("rs")`` with a proven even literal count → ``unrolled(2)`` (6 t/v).
UNROLL2 = _make_unroll(2)
#: … a count divisible by four → ``unrolled(4)`` (5 t/v).
UNROLL4 = _make_unroll(4)
#: … a count divisible by eight → ``unrolled(8)`` (4.5 t/v).
UNROLL8 = _make_unroll(8)


# ── the horizontal mirror (footprint lever) ───────────────────────────────────
def _mirror_recognize(ast: object, room: RoomNode) -> list[MatchSite]:
    """Every tall ``counted_loop`` in `room`, when the grid's height is the binder.

    The mirror is a pure *footprint* lever: it does not change what the loop
    computes or its tick cost, only its shape (``2×(k+2)`` → ``(k+2)×2``). It is only
    worth proposing when the **tall side is the binding axis** — i.e. the whole
    grid's height already ≥ its width, so trading rows for columns can shrink
    ``max(w, h)``. That gate is heuristic; the driver's exact score gate decides
    finally, and a width-bound grid is skipped so the driver is not handed a
    candidate that cannot help. When `ast` exposes no bounding box (a bare
    recogniser unit test passes ``None``) the gate defaults open.
    """
    if room.kind != "compute":
        return []
    height_binds = True
    bbox = getattr(ast, "bbox", None)
    if isinstance(bbox, tuple) and len(bbox) == 2:
        w, h = bbox
        height_binds = h >= w
    if not height_binds:
        return []
    sites: list[MatchSite] = []
    for child in room.children:
        if not isinstance(child, Atom):
            continue
        m = match_counted_loop(child)
        if m is None:
            continue
        sites.append(
            MatchSite(
                rule=MIRROR,
                room_id=room.id,
                cells=frozenset(child.paint()),
                entry=m.entry,
                exits=(m.exit_,),
                env={"k": BPFacts.unknown(), "body": m.body, "pairs": []},
            )
        )
    return sites


def _mirror_build(site: MatchSite) -> list[object]:
    """The reshaped block: the same loop rotated into two rows."""
    body = site.env.get("body")
    if not isinstance(body, str):
        body = _BODY
    return [counted_loop_horizontal(body)]


def _mirror_cost(site: MatchSite) -> CostDelta:
    """``d_ticks = 0`` (identical cost); ``d_cells`` = the block's height change.

    Occupied-cell *count* is unchanged by the reshape (a ``"rs"`` loop is seven
    glyphs either way), so a raw cell count would read ``0`` and hide the whole
    point of the lever. What the footprint reads is the *binding dimension*: the
    reshape trades ``k+2`` rows for ``2``, so the block's height drops by ``k``. We
    report that height change as ``d_cells`` (negative = smaller in the binding
    axis), which is the footprint proxy the driver's lever compares.
    """
    body = site.env.get("body")
    if not isinstance(body, str):
        body = _BODY
    before = counted_loop(body)
    after = counted_loop_horizontal(body)
    d_ticks = (after.ticks_per_value or 0.0) - (before.ticks_per_value or 0.0)  # 0.0
    d_cells = len(after.rows) - len(before.rows)  # 2 - (k+2) = -k
    return CostDelta(d_cells=d_cells, d_ticks_per_value=d_ticks)


#: ``counted_loop(body)`` (tall) ⇄ ``counted_loop_horizontal(body)`` (wide).
#:
#: **Ports differ, so this is not a drop-in swap.** The tall loop enters top-left
#: heading **east** and exits the ``d`` heading **east**; the horizontal loop enters
#: top-right heading **south** and exits the bottom ``d`` heading **south** (see
#: :func:`~randomfun2026solvers.manatom.counted_loop` /
#: :func:`~randomfun2026solvers.manatom.counted_loop_horizontal`). A man that reached
#: the tall block from the left cannot traverse the reshaped block correctly, so the
#: default gadget-swap applier produces a grid the driver's ``verify`` rejects — safe
#: (never a wrong-but-accepted grid), but no in-pass win. A *correct* application
#: must re-route the man's entry/exit and re-place the feeding pipe, which risks a
#: nearest-pipe rebind (hazard #1); that router is P5/P8/S2 work, out of this
#: data-only stream. Additionally, the room cannot shrink within a single content
#: pass (``swap_gadget`` only grows), so the footprint payoff only materialises after
#: a later relayout/compaction pass. The rule is therefore registered as the
#: footprint-lever **cost contribution** (``d_ticks = 0``, ``d_cells = −k``) plus a
#: recogniser gated to height-bound grids; S2 pairs it with a re-router to realise
#: the win. ``apply`` is left ``None`` deliberately — the honest swap is verify-gated,
#: never silently mis-wired.
MIRROR = RewriteRule(
    name="loop.mirror_horizontal",
    family="loop",
    recognize=_mirror_recognize,
    build=_mirror_build,
    cost_delta=_mirror_cost,
    clobbers=frozenset(),  # same registers as the tall loop: A (via r), BP
    resizes_room=True,  # reshapes the footprint; verify/relayout realise it
    mirrorable=True,
)

register(MIRROR)
