#!/usr/bin/env python3
"""Generate a labelled debug bundle for the parameterized LM-1 tape STORE."""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.lm1.machine import (
    _resolve_tape_skip_batch,
    tape_block,
)
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_tape import (
    V2_JUMP_IH,
    V2_JUMP_IW,
)


def build_debug(
    cells: int,
    *,
    skip_batch: int | None = 2,
    jump_threshold: int = 128,
) -> tuple[list[str], DebugMap]:
    """Build one tape block and a coordinate-accurate explanatory overlay."""
    resolved = _resolve_tape_skip_batch(cells, skip_batch, jump_threshold)
    tape = tape_block(
        cells,
        skip_batch=skip_batch,
        jump_threshold=jump_threshold,
    )
    rows = [
        "".join(tape.cells.get((x, y), " ") for x in range(tape.width)).rstrip()
        for y in range(tape.height)
    ]
    debug = DebugMap(
        f"LM-1 tape STORE — cells={cells}, skip_batch={resolved}, "
        f"jump_threshold={jump_threshold}, capacity={tape.slots}"
    )

    # Both worker variants are placed at the same block origin.
    wx, wy = 8, 8
    worker_w = V2_JUMP_IW if resolved == 2 else 22
    worker_h = V2_JUMP_IH if resolved == 2 else 18
    debug.region(
        "worker",
        wx,
        wy,
        worker_w,
        worker_h,
        note=(
            f"{worker_w}×{worker_h} interior; request protocol is op, addr, "
            "optional write value"
        ),
        color="#38bdf8",
    )
    debug.region(
        "value-ring",
        1,
        wy + worker_h + 1,
        tape.width - 1,
        tape.height - (wy + worker_h + 1),
        note=(
            f"{tape.slots}-value pipe capacity; {cells + 1} required because a "
            "WRITE briefly holds replacement and displaced values"
        ),
        color="#10b981",
    )
    debug.lane(
        "request",
        [tape.in_cell, (wx - 1, tape.in_cell[1])],
        kind="pipe",
        expect="op, addr, optional value",
        color="#22c55e",
    )
    debug.lane(
        "read-response",
        [(tape.out_cell[0], wy - 1), tape.out_cell],
        kind="pipe",
        expect="READ value",
        color="#a78bfa",
    )

    if resolved == 2:
        debug.region(
            "init",
            wx + 29,
            wy,
            5,
            6,
            note=f"one-time fill of {cells} zero values",
            color="#64748b",
        )
        debug.region(
            "request-decode",
            wx,
            wy + 2,
            16,
            4,
            note="READ/WRITE setup; B preserves ±(N−addr)",
            color="#60a5fa",
        )
        debug.region(
            "P1-two-value-skip",
            wx + 19,
            wy + 6,
            5,
            2,
            note="advance addr words; two BP tests and two rs,m bodies per lap",
            color="#22c55e",
        )
        debug.lane(
            "P1-odd-tail",
            [(wx + 19, wy + 5), (wx + 23, wy + 5), (wx + 23, wy + 6)],
            kind="control",
            expect="odd final word re-enters with BP=0, then takes the common exit",
            color="#84cc16",
        )
        debug.region(
            "target-access",
            wx + 16,
            wy + 9,
            17,
            5,
            note="READ sends target to output+tape; WRITE replaces target",
            color="#f59e0b",
        )
        debug.region(
            "P2-two-value-skip",
            wx + 19,
            wy + 14,
            5,
            2,
            note="advance N−1−addr words and restore original tape alignment",
            color="#14b8a6",
        )
        debug.lane(
            "P2-odd-tail-and-merge",
            [(wx + 19, wy + 13), (wx + 23, wy + 13), (wx + 23, wy + 14)],
            kind="control",
            expect="READ, WRITE, and odd tail converge on the same P2 entry",
            color="#06b6d4",
        )
        debug.region(
            "main-return",
            wx + 23,
            wy + 16,
            11,
            2,
            note="single P2 exit returns through the east gutter to MAIN",
            color="#ec4899",
        )
    return rows, debug


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", type=int, default=200)
    parser.add_argument(
        "--skip-batch",
        choices=("1", "2", "auto"),
        default="2",
    )
    parser.add_argument("--jump-threshold", type=int, default=128)
    parser.add_argument("--man", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()

    batch = None if args.skip_batch == "auto" else int(args.skip_batch)
    rows, debug = build_debug(
        args.cells,
        skip_batch=batch,
        jump_threshold=args.jump_threshold,
    )
    args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    debug.write_html(rows, args.html)
    debug.write_json(args.json)


if __name__ == "__main__":
    main()
