"""The three hand-built grids that beat their generators, pinned against the judge.

These have no generator behind them -- they were written cell by cell -- so the
only thing standing between them and a silent regression is this file.  Each is
the best score the contest has recorded for its problem:

* ``tcp_hand``            17x17, 20/20,       535,084  (was 908,720,960)
* ``matmul_hand``         72x81, 20/20,   232,294,501  (was 1,429,920,945)
* ``reverse-a-list_carrier`` 14x14, 20/20,    34,535  (was 513,410)

Two of them are **round-gated**: the judge withholds each round's input until the
previous round's output is complete, and ``tcp_hand`` and
``reverse-a-list_carrier`` both depend on that -- handed the whole stream at once
they emit nothing at all.  So every case here goes through
:meth:`Littleman.judge` with ``expected=``, which is the only harness that
reproduces the gating.  ``scoring.score_program`` calls ``lm.tick`` without it
and will read these grids as total failures; that is a limitation of the tick
measurer, not of the grids.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from randomfun2026solvers.littleman import Littleman

ROOT = Path(__file__).resolve().parents[1]
SOLUTIONS = ROOT / "tasks" / "solutions"
PROBLEMS = ROOT / "tasks" / "problems"

# (solution stem, problem slug, expected w, expected h)
GRIDS = [
    ("tcp_hand", "tcp", 17, 17),
    ("matmul_hand", "matmul", 72, 81),
    ("reverse-a-list_carrier", "reverse-a-list", 14, 14),
]


def _rounds(case: dict) -> list[dict]:
    return case.get("rounds") or []


def _cases(slug: str) -> list[dict]:
    return json.loads((PROBLEMS / f"{slug}.json").read_text())["publicTestData"]


def _ids(slug: str) -> list[str]:
    return [c["name"] for c in _cases(slug)]


@pytest.mark.parametrize(("stem", "slug", "width", "height"), GRIDS, ids=[g[0] for g in GRIDS])
def test_footprint_is_what_was_submitted(stem: str, slug: str, width: int, height: int) -> None:
    """``max(w, h)**2`` is half the score, so the box is worth pinning by itself."""
    rows = (SOLUTIONS / f"{stem}.man").read_text().rstrip("\n").split("\n")
    assert max(len(r) for r in rows) == width
    assert len(rows) == height


@pytest.mark.parametrize(
    ("stem", "slug", "case"),
    [(s, g, c) for s, g, _, _ in GRIDS for c in _cases(g)],
    ids=[f"{s}-{c['name']}" for s, g, _, _ in GRIDS for c in _cases(g)],
)
def test_public_cases(stem: str, slug: str, case: dict) -> None:
    """Rounds joined with ``/`` so the engine gates them the way the judge does."""
    rounds = _rounds(case)
    inp = " / ".join(" ".join(map(str, r.get("in") or [])) for r in rounds)
    expected = " / ".join(" ".join(map(str, r.get("out") or [])) for r in rounds)
    want = [int(t) for r in rounds for t in (r.get("out") or [])]

    snap = Littleman().judge(
        (SOLUTIONS / f"{stem}.man").read_text(),
        input=inp,
        expected=expected,
        max_ticks=1_000_000,
    )
    assert snap.fatal is None, f"fatal: {snap.fatal}"
    assert [int(v) for v in snap.output] == want


def test_reverse_carrier_handles_every_length_and_the_round_boundary() -> None:
    """``n`` up to 16 and three rounds -- the private cases are longer than the public ones.

    The carrier ring holds one man per value on 32 cells; ``n = 16`` is the point
    where all sixteen are alive at once and the ring is exactly full, so the
    largest list is also the one that would break first.
    """
    lm = Littleman()
    src = (SOLUTIONS / "reverse-a-list_carrier.man").read_text()
    for lists in ([list(range(1, n + 1))] for n in range(1, 17)):
        inp = " / ".join(f"{len(l)} " + " ".join(map(str, l)) for l in lists)
        exp = " / ".join(" ".join(map(str, reversed(l))) for l in lists)
        snap = lm.judge(src, input=inp, expected=exp, max_ticks=100_000)
        assert snap.fatal is None, f"n={len(lists[0])}: {snap.fatal}"
        assert [int(v) for v in snap.output] == [v for l in lists for v in reversed(l)]

    three = [list(range(16)), list(range(100, 116)), list(range(200, 216))]
    inp = " / ".join(f"{len(l)} " + " ".join(map(str, l)) for l in three)
    exp = " / ".join(" ".join(map(str, reversed(l))) for l in three)
    snap = lm.judge(src, input=inp, expected=exp, max_ticks=100_000)
    assert snap.fatal is None
    assert [int(v) for v in snap.output] == [v for l in three for v in reversed(l)]


def test_the_generator_backed_solutions_are_left_alone() -> None:
    """These are additions, not replacements -- the generated grids still stand."""
    for stem in ("tcp_ring", "matmul_cpu", "reverse-a-list_ring"):
        rows = (SOLUTIONS / f"{stem}.man").read_text().rstrip("\n").split("\n")
        assert rows and all(rows)
