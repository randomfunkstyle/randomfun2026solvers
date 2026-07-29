"""Screen (candidate, ROM fold) pairs: does it bind, and does it parse?

Cheaper than `band_root_gate.py` — no 75-second tour — so it finds the folds
worth spending a tour on. `FastLittleman(src)` is the parse gate; a fold that
stacks too many digits in one column raises there rather than at build time.
"""

from __future__ import annotations

import sys

from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine

from band_root_build import CANDIDATES, KEY


def screen(name: str, folds) -> None:
    machine.OPCODE_SLOTS[KEY] = CANDIDATES[name]
    for rows in folds:
        machine.SEEK_TIER_LAYOUT[KEY] = {"rom_rows": rows}
        try:
            m = machine.build_for("deadman-3d", store="taped")
        except Exception as exc:  # noqa: BLE001
            print(f"  {rows:3d}: build  {str(exc)[:70]}")
            continue
        try:
            FastLittleman("\n".join(m.rows))
        except Exception as exc:  # noqa: BLE001
            print(f"  {rows:3d}: parse  {m.width}x{m.height}  {str(exc)[:60]}")
            continue
        print(f"  {rows:3d}: OK     {m.width}x{m.height}")


if __name__ == "__main__":
    name = sys.argv[1]
    folds = [int(a) for a in sys.argv[2:]] or range(78, 112)
    print(f"{name}:")
    screen(name, folds)
