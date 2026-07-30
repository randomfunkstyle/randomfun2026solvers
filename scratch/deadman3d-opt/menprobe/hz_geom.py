"""The build-free binding model: predict the geometry, then apply §7.1 exactly.

The rule is never approximated — :func:`hz_core.verdict` is ``check_bindings``
restated line for line.  Everything modelled here is *where the glyphs and the
touches land*, so a wrong verdict is always a wrong geometry and is attributable
to one term.

Two gates, in the order the builder applies them.

**Gate 1, the level rule.**  ``store_request_west`` refuses before §7.1 is ever
consulted unless the store's request wall and the adapter's request outlet share
a row.  Measured over the whole reachable range (``hz_level.py``), both sides are
exactly linear and independent:

    adapter = 164 - squash_band + (mem_out_row - 20) + rom_shift
    wall    = 147 + store_dy                        + rom_shift

so the machine is level iff ``store_dy + squash_band - (mem_out_row - 20) == 17``.
``rom_rows`` moves both together and cancels; ``store_dx``, the store shape and
``mem_pad`` do not appear.

*This is the gate that has been mistaken for a constraint.*  ``SQUASH_BAND`` is
recorded in the registry as binding at 7 and nowhere else in 0..21, and the
frequency-shaping search lost fourteen ``mem_out_row``-moving candidates to it.
Both readings held ``store_dy`` fixed.  With :func:`repair_dy` turning the one
free variable the equation has, **every k in 0..15 binds** (k >= 18 fails
elsewhere, on a grid collision once dy goes negative).

**Gate 2, §7.1.**  ``build_for`` sweeps ``mem_pad`` 0..39 and ships the smallest
footprint that binds, so "does this vector bind" is an existential over forty
binding problems, not one verdict.  The model rules on all forty.

**How the geometry is predicted.**  An anchor is one real
:func:`hz_core.capture` — forty exact binding problems for one 11s build prefix.
A candidate is a displacement of it, and every displacement below was *measured*
on the shipped machine rather than assumed:

===========================  ==============================================
lever                        effect on the CPU's binding problem
===========================  ==============================================
``rom_touch_drop``           the ``rom`` touch moves 1:1 in y; glyphs identical
``store_dx``                 nothing at all
``store_dy``                 nothing at all (gate 1 only)
``store_cols`` / ``_rows``   nothing at all
``folded_lanes``             nothing at all
``tucked_drops``             nothing at all
``rom_rows``                 rigid y-translation of everything, up to ~+2;
                             reshapes beyond that
``squash_band``              rigid y-translation of the band
``LANE_ORDER``/``SLOTS``     repacks the band, per lane
``lane_pitch``               reshapes
``seek_slab_pitch``          reshapes
``straight_trie``            reshapes
``seek_tight_struct_drops``  moves the east-wall touches 1 column
===========================  ==============================================

The reshaping levers are outside the anchor's class and :func:`predict` returns
``None`` for them rather than guessing.  A search reads ``None`` as "ask the
builder", which costs 11s instead of 20us and is never wrong.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hz_core as H  # noqa: E402
import trie_shape as TS  # noqa: E402

#: Levers that reshape the CPU rather than translate it.  A candidate differing
#: from its anchor in any of these is not predicted — see the table above.
CLASS_FIELDS = ("lane_pitch", "straight_trie", "seek_taken_drop_east",
                "seek_tight_struct_drops", "seek_slab_pitch")

#: How far ``rom_rows`` may move before the glyph set stops being a rigid
#: translation.  Measured: +-2 rigid, +6 and +11 reshaped (33 and 36 of 37
#: glyphs moved independently).
ROM_ROWS_RIGID = 2


def band_of(p: H.P) -> dict[str, int]:
    """The lane rows this vector puts the band on, CPU-relative.

    ``squash_band`` is deliberately not passed through to
    :func:`trie_shape.band_rows`.  The builder bottom-aligns the band and takes
    the squash out of the room *below* it, so a squash translates the band
    rigidly and cancels out of every lane-to-lane difference.  It enters the
    model once, in :func:`adapter_row`.  Conflating the two double-counts it.
    """
    order = full_order(p.lane_order) if p.lane_order else TS.DEFAULT_ORDER
    slots = dict(p.opcode_slots) if p.opcode_slots else TS.contiguous(order)
    return TS.band_rows(order, slots, straight=p.straight_trie, squash=0)


def full_order(middle) -> tuple[str, ...]:
    """Re-pin the head and tail the registry is not allowed to name.

    ``LANE_ORDER`` is a permutation of the **unpinned** lanes, but the band is
    twenty-two rows: ``plan`` puts ``IN`` on top and the display lane at the
    bottom.  Pricing the twenty alone shifts every row by one and mis-reports
    ``mem_out_row``, which is exactly the number the level repair is solved from
    — so getting this wrong makes the repair confidently wrong.
    """
    mid = tuple(m for m in middle if m not in TS.PINNED_HEAD + TS.PINNED_TAIL)
    return TS.PINNED_HEAD + mid + TS.PINNED_TAIL


def mem_out(p: H.P) -> int:
    """The median MEM lane row — the one number the adapter is placed from."""
    return TS.mem_out_row(band_of(p))


@dataclass
class Cal:
    """What the anchor build measured.  Nothing here is a constant of the design."""

    wall: int = 157
    adapter: int = 157
    mem_out0: int = 20
    squash0: int = 7
    dy0: int = 10
    rom_rows0: int = 119


@dataclass
class Anchor:
    """One captured vector and the forty binding problems it posed."""

    p: H.P
    pads: dict = field(default_factory=dict)
    cal: Cal = field(default_factory=Cal)
    band: dict = field(default_factory=dict)
    rows: dict = field(default_factory=dict)   # grid row -> band row, for glyphs

    @staticmethod
    def take(p: H.P, wall: int, adapter: int) -> "Anchor":
        """Capture ``p`` and pin the level calibration to its two measured rows."""
        c = H.capture(p)
        if not c.pads:
            raise RuntimeError(f"anchor did not reach §7.1: {c.reason}")
        a = Anchor(p=p, pads=c.pads, band=band_of(p),
                   cal=Cal(wall=wall, adapter=adapter, mem_out0=mem_out(p),
                           squash0=p.squash_band, dy0=p.store_dy,
                           rom_rows0=p.rom_rows))
        # Anchor the band in grid coordinates: the CPU's row r0 carries the lane
        # whose band row is the smallest, and the band's rows are consecutive
        # ranks, so one offset aligns the two.  Taken from the ``mem`` glyphs,
        # whose rows are exactly the MEM lanes' (they are that lane's east port).
        glyphs = a.pads[min(a.pads)][0]
        mem_rows = sorted({y for _, y, _, band in glyphs if band == "mem"})
        band_mem = sorted(a.band[m] for m in TS.MEM_LANES if m in a.band)
        a.rows = dict(zip(mem_rows, band_mem)) if len(mem_rows) == len(band_mem) else {}
        return a

    def same_class(self, q: H.P) -> bool:
        return (all(getattr(q, f) == getattr(self.p, f) for f in CLASS_FIELDS)
                and abs(q.rom_rows - self.p.rom_rows) <= ROM_ROWS_RIGID)


# ── gate 1 ───────────────────────────────────────────────────────────────────

def adapter_row(a: Anchor, p: H.P) -> int:
    return (a.cal.adapter + (mem_out(p) - a.cal.mem_out0)
            - (p.squash_band - a.cal.squash0) + (p.rom_rows - a.cal.rom_rows0))


def wall_row(a: Anchor, p: H.P) -> int:
    return a.cal.wall + (p.store_dy - a.cal.dy0) + (p.rom_rows - a.cal.rom_rows0)


def level(a: Anchor, p: H.P) -> bool:
    return wall_row(a, p) == adapter_row(a, p)


def repair_dy(a: Anchor, p: H.P) -> H.P:
    """The ``store_dy`` that makes ``p`` level — the knob nobody turned.

    One equation, two unknowns, and every search so far has solved it by
    refusing to move either.  ``rom_rows`` cancels, so the repair is exact
    whatever else the vector does, and it costs nothing: the wall is placed from
    ``store_offset`` dy and from nothing else on the machine.
    """
    return H.bump(p, store_dy=p.store_dy + (adapter_row(a, p) - wall_row(a, p)))


#: ``the store's request wall is on row A and the adapter's request leaves on row B``
_ROWS = __import__("re").compile(
    r"request wall is on row (\d+) and the adapter's request leaves on row (\d+)")


def measured_rows(p: H.P):
    """``(wall, adapter)`` read out of the builder itself, or ``None`` if level.

    The refusal names both rows, so a deliberately *unlevel* build is a free
    instrument.  This is the ground truth :func:`repair_measured` corrects
    against, and it is what makes the repair safe on the levers the geometry
    model only approximates.
    """
    c = H.capture(p)
    for msg in list(c.early.values()) + [c.reason]:
        m = _ROWS.search(msg or "")
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def repair_measured(a: Anchor, p: H.P, tries=3) -> tuple[H.P, int]:
    """Repair with the model, then **check with the builder** and correct.

    The model's ``mem_out_row`` comes from :mod:`trie_shape`'s reimplementation
    of the band, which is exact for the levers it was derived on and an
    approximation for a repacking one.  A wrong repair does not merely lose a
    candidate — it loses it with a message that looks exactly like the
    unrepairable case, which is how ``SQUASH_BAND`` came to be recorded as
    binding at one value.  So the repair is verified, not trusted: each capture
    reports the residual and the next guess corrects by exactly it.

    Returns the repaired vector and how many captures it cost.
    """
    q = repair_dy(a, p)
    for i in range(1, tries + 1):
        got = measured_rows(q)
        if got is None:
            return q, i          # level: the builder stopped naming the rows
        wall, adapter = got
        if wall == adapter:
            return q, i
        q = H.bump(q, store_dy=q.store_dy + (adapter - wall))
    return q, tries


# ── gate 2 ───────────────────────────────────────────────────────────────────

def _shift(a: Anchor, p: H.P):
    """grid row -> dy, for the band's repack plus the rigid terms above it."""
    b1 = band_of(p)
    rigid = (p.rom_rows - a.cal.rom_rows0) - (p.squash_band - a.p.squash_band)
    out = {}
    for grid_y, band_y in a.rows.items():
        lane = next((m for m, r in a.band.items() if r == band_y), None)
        d = (b1[lane] - band_y) if (lane and lane in b1) else 0
        out[grid_y] = d + rigid
    return out, rigid


def predict(a: Anchor, p: H.P):
    """``(binds, reason, good_pads)`` with no builder, or ``None`` to ask one."""
    if not a.same_class(p):
        return None
    if not level(a, p):
        return False, f"level: wall {wall_row(a, p)} vs adapter {adapter_row(a, p)}", ()
    shift, rigid = _shift(a, p)
    d_rom = (p.rom_touch_drop - a.p.rom_touch_drop) + rigid
    good, why = [], {}
    for pad, (glyphs, touches) in sorted(a.pads.items()):
        g2 = [(x, y + shift.get(y, rigid), gl, band) for x, y, gl, band in glyphs]
        t2 = {n: ((x, y + rigid) if n != "rom" else (x, y + d_rom))
              for n, (x, y) in touches.items()}
        ok, reason = H.verdict(g2, t2)
        (good.append(pad) if ok else why.setdefault(reason, pad))
    if good:
        return True, "ok", tuple(good)
    return False, "; ".join(list(why)[:2]) or "no pad binds", ()


__all__ = ["Anchor", "CLASS_FIELDS", "Cal", "adapter_row", "band_of", "level",
           "mem_out", "predict", "repair_dy", "wall_row"]
