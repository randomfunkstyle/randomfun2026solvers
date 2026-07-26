#!/usr/bin/env python3
"""What a pipe relay would be worth to the LLLM room, before building one.

`little-little-man` dissolved an "impossible" constraint by moving a choice behind
a seam: `isa.py` recorded that ``DSP p`` could not be built because an ``s`` binds
to a pipe by *where the glyph sits*, and that is true of a lane and false of the
system.  A lane sends two words down one pipe, a small room receives, branches,
and forwards.  The fold took that machine's ``area2`` from 37,636 to 33,856.

LLLM is pinned by the same rule wearing different clothes.  ``lllm_layout`` runs
zones west to east and a block's ops must sit in the band of the pipe they talk
to, because position decides binding (``Geometry.binds``).  That is what makes 40
of 63 blocks column-bound, and it is the root of one-block-per-row-band.

This module answers the question a relay poses *without* building one: if every
pipe op bound to a single relay pipe, so a block could talk to any pipe from any
column, how many rows would the room need?

Two effects, and only the first is certain:

1. **Seeks vanish.**  A wrap happens when a block must revisit a band it has
   already passed (``_Pen.seek`` walking backwards).  With one band there is
   nothing to revisit, so every block is one glyph row unless its own glyphs
   overrun the usable width.  That is measurable here, exactly.

2. **Pairing becomes available to all 63 blocks** rather than the 23 that use no
   pipe op.  This one is a *ceiling*, not a promise: it assumes an eastern channel
   bank exists and that every pairing routes.  Stated separately for that reason.

Answer: **the trick does not transfer, for two independent reasons.**

**The lane rows dominate and no relay touches them.**  Of the room's 168-row span,
80 are glyph rows and **88 are lanes** — a block still has to route to its
successor whatever column it sits in.  Deleting every seek takes the span to 151,
and the optimistic all-blocks-pairing ceiling is 120.  Neither approaches the
60-80 that would justify rewriting the allocator, because 88 rows are untouched
by construction.

**And the pipes that pin the blocks cannot be relayed anyway.**  Of the 40
column-bound blocks, **38 touch ``ST`` or ``FI`` and only 2 are ``IO``-only**.
``little-little-man``'s display ports were one-way sinks: a port takes an address
and a colour, nothing flows back, and nothing about the pipe's *length* means
anything.  LLLM's ``ST`` and ``FI`` are the opposite — they are **rotating loops
where the pipe is the data structure**.  ``lllm_ring``'s own docstring: "Reading
slot *i* means rotating *i* words", and "a tick is one rotation of ``FILE`` and
one lap of ``STORE``".  A relay spliced into such a loop preserves FIFO order, so
slot addressing by count still works — but its cells lengthen the ring, and lap
latency is multiplied by every interpreted tick.  ``STORE`` holds up to 257
values; the relay would be paid on every lap of every tick.

So a relay could serve ``IO`` soundly and would free **two blocks**.  That is the
whole of it.
"""

from __future__ import annotations

from . import lllm_layout as L
from . import lllm_ring as R

__all__ = ["block_widths", "relay_bound", "report"]


def _item_width(kind: str, val: object) -> int:
    """Columns one placeable item occupies.  A literal carries its two backticks."""
    return 1 if kind == "g" else len(str(val)) + 2


def block_widths(worker=R.WORKER, geo: L.Geometry = L.LLLM) -> dict[str, int]:
    """Each block's glyph width if its items were laid contiguously.

    This is what a block costs with a relay: no ``seek`` to a band, so no dead
    columns walked between ops and no wrap when a later op lies west of the pen.
    """
    out: dict[str, int] = {}
    for name, (toks, _succ) in worker.items():
        w = 0
        for tok in toks:
            for kind, val in L._items(tok, geo):
                w += _item_width(kind, val)
        out[name] = w
    return out


def relay_bound(worker=R.WORKER, geo: L.Geometry = L.LLLM) -> dict[str, object]:
    """Rows the room needs under a relay, against what it needs today."""
    room = L.build_room(worker)
    order = room.order
    today_glyph_rows = sum(len(room.plans[n].rows) for n in order)
    ys = sorted(y for n in order for y in room.glyph_ys[n])
    today_span = max(ys) - min(ys) + 1

    widths = block_widths(worker, geo)
    # Usable code width: the room's interior east of the channel bank.
    usable = geo.iw - geo.code0
    # With one band a block wraps only if its own glyphs exceed the usable width.
    relay_glyph_rows = sum(max(1, -(-w // usable)) for w in widths.values())

    # Lane rows are unaffected by the relay: a block still routes to its successor.
    lane_rows = today_span - today_glyph_rows

    # Ceiling only: two runs a row (one entered from each bank), so at best the
    # eastern partner's glyph rows are absorbed.  Its lane row is not.
    pairable = len(order) // 2
    paired_saving = sum(
        sorted((max(1, -(-widths[n] // usable)) for n in order), reverse=True)[:pairable]
    )

    return {
        "blocks": len(order),
        "today_glyph_rows": today_glyph_rows,
        "today_span": today_span,
        "lane_rows": lane_rows,
        "usable_width": usable,
        "widest_block": max(widths.values()),
        "relay_glyph_rows": relay_glyph_rows,
        "seek_rows_saved": today_glyph_rows - relay_glyph_rows,
        "relay_span": relay_glyph_rows + lane_rows,
        "relay_paired_span_ceiling": relay_glyph_rows + lane_rows - paired_saving,
    }


def report(worker=R.WORKER, geo: L.Geometry = L.LLLM) -> str:
    b = relay_bound(worker, geo)
    lines = [
        f"blocks                     {b['blocks']}",
        f"usable code width          {b['usable_width']}   widest block {b['widest_block']}",
        "",
        f"today: glyph rows          {b['today_glyph_rows']}",
        f"today: lane rows           {b['lane_rows']}",
        f"today: span                {b['today_span']}",
        "",
        f"relay: glyph rows          {b['relay_glyph_rows']}   (seeks gone: "
        f"-{b['seek_rows_saved']})",
        f"relay: span                {b['relay_span']}",
        f"relay + pairing ceiling    {b['relay_paired_span_ceiling']}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
