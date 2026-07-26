"""Adversarial and exact-bound tests for the general brackets semantics.

The published suite is too small to distinguish a parser from a lookup table.
In particular it has no two inputs of the same length with different answers.
These cases pin all result shapes, all six legal ASCII values, and the real
constraint corners: n=64, depth=32, first error=64, and n+1=65.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman

ROOT = Path(__file__).resolve().parents[1]
GENERAL_SOLUTION = ROOT / "tasks" / "solutions" / "brackets_cpu.man"

OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
LEGAL = frozenset("()[]{}")


def expected_answer(text: str) -> int:
    stack: list[str] = []
    for position, char in enumerate(text, start=1):
        if char in OPEN_TO_CLOSE:
            stack.append(char)
        elif not stack or OPEN_TO_CLOSE[stack[-1]] != char:
            return position
        else:
            stack.pop()
    return len(text) + 1 if stack else 0


def peak_depth_before_first_error(text: str) -> int:
    stack: list[str] = []
    peak = 0
    for char in text:
        if char in OPEN_TO_CLOSE:
            stack.append(char)
            peak = max(peak, len(stack))
        elif not stack or OPEN_TO_CLOSE[stack[-1]] != char:
            break
        else:
            stack.pop()
    return peak


def encoded(text: str) -> list[int]:
    return [len(text), *(ord(char) for char in text)]


@dataclass(frozen=True)
class Case:
    name: str
    text: str
    answer: int


MIXED_MAX_DEPTH = "([{" + "(" * 29
MIXED_MAX_DEPTH += ")" * 29 + "}])"

CASES = [
    Case("empty", "", 0),
    # Same n, every legal answer shape from 0 through n+1. This is the direct
    # regression for the failed length-only submission.
    Case("n4-balanced", "()[]", 0),
    Case("n4-first-char", ")()(", 1),
    Case("n4-second-char", "(]()", 2),
    Case("n4-third-char", "([)]", 3),
    Case("n4-fourth-char", "()[}", 4),
    Case("n4-unclosed", "([](", 5),
    # All three types, both underflow and wrong-type behavior.
    Case("paren-underflow", ")", 1),
    Case("square-underflow", "]", 1),
    Case("curly-underflow", "}", 1),
    Case("wrong-paren", "[)", 2),
    Case("wrong-square", "{]", 2),
    Case("wrong-curly", "(}", 2),
    # Exact legal bounds.
    Case("n64-depth32-balanced", MIXED_MAX_DEPTH, 0),
    Case("n64-concatenated", "()" * 32, 0),
    Case("n64-depth32-unclosed", "()" * 16 + "{" * 32, 65),
    Case("wrong-type-at-position64", "(" * 31 + ")" * 31 + "[)", 64),
    Case("underflow-at-latest-possible-position", "()" * 31 + ")", 63),
    Case("first-error-with-64-input-values", ")" + "()" * 31 + "(", 1),
]


def test_the_corpus_itself_obeys_the_problem_bounds() -> None:
    assert set().union(*(set(case.text) for case in CASES)) == LEGAL
    for case in CASES:
        assert len(case.text) <= 64, case.name
        assert peak_depth_before_first_error(case.text) <= 32, case.name
        assert expected_answer(case.text) == case.answer, case.name
    assert len(MIXED_MAX_DEPTH) == 64
    assert peak_depth_before_first_error(MIXED_MAX_DEPTH) == 32
    assert max(case.answer for case in CASES) == 65


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_general_solution_handles_the_adversarial_corpus(case: Case) -> None:
    result = FastLittleman(GENERAL_SOLUTION).run(
        encoded(case.text),
        expected=[case.answer],
        max_ticks=3_000_000,
    )
    assert result.passed, (case.name, result.fatal, result.output)
    assert result.output == [case.answer]
