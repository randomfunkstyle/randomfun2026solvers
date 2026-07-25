"""`snake` as a dataflow ring machine: the panel harness and the worker program.

Three things are pinned here:

* the generator reproduces the checked-in panel-probe grid byte for byte, and its
  footprint, so a change that grows the box fails loudly;
* the worker's program (:data:`WORKER`) reproduces **every** frame of **every**
  public case at the op level, which is where a logic bug shows up cheaply;
* the panel harness itself commits those same frames on the **reference engine**,
  driven with the per-frame deltas the worker will send.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import deque
from pathlib import Path

import pytest
from randomfun2026solvers.snake_ring import (
    PANEL_H,
    PANEL_W,
    WORKER,
    build_panel_probe,
    simulate_worker,
    worker_glyph_cells,
)

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tasks" / "solutions" / "snake_panel_probe.man"
PROBLEM = ROOT / "tasks" / "problems" / "snake.json"
LM = ROOT / "littleman"
DIRS = {2: (0, -1), 3: (1, 0), 4: (0, 1), 5: (-1, 0)}


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def expected(case: dict) -> list[list[str]]:
    return [f for r in case["rounds"] for f in (r.get("frames") or [])]


# ── the generator and its footprint ───────────────────────────────────────────
def test_the_generator_reproduces_the_checked_in_panel_probe() -> None:
    rows, _ = build_panel_probe()
    assert "\n".join(rows) + "\n" == PROBE.read_text()


def test_the_panel_harness_footprint_is_22x26() -> None:
    rows = PROBE.read_text().rstrip("\n").split("\n")
    assert (max(len(r) for r in rows), len(rows)) == (22, 26)


def test_the_three_port_pipes_deliver_in_the_order_the_panel_needs() -> None:
    _, lens = build_panel_probe()
    # ADDR must not arrive after its own DATA, and SWAP must not overtake the
    # DATA writes still in flight when the frame ends.
    assert lens["addr"] <= lens["data"]
    assert lens["swap"] > lens["data"]


# ── the worker's program ──────────────────────────────────────────────────────
def test_the_worker_program_commits_every_public_frame(cases: list[dict]) -> None:
    for case in cases:
        got = simulate_worker(case["rounds"])
        assert got == expected(case), case["name"]


def test_the_worker_program_is_251_glyph_cells_over_43_blocks() -> None:
    """The budget the layout has to fit; a rewrite that grows it should say so."""
    assert (worker_glyph_cells(), len(WORKER)) == (251, 43)


def test_every_branch_lane_names_a_block_that_exists() -> None:
    for name, (_, succ) in WORKER.items():
        targets = [succ] if isinstance(succ, str) else list(succ.values())
        for t in targets:
            assert t == "HALT" or t in WORKER, f"{name} -> {t}"


def test_the_end_sentinel_is_the_only_negative_the_ring_holds(cases: list[dict]) -> None:
    """The body loops find their end with a bare `X`, which only works if no
    other ring slot can ever go negative -- so `f` is 256 for "no fruit", not -1,
    and `d` is popped explicitly rather than by the loops."""
    ring_negatives = set()
    for case in cases:
        # replay the game and check the invariant on the values the ring holds
        rounds = case["rounds"]
        sx, sy = int(rounds[0]["in"][0]), int(rounds[0]["in"][1])
        body, fruit, dirn = deque([(sx, sy)]), None, 3
        for r in rounds[1:]:
            p = [int(v) for v in r["in"]]
            if p[0] == 1:
                fruit = (p[1], p[2])
            elif p[0] == 0:
                hx, hy = body[-1]
                dx, dy = DIRS[dirn]
                nx, ny = hx + dx, hy + dy
                if not (0 <= nx < PANEL_W and 0 <= ny < PANEL_H):
                    break
                grow = fruit == (nx, ny)
                if not grow:
                    if (nx, ny) in set(body) - {body[0]}:
                        break
                    body.popleft()
                else:
                    fruit = None
                body.append((nx, ny))
            else:
                dirn = p[0]
            slots = [16 * y + x for (x, y) in body]
            slots.append(256 if fruit is None else 16 * fruit[1] + fruit[0])
            ring_negatives |= {v for v in slots if v < 0}
    assert ring_negatives == set()


# ── the reference engine ──────────────────────────────────────────────────────
def painter_stream(case: dict) -> list[dict]:
    """The rounds the panel harness is driven with: one frame's delta per round."""
    rounds = case["rounds"]
    sx, sy = int(rounds[0]["in"][0]), int(rounds[0]["in"][1])
    body = deque([(sx, sy)])
    fruit, dirn, dead = None, 3, False
    out: list[list[tuple[int, int]]] = [[(sy * PANEL_W + sx, 10)]]
    for r in rounds[1:]:
        p = [int(v) for v in r["in"]]
        if p[0] == 1:
            fruit = (p[1], p[2])
            out.append([(fruit[1] * PANEL_W + fruit[0], 9)])
        elif p[0] == 0:
            hx, hy = body[-1]
            dx, dy = DIRS[dirn]
            nx, ny = hx + dx, hy + dy
            delta: list[tuple[int, int]] = []
            if not (0 <= nx < PANEL_W and 0 <= ny < PANEL_H):
                dead = True
            else:
                grow = fruit == (nx, ny)
                if (nx, ny) in (set(body) if grow else set(body) - {body[0]}):
                    dead = True
                else:
                    if not grow:
                        tail = body.popleft()
                        delta.append((tail[1] * PANEL_W + tail[0], 0))
                    else:
                        fruit = None
                    body.append((nx, ny))
                    delta.append((ny * PANEL_W + nx, 10))
            if dead:
                delta = [(y * PANEL_W + x, 9) for (x, y) in body]
            out.append(delta)
        else:
            dirn = p[0]
    rows = []
    for delta, frame in zip(out, expected(case), strict=True):
        stream = [str(len(delta))]
        for addr, colour in delta:
            stream += [str(addr), str(colour)]
        rows.append({"in": stream, "out": [], "frames": [frame]})
    return rows


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node for the engine")
def test_the_panel_harness_commits_every_public_frame_on_the_engine(
    cases: list[dict], tmp_path: Path
) -> None:
    spec = tmp_path / "cases.json"
    spec.write_text(json.dumps(
        [{"name": c["name"], "rounds": painter_stream(c)} for c in cases]))
    proc = subprocess.run(
        ["node", "tools/display-frames.mjs", str(PROBE), str(spec), "200000"],
        cwd=LM, capture_output=True, text=True, check=True,
    )
    got = json.loads(proc.stdout)["cases"]
    for out, case in zip(got, cases, strict=True):
        assert out.get("fatal") is None, (case["name"], out.get("fatal"))
        assert out["frames"] == expected(case), case["name"]
