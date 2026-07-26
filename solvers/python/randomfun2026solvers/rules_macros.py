#!/usr/bin/env python3
"""Cross-family *macro* rewrites — a combination of glyphs collapsed as a whole.

The per-family catalogs (``rules_loops``, ``rules_arith``, ``rules_const``, …) each
reason inside one glyph family. A **macro** is the thing none of them can do alone:
it recognises a *combination that spans families* and replaces it with a shorter or
faster equivalent that only becomes provable once both families are in view at once.
This is the task's explicit "macro heuristic".

Two macros live here, each registered into the catalog bucket of its **dominant
effect** (:mod:`manrules` ``FAMILIES``):

* **``loop.const_unroll`` (family ``loop``) — the flagship.** A literal constant
  loads ``A`` and ``b`` copies it into ``BP`` (``` `8`b ``` or ``8`` then ``b``), and
  that ``BP`` immediately drives a :func:`~manatom.counted_loop`. Because the count is
  a *statically visible constant*, its divisibility by an unroll factor ``v`` is
  **proven at rewrite time** — the one fact the standalone loop family cannot supply,
  since it never sees the constant. The macro emits :func:`~manatom.unrolled` at the
  **largest ``v`` in 2..8 that divides the constant**, taking ticks-per-value from 8
  down toward 4. This turns the loop family's "usually no match" (an unknown ``BP``)
  into a real match whenever the count is a literal — including odd composites like 9
  (``v=3``) that an even-only unroll would miss.

* **``arith.load_op_fold`` (family ``arith``) — const-fold across a hand move.** A run
  ``L1 M L2 OP`` loads ``L1`` into ``A``, copies it into ``B`` with ``M``, loads
  ``L2`` into ``A``, then applies a binary op: ``A = L2 OP L1``. When both operands
  are literals the result is a constant, so the four tokens collapse to ``L1 M R``
  (``R`` = the folded literal). ``L1 M`` is kept verbatim, so ``B`` still ends at
  ``L1`` exactly as before — the fold preserves **both** hands and the heading, and is
  therefore safe with no liveness analysis. It only fires when the replacement is no
  wider than the original span (no reflow) and the op is not ``/`` (which also writes
  ``B``, breaking the preserved-``B`` invariant).

**Hazards honoured.** BP divisibility for the unroll is proven from the *visible*
constant only (``source == "literal"``); a ``q``-sourced or otherwise unknown count
refuses to match (plan hazard #2). Both macros preserve the man's entry/exit ports and
register state; the loop swap reuses ``counted_loop``/``unrolled``'s identical ports,
and the arith fold keeps ``L1 M`` so nearest-pipe bindings and downstream reads are
untouched. The driver's ``verify`` gate is the final authority on both.
"""

from __future__ import annotations

from .manast import Ast, Atom, RoomNode, Run
from .manatom import counted_loop, unrolled
from .manrecog import match_counted_loop
from .manrules import CostDelta, MatchSite, RewriteRule, register
from .mansem import BPFacts

__all__ = [
    "CONST_UNROLL",
    "LOAD_OP_FOLD",
    "const_feeding_bp",
    "best_unroll_factor",
    "encode_literal",
    "eval_binop",
]

# ── shared: proving a visible constant feeds BP ───────────────────────────────
#: The body the flagship handles — the canonical move-one-value loop, 8 ticks/value.
_LOOP_BODY = "rs"
#: The largest unroll factor the library caps at (``unrolled(8)`` = 18 rows tall).
_MAX_V = 8


def _literal_before_b(glyphs: str) -> int | None:
    """The integer a ``… <literal> b`` prefix loads into ``BP``, or ``None``.

    Recognises one bare digit or one backtick-delimited integer immediately before the
    first ``b``. Any other shape (no ``b``, a non-literal glyph before it) yields
    ``None`` so the prover stays conservative — the whole point of the macro is a
    *proven* constant, so an ambiguous source must refuse.
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
        digits = inner[start + 1 :].replace(" ", "")
        return int(digits) if digits.isdigit() else None
    last = prefix[-1]
    return int(last) if last.isdigit() else None


def const_feeding_bp(room: RoomNode) -> BPFacts:
    """Prove the loop's backpack count is a literal constant, or return ``unknown``.

    Walks the room's straight-code :class:`~manast.Run` children for a single
    ``… <literal> b`` load and returns a ``"literal"`` :class:`~mansem.BPFacts` with
    the exact value and its 2..8 divisors. Zero or several such loads → ``unknown``
    (the macro cannot tell which count feeds the loop, so it refuses to unroll).
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
    divisors = frozenset(d for d in range(2, _MAX_V + 1) if const % d == 0)
    return BPFacts(const=const, divisible_by=divisors, source="literal")


def best_unroll_factor(facts: BPFacts) -> int | None:
    """The largest ``v`` in 2..8 dividing a *literal* count, or ``None`` if there is none.

    ``None`` for a non-literal source (nothing is proven), for a count below 2, and for
    a literal whose only divisors exceed 8 (a prime like 11, or 1/0) — in every one of
    those cases unrolling is either unprovable or pointless, so the macro must not fire.
    """
    if facts.source != "literal" or facts.const is None or facts.const < 2:
        return None
    return max(facts.divisible_by) if facts.divisible_by else None


# ── flagship macro: const-count → provable unroll ─────────────────────────────
def _unroll_recognize(_ast: object, room: RoomNode) -> list[MatchSite]:
    """Every ``counted_loop("rs")`` in `room` whose count is a proven literal.

    A site is emitted only when (a) a canonical move-one-value loop is present and
    (b) :func:`const_feeding_bp` proves a literal count with a 2..8 divisor. The chosen
    factor is the *largest* such divisor, carried in ``env["pairs"]`` for the builder.
    """
    if room.kind != "compute":
        return []
    facts = const_feeding_bp(room)
    v = best_unroll_factor(facts)
    if v is None:
        return []
    sites: list[MatchSite] = []
    for child in room.children:
        if not isinstance(child, Atom):
            continue
        m = match_counted_loop(child)
        if m is None or m.body != _LOOP_BODY:
            continue
        sites.append(
            MatchSite(
                rule=CONST_UNROLL,
                room_id=room.id,
                cells=frozenset(child.paint()),
                entry=m.entry,
                exits=(m.exit_,),
                env={"k": facts, "body": m.body, "pairs": [v]},
            )
        )
    return sites


def _unroll_build(site: MatchSite) -> list[object]:
    """The replacement: a single :func:`~manatom.unrolled` gadget at the chosen factor."""
    (v,) = site.env["pairs"]  # type: ignore[misc]
    return [unrolled(v)]


def _unroll_cost(site: MatchSite) -> CostDelta:
    """8 → ``(4v+4)/v`` ticks per value; the block grows to ``2v+2`` rows."""
    (v,) = site.env["pairs"]  # type: ignore[misc]
    before = counted_loop(_LOOP_BODY)
    after = unrolled(v)
    d_ticks = (after.ticks_per_value or 0.0) - (before.ticks_per_value or 0.0)
    d_cells = _nonblank_cells(after.rows) - _nonblank_cells(before.rows)
    return CostDelta(d_cells=d_cells, d_ticks_per_value=d_ticks)


def _unroll_precondition(site: MatchSite) -> bool:
    """Re-prove ``const % v == 0`` from a literal source before the applier runs."""
    facts = site.env.get("k")
    pairs = site.env.get("pairs")
    if not (isinstance(facts, BPFacts) and facts.source == "literal" and facts.const is not None):
        return False
    if not (isinstance(pairs, list) and len(pairs) == 1):
        return False
    v = pairs[0]
    return isinstance(v, int) and 2 <= v <= _MAX_V and facts.const % v == 0


def _nonblank_cells(rows: tuple[str, ...]) -> int:
    """Occupied (non-blank) cells of a gadget — the ``d_cells`` the footprint sees."""
    return sum(len(r.replace(" ", "")) for r in rows)


#: Visible-constant count feeding ``counted_loop("rs")`` → ``unrolled(largest v≤8)``.
CONST_UNROLL = RewriteRule(
    name="loop.const_unroll",
    family="loop",
    recognize=_unroll_recognize,
    build=_unroll_build,
    cost_delta=_unroll_cost,
    preconditions=_unroll_precondition,
    clobbers=frozenset(),  # same registers as the loop: A (via r), BP
    resizes_room=True,  # up to 2v+2 rows: the room must grow to hold it
    mirrorable=False,
)


# ── arith macro: const-fold across a hand move ────────────────────────────────
#: Binary ops that read ``{A,B}`` and write **only** ``A`` (``/`` also writes ``B`` and
#: is excluded, so the fold's preserved-``B`` invariant holds).
_FOLDABLE_OPS = frozenset("+-*%&|~{}")
_MASK64 = (1 << 64) - 1


def _to_signed64(n: int) -> int:
    n &= _MASK64
    return n - (1 << 64) if n & (1 << 63) else n


def eval_binop(op: str, a: int, b: int) -> int | None:
    """``A = a OP b`` per SPEC.md's 64-bit two's-complement arithmetic, or ``None``.

    ``a`` is the value in ``A`` (the second literal), ``b`` the value in ``B`` (the
    first literal, copied by ``M``). Returns ``None`` for an op this macro does not
    fold (``/``, or anything outside :data:`_FOLDABLE_OPS`) so the caller refuses.
    """
    if op == "+":
        return _to_signed64(a + b)
    if op == "-":
        return _to_signed64(a - b)
    if op == "*":
        return _to_signed64(a * b)
    if op == "%":
        return 0 if b == 0 else a - (a // b) * b  # floored, takes B's sign (Python %)
    if op == "&":
        return _to_signed64(a & b)
    if op == "|":
        return _to_signed64(a | b)
    if op == "~":  # SPEC: `~` is XOR
        return _to_signed64(a ^ b)
    if op == "{":  # A << B, 0 if B outside 0..63
        return 0 if not (0 <= b <= 63) else _to_signed64(a << b)
    if op == "}":  # arithmetic A >> B, 0 if B<0, sign-fill if B>63
        if b < 0:
            return 0
        return _to_signed64(a >> min(b, 63))
    return None


def encode_literal(n: int) -> str | None:
    """Encode a non-negative integer as a littleman literal, or ``None`` if it can't.

    ``0``–``9`` become a bare digit; larger values a backtick literal ``` `NN` ```.
    Negative results need an extra ``N`` glyph (and would widen), so they return
    ``None`` and the fold declines — keeping the replacement purely local.
    """
    if n < 0:
        return None
    if n <= 9:
        return str(n)
    return f"`{n}`"


def _read_literal_token(glyphs: str, i: int) -> tuple[int, int] | None:
    """Read a literal token starting at index `i`: ``(value, end_index)`` or ``None``.

    A bare digit is one char; a backtick literal runs to its closing backtick. Anything
    else (an op glyph, ``M``, a space) is not a literal, so returns ``None``.
    """
    ch = glyphs[i]
    if ch.isdigit():
        return int(ch), i + 1
    if ch == "`":
        end = glyphs.find("`", i + 1)
        if end < 0:
            return None
        inner = glyphs[i + 1 : end].replace(" ", "")
        if not inner.isdigit():
            return None
        return int(inner), end + 1
    return None


def _fold_sites_in_run(room: RoomNode, run: Run) -> list[MatchSite]:
    """Every non-overlapping ``L1 M L2 OP`` fold in one straight run."""
    sites: list[MatchSite] = []
    g = run.glyphs
    i = 0
    while i < len(g):
        found = _match_fold_at(g, i)
        if found is None:
            i += 1
            continue
        end, new_span, op, result = found
        cells = _span_cells(run, i, end)
        sites.append(
            MatchSite(
                rule=LOAD_OP_FOLD,
                room_id=room.id,
                cells=cells,
                entry=None,  # a content edit; the man's path is unchanged
                exits=(),
                env={
                    "op": op,
                    "run_id": run.id,
                    "at": i,
                    "old": g[i:end],
                    "new": new_span,
                    "result": result,
                },
            )
        )
        i = end  # non-overlapping: skip past this match
    return sites


def _match_fold_at(g: str, i: int) -> tuple[int, str, str, int] | None:
    """Try to match ``L1 M L2 OP`` starting at `i`; ``(end, new_span, op, result)``.

    Returns ``None`` unless the four tokens are present, the op is foldable, the result
    encodes non-negatively, and the replacement ``L1 M R`` is **no wider** than the
    matched span (so the run never grows and nothing reflows).
    """
    t1 = _read_literal_token(g, i)
    if t1 is None:
        return None
    v1, j = t1
    if j >= len(g) or g[j] != "M":
        return None
    j += 1
    t2 = _read_literal_token(g, j)
    if t2 is None:
        return None
    v2, k = t2
    if k >= len(g) or g[k] not in _FOLDABLE_OPS:
        return None
    op = g[k]
    end = k + 1
    result = eval_binop(op, v2, v1)  # A = A(=v2) OP B(=v1)
    if result is None:
        return None
    enc = encode_literal(result)
    if enc is None:
        return None
    l1 = g[i:j - 1]  # the L1 literal, verbatim (keeps B = v1)
    new_span = f"{l1}M{enc}"
    if len(new_span) > (end - i):  # must not widen the run
        return None
    return end, new_span, op, result


def _span_cells(run: Run, start: int, end: int) -> frozenset[tuple[int, int]]:
    """Absolute cells the glyph span ``[start, end)`` of `run` paints (blanks skipped)."""
    dx, dy = (1, 0) if run.heading == "E" else (0, 1)
    out = {
        (run.x + n * dx, run.y + n * dy)
        for n in range(start, end)
        if run.glyphs[n] != " "
    }
    return frozenset(out)


def _fold_recognize(_ast: object, room: RoomNode) -> list[MatchSite]:
    """Every ``L1 M L2 OP`` const-fold across the room's straight runs."""
    if room.kind != "compute":
        return []
    sites: list[MatchSite] = []
    for child in room.children:
        if isinstance(child, Run):
            sites.extend(_fold_sites_in_run(room, child))
    return sites


def _fold_apply(ast: Ast, site: MatchSite) -> None:
    """Rewrite the matched run's glyphs in place, folding the span to ``L1 M R``.

    Locates the run by id in the (deep-copied) AST and asserts the span still reads as
    the recogniser saw it before splicing, so a stale site can never corrupt the run.
    """
    room = next(r for r in ast.rooms if r.id == site.room_id)
    run = next(
        c
        for c in room.children
        if isinstance(c, Run) and c.id == site.env["run_id"]
    )
    at = site.env["at"]
    old = site.env["old"]
    new = site.env["new"]
    assert isinstance(at, int) and isinstance(old, str) and isinstance(new, str)
    if run.glyphs[at : at + len(old)] != old:
        raise ValueError("run glyphs drifted from the recognised span")
    run.glyphs = run.glyphs[:at] + new + run.glyphs[at + len(old) :]


def _fold_cost(site: MatchSite) -> CostDelta:
    """Fewer glyphs walked: the op (and any literal shrink) is gone — a tick + cells win."""
    old = site.env["old"]
    new = site.env["new"]
    assert isinstance(old, str) and isinstance(new, str)
    d = len(new) - len(old)  # ≤ -1: at least the op glyph is removed
    return CostDelta(d_cells=d, d_ticks_per_value=float(d))


def _fold_precondition(site: MatchSite) -> bool:
    """Re-check the fold is a foldable op and the replacement does not widen the run."""
    old = site.env.get("old")
    new = site.env.get("new")
    op = site.env.get("op")
    return (
        isinstance(old, str)
        and isinstance(new, str)
        and isinstance(op, str)
        and op in _FOLDABLE_OPS
        and len(new) <= len(old)
    )


#: ``L1 M L2 OP`` with literal operands → ``L1 M <folded literal>`` (``B`` preserved).
LOAD_OP_FOLD = RewriteRule(
    name="arith.load_op_fold",
    family="arith",
    recognize=_fold_recognize,
    build=lambda _s: [],  # a cell edit, not a gadget swap
    cost_delta=_fold_cost,
    preconditions=_fold_precondition,
    clobbers=frozenset(),  # A and B end identical to the original
    resizes_room=False,
    mirrorable=False,
    apply=_fold_apply,
)


register(CONST_UNROLL)
register(LOAD_OP_FOLD)
