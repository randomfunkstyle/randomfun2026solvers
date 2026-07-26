#!/usr/bin/env python3
"""Compile :data:`matmul_cfg.WORKER` -- a 32-block token CFG -- into one room.

The CFG is proven at op level in :mod:`matmul_cfg` (4.0 cells a MAC, mean 6,569
cells over the seven public cases against the CPU build's 153,786 ticks).  This
module is only the *layout*: it turns those tokens into glyphs, gives every pipe
op a column that binds to the pipe it means, and routes the control edges.

Measured: **88x98, 31,553 ticks a case, score 3.03e8** against the CPU build's
85x86 and 153,786 ticks (1.14e9) -- 3.8x better, from 4.9x the ticks and 1.3x
the area.

## Fourteen pipes on the north wall, so binding is one-dimensional

Every pipe attaches to the worker's **north** wall, so the Manhattan distance
from a cell to any of them is ``|x - col| + y + 1``: the ``y`` term is shared and
"nearest pipe" collapses to *nearest column* at every row (``tcp_ring``'s rule).
Receives and sends are two independent nearest-sets, so a ring's two columns may
sit **next to each other** and one turnaround room straddles both.  The room is
cut into seven bands (`io` plus the six rings), and each band's usable span is
computed by brute force with ties excluded -- a tie breaks by reading order over
pipe *segments*, which is far too easy to get wrong by accident.

Where the bands go is searched, not guessed, and against the contest's own
objective rather than the room's height: see :mod:`matmul_geom_search`.  The hot
loop's cost is the *spread* of `s`, `b`, `c`, not the twelve glyphs it runs.

## Fall-through is a pen wrap, so a chain costs no routing

Blocks are grouped into maximal fall-through **chains** -- 32 blocks into 17 --
and inside a chain the successor simply continues along the same walked row (a
branch's straight lane runs on east past the branch glyph) or wraps to the next.
That leaves 28 edges to route against the CFG's 47, and only a chain *head*
needs an entry cell.  A self-loop stays inside its chain: its return leg turns up
into the blank column just west of its own first glyph, and a `>` met while
already heading east is a no-op.

Corridors take any clear column in the room, not a margin: two walks may **cross
at a blank** and conflict only where one of them turns, so the sets of turned and
of vertically-walked cells are both tracked and checked.

Three lanes never fire and are not drawn: ``BROW``, ``ROW`` and ``TTAIL`` test a
counter that is stepped down to zero and stops there, so their ``neg`` lanes are
dead -- proven over every shape in 2..16 in ``tests/test_matmul_grid.py``.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from randomfun2026solvers.circuit import CCW, CW, E, N, S, W, Circuit, Collision
from randomfun2026solvers.matmul_cfg import WORKER

__all__ = [
    "BANDS",
    "Bands",
    "Chain",
    "Geometry",
    "Plan",
    "Room",
    "build_grid",
    "build_room",
    "chains_of",
    "check_room",
    "estimate_ticks",
    "plan",
    "public_traces",
    "trace",
]

#: The rings, plus `io` for the input/output pair.
BANDS: tuple[str, ...] = ("io", "x", "k", "s", "b", "c", "q")


@dataclass(frozen=True)
class Geometry:
    """Where the fourteen pipes attach: one ordering and width per direction."""

    recv_order: tuple[str, ...]
    send_order: tuple[str, ...]
    recv_w: dict[str, int]
    send_w: dict[str, int]

    @property
    def width(self) -> int:
        return sum(self.recv_w.values())


#: Found by :mod:`randomfun2026solvers.matmul_geom_search`, whose objective is
#: the contest's own -- ``max(w, h)^2 * ticks`` -- rather than the room's height.
#: `s`, `b`, `c` come out adjacent and as narrow as the rule allows, because
#: ``MAC`` reads them in that order 1,536 times at full size and the man walks
#: every blank between two bands: the hot loop's cost is the *spread* of the
#: three, not the twelve glyphs it executes.
_W = {"q": 11, "k": 11, "io": 4, "s": 7, "b": 4, "c": 8, "x": 10}
GEOMETRY = Geometry(
    recv_order=("q", "k", "io", "s", "b", "c", "x"),
    send_order=("q", "k", "io", "s", "b", "c", "x"),
    recv_w=_W,
    send_w=dict(_W),
)

#: Blocks whose only remaining successor is themselves; laid as a tight loop
#: whose return leg merges into the block's own entry cell.
SELF_LOOPS = ("LOADA_GO", "CFILL_GO", "MAC")

#: Lanes the machine can never take: the counter each block tests is stepped
#: down to zero and stops there.  Proven by simulation in the tests.
DEAD_LANES = {("BROW", "neg"), ("ROW", "neg"), ("TTAIL", "neg")}

#: Multi-digit literals, rewritten as small-digit arithmetic.
#:
#: A literal wider than one digit needs a backtick pair, and backticks pair on
#: **columns** as well as rows -- so every one of them has to own a column of the
#: room outright, and the ten in this CFG would spend twenty.  Each is instead
#: built from single-digit glyphs, which cost the same three or four cells and no
#: columns at all.  Every one is immediately followed by `M`, so the `B` the
#: rewrite clobbers is dead (asserted in :func:`expand`).
LITERALS: dict[str, list[str]] = {
    "L21": ["L3", "M", "L7", "*"],           # 21 = 3*7
    "L42": ["L6", "M", "L7", "*"],           # 42 = 6*7
    "L19": ["L2", "M", "L9", "*", "M", "L1", "+"],   # 19 = 2*9+1
}


def expand(toks: list[str]) -> list[str]:
    """A block's tokens with every multi-digit literal rewritten."""
    out: list[str] = []
    for i, tok in enumerate(toks):
        if tok in LITERALS:
            if toks[i + 1] != "M":
                raise Collision(f"{tok} is not followed by M; the rewrite clobbers B")
            out += LITERALS[tok]
        elif tok.startswith("L") and tok != "L" and len(tok) > 2:
            raise Collision(f"{tok} has no single-digit rewrite")
        else:
            out.append(tok)
    return out


#: The CFG as laid: same blocks and edges, literals in single digits.
LAID: dict[str, tuple[list[str], dict[str, str] | str]] = {
    name: (expand(toks), succ) for name, (toks, succ) in WORKER.items()
}

#: Ring letter -> the band its ops stand in.  Two pipes share a band when one of
#: them is only ever *sent* to and the other only ever *received* from: sends and
#: receives are independent nearest-sets, so they cost one band between them.
RINGS: dict[str, str] = {c: c for c in "xbcsqk"}

#: The block the man starts in.  Its chain is laid first and gets the ``@``.
ENTRY = "HEAD"


@dataclass(frozen=True)
class Spec:
    """One man's CFG and the vocabulary his room is cut into bands for.

    The compiler below reads five module globals.  A second man needs all five
    changed together and nothing else, so they are bundled here and swapped by
    :func:`use` -- which keeps the one-man build's globals as the default and
    lets :mod:`matmul_pair_grid` compile two rooms out of the same code.
    """

    laid: dict[str, tuple[list[str], dict[str, str] | str]]
    rings: dict[str, str]
    self_loops: tuple[str, ...]
    dead_lanes: frozenset[tuple[str, str]]
    entry: str = "HEAD"

    @classmethod
    def of(cls, worker: dict, rings: dict[str, str], entry: str,
           self_loops: tuple[str, ...],
           dead_lanes: frozenset[tuple[str, str]] = frozenset()) -> "Spec":
        laid = {n: (expand(t), s) for n, (t, s) in worker.items()}
        return cls(laid, dict(rings), tuple(self_loops), frozenset(dead_lanes), entry)


def use(spec: Spec):
    """Point the compiler at one man's CFG, for the duration of a ``with``."""
    import contextlib

    @contextlib.contextmanager
    def _swap():
        global LAID, RINGS, SELF_LOOPS, DEAD_LANES, ENTRY
        saved = (LAID, RINGS, SELF_LOOPS, DEAD_LANES, ENTRY)
        LAID, RINGS = spec.laid, spec.rings
        SELF_LOOPS, DEAD_LANES, ENTRY = spec.self_loops, set(spec.dead_lanes), spec.entry
        try:
            yield spec
        finally:
            LAID, RINGS, SELF_LOOPS, DEAD_LANES, ENTRY = saved

    return _swap()


# ── tokens ────────────────────────────────────────────────────────────────────
def band_of(tok: str) -> str | None:
    """Which band a token has to stand in, or `None` if it is a plain glyph."""
    if tok in ("ri", "so"):
        return "io"
    if len(tok) == 2 and tok[0] in "rs" and tok[1] in RINGS:
        return RINGS[tok[1]]
    return None


def items(tok: str) -> list[tuple[str, str]]:
    """A token as placeable items: ``('g', glyph)`` or ``('lit', digits)``.

    A pipe token names a *column discipline*, not a glyph: `rq` is a bare `r`.
    Writing the two-character token into the grid would shift every row that
    holds one, and the loader would then blame a block far away.
    """
    if tok.startswith("L") and tok != "L":
        v = tok[1:]
        return [("g", v)] if len(v) == 1 else [("lit", v)]
    b = band_of(tok)
    if b is not None:
        return [("g", "r" if tok[0] == "r" else "s")]
    return [("g", tok)]


def straight_key(glyph: str) -> str | None:
    """Which lane of a branch walks on straight ahead."""
    return {"X": "zero", "d": "zero", "x": None}[glyph]


# ── band geometry ─────────────────────────────────────────────────────────────
def _voronoi(order: tuple[str, ...], widths: dict[str, int], x0: int, shift: int = 0
             ) -> tuple[dict[str, int], dict[str, tuple[int, int]], int]:
    """Attach columns for one direction, and the columns each of them owns.

    Ties are excluded rather than resolved: they break by reading order, which is
    a rule about pipe *segments* and far too easy to get wrong by accident.
    """
    col, x = {}, x0
    for name in order:
        col[name] = x + (widths[name] - 1) // 2 + shift
        x += widths[name]
    x1 = x - 1

    span: dict[str, tuple[int, int]] = {}
    for name in order:
        owned = []
        for px in range(x0, x1 + 1):
            d = sorted((abs(px - c), k) for k, c in col.items())
            if d[0][0] != d[1][0] and d[0][1] == name:
                owned.append(px)
        if not owned or owned != list(range(owned[0], owned[-1] + 1)):
            raise Collision(f"{name!r} owns {owned}, which is empty or split")
        span[name] = (owned[0], owned[-1])
    return col, span, x1


@dataclass(frozen=True)
class Bands:
    """Column geometry: where each pipe attaches and where its ops may stand.

    Receives and sends are **two independent nearest-sets** -- ``r`` ranges over
    the incoming pipes only and ``s`` over the outgoing ones -- so the room is cut
    twice over, and the two cuts need not agree.  That is what lets
    ``ri sq ri sq ri sq`` sit in one row: `io`'s *incoming* cell and `q`'s
    *outgoing* cell can be the same six columns.  Insisting the two partitions
    line up (one band per ring) costs about twenty rows.
    """

    x0: int
    x1: int
    recv_order: tuple[str, ...]
    send_order: tuple[str, ...]
    recv_col: dict[str, int]
    send_col: dict[str, int]
    recv_span: dict[str, tuple[int, int]]
    send_span: dict[str, tuple[int, int]]

    def span(self, tok_band: str, sending: bool) -> tuple[int, int]:
        return (self.send_span if sending else self.recv_span)[tok_band]


def layout_bands(recv_order: tuple[str, ...], send_order: tuple[str, ...],
                 recv_w: dict[str, int], send_w: dict[str, int], x0: int = 0) -> Bands:
    """Place the fourteen north-wall attach columns and derive their cells."""
    if sum(recv_w.values()) != sum(send_w.values()):
        raise Collision("the two partitions must cover the same columns")
    # The receive columns sit one east of the send columns: a ring's two pipes
    # have to be distinct cells, and keeping them adjacent lets one turnaround
    # room straddle both -- which is what makes the rings short.
    rcol, rspan, x1 = _voronoi(recv_order, recv_w, x0, shift=1)
    scol, sspan, _ = _voronoi(send_order, send_w, x0)
    return Bands(x0, x1, recv_order, send_order, rcol, scol, rspan, sspan)


# ── the pen ───────────────────────────────────────────────────────────────────
@dataclass
class Row:
    """One walked row: a direction and the glyphs poured along it."""

    east: bool
    y: int = 0                               # filled in by the room builder
    cells: list[tuple[int, str]] = field(default_factory=list)
    start: int = 0
    end: int = 0                             # the cell the pen stopped on
    skip: int = 1                            # rows to step down when wrapping


class Pen:
    """Pours glyphs along a row, wrapping when the next band lies behind it."""

    def __init__(self, bands: Bands, backticks: set[int], x: int, east: bool = True) -> None:
        self.b = bands
        self.backticks = backticks
        self.rows: list[Row] = [Row(east=east, start=x)]
        self.x = x

    @property
    def row(self) -> Row:
        return self.rows[-1]

    @property
    def step(self) -> int:
        return 1 if self.row.east else -1

    def wrap(self) -> None:
        self.row.end = self.x
        self.rows.append(Row(east=not self.row.east, start=self.x))
        self.x += self.step

    def put(self, glyph: str) -> int:
        if not (self.b.x0 <= self.x <= self.b.x1):
            raise Collision(f"row ran off the code area at column {self.x}")
        at = self.x
        self.row.cells.append((at, glyph))
        self.x += self.step
        return at

    def ensure(self, need: int) -> None:
        if self.row.east:
            if self.x + need > self.b.x1:
                self.wrap()
        elif self.x - need < self.b.x0:
            self.wrap()

    def seek(self, band: str, send: bool) -> None:
        lo, hi = self.b.span(band, send)
        for _ in range(3):
            if lo <= self.x <= hi:
                return
            if self.row.east:
                if self.x < lo:
                    self.x = lo
                    return
            elif self.x > hi:
                self.x = hi
                return
            self.wrap()
        raise Collision(f"cannot reach band {band!r} from column {self.x}")

    def approach(self, band: str, send: bool, lead: int) -> None:
        """Skip ahead so that `lead` plain cells from here land inside `band`.

        Pouring the filler where the pen happens to stand and only then looking
        for the next band is what makes a naive pen wrap once per constant:
        ``BLOAD_DONE`` builds eight of them and every `sk` would be behind the
        pen.  Leading *into* the band costs nothing -- the cells between bands
        are blanks the man walks either way -- and it never wraps on its own, so
        an impossible lead simply leaves the pen where it was.
        """
        lo, hi = self.b.span(band, send)
        if self.row.east:
            want = lo - lead
            if self.b.x0 <= want and self.x < want:
                self.x = want
        else:
            want = hi + lead
            if want <= self.b.x1 and self.x > want:
                self.x = want

    def go_east(self) -> None:
        """Force the pen onto an east-walking row (a south lane needs one)."""
        if not self.row.east:
            self.wrap()

    def restart_east(self, target: int, need: int) -> None:
        """Begin a fresh east-walked row at or west of `target`, `need` columns wide.

        Wrapping alone cannot do this: a wrap keeps the column, and a block that
        has to start back at its first band would find the band behind the pen.
        Turning west first and walking back through blanks is what puts the pen
        where the next row can start.
        """
        if target + need > self.b.x1:
            raise Collision(f"a row of {need} columns does not fit from {target}")
        for _ in range(3):
            if self.row.east:
                self.wrap()
                continue
            self.x = min(self.x, target - 1)
            self.wrap()
            return
        raise Collision("cannot restart a row heading east")

    def literal_span(self, digits: str) -> int:
        """Exactly how many cells `` `ddd` `` will take from here, skips included."""
        x, n = self.x, 0
        while x in self.backticks:
            x += self.step
            n += 1
        x += self.step * (1 + len(digits))
        n += 1 + len(digits)
        while x in self.backticks:
            x += self.step
            n += 1
        return n + 1

    def literal(self, digits: str) -> None:
        """Write `` `ddd` ``, giving each backtick a column of its own.

        Backticks pair on **columns** as well as rows, and a column holding two
        of them turns whatever lies between into a vertical literal -- a load
        error the moment that includes a turn glyph.
        """
        while self.x in self.backticks:
            self.x += self.step
        open_col = self.put("`")
        for ch in digits:
            self.put(ch)
        while self.x in self.backticks:
            self.x += self.step
        close_col = self.put("`")
        self.backticks.update((open_col, close_col))


# ── chains ────────────────────────────────────────────────────────────────────
@dataclass
class Chain:
    """A maximal fall-through run of blocks, laid as one continuous pen walk."""

    blocks: list[str]
    rows: list[Row] = field(default_factory=list)
    entry_col: int = 0                       # column of the first glyph
    entry_row: int = 0                       # index into `rows`
    #: block -> (row index, column) of its branch glyph, for blocks that end in one
    branch_at: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: block -> (row index, column) of its first glyph
    start_at: dict[str, tuple[int, int]] = field(default_factory=dict)


def successors(name: str) -> dict[str, str]:
    """Live successor lanes of a block: ``lane -> target``.

    ``FIN`` halts, dead lanes are dropped, and a self-edge is a loop rather than
    an edge between blocks.
    """
    toks, succ = LAID[name]
    if name == "FIN":
        return {}
    if isinstance(succ, str):
        out = {"straight": succ}
    else:
        out = {k: v for k, v in succ.items() if (name, k) not in DEAD_LANES}
    return {k: v for k, v in out.items() if v != name}


def fallthrough(name: str) -> str | None:
    """The successor that can continue along the walk without being routed."""
    toks, succ = LAID[name]
    if name == "FIN":
        return None
    if isinstance(succ, str):
        return succ if succ != name else None
    key = straight_key(toks[-1])
    tgt = succ.get(key) if key else None
    return tgt if tgt and tgt != name else None


def chains_of() -> list[Chain]:
    """Group the CFG into fall-through chains, entry block first."""
    preds: dict[str, int] = dict.fromkeys(LAID, 0)
    for name in LAID:
        for tgt in successors(name).values():
            preds[tgt] += 1
    link: dict[str, str] = {}
    taken: set[str] = set()
    for name in LAID:
        tgt = fallthrough(name)
        if tgt and preds[tgt] == 1 and tgt not in taken:
            link[name] = tgt
            taken.add(tgt)

    heads = [n for n in LAID if n not in taken]
    heads.sort(key=lambda n: (ENTRY != n, n))
    chains: list[Chain] = []
    for head in heads:
        blocks, cur = [head], head
        while cur in link:
            cur = link[cur]
            blocks.append(cur)
        chains.append(Chain(blocks))
    return chains


# ── planning one chain ────────────────────────────────────────────────────────
@dataclass
class Item:
    """One placeable cell-group of the flattened chain."""

    kind: str                                # 'g' | 'lit'
    payload: str
    band: str | None
    send: bool
    block: str
    last: bool = False                       # the block's branch glyph


def flatten(chain: Chain) -> list[Item]:
    """The chain's blocks as one stream of placeable items."""
    out: list[Item] = []
    for name in chain.blocks:
        toks, succ = LAID[name]
        branch_i = len(toks) - 1 if isinstance(succ, dict) else None
        for i, tok in enumerate(toks):
            zone = band_of(tok)
            send = zone is not None and tok[0] == "s"
            for kind, payload in items(tok):
                out.append(Item(kind, payload, zone, send, name, i == branch_i))
    return out


def _solo_span(name: str, bands: Bands) -> tuple[int, int]:
    """Where a block starts, and how wide it is, laid alone on an east row."""
    pen = Pen(bands, set(), bands.x0, east=True)
    for it in flatten(Chain([name])):
        if it.band is not None:
            pen.seek(it.band, it.send)
        pen.put(it.payload)
    if len(pen.rows) > 1:
        raise Collision(f"{name} cannot be laid on one row")
    at = pen.rows[0].cells[0][0]
    return at, pen.x - at


def plan_chain(chain: Chain, bands: Bands, backticks: set[int]) -> None:
    """Lay the chain's glyphs into walked rows, in place."""
    flat = flatten(chain)
    first = next((it for it in flat if it.band), None)
    start = bands.span(first.band, first.send)[0] if first else bands.x0
    pen = Pen(bands, backticks, start, east=True)

    for i, it in enumerate(flat):
        if it.block in SELF_LOOPS and it.block not in chain.start_at:
            # The loop's return leg comes up the column just west of the block's
            # first glyph and turns east there, so that cell has to be free and
            # the whole block has to fit on one east-walked row.  A `>` in an
            # east walk is a no-op to the man already going east, which is why a
            # self-loop can sit inside a chain at all -- and keeping `MAC` inside
            # its chain is worth a fifth of the ticks.
            at, wide = _solo_span(it.block, bands)
            pen.restart_east(at - 1, wide + 2)
            if pen.row.cells:
                pen.x += pen.step             # the gap the return leg turns in
        if it.band is not None:
            pen.seek(it.band, it.send)
        else:
            # Lead into the next band op rather than trailing away from the last.
            lead, j = 0, i
            while j < len(flat) and flat[j].band is None:
                lead += 1 if flat[j].kind == "g" else len(flat[j].payload) + 2
                j += 1
            if j < len(flat):
                pen.approach(flat[j].band, flat[j].send, lead)
        if it.last:
            # A south lane leaves the cell *below* the branch glyph, which only
            # exists if the row is walked east; and the glyph may not be the
            # row's first cell, since the walk has to reach it.
            pen.go_east()
            if not pen.row.cells:
                pen.put(" ")
        was = (len(pen.rows), len(pen.row.cells))
        if it.kind == "lit":
            pen.ensure(pen.literal_span(it.payload))
            pen.ensure(pen.literal_span(it.payload))
            pen.literal(it.payload)
        else:
            pen.ensure(2)
            col = pen.put(it.payload)
            if it.band is not None:
                lo, hi = bands.span(it.band, it.send)
                if not lo <= col <= hi:
                    raise Collision(f"{it.block}: {it.band} op at column {col} rebinds")
        if it.block not in chain.start_at:
            first = was[1] if len(pen.rows) == was[0] else 0
            chain.start_at[it.block] = (len(pen.rows) - 1, pen.row.cells[first][0])
        if it.last:
            chain.branch_at[it.block] = (len(pen.rows) - 1, pen.row.cells[-1][0])
    pen.row.end = pen.x
    chain.rows = pen.rows
    chain.entry_col = pen.rows[0].cells[0][0] if pen.rows[0].cells else start


@dataclass
class Plan:
    """A whole worker laid into walked rows, before any routing."""

    bands: Bands
    chains: list[Chain]

    @property
    def pen_rows(self) -> int:
        return sum(len(c.rows) for c in self.chains)


def plan(geom: Geometry = None) -> Plan:  # type: ignore[assignment]
    geom = geom or GEOMETRY
    bands = layout_bands(geom.recv_order, geom.send_order, geom.recv_w, geom.send_w)
    backticks: set[int] = set()
    chains = chains_of()
    for chain in chains:
        plan_chain(chain, bands, backticks)
    return Plan(bands, chains)


# ── rows, lanes and channels ──────────────────────────────────────────────────
@dataclass
class Lane:
    """One control edge that could not be a fall-through, and its west run."""

    src: str
    kind: str                                # 'south' | 'straight' | 'loop'
    target: str
    row: int                                 # grid row the run travels west along
    col: int                                 # column the run starts from
    channel: int = 0                         # margin column it turns down/up in


def assign_rows(chains: list[Chain]) -> tuple[list[Lane], dict[str, int], int]:
    """Give every walked row a grid row, reserving the lane rows in between.

    A branch's clockwise lane leaves the cell *below* the branch glyph, so its
    row has to stay clear west of that column; the walk skips it and the pen's
    wrap steps two rows instead of one.  The straight lane of a chain's last
    block already runs on east along its own row, so it costs a row only when
    that row is walked east and has to turn back.
    """
    lanes: list[Lane] = []
    entry_y: dict[str, int] = {}
    y = 0
    for chain in chains:
        entry_y[chain.blocks[0]] = y
        nxt = dict(zip(chain.blocks, chain.blocks[1:]))
        for i, row in enumerate(chain.rows):
            row.y = y
            y += 1
            for name, (ri, bx) in chain.branch_at.items():
                if ri != i:
                    continue
                lane = "loop" if name in SELF_LOOPS else "south"
                cw = successors(name).get("pos")
                if name in SELF_LOOPS:
                    lanes.append(Lane(name, "loop", name, y, bx))
                elif cw is not None:
                    lanes.append(Lane(name, "south", cw, y, bx))
                else:  # pragma: no cover - every branch here has a cw lane
                    continue
                del lane
                y += 1
            row.skip = y - row.y

        tail = chain.blocks[-1]
        straight = {k: v for k, v in successors(tail).items()
                    if k != "pos" and v != nxt.get(tail)}
        if straight:
            (_, target), = straight.items()
            last = chain.rows[-1]
            if last.east:
                lanes.append(Lane(tail, "straight", target, y, last.end))
                y += 1
            else:
                lanes.append(Lane(tail, "straight", target, last.y, last.end))
    return lanes, entry_y, y


def assign_channels(lanes: list[Lane], entry_y: dict[str, int]) -> int:
    """Colour the vertical corridors: one margin column per overlapping group."""
    spans: list[list[tuple[int, int]]] = []
    for lane in lanes:
        if lane.kind == "loop":
            continue                          # closes on its own row; no corridor
        lo, hi = sorted((lane.row, entry_y[lane.target]))
        for ch, taken in enumerate(spans):
            if all(hi + 1 < a or b + 1 < lo for a, b in taken):
                taken.append((lo, hi))
                lane.channel = ch
                break
        else:
            spans.append([(lo, hi)])
            lane.channel = len(spans) - 1
    return len(spans)


# ── drawing the room ──────────────────────────────────────────────────────────
@dataclass
class Room:
    circuit: Circuit
    bands: Bands
    chains: list[Chain]
    lanes: list[Lane]
    entry_y: dict[str, int]
    margin: int
    iw: int
    ih: int

    def pipe_col(self, ring: str, sending: bool) -> int:
        cols = self.bands.send_col if sending else self.bands.recv_col
        return cols[ring] + self.margin

    # -- the walker's view of a room ------------------------------------------
    # `walk_blocks`, `walk_costs` and `check_room` need only these three: the
    # drawn cells, where each block's man starts, and which way he faces.  Any
    # other layout that can answer them -- `matmul_place`'s packed room, say --
    # is priced and proven by exactly the same code, which is the point.
    @property
    def starts(self) -> dict[tuple[int, int], str]:
        """Cell -> the block whose first glyph stands on it."""
        return {(col + self.margin, chain.rows[ri].y): name
                for chain in self.chains
                for name, (ri, col) in chain.start_at.items()}

    def heading(self, name: str) -> tuple[tuple[int, int], tuple[int, int]]:
        """Where a block's man stands at entry, and the way he is facing."""
        for chain in self.chains:
            if name in chain.start_at:
                ri, col = chain.start_at[name]
                row = chain.rows[ri]
                return (col + self.margin, row.y), (E if row.east else W)
        raise KeyError(name)                      # pragma: no cover

    def regions(self):
        """(label, x, y, w, h, note) for the debug overlay, one per chain."""
        for chain in self.chains:
            y0 = self.entry_y[chain.blocks[0]]
            yield (f"chain:{chain.blocks[0]}", self.margin, y0,
                   self.iw - self.margin, chain.rows[-1].y - y0 + 1,
                   " ".join(chain.blocks))


def _walk(c: Circuit, y: int, x_from: int, x_to: int, glyph: str) -> None:
    """Reserve a straight horizontal run, exclusive of both ends."""
    step = 1 if x_to > x_from else -1
    for x in range(x_from + step, x_to, step):
        here = c.get(x, y)
        if here not in (" ", glyph):
            raise Collision(f"lane on row {y} hits {here!r} at column {x}")


def build_room(p: Plan | None = None, *, trim: bool = False) -> Room:
    """Draw the whole worker: glyphs, wrap links, lanes and corridors.

    `trim` drops the channel columns the router never reached.  It defaults off
    so that the shipped `matmul_ring.man` stays byte-for-byte what it was; the
    tuned generator turns it on.
    """
    p = p or plan()
    lanes, entry_y, ih = assign_rows(p.chains)
    # The west margin is sized so that *every* lane could take a corridor of its
    # own there; the router below almost never needs one, but a long span over a
    # busy room has no clear column inside the code and must have a fallback.
    margin = assign_channels(lanes, entry_y) + 1
    iw = margin + p.bands.x1 + 1
    c = Circuit(iw, ih)

    turns: set[tuple[int, int]] = set()       # cells a walk turns in
    vert: set[tuple[int, int]] = set()        # cells a walk crosses north-south
    first_x: dict[str, int] = {}
    first_y: dict[str, int] = {}
    for chain in p.chains:
        for name, (ri, col) in chain.start_at.items():
            first_x[name] = col + margin
            first_y[name] = chain.rows[ri].y

    for i, chain in enumerate(p.chains):
        head = chain.blocks[0]
        c.set(first_x[head] - 1, entry_y[head], "@" if i == 0 else ">")
        for j, row in enumerate(chain.rows):
            for col, glyph in row.cells:
                c.set(col + margin, row.y, glyph)
            if j + 1 < len(chain.rows):
                nxt = chain.rows[j + 1]
                c.set(row.end + margin, row.y, "v")
                for yy in range(row.y + 1, nxt.y):
                    c.set(row.end + margin, yy, " ")
                    vert.add((row.end + margin, yy))
                c.set(row.end + margin, nxt.y, ">" if nxt.east else "<")
                turns.update({(row.end + margin, row.y),
                              (row.end + margin, nxt.y)})

    def clear(y: int, x_from: int, x_to: int) -> bool:
        """Can a corridor walk this row from `x_from` to `x_to`?

        A turn glyph already pointing the way we are walking is a merge, not a
        collision -- two lanes into the same entry share the last stretch of it.
        """
        step = 1 if x_to > x_from else -1
        glyph = ">" if step > 0 else "<"
        return all(c.get(x, y) in (" ", glyph) for x in range(x_from + step, x_to, step))

    def free_col(x: int, y0: int, y1: int) -> bool:
        """Can a corridor run down this column between the two rows?

        Two walks may **cross** at a blank -- one going north, one going east --
        but they may not share it lengthwise, and neither may turn where the
        other passes.  A cell that is walked and holds no glyph is invisible to
        every other check, so both sets are tracked explicitly.
        """
        step = 1 if y1 > y0 else -1
        return all(c.free(x, yy) and (x, yy) not in turns and (x, yy) not in vert
                   for yy in range(y0 + step, y1, step))

    def spare(x: int, y: int) -> bool:
        return c.free(x, y) and (x, y) not in vert

    # Where each lane leaves from, drawn *before* any corridor is routed: a
    # straight lane's drop to its own lane row occupies a column for a row or
    # two, and a corridor that took that column first would be crossed by it
    # without either check noticing.
    starts: dict[int, tuple[int, bool]] = {}
    for i, lane in enumerate(lanes):
        chain = next(ch for ch in p.chains if lane.src in ch.blocks)
        last = chain.rows[-1]
        if lane.kind == "loop":
            starts[i] = (lane.col + margin, True)
            continue
        if lane.kind == "straight" and not last.east:
            starts[i] = (lane.col + margin, False)
            continue
        if lane.kind == "straight":
            start = last.end + margin
            c.set(start, last.y, "v")
            for yy in range(last.y + 1, lane.row):
                if not spare(start, yy):
                    raise Collision(f"{lane.src}: the drop to its lane row is busy")
                c.set(start, yy, " ")
                vert.add((start, yy))
            turns.add((start, last.y))
            starts[i] = (start, True)
        else:
            starts[i] = (lane.col + margin, True)

    for i, lane in enumerate(lanes):
        chain = next(ch for ch in p.chains if lane.src in ch.blocks)
        last = chain.rows[-1]
        start, always = starts[i]
        if lane.kind == "loop":
            # The return leg walks back along the row below and turns up into the
            # cell just west of the block's own first glyph, which the pen left
            # blank for it: a self-loop costs a turn and the walk back, not a
            # corridor, and it is the only reason `MAC` can sit inside a chain.
            stop, home = first_x[lane.src] - 1, first_y[lane.src]
            c.set(lane.col + margin, lane.row, "<")
            _walk(c, lane.row, lane.col + margin, stop, "<")
            c.set(stop, lane.row, "^")
            for yy in range(home + 1, lane.row):
                if not spare(stop, yy):
                    raise Collision(f"{lane.src}: the loop's return column is busy")
                c.set(stop, yy, " ")
                vert.add((stop, yy))
            c.set(stop, home, ">")
            turns.update({(stop, lane.row), (stop, home)})
            continue

        target_y, home = entry_y[lane.target], first_x[lane.target] - 1
        # A corridor only conflicts with another where one of them *turns*: a
        # straight run crosses a blank without noticing it.  So the channel is
        # chosen for shortness, anywhere in the room, and falls back to the west
        # margin only when the direct columns are taken.
        best = None
        for ch in range(iw - 1):
            # Two corridors may share the cell they both turn east in: they are
            # merging into the same entry, and a `>` met while already heading
            # east is a no-op.  Any other collision is refused.
            if ch == start or not spare(ch, lane.row):
                continue
            if not (spare(ch, target_y) or (c.get(ch, target_y) == ">"
                                            and (ch, target_y) in turns)):
                continue
            if not clear(lane.row, start, ch) or not free_col(ch, lane.row, target_y):
                continue
            if ch != home and not clear(target_y, ch, home):
                continue
            cost = abs(start - ch) + abs(ch - home)
            if best is None or cost < best[0]:
                best = (cost, ch)
        if best is None:
            import os
            if os.environ.get("MM_DEBUG"):
                for ch in range(iw - 1):
                    why = []
                    if ch == start: why.append("start")
                    if not c.free(ch, lane.row): why.append(f"row{lane.row}={c.get(ch,lane.row)!r}")
                    if not (c.free(ch, target_y) or (c.get(ch, target_y) == ">" and (ch, target_y) in turns)): why.append(f"trow={c.get(ch,target_y)!r}")
                    if not clear(lane.row, start, ch): why.append("hrun")
                    if not free_col(ch, lane.row, target_y): why.append("vrun")
                    if ch != home and not clear(target_y, ch, home): why.append("erun")
                    print("  ch", ch, why)
            raise Collision(
                f"no corridor for {lane.src}->{lane.target}: row {lane.row} "
                f"col {start} to row {target_y} col {home}")
        ch = best[1]
        lane.channel = ch
        if always or ch > start:
            c.set(start, lane.row, "<" if ch < start else ">")
        for yy in range(min(lane.row, target_y) + 1, max(lane.row, target_y)):
            c.set(ch, yy, " ")
            vert.add((ch, yy))
        c.set(ch, lane.row, "v" if target_y > lane.row else "^")
        c.set(ch, target_y, ">")
        turns.update({(ch, lane.row), (ch, target_y)})
        _walk(c, target_y, ch, home, ">")   # merges are legal; _walk allows them

    room = Room(c, p.bands, p.chains, lanes, entry_y, margin, iw, ih)
    return _trim_west(room) if trim else room


def _trim_west(room: Room) -> Room:
    """Drop the channel columns the router never reached.

    `margin` is sized so that *every* lane could have a corridor column of its
    own, because a long span over a busy room has no clear column inside the
    code and must have a fallback.  The router almost never needs one: five of
    the nine columns hold nothing at all, and each is a column of the whole
    machine, paid for once in `max(w, h)` and again in the square.  Trimming
    them is a pure translation -- the bands, the pipes and the code all move
    west together -- so nothing about the binding changes, and `margin` is the
    only number that has to move with them.
    """
    dead = 0
    while dead < room.margin and all(room.circuit.get(dead, y) == " "
                                     for y in range(room.ih)):
        dead += 1
    if not dead:
        return room
    c = Circuit(room.iw - dead, room.ih)
    for (x, y), ch in room.circuit.cell.items():
        if ch != " ":
            c.set(x - dead, y, ch)
    for lane in room.lanes:
        lane.channel -= dead
    return Room(c, room.bands, room.chains, room.lanes, room.entry_y,
                room.margin - dead, room.iw - dead, room.ih)


# ── how often each block and each lane runs ───────────────────────────────────
def trace(values: list[int]) -> tuple[dict[str, int], dict[tuple[str, str], int], list[int]]:
    """Run the CFG over one case, counting block entries and lanes taken."""
    from collections import Counter, deque

    from randomfun2026solvers.matmul_cfg import _BIN

    inp: deque[int] = deque(values)
    ring = {k: deque() for k in "xbcsqk"}
    out: list[int] = []
    a = b = bp = 0
    runs: Counter[str] = Counter()
    lanes: Counter[tuple[str, str]] = Counter()
    block = "HEAD"
    while True:
        toks, succ = LAID[block]
        runs[block] += 1
        branch = None
        for t in toks:
            if t == "H":
                return dict(runs), dict(lanes), out
            if t.startswith("L") and t != "L":
                a = int(t[1:])
            elif t == "ri":
                a = inp.popleft()
            elif t == "so":
                out.append(a)
            elif t[0] == "r" and t[1:] in ring:
                a = ring[t[1:]].popleft()
            elif t[0] == "s" and t[1:] in ring:
                ring[t[1:]].append(a)
            elif t == "M":
                b = a
            elif t == "W":
                a, b = b, a
            elif t == "N":
                a = -a
            elif t == "/":
                a, b = a // b, a % b
            elif t == "%":
                a = a % b if b else 0
            elif t in _BIN:
                a = _BIN[t](a, b)
            elif t == "b":
                bp = a
            elif t == "m":
                bp -= 1
            elif t == "]":
                bp >>= 1
            elif t == "X":
                branch = "zero" if a == 0 else ("pos" if a > 0 else "neg")
            elif t == "d":
                branch = "pos" if bp > 0 else "zero"
            else:  # pragma: no cover - the token table is closed
                raise AssertionError(t)
        lanes[(block, branch or "straight")] += 1
        block = succ if isinstance(succ, str) else succ[branch]


def public_traces() -> list[tuple[dict[str, int], dict[tuple[str, str], int]]]:
    """Block and lane counts for each of the seven public cases."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    prob = json.loads((root / "tasks/problems/matmul.json").read_text())
    outs = []
    for case in prob["publicTestData"]:
        rnd = case["rounds"][0]
        runs, lanes, _ = trace([int(v) for v in rnd["in"]])
        outs.append((runs, lanes))
    return outs


# ── walking the drawn room, to prove it is the CFG ────────────────────────────
_TURN = {">": E, "<": W, "^": N, "v": S}
_LANE_TURN = {
    "X": {"pos": CW, "neg": CCW, "zero": None},
    "d": {"pos": CW, "zero": None},
    "x": {"one": CW, "zero": CCW},
}


def walk_blocks(room: Room) -> dict[str, tuple[list[str], dict[str, str]]]:
    """Walk the drawn grid and report what each block actually executes.

    Cells that are *walked* but hold no glyph are invisible to every other kind
    of check -- the grid loads, the pipes bind, and the machine computes
    something else -- so the layout is only believable once someone has followed
    the man's feet from every block's first cell.
    """
    c = room.circuit
    start = room.starts
    heading_at = room.heading

    def follow(pos: tuple[int, int], d: tuple[int, int]) -> str:
        """Walk turns and blanks until the man stands on some block's first cell."""
        for _ in range(4 * (room.iw + room.ih)):
            if pos in start:
                return start[pos]
            ch = c.get(*pos)
            if ch in _TURN:
                d = _TURN[ch]
            elif ch != " ":
                raise Collision(f"lane runs into {ch!r} at {pos}")
            pos = (pos[0] + d[0], pos[1] + d[1])
            if not (0 <= pos[0] < room.iw and 0 <= pos[1] < room.ih):
                raise Collision(f"lane leaves the room at {pos}")
        raise Collision(f"lane from {pos} never reaches a block")

    out: dict[str, tuple[list[str], dict[str, str]]] = {}
    for name in LAID:
        pos, d = heading_at(name)
        ops: list[str] = []
        lanes: dict[str, str] = {}
        want = len(_glyphs(name))
        while True:
            ch = c.get(*pos)
            if ch in _TURN:
                d = _TURN[ch]
            elif ch in _LANE_TURN:
                ops.append(ch)
                for lane, turn in _LANE_TURN[ch].items():
                    if (name, lane) in DEAD_LANES:
                        continue     # never taken; nothing is drawn for it
                    nd = turn[d] if turn else d
                    lanes[lane] = follow((pos[0] + nd[0], pos[1] + nd[1]), nd)
                break
            elif ch == "H":
                ops.append(ch)
                break
            elif ch != " ":
                ops.append(ch)
                if len(ops) == want:
                    nd = d
                    lanes["straight"] = follow((pos[0] + nd[0], pos[1] + nd[1]), nd)
                    break
            pos = (pos[0] + d[0], pos[1] + d[1])
            if len(ops) > want:  # pragma: no cover - the walk is bounded above
                raise Collision(f"{name}: walked {len(ops)} glyphs, wanted {want}")
        out[name] = (ops, lanes)
    return out


def _glyphs(name: str) -> list[str]:
    """The glyph run a block compiles to, literals written out in digits."""
    out: list[str] = []
    for tok in LAID[name][0]:
        for kind, payload in items(tok):
            out += list(payload) if kind == "lit" else [payload]
    return out


def walk_costs(room: Room) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """Cells the man walks inside each block, and along each lane out of it.

    The op count is a floor, not a cost: a block's glyphs are spread across the
    bands its pipe ops belong to, and the man walks every blank in between.  This
    is what makes the band *spread* -- not the block's length -- the thing that
    sets the tick count, and why :mod:`matmul_geom_search` prices a geometry by
    walking it rather than by counting rows.
    """
    c = room.circuit
    start = room.starts

    def follow(pos: tuple[int, int], d: tuple[int, int]) -> int:
        for n in range(4 * (room.iw + room.ih)):
            if pos in start:
                return n
            ch = c.get(*pos)
            if ch in _TURN:
                d = _TURN[ch]
            pos = (pos[0] + d[0], pos[1] + d[1])
        raise Collision(f"lane from {pos} never reaches a block")

    body: dict[str, int] = {}
    lane_cost: dict[tuple[str, str], int] = {}
    for name in LAID:
        pos, d = room.heading(name)
        want, ops, steps = len(_glyphs(name)), 0, 0
        while True:
            ch = c.get(*pos)
            if ch in _TURN:
                d = _TURN[ch]
            elif ch in _LANE_TURN:
                ops += 1
                for lane, turn in _LANE_TURN[ch].items():
                    if (name, lane) in DEAD_LANES:
                        continue
                    nd = turn[d] if turn else d
                    lane_cost[(name, lane)] = 1 + follow(
                        (pos[0] + nd[0], pos[1] + nd[1]), nd)
                break
            elif ch == "H":
                break
            elif ch != " ":
                ops += 1
                if ops == want:
                    lane_cost[(name, "straight")] = 1 + follow(
                        (pos[0] + d[0], pos[1] + d[1]), d)
                    break
            pos = (pos[0] + d[0], pos[1] + d[1])
            steps += 1
        body[name] = steps
    return body, lane_cost


def estimate_ticks(room: Room, runs: dict[str, int],
                   lanes: dict[tuple[str, str], int]) -> int:
    """Cells the man walks for one case, given how often each block and lane ran."""
    body, cost = walk_costs(room)
    total = sum(body[name] * n for name, n in runs.items())
    return total + sum(cost.get(key, 0) * n for key, n in lanes.items())


def check_room(room: Room) -> None:
    """Raise unless every block walks its own tokens and lands where it should."""
    walked = walk_blocks(room)
    for name in LAID:
        ops, lanes = walked[name]
        want = _glyphs(name)
        if [o for o in ops if o != " "] != want:
            raise Collision(f"{name} walks {''.join(ops)}, wanted {''.join(want)}")
        for lane, target in successors(name).items():
            if lanes.get(lane) != target:
                raise Collision(f"{name} lane {lane} reaches {lanes.get(lane)}, "
                                f"wanted {target}")
        if name in SELF_LOOPS and lanes.get("pos") != name:
            raise Collision(f"{name} does not loop back to itself")


# ── the whole machine ─────────────────────────────────────────────────────────
#: Words each ring has to hold, measured over every shape in 2..16 by
#: ``tests/test_matmul_grid.py``.  A pipe's capacity is its cell count, and a
#: ring that cannot hold its contents deadlocks *silently*.
RING_WORDS = {"x": 256, "b": 96, "c": 8, "k": 6, "q": 4, "s": 1}

#: Rings whose turnaround room stacks straight above its own band, innermost
#: first.  A level costs two rows and buys two cells of ring, so the order is by
#: how little each ring has to hold -- and `s`, the one-word spill the MAC
#: re-reads every lap, sits closest to the wall where its latency is least.
LEVELS = {"s": 0, "q": 1, "k": 2, "c": 3}

#: Rings too long to stack: their pipes leave north, run east over the worker
#: and coil in the strip beside it, where the height is already paid for.
COILED = ("x", "b")

#: The smallest turnaround room there is: an eight-cell walk carrying one word.
#: A ring must have two rooms -- a pipe may not loop back into its own -- and for
#: the one-word spill ring the room's *latency* is what the MAC pays, so the
#: shortest perimeter wins even though a bigger room would carry more per lap.
RELAY = [
    "+----+",
    "|>@rv|",
    "|^ s<|",
    "+----+",
]
RELAY_W, RELAY_H = 6, 4                      # outside, walls included
STRIP_W = 7                                  # columns each strip block owns
NB = 15                                      # rows of north band above the wall
MARGIN = 3                                   # spare columns west of the code
WX = 1


def _north_rows(bands: Bands, off: int) -> tuple[dict[tuple[str, bool], int], list[str]]:
    """A north-band row and a strip order for every pipe that runs out east.

    Two crossing rules have to hold at once, and they point opposite ways.  A
    pipe climbs from its column on the worker's wall to its own row and then runs
    east, so a run must never pass over a riser that reaches higher than it: rows
    go **west to east** on the worker side.  In the strip the same pipe drops
    from its row to its room, so a run must never pass over a drop that starts
    higher: strip columns go **east to west**.  A ring's send column is one west
    of its receive column, so the two orders agree ring by ring and the strip
    blocks come out in reverse order of the bands.
    """
    pipes = sorted([(bands.send_col[r] + off, (r, True)) for r in (*COILED, "io")]
                   + [(bands.recv_col[r] + off, (r, False)) for r in (*COILED, "io")])
    rows = {key: row for row, (_, key) in enumerate(pipes)}
    order = [r for (r, send) in (k for _, k in pipes) if send]
    return rows, order[::-1]


def build_grid(room=None) -> tuple[list[str], object, dict[str, object]]:
    """Worker room, six rings, and the input and output rooms.

    `room` is any layout answering the walker's questions -- the chain layout
    here, or :class:`matmul_place.PlacedRoom`.  Everything north and east of the
    worker depends only on where the bands attach, which both agree on.
    """
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

    room = room if room is not None else build_room()
    iw, ih, margin = room.iw, room.ih, room.margin
    wy = NB + 1
    off = WX + margin
    es = WX + iw + 3                          # first column of the east strip
    rows, blocks = _north_rows(room.bands, off)
    g = Circuit(es + STRIP_W * len(blocks), wy + ih + 1)

    stamp(g, WX, wy, room.circuit.rows())
    walls(g, WX, wy, iw, ih)
    wall = wy - 1
    top, deep = wy + 1, wy + ih - 5

    def col(ring: str, send: bool) -> int:
        return (room.bands.send_col if send else room.bands.recv_col)[ring] + off

    lengths: dict[str, int] = {}

    # -- the four stacked rings -----------------------------------------------
    for ring, lev in LEVELS.items():
        sc, rc = col(ring, True), col(ring, False)
        if sc + 1 != rc:
            raise Collision(f"{ring}: pipes at {sc} and {rc} are not adjacent")
        box_y = wall - 6 - lev                # top wall of the turnaround room
        stamp(g, sc - 2, box_y, RELAY)
        fwd = draw_pipe(g, [(sc, wall - 1), (sc, box_y + RELAY_H)])
        ret = draw_pipe(g, [(rc, box_y + RELAY_H), (rc, wall - 1)])
        lengths[ring] = fwd + ret
        if fwd + ret < RING_WORDS[ring] + 1:
            raise Collision(f"ring {ring} holds {fwd + ret}, "
                            f"needs {RING_WORDS[ring] + 1}")

    # -- input, output and the two coiled rings, out in the strip -------------
    for i, ring in enumerate(blocks):
        gx = es + i * STRIP_W
        sc, rc = col(ring, True), col(ring, False)
        r_out, r_in = rows[(ring, True)], rows[(ring, False)]
        if ring == "io":
            stamp(g, gx, top, ["+-+", "|I|", "+-+"])
            stamp(g, gx + 3, top, ["+-+", "|O|", "+-+"])
            draw_pipe(g, [(sc, wall - 1), (sc, r_out), (gx + 4, r_out),
                          (gx + 4, top - 1)])
            draw_pipe(g, [(gx + 1, top - 1), (gx + 1, r_in), (rc, r_in),
                          (rc, wall - 1)])
            continue
        # The coil is three columns of the height already paid for beside the
        # worker; the turnaround room sits under it, so both ends of the ring
        # reach the same room without either pipe crossing the other's drop.
        legs = [(sc, wall - 1), (sc, r_out), (gx + 4, r_out)]
        y = top
        for c_off in (4, 3, 2):
            legs += [(gx + c_off, y), (gx + c_off, deep if y == top else top)]
            y = deep if y == top else top
        fwd = draw_pipe(g, [q for j, q in enumerate(legs) if j == 0 or q != legs[j - 1]])
        stamp(g, gx, deep + 1, RELAY)
        ret = draw_pipe(g, [(gx + 1, deep), (gx + 1, r_in), (rc, r_in),
                            (rc, wall - 1)])
        lengths[ring] = fwd + ret
        if fwd + ret < RING_WORDS[ring] + 1:
            raise Collision(f"ring {ring} holds {fwd + ret}, "
                            f"needs {RING_WORDS[ring] + 1}")

    art = [r.rstrip() for r in g.rows()]
    while art and not art[-1]:
        art.pop()

    d = DebugMap("matmul -- a dataflow ring machine")
    d.region("worker", WX, wy, iw, ih, color="#f59e0b",
             note=f"{len(LAID)} blocks laid by {type(room).__name__}")
    for band in BANDS:
        lo = min(room.bands.recv_span[band][0], room.bands.send_span[band][0])
        hi = max(room.bands.recv_span[band][1], room.bands.send_span[band][1])
        d.region(f"band:{band}", WX + margin + lo, wy, hi - lo + 1, ih,
                 color="#1f2937",
                 note=f"{band}: r binds col {room.pipe_col(band, False) + WX}, "
                      f"s binds col {room.pipe_col(band, True) + WX}")
    for ring, n in lengths.items():
        d.region(f"ring:{ring}", col(ring, True) - 2, 0, 6, wy, color="#0ea5e9",
                 note=f"{n} cells, holds {RING_WORDS[ring]} words")
    for label, x, y, w, h, note in room.regions():
        d.region(label, WX + x, wy + y, w, h, tags=["block"],
                 color="#22c55e", note=note)
    info = {"worker": (iw, ih), "rings": lengths,
            "blocks": len(LAID),
            "size": (max(len(r) for r in art), len(art))}
    return art, d, info


if __name__ == "__main__":  # pragma: no cover - the generator's CLI
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--man", "--out", dest="man", type=Path, help="write the grid here")
    ap.add_argument("--html", type=Path, help="write a labelled debug overlay here")
    ap.add_argument("--json", type=Path, help="write the debug region sidecar here")
    args = ap.parse_args()
    grid, dbg, meta = build_grid()
    if args.man:
        args.man.write_text("\n".join(grid) + "\n")
    if args.html:
        dbg.write_html(grid, args.html)
    if args.json:
        dbg.write_json(args.json)
    if not (args.man or args.html or args.json):
        print("\n".join(grid))
    else:
        print(meta)
