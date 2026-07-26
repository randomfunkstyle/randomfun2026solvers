"""The parameterized two-value skip worker for large rotating tapes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers.circuit import Circuit
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.lm1 import machine
from randomfun2026solvers.lm1.asm import assemble
from randomfun2026solvers.memory_tape import (
    V2_JUMP_IH,
    V2_JUMP_IW,
    worker_v2_jump,
)
from randomfun2026solvers.tape_jump_debug import build_debug

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "littleman" / "examples" / "memory-tape-jump-200"
PROBE_ASM = """
        LDI 11
        ST  1
        LDI 22
        ST  2
        LDI 33
        ST  99
        LDI 44
        ST  100
        LDI 55
        ST  198
        LDI 66
        ST  199
        LD  1
        OUT
        LD  2
        OUT
        LD  99
        OUT
        LD  100
        OUT
        LD  198
        OUT
        LD  199
        OUT
        HALT
dead:   ADDI 0
        SUBI 0
        BRZ dead
        JMPF dead
"""
PROBE_OUTPUT = [11, 22, 33, 44, 55, 66]


def test_horizontal_counted_ring_has_two_exact_tests() -> None:
    c = Circuit(7, 4)

    exits = c.counted_ring_horizontal(1, 1, "rs")

    assert exits == [(5, 3), (1, 0)]
    assert c.rows()[1][1:6] == "drsmv"
    assert c.rows()[2][1:6] == "^msrd"
    assert c.rows()[1].count("d") + c.rows()[2].count("d") == 2


def test_jump_worker_and_tape_are_parameterized_by_size_and_batch() -> None:
    worker = worker_v2_jump(200)
    baseline = machine.tape_block(200, skip_batch=1)
    jump = machine.tape_block(200, skip_batch=2)

    assert (worker.w, worker.h) == (V2_JUMP_IW, V2_JUMP_IH)
    assert jump.slots >= 201
    assert baseline.slots >= 201
    assert jump.cells != baseline.cells
    assert (
        machine.tape_block(199, skip_batch=None, jump_threshold=200).cells
        == machine.tape_block(199, skip_batch=1).cells
    )
    assert (
        machine.tape_block(200, skip_batch=None, jump_threshold=200).cells
        == jump.cells
    )
    with pytest.raises(ValueError, match="skip_batch must be 1, 2, or None"):
        machine.tape_block(200, skip_batch=3)


def test_size_200_debug_bundle_matches_its_parameterized_generator() -> None:
    rows, debug = build_debug(200, skip_batch=None, jump_threshold=128)

    assert rows == EXAMPLE.with_suffix(".man").read_text().rstrip("\n").splitlines()
    assert debug.to_dict() == json.loads(EXAMPLE.with_suffix(".json").read_text())
    html = EXAMPLE.with_suffix(".html").read_text()
    assert debug.title in html
    assert "P1-two-value-skip" in html
    assert "P2-odd-tail-and-merge" in html


@pytest.mark.slow
def test_two_value_skip_round_trips_odd_even_and_boundary_addresses_faster() -> None:
    """Behavior is identical and speed is relative to this run's baseline."""
    program = assemble(PROBE_ASM, name="jump-tape-probe")
    results = {}
    for batch in (1, 2):
        built = machine.build(
            program,
            tape_n=200,
            tape_skip_batch=batch,
            mem_pad=0,
        )
        assert built.tape_skip_batch == batch
        assert f"skip_batch={batch}" in built.report()
        results[batch] = FastLittleman("\n".join(built.rows)).run(
            expected=PROBE_OUTPUT,
            max_ticks=500_000,
        )
        assert results[batch].passed
        assert results[batch].output == PROBE_OUTPUT
        assert results[batch].fatal is None

    assert results[2].step < results[1].step
