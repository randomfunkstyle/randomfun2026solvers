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

__all__ = ["RomLayout", "build_rom", "digit_width", "group_cells", "rows_for_budget"]


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
