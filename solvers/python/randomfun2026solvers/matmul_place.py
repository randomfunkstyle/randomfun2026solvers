#!/usr/bin/env python3
"""`matmul`'s one-room machine, laid by :mod:`blockplace` instead of by chains.

`matmul_grid.assign_rows` gives every walked row a grid row of its own and every
routed lane another, straight down the room: 32 blocks became 81 rows in a
64-column room, and the score squares ``max(w, h)``.  Nothing about the machine
needs that.  A block's *columns* are pinned by its pipe ops -- an `r` binds the
nearest incoming pipe, an `s` the nearest outgoing one -- but its **rows are
free**, so two blocks whose column spans are disjoint may stand on the same rows.

Two things have to be translated for `blockplace` to see that:

* **Fourteen zones, not seven.**  `matmul` cuts the room twice over, once for
  receives and once for sends, and the two cuts do not agree -- which is what
  lets ``ri sq ri sq`` sit in one row.  `lllm_layout.Geometry` already keeps
  `pipe_in` and `pipe_out` as separate nearest-sets, so each band becomes *two*
  zones, ``k<`` and ``k>``, and `binds` ranges over the right seven either way.
* **The bands are the banks.**  A bank is a column window that binds a set of
  zones; here the windows are unions of adjacent bands, so a bank is named by
  the bands it spans and binds their fourteen half-zones.
"""

from __future__ import annotations

from dataclasses import dataclass

from randomfun2026solvers import blockplace as B
from randomfun2026solvers import matmul_grid as G
from randomfun2026solvers.circuit import Circuit, Collision

__all__ = ["BankSpec", "PlacedRoom", "geometry", "build_room", "banks_of"]

E, W = (1, 0), (-1, 0)


# ── the fourteen zones ────────────────────────────────────────────────────────
def zone_name(band: str, sending: bool) -> str:
    """``'k>'`` for the outgoing half of band `k`, ``'k<'`` for the incoming."""
    return band + (">" if sending else "<")


def token_zones() -> dict[str, str]:
    """Every pipe token of the current :data:`matmul_grid.RINGS` to its zone.

    A band is split in two on purpose.  `matmul_grid.Bands` keeps `recv_span`
    and `send_span` apart because the two partitions are independent -- band
    `q`'s send cells and band `io`'s receive cells overlap by six columns -- and
    a single ``zone_cols[band]`` would have to be their intersection, which for
    most bands is empty.
    """
    out = {"ri": zone_name("io", False), "so": zone_name("io", True)}
    for ring in G.RINGS:
        out["r" + ring] = zone_name(G.RINGS[ring], False)
        out["s" + ring] = zone_name(G.RINGS[ring], True)
    return out


def geometry(bands: G.Bands, code0: int, iw: int, *, zones=None):
    """A `lllm_layout.Geometry` over the fourteen half-bands of one room."""
    from randomfun2026solvers.lllm_layout import Geometry

    cols = {}
    for band, (lo, hi) in bands.recv_span.items():
        cols[zone_name(band, False)] = (lo, hi)
    for band, (lo, hi) in bands.send_span.items():
        cols[zone_name(band, True)] = (lo, hi)
    if zones is not None:
        cols = {z: c for z, c in cols.items() if z in zones}
    return Geometry(
        token_zone=token_zones(),
        zone_cols=cols,
        # `binds` picks the nearest *incoming* pipe for an `r` and the nearest
        # *outgoing* pipe for an `s`, so the two dicts must stay disjoint in
        # their keys or a receive could "bind" a send column.
        pipe_in={zone_name(b, False): c for b, c in bands.recv_col.items()},
        pipe_out={zone_name(b, True): c for b, c in bands.send_col.items()},
        code0=code0,
        iw=iw,
        lit_slack=0,
    )


#: The CFG as `blockplace` wants it: `matmul_grid.LAID` with the lanes that can
#: never be taken removed, so no row is reserved under a branch for them.
def worker() -> dict[str, tuple[list[str], dict[str, str] | str]]:
    out = {}
    for name, (toks, succ) in G.LAID.items():
        if isinstance(succ, dict):
            succ = {k: v for k, v in succ.items()
                    if (name, k) not in G.DEAD_LANES}
        out[name] = (list(toks), succ)
    return out


# ── banks ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BankSpec:
    """One code window, named by the bands it spans.

    `nch` channel columns stand west of the window; the block's man is entered
    at their east edge and walks east from there, exactly as in the single-bank
    layouts.  The channels are not corridors reserved in advance -- the router
    uses whatever is blank -- they are the columns a lane needs to get clear of
    the code before it turns, and the entry column itself.
    """

    bands: tuple[str, ...]
    nch: int = 1


def banks_of(bands: G.Bands, specs) -> dict[str, B.Bank]:
    """Turn band-named specs into `blockplace.Bank`s, in the room's own columns.

    A bank's window is the union of its bands' column spans over *both*
    partitions, and it is not moved: the pipes stand where `layout_bands` put
    them, so shifting a window would break the binding it was cut for.  The
    channel columns are therefore taken off the window's own western end, which
    costs `nch + 1` columns of the westernmost band and nothing else.
    """
    out: dict[str, B.Bank] = {}
    for spec in specs:
        cols = [c for band in spec.bands
                for span in (bands.recv_span[band], bands.send_span[band])
                for c in span]
        lo, hi = min(cols), max(cols)
        name = "".join(spec.bands)
        zones = tuple(zone_name(b, s) for b in spec.bands for s in (False, True))
        out[name] = B.Bank(name, lo, spec.nch, hi, zones)
    return out


#: One bank spanning every band.  Measured at 83 rows against the chain
#: layout's 81 -- packing buys **nothing** here, and the reason is instructive:
#: a block claims only the columns it uses, but `pack` widens its first row and
#: `lane_claims` widens its last one out to `bank.entry`, so with one entry
#: column at the west wall every block is full width top and bottom and no two
#: can share a row.  The entry column is the thing that has to divide.
ONE_BANK = (BankSpec(G.BANDS),)


def span_banks(bands: G.Bands, nch: int = 1) -> dict[str, B.Bank]:
    """A bank for every contiguous run of bands, so each block enters beside its code.

    `snake` had two pipe pairs and put its banks side by side; `matmul` has
    seven, and a block's window is already pinned to the bands its ops name.  So
    the banks are not chosen -- they are enumerated, one per run ``bands[i:j]``,
    and a block takes the **narrowest** one that covers it.  Its entry column
    then stands one west of its own leftmost band instead of at the west wall,
    and its claims stop being full width.

    The runs overlap, which is fine and is the point: two banks sharing columns
    simply means their blocks cannot share a row, which is exactly true.
    """
    out: dict[str, B.Bank] = {}
    order = bands.recv_order
    for i in range(len(order)):
        for j in range(i, len(order)):
            run = order[i:j + 1]
            cols = [c for band in run
                    for span in (bands.recv_span[band], bands.send_span[band])
                    for c in span]
            lo, hi = min(cols), max(cols)
            if lo + nch + 1 > hi:
                continue                  # no code column left after the entry
            zones = tuple(zone_name(b, s) for b in run for s in (False, True))
            out["".join(run)] = B.Bank("".join(run), lo, nch, hi, zones)
    return out


def assign_banks(wrk, banks: dict[str, B.Bank], base_geo) -> dict[str, tuple]:
    """Candidate banks for a block: every one that covers it, narrowest first.

    Narrowest first is what makes the packing work -- the tightest window is the
    one whose entry column is nearest the block's own glyphs.  The wider ones
    stay on the list because a narrow window can fail to *lay* the block at all
    (a long straight run wraps, and a wrapped row needs a column for its link),
    and `blockplace.build` falls through to the next candidate rather than
    giving up on the room.
    """
    out = {}
    for name in wrk:
        want = B.block_zones(wrk, name, base_geo.token_zone)
        fits = sorted((b for b in banks.values() if want <= set(b.zones)),
                      key=lambda b: (b.width, b.ch0))
        if not fits:
            raise Collision(f"{name} needs {sorted(want)}; no bank binds them all")
        out[name] = tuple(fits)
    return out


# ── the room, in the shape the walkers already understand ─────────────────────
@dataclass
class PlacedRoom:
    """A packed worker room, answering the same three questions as `Room`.

    `matmul_grid.walk_blocks`, `walk_costs` and `check_room` follow the man's
    feet through the drawn cells; all they need to know besides the cells is
    where each block's man starts and which way he faces.  Reusing them means
    the packed layout is proven and priced by exactly the code that proved and
    priced the chain layout, rather than by a second copy of it that could
    disagree.
    """

    circuit: Circuit
    bands: G.Bands
    room: B.Room
    iw: int
    ih: int
    margin: int = 0                # the bands already stand in room columns

    def pipe_col(self, ring: str, sending: bool) -> int:
        cols = self.bands.send_col if sending else self.bands.recv_col
        return cols[ring] + self.margin

    @property
    def starts(self) -> dict[tuple[int, int], str]:
        return {self._first(p): name for name, p in self.room.placed.items()}

    def heading(self, name: str):
        p = self.room.placed[name]
        return self._first(p), (E if p.plan.rows[0].east else W)

    @staticmethod
    def _first(p) -> tuple[int, int]:
        """The block's first glyph -- not its entry cell.

        The run from the entry `>` east to that glyph is walked too, but it is
        charged to whichever lane arrived, exactly as the chain layout charges
        it: `walk_costs` follows a lane until it lands on a start cell, so
        putting the start on the entry would count the run twice for a
        fall-through and not at all for a jump.
        """
        return min(c for c, _g in p.plan.rows[0].cells), p.ys[0]

    def regions(self):
        """(label, x, y, w, h, note) per block, for the debug overlay."""
        for name, p in self.room.placed.items():
            xs = [c for row in p.plan.rows for c, _g in row.cells]
            yield (f"block:{name}", min(xs), p.ys[0],
                   max(xs) - min(xs) + 1, p.ys[-1] - p.ys[0] + 1,
                   f"{p.bank.name}: " + " ".join(G.LAID[name][0]))


def build_room(geom: G.Geometry | None = None, specs=None, *,
               nch: int = 1, order: list[str] | None = None, seed: int = 0,
               attempts: int = 24) -> PlacedRoom:
    """Plan, pack, stamp and route the worker with :mod:`blockplace`."""
    geom = geom or G.GEOMETRY
    bands = G.layout_bands(geom.recv_order, geom.send_order,
                           geom.recv_w, geom.send_w)
    wrk = worker()
    banks = banks_of(bands, specs) if specs else span_banks(bands, nch)
    base = geometry(bands, 0, bands.x1 + 1)
    room = B.build(wrk, G.ENTRY, assign_banks(wrk, banks, base), base,
                   order=order, attempts=attempts, seed=seed)
    c = Circuit(room.width, room.height)
    for y, line in enumerate(room.rows()):
        for x, ch in enumerate(line):
            if ch != " ":
                c.set(x, y, ch)
    return PlacedRoom(c, bands, room, room.width, room.height)
