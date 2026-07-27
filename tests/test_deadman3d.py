"""The ``deadman-3d`` golden model (M0): map data, heading tables, the Q10
raycaster, the HUD, the demo walk, the cases-file shape — and the asm (M1+M2):
generator/registry pins plus emulator runs pixel-equal to the golden model.

Fast tier: the pure-integer model tests (milliseconds) plus a short emulator
run (~130k instructions, ~0.3s). Slow tier: the full demo walk and the seeded
fuzz walk on the emulator, and the machine synthesis. The wasm engine run
(round-gating for real) is milestone M3.
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
def test_map_row_words_fit_a_signed_word_and_round_trip() -> None:
    words = d3.map_row_words()
    assert len(words) == d3.MAP_SIZE
    assert all(0 < w < 2 ** 63 for w in words)
    # Rebuild MAP_STR from the packed words alone: nibble y of word x.
    printed = []
    for p in range(d3.MAP_SIZE):
        y = d3.MAP_SIZE - 1 - p
        row = ""
        for x in range(d3.MAP_SIZE):
            t = (words[x] // 16 ** y) % 16
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
        assert d3.map_cell(i, 15) > 0 and d3.map_cell(15, i) > 0


# ── heading tables ───────────────────────────────────────────────────────────
def test_heading_tables_norm_orthogonality_and_packing() -> None:
    dirs, planes = d3.dir_table(), d3.plane_table()
    assert len(dirs) == len(planes) == d3.HEADINGS
    for h in range(d3.HEADINGS):
        assert dirs[h] > 0 and planes[h] > 0
        dx, dy = d3.unpack_vec(dirs[h])
        px, py = d3.unpack_vec(planes[h])
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
    assert len(frames) == len(d3.WALK)


def test_walk_stays_inside_open_cells() -> None:
    state = d3.SPAWN
    for cmd in d3.WALK:
        state = d3.step(state, cmd)
        cx, cy = d3.div(state.posX, d3.UNITS), d3.div(state.posY, d3.UNITS)
        assert d3.map_cell(cx, cy) == 0, f"walk entered wall cell {(cx, cy)}"
    # And the finale is where the demo promises: facing the red door up close.
    assert state.heading == 0
    assert d3.map_cell(d3.div(state.posX, d3.UNITS) + 2, d3.div(state.posY, d3.UNITS)) == 1


# ── pinned frames (hand-checked against the scratchpad PNGs) ─────────────────
#: The spawn view (WALK[0] is a no-op): dark-cyan pillar (2,4) on the LEFT —
#: north of an east-facing player, i.e. the player's left, which is the
#: no-mirror evidence (risk R10) — bright-cyan pillar (4,2) on the right, the
#: sunlit-blue door trim mid-left, and the red door ('9', sunlit x-face) dead
#: centre down the corridor.
SPAWN_FRAME = [
    "6666666600000000000000000000000000000000000000000000000000000000",
    "6666666660000000000000000000000000000000000000000000000000000000",
    "6666666666600000000000000000000000000000000000000000000000000000",
    "6666666666660000000000000000000000000000000000000000000000000000",
    "6666666666666000000000000000000000000000000000000000000000000000",
    "6666666666666600000000000000000000000000000000000000000000000000",
    "6666666666666666000000000000000000000000000000000000000000000000",
    "6666666666666666000000000000000000000000000000000000000000000000",
    "6666666666666666000000000000000000000000000000000000000000000000",
    "6666666666666666000000000000000000000000000000000000000000000000",
    "6666666666666666000000000000000000000000000000000000000000000000",
    "6666666666666666000000000000000000000000000000000000000000000000",
    "666666666666666600000000000000000000000000eeeeeeeeeeeeeeeeeeee00",
    "666666666666666600000000000000000000000006eeeeeeeeeeeeeeeeeeee00",
    "666666666666666600000000000000000000000066eeeeeeeeeeeeeeeeeeee00",
    "666666666666666600000000000000000000000666eeeeeeeeeeeeeeeeeeee07",
    "6666666666666666ccccccccccc40000000004c666eeeeeeeeeeeeeeeeeeee77",
    "6666666666666666ccccccccccc43000000074c666eeeeeeeeeeeeeeeeeeee77",
    "6666666666666666ccccccccccc43b00000774c666eeeeeeeeeeeeeeeeeeee77",
    "6666666666666666ccccccccccc43bf9997774c666eeeeeeeeeeeeeeeeeeee77",
    "6666666666666666ccccccccccc43bf9997774c666eeeeeeeeeeeeeeeeeeee77",
    "6666666666666666ccccccccccc43bf9997774c666eeeeeeeeeeeeeeeeeeee77",
    "6666666666666666ccccccccccc43b88888774c666eeeeeeeeeeeeeeeeeeee77",
    "6666666666666666ccccccccccc43888888874c666eeeeeeeeeeeeeeeeeeee77",
    "6666666666666666ccccccccccc48888888884c666eeeeeeeeeeeeeeeeeeee77",
    "666666666666666688888888888888888888888666eeeeeeeeeeeeeeeeeeee87",
    "666666666666666688888888888888888888888866eeeeeeeeeeeeeeeeeeee88",
    "666666666666666688888888888888888888888886eeeeeeeeeeeeeeeeeeee88",
    "666666666666666688888888888888888888888888eeeeeeeeeeeeeeeeeeee88",
    "6666666666666666888888888888888888888888888888888888888888888888",
    "6666666666666666888888888888888888888888888888888888888888888888",
    "6666666666666666888888888888888888888888888888888888888888888888",
    "6666666666666666888888888888888888888888888888888888888888888888",
    "6666666666666666888888888888888888888888888888888888888888888888",
    "6666666666666666888888888888888888888888888888888888888888888888",
    "6666666666666688888888888888888888888888888888888888888888888888",
    "6666666666666888888888888888888888888888888888888888888888888888",
    "6666666666668888888888888888888888888888888888888888888888888888",
    "6666666666688888888888888888888888888888888888888888888888888888",
    "6666666668888888888888888888888888888888888888888888888888888888",
    "7777777777777777777777777777777777777777777777777777777777777777",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888999999999888888888888888bbbbbbbb88888888888888ccccccccc88888",
    "8888888888888888888888888888888888888888888888888888888888888888",
]

#: The view after the first turn left (WALK[4], heading 1 at cell (3,3)): the
#: green room wall sweeping in from the left (dark '2' y-faces, bright 'a'
#: x-face), the sunlit blue door trim 'c' centre, and the corridor with the
#: red door receding to the right.
POST_TURN_FRAME = [
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
    "222222200000000000000000000000000000000000000000000000000000004c",
    "222222222222200000000000000000cccccccccccccc4000000000000000444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4400000000000007444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4430000000000077444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4433000000000777444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4433b30000077777444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4433b39999977777444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4433b39999977777444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4433b39999977777444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4433b38888877777444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4433888888888777444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4438888888888877444c",
    "22222222222222222aaaaaaaaaaaaccccccccccccccc4488888888888887444c",
    "222222222222288888888888888888cccccccccccccc4888888888888888444c",
    "222222288888888888888888888888888888888888888888888888888888884c",
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


def test_pinned_post_turn_frame() -> None:
    assert d3.WALK[4] == 2, "the pin is the frame right after the first turn left"
    assert d3.frames_for_commands(d3.WALK)[4] == POST_TURN_FRAME


# ── boot data and the cases file ─────────────────────────────────────────────
def test_preamble_is_the_documented_tape_order() -> None:
    pre = d3.preamble_words()
    assert len(pre) == 71
    assert pre[0:16] == d3.map_row_words()          # MAPB, slots 1..16
    assert pre[16:32] == [16 ** y for y in range(16)]  # POWB, slots 17..32
    assert pre[32:48] == d3.dir_table()             # DIRB, slots 33..48
    assert pre[48:64] == d3.plane_table()           # PLNB, slots 49..64
    # Spawn scalars, slots 65..71 (negatives allowed as input words).
    assert pre[64:] == [1536, 3584, 0, 1024, 0, 0, -676]
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


# ── the asm program (M1+M2): pins, registry, and emulator pixel-equality ─────
def _emulator_frames(cmds: list[int], *, max_instructions: int = 5_000_000) -> list[list[str]]:
    """Run the checked-in program on the emulator and return its committed frames.

    One Round carrying everything: the emulator releases all of a display
    program's input up front (round-gating is only real on the wasm engine —
    milestone M3), so the split into rounds would change nothing here.
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
    assert (slots["MAPB"], slots["POWB"], slots["DIRB"], slots["PLNB"]) == (1, 17, 33, 49)
    assert slots["POSX"] == len(d3.preamble_words()) - 6 == 65
    # The scalars run consecutively after the boot data, PTR last.
    scalars = sorted(v for k, v in slots.items() if v >= slots["CMD"])
    assert scalars == list(range(72, 98))
    assert slots["PTR"] == max(slots.values()) == 97


def test_registry_pins() -> None:
    """The demo borrows plotter's slug for registration; everything else is its own."""
    assert programs.problem_of("deadman-3d") == "plotter"
    assert "deadman-3d" in programs.DEMOS
    # The 64x48 panel comes from DISPLAY_OVERRIDE, not plotter's problem JSON …
    assert machine.display_for("deadman-3d") == (d3.WIDTH, d3.HEIGHT) == (64, 48)
    assert machine.display_for("plotter") == (32, 24)
    # … and the tape is highest .equ address + 1 (an exactly-sized tape stalls).
    assert machine.TAPE_SIZE["deadman-3d"] == max(d3.tape_slots().values()) + 1
    # The default pad search stops at 39 and this panel needs 45 (measured).
    assert machine.MEM_PAD["deadman-3d"] == 45


def test_short_emulator_run_is_pixel_equal_to_golden() -> None:
    """Spawn view, a forward step, a turn, another step: ~130k instructions.

    Covers the no-op, move and turn arms plus the render pipeline end to end;
    the full walk and the fuzz walk are the slow tier.
    """
    cmds = [4, 0, 2, 0]
    assert _emulator_frames(cmds) == d3.frames_for_commands(cmds)


@slow
def test_the_full_demo_walk_is_pixel_equal_to_golden() -> None:
    """All 28 WALK commands (~870k instructions, ~32k per frame)."""
    assert _emulator_frames(d3.WALK) == d3.frames_for_commands(d3.WALK)


@slow
def test_a_seeded_fuzz_walk_is_pixel_equal_to_golden() -> None:
    """40 seeded commands: every arm, wall bumps, all the diagonal headings."""
    rng = random.Random(2026)
    cmds = [rng.randrange(6) for _ in range(40)]
    assert _emulator_frames(cmds) == d3.frames_for_commands(cmds)


@slow
def test_the_machine_synthesizes_with_the_64x48_panel() -> None:
    """`build_for` binds every pipe at the registered pad (the .man is M3)."""
    m = machine.build_for("deadman-3d")
    assert m.mem_pad == machine.MEM_PAD["deadman-3d"]
    assert m.display == (64, 48)


# ── M3: the checked-in machine, judged for real ──────────────────────────────

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
    Go heap dies at 4 GB around ~10M ticks, while one 64x48 frame costs ~35M
    (P=1158 words of backward-jump slab per lap). FastLittleman streams the
    committed frames against the expected rounds natively, which is exactly
    the judge's compare.
    """
    from randomfun2026solvers.fast_littleman import FastLittleman

    case = d3.cases_json(d3.WALK[:1])["publicTestData"][0]
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    frames = [r["frames"] for r in case["rounds"]]
    res = FastLittleman(MAN).run(inp, frames=frames, max_ticks=100_000_000)
    assert res.fatal is None, res.fatal
    assert res.output == []
    assert res.passed is True
