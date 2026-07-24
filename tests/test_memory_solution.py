"""The shipped ``memory`` solution must pass all four local suites — *strictly*.

Strictly means two things:

1. the program emits exactly the expected values, in order, and
2. it then **stays silent**: we keep stepping for ``EXTRA_TICKS`` ticks after the
   expected output is complete and fail if any further value appears.

(2) is not paranoia. ``littleman/tools/run-cases.mjs`` stops the instant the
output *length* matches, so a loop that moves one value too many passes it
silently — the right prefix is there and the extra value lands after the check
(see ``littleman/programs/blocks/lap-ring.md``, trap 6). The Node-side twin of
this module is ``littleman/tools/run-cases-strict.mjs``.

Everything here drives the reference engine (``littleman.wasm`` via
``lm.mjs``) through :class:`randomfun2026solvers.littleman.Littleman`: one
``tick(n)`` steps *exactly* n ticks from a fresh load, and output length is
monotonic in n, which is all the strict check needs.

Run the fast suites (public / edge / fresh)::

    PYTHONPATH=solvers/python uv run python -m pytest tests/test_memory_solution.py -q

Add the judge-weight heavy suite (marked ``slow``)::

    PYTHONPATH=solvers/python uv run python -m pytest tests/test_memory_solution.py -q -m slow
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
LM_MJS = REPO / "littleman" / "lm.mjs"
PROGRAMS = REPO / "littleman" / "programs"
# The shipped solution, unless $MEMORY_MAN points somewhere else — set it to run
# this whole strict suite against a candidate build before shipping it.
PROGRAM = Path(os.environ.get("MEMORY_MAN") or REPO / "tasks" / "solutions" / "memory_tape.man")

if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.littleman import Littleman  # noqa: E402
from randomfun2026solvers.scoring import footprint  # noqa: E402

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists() or not PROGRAM.exists(),
    reason="node, littleman/lm.mjs and tasks/solutions/memory_tape.man required",
)

# Silence window after the output is complete: ~30 memory ops at ~700 ticks/op —
# enough to cover a WRITE-only tail plus a spurious emit from the next loop.
EXTRA_TICKS = 20_000
# The grading step cap; a program needing more than this does not pass at all.
TICK_CAP = 4_000_000
# Tick budget guess: ticks/token for the shipped build is ~265 (max observed
# 302); 420 leaves ~40% headroom so the happy path is two engine calls per case.
# Too small only costs time (the budget doubles until the output is complete).
TICKS_PER_TOKEN = 420
BUDGET_FLOOR = 4_000


class CaseFailure(AssertionError):
    """A case did not pass: wrong values, too few values, or extra output."""


@dataclass(frozen=True)
class Case:
    suite: str
    index: int
    input: str
    want: tuple[int, ...]

    def __str__(self) -> str:  # pytest test id
        return f"{self.suite}[{self.index}]:{len(self.input.split())}tok"


def load_suite(name: str) -> list[Case]:
    path = PROGRAMS / f"{name}.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    return [
        Case(
            suite=name,
            index=i,
            input=c["in"],
            want=tuple(int(v) for v in c["out"].split()),
        )
        for i, c in enumerate(cases)
    ]


def budget(case: Case) -> int:
    return max(BUDGET_FLOOR, TICKS_PER_TOKEN * len(case.input.split()))


def _output_at(lm: Littleman, program: Path, n: int, case: Case) -> list[int]:
    snap = lm.tick(program, n, input=case.input)
    return list(snap.output)


def check_case(
    lm: Littleman,
    program: Path,
    case: Case,
    *,
    extra_ticks: int = EXTRA_TICKS,
    tick_cap: int = TICK_CAP,
) -> int:
    """Assert ``program`` emits exactly ``case.want`` and then nothing more.

    Returns the tick budget at which the output was seen complete (an upper
    bound on the scoring tick, not an exact measurement).
    """
    n = budget(case)
    while True:
        out = _output_at(lm, program, n, case)
        if len(out) > len(case.want):
            raise CaseFailure(
                f"{case}: extra output within {n} ticks — emitted {len(out)} values, "
                f"want {len(case.want)} (first extra {out[len(case.want)]})"
            )
        if len(out) == len(case.want):
            break
        if out != list(case.want[: len(out)]):
            raise CaseFailure(f"{case}: wrong output {out} is not a prefix of {list(case.want)}")
        if n >= tick_cap:
            raise CaseFailure(
                f"{case}: only {len(out)}/{len(case.want)} values after {n} ticks "
                f"(tick cap) — the program does not pass this case"
            )
        n = min(n * 2, tick_cap)

    # The output is complete by tick n; demand silence for extra_ticks more.
    after = _output_at(lm, program, n + extra_ticks, case)
    if after != list(case.want):
        extra = after[len(case.want) :]
        raise CaseFailure(
            f"{case}: output not exactly as expected {n}..{n + extra_ticks} ticks in — "
            f"got {after}, want {list(case.want)}"
            + (f"; extra values emitted: {extra}" if extra else "")
        )
    return n


@pytest.fixture(scope="module")
def lm() -> Littleman:
    return Littleman()


# ── the shipped solution, suite by suite ──────────────────────────────────────
@node_required
@pytest.mark.parametrize("case", load_suite("memory-cases"), ids=str)
def test_public_cases(lm: Littleman, case: Case) -> None:
    check_case(lm, PROGRAM, case)


@node_required
@pytest.mark.parametrize("case", load_suite("memory-edge-cases"), ids=str)
def test_edge_cases(lm: Littleman, case: Case) -> None:
    """Boundary addresses 0/99, repeated writes, a full 100-cell read, mid-WRITE end."""
    check_case(lm, PROGRAM, case)


@node_required
@pytest.mark.parametrize("case", load_suite("memory-fresh-cases"), ids=str)
def test_fresh_cases(lm: Littleman, case: Case) -> None:
    """A different seed than the tuning suites: read-heavy and write-heavy mixes."""
    check_case(lm, PROGRAM, case)


@pytest.mark.slow
@node_required
@pytest.mark.parametrize("case", load_suite("memory-heavy-cases"), ids=str)
def test_heavy_cases(lm: Littleman, case: Case) -> None:
    """Judge-weight streams (300–1000 tokens). Slow: ~4s for the five of them."""
    check_case(lm, PROGRAM, case)


# ── footprint guard (the other half of the score) ─────────────────────────────
def test_footprint_does_not_regress() -> None:
    """area² is half the score. 1024 is shipped (32×32); the plan's ceiling is 1100."""
    w, h, area2 = footprint(PROGRAM)
    assert area2 <= 1100, (
        f"shipped memory_tape.man is {w}x{h} → area2 {area2}, above the 1100 ceiling "
        f"in littleman/programs/PLAN-memory-24M.md (32x32 / 1024 scored 61.9M)"
    )


# ── negative controls: the strict check must actually bite ────────────────────
# Reads one value, emits it, spins ~5400 ticks in a counted loop, then emits it
# a second time. The right prefix is emitted immediately, so any checker that
# stops when the output *length* matches passes this program. (On-disk twins, for
# the Node runners: littleman/tools/negative-controls/extra-output{,-clean}.man.)
EXTRA_OUTPUT_PROGRAM = """+-+  +---------------+  +-+
|I|>>|@rsM`900`b>dWsH|>>|O|
+-+  |          m.   |  +-+
     |          ^<   |
     +---------------+
"""
# Same program with the second send replaced by a nop — the correct twin.
CLEAN_PROGRAM = EXTRA_OUTPUT_PROGRAM.replace("b>dWsH", "b>dW.H")


@node_required
def test_check_case_accepts_the_clean_twin(lm: Littleman, tmp_path: Path) -> None:
    path = tmp_path / "clean.man"
    path.write_text(CLEAN_PROGRAM, encoding="utf-8")
    case = Case(suite="control", index=0, input="5", want=(5,))
    check_case(lm, path, case, extra_ticks=EXTRA_TICKS)


@node_required
def test_check_case_catches_one_value_too_many(lm: Littleman, tmp_path: Path) -> None:
    path = tmp_path / "extra.man"
    path.write_text(EXTRA_OUTPUT_PROGRAM, encoding="utf-8")
    case = Case(suite="control", index=0, input="5", want=(5,))

    # A checker that stops at "output length matches" sees nothing wrong…
    assert _output_at(lm, path, 100, case) == [5]
    # …but the value comes back out ~5400 ticks later, and check_case must fail.
    with pytest.raises(CaseFailure, match="extra values emitted"):
        check_case(lm, path, case, extra_ticks=EXTRA_TICKS)
