"""The LLLM panel harness: its footprint, its port timing, and its frames.

Three things are pinned here:

* the probe's **shape** — 22x26, area2 676, and the three port pipe lengths, so a
  change that grows the box or re-routes a port fails in milliseconds;
* the ordering assertion inside :func:`attach_panel` actually fires, so rule 3 is
  a guard rather than a comment;
* :func:`delta_stream` round-trips through ``SWAP 1`` semantics, and the harness
  commits **every** frame of **every** public LLLM case on the real engine when
  driven with that stream.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from randomfun2026solvers import lllm_panel
from randomfun2026solvers.lllm_panel import (
    PANEL_H,
    PANEL_W,
    attach_panel,
    build_panel_probe,
    delta_stream,
    replay,
)
from randomfun2026solvers.optimize import verify
from randomfun2026solvers.scoring import footprint

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = ROOT / "tasks" / "problems" / "little-little-little-man.json"

# The probe as it stands: ADDR is the shortest pipe, SWAP the longest.
PIPE_LENS = {"addr": 2, "data": 6, "swap": 43}


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def expected(case: dict) -> list[list[str]]:
    return [f for r in case["rounds"] for f in (r.get("frames") or [])]


def probe_problem(case: dict) -> dict:
    """The case rewritten as the painter's protocol: one frame's delta per round.

    Round gating is what makes this a real test — the engine withholds round
    N+1's input until round N's frame is committed, exactly as the judge does.
    """
    frames = expected(case)
    stream = delta_stream(frames)
    rounds, i = [], 0
    for frame in frames:
        n = stream[i]
        chunk, i = stream[i : i + 1 + 2 * n], i + 1 + 2 * n
        rounds.append({"in": [str(v) for v in chunk], "out": [], "frames": [frame]})
    assert i == len(stream)
    return {
        "slug": "little-little-little-man",
        "scoring": "footprint-tick",
        "publicTestData": [{"name": case["name"], "rounds": rounds}],
    }


# ── the probe's shape ─────────────────────────────────────────────────────────
def test_the_panel_harness_footprint_is_22x26() -> None:
    rows, _ = build_panel_probe()
    assert footprint("\n".join(rows) + "\n") == (22, 26, 676)


def test_the_three_port_pipes_are_the_lengths_the_panel_needs() -> None:
    _, lens = build_panel_probe()
    assert lens == PIPE_LENS
    # ADDR must not arrive after its own DATA (sent two ticks later), and SWAP
    # must not overtake the DATA writes still in flight one lap behind it.
    assert lens["addr"] - 2 <= lens["data"]
    assert lens["swap"] > lens["data"] - 12


def test_the_painter_sends_from_three_distinct_columns() -> None:
    """All three ports leave the south wall and `s` binds by Manhattan distance,
    so two sends sharing a column would bind to the same pipe."""
    cols = (lllm_panel.P_DATA, lllm_panel.P_ADDR, lllm_panel.P_SWAP)
    assert len(set(cols)) == 3
    assert all(0 <= c < lllm_panel.PAINTER_IW for c in cols)


@pytest.mark.parametrize(
    ("which", "delta"),
    [("addr", +100), ("swap", -100)],
)
def test_the_ordering_assertion_fires_if_a_pipe_length_is_perturbed(
    monkeypatch: pytest.MonkeyPatch, which: str, delta: int
) -> None:
    real = lllm_panel.pipe
    order = ["addr", "data", "swap"]  # attach_panel draws them in this order
    drawn: list[int] = []

    def fake(g, cells, into):  # type: ignore[no-untyped-def]
        n = real(g, cells, into)
        drawn.append(n)
        return n + delta if order[len(drawn) - 1] == which else n

    monkeypatch.setattr(lllm_panel, "pipe", fake)
    with pytest.raises(ValueError, match="deliver out of order"):
        build_panel_probe()


def test_attach_panel_raises_rather_than_drawing_over_a_worker() -> None:
    """The band two rows below the painter's south wall belongs to the ports."""
    from randomfun2026solvers.circuit import Circuit, Collision

    g = Circuit(22, 26)
    g.set(lllm_panel.P_DATA + 2, 6, "M")  # squarely on the DATA pipe's bend row
    with pytest.raises(Collision):
        attach_panel(g, 2, 1, 3, 7)


# ── the delta stream ──────────────────────────────────────────────────────────
def test_delta_stream_round_trips_through_swap_one_semantics(cases: list[dict]) -> None:
    """`SWAP 1` preserves the next buffer, so a frame is a delta on the last one
    and replaying the stream against a black 16x16 buffer must rebuild them all."""
    for case in cases:
        frames = expected(case)
        assert replay(delta_stream(frames)) == frames, case["name"]


def test_the_first_frames_delta_is_every_non_black_pixel(cases: list[dict]) -> None:
    """Both LM-75 buffers start black, so frame 1 has no previous frame to lean on."""
    for case in cases:
        first = expected(case)[0]
        want = sum(1 for row in first for ch in row if ch != "0")
        assert delta_stream([first])[0] == want, case["name"]


def test_every_public_frame_is_16x16(cases: list[dict]) -> None:
    for case in cases:
        for frame in expected(case):
            assert len(frame) == PANEL_H
            assert all(len(r) == PANEL_W for r in frame)


# ── the reference engine ──────────────────────────────────────────────────────
def _needs_node() -> bool:
    return os.environ.get("LM_VALIDATOR", "fast").lower() == "reference"


@pytest.mark.slow
@pytest.mark.skipif(
    _needs_node() and shutil.which("node") is None, reason="needs node for the engine"
)
def test_the_panel_harness_commits_every_public_lllm_frame(cases: list[dict]) -> None:
    rows, _ = build_panel_probe()
    source = "\n".join(rows) + "\n"
    ticks: dict[str, int] = {}
    for case in cases:
        result = verify(source, probe_problem(case))
        (verdict,) = result.cases
        assert verdict.passed, (case["name"], verdict.detail)
        ticks[case["name"]] = verdict.ticks
    print("\nticks per case:", json.dumps(ticks))
    assert len(ticks) == len(cases)


@pytest.mark.slow
@pytest.mark.skipif(
    _needs_node() and shutil.which("node") is None, reason="needs node for the engine"
)
def test_one_pixel_pair_costs_twelve_painter_ticks() -> None:
    """The pixel loop is a 12-cell lap, so the marginal cost of a pair is 12 ticks.

    Measured rather than counted: two frames that differ only in how many pairs
    they carry, and the difference in the tick their frame lands on.
    """
    rows, _ = build_panel_probe()
    source = "\n".join(rows) + "\n"

    def ticks_for(pairs: int) -> int:
        frame = ["".join("9" if y * PANEL_W + x < pairs else "0"
                         for x in range(PANEL_W)) for y in range(PANEL_H)]
        prob = probe_problem({"name": f"{pairs}px", "rounds": [{"frames": [frame]}]})
        result = verify(source, prob)
        (verdict,) = result.cases
        assert verdict.passed, verdict.detail
        return verdict.ticks

    one, ten = ticks_for(1), ticks_for(10)
    assert (ten - one) / 9 == 12.0, (one, ten)
