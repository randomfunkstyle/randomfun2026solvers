#!/usr/bin/env python3
"""Named code containers for the compact one-pass MEMORY layout.

This is the fresh replacement for the discarded wide control draft.  It has no
global canvas coordinates: those belong to the final assembly.  The containers
are built from the flow-reviewed two-arm protocol in :mod:`memory_onepass_plan`.
"""
from __future__ import annotations

from dataclasses import dataclass

from randomfun2026solvers.memory_blocks import Block, counted_pass, vertical_rail
from randomfun2026solvers.memory_tape import lit


@dataclass(frozen=True)
class OnePassContainers:
    """Reusable code blocks; each operation uses only one of the two passes."""

    shared_delta: Block
    read_commit: Block
    write_commit: Block
    read_pass: Block
    write_pass: Block


def delta_ops(size: int) -> str:
    """A=current, B=address -> A=B=(address-current) mod size."""
    return "W-M" + lit(size) + "W%M"


def commit_ops(size: int) -> str:
    """A=current, B=delta -> store current+delta+1 modulo size.

    BP is intentionally untouched, so it remains the pass count.  The final
    ``1sWs`` is the index-cell FIFO store protocol.
    """
    return "+M1+M" + lit(size) + "W%M1sWs"


def containers(size: int) -> OnePassContainers:
    if size <= 0:
        raise ValueError("size must be positive")
    return OnePassContainers(
        shared_delta=vertical_rail(
            "shared-delta",
            delta_ops(size),
            note="current/address to relative delta; opcode remains in BP",
            color="#60a5fa",
        ),
        read_commit=vertical_rail(
            "read-commit",
            "b" + commit_ops(size),
            note="read arm: BP=delta, commit current before its only pass",
            color="#a78bfa",
        ),
        write_commit=vertical_rail(
            "write-commit",
            "b" + commit_ops(size),
            note="write arm: BP=delta, commit current before its only pass",
            color="#fb923c",
        ),
        read_pass=counted_pass(
            "read-pass",
            "rs",
            note="read arm rotates exactly delta tape values",
            color="#22c55e",
        ),
        write_pass=counted_pass(
            "write-pass",
            "rs",
            note="write arm rotates exactly delta tape values",
            color="#14b8a6",
        ),
    )


def _self_test() -> None:
    blocks = containers(10)
    assert blocks.shared_delta.height == len(delta_ops(10)) + 1
    assert blocks.read_commit.height == len("b" + commit_ops(10)) + 1
    assert blocks.read_pass.width == blocks.write_pass.width == 3
    assert blocks.read_pass.height == blocks.write_pass.height == 4
    assert blocks.read_commit.ports["out"].heading == (0, 1)


if __name__ == "__main__":
    _self_test()
