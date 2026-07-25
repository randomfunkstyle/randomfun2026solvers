"""The op-level model of the pathfinder machine, checked against every case.

The model is pure Python (no engine, no grid), so this whole file belongs in
the fast tier: it is the thing that says the *algorithm* and the *register
schedule* are right, independently of where the glyphs end up on the grid.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from randomfun2026solvers import pathfinder_prog as pf

PROBLEM = Path(__file__).resolve().parents[1] / "tasks" / "problems" / "pathfinder.json"


def cases():
    return json.loads(PROBLEM.read_text())["publicTestData"]


def run(case):
    ins = [int(v) for rnd in case["rounds"] for v in rnd["in"]]
    m = pf.Machine(pf.build(), ins)
    return m, m.run()


@pytest.mark.parametrize("case", cases(), ids=lambda c: c["name"])
def test_frames_match(case):
    expected = [f for rnd in case["rounds"] for f in rnd["frames"]]
    _, got = run(case)
    assert got == expected


@pytest.mark.parametrize("case", cases(), ids=lambda c: c["name"])
def test_pipe_capacities(case):
    """Every pipe's high-water mark, which sizes the physical loops."""
    m, _ = run(case)
    # 18 ring words: [P, Q] plus four groups of [S1, NB, S2, S3].
    assert m.maxring == pf.RING_WORDS
    assert m.maxfifo <= pf.FIFO_WORDS
    assert m.maxscr <= pf.SCRATCH_WORDS


def test_every_block_is_reachable():
    P = pf.build()
    seen = {"INIT"}
    stack = ["INIT"]
    while stack:
        _, succ = P[stack.pop()]
        for nxt in ([succ] if isinstance(succ, str) else succ.values()):
            if nxt != "HALT" and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    assert seen == set(P), f"unreachable: {set(P) - seen}"


def test_branch_lanes_are_declared():
    """A branch token must be last in its block and name every lane it can pick."""
    lanes = {"X": {"zero", "pos", "neg"}, "x": {"one", "zero"},
             "d": {"pos", "zero"}}
    for name, (toks, succ) in pf.build().items():
        branches = [t for t in toks if t in lanes]
        if isinstance(succ, dict):
            assert len(branches) == 1, name
            assert toks[-1] == branches[0], name
            assert set(succ) <= lanes[branches[0]], name
            assert lanes[branches[0]] <= set(succ) | {"neg"}, name
        else:
            assert not branches, name
