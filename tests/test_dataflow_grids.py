"""The dataflow grids, pinned against their problems and against their scores.

``littleman/DATAFLOW-SURVEY.md``'s headline finding is that three of the four
slugs in ``test_lm1_programs.py``'s ``BLOCKED`` set are already solved by
generator-emitted dataflow grids, and that those grids beat every CPU-generated
machine in the repo by two to four orders of magnitude. Both halves of that claim
were unpinned:

* ``value_ring`` is well covered by ``test_value_ring.py`` — but nothing computed
  a *score* for either ring, so the survey's numbers could drift silently.
* ``memory`` had **no test at all** against the ``memory`` problem. The grid is
  checked in as ``littleman/programs/memory.man`` and the generator that emits it
  is ``memory_tape.build_v2(100)``, and neither fact was asserted anywhere.

So this module asserts the survey's arithmetic rather than trusting it: the
generator reproduces each committed grid, each grid answers every public case on
the reference interpreter, and ``scoring.score_program`` returns the number the
survey quotes.

Why the ``BLOCKED`` set is *not* edited to match: it feeds
``test_the_expected_programs_exist``, which asserts an ``.asm`` exists for every
non-blocked slug. ``BLOCKED`` therefore means "no LM-1 assembly program solves
this", which is a true statement about ``lm1/machine.py`` — it just is not the
statement its name suggests. The docstring there now says so; the coverage lives
here.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import scoring  # noqa: E402
from randomfun2026solvers.littleman import Littleman  # noqa: E402
from randomfun2026solvers.memory_tape import build_v2  # noqa: E402
from randomfun2026solvers.value_ring import build_reverse, build_sort  # noqa: E402

LM_MJS = REPO / "littleman" / "lm.mjs"

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)
slow = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the reference-interpreter sweeps",
)


def _memory_grid() -> Path:
    return REPO / "littleman" / "programs" / "memory.man"


#: ``slug -> (generator, committed grid, width, height)``. One row per dataflow
#: machine that solves a graded problem outright.
GRIDS: dict[str, tuple[object, Path, int, int]] = {
    "memory": (lambda: build_v2(100), _memory_grid(), 32, 32),
    "reverse-a-list": (
        build_reverse,
        REPO / "tasks" / "solutions" / "reverse-a-list_ring.man",
        21,
        21,
    ),
    "sort-numbers": (
        build_sort,
        REPO / "tasks" / "solutions" / "sort-numbers_ring.man",
        25,
        25,
    ),
}

#: The contest score each grid earns on the public set, ``max(w,h)**2 * avgTicks``.
#: Pinned exactly: these are what ``DATAFLOW-SURVEY.md`` §2 quotes, and a change
#: here is either an improvement worth recording or a regression worth failing on.
#: ``memory`` is the 32x32 build in ``programs/memory.man``; ARCH.md's header table
#: quotes 14,289,920 for the 31x32 variant in ``littleman/examples``, which is a
#: different grid.
SCORES: dict[str, float] = {
    "memory": 1024 * 19201.571428571428,
    "reverse-a-list": 441 * 1094.25,
    "sort-numbers": 625 * 3333.2857142857142,
}

#: The best score any CPU-generated (``lm1/machine.py``) machine achieves, for the
#: survey's central comparison. ``brackets`` at 98x75.
BEST_GENERATED_SCORE = 308_880_647


def public_cases(slug: str) -> list[dict]:
    problem = REPO / "tasks" / "problems" / f"{slug}.json"
    return json.loads(problem.read_text())["publicTestData"]


def rounds_of(case: dict) -> list[dict]:
    if "rounds" in case:
        return case["rounds"]
    return [{"in": case.get("in", []), "out": case.get("out", [])}]


# ── the generators still emit the committed grids ─────────────────────────────
@pytest.mark.parametrize("slug", sorted(GRIDS))
def test_the_generator_reproduces_the_committed_grid(slug: str) -> None:
    """Every dataflow grid in the repo is *generated*, including ``memory.man``.

    ``memory.man`` predates the generator and looks hand-drawn, which is exactly
    why this needs asserting: ``memory_tape.build_v2(100)`` emits it byte for byte,
    so the 906-byte grid is a build artefact and not a manuscript.
    """
    build, path, _, _ = GRIDS[slug]
    assert build() == path.read_text().rstrip("\n").split("\n")


@pytest.mark.parametrize("slug", sorted(GRIDS))
def test_the_footprint_is_what_the_survey_claims(slug: str) -> None:
    _, path, w, h = GRIDS[slug]
    width, height, area2 = scoring.footprint(path)
    assert (width, height) == (w, h)
    assert area2 == max(w, h) ** 2


def test_every_dataflow_grid_beats_every_generated_machine() -> None:
    """The survey's headline, as an assertion rather than a paragraph.

    The worst of the three dataflow scores is ``sort-numbers`` at ~2.08e6; the best
    CPU-generated machine is ``brackets`` at ~3.09e8. Two orders of magnitude is
    the *floor* of the gap, not the ceiling — ``sudoku-validity`` is 12.7e9.
    """
    assert max(SCORES.values()) * 15 < BEST_GENERATED_SCORE


# ── correctness on the real engine ────────────────────────────────────────────
@node_required
@pytest.mark.parametrize(
    ("slug", "case"),
    [(s, c) for s in sorted(GRIDS) for c in public_cases(s)],
    ids=[f"{s}: {c['name']}" for s in sorted(GRIDS) for c in public_cases(s)],
)
def test_public_case_on_the_reference_interpreter(slug: str, case: dict) -> None:
    """Every public case of every dataflow-solved problem, engine-side gated.

    ``judge`` rather than ``run``: none of these machines halt — the final read
    blocks forever on an exhausted input pipe — and per ``SPEC.md`` that is fine,
    you pass the moment the correct output is emitted. ``run`` would report
    "exceeded max ticks" on a program that has already passed.
    """
    _, path, _, _ = GRIDS[slug]
    rounds = rounds_of(case)
    want = [int(t) for r in rounds for t in r["out"]]
    snap = Littleman().judge(
        path.read_text(),
        input=" / ".join(" ".join(r["in"]) for r in rounds),
        expected=" / ".join(" ".join(r["out"]) for r in rounds),
        max_ticks=400_000,
    )
    assert snap.fatal is None, f"fatal: {snap.fatal}"
    assert list(snap.output) == want


@node_required
def test_memory_handles_the_boundary_addresses_and_the_value_extremes() -> None:
    """Address 0 and 99 and value +/-1000000 are the constraint corners.

    ``memory``'s tape is 100 cells addressed ``0..99``, and it is the one dataflow
    grid whose addressing is genuinely random — so the ends of the address range
    are where an off-by-one in the two pass-through counts would show. Interleaving
    a fresh (never-written) cell into the same stream also pins that an unwritten
    cell reads as 0, which is the problem's initial-state rule.
    """
    prog = _memory_grid().read_text()
    ops = "1 0 -1000000 1 99 1000000 0 0 0 99 0 50 1 50 7 0 50 0 0"
    snap = Littleman().judge(
        prog, input=ops, expected="-1000000 1000000 0 7 -1000000", max_ticks=400_000
    )
    assert snap.fatal is None, f"fatal: {snap.fatal}"
    assert list(snap.output) == [-1_000_000, 1_000_000, 0, 7, -1_000_000]


# ── the scores the survey quotes ──────────────────────────────────────────────
@node_required
@slow
@pytest.mark.parametrize("slug", sorted(GRIDS))
def test_the_contest_score_does_not_regress(slug: str) -> None:
    """``max(w,h)**2 * avgTicks`` on the public set, to the tick.

    Pinned exactly rather than as a bound: ticks on this engine are deterministic,
    so an inequality would hide an improvement as readily as a regression. Raise
    the number when a build genuinely gets faster.
    """
    _, path, _, _ = GRIDS[slug]
    res = scoring.score_program(path, slug)
    assert res.score is not None
    assert res.score == pytest.approx(SCORES[slug], rel=1e-9)
