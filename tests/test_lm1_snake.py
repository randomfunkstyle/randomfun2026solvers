"""``snake`` — the LM-1 display machine for Semester 4, from ``.asm`` to ``.man``.

Snake is judged on committed frames, so nothing here can be inferred from program
output: the program emits none, and emitting any is an error. Three tiers, cheapest
first:

* the **program**, on the emulator, replayed through ``lm1/display.py``'s panel model
  against every public case's expected frames. This is the cheap proof and the one
  that fails first when the ``.asm`` is wrong;
* the **ROM image** — the bytecode the generated hardware actually fetches, with
  opcodes renumbered from their lane rows and skip counts rescaled — drawing the same
  picture as the assembler's source ring;
* the **grid**, on the reference wasm, with the engine gating the rounds itself. One
  cheap case runs in the fast tier because a swapped ADDR/DATA port is invisible
  everywhere else; the full sweep is marked slow (the longest public case is ~2.3M
  ticks on the engine).

The frame contract is what makes this problem different from ``plotter``: a start,
fruit or tick round commits exactly one frame and a direction round commits **none**.
``randomfun2026solvers.snake_sim`` is the independent oracle for that, and
``tests/test_snake_sim.py`` pins it against the same public data.
"""

from __future__ import annotations

import json
import shutil
import sys
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1 import machine, programs  # noqa: E402
from randomfun2026solvers.lm1.display import frames_from_writes  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator  # noqa: E402

LM_MJS = REPO / "littleman" / "lm.mjs"
FRAME_TOOL = REPO / "littleman" / "tools" / "display-frames.mjs"
GRID = REPO / "tasks" / "solutions" / "snake_cpu.man"

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists() or not FRAME_TOOL.exists(),
    reason="node, littleman/lm.mjs and littleman/tools/display-frames.mjs required",
)

#: The cheapest public case that ends in a loss — 5 rounds, ~73k engine ticks. A
#: mis-bound port or an inverted wall test both show up here.
CHEAP_CASE = "game over at the wall"

MAX_INSTRUCTIONS = 400_000

#: ``snake.json`` raises the step cap to 15M. The dearest public case is ~2.3M, and
#: the constraint box allows ~8 more rounds than that case uses, so the margin is real.
STEP_CAP = 15_000_000


@lru_cache(maxsize=1)
def _machine() -> machine.Machine:
    return machine.build_for("snake")


def _cases() -> list[dict]:
    path = REPO / "tasks" / "problems" / "snake.json"
    return json.loads(path.read_text(encoding="utf-8"))["publicTestData"]


def _expected_frames(case: dict) -> list[list[str]]:
    return [f for r in (case.get("rounds") or [case]) for f in (r.get("frames") or [])]


# ── the program, on the emulator ─────────────────────────────────────────────
def test_the_panel_is_the_problems_own_resolution() -> None:
    assert machine.display_for("snake") == (16, 16)
    assert programs.display_size("snake") == (16, 16)


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_the_program_draws_every_public_frame_on_the_emulator(case: dict) -> None:
    """Every frame of every public case, pixel for pixel, and no program output."""
    (name, rounds) = next(
        (n, r) for n, r in programs.rounds_for_problem("snake") if n == case["name"]
    )
    res = Emulator(programs.load("snake")).run(rounds, max_instructions=MAX_INSTRUCTIONS)
    got = frames_from_writes(res.display_writes, width=16, height=16)
    want = _expected_frames(case)
    assert not res.output, f"{name}: a display problem must emit no program output"
    assert len(got) == len(want), f"{name}: committed {len(got)} frames, expected {len(want)}"
    for i, (frame, expect) in enumerate(zip(got, want, strict=True)):
        assert frame == expect, f"{name}: frame {i} differs\n" + "\n".join(
            f"  row {y}: got {g} want {w}"
            for y, (g, w) in enumerate(zip(frame, expect, strict=True))
            if g != w
        )


def test_a_direction_round_commits_nothing_and_every_other_round_commits_one() -> None:
    """The frame contract, counted straight off the public data.

    Committing on a direction round desynchronises every later frame *and* deadlocks
    the machine, since the engine withholds the next round's input until the current
    round's frames arrive. It is the one mistake this problem punishes twice.
    """
    turns = commits = 0
    for case in _cases():
        for rnd in case["rounds"]:
            frames = len(rnd.get("frames") or [])
            if rnd["in"][0] in ("2", "3", "4", "5") and len(rnd["in"]) == 1:
                turns += 1
                assert frames == 0, f"{case['name']}: a direction round committed a frame"
            else:
                commits += 1
                assert frames == 1, f"{case['name']}: {rnd['in']} committed {frames} frames"
    assert (turns, commits) == (26, 129)


def test_the_body_ring_holds_every_snake_the_constraints_allow() -> None:
    """A growth costs a spawn round *and* a tick round, so 100 rounds cap length at 50.

    The tape is sized from that bound rather than from the public cases, whose longest
    snake is 6 cells: ``TAPE_SIZE`` is the only place the ring's extent is stated,
    because every address in the program is computed at run time.

    Exactly 50, not a round 64: the ring's size is a first-order tick cost. One tape
    read costs ~105 + 8.3N ticks, so every unneeded slot taxes all ~2,100 reads a case.
    """
    prog = programs.load("snake")
    body = prog.equs["BODY"]
    assert machine.TAPE_SIZE["snake"] == body + 50
    assert (
        "At most 100 rounds per test case (including the starting round)."
        in (programs.problem_json("snake")["io"]["constraints"])
    )


# ── the ROM image ────────────────────────────────────────────────────────────
def test_the_rom_image_draws_what_the_source_program_draws() -> None:
    """``rom_words`` renumbers every opcode and rescales every skip count.

    Either can be off by one without the source program noticing, so run both and
    compare the panel.
    """
    source = programs.load("snake")
    image = machine.image_program(source)
    (_name, rounds) = programs.rounds_for_problem("snake")[0]
    runs = [
        Emulator(prog).run(rounds, max_instructions=MAX_INSTRUCTIONS) for prog in (source, image)
    ]
    frames = [frames_from_writes(r.display_writes, width=16, height=16) for r in runs]
    assert frames[0] == frames[1], "the image draws a different picture"
    assert frames[0], "nothing was committed"
    assert not runs[1].output


# ── the generated grid ───────────────────────────────────────────────────────
@node_required
@pytest.mark.slow
def test_the_checked_in_grid_matches_the_generator() -> None:
    expected = "\n".join(_machine().rows) + "\n"
    assert GRID.read_text(encoding="utf-8") == expected, (
        "snake_cpu.man is stale; regenerate with `python -m randomfun2026solvers.lm1.machine "
        f"snake --man {GRID.relative_to(REPO)}`"
    )


@node_required
@pytest.mark.slow
def test_the_generated_machine_declares_the_panel_and_no_output() -> None:
    m = _machine()
    assert m.display == (16, 16)
    assert "O" not in "".join(m.rows), "output on a display problem is an error"

@node_required
@pytest.mark.slow
def test_the_rom_fold_is_the_footprint_optimum() -> None:
    """The default fold aims the ROM at the *CPU's* width and knows nothing about the
    panel, which adds rows and makes height binding — the same trade ``plotter`` makes.

    The two-read jump block changed the default fold's geometry, but the explicit
    9-row fold still wins on the scored maximum dimension.
    """
    default = machine.build(programs.load("snake"), tape_n=80, display=(16, 16))
    tuned = _machine()
    assert tuned.rom_rows == machine.ROM_ROWS["snake"] == 9
    assert tuned.footprint < default.footprint
    assert tuned.height < default.height


@node_required
def test_the_engine_draws_the_losing_frame_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """One public case on the real wasm, with the engine gating the rounds itself.

    The cheap end-to-end proof: ROM -> fetch -> trie -> port lane -> pipe -> panel,
    including the two things only the engine can show — that each ``s`` binds its own
    port, and that the final frame recolours the snake red without moving it.
    """
    from randomfun2026solvers.littleman import Littleman

    case = next(c for c in _cases() if c["name"] == CHEAP_CASE)
    (res,) = Littleman().display_frames(GRID, [case], max_ticks=STEP_CAP)
    want = _expected_frames(case)
    assert res.fatal is None, res.fatal
    assert not res.output
    assert (res.width, res.height) == (16, 16)
    assert res.frames == want
    assert set("".join(want[-1])) == {"0", "9"}, "the losing frame is red, not green"


@pytest.mark.slow  # drives the engine over the whole problem
@node_required
def test_every_public_case_commits_exactly_the_expected_frames() -> None:
    from randomfun2026solvers.littleman import Littleman

    cases = _cases()
    got = Littleman().display_frames(GRID, cases, max_ticks=STEP_CAP)
    assert len(got) == len(cases)
    for case, res in zip(cases, got, strict=True):
        name = case["name"]
        assert res.fatal is None, f"{name}: {res.fatal}"
        assert not res.output, f"{name}: emitted output on a display problem"
        want = _expected_frames(case)
        assert len(res.frames) == len(want), name
        for i, (frame, expect) in enumerate(zip(res.frames, want, strict=True)):
            assert frame == expect, f"{name}: frame {i} differs"


@pytest.mark.slow  # a full scoring run
@node_required
def test_the_score_is_measured_from_the_committed_frames() -> None:
    """``footprint x avg ticks``, with the ticks taken at each case's final commit.

    The program halts only when the snake dies, so three of the five public cases run
    out of input instead — the settle tick would misprice those by an order of
    magnitude, which is what ``assert not approx`` pins.
    """
    from randomfun2026solvers.scoring import score_program

    res = score_program(GRID, "snake")
    assert not res.approx, "display ticks fell back to the settle-tick estimate"
    assert res.avg_ticks is not None and res.score == pytest.approx(res.area2 * res.avg_ticks)
    # The dearest public case is ~2.3M of the 15M cap. Private cases are never served
    # for this problem, but ``gradebook`` reported 0 and served one anyway, so keep the
    # margin: the constraint box allows only ~8 rounds more than the longest case uses.
    assert max(c.ticks for c in res.cases) < STEP_CAP // 4, [c.ticks for c in res.cases]


# ── the coprocessor interface (see solvers/.../lm1/snake_unit.py) ─────────────
def _stub_unit(**_sizes: int) -> object:
    """A block whose only external connection is the incoming command pipe.

    Stands in for the real body-ring unit so the *machine's* geometry can be tested
    without its internals: one room, one ``r``, one ``H``.
    """
    from randomfun2026solvers.lm1.stream import StreamBlock

    w, h = 6, 3
    cells: dict[tuple[int, int], str] = {}
    for x in range(w + 1):
        cells[(x, 0)] = cells[(x, h)] = "-"
    for y in range(1, h):
        cells[(0, y)] = cells[(w, y)] = "|"
    for corner in ((0, 0), (w, 0), (0, h), (w, h)):
        cells[corner] = "+"
    cells[(1, 1)], cells[(2, 1)], cells[(3, 1)] = "@", "r", "H"
    return StreamBlock(
        cells=cells,
        width=w + 1,
        height=h + 1,
        cmd_cell=(2, -1),  # one cell above the north wall, in the `r`'s own column
        resp_cell=(0, 0),
        ring_a=0,
        ring_b=0,
        ring_c=0,
        rows_a=0,
        rows_b=0,
        pipes=0,
        glyphs=[(2, 1, "r", "cmd")],
        codes={},
        regions={"stub": (0, 0, w + 1, h + 1)},
    )


@node_required
@pytest.mark.slow
def test_a_unit_that_answers_nothing_places_where_one_that_answers_cannot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason ``snake``'s coprocessor is write-only, measured rather than argued.

    ``ARCH.md`` §7.1: an ``r`` competes only with *incoming* pipes. A response pipe
    into the CPU is therefore a rival for every ``r`` in it — including the jump slab's
    ROM read, which sits at the bottom of the CPU exactly where a south-wall port
    lands. Sweeping 4,800 (fold, mem_pad, stream_pad) combinations for a snake program
    with an ``RCV`` failed *every* one on that binding; ``matmul``, the only other
    machine with a unit, escapes it only by containing no ``JMPF`` at all.

    So the same program with the reply dropped has to place, and the two book-keeping
    spots that assumed a response pipe (the pipe count and the resp routing) have to
    cope with its absence. Both are checked here rather than in the snake grid, so a
    regression names the cause.
    """
    from randomfun2026solvers.lm1 import stream as streammod
    from randomfun2026solvers.lm1.asm import assemble

    monkeypatch.setattr(streammod, "build_stream", _stub_unit)
    talks = assemble("loop:   IN\n        MULI 8\n        SND\n        RCV\n        JMP loop\n")
    quiet = assemble("loop:   IN\n        MULI 8\n        SND\n        JMP loop\n")

    with pytest.raises(machine.MachineError):
        machine.build(talks, tape_n=2, stream=(0, 0, 0))
    m = machine.build(quiet, tape_n=2, stream=(0, 0, 0))
    assert m.width > 0 and m.height > 0


# ── snake-ring: the same game on the body-ring coprocessor ────────────────────
# The shipped machine. `snake.asm` is kept as the tape-only reference — it is the one
# that proves the CPU can do this unaided, and it is what the coprocessor's numbers are
# measured against.
RING_GRID = REPO / "tasks" / "solutions" / "snake-ring_cpu.man"


@lru_cache(maxsize=1)
def _ring_machine() -> machine.Machine:
    return machine.build_for("snake-ring")


def test_the_ring_program_drives_the_unit_and_never_draws_itself() -> None:
    """The CPU has no display lanes at all: the coprocessor owns the panel.

    So a ``DSP*`` opcode appearing here would mean the panel had been wired to the CPU
    as well — two rooms driving one port, which binds to whichever is nearer and is
    invisible in the ASCII.
    """
    prog = programs.load("snake-ring")
    assert prog.unit == "snake"
    assert not [op for op in prog.ops_used if op.mnemonic.startswith("DSP")]
    assert "SND" in [op.mnemonic for op in prog.ops_used]
    # No RCV: an incoming pipe would compete with every `r` in the CPU (see
    # test_a_unit_that_answers_nothing_places_where_one_that_answers_cannot).
    assert "RCV" not in [op.mnemonic for op in prog.ops_used]


def test_sixteen_opcodes_exactly_which_is_a_depth_four_trie() -> None:
    """The 17th opcode costs a whole trie level *plus* its lane rows.

    Measured: dropping ``NEG`` (built as ``LDI 0`` / ``SUBI n``) and the final ``HALT``
    (a blocking ``IN`` instead — the case has ended, so no input ever comes) took the
    machine from 158x167 to 121x136 **and** the average from 151,544 ticks to 122,264,
    because every instruction's trie walk got shorter too.
    """
    assert len(programs.load("snake-ring").ops_used) == 16


def test_the_command_codes_are_the_hardwares_own() -> None:
    """The unit reads its codes off its trie's geometry, so the program must not guess.

    ``STEP`` is 0 because it is the easternmost leaf — it is the only arm that outgrows
    its columns. Move a leaf and these numbers move with it.
    """
    from randomfun2026solvers.lm1 import snake_unit
    from randomfun2026solvers.lm1.store import SnakeUnit

    codes = snake_unit.arm_codes()
    assert codes == SnakeUnit.CODES
    equs = programs.load("snake-ring").equs
    for arm, code in codes.items():
        assert equs[f"C_{arm}"] == code, f"the program's C_{arm} disagrees with the trie"


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_the_ring_program_draws_every_public_frame_on_the_emulator(case: dict) -> None:
    """Same frames as the tape version, but every pixel comes out of the unit's model.

    ``store.SnakeUnit`` writes through the emulator's ``display_writes`` hook, so this
    is the same comparison as for ``snake.asm`` even though this CPU never draws.
    """
    (name, rounds) = next(
        (n, r) for n, r in programs.rounds_for_problem("snake") if n == case["name"]
    )
    res = Emulator(programs.load("snake-ring")).run(rounds, max_instructions=MAX_INSTRUCTIONS)
    got = frames_from_writes(res.display_writes, width=16, height=16)
    want = _expected_frames(case)
    assert not res.output, f"{name}: a display problem must emit no program output"
    assert got == want, f"{name}: frames differ"


@node_required
@pytest.mark.slow
def test_the_checked_in_ring_grid_matches_the_generator() -> None:
    expected = "\n".join(_ring_machine().rows) + "\n"
    assert RING_GRID.read_text(encoding="utf-8") == expected, (
        "snake-ring_cpu.man is stale; regenerate with `python -m randomfun2026solvers.lm1.machine "
        f"snake-ring --man {RING_GRID.relative_to(REPO)}`"
    )


@node_required
@pytest.mark.slow
def test_the_ring_machine_is_wired_the_way_the_coprocessor_needs() -> None:
    m = _ring_machine()
    assert "O" not in "".join(m.rows)
    # The panel belongs to the block, so the CPU declares no display of its own.
    assert m.display is None
    named = {r.name for r in m.debug_map().regions}
    for arm in ("GROW", "STEP", "FRUIT", "RED"):
        assert f"stream:unit:{arm}" in named, f"the {arm} arm is unnamed in the overlay"
    assert {"stream:ring", "stream:relay", "stream:panel"} <= named

@node_required
def test_the_engine_plays_the_losing_case_on_the_ring_machine() -> None:
    """The cheap end-to-end proof, on the real wasm: CPU -> cmd pipe -> unit -> panel."""
    from randomfun2026solvers.littleman import Littleman

    case = next(c for c in _cases() if c["name"] == CHEAP_CASE)
    (res,) = Littleman().display_frames(RING_GRID, [case], max_ticks=STEP_CAP)
    want = _expected_frames(case)
    assert res.fatal is None, res.fatal
    assert not res.output
    assert res.frames == want
    assert set("".join(want[-1])) == {"0", "9"}, "the losing frame is red, not green"


@pytest.mark.slow  # drives the engine over the whole problem
@node_required
def test_every_public_case_commits_the_expected_frames_on_the_ring_machine() -> None:
    from randomfun2026solvers.littleman import Littleman

    cases = _cases()
    got = Littleman().display_frames(RING_GRID, cases, max_ticks=STEP_CAP)
    assert len(got) == len(cases)
    for case, res in zip(cases, got, strict=True):
        assert res.fatal is None, f"{case['name']}: {res.fatal}"
        assert not res.output, f"{case['name']}: emitted output on a display problem"
        assert res.frames == _expected_frames(case), f"{case['name']}: frames differ"


@pytest.mark.slow  # a full scoring run
@node_required
def test_the_coprocessor_grid_is_measurable_and_fits_the_cap() -> None:
    from randomfun2026solvers.scoring import score_program

    res = score_program(RING_GRID, "snake")
    assert not res.approx, "display ticks fell back to the settle-tick estimate"
    assert res.avg_ticks is not None and res.score is not None
    # ~378k of the 15M cap on the dearest public case: a 39x margin.
    assert max(c.ticks for c in res.cases) < STEP_CAP // 8, [c.ticks for c in res.cases]
