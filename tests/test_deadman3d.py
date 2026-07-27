"""The ``deadman-3d`` golden model: the 32x32 E1M1 map data, the packed heading
table, the Q10 raycaster, the HUD, the demo walk, the cases-file shape — and
the generated asm: generator/registry pins plus emulator runs pixel-equal to
the golden model.

Fast tier: the pure-integer model tests (milliseconds) plus a short emulator
run (~250k instructions, <1s). Slow tier: the full demo walk and the seeded
fuzz walk on the emulator, and the machine synthesis (grid-store STORE tier).
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers.lm1 import machine, programs  # noqa: E402
from randomfun2026solvers.lm1.display import frames_from_writes  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402

slow = pytest.mark.slow


# ── the map ──────────────────────────────────────────────────────────────────
def test_map_words_fit_a_signed_word_and_round_trip() -> None:
    words = d3.map_words()
    assert len(words) == 2 * d3.MAP_SIZE == 64
    assert all(0 < w < 2 ** 63 for w in words)
    # Rebuild MAP_STR from the packed words alone: two half-columns per x,
    # nibble y mod 16 of word 2x + (y / 16).
    printed = []
    for p in range(d3.MAP_SIZE):
        y = d3.MAP_SIZE - 1 - p
        row = ""
        for x in range(d3.MAP_SIZE):
            t = (words[2 * x + y // 16] // 16 ** (y % 16)) % 16
            row += "." if t == 0 else "%x" % t
        printed.append(row)
    assert "\n".join(printed) + "\n" == d3.MAP_STR


def test_wall_types_stay_in_1_to_7() -> None:
    for x in range(d3.MAP_SIZE):
        for y in range(d3.MAP_SIZE):
            t = d3.map_cell(x, y)
            assert 0 <= t <= 7
    # The border is solid (rays and the walk can never escape the grid).
    for i in range(d3.MAP_SIZE):
        assert d3.map_cell(i, 0) > 0 and d3.map_cell(0, i) > 0
        assert d3.map_cell(i, 31) > 0 and d3.map_cell(31, i) > 0


def test_the_map_is_e1m1s_landmarks() -> None:
    """The homage's fixed points: spawn cell, exit door, armor pedestal."""
    # Spawn: the vestibule between the two entry alcoves, facing north.
    assert (d3.SPAWN.posX, d3.SPAWN.posY, d3.SPAWN.heading) == (22016, 3584, 4)
    assert d3.map_cell(21, 3) == 0
    # The RED exit door, set in the west wall of the zigzag nukage room.
    assert all(d3.map_cell(1, y) == 1 for y in (9, 10, 11))
    # The blue armor pedestal in the courtyard, behind the north window slits.
    assert d3.map_cell(23, 17) == 4
    assert all(d3.map_cell(x, 15) == 0 for x in (22, 23, 24))


# ── the heading table ────────────────────────────────────────────────────────
def test_heading_table_packing_norm_and_orthogonality() -> None:
    words = d3.heading_table()
    assert len(words) == d3.HEADINGS
    for h in range(d3.HEADINGS):
        assert 0 < words[h] < 2 ** 48  # positive, fits an int64 with headroom
        dx, dy, px, py = d3.unpack_heading(words[h])
        # Unpacking recovers exactly the rounded trig the table was built from.
        th = math.radians(h * 22.5)
        assert (dx, dy) == (round(1024 * math.cos(th)), round(1024 * math.sin(th)))
        assert (px, py) == (round(675.84 * math.sin(th)), round(-675.84 * math.cos(th)))
        assert 1023 <= math.hypot(dx, dy) <= 1025
        assert abs(dx * px + dy * py) <= 512  # measured max is 406


# ── frames ───────────────────────────────────────────────────────────────────
def test_frame_shape_is_48_rows_of_64_hex_chars() -> None:
    frame = d3.render(d3.SPAWN)
    assert len(frame) == d3.HEIGHT == 48
    hexdigits = set("0123456789abcdef")
    for row in frame:
        assert len(row) == d3.WIDTH == 64
        assert set(row) <= hexdigits


def test_hud_is_constant_and_fills_rows_40_to_47() -> None:
    hud = d3.hud_rows()
    assert len(hud) == 8
    other = d3.step(d3.step(d3.SPAWN, 0), 2)
    assert d3.render(d3.SPAWN)[d3.H3D:] == hud
    assert d3.render(other)[d3.H3D:] == hud
    # The RLE is a faithful re-encoding of the same 8 rows.
    replay = "".join("%x" % c * n for c, n in d3.hud_runs())
    assert replay == "".join(hud)
    assert sum(n for _, n in d3.hud_runs()) == 8 * d3.WIDTH


def test_one_frame_per_command() -> None:
    frames = d3.frames_for_commands(d3.WALK)
    assert len(frames) == len(d3.WALK) == 35


def test_walk_stays_inside_open_cells() -> None:
    state = d3.SPAWN
    for cmd in d3.WALK:
        state = d3.step(state, cmd)
        cx, cy = d3.div(state.posX, d3.UNITS), d3.div(state.posY, d3.UNITS)
        assert d3.map_cell(cx, cy) == 0, f"walk entered wall cell {(cx, cy)}"
    # And the finale is where the demo promises: on the zigzag walkway, facing
    # the red exit door in the west wall.
    assert state.heading == 8
    cx, cy = d3.div(state.posX, d3.UNITS), d3.div(state.posY, d3.UNITS)
    assert (cx, cy) == (5, 10)
    assert d3.map_cell(cx - 4, cy) == 1


# ── pinned frames (hand-checked against the scratchpad PNGs) ─────────────────
#: The spawn view (WALK[0] is a no-op): up the vestibule, sunlit white walls
#: flanking, the green corridor-jamb sliver far left, and the octagon's north
#: wall ahead with the three window slits — the blue armor pedestal '4' in
#: them *right* of centre (it is east of the spawn column), which is the
#: no-mirror evidence (risk R10).
SPAWN_FRAME = [
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "fff00000000000000000000000000000000000000000000000000000000000ff",
    "fff00000000000000000000000000000000000000000000000000000000000ff",
    "fff00000000000000000000000000000000000000000000000000000000000ff",
    "fff00000000000000000000000000000000000000000000000000000000000ff",
    "fff00000000000000000000000000000000000000000000000000000000000ff",
    "fff00000000000000000000000000000000000000000000000000000000000ff",
    "fffaff00000000000000000000000000000000000000000000000000000fffff",
    "fffafff777777777ff7777777777777777777744477777fff777777777ffffff",
    "fffafff777777777ff7777777777777777777744477777fff777777777ffffff",
    "fffafff777777777ff7777777777777777777744477777fff777777777ffffff",
    "fffaff88888888888888888888888888888888888888888888888888888fffff",
    "fff88888888888888888888888888888888888888888888888888888888888ff",
    "fff88888888888888888888888888888888888888888888888888888888888ff",
    "fff88888888888888888888888888888888888888888888888888888888888ff",
    "fff88888888888888888888888888888888888888888888888888888888888ff",
    "fff88888888888888888888888888888888888888888888888888888888888ff",
    "fff88888888888888888888888888888888888888888888888888888888888ff",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "7777777777777777777777777777777777777777777777777777777777777777",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888888888888888888888888888888888888888888888888888888888888888",
]

#: The doorway half-look (WALK[28], heading 7 at cell (8, 10)): the sunlit red
#: door '9' left, and the zigzag walkway's sawtooth right — the near cyan spur
#: dark '6' y-face with its bright 'e' x-face receding up the nukage room.
ZIGZAG_LOOK_FRAME = [
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "00000000000000000000000000000000000000066666eeeeeeeeeeeeeeee0000",
    "999999999999999999999999ff000000666666666666eeeeeeeeeeeeeeee0000",
    "999999999999999999999999fffffff6666666666666eeeeeeeeeeeeeeeeffff",
    "999999999999999999999999fffffff6666666666666eeeeeeeeeeeeeeeeffff",
    "999999999999999999999999fffffff6666666666666eeeeeeeeeeeeeeeeffff",
    "999999999999999999999999fffffff6666666666666eeeeeeeeeeeeeeeeffff",
    "999999999999999999999999fffffff6666666666666eeeeeeeeeeeeeeeeffff",
    "999999999999999999999999ff888888666666666666eeeeeeeeeeeeeeee8888",
    "88888888888888888888888888888888888888866666eeeeeeeeeeeeeeee8888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "7777777777777777777777777777777777777777777777777777777777777777",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888888888888888888888888888888888888888888888888888888888888888",
]


def test_pinned_spawn_frame() -> None:
    assert d3.frames_for_commands(d3.WALK)[0] == SPAWN_FRAME


def test_pinned_zigzag_look_frame() -> None:
    assert d3.WALK[28] == 3, "the pin is the frame right after the doorway half-look"
    assert d3.frames_for_commands(d3.WALK)[28] == ZIGZAG_LOOK_FRAME


# ── boot data and the cases file ─────────────────────────────────────────────
def test_preamble_is_the_documented_tape_order() -> None:
    pre = d3.preamble_words()
    assert len(pre) == 103
    assert pre[0:64] == d3.map_words()                 # MAPB, slots 1..64
    assert pre[64:80] == [16 ** k for k in range(16)]  # POWB, slots 65..80
    assert pre[80:96] == d3.heading_table()            # HDGB, slots 81..96
    # Spawn scalars, slots 97..103: cell (21, 3), heading 4 = north.
    assert pre[96:] == [22016, 3584, 4, 0, 1024, 676, 0]
    assert all(w >= 0 for w in pre)  # the E1M1 spawn happens to need no negatives
    assert d3.input_words(d3.WALK) == pre + d3.WALK


def test_cases_json_shape_one_round_per_command() -> None:
    cases = d3.cases_json(d3.WALK)
    (case,) = cases["publicTestData"]
    assert case["name"] == "deadman-3d"
    rounds = case["rounds"]
    assert len(rounds) == len(d3.WALK)
    frames = d3.frames_for_commands(d3.WALK)
    preamble = [str(w) for w in d3.preamble_words()]
    for k, rnd in enumerate(rounds):
        want_in = (preamble if k == 0 else []) + [str(d3.WALK[k])]
        assert rnd["in"] == want_in
        assert rnd["out"] == []
        assert rnd["frames"] == [frames[k]]
        assert all(isinstance(w, str) for w in rnd["in"])


# ── the asm program: pins, registry, and emulator pixel-equality ─────────────
def _emulator_frames(cmds: list[int], *, max_instructions: int = 20_000_000) -> list[list[str]]:
    """Run the checked-in program on the emulator and return its committed frames.

    One Round carrying everything: the emulator releases all of a display
    program's input up front (round-gating is only real on the native engine),
    so the split into rounds would change nothing here.
    """
    prog = programs.load("deadman-3d")
    res = Emulator(prog).run(
        [Round(input=tuple(d3.input_words(cmds)))], max_instructions=max_instructions
    )
    # The demo ends on a blocking IN (no HALT): input-exhausted is the legal end.
    assert res.reason == "input-exhausted", res.reason
    assert res.output == (), "a display program must emit no program output"
    return frames_from_writes(res.display_writes, width=d3.WIDTH, height=d3.HEIGHT)


def test_checked_in_asm_matches_the_generator() -> None:
    path = programs.PROGRAM_DIR / "deadman-3d.asm"
    assert path.read_text(encoding="utf-8") == d3.deadman3d_source(), (
        "deadman-3d.asm is stale; regenerate with "
        '(PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())'
    )


def test_tape_slots_are_the_documented_map() -> None:
    slots = d3.tape_slots()
    assert (slots["MAPB"], slots["POWB"], slots["HDGB"]) == (1, 65, 81)
    assert slots["POSX"] == len(d3.preamble_words()) - 6 == 97
    # The scalars run consecutively after the boot data, PTR last.
    scalars = sorted(v for k, v in slots.items() if v >= slots["CMD"])
    assert scalars == list(range(104, 131))
    assert slots["PTR"] == max(slots.values()) == 130


def test_registry_pins() -> None:
    """The demo borrows plotter's slug for registration; everything else is its own."""
    assert programs.problem_of("deadman-3d") == "plotter"
    assert "deadman-3d" in programs.DEMOS
    # The 64x48 panel comes from DISPLAY_OVERRIDE, not plotter's problem JSON …
    assert machine.display_for("deadman-3d") == (d3.WIDTH, d3.HEIGHT) == (64, 48)
    assert machine.display_for("plotter") == (32, 24)
    # … and the tape is highest .equ address + 1 (an exactly-sized tape stalls).
    assert machine.TAPE_SIZE["deadman-3d"] == max(d3.tape_slots().values()) + 1 == 131
    # 131 slots is past the rotating tape's practical cap, so the STORE rides
    # the grid_block man-memory (~31 ticks an access, whatever n is) …
    assert machine.STORE_TIER["deadman-3d"] == "grid"
    # … at the recorded pad, which pins the checked-in grid (measured: the
    # first pad where every pipe binds under the 64x48 panel).
    assert machine.MEM_PAD["deadman-3d"] == 36


def test_short_emulator_run_is_pixel_equal_to_golden() -> None:
    """Spawn view, a forward step, a turn, another step: ~250k instructions.

    Covers the no-op, move and turn arms plus the render pipeline end to end;
    the full walk and the fuzz walk are the slow tier.
    """
    cmds = [4, 0, 2, 0]
    assert _emulator_frames(cmds, max_instructions=5_000_000) == d3.frames_for_commands(cmds)


@slow
def test_the_full_demo_walk_is_pixel_equal_to_golden() -> None:
    """All 35 WALK commands (~1.6M instructions)."""
    assert _emulator_frames(d3.WALK) == d3.frames_for_commands(d3.WALK)


@slow
def test_a_seeded_fuzz_walk_is_pixel_equal_to_golden() -> None:
    """40 seeded commands: every arm, wall bumps, all the diagonal headings."""
    rng = random.Random(2026)
    cmds = [rng.randrange(6) for _ in range(40)]
    assert _emulator_frames(cmds) == d3.frames_for_commands(cmds)


@slow
def test_the_machine_synthesizes_with_the_grid_store() -> None:
    """`build_for` binds every pipe: 64x48 panel + 131-slot grid_block STORE."""
    m = machine.build_for("deadman-3d")
    assert m.mem_pad == machine.MEM_PAD["deadman-3d"]
    assert m.display == (64, 48)


# ── the checked-in machine, judged for real ──────────────────────────────────

MAN = REPO / "littleman" / "examples" / "deadman-3d.man"
CASES = REPO / "littleman" / "examples" / "deadman-3d.cases.json"


def test_checked_in_cases_match_the_generator() -> None:
    import json

    assert json.loads(CASES.read_text()) == d3.cases_json(d3.WALK)


@slow
def test_checked_in_man_matches_the_machine_builder() -> None:
    rows = MAN.read_text().rstrip("\n").split("\n")
    assert machine.build_for("deadman-3d").rows == rows


@slow
def test_the_first_round_judges_clean_on_the_native_engine() -> None:
    """Round-gated frame judging on the independent native validator.

    This is the one place gating is enforced for real: the Python emulator
    releases every display-problem round up front. The reference wasm session
    API cannot host this machine at all — it retains memory per tick and the
    Go heap dies at 4 GB around ~10M ticks, while one 64x48 frame costs tens
    of millions. FastLittleman streams the committed frames against the
    expected rounds natively, which is exactly the judge's compare.
    """
    from randomfun2026solvers.fast_littleman import FastLittleman

    case = d3.cases_json(d3.WALK[:1])["publicTestData"][0]
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    frames = [r["frames"] for r in case["rounds"]]
    res = FastLittleman(MAN).run(inp, frames=frames, max_ticks=200_000_000)
    assert res.fatal is None, res.fatal
    assert res.output == []
    assert res.passed is True
