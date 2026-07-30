"""The level rule, as an identity rather than a knife edge.

``store_request_west`` refuses to build unless the store's request wall and the
adapter's request outlet are on the **same row**::

    store_in[1] != adapter_out[1]  ->  MachineError

Both sides are placed from things a search wants to move.  The adapter is placed
from ``mem_out_row`` — the median MEM lane row — which ``SQUASH_BAND`` shifts one
row per unit and which ``OPCODE_SLOTS`` / ``LANE_ORDER`` shift by repacking the
band.  The wall is placed from ``TIER_LAYOUT``'s ``store_offset`` dy alone.

So the rule is *one* equation in two unknowns, and it has been treated as a
constraint on one of them: ``SQUASH_BAND`` is documented as binding at exactly 7
"with the store where it is", and the frequency-shaping search lost fourteen
candidates to it.  If ``store_dy`` moves with it the equation is satisfiable
everywhere, and the whole axis — and every ``mem_out_row``-moving candidate —
opens up.

This probe measures both rows directly.  The builder prints them in the refusal,
so a deliberately *unlevel* build is an instrument: capture at ``dy`` and at
``dy + 1`` and the two messages pin the wall's slope and the adapter's row
without any arithmetic being taken on trust.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hz_core as H  # noqa: E402

ROWS = re.compile(r"request wall is on row (\d+) and the adapter's request "
                  r"leaves on row (\d+)")


def rows_of(p: H.P):
    """``(wall_row, adapter_row)`` for ``p``, or ``None`` when they are level.

    Level is the silent case — the builder only names the rows when it refuses —
    so a caller that needs both numbers unconditionally uses :func:`probe_rows`.
    """
    c = H.capture(p)
    for msg in list(c.early.values()) + [c.reason]:
        m = ROWS.search(msg or "")
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def probe_rows(p: H.P):
    """Both rows, whatever ``p`` does, by nudging ``store_dy`` until it refuses.

    The nudge moves the wall and nothing else, so the adapter row comes back
    unchanged and the wall's is corrected by the nudge.  Two captures at most.
    """
    for d in (0, 1, -1):
        got = rows_of(H.bump(p, store_dy=p.store_dy + d))
        if got:
            wall, adapter = got
            return wall - d, adapter
    return None


def main():
    base = H.shipped()
    print(f"base store_dy={base.store_dy} squash_band={base.squash_band}", flush=True)
    got = probe_rows(base)
    print(f"base rows: wall={got[0]} adapter={got[1]} (level={got[0] == got[1]})",
          flush=True)

    print("\n--- how each row moves ---", flush=True)
    for name, vals in (("squash_band", [0, 3, 5, 9, 12, 15, 18, 21]),
                       ("store_dy", [5, 8, 12, 15]),
                       ("rom_rows", [118, 120, 125])):
        for v in vals:
            p = H.bump(base, **{name: v})
            r = probe_rows(p)
            print(f"  {name}={v:4}: {'wall=%d adapter=%d  need dy%+d' % (r[0], r[1], r[1]-r[0]) if r else 'no reading'}",
                  flush=True)

    print("\n--- the repair: does dy compensate squash_band? ---", flush=True)
    for k in [0, 2, 3, 5, 6, 8, 9, 10, 12, 15, 18, 21]:
        p = H.bump(base, squash_band=k)
        r = probe_rows(p)
        if not r:
            print(f"  k={k:3d}: already level at dy={base.store_dy}", flush=True)
            continue
        dy = base.store_dy + (r[1] - r[0])
        c = H.capture(H.bump(base, squash_band=k, store_dy=dy))
        print(f"  k={k:3d}: dy {base.store_dy} -> {dy}  binds={c.binds} "
              f"pads={c.good[:4]}{'...' if len(c.good) > 4 else ''}  "
              f"{'' if c.binds else c.reason[:110]}", flush=True)


if __name__ == "__main__":
    main()
