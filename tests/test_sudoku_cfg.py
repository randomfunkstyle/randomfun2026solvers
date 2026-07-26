"""The sudoku-validity block CFG: shape, semantics, and the six public cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers import sudoku_cfg

PROBLEM = Path(__file__).resolve().parents[1] / "tasks" / "problems" / "sudoku-validity.json"


def _cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def _expected(case: dict) -> list[int]:
    return [int(v) for rnd in case["rounds"] for v in rnd["out"]]


# ── the token program ─────────────────────────────────────────────────────────
def test_every_successor_names_a_real_block() -> None:
    for name, (_, succ) in sudoku_cfg.WORKER.items():
        targets = [succ] if isinstance(succ, str) else list(succ.values())
        for target in targets:
            assert target in sudoku_cfg.WORKER, f"{name} -> {target}"


def test_branch_blocks_end_in_a_branch_glyph() -> None:
    want = {"X": {"neg", "zero", "pos"}, "x": {"one", "zero"}, "d": {"pos", "zero"}}
    for name, (toks, succ) in sudoku_cfg.WORKER.items():
        if isinstance(succ, dict):
            assert toks[-1] in want, (name, toks[-1])
            assert set(succ) == want[toks[-1]], (name, sorted(succ))
        else:
            assert not (set(toks) & {"X", "x", "d"}), name


def test_only_branch_blocks_use_branch_glyphs() -> None:
    for name, (toks, succ) in sudoku_cfg.WORKER.items():
        for tok in toks[:-1]:
            assert tok not in {"X", "x", "d"}, f"{name} branches mid-block on {tok}"
        assert isinstance(succ, (str, dict))


def test_b_is_pinned_to_P_from_the_prologue_through_the_access() -> None:
    """B is the machine's one durable register: nothing may touch it after `M`.

    The whole design rests on `B = P` surviving the ring rotation, so this walks
    the actual path -- ROUND's tail, both rotation blocks, and ACCESS up to the
    last glyph that reads B -- and asserts no B-writing glyph appears.
    """
    writes_b = {"M", "W", "/", "%"}
    tail = sudoku_cfg.WORKER["ROUND"][0]
    tail = tail[len(tail) - tail[::-1].index("M") :]  # everything after `B = P`
    access = sudoku_cfg.WORKER["ACCESS"][0]
    path = tail + sudoku_cfg.WORKER["ROT1"][0] + sudoku_cfg.WORKER["ROT1_BODY"][0]
    path += access[: access.index("-") + 1]  # `-` is the last glyph that reads B
    offenders = [t for t in path if t in writes_b]
    assert not offenders, offenders


def test_the_ring_keeps_exactly_nine_words() -> None:
    """Every block pushes to the ring as often as it pops."""
    for name, (toks, _) in sudoku_cfg.WORKER.items():
        if name.startswith("FILL") or name == "INIT":
            continue
        assert toks.count("rr") == toks.count("sr"), name


def test_the_file_is_empty_at_the_top_of_every_round() -> None:
    """FILE is a plain FIFO, so ROUND + OK must balance their parks and pops."""
    round_toks = sudoku_cfg.WORKER["ROUND"][0]
    ok_toks = sudoku_cfg.WORKER["OK"][0]
    pushes = round_toks.count("sq") + ok_toks.count("sq")
    pops = round_toks.count("rq") + ok_toks.count("rq")
    assert pushes == pops


# ── behaviour ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_worker_matches_every_public_case(case: dict) -> None:
    out, _ticks = sudoku_cfg.simulate_worker(case["rounds"])
    assert out == _expected(case)


def test_the_valid_grid_emits_eighty_one_ones() -> None:
    case = next(c for c in _cases() if c["name"] == "a valid grid")
    out, _ = sudoku_cfg.simulate_worker(case["rounds"])
    assert out == [1] * 81


def test_a_violation_ends_the_case_with_a_single_zero() -> None:
    for case in _cases():
        expected = _expected(case)
        if expected[-1] != 0:
            continue
        out, _ = sudoku_cfg.simulate_worker(case["rounds"])
        assert out.count(0) == 1
        assert out[-1] == 0
        assert len(out) == len(expected)


def test_duplicates_in_each_unit_are_all_caught() -> None:
    """Row, column and box duplicates, plus a legal repeat that is none of them."""
    def rounds(cells: list[tuple[int, int, int]]) -> list[dict]:
        return [{"in": [str(r), str(c), str(v)]} for r, c, v in cells]

    row = rounds([(4, 0, 7), (4, 8, 7)])
    col = rounds([(0, 4, 7), (8, 4, 7)])
    box = rounds([(3, 3, 7), (5, 5, 7)])
    fine = rounds([(0, 0, 7), (4, 4, 7), (8, 8, 7)])
    for case in (row, col, box):
        assert sudoku_cfg.simulate_worker(case)[0] == [1, 0]
    assert sudoku_cfg.simulate_worker(fine)[0] == [1, 1, 1]


def test_every_value_and_every_unit_index_is_reachable() -> None:
    """A full legal Latin-square pass exercises all nine ring slots and all bits."""
    cells = [(r, c, (3 * (r % 3) + r // 3 + c) % 9 + 1) for r in range(9) for c in range(9)]
    out, _ = sudoku_cfg.simulate_worker(
        [{"in": [str(r), str(c), str(v)]} for r, c, v in cells]
    )
    assert out == [1] * 81


# ── cost ──────────────────────────────────────────────────────────────────────
def test_ticks_a_round_are_flat_in_the_value() -> None:
    """`v - 1` forward and `9 - v` back is eight slots whatever `v` is."""
    per_v = []
    for v in range(1, 10):
        # (0,0) and (4,4) share no row, column or box, so both rounds run whole.
        rounds = [{"in": ["0", "0", str(v)]}, {"in": ["4", "4", str(v)]}]
        _out, ticks = sudoku_cfg.simulate_worker(rounds)
        per_v.append(ticks)
    assert len(set(per_v)) == 1, per_v


def test_a_round_costs_under_a_hundred_ticks() -> None:
    case = next(c for c in _cases() if c["name"] == "a valid grid")
    _out, ticks = sudoku_cfg.simulate_worker(case["rounds"])
    assert ticks / len(case["rounds"]) < 100


def test_the_costed_table_agrees_with_the_simulator() -> None:
    row = sudoku_cfg.layout_costs()["C: 9 words x 27 bits, by value"]
    assert row["glyph_cells"] == sudoku_cfg.worker_glyph_cells()
    case = next(c for c in _cases() if c["name"] == "a valid grid")
    _out, ticks = sudoku_cfg.simulate_worker(case["rounds"])
    assert abs(ticks / len(case["rounds"]) - row["ticks_per_round"]) < 2
    assert row["rotation_ticks"] + row["other_ticks"] == row["ticks_per_round"]
