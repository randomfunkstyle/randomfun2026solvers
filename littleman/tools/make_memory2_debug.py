#!/usr/bin/env python3
"""Generate interactive debug sidecars for the checked-in memory2 program."""
from __future__ import annotations

import argparse
from pathlib import Path

from randomfun2026solvers.man_debug import DebugMap


ROOT = Path(__file__).resolve().parents[2]
MACHINE = ROOT / "littleman" / "examples" / "memory2.man"


def build_debug_map() -> DebugMap:
    """Named geometry for memory2.man's fixed 31x32 layout."""
    dbg = DebugMap("memory2: compact two-pass tape")

    dbg.region("worker", 6, 1, 22, 23, note="main control room", color="#38bdf8")
    dbg.region("init", 7, 1, 14, 3, note="fill the 100-value tape with zeroes", color="#64748b")
    dbg.region("main-dispatch", 6, 5, 14, 3, note="operation 0 selects read; operation 1 selects write", color="#60a5fa")
    dbg.region("first-pass", 15, 8, 8, 5, note="pass address values through the tape", color="#22c55e")
    dbg.region("target-dispatch", 15, 13, 7, 3, note="sign of the preserved operation state selects target action", color="#f59e0b")
    dbg.region("write-target", 7, 13, 15, 5, note="read input value, append it, then discard old target", color="#fb923c")
    dbg.region("read-target", 21, 15, 6, 3, note="read target and send it to output and tape", color="#a78bfa")
    dbg.region("second-pass", 15, 18, 4, 6, note="pass the remaining values to restore address alignment", color="#14b8a6")
    dbg.region("relay", 0, 27, 6, 5, note="tape turnaround room", color="#fb7185")
    dbg.region("input-room", 0, 4, 3, 3, note="operation stream", color="#22c55e")
    dbg.region("output-room", 0, 0, 3, 3, note="read results", color="#a78bfa")

    dbg.lane("input-pipe", [(3, 5), (5, 5)], kind="pipe", expect="operations enter the worker", color="#22c55e")
    dbg.lane("output-pipe", [(5, 1), (3, 1)], kind="pipe", expect="read results leave the worker", color="#a78bfa")
    dbg.lane(
        "tape-forward-pipe",
        [(29, 10), (30, 10), (30, 27), (6, 27)],
        kind="pipe",
        expect="worker sends tape values around the outer turnaround", color="#34d399",
    )
    dbg.lane(
        "tape-return-pipe",
        [(6, 28), (29, 28), (29, 27), (30, 27)],
        kind="pipe",
        expect="relay returns tape values to the worker", color="#10b981",
    )

    dbg.lane(
        "read-setup",
        [(6, 5), (17, 5), (19, 5), (19, 8), (15, 8)],
        kind="expected",
        expect="load address and prepare the first tape pass", color="#60a5fa",
    )
    dbg.lane(
        "write-setup",
        [(8, 6), (19, 6), (19, 8), (15, 8)],
        kind="expected",
        expect="load address and retain write state through the first pass", color="#fb923c",
    )
    dbg.lane(
        "first-tape-pass",
        [(15, 8), (18, 8), (18, 12), (15, 12), (15, 8)],
        kind="expected",
        expect="rotate exactly address values", color="#22c55e",
    )
    dbg.lane(
        "read-target-access",
        [(20, 14), (20, 15), (21, 15), (26, 15), (26, 17), (18, 17), (18, 18)],
        kind="expected",
        expect="target is emitted and put back on tape", color="#a78bfa",
    )
    dbg.lane(
        "write-target-access",
        [(20, 14), (20, 13), (7, 13), (7, 16), (18, 16), (18, 18)],
        kind="expected",
        expect="new input replaces the old target", color="#fb923c",
    )
    dbg.lane(
        "second-tape-pass",
        [(15, 18), (18, 18), (18, 22), (15, 22), (15, 18)],
        kind="expected",
        expect="rotate the remaining values so logical address zero returns to the head", color="#14b8a6",
    )
    dbg.scenario(
        "write-read-7",
        "1 7 42 0 7",
        700,
        2400,
        watch=["write-setup", "first-tape-pass", "write-target-access", "second-tape-pass", "tape-forward-pipe", "tape-return-pipe"],
        note="write 42 at address 7, then read it",
    )
    return dbg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", type=Path, default=MACHINE.with_suffix(".debug.html"))
    parser.add_argument("--json", type=Path, default=MACHINE.with_suffix(".debug.json"))
    args = parser.parse_args()

    rows = MACHINE.read_text(encoding="utf-8").rstrip("\n").splitlines()
    debug = build_debug_map()
    debug.write_html(rows, args.html)
    debug.write_json(args.json)
    print(args.html)
    print(args.json)


if __name__ == "__main__":
    main()
