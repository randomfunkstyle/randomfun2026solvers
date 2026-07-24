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
