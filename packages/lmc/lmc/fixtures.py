"""Parse `in:` / `out:` public test cases out of the problem `.md` files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROBLEMS_DIR = Path(
    "/Users/oleg/projects/randomfun2026claude/task_docs/problem_sets"
)


@dataclass
class Case:
    inputs: list[int]
    outputs: list[int]


def _ints(s: str) -> list[int]:
    s = s.strip()
    if not s or s.lower().startswith("(none)"):
        return []
    return [int(t) for t in s.split()]


def parse_cases(md_text: str) -> list[Case]:
    """Flat list of (in, out) pairs -- one per consecutive in:/out: line pair.

    Good for single-shot problems (triangle, subset_sum). Round-structured
    problems concatenate rounds; callers can merge as needed.
    """
    cases: list[Case] = []
    pending_in: list[int] | None = None
    for line in md_text.splitlines():
        m = re.match(r"\s*in:\s*(.*)$", line)
        if m:
            pending_in = _ints(m.group(1))
            continue
        m = re.match(r"\s*out:\s*(.*)$", line)
        if m and pending_in is not None:
            cases.append(Case(pending_in, _ints(m.group(1))))
            pending_in = None
    return cases


def load_problem(semester: str, name: str) -> list[Case]:
    path = PROBLEMS_DIR / semester / f"{name}.md"
    return parse_cases(path.read_text())


def parse_test_cases(md_text: str) -> list[Case]:
    """One Case per named test case, rounds concatenated into a single stream.

    Round-structured problems (reverse_list, ...) feed 1-3 rounds to one running
    program -- the next list only arrives after the current one is fully printed.
    A named group heading (any non in:/out:/Round line after `test cases`) opens a
    new test case; its rounds' inputs and outputs are concatenated in order, which
    is exactly what the reference engine sees for that case.
    """
    lines = md_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip().lower() == "test cases")
    except StopIteration:
        return []
    cases: list[Case] = []
    cur: Case | None = None
    pending_in: list[int] | None = None
    for line in lines[start + 1 :]:
        s = line.strip()
        if not s:
            continue
        m = re.match(r"in:\s*(.*)$", s)
        if m:
            pending_in = _ints(m.group(1))
            continue
        m = re.match(r"out:\s*(.*)$", s)
        if m and pending_in is not None and cur is not None:
            cur.inputs.extend(pending_in)
            cur.outputs.extend(_ints(m.group(1)))
            pending_in = None
            continue
        if s.lower().startswith("round"):
            continue  # round label -- rounds fold into the current test case
        cur = Case([], [])  # a named test-case heading
        cases.append(cur)
    return [c for c in cases if c.inputs]


def load_test_cases(semester: str, name: str) -> list[Case]:
    path = PROBLEMS_DIR / semester / f"{name}.md"
    return parse_test_cases(path.read_text())
