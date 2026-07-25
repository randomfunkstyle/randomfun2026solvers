#!/usr/bin/env python3
"""Render the current flow-reviewed one-pass MEMORY containers as HTML.

This is a placement preview, not an executable Littleman program.  It is built
directly from the same named containers that will form the final worker, so
hover regions and lanes remain coupled to the algorithm while routing evolves.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "solvers" / "python"))

from randomfun2026solvers.memory_blocks import (  # noqa: E402
    Assembly,
    backpack_branch_east,
    east_fork,
    fetch_transaction,
    register_cell,
)
from randomfun2026solvers.memory_onepass_compact import containers  # noqa: E402


def build_preview(size: int) -> tuple[list[str], object]:
    """Place real containers compactly; runner tracks and pipes come next."""
    blocks = containers(size)
    flow = Assembly(52, 52, f"one-pass compact n={size}: container placement preview")
    gap = 2

    index = flow.place_device(
        "index",
        register_cell("index", note="persistent tape-head address", color="#facc15"),
        (2, 3),
    )
    setup_fetch = flow.place(
        "setup-fetch",
        fetch_transaction("setup-fetch", gap=2, note="fetch old current", color="#facc15"),
        (index.device.width + gap + 2, 2),
    )
    shared = flow.place(
        "shared-delta",
        blocks.shared_delta,
        (setup_fetch.origin[0] + setup_fetch.block.width - 1, setup_fetch.origin[1] + 2),
    )
    branch = flow.place(
        "opcode-branch",
        backpack_branch_east("opcode-branch", note="zero/read, positive/write", color="#f59e0b"),
        (shared.origin[0], shared.origin[1] + shared.block.height),
    )

    arm_top = branch.origin[1] + branch.block.height + gap
    read_fetch = flow.place(
        "read-index-fetch",
        fetch_transaction("read-index-fetch", gap=2, note="re-fetch current for read commit", color="#facc15"),
        (3, arm_top),
    )
    read_commit = flow.place(
        "read-commit",
        blocks.read_commit,
        (read_fetch.origin[0] + read_fetch.block.width + gap, arm_top),
    )
    write_fetch = flow.place(
        "write-index-fetch",
        fetch_transaction("write-index-fetch", gap=2, note="re-fetch current for write commit", color="#facc15"),
        (28, arm_top),
    )
    write_commit = flow.place(
        "write-commit",
        blocks.write_commit,
        (write_fetch.origin[0] + write_fetch.block.width + gap, arm_top),
    )

    pass_top = arm_top + max(read_commit.block.height, write_commit.block.height) + gap
    read_pass = flow.place("read-pass", blocks.read_pass, (9, pass_top))
    write_pass = flow.place("write-pass", blocks.write_pass, (31, pass_top))
    fork = flow.place(
        "read-target-fork",
        east_fork("read-target-fork", note="separate output/tape copies", color="#a78bfa"),
        (read_pass.origin[0] + read_pass.block.width + gap, pass_top),
    )

    # These lanes are the dataflow review, not fabricated runner tracks.
    flow.debug.lane(
        "shared-current",
        [
            (index.origin[0] + index.device.width, index.origin[1] + 3),
            (index.origin[0] + index.device.width, setup_fetch.origin[1]),
            setup_fetch.origin,
        ],
        kind="expected",
        expect="index fetch supplies current to the shared delta block",
        color="#facc15",
    )
    flow.debug.lane(
        "delta-read-arm",
        [(branch.origin[0] + 1, branch.origin[1]), (branch.origin[0] + 1, read_fetch.origin[1]), read_fetch.origin],
        kind="expected",
        expect="opcode zero selects the read arm; delta stays in BP",
        color="#a78bfa",
    )
    flow.debug.lane(
        "delta-write-arm",
        [(branch.origin[0], branch.origin[1] + 1), (branch.origin[0], write_fetch.origin[1]), write_fetch.origin],
        kind="expected",
        expect="opcode positive selects the write arm; delta stays in BP",
        color="#fb923c",
    )
    flow.debug.lane(
        "read-single-pass",
        [read_commit.origin, (read_commit.origin[0], read_pass.origin[1]), read_pass.origin],
        kind="expected",
        expect="read arm commits current, then rotates delta values once",
        color="#22c55e",
    )
    flow.debug.lane(
        "write-single-pass",
        [write_commit.origin, (write_commit.origin[0], write_pass.origin[1]), write_pass.origin],
        kind="expected",
        expect="write arm commits current, then rotates delta values once",
        color="#14b8a6",
    )
    flow.debug.lane(
        "read-target-split",
        [read_pass.origin, fork.origin],
        kind="expected",
        expect="Y sends the target separately to output and tape",
        color="#a78bfa",
    )
    flow.debug.scenario(
        "write-read-7",
        "1 7 42 0 7",
        0,
        0,
        watch=["shared-current", "delta-write-arm", "write-single-pass", "delta-read-arm", "read-single-pass"],
        note="one pass per access; the index moves to address + 1 before each pass",
    )
    return flow.rows(), flow.debug


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    rows, debug = build_preview(args.size)
    debug.write_html(rows, args.html)
    debug.write_json(args.json)


if __name__ == "__main__":
    main()
