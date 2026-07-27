"""The DOOM (1993) title screen as line-segment data for the ``doom-screen`` CPU.

``lm1/programs/doom-screen.asm`` is a general vector-display processor: it reads
``x0 y0 x1 y1 colour`` five-word segments, draws each with Bresenham into the
LM-75's ``next`` buffer, and commits the whole frame once when it reads a negative
sentinel. This module supplies its demo payload — the 32x24, 16-colour DOOM title
screen, decomposed into maximal horizontal runs of equal colour.

Colour-0 runs are skipped: ``DSPS`` with 0 clears ``next``, so the buffer starts
black and black pixels need no segments. Every other run becomes one segment with
``y0 == y1`` and ``x0 <= x1`` (a one-pixel run is ``x0 == x1``, which the plotter's
Bresenham degenerates to correctly). The stream ends with a single ``-1``.

``main`` writes the case JSON that ``littleman/tools/display-frames.mjs`` grades
against::

    python -m randomfun2026solvers.doom_vector --cases \
        littleman/examples/doom-screen-cpu.cases.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

#: The frame, 24 rows by 32 columns, one hex digit (palette 0..15) per pixel.
#: Hand-derived from the DOOM title screen, row-major, top to bottom.
_ROWS = (
    "11111111111111111111111111111111",
    "11110000010000010000080000001111",
    "19310000030030030000030000001111",
    "88880000030080030000030000001111",
    "84413080030030030030030000001111",
    "11113033833030030033333300401111",
    "11113033833833030033333330801111",
    "11113033833333030333333333301111",
    "111138333773333b3333333333381111",
    "11113333888833b3b33333b333331111",
    "1111b3338887882333b3133b33331111",
    "1111b338888888821331111383331111",
    "1111bb38088882881111111108b33881",
    "11113380000882833111111008380811",
    "10013308808888233311100000881111",
    "10011808888808888330000000000101",
    "11100008370083888838000000000000",
    "11110088838833881888000000110000",
    "111110038378888881000800011884b8",
    "1111110118338888880111111111bbb8",
    "1111110118888888881111111111b8b8",
    "1111110888808888723311111113bbb3",
    "01110188888008887ff7311111118880",
    "0111018000000887fffb311011111130",
)

WIDTH = 32
HEIGHT = 24

#: The end-of-drawing sentinel: any negative x0 stops the CPU's round loop.
SENTINEL = -1


def frame_rows() -> list[str]:
    """The expected frame, in the problem JSONs' shape: rows of hex digits."""
    return list(_ROWS)


def segments() -> list[tuple[int, int, int, int, int]]:
    """``(x0, y0, x1, y1, colour)`` per maximal non-black horizontal run."""
    segs: list[tuple[int, int, int, int, int]] = []
    for y, row in enumerate(_ROWS):
        x = 0
        while x < WIDTH:
            colour = int(row[x], 16)
            end = x
            while end + 1 < WIDTH and row[end + 1] == row[x]:
                end += 1
            if colour != 0:  # the buffer starts black; black runs are free
                segs.append((x, y, end, y, colour))
            x = end + 1
    return segs


def input_words() -> list[int]:
    """The whole input stream: five words per segment, then the sentinel."""
    return [w for seg in segments() for w in seg] + [SENTINEL]


def _replayed_rows() -> list[str]:
    """Replay the segments over a black buffer — must reproduce ``_ROWS``."""
    buf = [[0] * WIDTH for _ in range(HEIGHT)]
    for x0, y0, x1, y1, colour in segments():
        assert y0 == y1 and 0 <= x0 <= x1 < WIDTH and 0 <= y0 < HEIGHT
        assert 1 <= colour <= 15
        for x in range(x0, x1 + 1):
            buf[y0][x] = colour
    return ["".join(f"{v:x}" for v in row) for row in buf]


def check() -> None:
    """Assert the decomposition is lossless. True by construction; assert anyway."""
    got = _replayed_rows()
    want = frame_rows()
    assert got == want, "\n".join(
        f"row {y}: got {g} want {w}" for y, (g, w) in enumerate(zip(got, want)) if g != w
    )


def cases_json() -> dict:
    """The display-frames case file: all input in ONE round, one expected frame."""
    check()
    return {
        "publicTestData": [
            {
                "name": "doom",
                "rounds": [
                    {
                        "in": [str(w) for w in input_words()],
                        "out": [],
                        "frames": [frame_rows()],
                    }
                ],
            }
        ]
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", metavar="PATH", help="write the display-frames case JSON here")
    args = ap.parse_args(argv)
    check()
    segs = segments()
    print(f"{len(segs)} segments, {len(input_words())} input words, {WIDTH}x{HEIGHT} frame")
    if args.cases:
        with open(args.cases, "w", encoding="utf-8") as fh:
            json.dump(cases_json(), fh, indent=2)
            fh.write("\n")
        print(f"wrote {args.cases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
