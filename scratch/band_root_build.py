"""Build the taped DOOM machine with a candidate OPCODE_SLOTS map.

`band_root_probe.py` says the trie's *shape* — which the shipped map never
optimised, because its DP scored the drum's opcode digits and nothing else — is
worth ticks in two places at once: the walk itself, and the **riser**, which is
`collector - root_row` and so shrinks whenever the root moves south. This builds
the real machine for a candidate so the pipe bindings and the box are checked by
the generator rather than argued.
"""

from __future__ import annotations

import sys
import time

from randomfun2026solvers.lm1 import machine

from band_root_probe import price, shipped

KEY = ("deadman-3d", "taped")

CANDIDATES: dict[str, dict[str, int]] = {
    "shipped": shipped(),
    # `search_u(11)` — the best map that leaves the **root row where it is**, so
    # the fetch cell, the ROM pipe's west-wall attachment and the riser are all
    # untouched and only the trie's internal shape moves. -1,099,534 on the
    # dispatch derivative, 87% of the unconstrained optimum's.
    "shape11": {
        "IN": 0, "NEG": 1, "MOVA": 2, "INCM": 3, "ADDI": 4, "MUL": 5, "LDA": 6,
        "DIV": 7, "SUB": 13, "ADD": 14, "ST": 15, "LD": 16, "MODI": 17,
        "DIVI": 23, "SUBI": 24, "MULI": 25, "LDI": 26, "JMPS": 27, "BRN": 28,
        "BRZ": 29, "JMPF": 30, "SND": 31,
    },
    # `search_u(12)` — one row of root travel, and 9 of the 10 one-digit opcodes
    # kept, so the drum barely moves. -1,229,816.
    "root123": {
        "IN": 0, "NEG": 1, "MOVA": 2, "INCM": 3, "ADDI": 4, "MUL": 5, "LDA": 6,
        "DIV": 7, "SUB": 8, "ADD": 12, "ST": 14, "LD": 15, "MODI": 16,
        "DIVI": 17, "SUBI": 20, "MULI": 24, "LDI": 25, "JMPS": 27, "BRN": 28,
        "BRZ": 29, "JMPF": 30, "SND": 31,
    },
    # `band_root_probe.search_joint()` — dispatch and drum cells priced together
    # in tour ticks. Same dispatch optimum as `root125` (11,167,796) but 7,631
    # opcode cells instead of 9,098, so the drum barely widens.
    "joint125": {
        "IN": 0, "NEG": 1, "MOVA": 2, "INCM": 3, "ADDI": 4, "MUL": 5, "LDA": 6,
        "DIV": 7, "SUB": 8, "ADD": 11, "ST": 12, "LD": 14, "MODI": 15,
        "DIVI": 16, "SUBI": 18, "MULI": 20, "LDI": 24, "JMPS": 25, "BRN": 26,
        "BRZ": 28, "JMPF": 30, "SND": 31,
    },
    # `band_root_probe.search()` — four seeds, same optimum.
    "root125": {
        "IN": 0, "NEG": 1, "MOVA": 2, "INCM": 3, "ADDI": 4, "MUL": 5, "LDA": 6,
        "DIV": 7, "SUB": 8, "ADD": 9, "ST": 13, "LD": 14, "MODI": 15, "DIVI": 16,
        "SUBI": 19, "MULI": 21, "LDI": 24, "JMPS": 25, "BRN": 27, "BRZ": 28,
        "JMPF": 30, "SND": 31,
    },
}


def build(name: str) -> None:
    machine.OPCODE_SLOTS[KEY] = CANDIDATES[name]
    d = price(CANDIDATES[name])
    t = time.time()
    try:
        m = machine.build_for("deadman-3d", store="taped")
    except Exception as exc:  # noqa: BLE001 — the point is to see which assertion
        print(f"{name:10s} root {d['root_row'] + 99}  BUILD FAILED: "
              f"{type(exc).__name__}: {exc}")
        return
    print(f"{name:10s} root {d['root_row'] + 99}  {m.width}x{m.height}  "
          f"dispatch-derivative {d['total']:,}  ({time.time() - t:.0f}s)")
    if len(sys.argv) > 2 and sys.argv[2] == "--emit":
        from pathlib import Path

        out = Path(__file__).resolve().parents[1] / "littleman" / "examples"
        (out / "deadman-3d_taped.man").write_text("\n".join(m.rows) + "\n")
        m.debug_map().write_html(m.rows, out / "deadman-3d_taped.debug.html")
        m.debug_map().write_json(out / "deadman-3d_taped.debug.json")
        print("  emitted deadman-3d_taped.*")


def sweep_fold(name: str, folds=range(78, 108)) -> None:
    """Which ROM folds leave the seek teleport's V room its clearance.

    A map with fewer one-digit opcodes makes the drum wider, and the V room wants
    a clear column band between the drum's east edge and the machine's. Folding
    the drum into more rows narrows it again; rows are free on a width-bound
    machine, and ticks are the only metric here anyway.
    """
    machine.OPCODE_SLOTS[KEY] = CANDIDATES[name]
    for rows in folds:
        machine.SEEK_TIER_LAYOUT[KEY] = {"rom_rows": rows}
        try:
            m = machine.build_for("deadman-3d", store="taped")
        except Exception as exc:  # noqa: BLE001
            print(f"  rom_rows {rows:3d}: {str(exc)[:78]}")
            continue
        print(f"  rom_rows {rows:3d}: {m.width}x{m.height}  BINDS")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--folds":
        sweep_fold(sys.argv[1])
    else:
        for name in ([sys.argv[1]] if len(sys.argv) > 1 else list(CANDIDATES)):
            build(name)
