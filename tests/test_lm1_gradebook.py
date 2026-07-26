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

#: Slots the generated machine's tape must hold: 15 scalars plus **one packed cell
#: per student** (16..31) — the whole grade book, ids included, is the 16 cells.
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
    """The roster occupies cells 16..16+N-1 and nothing above; the rest stay 0.

    Both halves matter. A cursor bug that walked one slot low would corrupt a
    scalar and still pass some cases, and the unrolled scans *rely* on every cell
    above N-1 holding 0 — that is what makes a slot for a student who does not
    exist lose every comparison and add nothing to a sum.
    """
    for name, rounds in CASES:
        _, store = _run(rounds)
        n = rounds[0].input[0]
        used = {16 + i for i in range(n)}
        cells = {a: v for a, v in store.snapshot().items() if a >= 16}
        assert set(cells) <= used, f"{name}: wrote past the roster: {sorted(cells)}"
        assert set(cells) == used, f"{name}: not every cell was written"
        empty = {16 + i for i in range(n, 16)}
        assert all(store.snapshot().get(a, 0) == 0 for a in empty), f"{name}: dirty tail"


def test_a_cell_really_is_the_whole_record() -> None:
    """Unpack the tape by hand: one slot holds K grades *and* the id.

    ``cell = packed * 2^14 + (16384 - id)`` with ``packed`` built by Horner in
    subject order, so subject s sits at bit ``14 + 11*(K - s)``. If this drifts,
    every mask in the program is wrong — and the program would still pass some
    cases, since K=1 makes most of the layout degenerate.
    """
    students = [(4321, 11, 22, 33, 44), (8765, 100, 0, 7, 99), (1000, 1, 2, 3, 4)]
    rounds = [_roster([*students, (9999, 5, 6, 7, 8)], subjects=4), _batch([(3, 1)], (29,))]
    _, store = _run(rounds)
    cells = {a: v for a, v in store.snapshot().items() if a >= 16}
    for sid, *grades in students:
        packed = 0
        for g in grades:
            packed = packed * 2048 + g
        want = packed * 16384 + (16384 - sid)
        assert want in cells.values(), f"id {sid}: no cell equals {want}"


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

    ``ARCH.md`` §4.1 puts one tape access at ``105 + 8.3N`` ticks, against the
    emulator's 12-18 for the same access. This is a floor, not the real bill — the
    measured cost of an access on this machine is ~340 ticks and the *ROM* is the
    other half of the total (see ``gradebook.asm``) — but it is the part the
    emulator can see, and it catches an edit that doubles the access count.
    """
    per_access = 105 + 8.3 * TAPE_N
    worst = 0.0
    for name, rounds in CASES:
        res, store = _run(rounds)
        ticks = res.ticks + store.ops * per_access
        worst = max(worst, ticks)
        assert ticks < TICK_CAP, f"{name}: ~{ticks:,.0f} ticks over the {TICK_CAP:,} cap"
    assert worst < TICK_CAP / 4, f"only {TICK_CAP / worst:.2f}x of margin left"


#: 10 batch rounds x 8 operations is the heaviest batch the constraints allow, and
#: the *public* data's worst case is 5 rounds — which is how this program shipped
#: passing 7/7 in public and failing a private test on the step cap. Measured on the
#: real engine, the three observable worst cases are ~3.35M / 2.12M / 1.83M ticks
#: against the 5,000,000 cap (the old two-array layout was 17.5M / 13.8M / 10.0M).
WORST_LEGAL_TICKS = {"TOP": 3_500_000, "AVG": 2_300_000, "GET": 2_000_000, "SET": 2_200_000}


def _worst_legal_case(op: str) -> tuple[str, str, list[int]]:
    """N=16, K=4, ten rounds of eight ``op``s: input, expected, flat expected."""
    n, k = 16, 4
    ids = [1000 + (i * 563) % 9000 for i in range(n)]
    grades = [[(i * 7 + s * 13) % 101 for s in range(k)] for i in range(n)]
    roster: list[int] = [n, k]
    for i in range(n):
        roster += [ids[i], *grades[i]]

    rounds_in, rounds_out = [], []
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
            elif op == "GET" or j == 7:  # a round of pure SETs emits nothing to gate on
                rin += [1, ids[n - 1], s]
                rout.append(grades[n - 1][s - 1])
            else:
                rin += [2, ids[n - 1], s, 50]
                grades[n - 1][s - 1] = 50
        rounds_in.append(rin)
        rounds_out.append(rout)

    joined = " / ".join([" ".join(map(str, roster))] + [" ".join(map(str, r)) for r in rounds_in])
    expected = " / ".join([""] + [" ".join(map(str, r)) for r in rounds_out])
    return joined, expected, [v for r in rounds_out for v in r]


@node_required
@pytest.mark.parametrize("op", sorted(WORST_LEGAL_TICKS))
def test_the_worst_legal_batch_fits_the_step_cap(op: str) -> None:
    """The gap that shipped this broken: public data never reaches 80 operations."""
    from randomfun2026solvers.littleman import Littleman

    inp, expected, want = _worst_legal_case(op)
    snap = Littleman().judge(GRID, input=inp, expected=expected, max_ticks=TICK_CAP)
    assert snap.fatal is None, f"80x {op}: fatal {snap.fatal}"
    assert list(snap.output) == want, f"80x {op}: wrong answers"
    assert snap.step < WORST_LEGAL_TICKS[op], f"80x {op}: {snap.step:,} ticks"
    assert snap.step < TICK_CAP, f"80x {op}: {snap.step:,} over the {TICK_CAP:,} cap"


def test_the_program_stays_on_the_depth_four_trie() -> None:
    """16 opcodes -> k=4 -> 16 lanes, exactly the budget. A 17th doubles the CPU."""
    prog = load(SLUG)
    p = machine.plan(prog)
    assert len(p.number) == 16
    assert p.k == 4 and p.lanes == 16
    for op in prog.ops_used:
        structured = op.sem in {Sem.JUMP, Sem.BR_ZERO, Sem.BR_NEG}
        assert machine.hw_micro(op.sem) or structured, op.mnemonic


# ── the generated machine ─────────────────────────────────────────────────────
@node_required
def test_checked_in_grid_matches_the_generator() -> None:
    """``build_for`` runs the engine's structural analysis: every pipe must bind."""
    m = machine.build_for(SLUG)
    assert (m.width, m.height) == (108, 101)
    # Width-bound: the unrolled scans make the ROM image 836 words, but packed tokens
    # halve its cells, so 31 rows is the first fold that gets it under the machine's
    # columns (see ROM_ROWS) and every fold below that is flat. Trading 20% more area
    # for 5x fewer ticks is what fits the step cap at all.
    #
    # Two things took this off 114. `LANE_ORDER` picks `mem_pad`, and weighting each
    # lane by how often its opcode runs needs one column less of it than
    # length-descending did (114 → 113); then `ADAPTER_TAPE_GAP` 6 → 1 took five more
    # off the adapter-to-STORE corridor (113 → 108). Both are west of the ROM, so the
    # fold is still the one `ROM_ROWS[gradebook]` documents and still clears the width.
    assert m.footprint == 108**2
    assert m.rom_rows == machine.ROM_ROWS[SLUG]
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
