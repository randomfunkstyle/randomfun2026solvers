#!/usr/bin/env python3
"""Two geometry-only questions the 3-round tour raised.

**1. Is the squash free if the drop compensates?** The tour's ticks are monotonic in
``drop - k`` and ``k4-d22`` tied ``k0-d18`` to the digit, which says the squash's
whole tick effect is that it shortens the ROM corridor by ``k`` (it moves
``cpu.centre``, and ``fetch_y = CY + cpu.centre + rom_touch_drop``). If so, ``k``
rows come off the box for nothing at ``drop = 22 + k`` — so how deep can that go
before §7.1 refuses?

**2. Does the recorded ``squashed, no SEEK_TELEPORT`` build reproduce at all?** The
``SQUASH_BAND`` docstring reports it at 649x485 / 191,600,156, but a full squash at
the shipped drop 22 fails to bind here. If no drop builds it, that row — and the
-0.243% differenced out of it — has no machine under it.

    python scratch/deadman3d-opt/squash_compensate.py
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


def main(argv: list[str]) -> int:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(WAD)
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)

    def attempt(sq, drop, tele):
        had = KEY in M.SEEK_TELEPORT
        (M.SEEK_TELEPORT.add if tele else M.SEEK_TELEPORT.discard)(KEY)
        try:
            m = M.build_for(SLUG, program=prog, store="taped",
                            squash_band=sq, rom_touch_drop=drop)
            return f"{m.width}x{m.height}", None
        except Exception as exc:  # noqa: BLE001
            return None, f"{exc}"
        finally:
            (M.SEEK_TELEPORT.add if had else M.SEEK_TELEPORT.discard)(KEY)

    print("1. corridor-compensated squash: k rows off, drop = 22 + k, "
          "SEEK_TELEPORT on", flush=True)
    for k in range(0, 9):
        sq = False if k == 0 else k
        box, err = attempt(sq, 22 + k, True)
        tag = box if box else ("ties/binding" if "must bind" in (err or "")
                               else "room H" if "room H" in (err or "") else "other")
        print(f"   k={k:>2} drop={22 + k:>2}: {tag}"
              + (f"   {err[:80]}" if box is None else ""), flush=True)

    print("\n2. the recorded 'squashed, no SEEK_TELEPORT' row: full squash, "
          "teleport off, every drop", flush=True)
    built = []
    for d in range(0, 33):
        box, err = attempt(True, d, False)
        if box:
            built.append((d, box))
    if built:
        print(f"   builds at drops {[d for d, _ in built]} -> "
              f"boxes {sorted({b for _, b in built})}", flush=True)
    else:
        print("   NO drop in 0..32 builds it — the recorded 649x485 row does not "
              "reproduce", flush=True)
        _, err = attempt(True, 22, False)
        print(f"   at the shipped drop 22: {err[:110]}", flush=True)

    print("\n   for reference, no squash + teleport off:", flush=True)
    for d in (14, 18, 22):
        box, err = attempt(False, d, False)
        print(f"     drop={d}: {box or err[:70]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
