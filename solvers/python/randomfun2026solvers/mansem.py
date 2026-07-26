#!/usr/bin/env python3
"""The ISA effect table — what each glyph *reads* and *writes*, as pure data.

Every semantic rewrite in this project asks the same three questions of a glyph:
which registers does it **need** (read before writing), which does it **write**,
and what does it do to the man's **heading**? A constant-fold is legal only if no
intervening glyph reads the value it folds away; a loop unroll is legal only if
the count register is not clobbered mid-lap; a steer coalesce is legal only
between glyphs that leave the heading alone. All of those checks bottom out in
this table, so it lives in one pure module that every family imports and none may
fork.

The three register names are **frozen** to ``A``, ``B``, and ``BP`` — the two
hands and the backpack. (The man's *heading* is tracked separately, as the
:attr:`GlyphEffect.heading` class, not as a register; downstream graph code names
it ``HEAD``.) A glyph's ``heading`` class is one of:

* ``"keep"``  — heading survives the glyph unchanged (all arithmetic, hands,
  literals, nops, and pipe ops; the man walks straight through);
* ``"steer"`` — heading is set unconditionally (``> < ^ v V``);
* ``"branch"`` — heading depends on machine state (``X d a x``): the exit is not
  known from the glyph alone;
* ``"split"`` — the man is replaced by two children (``Y``);
* ``"halt"``  — the man stops (``H``).

The figures are taken **verbatim from** :doc:`littleman/SPEC.md`'s glyph tables
and cross-checked against the branch/steer sets already encoded in
:mod:`manatom`. Two SPEC subtleties are pinned here because they bite rewrites:

* ``/`` writes **both** hands — the quotient to ``A`` and the remainder to ``B``
  — so a rewrite may not treat ``B`` as free after a division.
* ``U`` receives into ``A`` **and turns** the man away from the side it read from.
  Its heading is therefore state-dependent, but we classify it ``"keep"`` (it
  does not *unconditionally* set a heading like a steer, nor fork like a branch)
  and flag the turn with :attr:`GlyphEffect.turns_on_read`, so a heading-coalesce
  pass can see it must not straighten a path across a ``U``.

:func:`run_effect` composes the table along a straight run of glyphs: a register
is *needed* by the run only if it is read before the run itself writes it.

:class:`BPFacts` is the type a (later) backpack-value prover will return; only the
type and its :meth:`BPFacts.unknown` bottom element live here for now.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "REGISTERS",
    "HEADINGS",
    "GlyphEffect",
    "glyph_effect",
    "run_effect",
    "BPFacts",
]

#: The frozen register namespace. The man's heading is *not* a register here; the
#: logic-graph layer names it ``HEAD`` separately.
REGISTERS: frozenset[str] = frozenset({"A", "B", "BP"})

#: The allowed :attr:`GlyphEffect.heading` classes.
HEADINGS: frozenset[str] = frozenset({"keep", "steer", "branch", "split", "halt"})


@dataclass(frozen=True)
class GlyphEffect:
    """The pure effect of a single instruction glyph.

    :param needs: registers read *before* this glyph writes them.
    :param writes: registers this glyph assigns.
    :param heading: one of :data:`HEADINGS`.
    :param turns_on_read: only ``U`` — it receives *and* turns, so its heading is
        state-dependent even though it is classed ``"keep"``. A path-straightening
        pass must treat this as an opaque turn.
    """

    needs: frozenset[str]
    writes: frozenset[str]
    heading: str
    turns_on_read: bool = False

    def __post_init__(self) -> None:  # cheap invariant check; table is hand-written
        if self.heading not in HEADINGS:
            raise ValueError(f"bad heading class {self.heading!r}")
        bad = (self.needs | self.writes) - REGISTERS
        if bad:
            raise ValueError(f"unknown register(s) {sorted(bad)}")


_A = frozenset({"A"})
_B = frozenset({"B"})
_BP = frozenset({"BP"})
_AB = frozenset({"A", "B"})
_NONE: frozenset[str] = frozenset()


def _keep(needs: frozenset[str], writes: frozenset[str]) -> GlyphEffect:
    return GlyphEffect(needs=needs, writes=writes, heading="keep")


# ── the table ─────────────────────────────────────────────────────────────────
# Built once, at import; every entry is a SPEC.md glyph-table row.
_TABLE: dict[str, GlyphEffect] = {}


def _register(glyphs: str, effect: GlyphEffect) -> None:
    for g in glyphs:
        _TABLE[g] = effect


# Constants: A = value; heading untouched. Digits and the backtick literal cell.
_register("0123456789`", _keep(_NONE, _A))

# Hands.
_register("M", _keep(_A, _B))  # B = A
_register("W", _keep(_AB, _AB))  # swap A and B

# Arithmetic / bitwise: A op= B, reading both, writing A. `/` also writes B (rem).
_register("+-*%&|~{}", _keep(_AB, _A))
_register("/", _keep(_AB, _AB))  # quotient -> A, remainder -> B
_register("N", _keep(_A, _A))  # A = -A

# Backpack loads / decrements (heading kept).
_register("b", _keep(_A, _BP))  # BP = A
_register("m", _keep(_BP, _BP))  # BP -= 1
_register("]", _keep(_BP, _BP))  # BP >>= 1
_register("q", _keep(_NONE, _BP))  # BP = count of nearest incoming pipe (no reg read)

# Branches: exit heading is state-dependent.
_register("da", GlyphEffect(needs=_BP, writes=_NONE, heading="branch"))  # BP>0 ? turn
_register("x", GlyphEffect(needs=_BP, writes=_NONE, heading="branch"))  # low-bit turn
_register("X", GlyphEffect(needs=_A, writes=_NONE, heading="branch"))  # sign(A) turn

# Steers: heading set unconditionally, no register effect.
_register("><^vV", GlyphEffect(needs=_NONE, writes=_NONE, heading="steer"))

# Split / halt / nops.
_register("Y", GlyphEffect(needs=_NONE, writes=_NONE, heading="split"))
_register("H", GlyphEffect(needs=_NONE, writes=_NONE, heading="halt"))
_register(". ", _keep(_NONE, _NONE))  # `.` and space are nops

# Pipe ops. Sends read A; receives write A. `U` also turns.
_register("sS", _keep(_A, _NONE))  # send A into a pipe (blocks if full)
_register("rR", _keep(_NONE, _A))  # receive into A
_TABLE["U"] = GlyphEffect(needs=_NONE, writes=_A, heading="keep", turns_on_read=True)


def glyph_effect(g: str) -> GlyphEffect:
    """The :class:`GlyphEffect` of a single instruction glyph.

    Raises :class:`ValueError` for anything that is not a room instruction (walls,
    pipe bodies, ``@``, and the like are not in the table — a caller that reaches
    them has a structural bug, not a semantic one).
    """
    if len(g) != 1:
        raise ValueError(f"glyph_effect expects one character, got {g!r}")
    try:
        return _TABLE[g]
    except KeyError:
        raise ValueError(f"{g!r} is not an instruction glyph") from None


def run_effect(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Compose the effects of a straight run of glyphs into ``(needs, writes)``.

    A register is *needed* by the run only if some glyph reads it **before** the
    run has written it; a register is *written* if any glyph in the run writes it.
    So ``run_effect("rs")`` needs nothing (``r`` writes ``A`` before ``s`` reads
    it) and writes ``{A}``, while ``run_effect("Mb")`` still needs ``{A}`` and
    writes ``{B, BP}``.
    """
    written: set[str] = set()
    needs: set[str] = set()
    writes: set[str] = set()
    for ch in text:
        eff = glyph_effect(ch)
        for r in eff.needs:
            if r not in written:
                needs.add(r)
        for r in eff.writes:
            written.add(r)
            writes.add(r)
    return frozenset(needs), frozenset(writes)


@dataclass(frozen=True)
class BPFacts:
    """What is provable about the backpack (``BP``) at a program point.

    Only the type and its bottom element exist now; a real prover lands later. A
    loop-unroll by ``v`` is legal only when ``v in divisible_by`` (or ``const`` is
    a known multiple of ``v``); ``source == "pipe-q"`` or ``"unknown"`` means the
    count came from ``q`` / cannot be reasoned about, so unrolling must refuse.

    :param source: one of ``"literal"``, ``"pipe-q"``, ``"derived"``, ``"unknown"``.
    """

    const: int | None
    divisible_by: frozenset[int] = field(default_factory=frozenset)
    source: str = "unknown"

    @classmethod
    def unknown(cls) -> BPFacts:
        """The bottom element: nothing is known about ``BP``."""
        return cls(const=None, divisible_by=frozenset(), source="unknown")
