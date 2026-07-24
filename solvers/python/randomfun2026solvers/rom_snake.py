"""Generate a snake-layout ``.man`` ROM that emits a fixed sequence of bytes.

For footprint-scored "just print this blob" problems (e.g. ``history-lesson``)
the program is pure data: one *load literal -> send* per output value. Written
as a single row that is one enormous line; this module folds it into a compact
**boustrophedon** (the man snakes east, drops a row, comes back west, and so on)
so there is no wasted whitespace.

The layout is chosen to be provably safe against the language's **vertical**
backtick-pairing rule (``SPEC.md`` -> "Fine print"): two backticks sharing a
column form a vertical literal, and a non-digit between them (or an over-long
digit run) is a *load* error. Snaking is the dangerous case because west-bound
rows mirror the cell, so a naive layout would stack an ``s`` between two
backticks. We avoid it with a **7-wide cell** ``` `DDD`s␠ ``` (one trailing pad):

* ``DDD`` is the value right-justified to 3 columns (spaces inside a
  ``` `...` ``` literal are ignored, so 2-3 digit values share one width).
* All cells align to the same 7-column grid. The pad makes the cell mirror
  cleanly, so in every column the two row directions contribute a
  backtick-vs-(digit/space) pair, never backtick-vs-``s``. Adjacent rows put at
  most one digit between vertical backticks, so nothing can overflow 64 bits.
* Turn glyphs (``@ > v < H``) live only in the two edge lanes (first/last
  interior column), which never hold a backtick.

Two ways to use the ROM:

* :func:`build` returns a complete standalone ``.man`` (room + pipe + ``O`` room).
* :func:`rom_container` returns a :class:`~randomfun2026solvers.layout.Container`
  so the ROM plugs into :func:`~randomfun2026solvers.layout.layout_graph`, which
  routes the output pipe to an :func:`output_room` for you. See
  :func:`string_rom_graph` for the ready-made wiring.

Verify any output with the bundled interpreter::

    node littleman/lm.mjs run tasks/solutions/history-lesson.man
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

from randomfun2026solvers.layout import Cell, Container, Edge, Graph

__all__ = [
    "CELL_W",
    "cell",
    "codes_of",
    "build",
    "rom_room",
    "rom_container",
    "output_room",
    "string_rom_graph",
    "packed_interior",
    "packed_room",
    "best_pack_width",
    "build_packed",
    "encode_string",
    "packed_rom_container",
]

# ` + three digit slots + ` + s + one pad space (pad makes west-bound mirror safe)
CELL_W = 7
_DIGITS = CELL_W - 4


def codes_of(data: str | bytes | Iterable[int]) -> list[int]:
    """Normalise ``data`` into a list of byte/codepoint values to emit.

    ``str`` is encoded as ASCII (littleman output is ASCII codes); ``bytes`` and
    any iterable of ints are taken verbatim.
    """
    if isinstance(data, str):
        return list(data.encode("ascii"))
    if isinstance(data, (bytes, bytearray)):
        return list(data)
    return [int(x) for x in data]


def cell(value: int) -> str:
    """One fixed-width ROM cell: load ``value`` into A, then send it."""
    digits = str(value)
    if not 0 <= value < 10**_DIGITS:
        raise ValueError(f"value {value} does not fit in {_DIGITS} digits")
    return "`" + digits.rjust(_DIGITS) + "`s "


def _interior(codes: list[int], cells_per_row: int) -> list[str]:
    """The bare boustrophedon rows (no room walls), each ``cells_per_row*7 + 2``
    wide: a turn lane, the data cells, another turn lane."""
    if not codes:
        raise ValueError("codes must be non-empty")
    if cells_per_row < 1:
        raise ValueError("cells_per_row must be >= 1")

    k = cells_per_row
    data_w = k * CELL_W
    rows_vals = [codes[i : i + k] for i in range(0, len(codes), k)]
    ndr = len(rows_vals)

    interior: list[str] = []
    for d, vals in enumerate(rows_vals):
        east = d % 2 == 0
        last = d == ndr - 1
        body = "".join(cell(v) for v in vals).ljust(data_w)
        if east:
            # walk east: entry lane on the left, exit (turn/halt) on the right
            left = "@" if d == 0 else ">"
            right = "H" if last else "v"
        else:
            # walk west: reverse the body, entry lane on the right, exit on left
            body = body[::-1]
            left = "H" if last else "v"
            right = "<"
        interior.append(left + body + right)
    return interior


def rom_room(codes: list[int], cells_per_row: int) -> list[str]:
    """The ROM as a self-contained rectangular room (``+``/``-``/``|`` walls).

    No pipe or output room — attach one via a pipe on any wall; the single ``s``
    per value sends to the (only) outgoing pipe.
    """
    interior = _interior(codes, cells_per_row)
    wi = cells_per_row * CELL_W + 2
    wall = "+" + "-" * wi + "+"
    return [wall, *("|" + r + "|" for r in interior), wall]


def _attach_output(room: list[str]) -> str:
    """Drop a 2-cell output pipe from the bottom wall of ``room`` into a 3x3
    ``O`` room and return the finished ``.man`` text."""
    room_w = len(room[0])
    cx = room_w // 2
    canvas = list(room)
    canvas.append((" " * cx) + "v")  # pipe cell 1 (backward = bottom wall)
    canvas.append((" " * cx) + "v")  # pipe cell 2 (forward = O top wall)
    canvas.append((" " * (cx - 1)) + "+-+")
    canvas.append((" " * (cx - 1)) + "|O|")
    canvas.append((" " * (cx - 1)) + "+-+")
    width = max(len(r) for r in canvas)
    return "\n".join(r.ljust(width) for r in canvas) + "\n"


def build(codes: list[int], cells_per_row: int = 20) -> str:
    """Return a standalone fixed-grid ``.man`` program that emits ``codes``.

    Wraps :func:`rom_room` and attaches an output room. ``cells_per_row`` trades
    width for height. See :func:`build_packed` for the smaller variable-width
    variant.
    """
    return _attach_output(rom_room(codes, cells_per_row))


def rom_container(
    container_id: str,
    data: str | bytes | Iterable[int],
    *,
    cells_per_row: int = 20,
) -> Container:
    """Build a :class:`Container` ROM that emits ``data`` (a string/bytes/codes).

    The container has a single output port on the middle of its **right** wall;
    wire it to an :func:`output_room`'s input via a :class:`Graph` and hand that
    to :func:`~randomfun2026solvers.layout.layout_graph`. Because the room has
    exactly one outgoing pipe, every ``s`` sends to it regardless of where the
    router attaches it.
    """
    room = rom_room(codes_of(data), cells_per_row)
    width = len(room[0])
    height = len(room)
    port: Cell = (width - 1, height // 2)  # right wall, vertically centred
    return Container(
        id=container_id,
        width=width,
        height=height,
        content=room,
        inputs=[],
        outputs=[port],
    )


def output_room(container_id: str = "O") -> Container:
    """A 3x3 littleman output room ``O`` with one input port on its left wall."""
    return Container(
        id=container_id,
        width=3,
        height=3,
        content=["+-+", "|O|", "+-+"],
        inputs=[(0, 1)],
        outputs=[],
    )


def string_rom_graph(
    data: str | bytes | Iterable[int],
    *,
    cells_per_row: int = 20,
    rom_id: str = "rom",
    out_id: str = "O",
) -> Graph:
    """A ready-to-lay-out :class:`Graph`: a ROM emitting ``data`` wired to ``O``.

    Example::

        from randomfun2026solvers.layout import layout_graph
        from randomfun2026solvers.rom_snake import string_rom_graph

        man = layout_graph(string_rom_graph("hi")).render()
    """
    return Graph(
        containers=[
            rom_container(rom_id, data, cells_per_row=cells_per_row),
            output_room(out_id),
        ],
        edges=[Edge(id="out", src=rom_id, src_output=0, dst=out_id, dst_input=0)],
    )


# ── variable-width "safe packer" ──────────────────────────────────────────────
# The fixed grid wastes a column on every 2-digit value plus a pad column. This
# packer emits tight ``` `N`s ``` tokens (no digit padding) and inserts a pad
# space *only* where a placement would drop an ``s`` inside a vertical backtick
# pair. Safety is a top-down invariant, so the result never hits a load error:
#
#   * an ``s`` is placed only in a column whose backtick count so far is EVEN —
#     any later backticks (below it) then pair among themselves *below* the s,
#     so it can never end up between a pair;
#   * a *closing* backtick (one landing where the column parity is odd) is placed
#     only if no ``s`` — and no >18-digit run — sits between it and its opener.


def packed_interior(codes: list[int], data_w: int) -> list[str]:
    """Boustrophedon rows, tightly packed and rule-safe, each ``data_w + 2`` wide
    (two turn lanes around ``data_w`` data columns)."""
    if not codes:
        raise ValueError("codes must be non-empty")
    if data_w < 6:
        raise ValueError("data_w must be >= 6 (smallest ` N ` s token)")

    parity = [0] * data_w
    bad = [False] * data_w   # non-digit seen since this column's open backtick
    dig = [0] * data_w       # digit run length since this column's open backtick

    def feasible(col: int, g: str) -> bool:
        if g == "s":
            return parity[col] % 2 == 0
        if g == "`":
            return parity[col] % 2 == 0 or (not bad[col] and dig[col] <= 18)
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

    rows: list[tuple[str, bool]] = []
    i = 0
    d = 0
    while i < len(codes):
        east = d % 2 == 0
        cells = [" "] * data_w
        placed = False
        cur = 0 if east else data_w - 1
        while i < len(codes):
            tok = f"`{codes[i]}`s"
            glyphs = tok if east else tok[::-1]  # west rows read right-to-left
            length = len(glyphs)
            start = cur if east else cur - length + 1
            # slide the whole token until every column is feasible
            while 0 <= start and start + length <= data_w and not all(
                feasible(start + j, glyphs[j]) for j in range(length)
            ):
                start += 1 if east else -1
            if not (0 <= start and start + length <= data_w):
                break  # no safe spot left in this row → next row
            for j, g in enumerate(glyphs):
                commit(start + j, g, cells)
            cur = start + length if east else start - 1
            i += 1
            placed = True
        if not placed:
            raise ValueError(f"data_w={data_w} too small to place value {codes[i]}")
        last = i >= len(codes)
        body = "".join(cells)
        if east:
            rows.append(("@" + body if d == 0 else ">" + body, True))
            rows[-1] = (rows[-1][0] + ("H" if last else "v"), True)
        else:
            rows.append((("H" if last else "v") + body + "<", False))
        d += 1
    return [r for r, _ in rows]


def packed_room(codes: list[int], data_w: int) -> list[str]:
    """The tightly-packed ROM as a self-contained rectangular room."""
    interior = packed_interior(codes, data_w)
    wall = "+" + "-" * (data_w + 2) + "+"
    return [wall, *("|" + r + "|" for r in interior), wall]


def best_pack_width(codes: list[int], lo: int = 40, hi: int = 260) -> int:
    """Search ``data_w`` in ``[lo, hi]`` for the one giving the smallest program
    (footprint is the score). Every width is load-safe, so this only minimises
    size."""
    best: tuple[int, int] | None = None
    for data_w in range(lo, hi + 1):
        try:
            size = len(_attach_output(packed_room(codes, data_w)))
        except ValueError:
            continue
        if best is None or size < best[0]:
            best = (size, data_w)
    if best is None:
        raise ValueError("no feasible width in range")
    return best[1]


def build_packed(codes: list[int], data_w: int | None = None) -> str:
    """Return a standalone tightly-packed ``.man`` (smaller than :func:`build`).

    With ``data_w=None`` the width that minimises program size is searched for.
    """
    if data_w is None:
        data_w = best_pack_width(codes)
    return _attach_output(packed_room(codes, data_w))


def encode_string(data: str | bytes | Iterable[int], *, data_w: int | None = None) -> str:
    """Encode a constant ``data`` (string/bytes/codes) into a complete standalone
    ``.man`` program that outputs it, tightly packed (auto width if ``data_w`` is
    ``None``). The one-call ``string -> .man`` entry point.

    Example::

        open("hello.man", "w").write(encode_string("hello world"))
    """
    return build_packed(codes_of(data), data_w)


def packed_rom_container(
    container_id: str,
    data: str | bytes | Iterable[int],
    *,
    data_w: int | None = None,
) -> Container:
    """Like :func:`rom_container` but using the tight :func:`packed_room`."""
    codes = codes_of(data)
    if data_w is None:
        data_w = best_pack_width(codes)
    room = packed_room(codes, data_w)
    width = len(room[0])
    height = len(room)
    return Container(
        id=container_id,
        width=width,
        height=height,
        content=room,
        inputs=[],
        outputs=[(width - 1, height // 2)],
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: read whitespace-separated integer codes from stdin, write ``.man``.

    Defaults to the tight packer with an auto-searched width. Pass an integer to
    force ``data_w`` (or ``fixed N`` to use the fixed-grid builder instead)::

        python -m randomfun2026solvers.rom_snake \\
          < codes.txt > tasks/solutions/history-lesson.man
    """
    args = sys.argv[1:] if argv is None else argv
    codes = [int(tok) for tok in sys.stdin.read().split()]
    if args and args[0] == "fixed":
        sys.stdout.write(build(codes, int(args[1]) if len(args) > 1 else 20))
    else:
        sys.stdout.write(build_packed(codes, int(args[0]) if args else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
