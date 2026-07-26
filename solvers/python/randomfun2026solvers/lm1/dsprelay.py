#!/usr/bin/env python3
"""The display fan-out relay: one CPU pipe in, three LM-75 ports out.

``isa.py`` says of the display ops:

    `DSP p` cannot be built: it picks a pipe from its *operand*, and which pipe an
    `s` talks to is decided by where the glyph sits (ARCH.md §7.1) — a static
    property of the grid.

That is true and it is not the end of the story.  A *lane* cannot pick a pipe, so
the CPU keeps sending down exactly one; the choice moves **behind** the seam, into
a small room whose three ``s`` glyphs each sit statically beside their own port.
The lane sends two words — the port selector, then ACC — and the relay reads the
selector, branches, and forwards the value to the port it names.

Why that is worth a room.  The CPU's lane band is ``2 * (1 << k) - 1`` rows where
``k`` is the decode trie's depth, and ``k = (len(used) - 1).bit_length()`` over the
opcodes *this program* uses.  `little-little-man` uses 19, so ``k = 5``, the band is
63 rows and **13 of the trie's 32 leaf slots are empty**.  Folding three display
opcodes into one is two of the three removals that take it to 16, ``k`` to 4, and
the band to 31 rows — 32 rows off a dimension the machine is charged for.

**Port codes are the emulator's, unchanged**: 0 = ADDR, 1 = DATA, 2 = SWAP, matching
``display_writes``.  ROM words are non-negative (``rom.digit_width`` rejects a
negative literal), so the selector cannot itself carry a sign for ``X`` to test.
The relay therefore subtracts one — ``p - 1`` is -1/0/+1 — which costs three glyphs
in a room rather than a wider word in every ROM literal.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from ..circuit import Circuit, E, N, S

__all__ = ["ARM_TAG", "PORT_ADDR", "PORT_DATA", "PORT_SWAP", "build_relay_probe", "relay_model"]

#: The emulator's ``display_writes`` port codes, which the selector reuses verbatim.
PORT_ADDR, PORT_DATA, PORT_SWAP = 0, 1, 2

#: What each arm of the probe adds to the value, so a single ``O`` room can say
#: which arm ran. The real relay sends the value unchanged to its own pipe.
ARM_TAG = {PORT_ADDR: 100, PORT_DATA: 200, PORT_SWAP: 300}


def relay_model(port: int, value: int) -> int:
    """What :func:`build_relay_probe` must emit. The oracle."""
    return ARM_TAG[port] + value


def build_relay_probe() -> list[str]:
    """One ``(port, value)`` pair in, ``tag + value`` out — the arm decision, proven.

    A probe rather than the relay itself, for the reason the store selector was
    probed before it was placed: the three real ports are three *pipes*, and a room
    holds one ``O``.  Tagging each arm's output is what makes the choice observable
    through a single outgoing pipe — emitting the bare value from all three would
    pass whether or not the branch worked, because the answer would not depend on
    which arm was live.

    The room has exactly one incoming and one outgoing pipe, so every ``r`` binds to
    the request and every ``s`` to the response wherever they sit (§7.1 has nothing
    to choose between).  In the placed relay the arms instead own different pipes,
    and that is the only difference — the control flow is this.
    """
    iw, ih = 34, 11
    r = Circuit(iw, ih)

    # A = p, B = p, then A = p - 1 with B holding 1. `X` is three-way: the man walks
    # east, so counter-clockwise is north, straight is east, clockwise is south.
    r.run(1, 5, "@rM`1`W-X")

    # p = 0 -> -1 -> north: ADDR.
    r.route((9, 4), N, [(9, 2)], (11, 2), E)
    r.run(11, 2, f"rM`{ARM_TAG[PORT_ADDR]}`+sH")

    # p = 1 -> 0 -> straight on east: DATA.
    r.run(11, 5, f"rM`{ARM_TAG[PORT_DATA]}`+sH")

    # p = 2 -> +1 -> south: SWAP.
    r.route((9, 6), S, [(9, 8)], (11, 8), E)
    r.run(11, 8, f"rM`{ARM_TAG[PORT_SWAP]}`+sH")

    g = Circuit(iw + 12, ih + 2)
    ox, oy = 6, 1
    for (x, y), glyph in r.cell.items():
        g.set(ox + x, oy + y, glyph)
    for x in range(-1, iw + 1):
        g.set(ox + x, oy - 1, "+" if x in (-1, iw) else "-")
        g.set(ox + x, oy + ih, "+" if x in (-1, iw) else "-")
    for y in range(ih):
        g.set(ox - 1, oy + y, "|")
        g.set(ox + iw, oy + y, "|")

    row = oy + 5  # the spawn's row: the room's only two pipes hang off it
    for i, line in enumerate(("+-+", "|I|", "+-+")):
        for j, glyph in enumerate(line):
            g.set(j, row - 1 + i, glyph)
    g.run(3, row, ">>", d=E)

    out_x = ox + iw + 3
    for i, line in enumerate(("+-+", "|O|", "+-+")):
        for j, glyph in enumerate(line):
            g.set(out_x + j, row - 1 + i, glyph)
    g.run(ox + iw + 1, row, ">>", d=E)

    return [line.rstrip() for line in g.rows() if line.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0]).parse_args(argv)
    print("\n".join(build_relay_probe()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
