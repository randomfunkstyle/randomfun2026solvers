"""Dispatch solver names to envelope-based solver functions."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from randomfun2026solvers.sudoku import solve_sudoku_payload


class SolverError(RuntimeError):
    """A user-facing solver failure."""


SolverFn = Callable[[dict[str, Any]], tuple[bytes, dict[str, Any]]]


def decode_task_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    """Decode ``task.payload_b64`` from the input envelope into a JSON object."""
    try:
        payload_b64 = envelope["task"]["payload_b64"]
        raw = base64.b64decode(payload_b64, validate=True)
        data = json.loads(raw.decode())
    except Exception as exc:  # noqa: BLE001 - convert envelope errors into one CLI error
        raise SolverError(f"invalid input envelope task payload: {exc}") from exc
    if not isinstance(data, dict):
        raise SolverError("decoded task payload must be a JSON object")
    return data


def _sudoku(envelope: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    task = envelope.get("task")
    if not isinstance(task, dict):
        raise SolverError("input envelope must contain task object")
    if task.get("contest_key") != "demo_sudoku":
        raise SolverError("sudoku solver only supports contest_key=demo_sudoku")
    payload = decode_task_payload(envelope)
    return solve_sudoku_payload(payload), {
        "solver": "sudoku",
        "contest_key": "demo_sudoku",
    }


_SOLVERS: dict[str, SolverFn] = {
    "sudoku": _sudoku,
}


def run_solver(name: str, envelope: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    solver = _SOLVERS.get(name)
    if solver is None:
        known = ", ".join(sorted(_SOLVERS))
        raise SolverError(f"unknown solver {name!r}; known solvers: {known}")
    return solver(envelope)
