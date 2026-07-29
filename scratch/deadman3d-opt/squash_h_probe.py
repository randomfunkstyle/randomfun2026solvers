#!/usr/bin/env python3
"""Can ``SQUASH_BAND`` coexist with ``SEEK_TELEPORT``? A geometry-only probe.

The recorded claim is that it cannot: squashing top-aligns the CPU's lane band,
``H`` shrinks, and ``_seek_teleport``'s room H — bottom-anchored at ``y_b = SY - 2``
where ``(SX, SY)`` is placed at ``CY + H + 1`` — loses its clear band.

But the two ends of that band are anchored to *different* things:

* its **north** end follows ``CY + H``, via the STREAM unit, so it moves north 12;
* its **south** end is the store, anchored to ``CY`` plus ``TIER_LAYOUT``'s
  ``store_offset``, and ``CY`` does not move under a squash at all.

So the band is squeezed from one side only, and the registry that moves the other
side already exists — ``store_offset`` dy, which is exactly the ``dy -5`` the
``deadman-3d`` note says a shrunk room needs. hires' entry is ``(-14, 0)``: dy has
never been anything but zero here.

This builds only — no tour — so it is seconds per variant, and reports for each
the box, room H's rect, and on failure which cells block the band.

    python scratch/deadman3d-opt/squash_h_probe.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(REPO))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"
SLUG = "deadman-3d_hires"
KEY = (SLUG, "taped")

#: filled by the diagnostic patch on every call, successful or not
TRACE: dict[str, object] = {}


def install_trace(M):
    """Wrap ``_seek_teleport`` so the room-H band is reported either way."""
    orig = M._seek_teleport

    def patched(g, *, cmd_y, src_x, x_e, rom_east, ry, y_b):
        hx0, hx1 = src_x + 1, x_e
        hy1 = y_b + 1
        hy0 = hy1 - (M._TELE_H + 1)
        blockers = [
            (x, y)
            for y in range(hy0, hy1 + 1)
            for x in range(hx0, hx1 + 1)
            if (x, y) in g.c
        ]
        by_row: dict[int, int] = {}
        for _, y in blockers:
            by_row[y] = by_row.get(y, 0) + 1
        TRACE.clear()
        TRACE.update(
            cmd_y=cmd_y, src_x=src_x, x_e=x_e, y_b=y_b,
            h_rect=(hx0, hy0, hx1, hy1), n_block=len(blockers),
            rows={k: by_row[k] for k in sorted(by_row)},
            sample=sorted(blockers)[:6],
        )
        return orig(g, cmd_y=cmd_y, src_x=src_x, x_e=x_e,
                    rom_east=rom_east, ry=ry, y_b=y_b)

    M._seek_teleport = patched


def build(M, prog, *, squash: bool, store_dy: int, seek_tele: bool = True):
    """One build with ``store_offset`` dy and the squash overridden."""
    base = dict(M.TIER_LAYOUT.get(KEY, {}))
    dx = base.get("store_offset", (0, 0))[0]
    M.TIER_LAYOUT[KEY] = {**base, "store_offset": (dx, store_dy)}
    had = KEY in M.SEEK_TELEPORT
    if seek_tele:
        M.SEEK_TELEPORT.add(KEY)
    else:
        M.SEEK_TELEPORT.discard(KEY)
    TRACE.clear()
    try:
        m = M.build_for(SLUG, program=prog, store="taped", squash_band=squash)
        return m, None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        M.TIER_LAYOUT[KEY] = base
        if had:
            M.SEEK_TELEPORT.add(KEY)
        else:
            M.SEEK_TELEPORT.discard(KEY)


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    install_trace(M)

    print(f"shipped registries: store_offset={M.TIER_LAYOUT[KEY]['store_offset']} "
          f"lane_pitch={M.LANE_PITCH[KEY]} rom_drop={M.ROM_TOUCH_DROP[KEY]} "
          f"seek_tele={KEY in M.SEEK_TELEPORT}", flush=True)

    cases: list[tuple[str, bool | int, int]] = [("shipped", False, 0), ("full squash", True, 0)]
    # the partial squash: take k rows out of the room, leave the rest blank
    cases += [(f"squash k={k}", k, 0) for k in range(1, 13)]
    # and the store-follows-north repair, which fails with or without the squash
    cases += [("full squash dy=-2", True, -2), ("shipped dy=-2", False, -2)]

    for name, squash, dy in cases:
        m, err = build(M, prog, squash=squash, store_dy=dy)
        h = TRACE.get("h_rect")
        if m is not None:
            store = m.regions.get("tape") or m.regions.get("store")
            hr = m.regions.get("seek:H")
            print(f"  {name:>16}: OK {m.width}x{m.height}  H={hr}  y_b={TRACE.get('y_b')}",
                  flush=True)
        else:
            print(f"  {name:>16}: {err}", flush=True)
            if h is not None:
                print(f"{'':>18}  band x{h[0]}..{h[2]} y{h[1]}..{h[3]} "
                      f"blocked={TRACE.get('n_block')} rows={TRACE.get('rows')} "
                      f"sample={TRACE.get('sample')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
