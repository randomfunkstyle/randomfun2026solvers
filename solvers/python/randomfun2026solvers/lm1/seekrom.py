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

``twin_station`` replaces the notice path with :func:`_twin_top`'s: the
cascades turn **north**, there is a station on each side of row -2, and the
collector row, the riser and column 0 do not exist. Same protocol, same
operands, same ladders — only where the man walks to read the request.

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


def _twin_top(put, cells, *, R, DR, EA, L0, WS0, WSX, ES0, ESX) -> None:
    """The twin-station notice path: two stations, no collector, no riser.

    The one-station drum walks a **loop of the whole room** on every taken
    seek: down the cascade to a collector at row ``R+2``, west across all 431
    cells of it, up a riser at column 0 to a station at row -2, and then back
    east across the room again on the half of seeks whose target row is entered
    from the east.  Measured on ``deadman-3d_hires``/men-v3 that is 1,079 ticks
    a seek with essentially none of it blocked — pure travel — while the CPU
    sits 927 t/seek blocked behind it.

    Three of those four legs are the same defect: **one station, in one corner,
    that both the arrival and the departure path have to reach.**  The
    departure leg is irreducible (a row is enterable only from the end it is
    packed from, so half of all seeks must cross the room whatever happens);
    the arrival legs are not.  This lays out the room so they vanish:

    * the two gadgets turn **north** instead of south (``a`` west, ``d`` east),
      so a diverted man cascades *up* his own side rather than down to a
      collector he then has to walk off;
    * he crosses the top connector on a ``.`` — a westbound man is already
      westbound, so those ``<`` were redundant — and lands on row -2, which
      carries **both** stations: the west one at ``WS0..WSX``, the east one at
      ``ES0..ESX``, each entered heading east off its own cascade column.  No
      collector row and no riser exist at all.
    * both stations being walked *eastward* is what lets them share one pair of
      feeders: ``x`` turns clockwise on an odd row and counter-clockwise on an
      even one, so entry heading fixes which row each parity lands on.  Mirror
      one station and the two parities swap rows, and the two long feeders then
      need the same row in opposite directions — which is what costs the
      obvious "put a station at each end" layout two extra rows.

    The feeders are one row each, and each carries both stations:

    * **row -3, westbound to the west ladder** (even rows).  The west station
      joins it at ``WSX`` after 23 cells; the east station at ``ESX``, and its
      run is the room-crossing half of the irreducible departure cost.
    * **row -1, converging on the east ladder's drop at ``L0+2``** (odd rows).
      The west station runs east into it, the east station runs *west* into it
      from beyond the wrap riser — opposite directions on one row, but the
      column ranges are disjoint either side of the drop, so they never share a
      cell.

    ``]`` moves off the feeders and onto the two drop columns, the only cells
    common to both stations' paths for a given parity.

    The cost is width: the east station is the only thing that reaches past the
    wrap riser, so the room grows by ``len(STATION) - 6`` columns, which
    ``build``'s fold loop pays for in rows.
    """
    # ── the two cascades' escape: up their own column, across row 0 on a `.`,
    #    and east into the station that is already on their side ─────────────
    put(7, -1, ".")
    put(7, -2, ">")
    put(EA, -2, ">")
    for j, ch in enumerate(STATION):
        put(WS0 + j, -2, ch)
        put(ES0 + j, -2, ch)

    # ── row -3: even rows, westbound, into the west ladder's drop ───────────
    put(1, -3, "v")
    for x in range(2, ESX + 1):
        put(x, -3, "<")
    put(1, -2, "]")  # halve BP on the drop, not on the feeder
    put(1, -1, ".")
    put(1, 0, ".")

    # ── row -1: odd rows, converging on the east ladder's drop at L0+2 ──────
    put(WSX, -1, ">")
    for x in range(WSX + 1, L0 + 2):
        if (x, -1) not in cells:
            put(x, -1, ".")
    put(L0 + 2, -1, "v")
    for x in range(L0 + 3, ESX + 1):
        put(x, -1, "<")


#: The station's micro-program, walked **eastward**: build ``K = 128`` in B
#: (digits only — no backtick enters the drum), receive the request, split it
#: into ``row`` (A) and ``rem`` (B), load BP, emit the ``-1`` sentinel and then
#: ``rem`` into the fetch corridor, and turn on ``row``'s parity.
STATION = "8M8*M2*Mr/b1NsWsx"


def build_seek_rom(
    words: list[int] | tuple[int, ...],
    *,
    rows: int = 2,
    wide: frozenset[int] | set[int] = frozenset(),
    wide_digits: int = 0,
    twin_station: bool = False,
) -> SeekRom:
    """Lay the seek-drum. Emits the same word stream as the packed drum.

    ``wide`` word indexes are emitted as zero-padded ``wide_digits`` literals,
    so their token width is independent of their value — which is what lets
    :func:`machine.seek_words` resolve jump operands in one repack instead of
    chasing a moving layout.

    ``twin_station`` rebuilds the **notice path** — see
    :func:`_twin_top` for the topology and why it is worth a registry.
    """
    if not words:
        raise ValueError("a ROM needs at least one word")
    rows = max(1, rows)
    tokens = [
        (f"`{w:0{wide_digits}d}`s" if i in wide else token_cells(w))
        for i, w in enumerate(words)
    ]
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
    # Twin mode's stations both sit on row -2 and are both walked *east*: the
    # west one from the west cascade's column, the east one from the east
    # cascade's. The east one is the only thing in the room that reaches past
    # the wrap riser, and its `x` column is the room's east edge.
    WS0 = DL  # west station, cols WS0..WS0 + len(STATION) - 1
    WSX = WS0 + len(STATION) - 1
    ES0 = EA + 1  # east station
    ESX = ES0 + len(STATION) - 1
    if twin_station and DR <= WSX + 1:
        raise ValueError(
            f"twin_station needs data_w > {WSX + 2 - DL}; the two stations "
            f"would overlap on row -2 at data_w={data_w}"
        )
    width = (max(WRAP, ESX) if twin_station else WRAP) + 1

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
    # The gadget's turn glyph is what sets the cascade's *direction*. Entered
    # heading east, `d` (clockwise) sends the diverted man south to the bottom
    # collector; `a` (counter-clockwise) sends him north, to a station on the
    # row band he is already walking towards. Straight-through on BP == 0 —
    # the whole not-seeking path — is identical either way.
    for idx in range(0, R, 2):
        y = idx + 1
        put(5, y, ">")
        put(6, y, "q")
        put(7, y, "a" if twin_station else "d")
        if y + 1 <= R:  # cascade passthrough below (even line)
            put(7, y + 1, ".")
    # ── east gadgets (even y: westbound row entries) ─────────────────────────
    for idx in range(1, R, 2):
        y = idx + 1
        put(ET, y, "<")
        put(EQ, y, "q")
        put(EA, y, "d" if twin_station else "a")
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
    # Twin mode halves BP *on the drop* rather than on the feeder row, because
    # its two feeders each carry two stations' traffic and only the drop column
    # is common to both. The wrap man crosses this cell every revolution with
    # BP == 0, and 0 >> 1 is 0.
    put(L0 + 2, 0, "]" if twin_station else ".")
    put(L0 + 2, 1, "v")

    # ── top connector (row 0) ────────────────────────────────────────────────
    # Every `<` between the wrap riser and the spawn is redundant — a man is
    # already heading west when he reaches it — so twin mode spends two of them
    # on `.`, and the two ascending cascades cross the connector without the
    # sequential man noticing.
    put(ET, 0, ".")
    put(EQ, 0, "<")
    put(EA, 0, "." if twin_station else "<")
    spawn = DR - 1
    for x in range(DL, DR + 1):
        put(x, 0, "@" if x == spawn else "<")
    put(WRAP, 0, "<")
    put(L0, 0, "<")
    put(L0 + 1, 0, "<")
    put(7, 0, "." if twin_station else "<")
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

    if twin_station:
        _twin_top(put, cells, R=R, DR=DR, EA=EA, L0=L0, WS0=WS0, WSX=WSX, ES0=ES0, ESX=ESX)
        bottom = R + 1
    else:
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

        # ── the station (row -2) and its feeders ─────────────────────────────
        put(0, -2, ">")
        put(1, -2, ".")
        put(2, -2, ".")
        for j, ch in enumerate(STATION):
            put(3 + j, -2, ch)
        xcol = 3 + len(STATION) - 1
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
        bottom = R + 2

    # normalise: shift everything down by top_pad so y >= 0
    shifted = {(x, y + 3): ch for (x, y), ch in cells.items()}
    height = bottom + 1 + 3  # rows -3..bottom
    return SeekRom(
        cells=shifted,
        width=width,
        height=height,
        rows_used=R,
        words=tuple(words),
        word_pos=tuple(word_pos),
    )
