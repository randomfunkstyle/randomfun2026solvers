"""base-128 "encode a big number, decode at runtime" ROM (history-lesson v2).

Where :mod:`rom_snake` spends one ``` `NNN`s ``` per output byte, this packs
**6 bytes per 64-bit word** (base 128, 3 decimal digits each, least-significant
byte first) and decodes them at runtime, so the expensive ``` `...`s ``` framing
is amortised ~6x. For history-lesson this is ~11.3 KB vs ~18.8 KB.

Three rooms, wired by pipes (via :func:`~randomfun2026solvers.layout.layout_graph`):

* **encoder** — a packed ROM that ``s``-sends each word literal into a pipe, then
  a negative **sentinel** (``1 N s`` -> emits -1) to signal end-of-stream;
* **decoder** — a one-``X`` loop: read a word, then repeatedly ``/ 1000`` (one
  ``/`` yields the quotient in A *and* the byte remainder in B), send the byte,
  until the word is exhausted (A hits 0 -> read next word); a negative word
  halts it;
* **output** — the ``O`` room.

Because 64-bit is the ceiling (a value must fit in 64 bits or it fails to load),
a *single* giant number is impossible — 6 bytes (<=18 digits) is the safe chunk.

Verify::

    node littleman/lm.mjs run tasks/solutions/history-lesson_base1000.man
"""

from __future__ import annotations

import sys

from randomfun2026solvers.layout import Container
from randomfun2026solvers.rom_snake import codes_of

__all__ = [
    "CHARS_PER_WORD",
    "WORD_BASE",
    "to_words",
    "pack_tokens",
    "decoder_container",
    "encoder_container",
    "build",
    "footprint",
    "best_encoder_width",
]

WORD_BASE = 128           # 7 bits/char, 9 chars = 63 bits <= signed-64
CHARS_PER_WORD = 8        # 8 * 7 = 56 bits -> 17-digit words, reverse-safe


def to_words(codes: list[int]) -> list[int]:
    """Group ``codes`` into base-128 words, least-significant byte first."""
    words: list[int] = []
    for i in range(0, len(codes), CHARS_PER_WORD):
        w = 0
        for j, c in enumerate(codes[i : i + CHARS_PER_WORD]):
            w += c * (WORD_BASE**j)
        words.append(w)
    return words


def pack_tokens(tokens: list[str], data_w: int) -> list[str]:
    """Boustrophedon-pack arbitrary token strings into interior rows (``data_w``
    data columns + 2 turn lanes), padding only where a placement would trap a
    non-digit between vertical backticks.

    Same top-down parity invariant as :func:`rom_snake.packed_interior`, but over
    explicit token strings (a word literal ``` `N`s ```, or the ``1Ns`` sentinel).
    """
    parity = [0] * data_w
    bad = [False] * data_w
    dig = [0] * data_w

    def feasible(col: int, g: str) -> bool:
        if g == "`":
            return parity[col] % 2 == 0 or (not bad[col] and dig[col] <= 18)
        if not (g.isdigit() or g == " "):
            return parity[col] % 2 == 0  # s / N / ... only in an even-parity column
        return True

    def commit(col: int, g: str, cells: list[str]) -> None:
        cells[col] = g
        if g == "`":
            parity[col] += 1
            bad[col] = False
            dig[col] = 0
        elif parity[col] % 2 == 1:
            if g.isdigit():
                dig[col] += 1
            elif g != " ":
                bad[col] = True

    rows: list[str] = []
    i = 0
    d = 0
    while i < len(tokens):
        east = d % 2 == 0
        cells = [" "] * data_w
        placed = False
        cur = 0 if east else data_w - 1
        while i < len(tokens):
            glyphs = tokens[i] if east else tokens[i][::-1]
            length = len(glyphs)
            start = cur if east else cur - length + 1
            while 0 <= start and start + length <= data_w and not all(
                feasible(start + j, glyphs[j]) for j in range(length)
            ):
                start += 1 if east else -1
            if not (0 <= start and start + length <= data_w):
                break
            for j, g in enumerate(glyphs):
                commit(start + j, g, cells)
            cur = start + length if east else start - 1
            i += 1
            placed = True
        if not placed:
            raise ValueError(f"data_w={data_w} too small for token {tokens[i]!r}")
        last = i >= len(tokens)
        body = "".join(cells)
        if east:
            rows.append(("@" + body if d == 0 else ">" + body) + ("H" if last else "v"))
        else:
            rows.append(("H" if last else "v") + body + "<")
        d += 1
    return rows


def encoder_container(
    words: list[int], *, container_id: str = "enc", data_w: int = 160
) -> Container:
    """A packed ROM that sends each word then a negative sentinel, output on the
    right wall."""
    tokens = [f"`{w}`s" for w in words] + ["1Ns"]  # words, then emit -1
    interior = pack_tokens(tokens, data_w)
    wall = "+" + "-" * (data_w + 2) + "+"
    room = [wall, *("|" + r + "|" for r in interior), wall]
    w, h = len(room[0]), len(room)
    return Container(
        id=container_id, width=w, height=h, content=room,
        inputs=[], outputs=[(w - 1, h // 2)],
    )


def decoder_container(container_id: str = "dec") -> Container:
    """The base-128 decode loop. One ``X`` branches on the running value A:
    ``>0`` extract a byte, ``==0`` read the next word, ``<0`` halt (sentinel).
    Input pipe on the left wall, output pipe on the right wall."""
    g: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, ch: str) -> None:
        g[(x, y)] = ch

    x = 3  # X column
    put(1, 3, "@")            # spawn on the approach lane, heading east
    put(2, 3, ">")            # return path merges here (heads east into X)
    put(x, 3, "X")            # branch on A
    put(x, 2, "H")            # A<0 (CCW/north) -> halt on sentinel
    put(x + 1, 3, "r")        # A==0 (straight/east) -> read next word
    # read-return: turn down at x+2, west on row 17, up column 2
    put(x + 2, 3, "v")
    put(x + 2, 17, "<")
    for xx in range(3, x + 2):
        put(xx, 17, " ")
    put(2, 17, "^")
    # extract body: A>0 (CW/south) from (x,4) down
    body = "M`128`W/WsW"     # M; load 1000; A/1000 -> quotient A, byte B; send byte
    for i, ch in enumerate(body):
        put(x, 4 + i, ch)
    put(x, 4 + len(body), "<")            # turn west
    for xx in range(3, x):
        put(xx, 4 + len(body), " ")
    put(2, 4 + len(body), "^")            # up column 2 back to the approach lane

    width = max(px for px, _ in g) + 1
    height = max(py for _, py in g) + 1
    rows = ["".join(g.get((px, py), " ") for px in range(width)) for py in range(height)]
    wall = "+" + "-" * width + "+"
    room = [wall, *("|" + r + "|" for r in rows), wall]
    w, h = len(room[0]), len(room)
    return Container(
        id=container_id, width=w, height=h, content=room,
        inputs=[(0, h // 2)], outputs=[(w - 1, h // 2)],
    )


def _assemble(words: list[int], data_w: int) -> str:
    """Place encoder ``>>`` decoder horizontally, then drop ``O`` *below* the
    decoder with a ``vv`` pipe — no A* routing or gaps.

    The decoder (short) is centred against the tall encoder; the input pipe is a
    straight ``>>`` on its mid row, and the output pipe goes straight *down* into
    an ``O`` room tucked below the decoder. Because the decoder is far shorter
    than the encoder, that ``O`` fits inside the encoder's height envelope, so it
    adds neither width nor height. Each room has a single in/out pipe, so
    ``s``/``r`` resolve regardless of attach position.
    """
    enc = encoder_container(words, data_w=data_w).content
    dec = decoder_container().content
    o = ["+-+", "|O|", "+-+"]
    we, he = len(enc[0]), len(enc)
    wd, hd = len(dec[0]), len(dec)

    yd = (he - hd) // 2           # centre the decoder against the encoder
    prow = yd + hd // 2           # decoder's input port row (straight >> in)
    xd = we + 2                   # encoder, 2-cell pipe, decoder
    cxo = xd + wd // 2            # O column, under the decoder's centre

    width = max(xd + wd, cxo + 2)
    height = max(he, yd + hd + 2 + 3)   # decoder bottom + vv pipe + O (3 tall)
    canvas = [[" "] * width for _ in range(height)]

    def blit(room: list[str], ox: int, oy: int) -> None:
        for j, row in enumerate(room):
            for i, ch in enumerate(row):
                canvas[oy + j][ox + i] = ch

    blit(enc, 0, 0)
    blit(dec, xd, yd)
    blit(o, cxo - 1, yd + hd + 2)
    canvas[prow][we] = canvas[prow][we + 1] = ">"           # encoder -> decoder
    canvas[yd + hd][cxo] = canvas[yd + hd + 1][cxo] = "v"   # decoder -> O (down)
    return "\n".join("".join(r).rstrip() for r in canvas)


def footprint(words: list[int], data_w: int) -> tuple[int, int, int]:
    """Return ``(width, height, max(width, height))`` of the whole program.
    ``footprint`` scoring is ``max(width, height)**2`` (GRADING.md), so
    ``max(width, height)`` is the number to minimise."""
    lines = _assemble(words, data_w).split("\n")
    w = max(len(line) for line in lines)
    h = len(lines)
    return w, h, max(w, h)


def best_encoder_width(words: list[int], lo: int = 50, hi: int = 220) -> int:
    """Full fine scan for the encoder ``data_w`` minimising the footprint metric
    ``max(width, height)`` of the entire program (encoder + decoder + ``O``).

    An exhaustive scan (not binary/ternary search): total size vs width is a
    sawtooth from how word tokens tile into rows, so a divide-and-conquer search
    would settle in a local dip and miss the global minimum.
    """
    best: tuple[int, int] | None = None
    for dw in range(lo, hi + 1):
        try:
            _, _, m = footprint(words, dw)
        except ValueError:
            continue
        if best is None or m < best[0]:
            best = (m, dw)
    if best is None:
        raise ValueError("no feasible encoder width")
    return best[1]


def build(data: str | bytes | list[int], *, encoder_width: int | None = None) -> str:
    """Return a complete ``.man`` that outputs ``data`` via base-128 decode."""
    words = to_words(codes_of(data))
    if encoder_width is None:
        encoder_width = best_encoder_width(words)
    return _assemble(words, encoder_width) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI: read whitespace-separated codes from stdin, write the ``.man``."""
    args = sys.argv[1:] if argv is None else argv
    codes = [int(tok) for tok in sys.stdin.read().split()]
    width = int(args[0]) if args else None
    sys.stdout.write(build(codes, encoder_width=width))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
