"""The looping ROM: a program's words, regenerated forever by one walking man.

``ARCH.md`` §5.3 established that a *looping* ROM beats a code ring on every axis
— no capacity constraint, no write-back invariant, fewer rooms — so this is the
program store of record. The man walks a closed circuit of numeric literals and
``s``ends each one into the CPU's fetch pipe; when the CPU halts he simply blocks
on a full pipe, which is the harmless steady state.

Two hazards from ``ARCH.md`` §4.2, neither optional once words exceed one digit:

**Digit reversal.** ```123``` walked right-to-left loads **321**, so a westbound
row is emitted reversed. Reversing the whole row string is enough: it visits the
row's characters in forward order when walked west, so the digits come out right
and the ``s`` still lands after its own literal.

**Accidental vertical literals.** Backticks pair on rows *and columns
independently* (``SPEC.md`` §Fine print), and a non-digit between a vertical pair
is a **load error**. Two rules together make that impossible here:

1. *Every word is one fixed-width group*, so backtick columns are identical in
   every literal row.
2. *The group is palindromic in its backtick offsets* — ``.`NNN`s``, with the pad
   in front. A group of ``` `NNN`s ``` has backticks at 0 and 4 of 6, which
   reversal moves to 1 and 5; adding the leading ``.`` makes the offsets 1 and
   ``width+2`` of ``width+4``, which reversal maps onto themselves. Without it an
   eastbound and a westbound row have backticks in *different* columns, and a
   column holding backticks in rows 1 and 3 pairs them across row 2's ``s`` — a
   non-digit, hence a load error.

With both, every backtick column holds one backtick per literal row, the rows are
adjacent, and the vertical pairs are (row 1, row 2), (row 3, row 4), … — **empty**
literals, which the spec explicitly makes a nop.

The serpentine is a real closed circuit, so one more detail matters: the spawn
must join it immediately before word 0, or execution starts mid-program
(``ARCH.md`` §5.3 — its first probe emitted ``9 7 8`` instead of ``7 8 9`` for
exactly this reason).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = [
    "RomLayout",
    "build_packed_rom",
    "build_rom",
    "digit_width",
    "group_cells",
    "pack_data_rows",
    "packed_cells",
    "rows_for_budget",
    "token_cells",
    "width_for_rows",
]


def digit_width(words: list[int] | tuple[int, ...]) -> int:
    """Digits needed to write every word at one fixed width.

    Words are non-negative: the ROM holds no negative literal (``ARCH.md`` §4.2),
    the assembler rejects them, and the CPU builds negatives at runtime.
    """
    if any(w < 0 for w in words):
        raise ValueError("ROM words must be non-negative (ARCH §4.2)")
    return max(1, max((len(str(w)) for w in words), default=1))


def group_cells(word: int, width: int) -> str:
    """One word's ROM cells: ``.`NNN`s`` — see the module docstring on the pad."""
    return f".`{word:0{width}d}`s"


def rows_for_budget(n_words: int, width: int, budget: int) -> int:
    """Literal rows needed to keep the ROM's interior within ``budget`` columns.

    Footprint is ``max(w, h)²``, so the ROM's job is to trade width into height
    until it stops being the widest thing in the machine (``ARCH.md`` §7.4).
    """
    per = width + 4
    usable = max(per, budget - 3)  # two turn columns plus the riser
    per_row = max(1, usable // per)
    return max(1, -(-n_words // per_row))


class RomLayout(BaseModel):
    """A placed ROM: its cells and its interior bounding box."""

    model_config = ConfigDict(frozen=True)

    cells: dict[tuple[int, int], str]
    width: int  # interior width, in cells
    height: int  # interior height, in cells
    rows_used: int  # literal rows in the serpentine
    words: tuple[int, ...]


def build_rom(words: list[int] | tuple[int, ...], *, rows: int = 2) -> RomLayout:
    """Lay a looping ROM that emits ``words`` in order, forever.

    ``rows`` literal rows are walked boustrophedon and closed into a cycle by a
    bottom connector and an east riser::

        y=0    v<<<<<<<<<<<<<@<     top connector, walked west; spawn sits on it
        y=1    >.`007`s.`008`s v ^  literal row, eastbound
        y=2    v s`800`s.`700`. < ^ literal row, westbound (row reversed)
        y=3    >.`009`s.`010`s v ^
        y=4    >..............>  ^  bottom connector, walked east
               ^ riser: north, back into the top connector
               1              L+2 L+3

    The riser gets its own column so it never climbs through the turn glyphs that
    sit at either end of a literal row.
    """
    if not words:
        raise ValueError("a ROM needs at least one word")
    rows = max(1, rows)
    width = digit_width(words)
    groups = [group_cells(w, width) for w in words]
    per_row = -(-len(groups) // rows)

    # Chunk into rows, then pad each to a common length so both turn columns line
    # up. Padding is spaces, which are nops walked in either direction and are
    # explicitly legal inside a literal.
    chunks = [groups[i : i + per_row] for i in range(0, len(groups), per_row)]
    rows_used = len(chunks)
    lit = ["".join(c) for c in chunks]
    L = max(len(s) for s in lit)
    lit = [s.ljust(L) for s in lit]

    cells: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, ch: str) -> None:
        old = cells.get((x, y))
        if old is not None and old != ch:
            raise ValueError(f"ROM collision at {(x, y)}: {old!r} vs {ch!r}")
        cells[(x, y)] = ch

    left, right, riser = 1, L + 2, L + 3

    # ── literal rows, alternating direction ──────────────────────────────────
    for i, body in enumerate(lit):
        y = i + 1
        if i % 2 == 0:  # eastbound: entered at the left wall end, exits right
            put(left, y, ">")
            for j, ch in enumerate(body):
                put(left + 1 + j, y, ch)
            put(right, y, "v")
        else:  # westbound: entered at the right, row reversed so digits read forward
            put(right, y, "<")
            for j, ch in enumerate(body[::-1]):
                put(left + 1 + j, y, ch)
            put(left, y, "v")

    # ── top connector: the riser's top turns west and runs back to row 1 ─────
    put(riser, 0, "<")
    put(left, 0, "v")
    spawn = riser - 1
    for x in range(left + 1, riser):
        # The spawn cell replaces one `<`; a westbound man just walks over it.
        put(x, 0, "@" if x == spawn else "<")

    # ── bottom connector: the last literal row's exit runs east to the riser ─
    bottom = rows_used + 1
    exit_col = right if (rows_used - 1) % 2 == 0 else left
    put(exit_col, bottom, ">")
    for x in range(exit_col + 1, riser):
        put(x, bottom, ".")
    put(riser, bottom, "^")
    for y in range(1, bottom):
        put(riser, y, "^")

    return RomLayout(
        cells=cells,
        width=riser + 1,
        height=bottom + 1,
        rows_used=rows_used,
        words=tuple(words),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Packed ROM: variable-width tokens, no fixed pad
# ─────────────────────────────────────────────────────────────────────────────
#
# :func:`build_rom` spends ``width + 4`` cells on *every* word because one fixed
# width keeps the backtick columns aligned, which is what makes the vertical
# pairing safe (see the module docstring). That is a bad deal for a real program:
# a CPU word is an opcode or a small operand, so most of them are one digit and
# pay for three.
#
# Two observations retire the fixed width. A bare digit is "an ordinary
# single-digit load" (``SPEC.md`` §Numeric literals), so a one-digit word needs no
# backticks at all — ``7s``, two cells instead of seven. And the alignment
# argument is only ever a *sufficient* condition: what the spec actually forbids
# is a non-digit trapped between a matched **vertical** pair, so it is enough to
# track each column's running backtick parity and place each token where it is
# provably safe. That is exactly ``rom_baseN.pack_tokens``' invariant, which was
# written for history-lesson's variable-length base-128 words; the CPU ROM is the
# same problem with different tokens.
#
# Measured on the shipped programs, that halves the ROM: 7.00 -> 3.46 cells/word
# on ``gradebook``, 6.00 -> 2.67 on ``tcp``. Both halves matter, because a looping
# ROM charges its cells twice — once as area, and once as *time*, since a backward
# jump makes the CPU wait out the rest of the lap (``ARCH.md`` §5.3). Halving the
# lap halves that jump overhead, which is 20–53% of these programs' ticks.


def token_cells(word: int) -> str:
    """One word's cheapest ROM cells.

    A single digit loads on its own (``SPEC.md``: "a digit walked in a direction
    where it belongs to no literal is an ordinary single-digit load"), so it skips
    the backticks; anything longer needs them to load as one value.
    """
    if word < 0:
        raise ValueError("ROM words must be non-negative (ARCH §4.2)")
    s = str(word)
    return f"{s}s" if len(s) == 1 else f"`{s}`s"


def packed_cells(words: list[int] | tuple[int, ...]) -> int:
    """Total data cells the packed tokens occupy, before any placement padding."""
    return sum(len(token_cells(w)) for w in words)


def pack_data_rows(tokens: list[str], data_w: int) -> list[str]:
    """Boustrophedon-pack ``tokens`` into rows of ``data_w`` data columns.

    Odd rows are walked west, so their tokens are laid reversed and filled from the
    right — the man then visits each token's characters in forward order and the
    digits load the right way round (``ARCH.md`` §4.2's digit reversal).

    Placement keeps one invariant per column, the same one
    :func:`rom_baseN.pack_tokens` keeps: a non-digit may only land where the
    running backtick parity is **even**, and a closing backtick may only close a
    run of at most 18 digits that holds no non-digit. A token that does not fit at
    the cursor slides along the row until it does, so the padding is per-placement
    rather than per-word — usually none at all.
    """
    if data_w < 1:
        raise ValueError("data_w must be positive")
    parity = [0] * data_w
    bad = [False] * data_w
    dig = [0] * data_w

    def feasible(col: int, glyph: str) -> bool:
        if glyph == "`":
            # Closing an open run is only safe if the run is loadable.
            return parity[col] % 2 == 0 or (not bad[col] and dig[col] <= 18)
        if not (glyph.isdigit() or glyph == " "):
            return parity[col] % 2 == 0  # would otherwise be trapped in a literal
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
        placed = False
        cur = 0 if east else data_w - 1
        while i < len(tokens):
            glyphs = tokens[i] if east else tokens[i][::-1]
            n = len(glyphs)
            start = cur if east else cur - n + 1
            while (
                0 <= start
                and start + n <= data_w
                and not all(feasible(start + j, glyphs[j]) for j in range(n))
            ):
                start += 1 if east else -1
            if not (0 <= start and start + n <= data_w):
                break  # row is full; the next row retries this token
            for j, glyph in enumerate(glyphs):
                commit(start + j, glyph, cells)
            cur = start + n if east else start - 1
            i += 1
            placed = True
        if not placed:
            raise ValueError(f"data_w={data_w} too small for token {tokens[i]!r}")
        rows.append("".join(cells))
    return rows


def width_for_rows(words: list[int] | tuple[int, ...], rows: int) -> int:
    """The narrowest ``data_w`` that packs ``words`` into at most ``rows`` rows.

    Row count falls monotonically with width, so this is a bisection. The lower
    bound is the widest single token, since a token never straddles two rows.
    """
    if rows < 1:
        raise ValueError("rows must be positive")
    tokens = [token_cells(w) for w in words]
    lo = max(len(t) for t in tokens)
    hi = max(lo, packed_cells(words))
    while lo < hi:
        mid = (lo + hi) // 2
        try:
            fits = len(pack_data_rows(tokens, mid)) <= rows
        except ValueError:
            fits = False
        if fits:
            hi = mid
        else:
            lo = mid + 1
    return lo


def build_packed_rom(words: list[int] | tuple[int, ...], *, rows: int = 2) -> RomLayout:
    """Lay a looping ROM of :func:`token_cells` tokens, folded onto ``rows`` rows.

    Same closed circuit as :func:`build_rom` — top connector, boustrophedon data
    rows, bottom connector, east riser — but the data rows are packed rather than
    tiled, and the bottom connector is walked over **blanks** instead of ``.``.
    A blank is a nop in either direction (``build_rom`` already relies on that for
    its row padding), and unlike ``.`` it cannot be the non-digit that spoils a
    column whose backtick parity happens to be open at the last row.
    """
    if not words:
        raise ValueError("a ROM needs at least one word")
    rows = max(1, rows)
    tokens = [token_cells(w) for w in words]
    data_w = width_for_rows(words, rows)
    lit = pack_data_rows(tokens, data_w)
    rows_used = len(lit)

    cells: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, ch: str) -> None:
        old = cells.get((x, y))
        if old is not None and old != ch:
            raise ValueError(f"ROM collision at {(x, y)}: {old!r} vs {ch!r}")
        cells[(x, y)] = ch

    left, right, riser = 1, data_w + 2, data_w + 3

    # ── data rows, alternating direction ─────────────────────────────────────
    for i, body in enumerate(lit):
        y = i + 1
        if i % 2 == 0:  # eastbound: enters at the left turn, exits at the right
            put(left, y, ">")
            put(right, y, "v")
        else:  # westbound: enters at the right turn, exits at the left
            put(right, y, "<")
            put(left, y, "v")
        for j, ch in enumerate(body):
            put(left + 1 + j, y, ch)

    # ── top connector: the riser turns west and runs back into row 1 ─────────
    put(riser, 0, "<")
    put(left, 0, "v")
    spawn = riser - 1
    for x in range(left + 1, riser):
        put(x, 0, "@" if x == spawn else "<")

    # ── bottom connector: the last data row's exit runs east to the riser ────
    bottom = rows_used + 1
    exit_col = right if (rows_used - 1) % 2 == 0 else left
    put(exit_col, bottom, ">")
    for x in range(exit_col + 1, riser):
        put(x, bottom, " ")
    put(riser, bottom, "^")
    for y in range(1, bottom):
        put(riser, y, "^")

    return RomLayout(
        cells=cells,
        width=riser + 1,
        height=bottom + 1,
        rows_used=rows_used,
        words=tuple(words),
    )
