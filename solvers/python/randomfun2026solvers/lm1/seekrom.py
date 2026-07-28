"""The seek-drum: a looping packed ROM whose man can jump to any data row.

The drum (``rom.build_packed_rom``'s packing) stays the sequential supply —
~3.3 cells a word — and gains a random-access *seek*: a **jump-request pipe**
from the CPU, a ``q``/``d`` (or ``q``/``a``) gadget at every data-row start,
and two pitch-2 ladders. A taken backward jump then costs notice (< one row) +
seek (~3 t/row) + the corridor flush instead of ``8 x (P - L)`` recirculation.

Protocol (all elements engine-proven by the Stage-2 RAM work):

* The CPU sends one word, ``row * K + rem`` (:data:`SEEK_K`), down the request
  pipe — the ROM room's only incoming pipe, so every ``q``/``r`` here binds it
  unambiguously.
* Every row transition passes a gadget (``q`` then ``d``/``a``): no request
  pending -> straight into the row (2 extra cells a row). Request pending ->
  the man is diverted into the **cascade**: he zigzags down the gadget cells
  themselves (a ``d`` met heading south turns him west, the turn cell sends
  him back east into the next ``d``, which sends him south again) to a bottom
  collector row, west to the seek riser, and up to the **station**.
* The station builds ``K = 128`` in B (digits only — no backticks), receives
  the request, ``/`` (``row`` in A, ``rem`` in B), ``b`` (BP = row), emits the
  sentinel ``-1`` and then ``rem`` into the fetch corridor, and splits on
  ``row``'s parity with ``x``: even rows are entered down the west ladder,
  odd down the east. ``]`` halves BP into the rung count.
* A ladder rung (pitch 2, three columns) is ``d`` entered heading east
  (mirrored ``a`` heading west): BP == 0 exits through the row's own gadget —
  whose ``q`` now reads 0, the request having been consumed — into the row;
  BP > 0 detours through ``m`` and re-enters one row-pair lower.
* The CPU flushes the corridor to the ``-1``, reads ``rem`` (always **even**:
  rows are packed to even word counts, and instruction starts sit at even
  global indexes), and runs the stock 2x4 counted discard: the next word is
  the target's opcode.

Interior layout (data rows y = 1..R, row index i = y - 1; eastbound rows at
odd y):

    col 0          seek riser (bottom collector -> station), '^'
    cols 1..3      west ladder: (1) 'v'/'>', (2) 'm'/'.', (3) '<'/'d'
    col 4          ladder-exit walkway '.'
    col 5          west transition: 'v' (even y), '>' (odd y)
    col 6          west gadget 'q' (odd y)
    col 7          west gadget 'd' (odd y); cascade passthrough '.' (even y)
    col DL=8..DR   the drum: '>' / '<' turn cols, packed data between
    col DR+1       east gadget 'a' (even y); cascade passthrough '.' (odd y)
    col DR+2       east gadget 'q' (even y)
    col DR+3       east transition: 'v' (odd y), '<' (even y)
    cols DR+4..+6  east ladder: (+4) 'a'/'>', (+5) 'm'/'.', (+6) 'v'/'<'
    col DR+7       the wrap riser (bottom connector -> top connector)
    row -3         west-ladder feeder (station's even-parity path)
    row -2         the station
    row -1         east-ladder feeder (odd-parity path)
    row 0          top connector (spawn; wrap re-entry into row 1's gadget)
    row R+1        bottom connector (last row -> wrap riser)
    row R+2        cascade collector (both sides -> seek riser)

Everything is verified on the reference engine by ``tests/test_seekrom.py``
and the toy harness in ``scratch/ram-program/``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .rom import pack_data_rows, token_cells

__all__ = ["SEEK_K", "SeekRom", "build_seek_rom", "pack_rows_even", "seek_target"]

#: Row/remainder encoding base; must exceed the words on any packed row.
#: 128 = 8*8*2 is buildable in digits, so no backtick enters the station.
SEEK_K = 128


class SeekRom(BaseModel):
    """A placed seek-drum: cells, box, and the word -> (row, offset) map."""

    model_config = ConfigDict(frozen=True)

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    rows_used: int
    words: tuple[int, ...]
    word_pos: tuple[tuple[int, int], ...]  # word index -> (row index, offset)
    #: grid y of the origin: interior row -3 is the topmost; callers blit at
    #: (x, y + 3) to keep coordinates non-negative.
    top_pad: int = 3


def seek_target(layout: SeekRom, word_index: int) -> int:
    """The operand for a jump to ``word_index``: ``row * SEEK_K + offset``."""
    row, off = layout.word_pos[word_index]
    return row * SEEK_K + off


def pack_rows_even(tokens: list[str], data_w: int) -> list[str]:
    """``rom.pack_data_rows`` with one extra rule: every row holds an EVEN
    number of words (its last token slides to the next row when odd).

    Even rows keep instruction starts at even in-row offsets — which is what
    lets the CPU reuse the stock 2x4 counted discard for the remainder. The
    packing algorithm (boustrophedon reversal, per-column backtick parity) is
    copied from :func:`rom.pack_data_rows` verbatim; only the row-close rule
    differs.
    """
    if data_w < 1:
        raise ValueError("data_w must be positive")
    parity = [0] * data_w
    bad = [False] * data_w
    dig = [0] * data_w

    def feasible(col: int, glyph: str) -> bool:
        if glyph == "`":
            return parity[col] % 2 == 0 or (not bad[col] and dig[col] <= 18)
        if not (glyph.isdigit() or glyph == " "):
            return parity[col] % 2 == 0
        return True

    def commit(col: int, glyph: str, cells: list[str]) -> None:
        cells[col] = glyph
        if glyph == "`":
            parity[col] += 1
            bad[col] = False
            dig[col] = 0
        elif parity[col] % 2 == 1:
            if glyph.isdigit():
                dig[col] += 1
            elif glyph != " ":
                bad[col] = True

    rows: list[str] = []
    i = 0
    while i < len(tokens):
        east = len(rows) % 2 == 0
        cells = [" "] * data_w
        placed: list[tuple[int, str]] = []  # (start, glyphs) actually committed
        cur = 0 if east else data_w - 1
        row_i = i
        while row_i < len(tokens):
            glyphs = tokens[row_i] if east else tokens[row_i][::-1]
            n = len(glyphs)
            start = cur if east else cur - n + 1
            while (
                0 <= start
                and start + n <= data_w
                and not all(feasible(start + j, glyphs[j]) for j in range(n))
            ):
                start += 1 if east else -1
            if not (0 <= start and start + n <= data_w):
                break
            for j, glyph in enumerate(glyphs):
                commit(start + j, glyph, cells)
            placed.append((start, glyphs))
            cur = start + n if east else start - 1
            row_i += 1
        count = row_i - i
        if count == 0:
            raise ValueError(f"data_w={data_w} too small for token {tokens[i]!r}")
        if count % 2 == 1 and row_i < len(tokens):
            # drop the last token to the next row: blank its cells and undo the
            # column bookkeeping by recomputing it for the removed glyphs.
            if count == 1:
                raise ValueError(f"data_w={data_w} holds fewer than two tokens a row")
            start, glyphs = placed.pop()
            for j, glyph in enumerate(glyphs):
                col = start + j
                cells[col] = " "
                if glyph == "`":
                    parity[col] -= 1
                # digit/bad bookkeeping inside an open literal cannot be undone
                # exactly, but a removed token only ever closed its own pair:
                # its two backticks cancel, restoring the column to even parity,
                # and dig/bad only matter while parity is odd.
            count -= 1
            row_i -= 1
        rows.append("".join(cells))
        i = row_i
    return rows


def _width_for_rows_even(tokens: list[str], rows: int) -> int:
    """Narrowest ``data_w`` that packs evenly into at most ``rows`` rows."""
    lo = max(max(len(t) for t in tokens), 4)
    hi = max(lo, sum(len(t) for t in tokens))
    while lo < hi:
        mid = (lo + hi) // 2
        try:
            fits = len(pack_rows_even(tokens, mid)) <= rows
        except ValueError:
            fits = False
        if fits:
            hi = mid
        else:
            lo = mid + 1
    return lo


def build_seek_rom(words: list[int] | tuple[int, ...], *, rows: int = 2) -> SeekRom:
    """Lay the seek-drum. Emits the same word stream as the packed drum."""
    if not words:
        raise ValueError("a ROM needs at least one word")
    rows = max(1, rows)
    tokens = [token_cells(w) for w in words]
    data_w = _width_for_rows_even(tokens, rows)
    lit = pack_rows_even(tokens, data_w)
    R = len(lit)

    per_row = [r.count("s") for r in lit]
    if sum(per_row) != len(words):
        raise ValueError("packing lost a word")
    word_pos: list[tuple[int, int]] = []
    for r, n in enumerate(per_row):
        if n >= SEEK_K:
            raise ValueError(f"row {r} holds {n} words >= K={SEEK_K}; deepen the fold")
        for off in range(n):
            word_pos.append((r, off))

    cells: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, ch: str) -> None:
        old = cells.get((x, y))
        if old is not None and old != ch:
            raise ValueError(f"seekrom collision at {(x, y)}: {old!r} vs {ch!r}")
        cells[(x, y)] = ch

    DL = 8
    DR = DL + data_w + 1
    EA, EQ, ET = DR + 1, DR + 2, DR + 3
    L0 = DR + 4  # east ladder columns L0..L0+2
    WRAP = DR + 7
    width = WRAP + 1

    # ── data rows and the drum's own turn columns ────────────────────────────
    for idx, body in enumerate(lit):
        y = idx + 1
        for j, ch in enumerate(body):
            if ch != " ":
                put(DL + 1 + j, y, ch)
        if idx % 2 == 0:  # eastbound: enters at DL, exits east through the gadget
            put(DL, y, ">")
            put(DR, y, ">")
            put(EA, y, ".")
            put(EQ, y, ".")
            put(ET, y, "v")
        else:  # westbound: enters at DR, exits west through the gadget
            put(DR, y, "<")
            put(DL, y, "<")
            put(7, y, ".")
            put(6, y, ".")
            put(5, y, "v")

    # ── west gadgets (odd y: eastbound row entries) ──────────────────────────
    for idx in range(0, R, 2):
        y = idx + 1
        put(5, y, ">")
        put(6, y, "q")
        put(7, y, "d")
        if y + 1 <= R:  # cascade passthrough below (even line)
            put(7, y + 1, ".")
    # ── east gadgets (even y: westbound row entries) ─────────────────────────
    for idx in range(1, R, 2):
        y = idx + 1
        put(ET, y, "<")
        put(EQ, y, "q")
        put(EA, y, "a")
        if y + 1 <= R:
            put(EA, y + 1, ".")

    # ── west ladder (rungs at odd y) ─────────────────────────────────────────
    # A rung's detour exists only when a next rung exists below it: a BP > 0 at
    # the last rung would seek past the last row, which no valid request does.
    for idx in range(0, R, 2):
        y = idx + 1
        put(3, y, "d")
        put(4, y, ".")
        put(2, y, ".")
        put(1, y, ">")
        if y + 2 <= R:
            put(3, y + 1, "<")
            put(2, y + 1, "m")
            put(1, y + 1, "v")
    # ── east ladder (rungs at even y) ────────────────────────────────────────
    for idx in range(1, R, 2):
        y = idx + 1
        put(L0, y, "a")
        put(L0 + 1, y, ".")
        put(L0 + 2, y, "<")
        if y + 2 <= R:
            put(L0, y + 1, ">")
            put(L0 + 1, y + 1, "m")
            put(L0 + 2, y + 1, "v")
    # east ladder entry: descend col L0+2 from the feeder row through odd cells
    put(L0 + 2, 0, ".")
    put(L0 + 2, 1, "v")

    # ── top connector (row 0) ────────────────────────────────────────────────
    put(ET, 0, ".")
    put(EQ, 0, "<")
    put(EA, 0, "<")
    spawn = DR - 1
    for x in range(DL, DR + 1):
        put(x, 0, "@" if x == spawn else "<")
    put(WRAP, 0, "<")
    put(L0, 0, "<")
    put(L0 + 1, 0, "<")
    put(7, 0, "<")
    put(6, 0, "<")
    put(5, 0, "v")  # drop into row 1's gadget line

    # ── bottom connector (row R+1) and the wrap riser ────────────────────────
    last_east = (R - 1) % 2 == 0
    if last_east:
        # last row exits east: its transition 'v' at (ET, R) drops here
        put(ET, R + 1, ">")
        for x in range(ET + 1, WRAP):
            put(x, R + 1, ".")
    else:
        # last row exits west: transition 'v' at (5, R) drops to (5, R+1)
        put(5, R + 1, ">")
        for x in range(6, WRAP):
            if (x, R + 1) not in cells:
                put(x, R + 1, ".")
    put(WRAP, R + 1, "^")
    for y in range(1, R + 1):
        put(WRAP, y, "^")

    # cascades cross the bottom connector then collect on row R+2
    put(7, R + 1, ".") if (7, R + 1) not in cells else None
    put(EA, R + 1, ".") if (EA, R + 1) not in cells else None
    put(EA, R + 2, "<")
    for x in range(1, EA):
        if (x, R + 2) not in cells:
            put(x, R + 2, "<")
    put(0, R + 2, "^")
    for y in range(-1, R + 2):
        put(0, y, "^")

    # ── the station (row -2) and its feeders ─────────────────────────────────
    put(0, -2, ">")
    put(1, -2, ".")
    put(2, -2, ".")
    station = "8M8*M2*Mr/b1NsWsx"
    for j, ch in enumerate(station):
        put(3 + j, -2, ch)
    xcol = 3 + len(station) - 1
    # even parity (bit 0 -> CCW from east = north): west feeder row -3.
    # The `]` halving BP into the rung count rides the feeder, after the turn.
    put(xcol, -3, "<")
    put(xcol - 1, -3, "]")
    for x in range(2, xcol - 1):
        put(x, -3, "<")
    put(1, -3, "v")
    put(1, -1, ".")
    put(1, 0, ".")
    # odd parity (bit 1 -> CW from east = south): east feeder row -1
    put(xcol, -1, ">")
    put(xcol + 1, -1, "]")
    for x in range(xcol + 2, L0 + 2):
        put(x, -1, ">")
    put(L0 + 2, -1, "v")

    # normalise: shift everything down by top_pad so y >= 0
    shifted = {(x, y + 3): ch for (x, y), ch in cells.items()}
    height = R + 2 + 1 + 3  # rows -3..R+2
    return SeekRom(
        cells=shifted,
        width=width,
        height=height,
        rows_used=R,
        words=tuple(words),
        word_pos=tuple(word_pos),
    )
