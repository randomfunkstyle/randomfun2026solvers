"""``gradebook`` end to end: emulator semantics, tick budget, generated grid.

The generic sweep in ``test_lm1_programs.py`` runs every program against every
public case and compares the *concatenation* of all rounds' output. That is not
enough for a round-based, stateful problem, so this file adds the three things it
cannot say:

* **Round by round.** Each round's replies are checked against that round's
  expected values, so a program that emitted the right multiset in the wrong round
  fails here. (The emulator also gates input on output, so a round that answered
  late would deadlock rather than pass — this pins the ordering explicitly.)
* **The operations' corner cases**, on hand-built rosters: a three-way tie for
  `TOP`, an all-zeros subject (which is what the "seed from student 0" trick in
  ``gradebook.asm`` has to get right), `AVG` flooring, and `SET` demoting the
  current leader.
* **The real tick bill.** The emulator charges a flat 6 ticks per word exchanged
  with STORE, ~150x less than the generated machine's tape (``ARCH.md`` §4.1:
  ``105 + 8.3N`` per access, and *every* variable in this program lives in the
  tape). Re-billed properly, the worst public case must still fit ``TICK_CAP``.

Plus the two machine-level gates ``brackets``/``tcp`` get in
``test_lm1_machine.py``: the checked-in ``.man`` is what the generator emits, and
(behind ``LM1_SLOW=1``) every public case passes on the real interpreter.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1 import machine  # noqa: E402
from randomfun2026solvers.lm1.emulator import TICK_CAP, Emulator, Round, RunResult  # noqa: E402
from randomfun2026solvers.lm1.isa import Sem  # noqa: E402
from randomfun2026solvers.lm1.programs import load, problem_json, rounds_for_problem  # noqa: E402
from randomfun2026solvers.lm1.store import DictStore  # noqa: E402

SLUG = "gradebook"
GRID = REPO / "tasks" / "solutions" / f"{SLUG}_cpu.man"
MAX_INSTRUCTIONS = 3_000_000

#: Slots the generated machine's tape must hold: scratch 1..13, ids 14..29, grades
#: 30..93 (``GRD + 4*15 + 3``), sized from the *constraints* (N <= 16, K <= 4).
TAPE_N = machine.TAPE_SIZE[SLUG]

CASES = rounds_for_problem(SLUG)

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)
slow = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the full public-case sweep on the real interpreter",
)


class _CountingStore(DictStore):
    """A ``DictStore`` that counts logical accesses, for the real tick model."""

    def __init__(self) -> None:
        super().__init__()
        self.ops = 0

    def _read(self, addr: int) -> int:
        self.ops += 1
        return super()._read(addr)

    def _write(self, addr: int, value: int) -> None:
        self.ops += 1
        super()._write(addr, value)


def _run(rounds: list[Round]) -> tuple[RunResult, _CountingStore]:
    store = _CountingStore()
    res = Emulator(load(SLUG), store=store).run(rounds, max_instructions=MAX_INSTRUCTIONS)
    return res, store


def _per_round(res: RunResult, rounds: list[Round]) -> list[tuple[int, ...]]:
    """Split the flat output back into one tuple per round."""
    out, at = [], 0
    for r in rounds:
        out.append(res.output[at : at + len(r.expected)])
        at += len(r.expected)
    return out


def _roster(students: list[tuple[int, ...]], subjects: int) -> Round:
    """Round 1: ``N K`` then the records, back to back. No output."""
    values: list[int] = [len(students), subjects]
    for rec in students:
        values.extend(rec)
    return Round(input=tuple(values))


def _batch(ops: list[tuple[int, ...]], expected: tuple[int, ...]) -> Round:
    """A later round: ``O`` then the operations, back to back."""
    values: list[int] = [len(ops)]
    for op in ops:
        values.extend(op)
    return Round(input=tuple(values), expected=expected)


# ── the public data, in full ──────────────────────────────────────────────────
def test_the_seven_public_cases_are_all_here() -> None:
    """Guards against a shrinking parametrization silently reducing coverage."""
    assert [name for name, _ in CASES] == [
        "tiny roster walkthrough",
        "TOP demotion",
        "tie-break",
        "floor rounding",
        "mixed batch",
        "K=1 minimal",
        "N=16 K=4 max",
    ]
    assert len(CASES) == len(problem_json(SLUG)["publicTestData"]) == 7


@pytest.mark.parametrize(("name", "rounds"), CASES, ids=[n for n, _ in CASES])
def test_public_case_matches_round_by_round(name: str, rounds: list[Round]) -> None:
    res, _ = _run(rounds)
    got = _per_round(res, rounds)
    want = [r.expected for r in rounds]
    assert got == want, f"{name}: round-by-round mismatch ({res.reason})"
    # Nothing extra after the last expected value, and no fault on the way.
    assert len(res.output) == sum(len(r.expected) for r in rounds)
    assert res.reason in ("halted", "input-exhausted"), f"{name}: {res.reason}"


def test_every_public_case_leaves_the_roster_where_the_layout_says() -> None:
    """Ids land in 14..29 and grades in 30..93 — never on a scratch slot.

    A cursor bug that walked one slot low would corrupt ``GEND`` and still pass
    some cases; this pins the arrays inside their own address ranges.
    """
    for name, rounds in CASES:
        _, store = _run(rounds)
        n, k = rounds[0].input[0], rounds[0].input[1]
        ids = {14 + i for i in range(n)}
        grades = {30 + 4 * i + s for i in range(n) for s in range(k)}
        touched = {a for a, v in store.snapshot().items() if a >= 14}
        assert touched <= ids | grades, f"{name}: wrote outside the arrays: {sorted(touched)}"
        assert ids <= touched, f"{name}: not every id slot was written"


# ── operation semantics, on hand-built rosters ────────────────────────────────
def test_top_breaks_a_three_way_tie_with_the_smallest_id() -> None:
    """Ids are not sorted, so "first seen wins" is *not* the same as "smallest"."""
    rounds = [
        _roster([(5000, 70), (1200, 70), (9999, 70), (3000, 10)], subjects=1),
        _batch([(4, 1)], expected=(1200,)),
    ]
    res, _ = _run(rounds)
    assert res.output == (1200,)


def test_top_on_an_all_zero_subject_still_names_a_student() -> None:
    """The seed is student 0's own grade, not a 0 sentinel that ties with everyone."""
    rounds = [
        _roster([(4000, 0), (2000, 0), (7000, 0), (9000, 0)], subjects=1),
        _batch([(4, 1)], expected=(2000,)),
    ]
    res, _ = _run(rounds)
    assert res.output == (2000,)


def test_top_follows_a_set_that_demotes_the_leader() -> None:
    rounds = [
        _roster([(4000, 90), (2000, 80), (7000, 70), (9000, 60)], subjects=1),
        _batch([(4, 1)], expected=(4000,)),
        _batch([(2, 4000, 1, 10), (4, 1)], expected=(2000,)),
        _batch([(2, 2000, 1, 5), (4, 1)], expected=(7000,)),
    ]
    res, _ = _run(rounds)
    assert res.output == (4000, 2000, 7000)


@pytest.mark.parametrize(
    ("grades", "want"),
    [
        ((1, 2, 3, 3), 2),  # 9/4  = 2.25 -> 2
        ((0, 0, 0, 1), 0),  # 1/4  = 0.25 -> 0
        ((100, 100, 100, 100), 100),  # exact
        ((99, 100, 100, 100), 99),  # 399/4 = 99.75 -> 99
    ],
)
def test_avg_rounds_down(grades: tuple[int, ...], want: int) -> None:
    ids = (4001, 4002, 4003, 4004)
    rounds = [
        _roster([(i, g) for i, g in zip(ids, grades, strict=True)], subjects=1),
        _batch([(3, 1)], expected=(want,)),
    ]
    res, _ = _run(rounds)
    assert res.output == (want,)


@pytest.mark.parametrize("n", range(4, 17))
def test_avg_divides_by_every_legal_roster_size(n: int) -> None:
    """The divide is a 13-way dispatch on N, so every arm needs exercising.

    Grades are ``1..n`` so the quotient has a non-zero remainder for every n > 1:
    ``sum = n(n+1)/2``, ``sum // n == (n + 1) // 2``.
    """
    students = [(1000 + i, i + 1) for i in range(n)]
    want = (n + 1) // 2
    rounds = [_roster(students, subjects=1), _batch([(3, 1)], expected=(want,))]
    res, _ = _run(rounds)
    assert res.output == (want,), f"N={n}"


@pytest.mark.parametrize("k", range(1, 5))
def test_every_subject_of_a_full_width_roster_is_addressable(k: int) -> None:
    """K < 4 must not read a neighbouring subject's slot out of the stride-4 row."""
    students = [(2000 + i, *[10 * (s + 1) + i for s in range(k)]) for i in range(4)]
    ops = [(1, 2000 + i, s + 1) for i in range(4) for s in range(k)]
    want = tuple(10 * (s + 1) + i for i in range(4) for s in range(k))
    res, _ = _run([_roster(students, subjects=k), _batch(ops, expected=want)])
    assert res.output == want, f"K={k}"


def test_set_emits_nothing_and_get_sees_it() -> None:
    rounds = [
        _roster([(1111, 1, 2), (2222, 3, 4), (3333, 5, 6), (4444, 7, 8)], subjects=2),
        _batch([(2, 3333, 2, 42)], expected=()),
        _batch([(1, 3333, 2), (1, 3333, 1)], expected=(42, 5)),
    ]
    res, _ = _run(rounds)
    assert res.output == (42, 5)


# ── cost ──────────────────────────────────────────────────────────────────────
def test_public_cases_fit_the_tick_cap_with_real_tape_latency() -> None:
    """Re-bill STORE at the tape's real cost, not the emulator's flat 6/word.

    ``ARCH.md`` §4.1 puts one tape access at ``105 + 8.3N`` ticks; with N=94 that is
    ~885, against the emulator's 12-18 for the same access. Every variable in this
    program is a tape slot, so this is the whole tick bill and the flat model
    understates it ~50x.
    """
    per_access = 105 + 8.3 * TAPE_N
    worst = 0.0
    for name, rounds in CASES:
        res, store = _run(rounds)
        ticks = res.ticks + store.ops * per_access
        worst = max(worst, ticks)
        assert ticks < TICK_CAP, f"{name}: ~{ticks:,.0f} ticks over the {TICK_CAP:,} cap"
    # The measured worst case on the real interpreter is ~2.2M; leave a real margin
    # so a future edit that doubles the access count fails here rather than silently
    # on the judge.
    assert worst < TICK_CAP / 1.5, f"only {TICK_CAP / worst:.2f}x of margin left"


def test_the_program_stays_on_the_depth_four_trie() -> None:
    """15 opcodes -> k=4 -> 16 lanes. A 17th would double the CPU's height."""
    prog = load(SLUG)
    p = machine.plan(prog)
    assert len(p.number) == 15
    assert p.k == 4 and p.lanes == 16
    for op in prog.ops_used:
        structured = op.sem in {Sem.JUMP, Sem.BR_ZERO, Sem.BR_NEG}
        assert machine.hw_micro(op.sem) or structured, op.mnemonic


# ── the generated machine ─────────────────────────────────────────────────────
@node_required
def test_checked_in_grid_matches_the_generator() -> None:
    """``build_for`` runs the engine's structural analysis: every pipe must bind."""
    m = machine.build_for(SLUG)
    assert (m.width, m.height) == (112, 103)
    assert m.footprint == 112**2  # width-bound: the CPU, adapter and 32-wide tape
    assert m.tape_n == TAPE_N
    expected = "\n".join(m.rows) + "\n"
    assert GRID.read_text(encoding="utf-8") == expected, (
        f"{GRID.name} is stale; regenerate with "
        f"`python -m randomfun2026solvers.lm1.machine {SLUG} --out {GRID}`"
    )


@node_required
@slow
def test_public_cases_pass_on_the_real_interpreter() -> None:
    from randomfun2026solvers import optimize

    res = optimize.verify(GRID, SLUG, tick_cap=TICK_CAP)
    failed = [c.name for c in res.cases if not c.passed]
    assert res.passed, f"{SLUG}: {failed}"
    assert len(res.cases) == 7
