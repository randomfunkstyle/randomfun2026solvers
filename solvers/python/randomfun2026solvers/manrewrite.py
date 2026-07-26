#!/usr/bin/env python3
"""The matcher/applier engine — turns a rule family into an ``optimize`` pass.

``optimize.optimize()`` drives a list of :data:`PassFn`s: each takes a parsed
:class:`manparse.Program` and returns candidate grids, which the driver gates with
``bindings_preserved`` → ``verify`` → ``score_grid`` and accepts only on a strict
score win. This module bridges the rule catalog to that seam: :func:`rule_pass`
builds one :data:`PassFn` per family (and :func:`all_rules_pass` one across every
family) that parses the program to a mutable :class:`manast.Ast`, gathers
:class:`manrules.MatchSite`s from the catalog, applies each on a **deep copy** of the
AST, and emits a :class:`optimize.Candidate` per surviving rewrite.

The **apply interface (two mutation kinds).** A rule expresses its edit one of two
ways, and the engine picks automatically:

* **gadget swap** — the rule leaves :attr:`manrules.RewriteRule.apply` ``None`` and
  supplies :attr:`~manrules.RewriteRule.build`. The engine finds the matched
  :class:`manast.Atom`, replaces it with the built :class:`manatom.Gadget` at the same
  origin (:func:`swap_gadget`), and grows the room to hold a taller replacement. The
  man's entry/exit are untouched, so this is correct exactly when the two blocks share
  ports — which the loop family's tall↔unrolled swaps do (``loop.unroll2``). This is
  the path stream S1 proved end to end.
* **cell edit / deletion** — the rule sets :attr:`~manrules.RewriteRule.apply` to a
  ``(Ast, MatchSite) -> None`` callback that mutates the AST arbitrarily in place:
  rewrite a :class:`manast.Run`'s glyphs, delete a nop :class:`manast.Corridor`,
  coalesce steers, re-flow a room after a run shrinks, or a swap that also re-routes
  the man. The engine hands it a private deep copy, so the callback owns all the
  geometry its edit needs; the engine only re-renders and gates the result.

Either way the contract is the same: **never mutate the caller's AST** (each site is
applied to a fresh :func:`copy.deepcopy`), a callback or builder that **raises is
skipped** rather than crashing the pass (at worst a rule yields no candidate), and the
mutated AST must **render without collision** (:func:`manast.render` with ``check``) —
after which the driver's ``verify`` on every public case is the real correctness gate.

**Why content rewrites carry ``placement=None``.** ``optimize.bindings_preserved`` keys
a pipe's identity on the ``(kind, interior-content)`` of the rooms it joins
(``optimize._room_key``), so it reports a false "binding changed" for **any** interior
edit — it is stable under a relayout *move* but not under a content rewrite. A rewrite
that edits a room's glyphs therefore cannot use that gate; its pipe-binding safety comes
from ``verify`` running every public case on the real engine (a rebind would change the
output). So every candidate this engine emits sets ``placement=None``. ``placement`` /
``bindings_preserved`` stay reserved for pipe-op *relocation* (the pipe family), which
moves a pipe-op cell without editing content and is not handled here.
"""

from __future__ import annotations

import copy
import importlib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from .manast import Ast, Atom, PaintError, RoomNode, parse_ast, render, round_trip_ok
from .manast import Refine as _Refine
from .manatom import Gadget
from .manrules import FAMILIES, MatchSite, RewriteRule, rules_for

if TYPE_CHECKING:
    from .manparse import Program
    from .optimize import Candidate

__all__ = ["PassFn", "apply_rules", "swap_gadget", "rule_pass", "all_rules_pass"]

#: An ``optimize`` pass: parsed program in, candidate grids out. Matches the
#: ``optimize.PassFn`` seam exactly, so a returned pass appends straight to
#: ``optimize.PASSES``.
PassFn = Callable[["Program"], list["Candidate"]]


# ── locating the match in a (deep-copied) AST ─────────────────────────────────
def _find_room(ast: Ast, room_id: int) -> RoomNode | None:
    """The room with `room_id`, if the AST still holds it."""
    return next((r for r in ast.rooms if r.id == room_id), None)


def _find_atom(room: RoomNode, cells: frozenset[tuple[int, int]]) -> Atom | None:
    """The room's gadget :class:`Atom` covering exactly `cells`, if any."""
    for child in room.children:
        if isinstance(child, Atom) and frozenset(child.paint()) == cells:
            return child
    return None


# ── mutation primitive: gadget swap ───────────────────────────────────────────
def swap_gadget(room: RoomNode, target: Atom, gadget: Gadget) -> None:
    """Replace `target` in `room` with `gadget`'s block at the same top-left.

    The block keeps its origin, so a replacement that shares the matched block's
    entry/exit ports (the loop family's tall↔unrolled swaps do) is still reached by
    the same man-walk with no corridor surgery. A **taller** replacement grows the
    room so its bottom wall clears the new block; a **shorter** one leaves the room
    unchanged (the surplus rows are blank floor the man does not traverse — correct,
    if not maximally compact; a later relayout pass reclaims the footprint). A
    replacement whose cells collide with something else fails to paint downstream in
    :func:`~manast.render` and the candidate is dropped. Edits that need real man
    re-routing or a room re-flow use :attr:`manrules.RewriteRule.apply`, not this.
    """
    delta_rows = len(gadget.rows) - len(target.rows)
    target.rows = list(gadget.rows)
    target.entry = gadget.entry
    target.exits = gadget.exits
    target.ticks = gadget.ticks
    target.note = f"{gadget.name}: {gadget.note}"
    if delta_rows > 0:
        room.h += delta_rows


def _gadget_swap_from_build(trial: Ast, rule: RewriteRule, site: MatchSite) -> None:
    """The fallback edit for a rule with no :attr:`~manrules.RewriteRule.apply`.

    Builds the rule's replacement and swaps the single matched gadget atom for it.
    Raises (and is caught by :func:`_apply_site`, so the candidate is skipped) if the
    room or atom cannot be located, or ``build`` does not return exactly one gadget —
    this minimal path replaces one atom with one gadget.
    """
    room = _find_room(trial, site.room_id)
    if room is None:
        raise KeyError(f"room {site.room_id} not in AST")
    target = _find_atom(room, site.cells)
    if target is None:
        raise KeyError(f"no gadget atom covers {sorted(site.cells)}")
    gadgets = rule.build(site)
    if len(gadgets) != 1:
        raise ValueError(f"gadget swap needs exactly one gadget, got {len(gadgets)}")
    swap_gadget(room, target, gadgets[0])


# ── applying one site to a fresh copy ─────────────────────────────────────────
def _apply_site(base: Ast, rule: RewriteRule, site: MatchSite) -> Candidate | None:
    """Apply one match to a deep copy of `base`, returning a Candidate or ``None``.

    Deep-copies `base` (the caller's AST is never touched), runs the rule's
    :attr:`~manrules.RewriteRule.apply` callback when present else the gadget-swap
    fallback, then re-renders with the collision check. A callback/builder that raises,
    or a render that collides, yields ``None`` — a buggy rule never crashes the pass.
    The Candidate carries ``placement=None``: a content rewrite cannot use
    ``bindings_preserved`` (see the module docstring); ``verify`` is its gate.
    """
    from .optimize import Candidate  # deferred: optimize pulls in heavy deps

    trial = copy.deepcopy(base)
    try:
        if rule.apply is not None:
            rule.apply(trial, site)
        else:
            _gadget_swap_from_build(trial, rule, site)
    except Exception:  # noqa: BLE001 — a rule's edit must never crash the pass
        return None

    try:
        grid = render(trial, check=True)
    except PaintError:
        return None
    return Candidate(grid=grid, placement=None, label=rule.name)


def apply_rules(ast: Ast, rules: Iterable[RewriteRule]) -> list[Candidate]:
    """Every surviving rewrite of `rules` over `ast`, as content-edit Candidates.

    The engine core, independent of parsing so it is unit-testable without the wasm
    oracle: an unfaithful base (one that does not :func:`~manast.round_trip_ok`) is
    never a safe thing to rewrite, so it yields ``[]``; otherwise each rule is matched
    over every room, its :attr:`~manrules.RewriteRule.preconditions` re-checked, and each
    surviving site applied on its own deep copy via :func:`_apply_site`. `ast` itself is
    never mutated. :func:`rule_pass` / :func:`all_rules_pass` wrap this after parsing.
    """
    if not round_trip_ok(ast):
        return []
    out: list[Candidate] = []
    for rule in rules:
        for room in ast.rooms:
            for site in rule.recognize(ast, room):
                if not rule.preconditions(site):
                    continue
                cand = _apply_site(ast, rule, site)
                if cand is not None:
                    out.append(cand)
    return out


# ── family loading + pass adapters ────────────────────────────────────────────
def _load_all_rule_modules() -> None:
    """Import **every** ``rules_*`` module in the package so the whole CATALOG populates.

    Registration is a side effect of importing a ``rules_*`` module, and a *macro* rule
    registers into the family of its **dominant effect**, not into a module named after
    that family: ``rules_macros`` puts the flagship ``loop.const_unroll`` in the ``loop``
    bucket and ``arith.load_op_fold`` in ``arith``. Loading only ``rules_<family>`` would
    therefore silently miss those macros. Discovering and importing all ``rules_*``
    modules keeps ``rules_for(family)`` complete for every family, macros included. Import
    is idempotent and cheap (already-imported modules are a dict lookup), so calling this
    from each pass builder is free after the first hit.
    """
    pkg_dir = Path(__file__).resolve().parent
    for path in sorted(pkg_dir.glob("rules_*.py")):
        try:
            importlib.import_module(f".{path.stem}", __package__)
        except ModuleNotFoundError:
            continue


def _load_family(family: str) -> None:
    """Populate the catalog for `family` (and the rest — see :func:`_load_all_rule_modules`).

    A family's rules are not confined to a ``rules_<family>`` module: cross-family macros
    live in ``rules_macros`` yet register into ``loop`` / ``arith``. So loading a single
    family means loading them all; the extra buckets are harmless and ``rules_for(family)``
    still returns only this family's slice. A family whose stream never landed simply has
    an empty bucket — not an error.
    """
    _load_all_rule_modules()


def rule_pass(family: str) -> PassFn:
    """Build the :data:`PassFn` that applies every registered rule in `family`.

    Imports the family's catalog (:func:`_load_family`), then returns a pass that parses
    the program to an ``Ast`` at :data:`~manast.Refine.BLOCKS` and runs
    :func:`apply_rules` over that family's rules. A clean no-match returns ``[]`` (the
    driver keeps the input unchanged); every candidate carries ``placement=None`` so the
    driver gates it on ``verify`` alone.
    """
    _load_family(family)

    def _pass(prog: Program) -> list[Candidate]:
        ast = parse_ast(prog, refine=_Refine.BLOCKS)
        return apply_rules(ast, rules_for(family))

    return _pass


def all_rules_pass() -> PassFn:
    """A :data:`PassFn` running every registered rule across all :data:`~manrules.FAMILIES`.

    The convenience pass for the integration stream (S2) and the benchmark (P10): it
    imports whatever family modules exist, parses once, and applies every rule in the
    catalog. Same gating and ``placement=None`` contract as :func:`rule_pass`.
    """
    _load_all_rule_modules()

    def _pass(prog: Program) -> list[Candidate]:
        ast = parse_ast(prog, refine=_Refine.BLOCKS)
        every: list[RewriteRule] = []
        for family in sorted(FAMILIES):
            every.extend(rules_for(family))
        return apply_rules(ast, every)

    return _pass
