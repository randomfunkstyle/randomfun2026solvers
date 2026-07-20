"""Backtracking 9x9 Sudoku solver for the demo_sudoku fake contest."""

from __future__ import annotations

import json
from typing import Any

_N = 9


def _solve(grid: list[int]) -> bool:
    """Fill `grid` in place using MRV backtracking."""
    best_idx = -1
    best_cands: list[int] | None = None
    for idx in range(_N * _N):
        if grid[idx] != 0:
            continue
        r, c = divmod(idx, _N)
        used = set()
        for k in range(_N):
            used.add(grid[r * _N + k])
            used.add(grid[k * _N + c])
        br, bc = (r // 3) * 3, (c // 3) * 3
        for dr in range(3):
            for dc in range(3):
                used.add(grid[(br + dr) * _N + (bc + dc)])
        cands = [d for d in range(1, 10) if d not in used]
        if not cands:
            return False
        if best_cands is None or len(cands) < len(best_cands):
            best_idx, best_cands = idx, cands
            if len(cands) == 1:
                break

    if best_idx == -1:
        return True

    for digit in best_cands or []:
        grid[best_idx] = digit
        if _solve(grid):
            return True
    grid[best_idx] = 0
    return False


def solve_sudoku_payload(payload: dict[str, Any]) -> bytes:
    givens = payload.get("givens")
    if not isinstance(givens, str):
        raise ValueError("sudoku payload must contain string givens")
    if len(givens) != _N * _N or any(ch not in "0123456789" for ch in givens):
        raise ValueError("sudoku givens must be 81 digits")

    grid = [int(ch) for ch in givens]
    if not _solve(grid):
        return json.dumps({"grid": givens}, sort_keys=True).encode()
    return json.dumps({"grid": "".join(str(digit) for digit in grid)}, sort_keys=True).encode()
