#!/usr/bin/env python3
"""Compile :data:`matmul_cfg.WORKER` -- a 32-block token CFG -- into one room.

The CFG is proven at op level in :mod:`matmul_cfg` (4.0 cells a MAC, mean 6,569
cells over the seven public cases against the CPU build's 153,786 ticks).  This
module is only the *layout*: it turns those tokens into glyphs, gives every pipe
op a column that binds to the pipe it means, and routes the control edges.

## Six rings on the north wall, so binding is one-dimensional

Every pipe attaches to the worker's **north** wall, so the Manhattan distance
from a cell to any of them is ``|x - col| + y + 1``: the ``y`` term is shared and
"nearest pipe" collapses to *nearest column* at every row (``tcp_ring``'s rule).
Incoming and outgoing pipes are two independent nearest-sets, so ring `k`'s
receive column and send column may sit **next to each other** -- the room is cut
into seven vertical bands (`io` plus the six rings) and a band's usable span is
where the incoming *and* the outgoing Voronoi cells agree.

Band order is searched, not guessed: :func:`best_order` lays every permutation
and keeps the one that needs the fewest rows.  `s`, `b`, `c` end up adjacent
because ``MAC`` -- 62% of the ticks -- reads them in that order, and a band it
had to come back to would cost a row *inside the hot loop*.

## Fall-through is a pen wrap, so a chain costs no routing

Blocks are grouped into maximal fall-through **chains**: inside a chain the
successor simply continues along the same walked row (a branch's straight lane
runs on east past the branch glyph) or wraps to the next row, exactly as if the
chain were one long block.  Only 17-20 edges are left to route, against 47 in
the CFG, and only a chain *head* needs an entry cell.

Three lanes never fire and are not drawn: ``BROW``, ``ROW`` and ``TTAIL`` test a
counter that is decremented to zero and never below it, so their ``neg`` lanes
are dead.  :func:`randomfun2026solvers.matmul_cfg.simulate` is instrumented in
the tests to prove it over every shape in 2..16.
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
    "Plan",
    "best_order",
    "chains_of",
    "plan_chains",
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


#: Found by :mod:`randomfun2026solvers.matmul_geom_search`; see that module for
#: the objective (walked rows first, then walked cells weighted by how often the
#: block runs).  The two orders differ, which is the whole point of splitting
#: them: `MAC` reads `s`, `b`, `c` in that order and sends them back in the same
#: order, so its receive columns and its send columns interleave.
_W = {"q": 9, "s": 11, "k": 8, "b": 10, "c": 7, "io": 7, "x": 8}
GEOMETRY = Geometry(
    recv_order=("q", "s", "k", "b", "c", "io", "x"),
    send_order=("q", "s", "k", "b", "c", "io", "x"),
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


# ── tokens ────────────────────────────────────────────────────────────────────
def band_of(tok: str) -> str | None:
    """Which band a token has to stand in, or `None` if it is a plain glyph."""
    if tok in ("ri", "so"):
        return "io"
    if len(tok) == 2 and tok[0] in "rs" and tok[1] in "xbcsqk":
        return tok[1]
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
        if name in SELF_LOOPS:
            continue                          # its own chain: the loop closes on its entry
        tgt = fallthrough(name)
        if tgt and tgt not in SELF_LOOPS and preds[tgt] == 1 and tgt not in taken:
            link[name] = tgt
            taken.add(tgt)

    heads = [n for n in LAID if n not in taken]
    heads.sort(key=lambda n: ("HEAD" != n, n))
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


def plan_chain(chain: Chain, bands: Bands, backticks: set[int]) -> None:
    """Lay the chain's glyphs into walked rows, in place."""
    flat = flatten(chain)
    first = next((it for it in flat if it.band), None)
    start = bands.span(first.band, first.send)[0] if first else bands.x0
    pen = Pen(bands, backticks, start, east=True)

    for i, it in enumerate(flat):
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


def _walk(c: Circuit, y: int, x_from: int, x_to: int, glyph: str) -> None:
    """Reserve a straight horizontal run, exclusive of both ends."""
    step = 1 if x_to > x_from else -1
    for x in range(x_from + step, x_to, step):
        here = c.get(x, y)
        if here not in (" ", glyph):
            raise Collision(f"lane on row {y} hits {here!r} at column {x}")


def build_room(p: Plan | None = None) -> Room:
    """Draw the whole worker: glyphs, wrap links, lanes and channels."""
    p = p or plan()
    lanes, entry_y, ih = assign_rows(p.chains)
    channels = assign_channels(lanes, entry_y)
    # One spare column between the corridors and the code: a chain whose first
    # glyph sits in column 0 puts its entry cell one to the west of it.
    margin = channels + 1
    iw = margin + p.bands.x1 + 1
    c = Circuit(iw, ih)

    entry_x = {ch.blocks[0]: ch.entry_col + margin - 1 for ch in p.chains}

    for i, chain in enumerate(p.chains):
        head = chain.blocks[0]
        c.set(entry_x[head], entry_y[head], "@" if i == 0 else ">")
        for j, row in enumerate(chain.rows):
            for col, glyph in row.cells:
                c.set(col + margin, row.y, glyph)
            if j + 1 < len(chain.rows):
                nxt = chain.rows[j + 1]
                c.set(row.end + margin, row.y, "v")
                for yy in range(row.y + 1, nxt.y):
                    c.set(row.end + margin, yy, " ")
                c.set(row.end + margin, nxt.y, ">" if nxt.east else "<")

    for lane in lanes:
        chain = next(ch for ch in p.chains if lane.src in ch.blocks)
        last = chain.rows[-1]
        if lane.kind == "straight" and not last.east:
            start = lane.col + margin          # already walking west; no turn needed
        elif lane.kind == "straight":
            start = last.end + margin
            c.set(start, last.y, "v")
            for yy in range(last.y + 1, lane.row):
                c.set(start, yy, " ")
            c.set(start, lane.row, "<")
        else:                                  # a branch's clockwise lane
            start = lane.col + margin
            c.set(start, lane.row, "<")

        target_y = entry_y[lane.target]
        stop = entry_x[lane.target] if lane.kind == "loop" else lane.channel
        _walk(c, lane.row, start, stop, "<")
        if lane.kind == "loop":
            # The return leg merges into the block's own entry cell from below,
            # so a self-loop costs one turn and the walk back -- not a corridor.
            c.set(stop, lane.row, "^")
            for yy in range(target_y + 1, lane.row):
                c.set(stop, yy, " ")
            continue
        c.set(stop, lane.row, "v" if target_y > lane.row else "^")
        step = 1 if target_y > lane.row else -1
        for yy in range(lane.row + step, target_y, step):
            if not c.free(stop, yy):
                raise Collision(f"channel {stop} blocked at row {yy} "
                                f"({lane.src}->{lane.target})")
            c.set(stop, yy, " ")
        c.set(stop, target_y, ">")
        _walk(c, target_y, stop, entry_x[lane.target], ">")

    return Room(c, p.bands, p.chains, lanes, entry_y, margin, iw, ih)


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
    start: dict[tuple[int, int], str] = {}
    for chain in room.chains:
        for name, (ri, col) in chain.start_at.items():
            start[(col + room.margin, chain.rows[ri].y)] = name

    def heading_at(name: str) -> tuple[tuple[int, int], tuple[int, int]]:
        chain = next(ch for ch in room.chains if name in ch.start_at)
        ri, col = chain.start_at[name]
        row = chain.rows[ri]
        return (col + room.margin, row.y), (E if row.east else W)

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
NB = 16                                      # rows of north band above the wall
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


def build_grid() -> tuple[list[str], object, dict[str, object]]:
    """Worker room, six rings, and the input and output rooms."""
    from randomfun2026solvers.man_debug import DebugMap
    from randomfun2026solvers.value_ring import draw_pipe, stamp, walls

    room = build_room()
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
             note=f"{len(LAID)} blocks in {len(room.chains)} fall-through chains")
    d.region("channels", WX, wy, margin, ih, color="#94a3b8",
             note=f"{margin - 1} corridors carrying {len(room.lanes)} routed lanes")
    for ring, n in lengths.items():
        d.region(f"ring:{ring}", col(ring, True) - 2, 0, 6, wy, color="#0ea5e9",
                 note=f"{n} cells, holds {RING_WORDS[ring]} words")
    for chain in room.chains:
        y0 = room.entry_y[chain.blocks[0]]
        d.region(f"chain:{chain.blocks[0]}", WX + margin, wy + y0,
                 iw - margin, chain.rows[-1].y - y0 + 1, tags=["block"],
                 color="#22c55e", note=" ".join(chain.blocks))
    info = {"worker": (iw, ih), "rings": lengths, "channels": margin - 1,
            "blocks": len(LAID), "chains": len(room.chains),
            "lanes": len(room.lanes),
            "size": (max(len(r) for r in art), len(art))}
    return art, d, info


if __name__ == "__main__":  # pragma: no cover - a development probe
    p = plan()
    print(f"code columns {p.bands.x0}..{p.bands.x1}")
    for name in p.bands.recv_order:
        print(f"  {name:3s} r{p.bands.recv_span[name]}@{p.bands.recv_col[name]}"
              f"  s{p.bands.send_span[name]}@{p.bands.send_col[name]}")
    print(f"{len(p.chains)} chains, {p.pen_rows} walked rows")
    room = build_room(p)
    print(f"{len(room.lanes)} lanes, {room.margin} channels, "
          f"interior {room.iw}x{room.ih}")
    check_room(room)
    print("every block walks its own tokens")
