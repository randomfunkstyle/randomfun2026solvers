from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path


def _envelope(payload: dict, *, solver: str = "shell-smoke") -> dict:
    return {
        "solver": solver,
        "run_id": 1,
        "solver_commit": {"ref": "main", "hash": "local"},
        "task": {
            "contest_key": "demo_sudoku",
            "external_id": "sudoku-000",
            "payload_b64": base64.b64encode(json.dumps(payload).encode()).decode(),
            "content_type": "application/json",
        },
    }


def _run_solve(tmp_path: Path, solver: str, payload: dict) -> subprocess.CompletedProcess[str]:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "nested" / "output.json"
    input_path.write_text(json.dumps(_envelope(payload, solver=solver)), encoding="utf-8")
    return subprocess.run(
        ["./solve", "--solver", solver, "--input", str(input_path), "--output", str(output_path)],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )


def test_shell_smoke_writes_output_envelope(tmp_path: Path) -> None:
    proc = _run_solve(tmp_path, "shell-smoke", {"unused": True})
    assert proc.returncode == 0, proc.stderr
    out = json.loads((tmp_path / "nested" / "output.json").read_text(encoding="utf-8"))
    assert json.loads(base64.b64decode(out["solution_b64"]).decode()) == {"smoke": True}
    assert out["meta"]["solver"] == "shell-smoke"


def test_sudoku_solves_puzzle(tmp_path: Path) -> None:
    givens = (
        "530070000"
        "600195000"
        "098000060"
        "800060003"
        "400803001"
        "700020006"
        "060000280"
        "000419005"
        "000080079"
    )
    proc = _run_solve(tmp_path, "sudoku", {"size": 9, "givens": givens})
    assert proc.returncode == 0, proc.stderr
    out = json.loads((tmp_path / "nested" / "output.json").read_text(encoding="utf-8"))
    assert json.loads(base64.b64decode(out["solution_b64"]).decode()) == {
        "grid": (
            "534678912"
            "672195348"
            "198342567"
            "859761423"
            "426853791"
            "713924856"
            "961537284"
            "287419635"
            "345286179"
        )
    }
    assert out["meta"]["solver"] == "sudoku"


def test_bad_solver_name_fails_without_output(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(
        json.dumps(_envelope({"unused": True}, solver="missing")),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            "./solve",
            "--solver",
            "missing",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 2
    assert "unknown solver" in proc.stderr
    assert not output_path.exists()
