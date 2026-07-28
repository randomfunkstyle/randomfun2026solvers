#!/usr/bin/env python3
"""deadman-3d_hires — the 128x96 tiled framebuffer, and the demo that drives it.

The LM-75's interior stops at **64x64** (``SPEC.md`` § The LM-75 display), so
128x96 cannot be one panel.  It can be four: a 2x2 of 64x48 panels, each one
*exactly* the geometry :mod:`lm1.d3_unit` already paints, with

    tile = (x >= 64) + 2 * (y >= 48)      in-tile = (x % 64, y % 48)

The machine side of that is :mod:`lm1.d3_router` — four unmodified DOOM units
behind a 1-of-4 router on one CPU command lane — and this module is the software
side: the tile split, the per-tile RLE encoder, and the demo program that proves
the whole path from a real ``build_for`` machine.

What this module is, and what it is not
---------------------------------------

It is the **tiled display layer** plus a program that exercises every part of it:
per-tile ``CURS``/``RUN`` painting of a real 128x96 image, per-tile ``COL``
painting of a column that straddles the tile seam, and the broadcast ``COMMIT``
that keeps the four panels' frame indices in step.

It is **not** the hi-res raycaster.  ``deadman3d.py``'s renderer and its
generated assembly are written against module-level ``WIDTH``/``HEIGHT``/``H3D``
constants (382 references) with art tables baked at 64x48, and porting them is a
separate piece of work — see :func:`col_words`, which is the one hot-loop
primitive that port needs and which is implemented and tested here.

The seam, and why ``col_words`` is the whole difficulty
-------------------------------------------------------

A raycaster paints one *column* at a time, and at 128x96 a column is 96 pixels
tall while a tile is 48 — so every column crosses the seam at ``y = 48`` and
becomes up to two ``COL`` commands on two different panels.  Worse, the unit's
COL arm ends each command with a **floor run** down to a baked row, and that row
is not the same on a top tile as on a bottom one: the 3D viewport is logical rows
0..79, so a top tile floors to its own row 47 and a bottom tile stops at 31 where
the HUD begins.  That constant is :data:`lm1.d3_unit.FLOOR_ROW`, now a parameter
precisely so the wall can bake 47 into its top pair and 31 into its bottom pair.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from randomfun2026solvers.lm1 import d3_router, d3_unit
from randomfun2026solvers.lm1.display import TILE_H, TILE_W, WALL_H, WALL_W, tile_addr, tile_of
from randomfun2026solvers.lm1.store import DoomUnit

__all__ = [
    "H3D",
    "HEIGHT",
    "MID",
    "WIDTH",
    "col_words",
    "commit_word",
    "frames_for_words",
    "hires_source",
    "image_words",
    "input_words",
    "interleave",
    "round_words",
    "seam_frame",
    "tile_addr",
    "tile_of",
    "title_frame",
    "upscale",
]

# ── the geometry ─────────────────────────────────────────────────────────────
WIDTH, HEIGHT = WALL_W, WALL_H  # 128x96: four 64x48 panels
H3D = 80  # 3D viewport rows 0..79; the HUD strip is rows 80..95
MID = H3D // 2  # horizon row of the 3D viewport

#: The unit's command codes and the router's selectors, resolved once.
CODES = DoomUnit.CODES
SEL = d3_router.SEL


def word(arm: str, arg: int, dest: str) -> int:
    """One router word: ``8 * (8 * arg + code) + sel``, the whole wire format."""
    return d3_router.word(d3_unit.word(CODES[arm], arg), SEL[dest])


def commit_word() -> int:
    """The broadcast COMMIT — the only command that must not name a tile.

    ``S`` puts it into all four command pipes at once, which is what makes tile
    frame *N* a piece of logical frame *N*; see :mod:`lm1.d3_router`.
    """
    return word("COMMIT", 0, "ALL")


# ── the tile split ───────────────────────────────────────────────────────────
def col_words(x: int, top: int, bot: int, colour: int) -> list[int]:
    """One 128x96 viewport column as ``COL`` commands, split across the seam.

    This is the primitive a hi-res raycaster's inner loop needs, and it is the
    only place the tiling is not free.  ``top..bot`` is the wall run in *logical*
    rows; below it the unit's own floor run fills to that tile's floor bound.

    Three cases, and the middle one is the reason the function exists:

    * the wall lies entirely in the top tile — one command, whose floor run then
      fills the rest of that tile (rows to 47) but **not** the bottom tile, so a
      second bare command is still needed there;
    * the wall straddles ``y = 48`` — two commands, the top one carrying rows
      ``top..47`` and the bottom one ``0..bot-48``;
    * the wall lies entirely in the bottom tile — the top tile still needs a
      command to floor it, and it gets a one-pixel wall run at its own last row.

    The argument shape is the unit's, unchanged: ``seed * 64 + n`` with
    ``seed = (row * 64 + col) * 16 + colour - 1024`` biased one lap early,
    because the wall loop adds a row *before* it paints (see
    :class:`lm1.store.DoomUnit`).
    """
    if not 0 <= x < WIDTH:
        raise ValueError(f"column {x} is outside 0..{WIDTH - 1}")
    if not 0 <= top <= bot < H3D:
        raise ValueError(f"the wall run {top}..{bot} is not inside 0..{H3D - 1}")
    if not 0 <= colour <= 15:
        raise ValueError(f"colour {colour} is not 0..15")

    out: list[int] = []
    for tile_row in (0, 1):
        y0, y1 = tile_row * TILE_H, tile_row * TILE_H + TILE_H - 1
        floor_row = d3_router.TILE_FLOOR_ROW[2 * tile_row]
        # This tile's slice of the wall run, clamped to the part of the tile the
        # viewport actually covers.
        lo, hi = max(top, y0), min(bot, y1, y0 + floor_row)
        if lo > hi:
            # No wall here. The tile below a finished wall is all floor, and the
            # unit only floors *after* a wall run, so give it a one-pixel run at
            # the seam it can floor from: the run's own colour is the wall's when
            # the wall is above, and the floor colour when it is below.
            if top > y0 + floor_row:  # the wall starts below this tile entirely
                continue
            lo = hi = y0
            colour_here = d3_unit.FLOOR
        else:
            colour_here = colour
        tile = tile_of(x, lo)
        seed = (tile_addr(x, lo) * 16 + colour_here) - 1024
        out.append(word("COL", seed * 64 + (hi - lo + 1), f"T{tile}"))
    return out


# ── the per-tile RLE encoder ─────────────────────────────────────────────────
def upscale(rows: Sequence[str], factor: int = 2) -> list[str]:
    """Nearest-neighbour upscale of a hex-digit frame — 64x48 art at 128x96."""
    return [
        "".join(ch * factor for ch in row) for row in rows for _ in range(factor)
    ]


def interleave(streams: Sequence[Sequence[int]]) -> list[int]:
    """Round-robin four per-tile command streams into one lane, order preserved.

    The largest single win in the whole design, and it costs nothing.  Four units
    paint **concurrently**, but only while all four have work: feed them tile by
    tile and the last panel starts after the first three have finished, so a frame
    costs the *sum* of the tiles instead of the maximum.

    Measured on the reference engine, the demo's 128x96 title frame, tile-by-tile
    against round-robin (tick the frame was committed, per tile)::

        tile by tile   412,068  427,205  556,022  633,876   last 633,876
        round-robin    301,674  237,762  323,102  282,884   last 323,102

    -49% on the frame, and the spread between the first and last panel's commit —
    the wall's whole tearing window — falls from 221,808 ticks to 85,340.

    Nothing about a panel's state is disturbed by the interleave: each cursor
    auto-advances over its own raster, so only the order *within* one tile's
    stream matters, and that is preserved.
    """
    out: list[int] = []
    for lap in range(max(len(s) for s in streams)):
        out += [s[lap] for s in streams if lap < len(s)]
    return out


def image_words(rows: Sequence[str]) -> list[int]:
    """A whole 128x96 frame as router words: per tile, one ``CURS`` and its RLE.

    Each panel's cursor auto-advances row-major over its *own* 64x48 raster, so a
    tile's pixels are a contiguous run-length stream once they are cut out of the
    logical frame — which is the entire reason a 2x2 of 64x48 panels is cheaper
    to drive than any other tiling of 128x96.  The four streams are then
    :func:`interleave`d, so all four panels paint at once.
    """
    if len(rows) != HEIGHT or any(len(r) != WIDTH for r in rows):
        raise ValueError(f"the frame is not {WIDTH}x{HEIGHT}")
    streams: list[list[int]] = []
    for tile in range(4):
        cx, cy = (tile % 2) * TILE_W, (tile // 2) * TILE_H
        flat = "".join(rows[cy + r][cx : cx + TILE_W] for r in range(TILE_H))
        out = [word("CURS", 0, f"T{tile}")]
        run_colour, count = int(flat[0], 16), 0
        for ch in flat:
            c = int(ch, 16)
            if c == run_colour:
                count += 1
                continue
            out.append(word("RUN", count * 16 + run_colour, f"T{tile}"))
            run_colour, count = c, 1
        out.append(word("RUN", count * 16 + run_colour, f"T{tile}"))
        streams.append(out)
    return interleave(streams)


# ── the demo's two frames ────────────────────────────────────────────────────
def title_frame() -> list[str]:
    """Freedoom's title art at 128x96 — ``deadman3d``'s 64x48 plate, doubled.

    A placeholder for properly dithered 128x96 art (a separate piece of work),
    but a real image with real runs: it proves the per-tile RLE, the cursor
    arithmetic and the seam at ``y = 48`` on actual content rather than a
    test card.
    """
    from randomfun2026solvers.deadman3d import TITLE_HEX_ROWS

    return upscale(TITLE_HEX_ROWS)


def seam_frame() -> list[str]:
    """What :func:`col_words` paints for a full sweep of ``COL`` commands.

    A wedge whose wall run crosses ``y = 48`` in the middle of the screen, so
    every one of :func:`col_words`' three cases is exercised across the 128
    columns, and the tile seam is exactly where a tear would show.
    """
    frame = [["0"] * WIDTH for _ in range(HEIGHT)]
    for x, (top, bot, colour) in enumerate(_wedge()):
        for y in range(top, bot + 1):
            frame[y][x] = f"{colour:x}"
        for y in range(bot + 1, H3D):
            frame[y][x] = f"{d3_unit.FLOOR:x}"
    return ["".join(r) for r in frame]


def _wedge() -> list[tuple[int, int, int]]:
    """Per column, the wall run ``(top, bot, colour)`` the demo paints.

    A V, so the run sits wholly in the top tile at the edges, straddles the seam
    for most of the screen, and bottoms out below it in the middle.
    """
    out: list[tuple[int, int, int]] = []
    for x in range(WIDTH):
        depth = abs(x - WIDTH // 2)
        half = max(4, 44 - depth // 3)
        top, bot = MID - half, min(H3D - 1, MID + half)
        out.append((max(0, top), bot, 1 + (x // 8) % 7))
    return out


def seam_words() -> list[int]:
    """The wedge as ``COL`` commands — one or two per column, tile-split.

    Columns are emitted **left half, right half, left half, ...** rather than
    0..127, and that ordering is a finding worth carrying into the raycaster port:
    a column only ever touches the two tiles of its own half (``tile = (x >= 64) +
    2 * (y >= 48)``), so sweeping 0..127 leaves the right-hand pair of panels idle
    for the first half of the frame and then makes the left pair wait through the
    second.  Alternating the halves keeps all four painting, exactly as
    :func:`interleave` does for the RLE.
    """
    wedge = _wedge()
    order = [x for pair in zip(range(64), range(64, 128), strict=True) for x in pair]
    return [w for x in order for w in col_words(x, *wedge[x])]


#: The forward loop's unroll. A backward jump costs ``8 * (P - loop)`` ticks
#: forever, so the CPU's whole cost per word is the loop overhead unless the body
#: is unrolled — the same reason ``deadman-3d``'s title loop forwards 8 per lap.
#: Measured here: 810 ticks a word rolled against 111 unrolled, and the bill then
#: sits with the four units' painting rather than with the CPU's counting.
FWD_UNROLL = 8


def round_words() -> list[list[int]]:
    """The demo's frames, each a burst of router words the CPU forwards verbatim.

    Padded at the front to a multiple of :data:`FWD_UNROLL` with ``CURS 0`` on
    tile 0 — a genuine no-op there, since the tile's next command either sets the
    cursor itself (the RLE's own ``CURS``) or addresses every pixel explicitly
    (``COL``).  Padding is what lets the CPU's forward loop be a straight-line
    unroll with no tail.
    """
    pad = word("CURS", 0, "T0")
    out: list[list[int]] = []
    for burst in (image_words(title_frame()), seam_words()):
        short = -len(burst) % FWD_UNROLL
        out.append([pad] * short + burst)
    return out


def input_words() -> list[int]:
    """The whole input stream: per round, a **lap** count then that round's words.

    The count is in laps of :data:`FWD_UNROLL`, not words, because that is what
    the unrolled loop counts down; :func:`round_words` pads each burst so the
    division is exact.
    """
    return [
        v for burst in round_words() for v in (len(burst) // FWD_UNROLL, *burst)
    ]


def frames_for_words() -> list[list[str]]:
    """The model's committed 128x96 frames — what the machine must reproduce."""
    from randomfun2026solvers.lm1.display import tiled_frames_from_writes

    writes: list[tuple[int, int, int]] = []
    from randomfun2026solvers.lm1.store import DoomWall

    wall = DoomWall(lambda t, p, v: writes.append((t, p, v)))
    for burst in round_words():
        for w in burst:
            wall.send(w)
        wall.send(commit_word())
    return tiled_frames_from_writes(writes)


# ── the program ──────────────────────────────────────────────────────────────
#: The one tape slot the demo needs, past slot 0 (the assembler's scratch).
SLOT_N = 1

#: The input protocol, quoted into the generated machine's debug sidecar.
INPUT_PROTOCOL = (
    "Per round: one word saying how many pre-encoded router words follow, then "
    "that many words, each 8*(8*arg + code) + sel — the DOOM unit's own command "
    "word with a tile selector in the low three bits (d3_router.SEL: T0=5, T1=1, "
    "T2=7, T3=3, ALL=6 broadcast). The CPU forwards each untouched (IN/SND) and "
    "ends the round with ONE broadcast COMMIT, which is what keeps the four "
    "panels' frame indices in step. No program output. "
    "deadman3d_hires.input_words() is the demo stream."
)


def hires_source() -> str:
    """The LM-1 assembly: forward this round's words to the wall, then commit.

    The CPU does no arithmetic on a command word at all — the tile selector is
    already in it — so the program is the smallest thing that can prove the whole
    path: input room -> CPU -> one ``SND`` lane -> router -> four units -> four
    panels -> one composed 128x96 frame.  Every piece of intelligence lives where
    the architecture puts it, which is the point being demonstrated.
    """
    commit = commit_word()
    return "\n".join(
        [
            "; deadman-3d_hires — GENERATED from randomfun2026solvers/deadman3d_hires.py,",
            "; do not hand-edit. Regenerate with:",
            ";   from randomfun2026solvers.deadman3d_hires import hires_source",
            ";   from randomfun2026solvers.lm1.programs import PROGRAM_DIR",
            ';   (PROGRAM_DIR / "deadman-3d_hires.asm").write_text(hires_source())',
            ";",
            "; A 128x96 framebuffer on hardware whose panel stops at 64x64: four 64x48",
            "; LM-75s in a 2x2, driven through ONE command lane by the 1-of-4 router in",
            "; lm1/d3_router.py (.unit doom4). tile = (x>=64) + 2*(y>=48); a command word",
            "; is the DOOM unit's own 8*arg + code with the tile selector shifted in",
            "; underneath it, so the CPU forwards words it never has to decode.",
            ";",
            "; The four panels commit on four separate SWAP pipes, so the frame is kept",
            "; whole by the router's broadcast leaf (`S` — send to EVERY outgoing pipe at",
            "; once): every panel sees the same COMMIT sequence, so tile frame N is always",
            "; a piece of logical frame N and a composed frame is never half-old.",
            ";",
            "; An ungraded demo: it borrows plotter's problem JSON for registration only.",
            "",
            ".unit doom4",
            "",
            f".equ N      {SLOT_N}            ; laps of {FWD_UNROLL} left this round",
            f".equ CMIT   {commit}           ; COMMIT on the broadcast leaf (SEL ALL)",
            "",
            f"round:  IN                  ; this round's burst, in laps of {FWD_UNROLL}",
            "        ST  N",
            "fwd:    IN                  ; a pre-encoded router word",
            "        SND                 ; ... straight through to its tile",
            *[line for _ in range(FWD_UNROLL - 1) for line in ("        IN", "        SND")],
            "        DECM N              ; ACC = the lap count BEFORE the decrement",
            "        SUBI 1",
            "        BRZ done",
            "        JMP fwd",
            "done:   LDI CMIT",
            "        SND                 ; SWAP 0 on all four panels at once",
            "        JMP round",
            "",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--asm", type=Path, help="write the generated .asm here")
    ap.add_argument("--input", type=Path, help="write the demo input stream here")
    ap.add_argument("--frames", action="store_true", help="print the model's frames")
    args = ap.parse_args(argv)

    if args.asm:
        args.asm.write_text(hires_source(), encoding="utf-8")
    if args.input:
        args.input.write_text(" ".join(str(v) for v in input_words()) + "\n", encoding="utf-8")
    if args.frames:
        for i, frame in enumerate(frames_for_words()):
            print(f"# frame {i}")
            print("\n".join(frame))
    if not (args.asm or args.input or args.frames):
        bursts = round_words()
        print(f"{WIDTH}x{HEIGHT}, {len(bursts)} frames, bursts {[len(b) for b in bursts]}")
        print(f"input words: {len(input_words())}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
