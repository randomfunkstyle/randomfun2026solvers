"""Which ROM folds bind the *counterfactual* taped builds the tests construct.

`tests/test_deadman3d.py` proves several registries are load-bearing by building
the taped machine with one of them flipped off and comparing. Those variants are
different geometry, so a fold that binds the shipped build is not enough — the
fold has to bind every build the suite asks for.
"""

from __future__ import annotations

import sys

from randomfun2026solvers.lm1 import machine

from band_root_build import CANDIDATES, KEY

VARIANTS: dict[str, dict[str, bool]] = {
    "shipped": {},
    "no-feed-teleport": {"TAPED_FEED_TELEPORT": False},
    "no-request-reach": {"STORE_REQUEST_REACH": False, "TAPED_CHAIN_REACH": False},
    "forwarded": {"STORE_REQUEST_REACH": False, "STORE_REQUEST_TELEPORT": True},
    "no-answer-west": {"STORE_ANSWER_WEST": False},
    "no-seek-teleport": {"SEEK_TELEPORT": False},
}


def with_registries(**registries):
    saved = {n: KEY in getattr(machine, n) for n in registries}
    try:
        for n, on in registries.items():
            reg = getattr(machine, n)
            reg.add(KEY) if on else reg.discard(KEY)
        return machine.build_for("deadman-3d", store="taped")
    finally:
        for n, on in saved.items():
            reg = getattr(machine, n)
            reg.add(KEY) if on else reg.discard(KEY)


if __name__ == "__main__":
    name = sys.argv[1]
    machine.OPCODE_SLOTS[KEY] = CANDIDATES[name]
    for rows in [int(a) for a in sys.argv[2:]]:
        machine.SEEK_TIER_LAYOUT[KEY] = {"rom_rows": rows}
        marks = []
        for vname, regs in VARIANTS.items():
            try:
                m = with_registries(**regs)
                marks.append(f"{vname}:{m.width}x{m.height}")
            except Exception as exc:  # noqa: BLE001
                marks.append(f"{vname}:FAIL({str(exc)[:34]})")
        print(f"  rom_rows {rows:3d}: " + "  ".join(marks))
