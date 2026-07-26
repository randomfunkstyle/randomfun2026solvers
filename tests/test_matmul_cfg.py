"""The matmul dataflow machine, at token level -- no grid in this phase."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from randomfun2026solvers import matmul_cfg
from randomfun2026solvers.matmul_cfg import (
    BIAS,
    BIAS3,
    LANE,
    LANES,
    WORKER,
    matmul_reference,
    simulate,
)

PROBLEM = Path(__file__).resolve().parents[1] / "tasks" / "problems" / "matmul.json"

#: The stated constraints, which is what everything here is sized against.
DIM_MIN, DIM_MAX = 2, 16
ENTRY_MAX = 99

BRANCH_LANES = {"X": {"neg", "zero", "pos"}, "x": {"one", "zero"}, "d": {"pos", "zero"}}


def _cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def _ids() -> list[str]:
    return [c["name"] for c in _cases()]


# ── the CFG is well formed ────────────────────────────────────────────────────
def test_every_successor_names_a_real_block() -> None:
    for name, (_toks, succ) in WORKER.items():
        targets = [succ] if isinstance(succ, str) else list(succ.values())
        for t in targets:
            assert t in WORKER, f"{name} -> {t} is not a block"


def test_branch_blocks_end_in_a_branch_glyph() -> None:
    for name, (toks, succ) in WORKER.items():
        if isinstance(succ, str):
            assert not toks or toks[-1] not in BRANCH_LANES, f"{name} ends in a branch"
        else:
            assert toks, f"{name} branches with no glyphs"
            glyph = toks[-1]
            assert glyph in BRANCH_LANES, f"{name} ends in {glyph!r}, not a branch"
            assert set(succ) == BRANCH_LANES[glyph], f"{name} names the wrong lanes"


def test_every_block_is_reachable_from_the_entry() -> None:
    seen, stack = {"HEAD"}, ["HEAD"]
    while stack:
        _toks, succ = WORKER[stack.pop()]
        for t in [succ] if isinstance(succ, str) else succ.values():
            if t not in seen:
                seen.add(t)
                stack.append(t)
    assert seen == set(WORKER)


# ── the packing is sized to the constraints, not to the public cases ──────────
def test_a_lane_holds_any_reachable_accumulator() -> None:
    worst = DIM_MAX * ENTRY_MAX * ENTRY_MAX  # 156,816
    assert BIAS > worst, "the bias must keep every lane positive"
    assert BIAS + worst < LANE, "a biased lane must stay inside its field"
    assert LANES * LANE.bit_length() - LANES <= 63


def test_a_packed_word_stays_inside_a_signed_64_bit_word() -> None:
    ones = sum(LANE**j for j in range(LANES))
    worst_b = ENTRY_MAX * ones  # the packed B word
    worst_acc = (BIAS + DIM_MAX * ENTRY_MAX * ENTRY_MAX) * ones
    assert ENTRY_MAX * worst_b < 2**63, "a*P overflows"
    assert worst_acc < 2**63, "the accumulator overflows"
    assert BIAS3 == BIAS * ones


# ── the machine computes the product ──────────────────────────────────────────
@pytest.mark.parametrize("case", _cases(), ids=_ids())
def test_every_public_case_is_emitted_exactly(case: dict) -> None:
    rounds = case["rounds"]
    assert len(rounds) == 1, "matmul is a single-round problem"
    values = [int(v) for v in rounds[0]["in"]]
    expected = [int(v) for v in rounds[0]["out"]]
    out, _tokens, _cells = simulate(values)
    assert out == expected


@pytest.mark.parametrize("dims", [(2, 2, 2), (16, 16, 16), (2, 16, 2), (16, 2, 16)])
@pytest.mark.parametrize("fill", ["max", "min", "alternating"])
def test_the_constraint_extremes(dims: tuple[int, int, int], fill: str) -> None:
    n, m, k = dims
    count = n * m + m * k
    if fill == "max":
        entries = [ENTRY_MAX] * count
    elif fill == "min":
        entries = [-ENTRY_MAX] * count
    else:
        entries = [ENTRY_MAX if i % 2 else -ENTRY_MAX for i in range(count)]
    values = [n, m, k, *entries]
    out, _tokens, _cells = simulate(values)
    assert out == matmul_reference(values)


def _shape_holds(n: int, m: int, k: int, rng: random.Random) -> None:
    values = [n, m, k] + [rng.randint(-ENTRY_MAX, ENTRY_MAX) for _ in range(n * m + m * k)]
    out, _tokens, _cells = simulate(values)
    assert out == matmul_reference(values), (n, m, k)


def test_the_shapes_that_exercise_the_group_padding() -> None:
    """`K mod 3` decides how many lanes of the last packed word are real, and
    `K = 2` is the one shape where a single group is the whole row."""
    rng = random.Random(20260726)
    for k in range(DIM_MIN, DIM_MAX + 1):
        _shape_holds(DIM_MAX, DIM_MAX, k, rng)
        _shape_holds(DIM_MIN, DIM_MIN, k, rng)
    for m in range(DIM_MIN, DIM_MAX + 1):
        _shape_holds(DIM_MAX, m, DIM_MAX, rng)
    for n in range(DIM_MIN, DIM_MAX + 1):
        _shape_holds(n, DIM_MAX, DIM_MAX, rng)


@pytest.mark.slow
def test_every_shape_in_the_stated_range() -> None:
    rng = random.Random(20260726)
    for n in range(DIM_MIN, DIM_MAX + 1):
        for m in range(DIM_MIN, DIM_MAX + 1):
            for k in range(DIM_MIN, DIM_MAX + 1):
                _shape_holds(n, m, k, rng)


def test_output_is_row_major_and_the_right_length() -> None:
    rng = random.Random(7)
    for n, m, k in ((5, 6, 4), (7, 5, 9), (3, 16, 11)):
        values = [n, m, k] + [rng.randint(-ENTRY_MAX, ENTRY_MAX) for _ in range(n * m + m * k)]
        out, _tokens, _cells = simulate(values)
        assert len(out) == n * k
        assert out == matmul_reference(values)


# ── the cost model this phase exists to measure ───────────────────────────────
def test_the_hot_loop_is_twelve_glyphs_for_three_macs() -> None:
    """One `MAC` block is one multiply, and one multiply is `LANES` MACs."""
    toks, succ = WORKER["MAC"]
    assert matmul_cfg.cell_cost(toks) == 12
    assert toks.count("*") == 1
    assert succ["pos"] == "MAC", "the counted loop must close on itself"


def test_ticks_per_mac_stays_in_single_digits_at_full_size() -> None:
    values = [16, 16, 16] + [99] * 512
    out, _tokens, cells = simulate(values)
    assert out == matmul_reference(values)
    assert cells / 4096 < 9.0, "a MAC must not cost double digits before layout"


def test_the_public_average_does_not_regress() -> None:
    """`footprint-tick` scores ``max(w,h)^2 * mean ticks``, so the mean is the
    number to hold down.  The CPU build sat at 153,786 ticks a case."""
    total = sum(simulate([int(v) for v in c["rounds"][0]["in"]])[2] for c in _cases())
    assert total / len(_cases()) < 7_000
