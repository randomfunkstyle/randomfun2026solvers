"""The one-register base-3 stack, as a three-man token CFG."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from randomfun2026solvers.brackets_stack import (
    CLASS,
    COUNT,
    MEN,
    WORK,
    reference,
    simulate,
)

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = ROOT / "tasks" / "problems" / "brackets.json"
LEGAL = "()[]{}"
MAX_DEPTH = 32


def public_cases() -> list[dict]:
    return json.loads(PROBLEM.read_text(encoding="utf-8"))["publicTestData"]


@pytest.mark.parametrize("case", public_cases(), ids=lambda case: case["name"])
def test_every_public_case(case: dict) -> None:
    text = "".join(chr(int(v)) for v in case["in"][1:])
    got, _ = simulate(text)
    assert got == int(case["out"][0])


def test_every_string_through_length_five() -> None:
    checked = 0
    for size in range(6):
        for chars in itertools.product(LEGAL, repeat=size):
            text = "".join(chars)
            got, _ = simulate(text)
            assert got == reference(text), text
            checked += 1
    assert checked == 9_331


def test_the_deepest_and_longest_legal_inputs() -> None:
    # 32 is the stated depth bound and 64 the length bound; the base-3 stack has
    # to survive both at once without leaving 64 bits.
    for text in (
        "(" * MAX_DEPTH + ")" * MAX_DEPTH,
        "{" * MAX_DEPTH + "}" * MAX_DEPTH,
        "{" * MAX_DEPTH + "}" * (MAX_DEPTH - 1) + ")",
        "[" * MAX_DEPTH,
        "()" * 32,
        "",
    ):
        got, _ = simulate(text)
        assert got == reference(text), text


def test_the_stack_word_stays_inside_a_signed_64_bit_register() -> None:
    # 32 threes in base 3 with digits 1..3 is the widest reachable word.
    widest = 0
    for _ in range(MAX_DEPTH):
        widest = 3 * widest + 3
    assert widest == (3 ** (MAX_DEPTH + 1) - 3) // 2 == 2_779_530_283_277_760
    assert widest < 2**63
    # ... and it is the *reachable* bound, not a slack one: one more `{` leaves
    # room to spare, which is the whole reason the stack fits in a register.
    assert 3 * widest + 3 < 2**63


def test_the_backpack_tree_separates_all_six_codes() -> None:
    # CLASS takes `c >> 5` as the type and reads bit0/bit1 for the sign, so the
    # whole classifier is four glyphs a character.
    for ch in LEGAL:
        c = ord(ch)
        assert ((c & 1, (c >> 1) & 1) == (1, 0)) is (ch in ")]}"), ch
    assert {ord(ch) >> 5 for ch in "()"} == {1}
    assert {ord(ch) >> 5 for ch in "[]"} == {2}
    assert {ord(ch) >> 5 for ch in "{}"} == {3}
    # 2 would be a free leaf of the same tree, but the worker branches on the
    # token's sign, so end-of-string is simply 0.
    assert (0 & 1, (0 >> 1) & 1) not in {(ord(c) & 1, (ord(c) >> 1) & 1) for c in ")]}"}


def test_each_man_stays_small() -> None:
    assert set(MEN) == {"CLASS", "WORK", "COUNT"}
    assert (len(CLASS), len(WORK), len(COUNT)) == (5, 10, 8)
