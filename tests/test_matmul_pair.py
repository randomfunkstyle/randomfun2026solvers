"""`matmul` split across two little men -- token level, and the two rooms.

The claim being checked is narrow and mechanical: neither man ever needs a
spill, so the twelve-glyph MAC of :mod:`matmul_cfg` becomes six glyphs in man M
and seven in man C, running on the same clock.  Everything below either proves
the split still computes ``A @ B``, or prices it.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from randomfun2026solvers import matmul_cfg, matmul_grid
from randomfun2026solvers.circuit import Collision
from randomfun2026solvers.matmul_pair import (
    RING_WORDS,
    WORKER_C,
    WORKER_M,
    matmul_reference,
    public_cases,
    simulate_pair,
)

PROBLEM = Path(__file__).resolve().parents[1] / "tasks" / "problems" / "matmul.json"
DIM_MIN, DIM_MAX = 2, 16


def _names() -> list[str]:
    return [c["name"] for c in json.loads(PROBLEM.read_text())["publicTestData"]]


def _case(n: int, m: int, k: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [n, m, k] + [rng.randint(-99, 99) for _ in range(n * m + m * k)]


# ── the two CFGs are well formed ──────────────────────────────────────────────
@pytest.mark.parametrize("worker", [WORKER_C, WORKER_M], ids=["C", "M"])
def test_every_successor_names_a_real_block(worker: dict) -> None:
    for name, (_toks, succ) in worker.items():
        targets = [succ] if isinstance(succ, str) else list(succ.values())
        for tgt in targets:
            assert tgt in worker, f"{name} -> {tgt}"


@pytest.mark.parametrize("worker", [WORKER_C, WORKER_M], ids=["C", "M"])
def test_a_branching_block_ends_in_its_branch_glyph(worker: dict) -> None:
    lanes = {"X": {"neg", "zero", "pos"}, "d": {"pos", "zero"}}
    for name, (toks, succ) in worker.items():
        if isinstance(succ, str):
            continue
        assert toks[-1] in lanes, f"{name} branches on {toks[-1]!r}"
        assert set(succ) <= lanes[toks[-1]], name


def _pipes(worker: dict) -> set[str]:
    return {t for toks, _ in worker.values() for t in toks
            if len(t) == 2 and t[0] in "rs" and t not in ("ri", "so")}


def test_exactly_one_wire_joins_the_two_rooms() -> None:
    """`p` is the only pipe between them, and it only ever runs one way.

    That is what makes the north band route: every horizontal run leaves its
    room going east, so sorting the runs by column orders them safely.  A second
    channel would have to come back westward, and its riser would sit inside the
    first one's span whichever way the rooms are ordered.
    """
    assert _pipes(WORKER_M) == {"sp", "ra", "sa", "rb", "sb", "rf", "sf",
                                "rk", "sk", "rs", "ss"}
    assert _pipes(WORKER_C) == {"rp"} | {f"{d}{r}" for r in "kscq" for d in "rs"}
    # Man M reads the input; man C writes the output; neither does both.
    assert "ri" in {t for toks, _ in WORKER_M.values() for t in toks}
    assert "ri" not in {t for toks, _ in WORKER_C.values() for t in toks}
    assert "so" in {t for toks, _ in WORKER_C.values() for t in toks}
    assert "so" not in {t for toks, _ in WORKER_M.values() for t in toks}


def test_man_c_has_no_load_phase_at_all() -> None:
    """The two bulk loops moved to the man who is idle during both."""
    c_toks = [t for toks, _ in WORKER_C.values() for t in toks]
    assert c_toks.count("ri") == 0            # forwarding `A` is man M's now
    assert not any(n.startswith("B") for n in WORKER_C)   # so is packing `B`
    assert any(n.startswith("MB") for n in WORKER_M)


def test_the_hot_loops_are_six_glyphs_and_seven() -> None:
    """The whole point: no spill in either man, and the two nearly balance."""
    assert WORKER_M["MMAC"][0] == ["rb", "sb", "*", "sp", "m", "d"]
    assert WORKER_C["CACC"][0] == ["rp", "M", "rc", "+", "sc", "m", "d"]
    assert matmul_cfg.WORKER["MAC"][0][:3] == ["rs", "ss", "M"]   # the spill
    assert len(matmul_cfg.WORKER["MAC"][0]) == 12


# ── it computes the right thing ───────────────────────────────────────────────
@pytest.mark.parametrize("case", public_cases(), ids=_names())
def test_every_public_case_comes_out_right(case: list[int]) -> None:
    assert simulate_pair(case)["out"] == matmul_reference(case)


@pytest.mark.parametrize(("n", "m", "k"),
                         [(2, 2, 2), (2, 3, 2), (4, 4, 4), (16, 16, 16),
                          (16, 2, 16), (5, 6, 4), (7, 5, 9), (2, 16, 2),
                          (16, 16, 2), (2, 2, 16), (3, 3, 3), (9, 7, 5)])
def test_the_pair_agrees_with_the_reference(n: int, m: int, k: int) -> None:
    case = _case(n, m, k, seed=n * 1000 + m * 31 + k)
    assert simulate_pair(case)["out"] == matmul_reference(case)


@pytest.mark.slow
def test_every_shape_in_two_to_sixteen() -> None:
    for n in range(DIM_MIN, DIM_MAX + 1):
        for m in range(DIM_MIN, DIM_MAX + 1):
            for k in range(DIM_MIN, DIM_MAX + 1):
                case = _case(n, m, k, seed=n * 289 + m * 17 + k)
                got = simulate_pair(case)["out"]
                assert got == matmul_reference(case), (n, m, k)


# ── the pipes are big enough, and the men stay in step ────────────────────────
@pytest.mark.parametrize("case", public_cases(), ids=_names())
def test_no_pipe_holds_more_than_it_is_declared_to(case: list[int]) -> None:
    """A pipe's capacity is its cell count; one that overflows deadlocks."""
    high = simulate_pair(case)["high"]
    for name, words in RING_WORDS.items():
        assert high[name] <= words, f"{name} peaked at {high[name]} > {words}"


@pytest.mark.slow
def test_the_declared_pipe_sizes_are_the_measured_peaks() -> None:
    peak = dict.fromkeys(RING_WORDS, 0)
    for n in range(DIM_MIN, DIM_MAX + 1):
        for m in range(DIM_MIN, DIM_MAX + 1):
            for k in range(DIM_MIN, DIM_MAX + 1):
                high = simulate_pair(_case(n, m, k, seed=n + m + k))["high"]
                for name in peak:
                    peak[name] = max(peak[name], high[name])
    for name, words in RING_WORDS.items():
        assert peak[name] <= words, f"{name} peaked at {peak[name]} > {words}"


@pytest.mark.parametrize("case", public_cases(), ids=_names())
def test_man_c_is_the_clock(case: list[int]) -> None:
    """Man C walks further than man M on every case, so he sets the tick count.

    Both men stall, and one stall is not avoidable: the input arrives in one
    stream, so man C can do nothing at all until man M has read `A`, packed `B`
    and produced a first product.  What must not happen is man M falling behind
    once the mill is turning, which would put the *multiply* on the clock.
    """
    res = simulate_pair(case)
    assert res["cells"]["C"] > res["cells"]["M"]
    assert res["ticks"] >= res["cells"]["C"]


def test_the_pair_beats_the_one_man_ring_on_every_public_case() -> None:
    for case in public_cases():
        one = matmul_cfg.simulate(case)[2]
        two = simulate_pair(case)["ticks"]
        assert two < one, (case[:3], one, two)


def test_the_hot_loop_models_under_four_cells_a_mac() -> None:
    """4.00 is the one-man floor; the pair has to beat it to be worth a room."""
    n, m, k = 16, 16, 16
    case = _case(n, m, k, seed=1)
    res = simulate_pair(case)
    groups = n * m * -(-k // 3)
    assert res["runs"]["C"]["CACC"] == groups
    assert res["runs"]["M"]["MMAC"] == groups
    # Steady state is `max(6, 7)` cells for a group of three lanes against the
    # one-man MAC's twelve, so the hot loop is 7/12 of the one-man cost: 2.33
    # cells a MAC against 4.00 where `K` fills every lane, and 2.63 against 4.50
    # at `K = 16`, where the last group carries one lane rather than three.
    hot = len(WORKER_C["CACC"][0]) * groups
    one_hot = len(matmul_cfg.WORKER["MAC"][0]) * groups
    assert hot / one_hot == pytest.approx(7 / 12, abs=1e-9)
    assert hot / (n * m * k) < 4.0
    # All-in the pair still beats the one-man ring's 7.32 cells a MAC.
    assert res["ticks"] / (n * m * k) < matmul_cfg.simulate(case)[2] / (n * m * k)


# ── both CFGs compile to a room ───────────────────────────────────────────────
def _spec(worker: dict, rings: dict[str, str], entry: str) -> matmul_grid.Spec:
    live: set[tuple[str, str]] = set()
    for case in public_cases():
        who = "C" if entry == "HEAD" else "M"
        live |= set(simulate_pair(case)["lanes"][who])
    dead = frozenset((n, lane) for n, (_t, s) in worker.items()
                     if isinstance(s, dict) for lane in s if (n, lane) not in live)
    loops = tuple(n for n, (t, s) in worker.items() if "H" not in t
                  and (s == n or (isinstance(s, dict) and n in s.values())))
    return matmul_grid.Spec.of(worker, rings, entry, loops, dead)


#: `so` lands in the `io` band; `rp` gets a band of its own in each room.
SPEC_C = _spec(WORKER_C, {"k": "k", "s": "s", "c": "c", "q": "q", "p": "w"},
               "HEAD")
SPEC_M = _spec(WORKER_M, {"a": "a", "b": "b", "f": "f", "k": "k", "s": "s",
                          "p": "p"}, "MHEAD")

#: Band orders and widths found by annealing against ``max(w, h)^2 * ticks``.
_WC = {"io": 4, "s": 7, "k": 7, "w": 4, "c": 7, "q": 7}
_WM = {"s": 7, "k": 7, "io": 4, "a": 4, "b": 4, "p": 4, "f": 7}
GEOM_C = matmul_grid.Geometry(("io", "s", "k", "w", "c", "q"),
                              ("io", "s", "k", "w", "c", "q"), _WC, dict(_WC))
GEOM_M = matmul_grid.Geometry(("s", "k", "io", "a", "b", "p", "f"),
                              ("s", "k", "io", "a", "b", "p", "f"), _WM,
                              dict(_WM))


@pytest.mark.parametrize(("spec", "geom"), [(SPEC_C, GEOM_C), (SPEC_M, GEOM_M)],
                         ids=["C", "M"])
def test_each_man_s_cfg_lays_into_a_room_that_walks_its_own_tokens(
        spec: matmul_grid.Spec, geom: matmul_grid.Geometry) -> None:
    with matmul_grid.use(spec):
        room = matmul_grid.build_room(matmul_grid.plan(geom))
        matmul_grid.check_room(room)          # raises unless every block walks
    assert room.iw > 0 and room.ih > 0


def test_the_swappable_spec_leaves_the_one_man_compiler_alone() -> None:
    before = dict(matmul_grid.LAID)
    with matmul_grid.use(SPEC_M):
        assert "MMAC" in matmul_grid.LAID
    assert matmul_grid.LAID == before
    assert matmul_grid.ENTRY == "HEAD"


def test_man_c_s_room_walks_fewer_cells_than_the_one_man_room() -> None:
    """The estimator agrees with the engine to 0.02%, so this is a tick claim."""
    one_room = matmul_grid.build_room()
    one = sum(matmul_grid.estimate_ticks(one_room, r, ln)
              for r, ln in matmul_grid.public_traces())
    with matmul_grid.use(SPEC_C):
        room = matmul_grid.build_room(matmul_grid.plan(GEOM_C))
        two = sum(matmul_grid.estimate_ticks(room, res["runs"]["C"],
                                             res["lanes"]["C"])
                  for res in map(simulate_pair, public_cases()))
    assert two < one, (one, two)


def test_a_geometry_that_splits_a_band_is_rejected() -> None:
    """Ties are excluded rather than resolved; a split band must not lay."""
    bad = matmul_grid.Geometry(GEOM_M.recv_order, GEOM_M.recv_order[::-1],
                               dict(GEOM_M.recv_w), dict(GEOM_M.send_w))
    with matmul_grid.use(SPEC_M), pytest.raises(Collision):
        matmul_grid.build_room(matmul_grid.plan(bad))
