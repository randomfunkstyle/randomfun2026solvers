#!/usr/bin/env python3
"""Arithmetic / hands rewrite rules (stream P2's file).

Every rule here is a **cell edit** on a straight :class:`~randomfun2026solvers.manast.Run`
of executing glyphs: it deletes or rewrites a few glyphs and lets the run reflow, so
the man walks fewer cells (fewer ticks) for the identical result. They register into
``CATALOG["arith"]`` and are picked up by
:func:`~randomfun2026solvers.manrewrite.rule_pass`.

Four rules, each with its precondition spelled out:

* ``arith.identity_pair`` — an **involution pair** ``NN`` (``A = -(-A)``) or ``WW``
  (swap twice) is a no-op *unconditionally*: the intermediate value is read only by
  the second glyph, which is also deleted, so nothing outside the pair ever observes
  it. Delete both glyphs. No liveness analysis needed.
* ``arith.strength`` — a pure-``A`` operator whose ``B`` operand is a **proven
  identity constant** is a no-op for ``A`` and writes nothing else, so the operator
  glyph is deleted. Identity elements (SPEC ``Arithmetic``/``Bitwise``):
  ``B == 0`` for ``+ - | ~ { }`` (add/sub/or/xor/shift-by-0), ``B == 1`` for ``*``,
  ``B == -1`` for ``&``. ``/`` is **excluded** — it also writes ``B`` (the remainder),
  so it is never a pure no-op. ``B``'s value is proven by straight-line constant
  propagation over the run; an unknown ``B`` refuses to match.
* ``arith.const_fold`` — a **literal load into ``A``** immediately followed by a
  pure-``A`` operator whose ``B`` is a known constant is replaced by the precomputed
  single-digit literal (``3`` then ``+`` with ``B == 2`` → ``5``). Fires only when
  both operands are proven constants and the result is a single digit ``0..9`` (so the
  rewrite never *grows* the run). ``B`` provenance unknown ⇒ refuse.
* ``arith.redundant_m`` — an ``M`` (``B = A``) whose written ``B`` is **never read
  before it is overwritten** downstream is dead; delete it. This is the core
  no-intervening-read gate, and it bottoms out in
  :func:`~randomfun2026solvers.mansem.run_effect`: ``M`` is dead iff ``B`` is *not* in
  the ``needs`` of the run suffix after it (``needs`` = read-before-written), i.e. the
  suffix overwrites ``B`` before any read, or never reads it at all.

**The reflow-safety guard (execution-order preservation, plan hazard #5).** Deleting
glyphs shortens a run and shifts its tail toward the entry, which moves the run's
*exit* cell — so any node wired to that exit would be disconnected. To stay
conservative and correct without a full room re-flow, a rule fires **only on a run
that ends in ``H``**: the man halts at the run's tail, so there is no downstream node
to disconnect, and the prefix before the edit keeps its cells (the incoming
connection is untouched). Pipe-op cells inside the shifted tail may re-bind to a
nearer pipe; that never corrupts output silently because the driver's ``verify`` runs
every public case on the real engine and rejects any candidate whose output changed —
so a bad re-bind costs a missed win, never correctness (see :mod:`manrewrite`).
"""

from __future__ import annotations

from .manast import Ast, Port, RoomNode, Run
from .manrules import CostDelta, MatchSite, RewriteRule, register
from .mansem import glyph_effect, run_effect

__all__ = [
    "IDENTITY_PAIR",
    "STRENGTH",
    "CONST_FOLD",
    "REDUNDANT_M",
    "values_before",
    "m_is_dead",
]

#: Operator glyphs that read ``A`` and ``B`` and write only ``A`` — the family whose
#: identity/fold reductions are pure (no side effect on ``B``). ``/`` is deliberately
#: absent: it also writes ``B`` (remainder), so it is never a plain no-op.
_PURE_A_OPS = frozenset("+-*%&|~{}")

#: Identity element -> the operators for which ``A op element == A`` (SPEC tables).
_IDENTITY_ELEMENT: dict[int, frozenset[str]] = {
    0: frozenset("+-|~{}"),  # A+0, A-0, A|0, A^0, A<<0, A>>0
    1: frozenset("*"),  # A*1
    -1: frozenset("&"),  # A & ~0
}

#: The involutions handled by ``arith.identity_pair``: g;g is a no-op as a pair.
_INVOLUTIONS = ("NN", "WW")


# ── straight-line value propagation (for strength + const-fold) ────────────────
def _eval_op(op: str, a: int, b: int) -> int | None:
    """``A op B`` for a pure-``A`` operator on two known integers, per SPEC.

    Returns ``None`` for the SPEC's degenerate cases (division/shift out of range)
    rather than guessing, so a fold refuses instead of emitting a wrong literal.
    """
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "%":
        return 0 if b == 0 else a - (a // b) * b  # floored, takes B's sign
    if op == "&":
        return a & b
    if op == "|":
        return a | b
    if op == "~":
        return a ^ b
    if op == "{":
        return 0 if not (0 <= b <= 63) else a << b
    if op == "}":
        if b < 0:
            return 0
        return a >> min(b, 63)
    return None


def values_before(glyphs: str, upto: int) -> tuple[int | None, int | None]:
    """The proven ``(A, B)`` constants just before glyph index `upto`, or ``None``.

    A conservative straight-line constant propagation over ``glyphs[:upto]``: bare
    digits load ``A``; ``M`` copies ``A`` into ``B``; ``W`` swaps; ``N`` negates
    ``A``; a pure-``A`` operator with both operands known folds. **Anything it cannot
    track exactly makes the written register unknown** — a backtick literal (whose
    value is not recovered here), a pipe receive, the spawn ``@`` — so the provers
    downstream stay sound: an unknown operand always refuses the rewrite.
    """
    a: int | None = None
    b: int | None = None
    i = 0
    n = min(upto, len(glyphs))
    while i < n:
        c = glyphs[i]
        if c == "`":  # backtick literal: value not tracked here -> A becomes unknown
            j = glyphs.find("`", i + 1)
            a = None
            i = (j + 1) if j != -1 else n
            continue
        if c.isdigit():
            a = int(c)
        elif c == "M":
            b = a
        elif c == "W":
            a, b = b, a
        elif c == "N":
            a = None if a is None else -a
        elif c == "/":
            a = b = None  # writes both; value not tracked
        elif c in _PURE_A_OPS:
            a = None if (a is None or b is None) else _eval_op(c, a, b)
        else:
            # Unknown value effect: consult the ISA table and drop whatever it writes.
            try:
                eff = glyph_effect(c)
            except ValueError:
                a = b = None  # e.g. '@' spawn: treat as a full barrier
            else:
                if "A" in eff.writes:
                    a = None
                if "B" in eff.writes:
                    b = None
        i += 1
    return a, b


def m_is_dead(glyphs: str, index: int) -> bool:
    """Is the ``M`` at `index` dead — its ``B`` never read before being rewritten?

    The no-intervening-read gate. ``M`` writes ``B``; deleting it is safe iff nothing
    downstream observes that ``B``. :func:`~randomfun2026solvers.mansem.run_effect`
    reports, for the run suffix after the ``M``, which registers are *needed* (read
    before the suffix itself writes them). ``B`` absent from ``needs`` means the
    suffix overwrites ``B`` before any read (or never reads it) — dead, deletable.
    A suffix ``run_effect`` cannot parse refuses conservatively.
    """
    if index < 0 or index >= len(glyphs) or glyphs[index] != "M":
        return False
    suffix = glyphs[index + 1 :]
    try:
        needs, _writes = run_effect(suffix)
    except ValueError:
        return False  # an unparsable suffix: refuse rather than guess
    return "B" not in needs


# ── site construction + the shared cell-edit applier ───────────────────────────
def _iter_target_runs(room: RoomNode):
    """Straight runs in a compute room that **end in ``H``** — the reflow-safe set.

    A run whose last executed glyph is ``H`` halts the man at its tail, so shrinking
    it disconnects no downstream node (see the module docstring's reflow guard).
    """
    if room.kind != "compute":
        return
    for child in room.children:
        if isinstance(child, Run) and child.glyphs and child.glyphs[-1] == "H":
            yield child


def _cells_for(run: Run, start: int, length: int) -> frozenset[tuple[int, int]]:
    """Absolute cells of ``glyphs[start:start+length]`` for a run at its origin."""
    dx, dy = (1, 0) if run.heading == "E" else (0, 1)
    return frozenset(
        (run.x + (start + k) * dx, run.y + (start + k) * dy) for k in range(length)
    )


def _make_site(
    rule: RewriteRule,
    room: RoomNode,
    run: Run,
    start: int,
    length: int,
    repl: str,
    *,
    extra: dict[str, object],
) -> MatchSite:
    """Build a :class:`MatchSite` whose ``env`` fully specifies the glyph edit.

    ``env`` carries the family-standard ``op`` label plus everything
    :func:`_apply_edit` needs to relocate and rewrite the run on a fresh AST copy:
    the run's origin+heading, the ``[start:start+length]`` slice, the matched text
    (drift check), and the replacement.
    """
    heading = (1, 0) if run.heading == "E" else (0, 1)
    env: dict[str, object] = {
        "op": extra.get("op", run.glyphs[start : start + length]),
        "run": (run.x, run.y, run.heading),
        "start": start,
        "length": length,
        "repl": repl,
        "match": run.glyphs[start : start + length],
    }
    env.update(extra)
    return MatchSite(
        rule=rule,
        room_id=room.id,
        cells=_cells_for(run, start, length),
        entry=Port(0, 0, heading, 0, "run entry"),
        exits=(),
        env=env,
    )


def _find_room(ast: Ast, room_id: int) -> RoomNode | None:
    return next((r for r in ast.rooms if r.id == room_id), None)


def _locate_run(room: RoomNode, x: int, y: int, heading: str) -> Run | None:
    for child in room.children:
        if isinstance(child, Run) and child.x == x and child.y == y and child.heading == heading:
            return child
    return None


def _apply_edit(trial: Ast, site: MatchSite) -> None:
    """Mutate `trial` in place: rewrite the matched run slice, letting the tail reflow.

    Shared by every arith rule. Re-locates the run on the (deep-copied) AST, asserts
    the matched slice has not drifted, and splices in the replacement — the run then
    repaints contiguously from its origin, so the tail shifts and the freed cells
    become blank floor. Raises (⇒ the candidate is skipped by ``manrewrite``) if the
    room/run is gone or the slice drifted, so a stale site never corrupts a grid.
    """
    room = _find_room(trial, site.room_id)
    if room is None:
        raise KeyError(f"room {site.room_id} gone")
    rx, ry, rh = site.env["run"]  # type: ignore[misc]
    run = _locate_run(room, rx, ry, rh)
    if run is None:
        raise KeyError("target run gone")
    start = int(site.env["start"])  # type: ignore[arg-type]
    length = int(site.env["length"])  # type: ignore[arg-type]
    match = str(site.env["match"])
    if run.glyphs[start : start + length] != match:
        raise ValueError("run drifted since recognition")
    repl = str(site.env["repl"])
    run.glyphs = run.glyphs[:start] + repl + run.glyphs[start + length :]


def _shrink_cost(site: MatchSite) -> CostDelta:
    """Fewer glyphs walked: negative cells and negative ticks/value, both proxies.

    ``removed = length - len(repl)`` cells vanish from the man's path; each is one
    fewer walked cell per pass (an upper bound on the tick win — the real figure is
    measured by ``verify``/``score_grid``), and one fewer occupied cell.
    """
    removed = int(site.env["length"]) - len(str(site.env["repl"]))  # type: ignore[arg-type]
    return CostDelta(d_cells=-removed, d_ticks_per_value=-float(removed))


# ── rule 1: involution-pair elision (NN, WW) ───────────────────────────────────
def _recognize_identity(_ast: object, room: RoomNode) -> list[MatchSite]:
    sites: list[MatchSite] = []
    for run in _iter_target_runs(room):
        g = run.glyphs
        for pat in _INVOLUTIONS:
            i = g.find(pat)
            while i != -1:
                sites.append(
                    _make_site(IDENTITY_PAIR, room, run, i, 2, "", extra={"op": pat})
                )
                i = g.find(pat, i + 2)  # non-overlapping occurrences
    return sites


def _precondition_identity(site: MatchSite) -> bool:
    """The match is still one of the recognised involution pairs."""
    return str(site.env.get("op")) in _INVOLUTIONS and site.env.get("match") in _INVOLUTIONS


IDENTITY_PAIR = RewriteRule(
    name="arith.identity_pair",
    family="arith",
    recognize=_recognize_identity,
    build=lambda _s: [],  # cell-edit rule: build is unused (apply is set)
    cost_delta=_shrink_cost,
    preconditions=_precondition_identity,
    clobbers=frozenset(),
    resizes_room=False,
    mirrorable=False,
    apply=_apply_edit,
)
register(IDENTITY_PAIR)


# ── rule 2: strength / identity reduction (op with identity-constant B) ────────
def _recognize_strength(_ast: object, room: RoomNode) -> list[MatchSite]:
    sites: list[MatchSite] = []
    for run in _iter_target_runs(room):
        g = run.glyphs
        for i, c in enumerate(g):
            if c not in _PURE_A_OPS:
                continue
            _a, b = values_before(g, i)
            if b is None or c not in _IDENTITY_ELEMENT.get(b, frozenset()):
                continue
            sites.append(
                _make_site(
                    STRENGTH, room, run, i, 1, "",
                    extra={"op": f"{c}{b}", "glyph": c, "b_val": b},
                )
            )
    return sites


def _precondition_strength(site: MatchSite) -> bool:
    """Re-prove ``A glyph B == A`` from the stored operand: glyph is identity for b_val."""
    glyph = site.env.get("glyph")
    b_val = site.env.get("b_val")
    if not isinstance(glyph, str) or not isinstance(b_val, int):
        return False
    return glyph in _IDENTITY_ELEMENT.get(b_val, frozenset())


STRENGTH = RewriteRule(
    name="arith.strength",
    family="arith",
    recognize=_recognize_strength,
    build=lambda _s: [],
    cost_delta=_shrink_cost,
    preconditions=_precondition_strength,
    clobbers=frozenset(),
    resizes_room=False,
    mirrorable=False,
    apply=_apply_edit,
)
register(STRENGTH)


# ── rule 3: constant fold (literal + op -> literal) ────────────────────────────
def _recognize_const_fold(_ast: object, room: RoomNode) -> list[MatchSite]:
    sites: list[MatchSite] = []
    for run in _iter_target_runs(room):
        g = run.glyphs
        for i in range(len(g) - 1):
            if not g[i].isdigit():
                continue
            op = g[i + 1]
            if op not in _PURE_A_OPS:
                continue
            a = int(g[i])
            _a_before, b = values_before(g, i)  # B just before the (A-writing) digit
            if b is None:
                continue
            result = _eval_op(op, a, b)
            if result is None or not (0 <= result <= 9):
                continue  # refuse anything that is not a single-digit literal
            sites.append(
                _make_site(
                    CONST_FOLD, room, run, i, 2, str(result),
                    extra={
                        "op": f"{a}{op}",
                        "glyph": op,
                        "a_val": a,
                        "b_val": b,
                        "result": result,
                    },
                )
            )
    return sites


def _precondition_const_fold(site: MatchSite) -> bool:
    """Recompute ``a op b`` and confirm it is the stored single-digit replacement."""
    glyph = site.env.get("glyph")
    a_val = site.env.get("a_val")
    b_val = site.env.get("b_val")
    result = site.env.get("result")
    if not (isinstance(glyph, str) and isinstance(a_val, int) and isinstance(b_val, int)):
        return False
    recomputed = _eval_op(glyph, a_val, b_val)
    return (
        recomputed == result
        and isinstance(result, int)
        and 0 <= result <= 9
        and str(site.env.get("repl")) == str(result)
    )


CONST_FOLD = RewriteRule(
    name="arith.const_fold",
    family="arith",
    recognize=_recognize_const_fold,
    build=lambda _s: [],
    cost_delta=_shrink_cost,
    preconditions=_precondition_const_fold,
    clobbers=frozenset(),
    resizes_room=False,
    mirrorable=False,
    apply=_apply_edit,
)
register(CONST_FOLD)


# ── rule 4: redundant M elision (dead B) ───────────────────────────────────────
def _recognize_redundant_m(_ast: object, room: RoomNode) -> list[MatchSite]:
    sites: list[MatchSite] = []
    for run in _iter_target_runs(room):
        g = run.glyphs
        i = g.find("M")
        while i != -1:
            if m_is_dead(g, i):
                sites.append(
                    _make_site(
                        REDUNDANT_M, room, run, i, 1, "",
                        extra={"op": "M", "suffix": g[i + 1 :]},
                    )
                )
            i = g.find("M", i + 1)
    return sites


def _precondition_redundant_m(site: MatchSite) -> bool:
    """Re-prove ``B`` is dead over the stored suffix via ``run_effect`` (no B read)."""
    suffix = site.env.get("suffix")
    if not isinstance(suffix, str):
        return False
    try:
        needs, _writes = run_effect(suffix)
    except ValueError:
        return False
    return "B" not in needs


REDUNDANT_M = RewriteRule(
    name="arith.redundant_m",
    family="arith",
    recognize=_recognize_redundant_m,
    build=lambda _s: [],
    cost_delta=_shrink_cost,
    preconditions=_precondition_redundant_m,
    clobbers=frozenset(),
    resizes_room=False,
    mirrorable=False,
    apply=_apply_edit,
)
register(REDUNDANT_M)
