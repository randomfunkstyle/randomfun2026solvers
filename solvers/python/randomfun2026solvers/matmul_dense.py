#!/usr/bin/env python3
"""`matmul` laid dense: one worker room, packed chains, rectangle loops.

:mod:`matmul_grid` compiles the same 32-block CFG into 83x98 and 31,553 ticks.
Rendered (``man_png``) that grid is one 56x83 void with block fragments scattered
through it -- **3.7% dense**, 8,624 cells holding 316 glyphs -- and both numbers
have the same cause: the pen gives every chain a full-width row of its own and
every routed edge a row of its own, so height counts *direction changes*, not
glyphs, and the man walks the blanks between the bands on every one of them.

Three changes, in the order they pay:

**Chains are boxes, and boxes share rows.**  A chain's ops only reach the bands
it actually uses, so its box is only as wide as that span -- `ROW` is three
columns, `MAC` is eight -- and two chains whose spans do not overlap sit on the
same rows.  The pen wraps inside its own box, so a wrap costs the box's width
instead of the room's.

**A counted loop is a rectangle, not a row plus a return corridor.**  A closed
grid walk has even length and four turns, so a 12-glyph body costs **16 ticks**
as a `w`x2 rectangle against 26 as a row whose return leg walks back empty --
and `MAC` runs 276 times on the mean public case, half of every tick spent.  The
`d` sits on a corner: BP>0 turns clockwise and stays in the loop, BP=0 goes
straight and leaves it.

**Every anchor is placed for the ops that use it, not paired with its ring.**  A
ring's two pipes are two independent nearest-sets, so `sx` can attach beside
`io` -- where ``LOADA_GO`` writes it -- while `rx` attaches beside `s`, where
``TBODY`` reads it.  The ring is then a long pipe between the two, which is
exactly what a ring wants: pipe cells *are* its storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from randomfun2026solvers.circuit import Collision, E, N, S, W
from randomfun2026solvers.matmul_grid import (
    LAID,
    SELF_LOOPS,
    band_of,
    items,
)

__all__ = [
    "ANCHORS",
    "Bands",
    "Box",
    "Weave",
    "bands",
    "block_span",
    "chain_span",
    "lay_chain",
    "rect_loop",
]

# ── where the fourteen pipes attach ───────────────────────────────────────────
#
# All fourteen sit on the north wall, so the Manhattan distance from any cell to
# any of them is ``|x - col| + y + 1``: the `y` term is shared and "nearest pipe"
# collapses to *nearest column* at every row.  Ties are excluded rather than
# resolved -- they break by reading order over pipe *segments*, which is far too
# easy to get wrong by accident -- so consecutive sites in one partition are
# always an odd number of columns apart.
#
# The six columns 13..18 are `MAC`'s own: with `sb` at 13, `rc` at 14, `rb` at
# 16, `sc` at 17 and `s` straddling 18/19, the twelve-glyph body closes as an
# 8x2 rectangle whose every pipe op lands in its own band with nothing skipped.
IW = 24
ANCHORS: dict[tuple[str, bool], int] = {
    #  (ring, sending) -> north-wall column
    ("q", True): 1,   ("q", False): 3,
    ("x", True): 6,   ("x", False): 21,      # split: `sx` by `io`, `rx` by `s`
    ("io", True): 9,  ("io", False): 8,
    ("k", True): 12,  ("k", False): 13,
    ("b", True): 15,  ("b", False): 17,
    ("c", True): 18,  ("c", False): 16,
    ("s", True): 19,  ("s", False): 20,
}

#: The two columns kept clear of glyphs.  A row that ends on the band at the
#: very edge of a box has nowhere to turn, so the outermost column each side is
#: reserved for turns and for the corridors between boxes.
MARGIN_COLS = (0, IW - 1)


@dataclass(frozen=True)
class Bands:
    """Column -> ring, once for receives and once for sends."""

    recv: dict[int, str]
    send: dict[int, str]
    iw: int

    def ring_at(self, x: int, sending: bool) -> str | None:
        return (self.send if sending else self.recv).get(x)

    def span(self, ring: str, sending: bool) -> tuple[int, int]:
        cols = [x for x, r in (self.send if sending else self.recv).items() if r == ring]
        if not cols:
            raise Collision(f"{ring} {'send' if sending else 'recv'} owns no column")
        return min(cols), max(cols)


def bands(anchors: dict[tuple[str, bool], int] = None, iw: int = IW) -> Bands:
    """Voronoi over the attach columns, ties dropped, one partition per direction."""
    anchors = anchors or ANCHORS
    if len(set(anchors.values())) != len(anchors):
        raise Collision("two pipes want the same wall cell")
    out: list[dict[int, str]] = []
    for sending in (False, True):
        sites = {r: c for (r, s), c in anchors.items() if s == sending}
        cells: dict[int, str] = {}
        for x in range(iw):
            if x in MARGIN_COLS:
                continue
            d = sorted((abs(x - c), r) for r, c in sites.items())
            if len(d) < 2 or d[0][0] != d[1][0]:
                cells[x] = d[0][1]
        out.append(cells)
    return Bands(out[0], out[1], iw)


# ── one chain, laid inside its own box ────────────────────────────────────────
@dataclass
class Box:
    """A fall-through chain drawn into its own rectangle of the room."""

    blocks: list[str]
    lo: int                                    # westmost column it needs
    hi: int                                    # eastmost column it needs
    cells: dict[tuple[int, int], str] = field(default_factory=dict)
    start: dict[str, tuple[int, int]] = field(default_factory=dict)
    facing: dict[str, tuple[int, int]] = field(default_factory=dict)
    branch: dict[str, tuple[int, int, tuple[int, int]]] = field(default_factory=dict)
    entry: tuple[int, int] = (0, 0)            # cell the man must be steered onto
    entry_dir: tuple[int, int] = E
    exits: dict[str, tuple[tuple[int, int], tuple[int, int]]] = field(default_factory=dict)
    h: int = 0
    y: int = 0                                 # filled in by the packer

    @property
    def w(self) -> int:
        return self.hi - self.lo + 1


def block_span(name: str, b: Bands) -> tuple[int, int] | None:
    """The column range a block's pipe ops force it to reach, or None."""
    lo, hi = b.iw, -1
    for tok in LAID[name][0]:
        ring = band_of(tok)
        if ring is None:
            continue
        a, z = b.span(ring, tok[0] == "s")
        lo, hi = min(lo, a), max(hi, z)
    return None if hi < 0 else (lo, hi)


def chain_span(blocks: list[str], b: Bands) -> tuple[int, int]:
    """The column range a whole chain needs, widened to give the pen elbow room."""
    spans = [s for s in (block_span(n, b) for n in blocks) if s]
    if not spans:
        return 0, 3
    lo = min(a for a, _ in spans)
    hi = max(z for _, z in spans)
    return lo, max(hi, lo + 3)


# ── laying one chain ──────────────────────────────────────────────────────────
_TURN_GLYPH = {E: ">", W: "<", N: "^", S: "v"}


class Weave:
    """A pen that pours a chain into its own box, wrapping inside it.

    The box is only as wide as the bands the chain reaches, so a wrap costs the
    chain's own span rather than the room's -- which is the whole reason two
    chains can share rows at all.
    """

    def __init__(self, lo: int, hi: int, bands_: Bands) -> None:
        self.lo, self.hi, self.b = lo, hi, bands_
        # A wrap needs a cell to turn in, and the last glyph of a row may well
        # sit on the band at the very edge of the span -- so the turn columns
        # are one wider on each side than the glyph columns.
        self.tlo, self.thi = max(0, lo - 1), min(bands_.iw - 1, hi + 1)
        self.cells: dict[tuple[int, int], str] = {}
        self.x, self.y, self.d = lo, 0, E
        self.h = 1
        self.walked: set[tuple[int, int]] = set()

    # -- primitives --------------------------------------------------------
    def _set(self, x: int, y: int, ch: str) -> None:
        if (x, y) in self.cells and self.cells[(x, y)] != ch:
            raise Collision(f"box cell ({x},{y}) holds {self.cells[(x,y)]!r}, not {ch!r}")
        self.cells[(x, y)] = ch
        self.walked.add((x, y))
        self.h = max(self.h, y + 1)

    def put(self, ch: str) -> tuple[int, int]:
        at = (self.x, self.y)
        self._set(self.x, self.y, ch)
        self.x += self.d[0]
        self.y += self.d[1]
        return at

    def wrap(self) -> None:
        """Turn down onto the next row and reverse direction."""
        x = min(max(self.x, self.tlo), self.thi)
        self._set(x, self.y, "v")
        self.d = W if self.d == E else E
        self.y += 1
        self._set(x, self.y, _TURN_GLYPH[self.d])
        self.x = x + self.d[0]

    def room_ahead(self, need: int) -> bool:
        end = self.x + self.d[0] * (need - 1)
        return self.lo <= end <= self.hi

    def seek(self, ring: str, sending: bool) -> None:
        """Move the pen to the next column of `ring`'s band, wrapping if behind."""
        cols = [x for x in range(self.lo, self.hi + 1)
                if self.b.ring_at(x, sending) == ring]
        if not cols:
            raise Collision(f"{ring} has no column in {self.lo}..{self.hi}")
        for _ in range(3):
            ahead = [x for x in cols if (x >= self.x if self.d == E else x <= self.x)]
            if ahead:
                self.x = min(ahead) if self.d == E else max(ahead)
                return
            self.wrap()
        raise Collision(f"cannot reach {ring} from column {self.x}")

    def approach(self, ring: str, sending: bool, lead: int) -> None:
        """Jump ahead so `lead` plain cells from here land just before `ring`.

        Pouring filler where the pen happens to stand and only then looking for
        the band is what makes a pen wrap once per constant: ``BLOAD_DONE``
        builds eight of them and every `sk` would be behind the pen.  Leading
        *into* the band costs nothing -- the cells between bands are blanks the
        man walks either way.
        """
        cols = [x for x in range(self.lo, self.hi + 1)
                if self.b.ring_at(x, sending) == ring]
        if not cols:
            return
        if self.d == E:
            want = min(cols) - lead
            if self.lo <= want and self.x < want:
                self.x = want
        else:
            want = max(cols) + lead
            if want <= self.hi and self.x > want:
                self.x = want

    def reaches(self, ring: str, sending: bool, lead: int) -> bool:
        """Would a band column still lie ahead after `lead` filler cells?"""
        cols = [x for x in range(self.lo, self.hi + 1)
                if self.b.ring_at(x, sending) == ring]
        end = self.x + self.d[0] * lead
        return any(x >= end for x in cols) if self.d == E else any(x <= end for x in cols)

    def go_east(self) -> None:
        """A branch's clockwise lane must leave *south*, which needs an east row."""
        if self.d != E:
            self.wrap()


# ── a counted loop as a rectangle ─────────────────────────────────────────────
def rect_loop(name: str, b: Bands, lo: int, hi: int
              ) -> tuple[dict[tuple[int, int], str], int, int, tuple[int, int], str]:
    """Lay a self-looping block as a `w`x2 rectangle; return cells and geometry.

    A closed grid walk has even length and four corners, so `n` glyphs need a
    perimeter of `2w >= n + 3` -- 16 cells for ``MAC``'s twelve against the 26 a
    row plus an empty return corridor costs.  The `d` sits on the west corner of
    the bottom row: arriving west it turns clockwise to north and stays in the
    loop, and goes straight on west to leave.  That puts the entry one cell west
    of the top row and the exit one cell west of the bottom row -- exactly where
    a pen walking east then wrapping west already is.

    Returns ``(cells, x0, w, first_glyph_cell, form)``; the loop occupies box
    rows 0 and 1 and is *not* written through :class:`Weave`, so the caller
    places it.  ``form`` is ``"west"`` (entry and exit one column west of the
    rectangle) or ``"east"``.
    """
    toks = LAID[name][0]
    slots: list[tuple[str, str | None, bool]] = []
    for tok in toks:
        ring = band_of(tok)
        for kind, payload in items(tok):
            if kind == "lit":
                raise Collision(f"{name}: a literal cannot ride a loop rectangle")
            slots.append((payload, ring, ring is not None and tok[0] == "s"))
    body, brancher = slots[:-1], slots[-1]
    if brancher[0] not in "dxX":
        raise Collision(f"{name} does not end in a branch")

    for w in range((len(slots) + 4) // 2, hi - lo + 2):
        for x0 in range(lo, hi - w + 2):
            for form in ("west", "east"):
                top = [(x, 0) for x in range(x0 + 1, x0 + w - 1)]
                bot = [(x, 1) for x in range(x0 + w - 2, x0, -1)]
                if form == "west":
                    # `d` on the south-west corner: arriving west it turns north
                    # and stays in, or goes straight on west and leaves.  Entry
                    # and exit both sit one column west, which is exactly where
                    # a pen walking east and then wrapping west already stands.
                    cells = {(x0, 0): ">", (x0 + w - 1, 0): "v",
                             (x0 + w - 1, 1): "<", (x0, 1): brancher[0]}
                    order = top + bot
                else:
                    # `d` on the north-east corner: the body then runs west along
                    # the bottom row first, which is the only way ``MAC`` closes
                    # -- it reads `s` before it reads `b` before it reads `c`,
                    # and those bands lie east to west in that order.
                    cells = {(x0, 0): ">", (x0 + w - 1, 0): brancher[0],
                             (x0 + w - 1, 1): "<", (x0, 1): "^"}
                    order = bot + top
                i = 0
                first: tuple[int, int] | None = None
                for cell in order:
                    if i >= len(body):
                        break
                    glyph, ring, send = body[i]
                    if ring is not None and b.ring_at(cell[0], send) != ring:
                        continue
                    cells[cell] = glyph
                    if first is None:
                        first = cell
                    i += 1
                if i == len(body):
                    return cells, x0, w, first or (x0, 1), form
    raise Collision(f"{name} does not close as a rectangle in {lo}..{hi}")


# ── a chain, block by block ───────────────────────────────────────────────────
@dataclass
class Lane:
    """One control edge that has to be routed: where it leaves and where to."""

    src: str
    key: str
    target: str
    cell: tuple[int, int]
    d: tuple[int, int]


def lay_chain(blocks: list[str], b: Bands) -> Box:
    """Pour a fall-through chain into its own box."""
    lo, hi = chain_span(blocks, b)
    box = Box(list(blocks), lo, hi)
    w = Weave(lo, hi, b)
    pending: list[Lane] = []

    for name in blocks:
        toks, succ = LAID[name][0], LAID[name][1]
        branch_i = len(toks) - 1 if isinstance(succ, dict) else None

        if name in SELF_LOOPS:
            cells, x0, rw, first, form = rect_loop(name, b, lo, hi)
            if form != "west":
                raise Collision(f"{name} needs an east-entry rectangle; "
                                "its chain has to be placed by hand")
            # the pen must stand one west of the rectangle, heading east
            if w.d != E:
                w.wrap()
            if w.x > x0 - 1:
                w.wrap()
            top = w.y
            w.x = x0 - 1
            for (cx, cy), ch in cells.items():
                w._set(cx, top + cy, ch)
            for yy in (top, top + 1):
                w._set(x0 - 1, yy, " ")
            box.start[name] = (first[0], top + first[1])
            box.facing[name] = E if first[1] == 0 else W
            box.branch[name] = (x0, top + 1, W)
            w.x, w.y, w.d = x0 - 2, top + 1, W
            w.h = max(w.h, top + 2)
            continue

        flat = [(i, tok, band_of(tok), kind, payload)
                for i, tok in enumerate(toks)
                for kind, payload in items(tok)]
        for j, (i, tok, ring, kind, payload) in enumerate(flat):
            if True:
                if ring is None:
                    lead, m = 0, j
                    while m < len(flat) and flat[m][2] is None:
                        lead += 1 if flat[m][3] == "g" else len(flat[m][4]) + 2
                        m += 1
                    if m < len(flat) and flat[m][0] != branch_i:
                        w.approach(flat[m][2], flat[m][1][0] == "s", lead)
                if kind == "lit":
                    need = len(payload) + 2
                    if not w.room_ahead(need):
                        w.wrap()
                    w.put("`")
                    for ch in payload:
                        w.put(ch)
                    at = w.put("`")
                else:
                    if i == branch_i:
                        w.go_east()
                    if ring is not None:
                        w.seek(ring, tok[0] == "s")
                    elif not w.room_ahead(2):
                        w.wrap()
                    at = w.put(payload)
                if name not in box.start:
                    box.start[name] = at
                    box.facing[name] = w.d
            if i == branch_i:
                box.branch[name] = (at[0], at[1], w.d)
    box.cells = w.cells
    box.h = w.h
    box.entry = box.start[blocks[0]]
    box.entry_dir = box.facing[blocks[0]]
    box.exits["_pen"] = ((w.x, w.y), w.d)
    del pending
    return box
