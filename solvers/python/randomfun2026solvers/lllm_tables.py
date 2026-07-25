#!/usr/bin/env python3
"""Decode tables for the `little-little-little-man` machine.

The setup pass has to turn one ASCII byte into a *class* (what the interpreted
man does when he stands there) and a *colour* (what the display shows).  A
14-way branch tree costs a hundred-odd grid cells; two 64-bit literals cost
about forty, because a 64-bit word **is** a sixteen-entry nibble table and `}`
plus `&` reads one entry.

Two lookups chained, not two parallel ones — that matters, because the shift
amount is destroyed by the mask (`&` needs `B = 15`, and `B` is where the shift
lives).  So the class table is indexed by a perfect hash of the byte, and the
colour table is indexed by the *class* the first lookup just produced:

    i      = (c * 29) >> 6 & 15          # injective over the twelve non-digits
    j      = (CLASS_MAGIC  >> 4*i) & 15  # j = class - 10
    class  = j + 10
    colour = (COLOUR_MAGIC >> 4*j) & 15

Digits are not in the table at all: `0`-`9` are ASCII 48..57 and their class is
`c - 48` with colour 8 throughout, which is one sign test and a subtraction.

Wall glyphs never reach this table.  Rows 0 and H-1 of a well-formed program are
entirely room wall, so the setup pass emits them without decoding anything, and
in every other row `|` is the only wall glyph — so `+` and `-` are unambiguously
arithmetic here and need no positional test.

`class` is the only thing the store keeps (one byte per cell, eight cells to a
64-bit word).  Its numbering is chosen so the two commonest cases need no
dispatch at all: `0..9` are the digits and `AI = class`; `16..19` are the four
headings and `DIR = class - 16`.
"""

from __future__ import annotations

__all__ = [
    "CLASS_MAGIC",
    "COLOUR_MAGIC",
    "CLS_ADD",
    "CLS_AT",
    "CLS_DIR0",
    "CLS_H",
    "CLS_M",
    "CLS_SPACE",
    "CLS_SUB",
    "CLS_WALL",
    "CLS_X",
    "HASH_MUL",
    "HASH_SHIFT",
    "class_colour",
    "decode_ascii",
    "hash_index",
]

# ── class numbering ───────────────────────────────────────────────────────────
# 0..9   digit d          AI = d           colour 8
CLS_SPACE = 10  # space                     colour 0
CLS_M = 11  # M            BI = AI          colour 12
CLS_ADD = 12  # +          AI += BI         colour 10
CLS_SUB = 13  # -          AI -= BI         colour 10
CLS_X = 14  # X            rotate by sign   colour 3
CLS_H = 15  # H            halt             colour 3
CLS_DIR0 = 16  # ^ > v <   DIR = cls - 16   colour 3   (16=N 17=E 18=S 19=W)
CLS_WALL = 20  # room wall  halt            colour 4
CLS_AT = 21  # @           like space, but marks the spawn; stored as CLS_SPACE

#: ASCII -> (class, colour) for every non-digit glyph the decoder can meet.
GLYPHS: dict[int, tuple[int, int]] = {
    32: (CLS_SPACE, 0),  # space
    43: (CLS_ADD, 10),  # +
    45: (CLS_SUB, 10),  # -
    60: (CLS_DIR0 + 3, 3),  # <  W
    62: (CLS_DIR0 + 1, 3),  # >  E
    # `@` paints as the man himself (9) on the setup frame — he is standing on
    # it — so the *decode* colour is 9.  Its stored class keeps him locatable,
    # and the runtime lane for that class paints the vacated cell 0.
    64: (CLS_AT, 9),  # @
    72: (CLS_H, 3),  # H
    77: (CLS_M, 12),  # M
    88: (CLS_X, 3),  # X
    94: (CLS_DIR0, 3),  # ^  N
    118: (CLS_DIR0 + 2, 3),  # v  S
    124: (CLS_WALL, 4),  # |
}

#: `(c * 29) >> 6 & 15` is injective over :data:`GLYPHS`.  Found by exhaustive
#: search over `(c * K) >> S & 15` for K < 512, S < 20; there are five such
#: (K, S) and this is the smallest.
HASH_MUL, HASH_SHIFT = 29, 6


def hash_index(code: int) -> int:
    return ((code * HASH_MUL) >> HASH_SHIFT) & 15


def _build() -> tuple[int, int]:
    cls_nibbles = [0] * 16
    col_nibbles = [0] * 16
    seen: dict[int, int] = {}
    for code, (cls, colour) in GLYPHS.items():
        idx = hash_index(code)
        if idx in seen:  # pragma: no cover - the hash is fixed and injective
            raise AssertionError(f"hash collision: {code} and {seen[idx]} -> {idx}")
        seen[idx] = code
        offset = cls - CLS_SPACE
        if not 0 <= offset <= 15:  # pragma: no cover - numbering is fixed
            raise AssertionError(f"class {cls} does not fit a nibble")
        cls_nibbles[idx] = offset
        col_nibbles[offset] = colour
    magic_c = sum(v << (4 * i) for i, v in enumerate(cls_nibbles))
    magic_l = sum(v << (4 * i) for i, v in enumerate(col_nibbles))
    for magic in (magic_c, magic_l):
        if magic >= 1 << 63:  # pragma: no cover - both fit as written
            raise AssertionError("magic does not fit a positive 64-bit literal")
    return magic_c, magic_l


CLASS_MAGIC, COLOUR_MAGIC = _build()


def decode_ascii(code: int) -> tuple[int, int]:
    """`(class, colour)` for one program byte, exactly as the grid computes it."""
    digit = code - 48
    if 0 <= digit <= 9:
        return digit, 8
    j = (CLASS_MAGIC >> (4 * hash_index(code))) & 15
    return j + CLS_SPACE, (COLOUR_MAGIC >> (4 * j)) & 15


#: Colour the *runtime* paints into a cell the man has just left, by class.
#: It differs from :data:`COLOUR_MAGIC` in exactly one entry: a vacated `@` is
#: ordinary black.  The halt classes never appear here — the man never leaves a
#: wall or an `H` — so the machine's lanes carry these as constants and no
#: second magic is needed on the grid.
RUNTIME_COLOUR: dict[int, int] = {
    CLS_SPACE: 0,
    CLS_M: 12,
    CLS_ADD: 10,
    CLS_SUB: 10,
    CLS_X: 3,
    CLS_DIR0: 3,
    CLS_DIR0 + 1: 3,
    CLS_DIR0 + 2: 3,
    CLS_DIR0 + 3: 3,
    CLS_AT: 0,
}


def class_colour(cls: int) -> int:
    """Colour the runtime repaints a vacated cell of class `cls` with."""
    if 0 <= cls <= 9:
        return 8
    return RUNTIME_COLOUR[cls]


if __name__ == "__main__":  # pragma: no cover - a one-line self-check
    print(f"CLASS_MAGIC  = {CLASS_MAGIC}\nCOLOUR_MAGIC = {COLOUR_MAGIC}")
    for code, want in sorted(GLYPHS.items()):
        got = decode_ascii(code)
        print(f"  {code:4} {chr(code)!r:5} -> {got}  {'ok' if got == want else 'BAD'}")
