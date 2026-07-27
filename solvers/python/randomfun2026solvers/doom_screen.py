#!/usr/bin/env python3
"""Draw the DOOM (1993) title screen on an LM-75 display. Ungraded, for fun.

Image provenance: DOOM (1993) title screen,
https://doomwiki.org/w/images/4/4b/Doom-1-.gif, downsampled 10x with per-block
minimal-Lab-distance quantization to the ANSI 16-color palette. 32x24 pixels,
one hex digit per pixel (palette index 0..15), row-major top-to-bottom.

The machine is three boxes and three pipes:

* **display** -- LM-75, interior 32x24. Only two of its ports are used: DATA
  (left wall) and SWAP (bottom wall). The cursor starts at (0,0) and DATA
  auto-advances row-major, so streaming all 768 colors paints the whole frame
  and no ADDR pipe is ever needed.

* **ROM room** -- a single man boustrophedon-walks rows of glyphs. The pixel
  stream is run-length coded for free by the machine itself: A persists across
  cells and turns, so a pixel whose color equals the previous one is a bare
  ``s`` and only color *changes* load a new constant. Colors 10..15 have no
  digit glyph; instead of backtick literals (whose row/column pairing rules
  are a minefield) the prologue parks 8 in B once (``8 M``) and any high color
  ``c`` is then ``(c^8) ~`` -- two plain glyphs, splittable across row turns.
  The room has exactly one outgoing pipe and zero incoming, so every ``s``
  binds to it regardless of position. Ends with ``H``.

* **relay room** -- ``` `768` b ``` then a counted loop of ``r``/``s``
  forwarding each color into DATA, then ``0`` ``s`` into SWAP to commit the
  frame, then a ~490-tick delay loop (``9 M * b`` + empty counted loop) before
  ``H``. The delay outlives the SWAP pipe's ~112-cell transit, so the commit
  happens while a man is still alive no matter how the engine treats pipes in
  flight after the last halt. The loop ``s`` sits by the east wall (nearest:
  DATA pipe), the exit ``s`` by the west wall under the SWAP pipe's port
  (nearest: SWAP pipe); both bindings are pinned by the engine's route oracle
  in ``tests/test_doom_screen.py``.

The SWAP pipe is deliberately long (north over the display, down its east
side, west under its bottom wall): the commit must arrive after all 768 DATA
values, and DATA's pipe is only 2 cells.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from randomfun2026solvers.circuit import Circuit
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

__all__ = ["HEX_ROWS", "build", "tokens", "debug_map", "main"]

#: The image: 24 rows of 32 hex digits, palette index 0..15, row-major.
HEX_ROWS = [
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
]

# ── geometry ─────────────────────────────────────────────────────────────────
GRID_W = 47

#: Display wall corners (11,1)-(44,26); interior x 12..43, y 2..25 = 32x24.
DISP_WX0, DISP_WY0 = 11, 1
DISP_IW, DISP_IH = 32, 24
DISP_WX1 = DISP_WX0 + DISP_IW + 1
DISP_WY1 = DISP_WY0 + DISP_IH + 1

#: Relay wall corners (0,15)-(8,27); interior x 1..7, y 16..26.
REL_WX0, REL_WY0, REL_WX1, REL_WY1 = 0, 15, 8, 27

#: ROM north wall row; interior rows start below it, height picked to fit.
ROM_WY0 = 30
ROM_IW = 38
TURN_L = 1                       # left turn column (holds `@` on row 0)
TURN_R = TURN_L + ROM_IW - 1     # right turn column
CONTENT_X0, CONTENT_X1 = TURN_L + 1, TURN_R - 1

PIPE_A_X = 4     # ROM -> relay, up through the relay's south wall
PIPE_B_Y = 19    # relay -> display DATA, east through the display's west wall
PIPE_C_X = 2     # relay -> display SWAP, out of the relay's north wall
SWAP_COL = 20    # column where the SWAP pipe enters the display's bottom wall
EAST_COL = 46    # the SWAP pipe's descent column, 2 east of the display

#: Relay interior, 7 wide, stamped at (1,16). Walk: `@` loads 768, `b`, then
#: the 8-cell counted loop (d tests BP before the body, m decrements on the
#: return leg) forwards 768 colors to DATA; on exit `0` `s` commits via SWAP
#: and `9 M * b` + an empty counted loop burn ~490 ticks before `H`.
RELAY_ART = [
    "@`768`v",
    "      b",
    "   > mv",
    "   ^srd",
    "vs0   <",
    "9      ",
    "M      ",
    "*      ",
    ">b>dH  ",
    "  m    ",
    "  ^<   ",
]

#: The relay's send cells (grid coordinates) and the pipe cell each must bind
#: to -- the source segment attached to the relay. Checked by the route oracle.
LOOP_SEND = (5, 19)      # inner-loop `s`  -> DATA pipe source (9, PIPE_B_Y)
EXIT_SEND = (2, 20)      # exit `s`        -> SWAP pipe source (PIPE_C_X, 14)
LOOP_RECV = (6, 19)      # inner-loop `r`  -> ROM pipe destination (PIPE_A_X, 28)


# ── the ROM's token stream ───────────────────────────────────────────────────
def tokens() -> list[str]:
    """Single-glyph ops, in walk order: prologue, RLE pixel stream, halt."""
    toks = ["8", "M"]  # B = 8 for the rest of the run; A = 8
    cur = 8
    for row in HEX_ROWS:
        for ch in row:
            color = int(ch, 16)
            if color != cur:
                if color <= 9:
                    toks.append(str(color))
                else:
                    # B is 8, so (color ^ 8) ~ reloads any high color.
                    toks.append(str(color ^ 8))
                    toks.append("~")
                cur = color
            toks.append("s")
    toks.append("H")
    return toks


# ── the boxes ────────────────────────────────────────────────────────────────
def _display(c: Circuit) -> None:
    for x in range(DISP_WX0, DISP_WX1 + 1):
        ch = "+" if x in (DISP_WX0, DISP_WX1) else "="
        c.set(x, DISP_WY0, ch)
        c.set(x, DISP_WY1, ch)
    for y in range(DISP_WY0 + 1, DISP_WY1):
        c.set(DISP_WX0, y, ":")
        c.set(DISP_WX1, y, ":")


def _relay(c: Circuit) -> None:
    walls(c, REL_WX0 + 1, REL_WY0 + 1, 7, 11)
    stamp(c, REL_WX0 + 1, REL_WY0 + 1, RELAY_ART)


def _rom(c: Circuit, toks: list[str], nrows: int) -> None:
    walls(c, TURN_L, ROM_WY0 + 1, ROM_IW, nrows)
    ncols = CONTENT_X1 - CONTENT_X0 + 1
    for r in range(nrows):
        y = ROM_WY0 + 1 + r
        chunk = toks[r * ncols : (r + 1) * ncols]
        if r % 2 == 0:  # east-going row
            c.set(TURN_L, y, "@" if r == 0 else ">")
            for i, t in enumerate(chunk):
                c.set(CONTENT_X0 + i, y, t)
            c.set(TURN_R, y, "v")
        else:  # west-going row: ops at decreasing x, read in walk order
            c.set(TURN_R, y, "<")
            for i, t in enumerate(chunk):
                c.set(CONTENT_X1 - i, y, t)
            c.set(TURN_L, y, "v")


def _pipes(c: Circuit) -> None:
    # A: ROM -> relay, exactly the 2-cell minimum.
    draw_pipe(c, [(PIPE_A_X, ROM_WY0 - 1), (PIPE_A_X, REL_WY1 + 1)])
    # B: relay -> display DATA (west wall), also 2 cells.
    draw_pipe(c, [(REL_WX1 + 1, PIPE_B_Y), (DISP_WX0 - 1, PIPE_B_Y)])
    # C: relay -> display SWAP (bottom wall), the long way round: north to
    # row 0, east over the display, down column EAST_COL (2 clear of its east
    # wall so no bend hugs it), west under its bottom wall. The terminal bend
    # into the wall is set by hand: draw_pipe ends straight runs with an
    # arrowhead along the flow, so its last cell stays a legal straight-through
    # '<' and the '^' at SWAP_COL is the real terminal.
    draw_pipe(
        c,
        [
            (PIPE_C_X, REL_WY0 - 1),
            (PIPE_C_X, 0),
            (EAST_COL, 0),
            (EAST_COL, DISP_WY1 + 1),
            (SWAP_COL + 1, DISP_WY1 + 1),
        ],
    )
    c.set(SWAP_COL, DISP_WY1 + 1, "^")


def build() -> list[str]:
    toks = tokens()
    ncols = CONTENT_X1 - CONTENT_X0 + 1
    nrows = -(-len(toks) // ncols)
    height = ROM_WY0 + nrows + 2
    c = Circuit(GRID_W, height)
    _display(c)
    _relay(c)
    _rom(c, toks, nrows)
    _pipes(c)
    return c.rows()


# ── debug sidecars ───────────────────────────────────────────────────────────
def debug_map() -> DebugMap:
    dbg = DebugMap("doom-screen - DOOM (1993) title screen on an LM-75")
    dbg.region(
        "display",
        DISP_WX0,
        DISP_WY0,
        DISP_IW + 2,
        DISP_IH + 2,
        note="LM-75, interior 32x24. DATA on the west wall, SWAP on the south.",
        color="#ef4444",
    )
    dbg.region(
        "relay",
        REL_WX0,
        REL_WY0,
        REL_WX1 - REL_WX0 + 1,
        REL_WY1 - REL_WY0 + 1,
        note="`768` b; counted loop r->s feeds DATA; exit 0->s commits via "
        "SWAP; 81-lap delay loop outlives the SWAP pipe before H.",
        color="#3b82f6",
    )
    toks = tokens()
    ncols = CONTENT_X1 - CONTENT_X0 + 1
    nrows = -(-len(toks) // ncols)
    dbg.region(
        "rom",
        0,
        ROM_WY0,
        ROM_IW + 2,
        nrows + 2,
        note="Boustrophedon glyph walk: run-length coded pixel stream, one s "
        "per pixel, color changes reload A (high colors via ~ with B=8).",
        color="#22c55e",
    )
    dbg.lane(
        "pipe A: ROM -> relay",
        [(PIPE_A_X, ROM_WY0 - 1), (PIPE_A_X, REL_WY1 + 1)],
        kind="pipe",
        note="768 colors, minimum-length pipe.",
    )
    dbg.lane(
        "pipe B: relay -> DATA",
        [(REL_WX1 + 1, PIPE_B_Y), (DISP_WX0 - 1, PIPE_B_Y)],
        kind="pipe",
        note="one color per loop lap; enters the display's west (DATA) wall.",
        color="#eab308",
    )
    dbg.lane(
        "pipe C: relay -> SWAP",
        [
            (PIPE_C_X, REL_WY0 - 1),
            (PIPE_C_X, 0),
            (EAST_COL, 0),
            (EAST_COL, DISP_WY1 + 1),
            (SWAP_COL, DISP_WY1 + 1),
        ],
        kind="pipe",
        note="deliberately ~112 cells so the commit trails the last pixel.",
        color="#a855f7",
    )
    return dbg


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--man", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    args = parser.parse_args(argv)

    rows = build()
    dbg = debug_map()
    for path in (args.man, args.html, args.json):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dbg.write_html(rows, args.html)
    dbg.write_json(args.json)
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["grid"]:
        print("\n".join(build()))
    else:
        raise SystemExit(main())
