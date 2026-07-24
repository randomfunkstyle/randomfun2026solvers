"""M1 end-to-end: Python-subset source -> grid -> reference interpreter output.

Skipped automatically when the reference runner (node + lm.mjs) is unavailable.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from lmc.compile import compile_source
from lmc.fixtures import load_problem
from lmc.oracle import LM_PATH, run_grid

_HAVE_ORACLE = shutil.which("node") is not None and Path(LM_PATH).exists()
requires_oracle = pytest.mark.skipif(
    not _HAVE_ORACLE, reason="reference runner (node + lm.mjs) not available"
)


@requires_oracle
def test_triangle():
    grid = compile_source("n = recv()\nemit(n*(n+1)//2)\n")
    for case in load_problem("semester1", "triangle"):
        r = run_grid(grid, case.inputs)
        assert r.output == case.outputs, (case.inputs, r.output, case.outputs)


@requires_oracle
def test_echo():
    grid = compile_source("emit(recv())\n")
    for n in (42, 7, -5, 1000000, -1000000):
        r = run_grid(grid, [n])
        assert r.output == [n]


@requires_oracle
@pytest.mark.parametrize(
    "src,inp,exp",
    [
        ("emit(recv()*recv())\n", [6, 7], [42]),
        ("n = recv()\nemit(n*n)\n", [9], [81]),
        ("n = recv()\nemit(2*n+1)\n", [10], [21]),
        ("n = recv()\nemit(n%7)\n", [100], [2]),
        ("n = recv()\nemit(-n)\n", [5], [-5]),
    ],
)
def test_straight_line(src, inp, exp):
    grid = compile_source(src)
    r = run_grid(grid, inp)
    assert r.output == exp, (src, r.output, exp)
