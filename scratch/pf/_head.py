"""The pathfinder machine as a block graph of straight glyph runs.

Cell (x, y) has display address ``p = 16*y + x``; the plane's bit index is
``g = 255 - p`` -- a 180-degree rotation -- because the setup loop can only
build a word with ``w = 2*w + bit``, which puts the first cell read at the
*top* bit.  Working rotated costs nothing: the board is symmetric and only
the tie-break order flips, which is a different test order and nothing else.

g-word ``j`` holds bits ``64j..64j+63``; ring group ``pos`` (production
order) is g-word ``j = 3 - pos``.

Every plane word is non-negative: bit 63 of word j is g = 64j+63, i.e.
p = 192-64j, i.e. column 0, always a border wall.  So ``}`` is a logical
shift on everything we shift, and an out-of-range shift yields 0 -- which is
what makes "test a bit in whichever word holds it" branch-free.

Ring, 18 words: ``[Q, P, g0, g1, g2, g3]``, group = ``[S1, NB, S2, S3]``.
Every pass is a 4-iteration counted lap that reads Q and P, pushes them back,
then walks the four groups.  ``F`` is the spill FIFO, ``G`` the scratch FIFO;
both are empty at every lap boundary.
"""
from __future__ import annotations


def L(v: int) -> str:
    return f"L{v}"


def lap(P, name, nxt, body):
    P[name + "T"] = (["d"], {"pos": name + "B", "zero": nxt})
    P[name + "B"] = ([*body, "m"], name + "T")


def build() -> dict:
    P: dict[str, tuple[list[str], object]] = {}

    # ══ INIT ════════════════════════════════════════════════════════════════
[176 more lines]