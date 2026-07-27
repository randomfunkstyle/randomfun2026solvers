"""``gradebook_cfg``: the block CFG's op-level model against every gate we have.

Three layers, in order of how much they would hurt to get wrong:

* **The seven public cases**, round by round, exact.
* **The sixteen program-agnostic semantic tests** that ``test_lm1_gradebook.py``
  built for the LM-1 CPU -- a three-way TOP tie, an all-zero subject, TOP after a
  demoting SET, AVG flooring, AVG over every legal ``N``, every ``K``
  addressable, SET emitting nothing.  They gate a rebuild for free, so they are
  re-run here against the ring model rather than restated.
* **The structural invariants a placer will rely on**: every successor names a
  real block, every branching block names exactly the lanes its last token can
  produce, and the pipes are left where the next operation expects them.

Plus the size numbers, pinned so a regression in either direction is visible:
the worst legal batch the constraints allow is 10 rounds x 8 operations at
N=16, K=4, which is exactly the shape that shipped the CPU build 19/20 after it
passed 7/7 in public.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import gradebook_cfg as gb  # noqa: E402

SLUG = "gradebook"
PROBLEM = json.loads((REPO / "tasks" / "problems" / f"{SLUG}.json").read_text("utf-8"))
CASES = [
    (c["name"], [{"in": [int(v) for v in r["in"]], "out": [int(v) for v in r["out"]]}
                 for r in c["rounds"]])
    for c in PROBLEM["publicTestData"]
]

#: Ticks the op model charges for one operation at the worst legal size, by kind.
#: Measured, not estimated; a 10% drift in any of them is worth looking at.
OP_TICKS = {"GET": 175, "SET": 185, "AVG": 150, "TOP": 265}

#: Roster of sixteen students with four subjects, once per case.
ROSTER_TICKS = 2_000


# ── helpers ───────────────────────────────────────────────────────────────────
def _roster(students: list[tuple[int, ...]], subjects: int) -> dict:
    values: list[int] = [len(students), subjects]
    for rec in students:
        values.extend(rec)
    return {"in": values, "out": []}


def _batch(ops: list[tuple[int, ...]], expected: tuple[int, ...] = ()) -> dict:
    values: list[int] = [len(ops)]
    for op in ops:
        values.extend(op)
    return {"in": values, "out": list(expected)}


def _run(rounds: list[dict]) -> list[int]:
    out, _ = gb.simulate_worker(rounds)
    return out


def _expected(rounds: list[dict]) -> list[int]:
    return [v for r in rounds for v in r["out"]]


# ── the public data, in full ──────────────────────────────────────────────────
def test_the_seven_public_cases_are_all_here() -> None:
    assert [name for name, _ in CASES] == [
        "tiny roster walkthrough",
        "TOP demotion",
        "tie-break",
        "floor rounding",
        "mixed batch",
        "K=1 minimal",
        "N=16 K=4 max",
    ]
    assert len(CASES) == 7


@pytest.mark.parametrize(("name", "rounds"), CASES, ids=[n for n, _ in CASES])
def test_public_case_matches_exactly(name: str, rounds: list[dict]) -> None:
    assert _run(rounds) == _expected(rounds), name


@pytest.mark.parametrize(("name", "rounds"), CASES, ids=[n for n, _ in CASES])
def test_public_case_emits_each_round_in_order(name: str, rounds: list[dict]) -> None:
    """Round by round, so a right multiset in the wrong round still fails.

    The machine is a stream: it reads a round's operations and answers them
    before the next round's count arrives, so a prefix check on every round
    boundary is the same statement as "answered in the right round".
    """
    got = _run(rounds)
    at = 0
    for i, r in enumerate(rounds):
        assert got[at : at + len(r["out"])] == r["out"], f"{name}: round {i + 1}"
        at += len(r["out"])
    assert at == len(got), f"{name}: {len(got) - at} values too many"


# ── operation semantics, on hand-built rosters ────────────────────────────────
def test_top_breaks_a_three_way_tie_with_the_smallest_id() -> None:
    """Ids are not sorted, so "first seen wins" is *not* the same as "smallest"."""
    rounds = [
        _roster([(5000, 70), (1200, 70), (9999, 70), (3000, 10)], subjects=1),
        _batch([(4, 1)], (1200,)),
    ]
    assert _run(rounds) == [1200]


def test_top_on_an_all_zero_subject_still_names_a_student() -> None:
    """The seed must lose to a key of ``0 << sh | T``, and ``T >= 6385`` does it."""
    rounds = [
        _roster([(4000, 0), (2000, 0), (7000, 0), (9000, 0)], subjects=1),
        _batch([(4, 1)], (2000,)),
    ]
    assert _run(rounds) == [2000]


def test_top_follows_a_set_that_demotes_the_leader() -> None:
    rounds = [
        _roster([(4000, 90), (2000, 80), (7000, 70), (9000, 60)], subjects=1),
        _batch([(4, 1)], (4000,)),
        _batch([(2, 4000, 1, 10), (4, 1)], (2000,)),
        _batch([(2, 2000, 1, 5), (4, 1)], (7000,)),
    ]
    assert _run(rounds) == [4000, 2000, 7000]


@pytest.mark.parametrize(
    ("grades", "want"),
    [
        ((1, 2, 3, 3), 2),            # 9/4  = 2.25 -> 2
        ((0, 0, 0, 1), 0),            # 1/4  = 0.25 -> 0
        ((100, 100, 100, 100), 100),  # exact
        ((99, 100, 100, 100), 99),    # 399/4 = 99.75 -> 99
    ],
)
def test_avg_rounds_down(grades: tuple[int, ...], want: int) -> None:
    ids = (4001, 4002, 4003, 4004)
    rounds = [
        _roster([(i, g) for i, g in zip(ids, grades, strict=True)], subjects=1),
        _batch([(3, 1)], (want,)),
    ]
    assert _run(rounds) == [want]


@pytest.mark.parametrize("n", range(4, 17))
def test_avg_divides_by_every_legal_roster_size(n: int) -> None:
    """``N`` is the ring's own sentinel, so every roster size exercises it."""
    students = [(1000 + i, i + 1) for i in range(n)]
    want = (n + 1) // 2
    assert _run([_roster(students, 1), _batch([(3, 1)], (want,))]) == [want]


@pytest.mark.parametrize("k", range(1, 5))
def test_every_subject_of_a_full_width_roster_is_addressable(k: int) -> None:
    """``K < 4`` is padded up to four fields, so no subject reads its neighbour."""
    students = [(2000 + i, *[10 * (s + 1) + i for s in range(k)]) for i in range(4)]
    ops = [(1, 2000 + i, s + 1) for i in range(4) for s in range(k)]
    want = [10 * (s + 1) + i for i in range(4) for s in range(k)]
    assert _run([_roster(students, k), _batch(ops, tuple(want))]) == want, f"K={k}"


def test_set_emits_nothing_and_get_sees_it() -> None:
    rounds = [
        _roster([(1111, 1, 2), (2222, 3, 4), (3333, 5, 6), (4444, 7, 8)], subjects=2),
        _batch([(2, 3333, 2, 42)], ()),
        _batch([(1, 3333, 2), (1, 3333, 1)], (42, 5)),
    ]
    assert _run(rounds) == [42, 5]


def test_a_set_to_zero_and_to_a_hundred_both_survive_the_packing() -> None:
    """The field is 11 bits wide for AVG's sake; the ends of the range prove it."""
    rounds = [
        _roster([(1111, 50, 50, 50, 50), (2222, 50, 50, 50, 50),
                 (3333, 50, 50, 50, 50), (4444, 50, 50, 50, 50)], subjects=4),
        _batch([(2, 2222, 1, 0), (2, 2222, 4, 100), (1, 2222, 1), (1, 2222, 4),
                (1, 2222, 2), (1, 2222, 3)], (0, 100, 50, 50)),
    ]
    assert _run(rounds) == [0, 100, 50, 50]


def test_avg_is_exact_when_every_column_is_at_its_maximum() -> None:
    """Sixteen students x 100 = 1,600 a field, the number the 11 bits are for."""
    students = [(1000 + i, 100, 100, 100, 100) for i in range(16)]
    ops = [(3, s + 1) for s in range(4)]
    assert _run([_roster(students, 4), _batch(ops, (100,) * 4)]) == [100] * 4


def test_the_id_column_cannot_carry_into_subject_four() -> None:
    """Sixteen complements sum to at most 246,024, which is why the base is 2^18.

    The tightest legal roster is the sixteen smallest ids, since ``T = 16384 -
    id``.  At base 2^14 this test reads a corrupted subject 4.
    """
    students = [(1000 + i, 7, 7, 7, 3) for i in range(16)]
    assert _run([_roster(students, 4), _batch([(3, 4), (3, 1)], (3, 7))]) == [3, 7]


def test_a_search_that_wraps_the_sentinel_still_finds_its_student() -> None:
    """Repeatedly hitting the last student walks the rings past the sentinel.

    The rings are re-aligned after every search, so the next full column scan
    starts where a lap can see all sixteen cells; a missing ``REST`` shows up
    here as a wrong ``AVG`` two operations later.
    """
    students = [(1000 + 7 * i, i, 100 - i) for i in range(16)]
    ops: list[tuple[int, ...]] = []
    want: list[int] = []
    for i in (15, 15, 0, 15, 8, 15):
        ops.append((1, 1000 + 7 * i, 1))
        want.append(i)
    ops.append((3, 1))
    want.append(sum(range(16)) // 16)
    ops.append((4, 2))
    want.append(1000)
    assert _run([_roster(students, 2), _batch(ops, tuple(want))]) == want


def test_a_batch_of_eight_operations_is_answered_in_full() -> None:
    """``O`` rides the backpack; eight is the largest batch the rules allow."""
    students = [(1000 + i, 10 + i) for i in range(4)]
    ops = [(1, 1000 + (i % 4), 1) for i in range(8)]
    want = [10 + (i % 4) for i in range(8)]
    assert _run([_roster(students, 1), _batch(ops, tuple(want))]) == want


def test_ten_rounds_of_eight_operations_all_land() -> None:
    students = [(1000 + i, 10 + i) for i in range(4)]
    rounds = [_roster(students, 1)]
    want: list[int] = []
    for _ in range(10):
        ops = [(1, 1000 + (i % 4), 1) for i in range(8)]
        got = [10 + (i % 4) for i in range(8)]
        rounds.append(_batch(ops, tuple(got)))
        want += got
    assert _run(rounds) == want


# ── the worst legal batch ─────────────────────────────────────────────────────
def _worst_legal_case(op: str) -> tuple[list[dict], list[int]]:
    """N=16, K=4, ten rounds of eight ``op``s -- the shape public data never hits."""
    n, k = 16, 4
    ids = [1000 + (i * 563) % 9000 for i in range(n)]
    grades = [[(i * 7 + s * 13) % 101 for s in range(k)] for i in range(n)]
    roster: list[int] = [n, k]
    for i in range(n):
        roster += [ids[i], *grades[i]]

    rounds = [{"in": roster, "out": []}]
    want: list[int] = []
    for _ in range(10):
        rin: list[int] = [8]
        rout: list[int] = []
        for j in range(8):
            s = (j % k) + 1
            if op == "TOP":
                rin += [4, s]
                rout.append(ids[max(range(n), key=lambda i: (grades[i][s - 1], -ids[i]))])
            elif op == "AVG":
                rin += [3, s]
                rout.append(sum(grades[i][s - 1] for i in range(n)) // n)
            elif op == "GET" or j == 7:  # a pure-SET round emits nothing to gate on
                rin += [1, ids[n - 1], s]
                rout.append(grades[n - 1][s - 1])
            else:
                rin += [2, ids[n - 1], s, 50]
                grades[n - 1][s - 1] = 50
        rounds.append({"in": rin, "out": rout})
        want += rout
    return rounds, want


@pytest.mark.parametrize("op", sorted(OP_TICKS))
def test_the_worst_legal_batch_is_right_and_within_budget(op: str) -> None:
    rounds, want = _worst_legal_case(op)
    got, ticks = gb.simulate_worker(rounds)
    assert got == want, f"80x {op}: wrong answers"
    budget = ROSTER_TICKS + 80 * OP_TICKS[op]
    assert ticks < budget, f"80x {op}: {ticks:,} ticks over the {budget:,} budget"


def test_the_roster_is_the_smaller_half_of_the_bill() -> None:
    """Sanity on where the ticks are: setup must not dominate a full batch."""
    rounds, _ = _worst_legal_case("GET")
    _, roster_only = gb.simulate_worker([rounds[0]])
    _, whole = gb.simulate_worker(rounds)
    assert roster_only < ROSTER_TICKS
    assert roster_only * 4 < whole


# ── structure, for the placer ─────────────────────────────────────────────────
def test_every_successor_names_a_real_block() -> None:
    for name, (_, succ) in gb.WORKER.items():
        targets = [succ] if isinstance(succ, str) else list(succ.values())
        for t in targets:
            assert t in gb.WORKER, f"{name} -> {t}"


def test_every_branching_block_names_exactly_its_lanes() -> None:
    """A block's last token fixes its lane set; a stray lane is a routing bug."""
    lanes = {"X": {"neg", "zero", "pos"}, "x": {"one", "zero"}, "d": {"pos", "zero"}}
    for name, (toks, succ) in gb.WORKER.items():
        last = toks[-1] if toks else ""
        if isinstance(succ, str):
            assert last not in lanes, f"{name} branches but names one successor"
            continue
        assert last in lanes, f"{name} names lanes but ends in {last!r}"
        assert set(succ) == lanes[last], f"{name}: {sorted(succ)} != {sorted(lanes[last])}"


def test_every_block_is_reachable_from_init() -> None:
    seen, stack = {"INIT"}, ["INIT"]
    while stack:
        _, succ = gb.WORKER[stack.pop()]
        for t in ([succ] if isinstance(succ, str) else succ.values()):
            if t not in seen:
                seen.add(t)
                stack.append(t)
    assert seen == set(gb.WORKER), f"unreachable: {sorted(set(gb.WORKER) - seen)}"


def test_the_token_table_is_closed() -> None:
    known = set("M W N / % b m ] X x d H".split()) | set(gb._BIN)
    pipes = {"ri", "so", "rr", "sr", "rq", "sq", "rt", "st"}
    for name, (toks, _) in gb.WORKER.items():
        for t in toks:
            ok = t in known or t in pipes or (t.startswith("L") and t[1:].isdigit())
            assert ok, f"{name}: unknown token {t!r}"


def test_the_machine_stays_small_enough_to_be_worth_laying_out() -> None:
    """Footprint is squared in the score, so block count is the thing to watch."""
    assert len(gb.WORKER) <= 40
    assert gb.worker_glyph_cells() <= 400


def test_the_layout_table_prices_every_alternative() -> None:
    table = gb.layout_costs()
    chosen = table["A: student-major packed word, two rings"]
    assert chosen["blocks"] == len(gb.WORKER)
    assert chosen["glyph_cells"] == gb.worker_glyph_cells()
    for name, row in table.items():
        assert row["score"] == row["side"] ** 2 * row["ticks_per_op"], name
    best = min(table, key=lambda k: table[k]["score"])
    assert best == "A: student-major packed word, two rings", best
