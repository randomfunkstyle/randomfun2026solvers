"""The generated LM-75 machine: ``plotter`` and ``palette``, from ``.asm`` to ``.man``.

``tests/test_lm1_programs.py`` proves the *programs* are right (their port writes,
replayed through ``lm1/display.py``'s panel model, produce the expected frames). This
file proves the *hardware* is right, which is a different claim: a display problem is
judged on committed frames, so nothing here can be inferred from program output —
these programs emit none, and emitting any at all is an error (``SPEC.md`` § The LM-75
display).

Four tiers, cheapest first:

* pure-Python invariants of the port wiring — above all the ``ARCH.md`` §7.1 tie-break
  that decides *which* port each lane's ``s`` talks to. Getting that wrong swaps ADDR
  for DATA and is invisible in the ASCII;
* the **ROM image** run on the emulator, which is the bytecode the generated hardware
  actually fetches (renumbered opcodes, rescaled jumps) rather than the assembler's
  source ring;
* generation, which runs the engine's structural analysis (rooms, pipes, the panel's
  resolution) and every pipe binding, plus the ``route`` oracle on the real grid;
* the real thing: every public case replayed on the reference wasm, frame against
  frame, with the engine doing its own round gating.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1 import machine, programs  # noqa: E402
from randomfun2026solvers.lm1.asm import assemble  # noqa: E402
from randomfun2026solvers.lm1.display import (  # noqa: E402
    ADDR,
    DATA,
    SWAP,
    Display,
    frames_from_writes,
)
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402
from randomfun2026solvers.lm1.isa import Sem  # noqa: E402

LM_MJS = REPO / "littleman" / "lm.mjs"
FRAME_TOOL = REPO / "littleman" / "tools" / "display-frames.mjs"

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists() or not FRAME_TOOL.exists(),
    reason="node, littleman/lm.mjs and littleman/tools/display-frames.mjs required",
)
slow = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the full public-case sweeps",
)

#: The display programs. ``palette`` is the small one (8x8, no input at all, which is
#: what exercises the omitted ``I`` room); ``plotter`` is the graded 32x24 Bresenham
#: machine.
DISPLAY_TARGETS = ("palette", "plotter")

#: ``max(width, height)²`` and the shape it comes from, pinned per slug so a
#: regression in either dimension is a failing test rather than a quietly worse score.
EXPECTED_SHAPE = {"plotter": (112, 106), "palette": (98, 98)}
EXPECTED_FOOTPRINT = {"plotter": 12_544, "palette": 9_604}

MAX_INSTRUCTIONS = 400_000
TICK_CAP = 3_000_000


def _grid_path(slug: str) -> Path:
    return REPO / "tasks" / "solutions" / f"{slug}_cpu.man"


def _rounds(case: dict) -> list[dict]:
    return case.get("rounds") or [case]


def _flat_expected_frames(case: dict) -> list[list[str]]:
    return [f for r in _rounds(case) for f in (r.get("frames") or [])]


def _public_cases(slug: str) -> list[dict]:
    path = REPO / "tasks" / "problems" / f"{slug}.json"
    return json.loads(path.read_text(encoding="utf-8"))["publicTestData"]


# ── the port lanes ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("sem", [Sem.DISPLAY_ADDR, Sem.DISPLAY_DATA, Sem.DISPLAY_SWAP])
def test_each_port_is_a_w_s_w_sandwich_on_its_own_band(sem: Sem) -> None:
    """ACC has to survive a port write, and each port needs a *different* pipe."""
    micro = machine.hw_micro(sem)
    assert [g for g, _ in micro] == ["W", "s", "W"]
    assert [b for glyph, b in micro if glyph == "s"] == [machine.DSP_SEM_BAND[sem]]
    assert len({b for _, b in micro if b}) == 1


def test_the_three_bands_are_exactly_the_three_ports() -> None:
    assert set(machine.DSP_SEM_BAND.values()) == set(machine.DSP_BANDS)
    assert len(machine.DSP_BANDS) == 3


def test_display_lanes_sit_at_the_bottom_beside_the_south_wall() -> None:
    """Their pipes leave the south wall, so ``ARCH.md`` §7.1 wants them nearest it."""
    p = machine.plan(programs.load("plotter"))
    dsp = {m: p.row[m] for m in p.row if p.sem[m] in machine.DSP_SEM_BAND}
    assert len(dsp) == 3
    span = 2 * p.lanes - 1
    assert max(dsp.values()) == span  # `plotter` has no OUT lane to displace them
    assert sorted(dsp.values()) == [span - 4, span - 2, span]
    # West-to-east band order maps onto bottom-to-top rows: DATA turns west round the
    # panel and so must start west of every column a later lane uses.
    by_band = {machine.DSP_SEM_BAND[p.sem[m]]: p.row[m] for m in dsp}
    assert [by_band[b] for b in machine.DSP_BANDS] == [span - 4, span - 2, span]


@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_port_columns_run_west_to_east_in_band_order(slug: str) -> None:
    """The pipe fan around the panel only closes in this order (``_display``).

    Each pipe leaves the south wall in its own ``s``'s column; DATA then turns west
    and SWAP east, so a pipe may only cross columns belonging to lanes on its own
    side of the panel.
    """
    prog = programs.load(slug)
    cpu = machine.build_cpu(prog, machine.plan(prog))
    cols = cpu.dsp_cols
    assert set(cols) == set(machine.DSP_BANDS)
    assert [cols[b] for b in machine.DSP_BANDS] == sorted(cols.values())
    assert len(set(cols.values())) == 3  # distinct, or two ports share a pipe
    # And there is no OUT lane to share the south wall with: `plan` refuses a program
    # that drives both, so `out_col` on a display machine is a dead default.
    assert not cpu.has_out


@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_each_display_s_is_strictly_nearest_its_own_port(slug: str) -> None:
    """The §7.1 tie-break, as arithmetic: column separation must beat row separation.

    Every display pipe leaves the south wall in the column of its own lane's ``s``,
    so an ``s`` binds the *wrong* port the moment another lane's column is closer
    than its own — which is exactly what ``_DSP_PITCH`` guards.
    """
    prog = programs.load(slug)
    p = machine.plan(prog)
    cpu = machine.build_cpu(prog, p)
    rows = {
        machine.DSP_SEM_BAND[p.sem[m]]: p.row[m]
        for m in p.row
        if p.sem[m] in machine.DSP_SEM_BAND
    }
    assert set(cpu.dsp_cols) == set(rows)
    wall = cpu.height + 2  # the pipes' source cells sit one row below the south wall
    for band, col in cpu.dsp_cols.items():
        own = wall - rows[band]
        for other, ocol in cpu.dsp_cols.items():
            if other == band:
                continue
            rival = abs(col - ocol) + (wall - rows[other])
            assert rival > own, f"{band}'s `s` is {rival} from {other} but {own} from its own"


def test_a_program_may_not_write_both_the_panel_and_the_output_room() -> None:
    """A display-judged problem emitting program output is an error (``SPEC.md``)."""
    prog = assemble("LDI 1\nOUT\nDSPD\nHALT\n")
    with pytest.raises(machine.MachineError, match="no program output"):
        machine.plan(prog)


def test_a_port_opcode_without_a_panel_size_is_refused() -> None:
    prog = assemble("LDI 15\nDSPD\nLDI 0\nDSPS\nHALT\n")
    with pytest.raises(machine.MachineError, match="no display resolution"):
        machine.build(prog, tape_n=2)


def test_a_display_program_needs_a_resolution() -> None:
    with pytest.raises(machine.MachineError, match="no display resolution"):
        machine.build(programs.load("plotter"), tape_n=machine.TAPE_SIZE["plotter"])


def test_the_resolution_comes_from_the_problem() -> None:
    assert machine.display_for("plotter") == (32, 24)
    assert machine.display_for("palette") == (8, 8)
    assert machine.display_for("brackets") is None


def test_dsp_p_still_has_no_hardware() -> None:
    """``ARCH.md`` §6's ``DSP p`` picks its pipe from a *word*; geometry cannot."""
    assert machine.hw_micro(Sem.DISPLAY) == ()
    with pytest.raises(machine.MachineError, match="no hardware micro-program"):
        machine.plan(assemble("LDI 15\nDSP 1\nHALT\n"))


# ── the ROM image, on the emulator ───────────────────────────────────────────
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_the_rom_image_draws_what_the_source_program_draws(slug: str) -> None:
    """The bytecode the hardware fetches is *not* the assembler's word ring.

    ``rom_words`` renumbers every opcode from its lane row and rescales every skip
    count into the fixed-width image; either can be off by one without the source
    program noticing. Run both and compare the panel.
    """
    width, height = programs.display_size(slug)
    source = programs.load(slug)
    image = machine.image_program(source)
    (_case, rounds) = programs.rounds_for_problem(slug)[0]

    runs = [
        Emulator(prog).run(rounds, max_instructions=MAX_INSTRUCTIONS) for prog in (source, image)
    ]
    frames = [frames_from_writes(r.display_writes, width=width, height=height) for r in runs]
    assert frames[0] == frames[1], f"{slug}: the image draws a different picture"
    assert frames[0], f"{slug}: nothing was committed"
    assert not runs[1].output, f"{slug}: the image emitted program output"


def test_palette_commits_sixteen_solid_frames_on_the_emulator() -> None:
    """Every public frame, pixel for pixel — the whole point of a display problem."""
    width, height = programs.display_size("palette")
    res = Emulator(programs.load("palette")).run([Round()], max_instructions=MAX_INSTRUCTIONS)
    got = frames_from_writes(res.display_writes, width=width, height=height)
    (_name, rounds) = programs.frames_for_problem("palette")[0]
    assert got == rounds[0]
    assert [row[0] for row in got[0]] == ["0"] * height
    assert got[-1] == ["f" * width] * height
    assert res.reason == "halted"
    assert not res.output


def test_the_panel_model_matches_the_engine_on_addr_data_swap_order() -> None:
    """``SPEC.md``: ADDR then DATA then SWAP, and SWAP 0 clears *and* homes."""
    panel = Display(width=4, height=4)
    panel.write(ADDR, 5)  # (col 1, row 1)
    panel.write(DATA, 9)
    assert panel.cursor == 6
    panel.write(SWAP, 0)
    assert panel.committed[-1] == ["0000", "0900", "0000", "0000"]
    assert panel.cursor == 0 and panel.next == [[0] * 4] * 4


# ── the generated grid, through the engine's structural analysis ──────────────
@node_required
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_the_checked_in_display_grid_matches_the_generator(slug: str) -> None:
    expected = "\n".join(machine.build_for(slug).rows) + "\n"
    assert _grid_path(slug).read_text(encoding="utf-8") == expected, (
        f"{slug}_cpu.man is stale; regenerate with "
        f"`python -m randomfun2026solvers.lm1.machine {slug} --out {_grid_path(slug)}`"
    )


@node_required
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_the_generated_shape_is_the_one_we_scored(slug: str) -> None:
    m = machine.build_for(slug)
    assert (m.width, m.height) == EXPECTED_SHAPE[slug]
    assert m.footprint == EXPECTED_FOOTPRINT[slug]
    assert m.display == programs.display_size(slug)
    # No `O` room: emitting output on a display problem is an error, and an unused
    # outgoing pipe would still compete for every `s` (§7.1).
    assert "O" not in "".join(m.rows)


@node_required
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_exactly_one_panel_at_the_stated_resolution_with_one_pipe_per_side(slug: str) -> None:
    """Two pipes on a side, a pipe on the right side, or one at a corner: load error."""
    from randomfun2026solvers.littleman import Littleman

    width, height = programs.display_size(slug)
    info = Littleman().analyze(_grid_path(slug))
    assert len(info.displays) == 1
    (x0, y0), (x1, y1) = info.displays[0]["min"], info.displays[0]["max"]
    assert (x1 - x0 - 1, y1 - y0 - 1) == (width, height)

    into_panel = [p for p in info.pipes if p.dst == -1]
    assert len(into_panel) == 3
    sides = set()
    for pipe in into_panel:
        x, y = pipe.path[-1].pos.as_tuple()
        if y == y0 - 1 and x0 < x < x1:
            sides.add("top")
        elif x == x0 - 1 and y0 < y < y1:
            sides.add("left")
        elif y == y1 + 1 and x0 < x < x1:
            sides.add("bottom")
        else:
            raise AssertionError(f"{slug}: pipe ends at {(x, y)}, off the panel's sides")
    assert sides == {"top", "left", "bottom"}


@node_required
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_the_engine_finds_exactly_the_pipes_the_generator_drew(slug: str) -> None:
    """ROM, 3 x panel, mem request/response, adapter -> tape, the tape's two ring pipes.

    ``plotter`` reads input and so has an ``I`` room; ``palette`` reads none, so its
    input pipe is omitted rather than left to compete for every ``r``.
    """
    from randomfun2026solvers.littleman import Littleman

    expected = 10 if slug == "plotter" else 9
    assert len(Littleman().analyze(_grid_path(slug)).pipes) == expected
    assert ("I" in "".join(machine.build_for(slug).rows)) == (slug == "plotter")


@node_required
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_every_port_s_routes_to_its_own_side_of_the_panel(slug: str) -> None:
    """The nearest-pipe oracle, on the real grid (``ARCH.md`` §7.1).

    A mis-bound port is silent: the program paints where it meant to address, or
    commits where it meant to paint, and only the pixels tell you.
    """
    from randomfun2026solvers.littleman import Littleman

    lm = Littleman()
    path = _grid_path(slug)
    m = machine.build_for(slug)
    rows = m.rows
    info = lm.analyze(path)
    (x0, y0), (_x1, y1) = info.displays[0]["min"], info.displays[0]["max"]
    want_end = {
        machine.Band.DSP_ADDR: lambda x, y: y == y0 - 1,
        machine.Band.DSP_DATA: lambda x, y: x == x0 - 1,
        machine.Band.DSP_SWAP: lambda x, y: y == y1 + 1,
    }

    assert set(m.dsp_glyphs) == set(machine.DSP_BANDS)
    for band, (x, y) in m.dsp_glyphs.items():
        assert rows[y][x] == "s", f"{slug}: expected the {band} `s` at {(x, y)}"
        cells = lm.route(path, x, y)
        assert cells, f"{slug}: the {band} `s` at {(x, y)} binds no pipe at all"
        ex, ey = cells[-1].as_tuple()
        assert want_end[band](ex, ey), f"{slug}: {band} routes to {(ex, ey)}, the wrong side"


@node_required
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_folding_the_rom_is_what_buys_plotters_footprint(slug: str) -> None:
    """``rows_for_budget`` aims the ROM at the *CPU's* width and knows nothing about
    the panel, which adds ~30 rows and makes height the binding dimension.

    ``palette`` is the control: its ROM is small enough that the default fold is
    already right, so it carries no ``ROM_ROWS`` entry and must not need one.
    """
    prog = programs.load(slug)
    default = machine.build(
        prog, tape_n=machine.TAPE_SIZE[slug], display=machine.display_for(slug)
    )
    tuned = machine.build_for(slug)
    if slug in machine.ROM_ROWS:
        assert tuned.rom_rows == machine.ROM_ROWS[slug]
        assert tuned.footprint < default.footprint
        assert tuned.width == default.width  # width is the tape's; only height moved
    else:
        assert tuned.rows == default.rows


# ── the real interpreter, drawing the real frames ────────────────────────────
@node_required
def test_the_generated_palette_cpu_draws_all_sixteen_frames_on_the_engine() -> None:
    """End to end: ROM -> fetch -> trie -> port lane -> pipe -> panel.

    The engine judges the frames itself (``--frames``), so this is the same
    comparison the contest does, on the same wasm.
    """
    from randomfun2026solvers.littleman import Littleman

    (_name, rounds) = programs.frames_for_problem("palette")[0]
    snap = Littleman().judge(_grid_path("palette"), frames=rounds, max_ticks=TICK_CAP)
    assert snap.fatal is None, snap.fatal
    assert not snap.output, "a display program must emit no program output"
    assert snap.frame_judge is not None and snap.frame_judge.passed, snap.frame_judge
    assert snap.frame_judge.total == 16
    panel = snap.entities.displays[0]
    assert panel.frames == 16
    assert panel.rows() == rounds[0][-1]  # the panel is left showing colour 15


@node_required
def test_the_generated_plotter_cpu_draws_bresenham_on_the_engine() -> None:
    """Two cheap cases; the full public sweep is the slow test below.

    ``main diagonal`` is the one that exercises both error branches every step, and
    ``one pixel`` is the degenerate start == end case, which is where an inverted
    "segment finished" test shows up as an infinite loop.
    """
    from randomfun2026solvers import optimize

    cheap = {"one pixel", "main diagonal"}
    prob = programs.problem_json("plotter")
    prob = {**prob, "publicTestData": [c for c in prob["publicTestData"] if c["name"] in cheap]}
    assert len(prob["publicTestData"]) == len(cheap)

    res = optimize.verify(_grid_path("plotter"), prob, tick_cap=TICK_CAP)
    assert res.passed, [(c.name, c.detail) for c in res.cases if not c.passed]


@node_required
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_every_public_case_commits_exactly_the_expected_frames(slug: str) -> None:
    """The generated ``.man`` on the reference wasm, frame for frame.

    ``Littleman.display_frames`` hands the expected frames to the engine's ``load``
    so it gates the rounds itself — round N+1's input stays withheld until round N's
    frame is committed (``GRADING.md`` § Rounds) — then snapshots the front buffer at
    every SWAP. Nothing is compared here that the judge would not compare.
    """
    from randomfun2026solvers.littleman import Littleman

    cases = _public_cases(slug)
    got = Littleman().display_frames(_grid_path(slug), cases, max_ticks=2_000_000)
    assert len(got) == len(cases)

    width, height = programs.display_size(slug)
    for case, res in zip(cases, got, strict=True):
        name = case["name"]
        assert res.fatal is None, f"{name}: {res.fatal}"
        assert not res.output, f"{name}: emitted output on a display problem"
        assert (res.width, res.height) == (width, height), name
        want = _flat_expected_frames(case)
        assert len(res.frames) == len(want), name
        for i, (frame, expect) in enumerate(zip(res.frames, want, strict=True)):
            assert frame == expect, f"{name}: frame {i} differs\n" + "\n".join(
                f"  row {y}: got {g} want {w}"
                for y, (g, w) in enumerate(zip(frame, expect, strict=True))
                if g != w
            )


@node_required
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_the_score_is_measured_from_the_committed_frames(slug: str) -> None:
    """``footprint x avg ticks``, with the ticks taken at each case's final commit.

    A display solver normally never halts (``plotter`` blocks on ``IN`` for ever),
    so the run settle tick is the cap and would misprice it by an order of
    magnitude. ``assert not approx`` is what pins the real measurement.
    """
    from randomfun2026solvers.lm1.emulator import TICK_CAP as CAP
    from randomfun2026solvers.scoring import score_program

    res = score_program(_grid_path(slug), slug)
    assert not res.approx, "display ticks fell back to the settle-tick estimate"
    assert (res.width, res.height) == EXPECTED_SHAPE[slug]
    assert res.area2 == EXPECTED_FOOTPRINT[slug]
    assert res.avg_ticks is not None and res.score == pytest.approx(res.area2 * res.avg_ticks)
    # plotter: avg ~483k, worst public case ~857k -> score ~6.06bn. palette has a
    # single case at ~151k -> ~1.45bn. The public plotter cases top out at 8 of the 20
    # legal rounds, so leave room for a case ~2.5x the longest.
    assert max(c.ticks for c in res.cases) < CAP / 4, [c.ticks for c in res.cases]
    assert res.avg_ticks < (600_000 if slug == "plotter" else 200_000), res.avg_ticks


@node_required
@slow
@pytest.mark.parametrize("slug", DISPLAY_TARGETS)
def test_every_public_case_draws_the_expected_frames_via_the_judge(slug: str) -> None:
    """The same sweep again through ``optimize.verify``, i.e. through ``judge --frames``.

    Not redundant with ``display_frames``: that tool does its own compare in Python
    off ``stopOnFrame`` snapshots, where this hands the frames to the engine and reads
    back the engine's own ``frameJudge`` verdict. They agree or one of them is wrong.
    """
    from randomfun2026solvers import optimize

    res = optimize.verify(_grid_path(slug), slug, tick_cap=TICK_CAP)
    failed = [(c.name, c.detail) for c in res.cases if not c.passed]
    assert res.passed, f"{slug}: {failed}"


#: GRADING.md's default step cap. ``plotter.json`` sets ``tickCap: null``.
STEP_CAP = 5_000_000

#: Measured on the engine: the most expensive legal segment is the one with the largest
#: minor-axis travel, ``dx = 31`` and ``dy = 23`` in either direction, at ~265.5k ticks
#: a round. Every other shape is cheaper (a full-width horizontal is 228k, a
#: full-height vertical 186k, a single pixel 12k).
WORST_SEGMENT = (0, 0, 31, 23)
WORST_ROUND_TICKS = 265_517


def _judge_segments(segments: list[tuple[int, ...]]) -> object:
    """Run ``segments`` on the engine, with the emulator supplying expected frames.

    The public cases already pin the emulator's pixels frame for frame, so taking the
    expected frames from it measures the *hardware*, not the algorithm.
    """
    from randomfun2026solvers.littleman import Littleman
    from randomfun2026solvers.lm1.display import frames_from_writes

    width, height = programs.display_size("plotter")
    res = Emulator(programs.load("plotter")).run(
        [Round(input=s) for s in segments], max_instructions=4_000_000
    )
    want = [[frame] for frame in frames_from_writes(res.display_writes, width=width, height=height)]
    assert len(want) == len(segments)
    snap = Littleman().judge(
        _grid_path("plotter"),
        input=" / ".join(" ".join(str(v) for v in s) for s in segments),
        frames=want,
        max_ticks=4 * STEP_CAP,
    )
    assert snap.fatal is None, snap.fatal
    assert snap.frame_judge is not None and snap.frame_judge.passed, snap.frame_judge
    return snap


@node_required
def test_only_the_public_cases_are_graded_which_is_why_plotter_is_submittable() -> None:
    """``privateTestCount`` is 0, so the *graded* worst case is a public one.

    This is the fact the tick margin turns on, and it is worth an assertion rather
    than a comment: ``plotter``'s public set tops out at 8 rounds / ~857k ticks, i.e.
    ~17% of the cap, while a 19- or 20-round case of near-maximal segments would
    overrun it (the test below). Those cases are legal by the stated constraints but
    are never served — "Private cases are never served" (``GRADING.md``), and the
    count is zero besides. If a future problem JSON grows private cases, this fails
    and the margin has to be re-argued.
    """
    prob = programs.problem_json("plotter")
    assert prob["privateTestCount"] == 0
    assert prob["tickCap"] is None
    assert max(len(c["rounds"]) for c in prob["publicTestData"]) == 8
    assert "at most 20 rounds per test case" in prob["io"]["constraints"]

    from randomfun2026solvers.scoring import score_program

    worst = max(c.ticks for c in score_program(_grid_path("plotter"), "plotter").cases)
    assert worst < STEP_CAP // 4, f"worst graded case is {worst:,} of the {STEP_CAP:,} cap"


@node_required
@slow
def test_a_worst_case_20_round_load_would_overrun_the_cap() -> None:
    """The margin, measured — and it is *negative* at the constraints' limit.

    A 20-round case of the most expensive legal segment costs ~5.31M ticks against the
    5M cap (~1.06x), and 19 rounds already costs ~5.05M. Only 18 fit. The reason
    ``plotter`` is still safe to submit is ``privateTestCount == 0`` (the test above),
    not headroom.

    Two figures that look contradictory are both right and measure different loads, so
    they are pinned together here: ~857k is the worst *public* case, and ~4.78M is the
    20-round load in ``test_lm1_programs.py``, whose segments average less than the
    worst. That one is *not* the worst legal load, and reading it as the margin is how
    "1.05x under the cap" gets written down for a machine that is 1.06x over it.

    The emulator's estimate for the same 20-round load is 3.8M — ~20% optimistic
    against the engine's 5.31M, so it must not be used as the margin either.
    """
    assert _judge_segments([WORST_SEGMENT]).step == pytest.approx(WORST_ROUND_TICKS, rel=0.02)

    fits = _judge_segments([WORST_SEGMENT] * 18).step
    assert fits < STEP_CAP, f"18 rounds should still fit, got {fits:,}"

    over = _judge_segments([WORST_SEGMENT] * 19).step
    assert over > STEP_CAP, (
        f"19 worst-case rounds now cost {over:,}, under the {STEP_CAP:,} cap — the "
        "machine got faster, so re-measure the limit and update this test"
    )
