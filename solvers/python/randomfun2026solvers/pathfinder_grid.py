#!/usr/bin/env python3
"""`pathfinder` as a grid: :mod:`pathfinder_prog`'s block graph poured into one
serpentine worker room, with **column bands** deciding every pipe binding.

The machine itself — 50 blocks of straight glyph runs over one man, an 18-word
ring, a spill FIFO ``F`` and a scratch FIFO ``G`` — is :mod:`pathfinder_prog`,
validated at the op level on all seven public cases.  This module is only the
*layout* problem, and for this program the layout problem is one constraint:

## Nearest-pipe binding is the whole floorplan

``s``/``r`` bind to the **nearest** pipe by Manhattan distance — nearest, not
nearest-ready — so each of the 327 pipe ops has to be standing where its pipe
wins.  There are four outgoing pipes (ring, spill ``F``, scratch ``G``,
painter) and four incoming (ring, ``F``, ``G``, program input), and the two
partitions are independent: an ``s`` only ever competes with the other sends.

**All eight anchors sit on the worker's north wall.**  For a north anchor the
distance from an interior cell is ``|x - col| + y + 2``, so the ``y`` term is
common to all four and "nearest pipe" collapses to *nearest anchor column* — a
one-dimensional rule that holds identically at every row, which is the whole
reason it can be asserted on every single placement rather than hoped for.

Band order left to right is **R (ring), G (scratch), F (spill), P/I** — the
measured optimum over the program's zone-transition matrix (``G-R`` 29,
``F-R`` 17, ``F-G`` 16, ``F-P`` 15, ``F-I`` 6, ``G-P`` 4).  The painter's send
anchor and the input's receive anchor share the rightmost band because the two
partitions are independent and ``ri`` occurs only five times.

Anchors alternate between mirrored offsets inside their bands.  Adjacent
anchors in either partition therefore sum to an **odd** number, so
``|x-c1| == |x-c2|`` has no integer solution and a tie — which would fall back
on reading order and rebind on a one-cell edit — cannot exist.
:func:`send_band` / :func:`recv_band` recompute the winner from the same
Manhattan rule the engine uses and :meth:`Placer.pipe_op` refuses to emit a
glyph outside its band, so a mis-binding is a build error.

## The room: a west channel and a serpentine code area

    columns 0 .. NCHW-1        west channel — no code, ever
    columns NCHW .. NCHW+4w-1  the code area, four bands of w columns

Each block owns a contiguous run of rows.  It is entered at ``(NCHW-1, r0)``
through a ``>``, runs **east** along ``r0``, and boustrophedons — dropping a
row and reversing whenever the next pipe op's band lies behind the cursor,
which is what makes the band hops cost travel proportional to the number of
zone transitions rather than to the number of ops.  It always ends heading
**west**, walking out of the code area into the channel on its last row ``r1``.
That is why a block is at least two rows: it enters eastbound and can only
reverse by dropping.

The branch glyph (``X``/``x``/``d``) is the block's last token and is placed in
the channel at ``(c_b, r1)``, where the man arrives heading **west**.  So

    straight = west      clockwise = north      counter-clockwise = south

and each of the three lanes has a free channel cell to leave through: north and
south are channel cells of the block's own rows, which never hold code.  Lanes
are then wires: a vertical run in one channel column, a ``>`` at the target's
entry row, and an eastward run into that target's ``>``.  Wires may cross each
other freely wherever both are blank — there is only one man in this room, so a
blank cell is shared by every lane that walks it; only glyphs are exclusive.
:class:`Circuit` with ``strict_corridors`` is what enforces that.

A north lane can only reach a target *above* it and a south lane one *below*,
so :data:`ORDER` is chosen to satisfy exactly those constraints — notably every
``lap``'s body is laid **before** its ``d``, and each ``x`` test's hit block
before it and its miss block after.  Two blocks (``MAIN`` and ``ROTPRE``) send
``pos`` and ``neg`` to the *same* successor, which cannot be both above and
below; their north lane takes a jog west on the row above the branch instead,
which needs that row's channel half to be empty and so pads them to four rows.

A block therefore occupies an **even** number of rows — it enters eastbound and
leaves westbound — and that is where the height goes: 48 blocks, 71 rows spent
on band reversals, 39 on the reversal a block that runs out of tokens heading
east still has to pay for.

## Numeric literals

Backticks pair on rows **and columns independently**, and a non-digit caught
between a vertical pair is a *load* error — the reference engine says
``expected a digit or a space between backticks``.  The program needs 20
multi-digit literals, so the generator remembers the row of the most recent
backtick in every column.  It reuses a column only when every already-emitted
cell since that delimiter is a digit or blank, exactly rechecking the loader's
rule rather than imposing the older and costlier one-backtick-per-column rule.

Digits are emitted in *walk* order, so a literal laid down on a westbound row
reads correctly for the man and backwards in the file.  That is fine: the load
check only requires the value to fit in 64 bits read either way.

## The rest of the box

* the panel harness is :mod:`pathfinder_panel`, a verified 20x24 block whose
  incoming pipe must terminate at block-relative ``(1, 1)`` heading east; it
  sits in the north-east corner and the painter pipe climbs the ``sp`` anchor
  column and runs east along row 1 into it;
* three turnaround rooms, one per pipe loop, sitting flush at the top of an
  **eight-row** north band.  Capacity is correctness, not tuning: the ring must
  hold **19** cells (18 words plus one) or it deadlocks silently, ``F`` 7 and
  ``G`` 8.  Each room is the flat two-row relay — every non-corner cell is half
  of an ``r``/``s`` pair, 2.55 ticks a word at width 14 against
  ``value_ring.RELAY_NORTH``'s 6.
* the band is pure overhead on the dimension that gets squared, so the ring
  buys its 20 cells from two horizontal jogs rather than from ten rows of
  descent; ``F`` and ``G`` reach theirs straight down in four.

Measured, all seven public cases, both backends agreeing exactly: 84 x 175,
``area2`` 30,625, 277,368 avg ticks, score 8.49e9.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from randomfun2026solvers import pathfinder_prog as prog
from randomfun2026solvers.circuit import Circuit, Collision, E, N, S, W
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.pathfinder_panel import BLOCK_H, BLOCK_W, build_block
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

__all__ = [
    "BAND_W",
    "NCHW",
    "ORDER",
    "Placer",
    "build",
    "flat_relay",
    "recv_band",
    "send_band",
    "worker",
]

# ── worker geometry ───────────────────────────────────────────────────────────
#: Columns per band.  The current literal and pipe-band placement needs 12.
BAND_W = 12
#: West-channel columns.  Every wire runs here; the widest row of the block
#: graph has ten live wires crossing it, plus the shared entry column.
NCHW = 11
CODEW = 4 * BAND_W
IW = NCHW + CODEW
#: First code column, and the last one.
CW0, CW1 = NCHW, IW - 1
#: The column that carries every block's entry ``>``; wires never run down it.
ENTRY_COL = NCHW - 1

_SEND_T = 4
_RECV_T = 6
#: Left-to-right order of the four pipe loops.  Measured over all 24
#: permutations: the ones that shave a row or two off the height (the
#: serpentine spends a row on every *reversal* of the band sequence) all pay
#: for it several times over in travel, because this order is the one that
#: minimises band-steps — ``G-R`` 29, ``F-R`` 17, ``F-G`` 16, ``F-P`` 15,
#: ``F-I`` 6, ``G-P`` 4, 87 transitions for 108 steps.  ``R P F G`` is 177 rows
#: and 369k ticks against this one's 181 and 276k.
LOOP_ORDER = ("R", "G", "F", "P")
#: The receive side of the same loop; the painter's band hosts program input.
RECV_OF = {"R": "R", "G": "G", "F": "F", "P": "I"}


def _anchors() -> tuple[dict[str, int], dict[str, int]]:
    """Anchor columns, placed so every band boundary is a half-integer.

    Neighbouring anchors sum to an odd number in both partitions, so
    ``|x-c1| == |x-c2|`` has no integer solution: there is no tie to fall back
    on reading order, and no one-cell edit can silently rebind a pipe op.
    """
    send: dict[str, int] = {}
    recv: dict[str, int] = {}
    for i, band in enumerate(LOOP_ORDER):
        # mirrored inside alternate bands, which is what makes every adjacent
        # pair sum odd whatever the band width is
        send[band] = CW0 + i * BAND_W + (
            _SEND_T if i % 2 == 0 else BAND_W - 1 - _SEND_T
        )
        recv[RECV_OF[band]] = CW0 + i * BAND_W + (
            _RECV_T if i % 2 == 0 else BAND_W - 1 - _RECV_T
        )
    return send, recv


SEND_ANCHOR, RECV_ANCHOR = _anchors()

SEND_TOK = {"sr": "R", "sg": "G", "sf": "F", "sp": "P"}
RECV_TOK = {"rr": "R", "rg": "G", "rf": "F", "ri": "I"}

#: High-water marks from :mod:`pathfinder_prog`, plus the one slot that keeps a
#: full FIFO from blocking its own producer.
RING_CELLS = prog.RING_WORDS + 1
FIFO_CELLS = prog.FIFO_WORDS + 1
SCRATCH_CELLS = prog.SCRATCH_WORDS + 1

#: Placement of the room inside the grid.  ``WY`` is the depth of the north
#: band, and it is set by the ring: ten pipe rows either side of its relay is
#: what buys the 20 cells the 18-word ring needs to not deadlock.
WX, WY = 1, 10

# ── which pipe does a column bind to? ─────────────────────────────────────────


def _nearest(x: int, anchors: dict[str, int]) -> str:
    """The anchor a north-wall pipe op at column ``x`` binds to.

    Distances are ``|x - col| + y + 2`` for every anchor, so the row cancels and
    this is exactly what the engine computes.  A tie is impossible by
    construction and is raised rather than resolved.
    """
    best = sorted((abs(x - c), name) for name, c in anchors.items())
    if best[0][0] == best[1][0]:
        raise Collision(f"column {x} ties between {best[0][1]} and {best[1][1]}")
    return best[0][1]


def send_band(x: int) -> str:
    return _nearest(x, SEND_ANCHOR)


def recv_band(x: int) -> str:
    return _nearest(x, RECV_ANCHOR)


def band_span(band: str, kind: str) -> tuple[int, int]:
    """The inclusive code-column range that binds to ``band``."""
    fn = send_band if kind == "s" else recv_band
    cols = [x for x in range(CW0, CW1 + 1) if fn(x) == band]
    if not cols:
        raise Collision(f"no code column binds to {kind}{band}")
    return cols[0], cols[-1]


# ── the relay room ────────────────────────────────────────────────────────────
def flat_relay(w: int = 10) -> list[str]:
    """A flat turnaround room: 2W/(W-3) ticks per word, 2.9 at W=10.

    ``@`` at (1,0) is the spawn — the man's first act must be ``r``, never an
    ``s`` that would inject a spurious 0 — and ``.`` at (1,1) keeps the body
    length even so the walk ends on an ``s``; an odd body would leave a value in
    A for the next lap's first ``r`` to overwrite, losing a word silently.
    """
    if w < 6 or w % 2:
        raise ValueError("relay width must be even and at least 6")
    top = [">", "@"] + ["rs"[i % 2] for i in range(w - 3)] + ["v"]
    bot = ["^", "."] + ["sr"[i % 2] for i in range(w - 3)] + ["<"]
    body = "".join(top), "".join(bot)
    seq = list(body[0][2 : w - 1]) + [body[1][x] for x in range(w - 2, 1, -1)]
    if seq != ["r", "s"] * (w - 3):
        raise ValueError(f"relay walk is not an r/s alternation: {seq}")
    return [
        "+" + "-" * w + "+",
        "|" + body[0] + "|",
        "|" + body[1] + "|",
        "+" + "-" * w + "+",
    ]


# ── block order ───────────────────────────────────────────────────────────────
#
# A north lane can only reach a target above the branch and a south lane one
# below, so this order is not cosmetic: every constraint below is asserted by
# :func:`_check_order`.  Read it as the program's flow with each ``lap``'s body
# hoisted above its ``d`` test, and each ``x`` test's hit block above it.
ORDER = [
    ["INIT"],
    ["OUTB"],
    ["PACKB"],
    ["PACKT"],
    ["PACKEND"],
    ["OUTT"],
    ["AFTERLOAD"],
    ["POUTB"],
    ["PB0"],
    ["PBITB"],
    ["PB1"],
    ["PBITT"],
    ["POUTT"],
    ["SETROBOT"],
    ["ALIGNB"],
    ["ALIGNT"],
    ["ALIGNEND"],
    ["SKIP"],
    ["MAIN"],
    ["ROUND", "SEEDPRE"],
    ["SEEDB"],
    ["SEEDT"],
    ["SEEDEND"],
    ["ITERPRE"],
    ["ITERB"],
    ["ITERT"],
    ["MVUP"],
    ["MVRIGHT"],
    ["MVDOWN"],
    ["MVLEFT"],
    ["ROTPRE"],
    ["ITEREND", "TU"],
    ["TR"],
    ["TD"],
    ["TL"],
    ["WAVEPRE"],
    ["LAPAB"],
    ["LAPAT"],
    ["LAPAEND"],
    ["LAPBB"],
    ["LAPBT"],
    ["LAPBEND"],
    ["DONE"],
    ["NULLB"],
    ["NULLT"],
    ["ROTPRE2"],
    ["ROTB"],
    ["ROTT"],
]

#: Blocks whose ``pos`` lane has to jog west on the row above the branch because
#: ``pos`` and ``neg`` share a successor and it cannot be both above and below.
JOG_POS = {"MAIN", "ROTPRE"}

#: Lane -> the direction it leaves a branch glyph the man reaches heading WEST.
#: straight = west, clockwise = north, counter-clockwise = south.
LANE_DIR = {
    "X": {"zero": W, "pos": N, "neg": S},
    "x": {"one": N, "zero": S},
    "d": {"pos": N, "zero": W},
}

GLYPH = {
    "M": "M", "W": "W", "N": "N", "+": "+", "-": "-", "*": "*",
    "&": "&", "|": "|", "~": "~", "{": "{", "}": "}",
    "b": "b", "m": "m", "]": "]", "X": "X", "x": "x", "d": "d",
}


def _superblocks() -> tuple[dict[str, list[str]], dict[str, object]]:
    """``ORDER`` resolved against the program: tokens and the successor map."""
    p = prog.build()
    names = [n for chain in ORDER for n in chain]
    if sorted(names) != sorted(p):
        missing = set(p) - set(names)
        extra = set(names) - set(p)
        raise Collision(f"ORDER mismatch: missing {missing}, extra {extra}")
    supers: dict[str, list[str]] = {}
    succ: dict[str, object] = {}
    for chain in ORDER:
        toks: list[str] = []
        for i, name in enumerate(chain):
            body, s = p[name]
            toks += body
            if i + 1 < len(chain) and s != chain[i + 1]:
                raise Collision(f"{name} does not fall through to {chain[i + 1]}")
        supers[chain[0]] = toks
        succ[chain[0]] = p[chain[-1]][1]
    # every edge must land on a superblock head
    head_of = {n: chain[0] for chain in ORDER for n in chain}
    for name, s in list(succ.items()):
        if isinstance(s, str):
            succ[name] = s if s == "HALT" else head_of[s]
        else:
            succ[name] = {k: (v if v == "HALT" else head_of[v]) for k, v in s.items()}
    return supers, succ


# ── the serpentine cursor ─────────────────────────────────────────────────────
class Placer:
    """Pours one block's token stream into the code area, band by band.

    The cursor is ``(x, y, dx)``.  Emitting a pipe op first walks to the nearest
    legal column of its band, reversing onto the next row when that band lies
    behind the current heading — which is the whole point of the boustrophedon:
    going east it reaches bands to its right for free, going west the ones to
    its left.
    """

    def __init__(self, circ: Circuit, backticks: dict[int, int]) -> None:
        self.c = circ
        self.backticks = backticks
        self.x = self.y = 0
        self.dx = 1

    # -- primitives ----------------------------------------------------------
    def start(self, row: int) -> None:
        self.c.set(ENTRY_COL, row, ">")
        self.x, self.y, self.dx = CW0, row, 1

    @property
    def ahead(self) -> int:
        """Code cells left in front of the cursor on this row, inclusive."""
        return (CW1 - self.x + 1) if self.dx > 0 else (self.x - CW0 + 1)

    def newline(self) -> None:
        """Drop a row and reverse.  Two glyphs: the ``v`` and the turn under it."""
        self.c.set(self.x, self.y, "v")
        self.y += 1
        self.c.set(self.x, self.y, "<" if self.dx > 0 else ">")
        self.dx = -self.dx
        self.x += self.dx

    def need(self, n: int) -> None:
        # One cell is always held back for the ``v`` that starts the next row,
        # so the cursor is never left standing outside the code area.
        if self.ahead < n + 1:
            self.newline()

    def emit(self, ch: str) -> None:
        self.need(1)
        self.c.set(self.x, self.y, ch)
        self.x += self.dx

    def blank_to(self, col: int) -> None:
        while self.x != col:
            self.c.set(self.x, self.y, " ")
            self.x += self.dx

    # -- tokens --------------------------------------------------------------
    def goto_band(self, lo: int, hi: int) -> None:
        """Walk to the nearest legal column of ``[lo, hi]``, dropping a row when
        the band lies behind the current heading."""
        for _ in range(4):
            if self.ahead < 2:
                self.newline()
                continue
            if lo <= self.x <= hi:
                return
            if (self.dx > 0 and lo > self.x) or (self.dx < 0 and hi < self.x):
                self.blank_to(lo if self.dx > 0 else hi)
                return
            self.newline()
        raise Collision(f"band hop to [{lo},{hi}] did not converge")

    def pipe_op(self, tok: str) -> None:
        kind, band = ("s", SEND_TOK[tok]) if tok in SEND_TOK else ("r", RECV_TOK[tok])
        lo, hi = band_span(band, kind)
        self.goto_band(lo, hi)
        got = send_band(self.x) if kind == "s" else recv_band(self.x)
        if got != band:
            raise Collision(f"{tok} at column {self.x} binds {kind}{got}, not {kind}{band}")
        self.emit(kind)

    def literal(self, digits: str) -> None:
        """```nnn```, placed so neither delimiter shares a column with another.

        Backticks pair vertically as well as horizontally and a live glyph
        caught between a vertical pair is a load error.  A column may be reused
        only when every already-emitted cell since its previous delimiter is a
        digit or blank, which is exactly the loader's rule.
        """
        span = len(digits) + 2
        for _ in range(3):
            while self.ahead >= span + 1:
                a = self.x
                b = self.x + (span - 1) * self.dx
                safe_a = a not in self.backticks or all(
                    self.c.get(a, y).isdigit() or self.c.get(a, y) == " "
                    for y in range(self.backticks[a] + 1, self.y)
                )
                safe_b = b not in self.backticks or all(
                    self.c.get(b, y).isdigit() or self.c.get(b, y) == " "
                    for y in range(self.backticks[b] + 1, self.y)
                )
                if safe_a and safe_b:
                    self.backticks[a] = self.y
                    self.backticks[b] = self.y
                    self.emit("`")
                    for ch in digits:
                        self.emit(ch)
                    self.emit("`")
                    return
                self.c.set(self.x, self.y, " ")
                self.x += self.dx
            self.newline()
        raise Collision(f"nowhere to put literal `{digits}` with fresh backtick columns")

    def token(self, tok: str) -> None:
        if tok in SEND_TOK or tok in RECV_TOK:
            self.pipe_op(tok)
        elif tok[0] == "L":
            v = tok[1:]
            if len(v) == 1:
                self.emit(v)
            else:
                self.literal(v)
        else:
            self.emit(GLYPH[tok])

    def exit_west(self, min_rows: int, top: int) -> int:
        """Finish the block: end heading west, out of the code area.

        Returns the block's last row.  A block always starts eastbound, so it
        needs at least one drop to reverse — which is why two rows is the floor
        and why ``min_rows`` (three, for the two jog blocks) is affordable.
        """
        while self.dx > 0 or self.y - top + 1 < min_rows:
            self.newline()
        while self.x >= CW0:
            self.c.set(self.x, self.y, " ")
            self.x -= 1
        return self.y


# ── laying the worker out ─────────────────────────────────────────────────────
class _Block:
    __slots__ = ("name", "r0", "r1", "succ")

    def __init__(self, name: str, r0: int, r1: int, succ: object) -> None:
        self.name, self.r0, self.r1, self.succ = name, r0, r1, succ


def _snapshot(c: Circuit):
    return dict(c.cell), set(c.reserved)


def _restore(c: Circuit, snap) -> None:
    c.cell, c.reserved = dict(snap[0]), set(snap[1])


def _wire(c: Circuit, start: tuple[int, int], heading, col: int, row: int) -> None:
    """Route one lane from ``start`` (already heading ``heading``) to ``row``.

    The wire runs vertically down ``col``, turns east at the target's entry row
    and merges into its ``>``.  Crossing another wire is free — both are blank —
    and crossing an earlier wire's ``>`` while heading east is a genuine merge,
    which is why candidate columns are tried from the east inward.
    """
    sx, sy = start
    corners: list[tuple[int, int]] = []
    if heading in (N, S):
        if col != sx:
            raise Collision("a vertical lane must keep its own column")
        corners.append((col, row))
    else:
        corners.append((col, sy))
        corners.append((col, row))
    c.route(start, heading, corners, (ENTRY_COL, row), E)


def _wire_jog(c: Circuit, start: tuple[int, int], heading, col: int, row: int) -> None:
    """A north lane that has to reach a target *below* it.

    It turns west on the row above the branch — a pure code row, so its channel
    half is empty — drops down a fresh column and comes back east.  Only
    ``MAIN`` and ``ROTPRE`` need this, and only because ``pos`` and ``neg``
    share a successor.
    """
    sx, sy = start
    if heading is not N:
        raise Collision("the jog shape is only defined for a north lane")
    c.route(start, N, [(sx, sy), (col, sy), (col, row)], (ENTRY_COL, row), E)


def worker() -> tuple[Circuit, list[_Block], DebugMap]:
    """The whole worker room: every block, every lane, every binding asserted."""
    supers, succ = _superblocks()
    _check_order(succ)
    c = Circuit(IW, 4096, strict_corridors=True)
    backticks: dict[int, int] = {}
    p = Placer(c, backticks)

    # ── pass 1: pour the code, one block per row run ─────────────────────────
    blocks: list[_Block] = []
    row = 0
    for chain in ORDER:
        name = chain[0]
        toks = list(supers[name])
        if isinstance(succ[name], dict):
            toks.pop()  # the branch glyph lives in the channel
        p.start(row)
        for tok in toks:
            p.token(tok)
        # A block always enters eastbound and always leaves westbound, so it
        # occupies an *even* number of rows.  The jog blocks need their north
        # lane to turn west on a row whose channel is empty, which the entry row
        # never is, so those two get four.
        r1 = p.exit_west(3 if name in JOG_POS else 2, row)
        blocks.append(_Block(name, row, r1, succ[name]))
        # A ``neg`` lane that halts needs an ``H`` one row below the branch, and
        # that row has to belong to *this* block: on the next block's entry row
        # it would sit squarely across the eastbound merge that reaches it.
        row = r1 + 1 + (isinstance(succ[name], dict) and "HALT" in succ[name].values())
    height = row

    # the spawn: the man appears facing east and walks into INIT's entry `>`
    c.set(0, blocks[0].r0, "@")
    for x in range(1, ENTRY_COL):
        c.set(x, blocks[0].r0, " ")

    # ── pass 2: route every lane ─────────────────────────────────────────────
    entry = {b.name: b.r0 for b in blocks}
    for b in blocks:
        if isinstance(b.succ, str):
            _route_plain(c, b, entry[b.succ])
        else:
            _route_branch(c, b, entry, supers[b.name][-1])

    c.h = height
    return c, blocks, _debug(blocks, height)


def _route_plain(c: Circuit, b: _Block, target_row: int) -> None:
    """A single-successor block: walk west, turn, drop, merge into the entry."""
    for col in range(ENTRY_COL, -1, -1):
        snap = _snapshot(c)
        try:
            _wire(c, (ENTRY_COL, b.r1), W, col, target_row)
            return
        except Collision:
            _restore(c, snap)
    raise Collision(f"{b.name}: no channel column reaches row {target_row}")


def _route_branch(c: Circuit, b: _Block, entry: dict[str, int], tok: str) -> None:
    dirs = LANE_DIR[tok]
    for cb in range(ENTRY_COL - 1, 0, -1):
        snap = _snapshot(c)
        try:
            c.set(cb, b.r1, tok)
            # the man walks the channel from the code area to the branch: those
            # cells are transit, so nothing may later be placed on them
            for x in range(cb + 1, ENTRY_COL + 1):
                if c.get(x, b.r1) != " ":
                    raise Collision(f"{b.name}: {c.get(x, b.r1)!r} on the walk to the branch")
                c.set(x, b.r1, " ")
                c.reserved.add((x, b.r1))
            # Two lanes of one branch can share a successor, and then the second
            # wire has to merge into the first one's ``>`` while heading east —
            # which only works if the *easternmost* turn column is claimed
            # first.  N and S own ``c_b``; the jog and the straight lane are
            # always west of it, so that is the order.
            def _rank(item: tuple[str, tuple[int, int]]) -> int:
                d = item[1]
                if d is N:
                    return 2 if b.name in JOG_POS else 0
                return 1 if d is S else 3

            for lane, d in sorted(dirs.items(), key=_rank):
                target = b.succ[lane]
                if target == "HALT":
                    if d is not S:
                        raise Collision("the halt lane has to leave southward")
                    c.set(cb, b.r1 + 1, "H")
                    continue
                row = entry[target]
                if d is N and row > b.r1:
                    _jog(c, b, cb, row)
                elif d is N:
                    _try_cols(c, (cb, b.r1 - 1), N, cb, row)
                elif d is S:
                    if row <= b.r1:
                        raise Collision(f"{b.name}.{lane}: south lane wants row {row}")
                    _try_cols(c, (cb, b.r1 + 1), S, cb, row)
                else:
                    _try_cols(c, (cb - 1, b.r1), W, None, row)
            return
        except Collision:
            _restore(c, snap)
    raise Collision(f"{b.name}: no branch column works")


def _try_cols(c: Circuit, start, heading, col: int | None, row: int) -> None:
    cols = [col] if col is not None else list(range(start[0], -1, -1))
    for cc in cols:
        snap = _snapshot(c)
        try:
            _wire(c, start, heading, cc, row)
            return
        except Collision:
            _restore(c, snap)
    raise Collision(f"lane from {start} to row {row} has no free column")


def _jog(c: Circuit, b: _Block, cb: int, row: int) -> None:
    for cc in range(cb - 1, -1, -1):
        snap = _snapshot(c)
        try:
            _wire_jog(c, (cb, b.r1 - 1), N, cc, row)
            return
        except Collision:
            _restore(c, snap)
    raise Collision(f"{b.name}: the pos jog has no free column")


def _check_order(succ: dict[str, object]) -> None:
    """Every branch lane's direction has to agree with where its target sits."""
    pos = {chain[0]: i for i, chain in enumerate(ORDER)}
    for name, s in succ.items():
        if not isinstance(s, dict):
            continue
        toks = prog.build()[[n for n in ORDER if n[0] == name][0][-1]][0]
        dirs = LANE_DIR[toks[-1]]
        for lane, d in dirs.items():
            target = s[lane]
            if target == "HALT":
                if d is not S:
                    raise Collision(f"{name}: HALT must be the south lane")
                continue
            if d is N and pos[target] > pos[name] and name not in JOG_POS:
                raise Collision(f"{name}.{lane}: north lane cannot reach {target} below")
            if d is S and pos[target] <= pos[name]:
                raise Collision(f"{name}.{lane}: south lane cannot reach {target} above")


# ── the north band and the whole grid ─────────────────────────────────────────
#: Relay interiors.  The ring's is wider because it turns 18 words a lap: 14
#: gives 13 ``r``/``s`` pairs per 32-cell walking cycle, 2.46 ticks a word.
#: Sixteen is the widest relay that fits before the G room without moving any
#: anchor or widening the scored box.
RING_RELAY_W, AUX_RELAY_W = 16, 10
#: Rows the relay rooms occupy (top wall .. bottom wall).  All three sit flush
#: at the top of the band, which is only eight rows deep in total: the band is
#: pure overhead on a dimension that gets squared, so the ring buys its 20 cells
#: of capacity from two horizontal jogs instead of from ten rows of descent.
RELAY_ROWS = (0, 3)


def build() -> tuple[list[str], DebugMap]:
    """Worker + north band + panel block, as grid rows and a debug sidecar."""
    w, blocks, dbg = worker()
    ih = w.h
    panel_x = WX + IW + 2
    grid_w = panel_x + BLOCK_W
    grid_h = max(BLOCK_H, WY + ih + 1)
    g = Circuit(grid_w, grid_h)

    stamp(g, WX, WY, w.rows()[:ih])
    walls(g, WX, WY, IW, ih)

    wall_row = WY - 2  # the row every pipe touches the worker's north wall from
    a = {k: WX + v for k, v in SEND_ANCHOR.items()}
    r = {k: WX + v for k, v in RECV_ANCHOR.items()}

    below = RELAY_ROWS[1] + 1  # the row a relay's pipes attach from
    in_x = r["I"] - 1
    relay_x: dict[str, int] = {}
    right = in_x - 2
    for band in ("F", "G"):
        relay_x[band] = right - AUX_RELAY_W - 1
        right = relay_x[band] - 1
    # ring: 20 cells of capacity out of an eight-row band.  The forward pipe
    # steps two rows up and runs west under the relay; the return drops from the
    # relay's far end, runs east one row above the wall and turns down into its
    # own anchor.  The two share no row, and the return has to arrive at the
    # wall from the *north* — a terminal arrowhead's forward cell must be a room
    # border, and the forward pipe is sitting one column west of it.
    ring_x = right - RING_RELAY_W - 1
    stamp(g, ring_x, RELAY_ROWS[0], flat_relay(RING_RELAY_W))
    n_fwd = draw_pipe(g, [
        (a["R"], wall_row), (a["R"], wall_row - 2),
        (a["R"] - 5, wall_row - 2), (a["R"] - 5, below),
    ])
    n_ret = draw_pipe(g, [
        (ring_x + RING_RELAY_W - 1, below), (ring_x + RING_RELAY_W - 1, wall_row - 1),
        (r["R"], wall_row - 1), (r["R"], wall_row),
    ])
    if n_fwd + n_ret < RING_CELLS:
        raise Collision(f"ring holds {n_fwd + n_ret} cells, need >= {RING_CELLS}")

    aux = []
    for band, need in (("G", SCRATCH_CELLS), ("F", FIFO_CELLS)):
        rx = relay_x[band]
        stamp(g, rx, RELAY_ROWS[0], flat_relay(AUX_RELAY_W))
        f = draw_pipe(g, [(a[band], wall_row), (a[band], below)])
        t = draw_pipe(g, [(r[band], below), (r[band], wall_row)])
        if f + t < need:
            raise Collision(f"{band} loop holds {f + t} cells, need >= {need}")
        aux.append((band, rx, f + t))

    # input room: west of the ri anchor, its pipe stepping across into it
    stamp(g, in_x - 1, wall_row - 5, ["+-+", "|I|", "+-+"])
    draw_pipe(g, [(in_x, wall_row - 2), (in_x, wall_row - 1),
                  (r["I"], wall_row - 1), (r["I"], wall_row)])

    # the panel block, and the painter pipe climbing the sp anchor to reach it
    info = build_block(g, panel_x, 0)
    px, py = info["in_cell"]
    draw_pipe(g, [(a["P"], wall_row), (a["P"], py), (px, py)])

    dbg = dbg.translated(WX, WY)
    dbg.region("worker", WX - 1, WY - 1, IW + 2, ih + 2,
               note=f"{IW}x{ih} interior; west channel {NCHW} cols, code {CODEW}",
               color="#334155")
    depth = RELAY_ROWS[1] - RELAY_ROWS[0] + 1
    dbg.region("ring relay", ring_x, RELAY_ROWS[0], RING_RELAY_W + 2, depth,
               note=f"{n_fwd + n_ret} ring cells (need {RING_CELLS})", color="#0ea5e9")
    for band, rx, cells in aux:
        dbg.region(f"{band} relay", rx, RELAY_ROWS[0], AUX_RELAY_W + 2, depth,
                   note=f"{cells} cells", color="#22c55e")
    dbg.region("input", in_x - 1, wall_row - 5, 3, 3, note="program input", color="#64748b")
    dbg.region("panel block", panel_x, 0, BLOCK_W, BLOCK_H,
               note="painter + 16x16 LM-75 + ADDR/DATA/SWAP", color="#ec4899")
    for band, col in SEND_ANCHOR.items():
        lo, hi = band_span(band, "s")
        dbg.region(f"s{band}", WX + lo, WY, hi - lo + 1, ih,
                   note=f"anchor col {WX + col}", color="#f97316")

    rows = [row.rstrip() for row in g.rows()]
    while rows and not rows[-1]:
        rows.pop()
    return rows, dbg


def _debug(blocks: list[_Block], height: int) -> DebugMap:
    d = DebugMap(f"pathfinder — band grid, {IW}x{height} worker")
    d.region("west channel", 0, 0, NCHW, height,
             note="wires only; column NCHW-1 carries every block entry", color="#475569")
    for b in blocks:
        d.region(b.name, CW0, b.r0, CODEW, b.r1 - b.r0 + 1,
                 note=f"rows {b.r0}-{b.r1}", color="#3b82f6")
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write a labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args(argv)
    rows, dbg = build()
    w = max(len(r) for r in rows)
    print(f"# {w}x{len(rows)}  area2={max(w, len(rows)) ** 2}", file=sys.stderr)
    if args.man:
        args.man.write_text("\n".join(rows) + "\n")
    if args.html:
        dbg.write_html(rows, args.html)
    if args.json:
        dbg.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
