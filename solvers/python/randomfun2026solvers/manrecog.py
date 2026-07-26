#!/usr/bin/env python3
"""Recognise a counted loop in a block's cells — the first real pattern matcher.

The semantic optimiser's whole flow is *recognise → build → apply → gate → score*.
This module is the **recognise** half for the cheapest rewrite in the catalog, the
loop unroll: it takes a block (an :class:`~randomfun2026solvers.manast.Atom`'s rows,
or a bare row list) and decides whether those cells *are* a
:func:`~randomfun2026solvers.manatom.counted_loop` — and if so, for which body.

The match is **exact against the generator**, not heuristic: a candidate body is
read straight out of the block's second column, the canonical
``counted_loop(body)`` is rebuilt, and the two are compared row for row. A block
that is one glyph off — a stray decrement, a wrong turn — does not match, so the
recogniser can never hand the applier a shape the cost model does not describe.

**What this does not check (documented limitation).** A row list carries no
information about where the *man* walks, so :func:`match_counted_loop` verifies the
block's **shape and ports** only; it does not prove the man actually enters at
:attr:`LoopMatch.entry` and leaves at :attr:`LoopMatch.exit_`. That cross-check
belongs to the inner-logic graph (stream P7) and, ultimately, to the driver's
engine ``verify`` gate, which is authoritative. For the ``loop.unroll2`` rewrite
this gap is harmless in practice: ``counted_loop(body)`` and its unrolled
replacement share *identical* entry/exit ports (top-left in, ``d`` out heading
east), so any man-path that reached the original block reaches the replacement
unchanged. When an :class:`~randomfun2026solvers.manast.Atom` with a resolved
origin is passed, :func:`match_counted_loop` records that origin so the applier can
place the replacement at the same top-left.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .manast import Atom, Port
from .manatom import counted_loop

__all__ = ["LoopMatch", "match_counted_loop"]


@dataclass(frozen=True)
class LoopMatch:
    """A recognised :func:`~randomfun2026solvers.manatom.counted_loop` block.

    :param body: the loop body glyphs (``"rs"`` for the canonical move-a-value
        loop), read out of the block's second column.
    :param k: ``len(body)`` — the body height, so the block is ``k + 2`` rows.
    :param entry: where/with-what-heading the man enters (top-left, heading east).
    :param exit_: the single exit — the ``d`` cell, heading east when ``BP == 0``.
    :param rows: the canonical ``counted_loop(body).rows`` the block matched.
    :param origin: the block's absolute top-left ``(x, y)`` when a placed
        :class:`~randomfun2026solvers.manast.Atom` was matched, else ``None``.
    """

    body: str
    k: int
    entry: Port
    exit_: Port
    rows: tuple[str, ...]
    origin: tuple[int, int] | None = None


def _rows_and_origin(
    atom_or_rows: Atom | Sequence[str],
) -> tuple[list[str], tuple[int, int] | None]:
    """Coerce the input to ``(rows, origin)``; origin is set only for a placed atom."""
    if isinstance(atom_or_rows, Atom):
        return list(atom_or_rows.rows), (atom_or_rows.x, atom_or_rows.y)
    return list(atom_or_rows), None


def match_counted_loop(atom_or_rows: Atom | Sequence[str]) -> LoopMatch | None:
    """Return the :class:`LoopMatch` for a counted-loop block, or ``None``.

    The block must be exactly two columns wide with ``">d"`` on top and ``"^<"`` on
    the bottom; the body is read from the second column of the middle rows and the
    whole shape is re-derived with :func:`~randomfun2026solvers.manatom.counted_loop`
    and compared exactly. Anything that is not a counted loop — a different width, a
    missing turn, a body that would not rebuild to the same glyphs — returns
    ``None`` rather than a partial match.
    """
    rows, origin = _rows_and_origin(atom_or_rows)
    if len(rows) < 3:
        return None
    width = max((len(r) for r in rows), default=0)
    if width != 2:
        return None
    padded = [r.ljust(2) for r in rows]
    if padded[0] != ">d" or padded[-1] != "^<":
        return None

    body = "".join(r[1] for r in padded[1:-1])
    if not body:
        return None
    try:
        gadget = counted_loop(body)
    except (ValueError, KeyError):
        return None
    # The rebuild is the real check: it re-derives the `m` on the first body row
    # and every other cell, so a block that differs anywhere fails to match.
    if list(gadget.rows) != padded:
        return None

    return LoopMatch(
        body=body,
        k=len(body),
        entry=gadget.entry,
        exit_=gadget.exits[0],
        rows=gadget.rows,
        origin=origin,
    )
