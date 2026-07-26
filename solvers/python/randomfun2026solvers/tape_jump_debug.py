#!/usr/bin/env python3
"""Generate a labelled debug bundle for the parameterized LM-1 tape STORE."""

from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.lm1.machine import (
    _resolve_tape_skip_batch,
    _resolve_tape_relay,
    tape_block,
)
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.memory_tape import (
    V2_JUMP4_IH,
    V2_JUMP4_IW,
    V2_JUMP_IH,
    V2_JUMP_IW,
)


def build_debug(
    cells: int,
    *,
    skip_batch: int | None = 2,
    jump_threshold: int = 128,
    relay_size: tuple[int, int] | None = None,
) -> tuple[list[str], DebugMap]:
    """Build one tape block and a coordinate-accurate explanatory overlay."""
    resolved = _resolve_tape_skip_batch(cells, skip_batch, jump_threshold)
    resolved_relay = _resolve_tape_relay(resolved, relay_size)[1]
    tape = tape_block(
        cells,
        skip_batch=skip_batch,
        jump_threshold=jump_threshold,
        relay_size=relay_size,
    )
    rows = [
        "".join(tape.cells.get((x, y), " ") for x in range(tape.width)).rstrip()
        for y in range(tape.height)
    ]
    debug = DebugMap(
        f"LM-1 tape STORE — cells={cells}, skip_batch={resolved}, "
        f"relay={resolved_relay}, jump_threshold={jump_threshold}, "
        f"capacity={tape.slots}"
    )

    # Both worker variants are placed at the same block origin.
    wx, wy = 8, 8
    worker_w = {1: 22, 2: V2_JUMP_IW, 4: V2_JUMP4_IW}[resolved]
    worker_h = {1: 18, 2: V2_JUMP_IH, 4: V2_JUMP4_IH}[resolved]
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
    elif resolved == 4:
        debug.region(
            "init",
            wx + 45,
            wy,
            4,
            5,
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
            "P1-bit-tail",
            wx + 24,
            wy + 4,
            12,
            3,
            note="x/] peels BP bits 0 and 1; conditionally advances 1 then 2 words",
            color="#84cc16",
        )
        debug.region(
            "P1-four-value-skip",
            wx + 36,
            wy + 6,
            11,
            2,
            note="bulk floor(addr/4) loop; four rs pairs per BP unit",
            color="#22c55e",
        )
        debug.region(
            "target-access",
            wx + 31,
            wy + 9,
            16,
            6,
            note="READ sends target to output+tape; WRITE replaces target",
            color="#f59e0b",
        )
        debug.region(
            "P2-bit-tail",
            wx + 22,
            wy + 14,
            14,
            4,
            note="exact cleanup for (N−1−addr) mod 4",
            color="#06b6d4",
        )
        debug.region(
            "P2-four-value-skip",
            wx + 36,
            wy + 17,
            11,
            2,
            note="bulk floor((N−1−addr)/4) loop; restores tape alignment",
            color="#14b8a6",
        )
        debug.lane(
            "main-return",
            [
                (wx + 46, wy + 19),
                (wx + 46, wy + 21),
                (wx + 48, wy + 21),
                (wx + 48, wy + 4),
            ],
            kind="control",
            expect="P2 and initializer share the proven return path to MAIN",
            color="#ec4899",
        )
    return rows, debug


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", type=int, default=200)
    parser.add_argument(
        "--skip-batch",
        choices=("1", "2", "4", "auto"),
        default="2",
    )
    parser.add_argument("--relay", help="relay interior WxH, for example 6x4 or 8x6")
    parser.add_argument("--jump-threshold", type=int, default=128)
    parser.add_argument("--man", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()

    batch = None if args.skip_batch == "auto" else int(args.skip_batch)
    relay_size = None
    if args.relay:
        try:
            rw, rh = args.relay.lower().split("x", 1)
            relay_size = (int(rw), int(rh))
        except (TypeError, ValueError) as exc:
            parser.error(f"--relay must be WxH, got {args.relay!r}: {exc}")
    rows, debug = build_debug(
        args.cells,
        skip_batch=batch,
        jump_threshold=args.jump_threshold,
        relay_size=relay_size,
    )
    args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    debug.write_html(rows, args.html)
    debug.write_json(args.json)


if __name__ == "__main__":
    main()
