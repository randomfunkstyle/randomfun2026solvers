"""Run a `.man` grid through the reference interpreter (lm.mjs / littleman.wasm).

This is the byte-exact engine the online editor uses; we validate all generated
grids against it rather than our own emulator.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

_DEFAULT_LM = "/Users/oleg/projects/randomfun2026solvers/littleman/lm.mjs"
LM_PATH = os.environ.get("LM_MJS", _DEFAULT_LM)


@dataclass
class OracleResult:
    output: list[int]
    step: int
    halted: bool
    reason: str | None
    fatal: dict | None

    @property
    def ok(self) -> bool:
        return self.fatal is None


def run_grid(
    program: str, inputs: list[int] | None = None, max_ticks: int = 1_000_000
) -> OracleResult:
    with tempfile.NamedTemporaryFile("w", suffix=".man", delete=False) as f:
        f.write(program)
        path = f.name
    try:
        args = ["node", LM_PATH, "run", path, "--json", "--max-ticks", str(max_ticks)]
        if inputs:
            args += ["--input", " ".join(str(x) for x in inputs)]
        p = subprocess.run(args, capture_output=True, text=True)
        try:
            j = json.loads(p.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"lm.mjs did not return JSON: {p.stdout}\n{p.stderr}") from e
        return OracleResult(
            output=j.get("output") or [],
            step=j.get("step", 0),
            halted=j.get("halted", False),
            reason=j.get("reason"),
            fatal=j.get("fatal"),
        )
    finally:
        os.unlink(path)
