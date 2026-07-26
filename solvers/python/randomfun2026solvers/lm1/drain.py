#!/usr/bin/env python3
"""The DRAIN — discard ``BP`` words from the ROM pipe at one read per tick.

A generated LM-1 has no program counter (``ROM-RECIRCULATION.md``): the only way
to reach an instruction is to let the ones before it go past, so every jump and
every taken branch is a *discard loop*. On ``little-little-man`` that loop is
**21.4% of measured ticks**, the largest single line in the heat map.

What is there today (``machine._discard_loop``) is a counted loop::

    a<
    rm
    rm
    >^

Two words a lap, eight cells a lap — **4 ticks per discarded word**. The shape
is not the problem; the *counter* is. ``m`` decrements ``BP`` by one and costs a
cell, so a counted loop can never spend less than one ``m`` per word beside its
one ``r`` per word. Widen the lap to ``R`` reads and it is ``2R + 3`` cells for
``R`` words — **2 ticks per word is the floor for anything counted with ``m``**,
approached but never beaten.

One glyph fires per tick, so **one read per tick is the hard ceiling for a single
man**. Reaching it means the man's path must be ``r`` cells and almost nothing
else, and its *length* must still depend on the count. That rules out ``m``
entirely and leaves the other counting primitive ``SPEC.md`` offers:
``]`` (``BP >>= 1``) and ``x`` (turn on ``BP``'s low bit) — the binary
decomposition pair.

The ladder
----------

``bits`` stages, low bit first. Stage ``j`` discards ``2**j`` words if bit ``j``
of the count is set, and is skipped if it is clear. After ``bits`` stages
``BP`` has been shifted ``bits`` times and is 0, so the ladder is straight-line
code with no loop and no termination test: the man walks in at the top heading
south and falls out of the bottom having read exactly ``n`` words.

The whole difficulty is the **bypass**. A run of ``2**j`` reads laid out as a
straight line has its two ends ``2**j`` cells apart, so skipping it costs as much
as walking it and the ladder buys nothing. The fix is to fold every run into a
**hairpin** — west along one row, back east along the next — which puts the exit
directly *below* the entry. Skipping is then two cells of falling, whatever the
run is worth::

        x.        <- BP low bit: clockwise (west, into the run)
     vrrrv           counter-clockwise (east, into the bypass)
     >rrrv<       <- both arms merge on the `v` under the `x`
        ]         <- BP >>= 1, then fall into the next stage

Counting the ``x`` and the ``]``, stage ``j`` costs ``2**j + 5`` ticks taken and
``5`` skipped, so the ladder as a whole costs::

    n + 5 * bits          ticks, for any n < 2**bits

which is one read per tick plus a constant — 45 ticks of overhead at ``bits=9``,
against the 4n of the loop it replaces.

``even``
--------

Every count this is built for is **even**: ``machine.rom_words`` emits
``2 * ((target - k - 1) % n)`` because the ROM image is fixed-width two-word
instructions. Bit 0 is therefore never set, and stage 0 is pure overhead — 5
ticks on every single jump to skip a run that can never be taken. ``even=True``
replaces it with a bare ``]`` (1 tick), which is both faster and two columns
narrower, and makes the ladder refuse an odd count instead of silently
mis-discarding one.

Deeper folds
------------

A hairpin is two rows tall and ``2**(j-1)`` columns wide, so the top stage of a
9-bit ladder is 128 columns wide. ``max_width`` folds the wide stages into
serpentines of more (always an even number of) legs instead, trading width for
height and for bypass cost: a stage of ``rows`` legs is ``rows`` tall, ``R/rows``
wide, and costs ``rows + 1`` ticks to skip rather than 3. The knob exists because
footprint is the *other* half of the score and which way to spend it is a
per-machine question — ``little-little-man`` is square and has a dead rectangle
east of the CPU stack, ``tcp`` has neither.

``unit`` — the cheap half of the curve
--------------------------------------

A pure ladder draws **one ``r`` cell per discardable word**: 511 cells of grid to
discard 511 words, however it is folded. That is the expensive part, and on a
footprint-scored machine it may well be the disqualifying one.

The fix is to stop believing the earlier claim that ``m`` costs a cell per word.
It costs a cell per *lap* — the reason today's loop is 4 ticks a word is that its
lap is two words long. A lap of ``U`` words in a rectangular loop is ``U + 6``
cells (four corners, one ``m``, one nop to make ``U`` even), so it runs at
``1 + 6/U`` ticks a word using ``U + 6`` cells of grid **whatever the count**.

What a counted loop cannot do is divide, and that is exactly what the ladder
already provides: after ``t`` stages the ladder has discarded ``n mod 2**t`` and
left ``BP = n >> t``. Hand that to a loop of ``U = 2**t`` words a lap and the two
halves compose into::

    n + 6 * (n >> t) + 5 * t          ticks, in ~2**t + 5*t cells, for *any* n

``unit_bits=t`` builds that. It is slower than the pure ladder and very much
smaller, it has **no capacity limit** — a loop runs as many laps as ``BP`` says,
so the block does not have to grow when the program does — and it spans the
whole range from "barely bigger than what is there today" to "within 5% of the
ladder":

===========  ========  ==============  ===========  ==========
``unit_bits``   cells   ticks/word@510   vs today       shape
===========  ========  ==============  ===========  ==========
2 (U=4)          ~30         2.51          1.6x      loop-dominated
3 (U=8)          ~65         1.77          2.3x      the cheap knee
4 (U=16)        ~110         1.50          2.7x
5 (U=32)        ~180         1.22          3.3x
none (ladder)   ~893         1.17          3.4x      one cell per word
===========  ========  ==============  ===========  ==========

What this component does not solve
----------------------------------

**The producer.** ``ROM-RECIRCULATION.md`` measures the ROM man emitting a word
every 3.36 ticks and prices a discard at ``max(loop ticks/word, ROM ticks/word)``.
A drain at 1 tick/word therefore realises 4.0 -> 3.36 and no more until the ROM
side is fixed too; below ~3.4 the ROM man *is* the bottleneck and a repeater is
what unblocks it. That is the reason this is a standalone, separately measurable
block rather than a patch to ``_slab``.

**Binding.** Every ``r`` here competes with every incoming pipe in the room
(``ARCH.md`` §7.1: nearest, not nearest-that-can-proceed), and this block has a
lot of ``r``. :attr:`DrainBlock.reads` is published so a placement can be checked
rather than hoped for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["DrainBlock", "DrainError", "build_drain", "cost", "walk"]


class DrainError(RuntimeError):
    pass


#: east, south, west, north
_EAST, _SOUTH, _WEST, _NORTH = (1, 0), (0, 1), (-1, 0), (0, -1)


def _cw(d: tuple[int, int]) -> tuple[int, int]:
    return (-d[1], d[0])


def _ccw(d: tuple[int, int]) -> tuple[int, int]:
    return (d[1], -d[0])


@dataclass
class DrainBlock:
    """A drawn ladder: cells at block-local coordinates, plus its contract.

    The man **arrives at** :attr:`entry` **heading south** with ``BP`` holding the
    word count, and **leaves** :attr:`exit` **heading south** with ``BP == 0``,
    having executed exactly that many ``r``. Both cells are on the spine column,
    so a caller only ever has to route a southbound man in and out; every turn the
    ladder needs is internal.
    """

    cells: dict[tuple[int, int], str]
    width: int
    height: int
    bits: int
    spine: int  # the column every stage enters and leaves on
    entry: tuple[int, int]  # arrive here heading south, BP = words to discard
    exit: tuple[int, int]  # first cell *below* the block; heading south, BP = 0
    reads: list[tuple[int, int]]  # every `r`, for a pipe-binding check
    even: bool = False  # no bit-0 stage: counts must be multiples of two
    unit: int = 0  # words a lap of the coarse loop discards; 0 = no loop
    stage_rows: list[tuple[int, int, int]] = field(default_factory=list)  # (bit, top, rows)
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)

    @property
    def step(self) -> int:
        """The granularity of a count: 2 for an ``even`` ladder, else 1."""
        return 2 if self.even else 1

    @property
    def bounded(self) -> bool:
        """False when a coarse loop makes the block good for any count at all."""
        return not self.unit

    @property
    def capacity(self) -> int:
        """The largest count this block can discard.

        A looped block has no real ceiling — the loop runs whatever ``BP`` says —
        so this reports the largest count that still fits in a 64-bit backpack,
        which is the only limit ``SPEC.md`` imposes.
        """
        if self.unit:
            return ((1 << 62) // self.unit) * self.unit
        return ((1 << self.bits) - 1) & ~(self.step - 1)

    def counts(self, limit: int | None = None) -> range:
        """Every count to sweep. Looped blocks need a ``limit``; ladders do not."""
        top = self.capacity if limit is None else limit
        if not self.bounded and limit is None:
            raise DrainError("a looped block is unbounded — say how far to sweep")
        return range(0, top + 1, self.step)

    def rows_text(self) -> list[str]:
        w = max(x for x, _ in self.cells) + 1
        h = max(y for _, y in self.cells) + 1
        return ["".join(self.cells.get((x, y), " ") for x in range(w)).rstrip() for y in range(h)]


def _legs(run: int, max_width: int | None) -> tuple[int, int]:
    """``(legs, width)`` for a run of ``run`` reads: an even number of legs.

    Even is not cosmetic. An odd number leaves the man at the *west* end of the
    fold, which is the one thing the whole design is built to avoid — the bypass
    is cheap only because entry and exit share a column.
    """
    if run == 1:
        return 1, 1  # the degenerate stage; drawn by hand below
    half = run // 2
    w = half if max_width is None else min(half, max(1, max_width))
    while w > 1 and (run % w or (run // w) % 2):
        w -= 1
    legs = run // w
    if legs % 2:
        raise DrainError(f"run {run} does not fold into an even number of legs at width {w}")
    return legs, w


def build_drain(
    bits: int,
    *,
    max_width: int | None = None,
    even: bool = False,
    unit_bits: int | None = None,
) -> DrainBlock:
    """Draw a ``bits``-stage ladder able to discard any count below ``2 ** bits``.

    ``max_width`` caps how wide a single stage's fold may be; ``None`` gives the
    two-row hairpin, which is the fastest to skip and the widest. ``even`` drops
    the bit-0 stage in favour of a bare ``]``, which is what every ROM discard
    count wants.

    ``unit_bits=t`` truncates the ladder at ``t`` stages and hangs a counted loop
    of ``2 ** t`` words a lap underneath it, which trades a few percent of speed
    for most of the area and removes the capacity limit entirely. ``bits`` is
    ignored in that case — the loop takes whatever ``BP`` is left. See the module
    docstring for the curve.
    """
    if unit_bits is not None:
        if unit_bits < 2:
            # A unit of two is `1 + 6/2` = 4 ticks a word: exactly the counted
            # loop this replaces, in more cells. There is no point below 4 words.
            raise DrainError("a coarse loop needs a unit of at least four words")
        bits = unit_bits
    if bits < 1:
        raise DrainError("a ladder needs at least one stage")
    if even and bits < 2:
        raise DrainError("an even ladder needs a stage above bit 0")

    first = 1 if even else 0
    shape = {j: _legs(1 << j, max_width) for j in range(first, bits)}
    # The spine sits one column east of the widest fold's turn column, and never
    # closer than 2 — the 1-word stage reaches two columns west of the spine.
    spine = max(max(w for _, w in shape.values()) + 1, 2)

    cells: dict[tuple[int, int], str] = {}
    reads: list[tuple[int, int]] = []
    stage_rows: list[tuple[int, int, int]] = []
    regions: dict[str, tuple[int, int, int, int]] = {}

    def put(x: int, y: int, ch: str) -> None:
        old = cells.get((x, y))
        if old is not None and old != ch:
            raise DrainError(f"collision at {(x, y)}: {old!r} vs {ch!r}")
        cells[(x, y)] = ch
        if ch == "r":
            reads.append((x, y))

    y = 0
    if even:
        # Bit 0 is structurally zero, so its stage would only ever be skipped.
        # Shifting it away costs one cell instead of five.
        put(spine, 0, "]")
        y = 1

    for j in range(first, bits):
        run = 1 << j
        legs, w = shape[j]
        top = y

        put(spine, top, "x")  # low bit set -> clockwise from south -> west

        if run == 1:
            # One read cannot be halved into a hairpin, so it takes the long way
            # round: read, turn, and come back on the row below.
            put(spine - 1, top, "r")
            put(spine - 2, top, "v")
            put(spine - 2, top + 1, ">")
            put(spine - 1, top + 1, ".")
            body = 2
        else:
            west = spine - w - 1
            for leg in range(legs):
                row = top + leg
                if leg % 2 == 0:  # heading west
                    for x in range(spine - 1, spine - w - 1, -1):
                        put(x, row, "r")
                    put(west, row, "v")
                else:  # heading east
                    put(west, row, ">")
                    for x in range(spine - w, spine):
                        put(x, row, "r")
                    if leg != legs - 1:
                        put(spine, row, "v")
                        put(spine, row + 1, "<")
            body = legs

        # The bypass: east off the `x`, straight down beside the fold, and back
        # west onto the merge cell. `body + 1` ticks whatever the stage is worth.
        put(spine + 1, top, "v")
        for row in range(top + 1, top + body - 1):
            put(spine + 1, row, ".")
        put(spine + 1, top + body - 1, "<")

        put(spine, top + body - 1, "v")  # merge: fold arrives east, bypass west
        put(spine, top + body, "]")  # BP >>= 1, then fall into the next stage

        stage_rows.append((j, top, body + 1))
        regions[f"drain:bit{j}"] = (spine - w - 1, top, w + 3, body + 1)
        y = top + body + 1

    exit_at = (spine, y)
    unit = 0
    if unit_bits is not None:
        # ── the coarse loop ──────────────────────────────────────────────────
        # The ladder has left ``BP = n >> unit_bits``: exactly the number of laps
        # of ``2 ** unit_bits`` words still owed. A rectangular loop is four
        # corners, one ``m`` and one nop — the nop only so the read count comes
        # out *even*, since a rectilinear cycle always has even length and an
        # even number of corners, which would otherwise force an odd unit and no
        # power of two to divide by.
        unit = 1 << unit_bits
        body = unit // 2 + 1  # cells per leg; 2*(body-1) == unit reads a lap
        put(spine, y, "<")  # the ladder's man turns west onto the test
        put(spine - 1, y, "a")  # BP > 0: counter-clockwise from west is south
        put(spine - 1, y + 1, ".")  # the parity nop
        for row in range(y + 2, y + body + 1):
            put(spine - 1, row, "r")
        put(spine - 1, y + body + 1, ">")
        put(spine, y + body + 1, "^")
        for row in range(y + 2, y + body + 1):
            put(spine, row, "r")
        put(spine, y + 1, "m")  # decrement immediately before the test
        regions["drain:loop"] = (spine - 1, y, 2, body + 2)

        # BP == 0 walks straight west out of the `a`, clear of the loop.
        put(spine - 2, y, "v")
        exit_at = (spine - 2, y + 1)
        y += body + 2

    width = spine + 2
    return DrainBlock(
        cells=cells,
        width=width,
        height=y,
        bits=bits,
        spine=spine,
        entry=(spine, 0),
        exit=exit_at,
        reads=reads,
        even=even,
        unit=unit,
        stage_rows=stage_rows,
        regions={"drain": (0, 0, width, y), **regions},
    )


def walk(block: DrainBlock, n: int, *, limit: int | None = None) -> tuple[int, int]:
    """Step a man through the ladder with ``BP = n``. Returns ``(reads, ticks)``.

    The interpreter is the oracle for whether a grid *runs*; this is the oracle
    for what it *costs*, and it is the only way to check every count exhaustively
    — ``2 ** bits`` cases is a fraction of a second here and minutes of node.

    It implements only the six glyphs the ladder uses and raises on anything else,
    so it cannot silently agree with a grid that has drifted.
    """
    if not 0 <= n <= block.capacity:
        raise DrainError(f"{n} is outside a {block.bits}-bit ladder's 0..{block.capacity}")
    if n % block.step:
        # An even ladder shifts bit 0 away unexamined, so an odd count would
        # discard one word too few and land the CPU on an operand. Refusing is
        # the whole reason `even` is a flag and not a silent optimisation.
        raise DrainError(f"{n} is odd; this ladder discards in steps of {block.step}")

    x, y = block.entry
    d = _SOUTH
    bp, reads, ticks = n, 0, 0
    # A pure ladder is straight-line — every stage falls into the next and
    # nothing loops — so the man cannot step on a cell twice and the cell count
    # *is* the bound. A coarse loop does revisit, once per lap, so it gets the
    # laps it is owed plus the ladder's own bound.
    cap = limit
    if cap is None:
        cap = len(block.cells) + 2
        if block.unit:
            cap += (n // block.unit + 1) * (block.unit + 6)

    while (x, y) != block.exit:
        ch = block.cells.get((x, y))
        if ch is None:
            raise DrainError(f"walked off the ladder at {(x, y)} after {ticks} ticks")
        if ch == "x":
            d = _cw(d) if bp & 1 else _ccw(d)
        elif ch == "]":
            bp >>= 1
        elif ch == "r":
            reads += 1
        elif ch == "a":
            if bp > 0:
                d = _ccw(d)
        elif ch == "m":
            bp -= 1
        elif ch == "v":
            d = _SOUTH
        elif ch == "<":
            d = _WEST
        elif ch == ">":
            d = _EAST
        elif ch == "^":
            d = _NORTH
        elif ch != ".":
            raise DrainError(f"unexpected glyph {ch!r} at {(x, y)}")
        x, y = x + d[0], y + d[1]
        ticks += 1
        if ticks > cap:
            raise DrainError(f"ladder did not terminate for n={n} within {cap} ticks")

    if bp:
        raise DrainError(f"BP is {bp}, not 0, after draining {n}")
    return reads, ticks


def cost(block: DrainBlock, n: int) -> int:
    """Ticks to discard ``n`` words, including walking off the bottom."""
    return walk(block, n)[1]


# ── the probe: the smallest grid that exercises the ladder for real ──────────
#: A man reads ``n``, loads it into ``BP``, falls through the ladder, then reads
#: **one more** word and emits it. Feed ``n 1 2 3 ...`` and the output is ``n+1``
#: exactly when the ladder consumed exactly ``n`` — the count is observable from
#: outside without trusting anything inside.
def build_probe(block: DrainBlock) -> tuple[list[str], dict[tuple[int, int], tuple[int, int]]]:
    """``(rows, translate)`` — the grid, and block-local -> grid coordinates."""
    from .machine import _Grid

    g = _Grid()
    # Room x0 is chosen so the input room and its pipe fit to the west.
    x0, y0 = 8, 0
    # The setup row needs `@ r b` west of the spine, and a narrow block (a small
    # coarse loop is four columns wide) does not otherwise leave room for them.
    pad = max(1, 4 - block.spine)
    inner_w = max(pad - 1 + block.width, block.spine + pad + 2)
    rh = block.height + 5
    g.room(x0, y0, x0 + inner_w + 1, y0 + rh + 1)

    ox, oy = x0 + pad, y0 + 2
    g.blit(ox, oy, block.cells)
    spine = ox + block.spine

    # Setup row, above the ladder: spawn, read the count, load BP, turn south.
    g.put(x0 + 1, y0 + 1, "@")
    g.put(x0 + 2, y0 + 1, "r")
    g.put(x0 + 3, y0 + 1, "b")
    for x in range(x0 + 4, spine):
        g.soft(x, y0 + 1, ".")
    g.put(spine, y0 + 1, "v")

    # Tail, below it: the witness read, the send, and the halt. A looped block
    # leaves on a different column from the one it entered — the coarse loop
    # occupies the spine — so this follows `exit`, not the spine.
    out_x, tail = ox + block.exit[0], oy + block.exit[1]
    g.put(out_x, tail, "r")
    g.put(out_x, tail + 1, "s")
    g.put(out_x, tail + 2, "H")

    # One incoming pipe and one outgoing, so every `r` and the `s` bind
    # unambiguously whatever the ladder's shape.
    g.room(1, 1, 3, 3)
    g.put(2, 2, "I")
    g.draw_pipe([(4, 2), (x0 - 1, 2)])

    oy_out = tail + 1
    g.room(x0 + inner_w + 4, oy_out - 1, x0 + inner_w + 6, oy_out + 1)
    g.put(x0 + inner_w + 5, oy_out, "O")
    g.draw_pipe([(x0 + inner_w + 2, oy_out), (x0 + inner_w + 3, oy_out)])

    translate = {(bx, by): (ox + bx, oy + by) for (bx, by) in block.cells}
    return g.rows(), translate


def trace(block: DrainBlock, n: int) -> list[tuple[int, int]]:
    """Every cell the man steps on for count ``n``, in order."""
    path: list[tuple[int, int]] = []
    x, y = block.entry
    d, bp = _SOUTH, n
    while (x, y) != block.exit:
        path.append((x, y))
        ch = block.cells[(x, y)]
        if ch == "x":
            d = _cw(d) if bp & 1 else _ccw(d)
        elif ch == "]":
            bp >>= 1
        elif ch == "a":
            d = _ccw(d) if bp > 0 else d
        elif ch == "m":
            bp -= 1
        elif ch in "v<>^":
            d = {"v": _SOUTH, "<": _WEST, ">": _EAST, "^": _NORTH}[ch]
        x, y = x + d[0], y + d[1]
    return [*path, (x, y)]


def debug_map(block: DrainBlock, *, counts: tuple[int, ...] = ()) -> tuple[list[str], object]:
    """``(rows, DebugMap)`` — the probe grid with every stage named and traced.

    The picture is the argument: a stage's *taken* path threads the fold and its
    *skipped* path falls two cells past it, and putting both on one grid is the
    only way to see at a glance that the bypass does not scale with the run.
    """
    from ..man_debug import DebugMap

    rows, translate = build_probe(block)
    ox, oy = translate[next(iter(block.cells))]
    bx, by = next(iter(block.cells))
    dbg = DebugMap(
        f"DRAIN — {'ladder' if block.bounded else f'ladder + loop of {block.unit}'}, "
        f"{block.width}x{block.height}, {len(block.reads)} reads drawn",
        offset=(ox - bx, oy - by),
    )
    for bit, top, rows_used in block.stage_rows:
        run = 1 << bit
        x, y, w, h = block.regions[f"drain:bit{bit}"]
        dbg.region(
            f"bit{bit}",
            x,
            y,
            w,
            h,
            note=f"bit {bit}: {run} word{'s' if run > 1 else ''} when set, "
            f"{run + 5} ticks taken / 5 skipped",
            color="#38bdf8" if bit % 2 else "#0ea5e9",
            local=True,
        )
    if "drain:loop" in block.regions:
        x, y, w, h = block.regions["drain:loop"]
        dbg.region(
            "coarse loop",
            x,
            y,
            w,
            h,
            note=f"{block.unit} words a lap in {block.unit + 6} cells — the area "
            "does not grow with the count",
            color="#f97316",
            local=True,
        )
    palette = ("#22c55e", "#ef4444", "#a855f7", "#eab308")
    for i, n in enumerate(counts):
        ticks = len(trace(block, n)) - 1
        dbg.lane(
            f"n={n}",
            trace(block, n),
            note=f"discards {n} in {ticks} ticks ({ticks / max(1, n):.2f} a word); "
            f"bits set: {format(n, 'b')}",
            color=palette[i % len(palette)],
            kind="control",
            local=True,
        )
    return rows, dbg


def probe_input(n: int, words: int) -> list[int]:
    """``n`` followed by ``1..words`` — the witness read returns ``n + 1``."""
    return [n, *range(1, words + 1)]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bits", type=int)
    ap.add_argument("--max-width", type=int)
    ap.add_argument("--unit-bits", type=int, help="truncate the ladder and add a coarse loop")
    ap.add_argument("--even", action="store_true", help="no bit-0 stage; even counts only")
    ap.add_argument("--probe", action="store_true", help="print the testable grid instead")
    ap.add_argument("--html", help="write an overlay here, tracing --trace")
    ap.add_argument("--trace", default="", help="comma-separated counts to draw as lanes")
    args = ap.parse_args(argv)

    blk = build_drain(
        args.bits, max_width=args.max_width, even=args.even, unit_bits=args.unit_bits
    )
    if args.html:
        counts = tuple(int(v) for v in args.trace.split(",") if v.strip())
        rows, dbg = debug_map(blk, counts=counts)
        dbg.write_html(rows, args.html)
        print(f"wrote {args.html}")
        return 0
    if args.probe:
        rows, _ = build_probe(blk)
        print("\n".join(rows))
        return 0

    print("\n".join(blk.rows_text()))
    worst = blk.capacity
    print(
        f"\n{blk.width}x{blk.height}, {len(blk.reads)} reads drawn, "
        f"capacity {worst}, {cost(blk, worst)} ticks at worst "
        f"({cost(blk, worst) / worst:.3f} ticks/word) vs {4 * worst} for the counted loop"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
