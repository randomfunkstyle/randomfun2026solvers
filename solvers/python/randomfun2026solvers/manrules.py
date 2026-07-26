#!/usr/bin/env python3
"""The rewrite-rule contract — the schema every catalog agrees on.

A *rewrite* is a local, semantics-preserving substitution inside a room: recognise
a pattern (a counted loop, an identity multiply, a dead literal), and emit an
equivalent block that scores better under ``max(w,h)² × avgTicks``. This module is
the **frozen interface** for that: the dataclasses a family fills in, and the
append-only registry the pass adapter reads back. It owns no rules itself — the
``recognize`` / ``build`` / ``cost_delta`` fields are callables the per-family
streams supply — so it stays a pure, import-cheap contract.

The right-hand side is deliberately *reused, not reinvented*:
:class:`manatom.Gadget` already carries name / rows / entry / exits / ticks /
per-lap / needs / writes and a ``to_atom`` bridge, so :attr:`RewriteRule.build`
returns ``list[Gadget]`` verbatim; ports are :class:`manast.Port`.

Frozen conventions (relied on across all parallel streams — do not drift):

* **Families.** :data:`FAMILIES` is the closed set of catalog keys —
  ``{"loop", "arith", "const", "steer", "pipe", "io"}``. :func:`register` rejects
  anything else, so a typo cannot silently create an unreachable catalog slice.
* **Register names.** Effects and clobbers name registers ``A`` / ``B`` / ``BP``
  and the heading ``HEAD`` (see :mod:`mansem` and :mod:`manlogic`).
* **``MatchSite.env`` keys.** ``env`` is the recogniser→builder handoff; its keys
  are fixed *per family* so a builder can read them without guessing:

  =========  ==================================================================
  family     ``env`` keys
  =========  ==================================================================
  loop       ``env["k"]`` (count / :class:`mansem.BPFacts`), ``env["body"]``
             (body glyph string), ``env["pairs"]`` (unroll factor candidates)
  const      ``env["value"]`` (the integer literal)
  arith      op-specific, e.g. ``env["op"]``; identity/fold operands as needed
  steer      ``env["headings"]`` (the coalesced steer sequence)
  pipe       ``env["side"]`` / ``env["capacity"]`` (pipe reshape params)
  io         ``env["port"]`` (display / IO marker being rewritten)
  =========  ==================================================================

  A recogniser MUST populate exactly the keys its family's builder reads; a
  builder MUST NOT read keys outside its family's row.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .manast import Port
from .manatom import Gadget

if TYPE_CHECKING:
    from .manast import Ast

__all__ = [
    "Cell",
    "FAMILIES",
    "CostDelta",
    "MatchSite",
    "RewriteRule",
    "CATALOG",
    "register",
    "rules_for",
]

#: An in-room cell coordinate, ``(x, y)`` — the same convention as ``manstruct``.
Cell = tuple[int, int]


def _always_legal(_site: MatchSite) -> bool:
    """Default :attr:`RewriteRule.preconditions`: nothing extra to check."""
    return True

#: The closed set of catalog keys. Frozen: a rule outside this set is a bug.
FAMILIES: frozenset[str] = frozenset({"loop", "arith", "const", "steer", "pipe", "io"})


@dataclass(frozen=True)
class CostDelta:
    """The score-relevant change a rewrite makes, both objective factors kept apart.

    :param d_cells: change in occupied cells (a proxy the footprint lever reads;
        negative is smaller).
    :param d_ticks_per_value: change in ticks per moved value (negative is faster).
        Read straight off :attr:`manatom.Gadget.ticks_per_value` on each side.
    """

    d_cells: int
    d_ticks_per_value: float


@dataclass(frozen=True)
class MatchSite:
    """One recognised occurrence of a rule's pattern, ready to be built.

    :param rule: the :class:`RewriteRule` that matched.
    :param room_id: the room the match lives in.
    :param cells: every cell the match covers (what the builder replaces).
    :param entry: where/how the man enters the matched block.
    :param exits: the block's exit ports (several for a branch, none for a halt).
    :param env: the recogniser→builder handoff; keys are fixed per family — see the
        module docstring's table.
    """

    rule: RewriteRule
    room_id: int
    cells: frozenset[Cell]
    entry: Port
    exits: tuple[Port, ...]
    env: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RewriteRule:
    """A named, family-tagged rewrite: recognise a pattern, build its replacement.

    :param name: unique within its family (e.g. ``"loop.unroll2"``).
    :param family: one of :data:`FAMILIES`.
    :param recognize: ``(...) -> list[MatchSite]`` — the caller (pass adapter)
        supplies the arguments (a logic graph / program + room); returns every
        occurrence. Empty list means "no match", never an error.
    :param build: ``(MatchSite) -> list[Gadget]`` — the replacement block(s). Used
        by ``manrewrite`` for a *gadget swap*: the matched block's :class:`manast.Atom`
        is replaced by the single built :class:`Gadget` at the same origin (the man's
        entry/exit ports must line up — they do for the loop family's tall↔unrolled
        swaps). A rule whose edit is not a one-atom swap sets :attr:`apply` instead.
    :param apply: ``(Ast, MatchSite) -> None`` **or** ``None`` (default). The applier
        interface for edits :attr:`build` cannot express: an in-place cell edit or
        deletion (constant fold, identity elision, nop/corridor removal, steer
        coalescing), a room re-flow after a run shrinks, or a swap that also re-routes
        the man. When present, ``manrewrite`` calls it to **mutate a deep copy** of the
        AST in place (never the caller's), then re-renders and gates the result on the
        engine; when ``None``, ``manrewrite`` falls back to the :attr:`build` gadget
        swap — so a rule that only swaps a gadget (e.g. ``loop.unroll2``) leaves this
        ``None`` and is unaffected. A callback that raises is skipped, not fatal: at
        worst a rule yields no candidate. Pure content edits leave the driver's
        ``placement`` at ``None`` (their pipe-binding safety comes from ``verify``, not
        ``bindings_preserved`` — see :mod:`manrewrite`); ``placement`` /
        ``bindings_preserved`` are reserved for pipe-op *relocation* (the pipe family).
    :param cost_delta: ``(MatchSite) -> CostDelta`` — signed score change.
    :param preconditions: ``(MatchSite) -> bool`` — extra legality the recogniser
        could not settle (e.g. ``BP`` divisibility for an unroll). Default: always
        legal.
    :param clobbers: registers (``A`` / ``B`` / ``BP`` / ``HEAD``) the replacement
        may overwrite that the original did not — the caller checks no live value
        crosses them.
    :param resizes_room: the rewrite changes the room's footprint or moves a
        pipe-op cell, so the driver MUST re-run ``bindings_preserved``.
    :param mirrorable: the replacement has a legal mirror image (used by the
        footprint lever to pick the orientation whose long side does not bind).
    """

    name: str
    family: str
    recognize: Callable[..., list[MatchSite]]
    build: Callable[[MatchSite], list[Gadget]]
    cost_delta: Callable[[MatchSite], CostDelta]
    preconditions: Callable[[MatchSite], bool] = _always_legal
    clobbers: frozenset[str] = field(default_factory=frozenset)
    resizes_room: bool = False
    mirrorable: bool = False
    #: Optional in-place editor; when ``None`` the applier uses the ``build`` gadget
    #: swap. See the field's ``:param apply:`` note above.
    apply: Callable[[Ast, MatchSite], None] | None = None


#: The append-only registry, one bucket per family. Read by the pass adapter.
CATALOG: dict[str, list[RewriteRule]] = {fam: [] for fam in FAMILIES}


def register(rule: RewriteRule) -> RewriteRule:
    """Append `rule` to its family's catalog bucket; return it (usable as a decorator).

    Raises :class:`ValueError` if ``rule.family`` is not in :data:`FAMILIES`, so a
    misfiled rule fails loudly at import rather than becoming dead data.
    """
    if rule.family not in FAMILIES:
        raise ValueError(f"unknown family {rule.family!r}; must be one of {sorted(FAMILIES)}")
    CATALOG[rule.family].append(rule)
    return rule


def rules_for(family: str) -> list[RewriteRule]:
    """Every registered rule in `family` (a fresh list; empty for an unused family).

    Raises :class:`ValueError` for a family outside :data:`FAMILIES`.
    """
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; must be one of {sorted(FAMILIES)}")
    return list(CATALOG[family])
