"""§7.1 as a **solved** constraint system, not a discovered failure.

Phase 1 (`scratch/layout1/bind.py`) calls the production ``check_bindings`` as a
*predicate*: a candidate either binds or it does not. That is enough to reject a
placement and useless for repairing one — it says "the BRN slab's ``r`` binds
``mem_resp``" and never "move the ROM touch between 5 and 30 rows south".

This module solves the same rule instead of sampling it. ``check_bindings`` is
small enough to invert exactly::

    dist(n) = |px_n - x| + |py_n - y|                      (Manhattan)
    bind ok <=> dist(want) < dist(v)  for every rival v    (strict; ties fail)

Let one touch move along one axis by an integer ``t``. Every constraint becomes

    |B + t| < K      when the moving touch is the one that must win, or
    |B + t| > K      when it is a rival that must lose,

with ``K`` a constant folded out of the fixed axis. The first is an interval, the
second the complement of one. So the feasible set is an exact intersection of
integer intervals — computed, never swept — and each endpoint is *attributable*
to the glyph and rival that produced it, which is the output a repair needs.

Two consequences worth stating because they are not obvious from the predicate:

* When the moving touch is the one that must **win**, every constraint is convex,
  so the feasible set is a single interval. ``ROM_TOUCH_DROP`` is this case, which
  is why its answer is the clean ``5..30`` and not a scatter.
* When the moving touch is a **rival**, the feasible set is a union of two rays,
  so "move it further" can un-fix what a smaller move fixed. A solver that
  assumes intervals everywhere is wrong for exactly this case.

Restriction, named rather than hidden: ``t`` moves along one axis. Every real
lever in ``machine.py`` is axis-aligned (``ROM_TOUCH_DROP`` is vertical,
``store_offset`` is a dx and a dy), and keeping to an axis makes the arithmetic
exact integers rather than a search over a 2-D piecewise-linear region.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

INF = math.inf


@dataclass(frozen=True)
class Ivl:
    """A closed integer interval, endpoints possibly infinite."""

    lo: float
    hi: float

    @property
    def empty(self) -> bool:
        return self.lo > self.hi

    def __str__(self) -> str:
        if self.empty:
            return "empty"
        lo = "-inf" if self.lo == -INF else f"{int(self.lo)}"
        hi = "+inf" if self.hi == INF else f"{int(self.hi)}"
        return f"[{lo}, {hi}]"


class ISet:
    """A sorted, disjoint union of integer intervals."""

    __slots__ = ("parts",)

    def __init__(self, parts: list[Ivl] | None = None) -> None:
        ps = sorted((p for p in (parts or []) if not p.empty), key=lambda p: p.lo)
        merged: list[Ivl] = []
        for p in ps:
            if merged and p.lo <= merged[-1].hi + 1:
                merged[-1] = Ivl(merged[-1].lo, max(merged[-1].hi, p.hi))
            else:
                merged.append(p)
        self.parts = tuple(merged)

    @staticmethod
    def all() -> ISet:
        return ISet([Ivl(-INF, INF)])

    @staticmethod
    def empty() -> ISet:
        return ISet([])

    @property
    def is_empty(self) -> bool:
        return not self.parts

    def __contains__(self, t: int) -> bool:
        return any(p.lo <= t <= p.hi for p in self.parts)

    def __and__(self, other: ISet) -> ISet:
        out: list[Ivl] = []
        for a in self.parts:
            for b in other.parts:
                lo, hi = max(a.lo, b.lo), min(a.hi, b.hi)
                if lo <= hi:
                    out.append(Ivl(lo, hi))
        return ISet(out)

    def clamp(self, lo: int, hi: int) -> ISet:
        return self & ISet([Ivl(lo, hi)])

    def __str__(self) -> str:
        return " u ".join(str(p) for p in self.parts) if self.parts else "empty"


def _abs_lt(K: float, B: int) -> ISet:
    """``|B + t| < K`` over integer ``t``."""
    if K <= 0:
        return ISet.empty()
    return ISet([Ivl(-K - B + 1, K - B - 1)])


def _abs_gt(K: float, B: int) -> ISet:
    """``|B + t| > K`` over integer ``t`` — the complement of an interval."""
    if K < 0:
        return ISet.all()
    return ISet([Ivl(-INF, -K - B - 1), Ivl(K - B + 1, INF)])


# ── the rule, restated from check_bindings so the two cannot drift ───────────
INCOMING = {"rom", "in", "mem_resp", "stream_resp"}


def want_of(glyph: str, band: str) -> str:
    """Which pipe this glyph must bind — ``check_bindings``' own dispatch."""
    if band == "mem":
        return "mem_req" if glyph == "s" else "mem_resp"
    return band


def rivals_of(glyph: str, touches: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """Only touches on this glyph's side of the incoming/outgoing divide."""
    return {n: p for n, p in touches.items() if (n in INCOMING) == (glyph == "r")}


@dataclass
class Violation:
    """One glyph that binds the wrong pipe at the current placement."""

    at: tuple[int, int]
    glyph: str
    want: str
    dists: list[tuple[str, int]]

    def __str__(self) -> str:
        return (f"{self.glyph!r} at {self.at} must bind {self.want!r} "
                f"but distances are {self.dists}")


@dataclass
class Bound:
    """An endpoint of the feasible set, and the constraint that produced it."""

    t: float
    glyph_at: tuple[int, int]
    want: str
    rival: str
    side: str  # "lo" or "hi"

    def __str__(self) -> str:
        edge = "-inf" if self.t == -INF else "+inf" if self.t == INF else f"{int(self.t)}"
        return (f"{self.side}={edge} set by {self.glyph_at} "
                f"({self.want!r} vs {self.rival!r})")


def violations(
    glyphs: list[tuple[int, int, str, str]], touches: dict[str, tuple[int, int]]
) -> list[Violation]:
    """Every glyph that misbinds — the diagnostic ``check_bindings`` throws away.

    ``check_bindings`` raises on the *first* failure, so a placement that breaks
    six glyphs looks exactly like one that breaks one. A repair needs all of them,
    because the feasible interval is their intersection.
    """
    out: list[Violation] = []
    for x, y, glyph, band in glyphs:
        want = want_of(glyph, band)
        rv = rivals_of(glyph, touches)
        if want not in rv:
            continue
        d = {n: abs(px - x) + abs(py - y) for n, (px, py) in rv.items()}
        best = min(d.values())
        if d[want] != best or sum(1 for v in d.values() if v == best) > 1:
            out.append(Violation((x, y), glyph, want,
                                 sorted(d.items(), key=lambda kv: kv[1])))
    return out


def feasible(
    glyphs: list[tuple[int, int, str, str]],
    touches: dict[str, tuple[int, int]],
    *,
    moving: str,
    axis: str = "y",
) -> tuple[ISet, list[Bound]]:
    """The exact set of shifts of ``touches[moving]`` that make every glyph bind.

    Returns the feasible set and the bounds that define it, each attributed to the
    glyph and rival that set it — so a caller can say *why* it cannot move further,
    which is the difference between a solver and a sweep.
    """
    if axis not in ("x", "y"):
        raise ValueError("axis must be 'x' or 'y'")
    if moving not in touches:
        raise KeyError(f"no touch named {moving!r}; have {sorted(touches)}")

    total = ISet.all()
    bounds: list[Bound] = []
    for x, y, glyph, band in glyphs:
        want = want_of(glyph, band)
        rv = rivals_of(glyph, touches)
        if want not in rv or moving not in rv:
            # This glyph cannot see the moving touch, so it constrains nothing —
            # but it must already bind, or no shift will ever fix it.
            continue
        wx, wy = rv[want]
        for v, (vx, vy) in rv.items():
            if v == want:
                continue
            # split each distance into the moving axis and the fixed one
            if axis == "y":
                Aw, Bw, Av, Bv = abs(wx - x), wy - y, abs(vx - x), vy - y
            else:
                Aw, Bw, Av, Bv = abs(wy - y), wx - x, abs(vy - y), vx - x
            if moving == want:
                Cv = Av + abs(Bv)
                got = _abs_lt(Cv - Aw, Bw)
            elif moving == v:
                Cw = Aw + abs(Bw)
                got = _abs_gt(Cw - Av, Bv)
            else:
                got = ISet.all() if Aw + abs(Bw) < Av + abs(Bv) else ISet.empty()
            before = total
            total = total & got
            if total.parts != before.parts:
                for side, t in (("lo", total.parts[0].lo if total.parts else INF),
                                ("hi", total.parts[-1].hi if total.parts else -INF)):
                    if not before.parts or (
                        side == "lo" and t != before.parts[0].lo
                    ) or (side == "hi" and t != before.parts[-1].hi):
                        bounds.append(Bound(t, (x, y), want, v, side))
    return total, bounds


__all__ = [
    "Bound", "ISet", "Ivl", "Violation",
    "feasible", "rivals_of", "violations", "want_of",
]
