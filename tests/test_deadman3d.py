"""The ``deadman-3d`` golden model: the 64x64 E1M1 map data, the packed heading
table, the Q10 raycaster, the HUD, the demo walk, the cases-file shape — and
the generated asm: generator/registry pins plus emulator runs pixel-equal to
the golden model.

Fast tier: the pure-integer model tests (milliseconds) plus a short emulator
run (~250k instructions, <1s). Slow tier: the full demo walk and the seeded
fuzz walk on the emulator, and the machine synthesis (men-v3 STORE tier).
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
    assert len(words) == 4 * d3.MAP_SIZE == 256
    assert all(0 <= w < 2 ** 63 for w in words)
    # Rebuild MAP_STR from the packed words alone: four quarter-columns per x,
    # nibble y mod 16 of word 4x + (y / 16).
    printed = []
    for p in range(d3.MAP_SIZE):
        y = d3.MAP_SIZE - 1 - p
        row = ""
        for x in range(d3.MAP_SIZE):
            t = (words[4 * x + y // 16] // 16 ** (y % 16)) % 16
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
        assert d3.map_cell(i, 63) > 0 and d3.map_cell(63, i) > 0


def test_the_map_is_e1m1s_landmarks() -> None:
    """The homage's fixed points: spawn, exit door, pedestal, platforms."""
    # Spawn: the corridor between the two entry alcoves, facing north.
    assert (d3.SPAWN.posX, d3.SPAWN.posY, d3.SPAWN.heading) == (43520, 4608, 4)
    assert d3.map_cell(42, 4) == 0
    # The RED exit door in the exit room's west wall, gray posts around it.
    assert all(d3.map_cell(3, y) == 1 for y in (9, 10, 11))
    assert d3.map_cell(3, 12) == 7 and d3.map_cell(3, 8) == 7
    # The blue armor pedestal in the courtyard, behind the north window slits.
    assert d3.map_cell(46, 34) == d3.map_cell(47, 35) == 4
    assert all(d3.map_cell(x, 31) == 0 for x in (44, 47, 50))
    # The hangar's two raised side platforms flanking its east end.
    assert d3.map_cell(53, 14) == d3.map_cell(53, 26) == 3


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


def test_hud_background_and_live_bars() -> None:
    """The HUD strip: static background (bezel + blue armor block) plus the
    proportional bars — red health 1px/4, yellow ammo 1px/2, from column 4."""
    bg = d3.hud_bg_rows()
    assert len(bg) == 8
    # The background RLE is a faithful re-encoding — it is the asm's constant
    # table (one pre-encoded RUN word per run behind one CURS).
    replay = "".join("%x" % c * n for c, n in d3.hud_bg_runs())
    assert replay == "".join(bg)
    assert sum(n for _, n in d3.hud_bg_runs()) == 8 * d3.WIDTH
    assert len(d3.hud_bg_runs()) == 14
    # Full bars at the spawn state: 25px of each, over the background.
    full = d3.hud_rows(100, 50)
    assert full[1][4:29] == "9" * 25 and full[4][4:29] == "b" * 25
    assert full[1][50:59] == "c" * 9  # the blue armor block, static
    # A drained clip paints NO ammo bar: the background shows through.
    empty = d3.hud_rows(100, 0)
    assert empty[4][4:29] == "8" * 25
    assert empty[1][4:29] == "9" * 25
    # The render wires them through: two shots in, the bar is one px shorter.
    assert d3.render(d3.SPAWN)[d3.H3D:] == d3.hud_rows(100, 50)
    assert d3.render(d3.SPAWN, ammo=48)[d3.H3D:] == d3.hud_rows(100, 48)
    assert d3.hud_rows(100, 48)[4][4:28] == "b" * 24


def test_walk_is_its_chords_and_keys_encodes_the_mux() -> None:
    """The walk is spelled as chords; keys() encodes held-key bitmasks."""
    assert d3.WALK == [d3.keys(ch) for ch in d3.WALK_CHORDS]
    assert len(d3.WALK) == 50
    assert (d3.KEY_FWD, d3.KEY_BACK, d3.KEY_LEFT, d3.KEY_RIGHT, d3.KEY_FIRE) == (
        1, 2, 4, 8, 16)
    assert d3.keys("wa ") == 21 and d3.keys(".") == 0 and d3.keys("ww") == 1
    # The FIRE beats: at the pedestal, chorded with the last step, at the door.
    assert [i for i, c in enumerate(d3.WALK) if d3.fire_bit(c)] == [10, 48, 49]
    assert d3.WALK[48] == d3.keys("w ") == 17  # fire while moving: the MUX


def test_chord_semantics_turn_then_move_and_cancelling() -> None:
    """A held chord turns first, then moves along the NEW heading; opposing
    keys cancel — mirrored exactly by the asm's decode (the fuzz walk's net)."""
    assert d3.step(d3.SPAWN, d3.keys("wa")) == d3.step(
        d3.step(d3.SPAWN, d3.keys("a")), d3.keys("w"))
    assert d3.step(d3.SPAWN, d3.keys("ws")) == d3.SPAWN  # W+S cancel
    assert d3.step(d3.SPAWN, d3.keys("ad")) == d3.SPAWN  # A+D cancel
    assert d3.step(d3.SPAWN, 32 + 64) == d3.SPAWN        # high bits ignored
    assert d3.fire_bit(d3.keys(" ")) and not d3.fire_bit(d3.keys("w"))


def test_one_frame_per_command() -> None:
    frames = d3.frames_for_commands(d3.WALK)
    assert len(frames) == len(d3.WALK) == 50


def test_walk_stays_inside_open_cells() -> None:
    state = d3.SPAWN
    for cmd in d3.WALK:
        state = d3.step(state, cmd)
        cx, cy = d3.div(state.posX, d3.UNITS), d3.div(state.posY, d3.UNITS)
        assert d3.map_cell(cx, cy) == 0, f"walk entered wall cell {(cx, cy)}"
    # And the finale is where the demo promises: in the exit room, facing
    # the red exit door in its west wall.
    assert state.heading == 8
    cx, cy = d3.div(state.posX, d3.UNITS), d3.div(state.posY, d3.UNITS)
    assert (cx, cy) == (8, 10)
    assert d3.map_cell(cx - 5, cy) == 1


# ── pinned frames (hand-checked against the scratchpad PNGs) ─────────────────
#: The spawn view (WALK[0] is a no-op): up the spawn corridor, the near
#: alcove wall flanking left in striped white/gray panels with their seam
#: rows (V3), the octagon's north wall ahead beyond NEAR_D — all dark '7' —
#: with the window slits, the blue armor pedestal '4' dots in them *right* of
#: centre (east of the spawn column: the no-mirror evidence, risk R10), and
#: the raised side platform at the right edge.
SPAWN_FRAME = [
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "7700000000000000000000000000000000000000000000000000000000000000",
    "ff70000000000000000000000000000000000000000000000000000000000000",
    "fff7700000000000000000000000000000000000000000000000000000000000",
    "fff7777000000000000000000000000000000000000000000000000000000000",
    "77f7777770000000000000000000000000000000000000000000000000000000",
    "ff77777777700000000000000000000000000000000000000000000000000000",
    "fff7777777770000000000000000000000000000000000000000000000000000",
    "fff7777777770000000000000000000000000000000000000000000000000000",
    "77f7777777770000000000000000000000000000000000000000000000000000",
    "ff77777777770000000000000000000000000000000000000000000000000000",
    "fff7777777770000000000000000000000000000000000000000000000000000",
    "fff7777777770000000000000000000000000000000000000000000000000000",
    "77f7777777770000000000000000000000000000000000000000000000000000",
    "ff77777777770000000000000000000000000000000000000000000000000000",
    "fff7777777770000000000000000000000000000000000000000000000000000",
    "fff7777777770000000000000000000000000000000000000000033333333333",
    "77f7777777777777777777777777777777700077740077000333333333333333",
    "ff77777777777777777777777777777777777777747777777333333333333333",
    "fff7777777777777777777777777777777788877748877888333333333333333",
    "fff7777777778888888888888888888888888888888888888888833333333333",
    "77f7777777778888888888888888888888888888888888888888888888888888",
    "ff77777777778888888888888888888888888888888888888888888888888888",
    "fff7777777778888888888888888888888888888888888888888888888888888",
    "fff7777777778888888888888888888888888888888888888888888888888888",
    "77f7777777778888888888888888888888888888888888888888888888888888",
    "ff77777777778888888888888888888888888888888888888888888888888888",
    "fff7777777778888888888888888888888888888888888888888888888888888",
    "fff7777777778888888888888888880770888888888888888888888888888888",
    "77f7777777778888888888888888807f77088888888888888888888888888888",
    "ff77777777788888888888888888807777088888888888888888888888888888",
    "fff7777778888888888888888888007777008888888888888888888888888888",
    "fff7777888888888888888888880777777770888888888888888888888888888",
    "77f7788888888888888888888880778877088888888888888888888888888888",
    "ff78888888888888888888888888007787708888888888888888888888888888",
    "ff88888888888888888888888888880778708888888888888888888888888888",
    "8888888888888888888888888888880777088888888888888888888888888888",
    "8888888888888888888888888888888077088888888888888888888888888888",
    "7777777777777777777777777777777777777777777777777777777777777777",
    "88889999999999999999999999999888888888888888888888ccccccccc88888",
    "88889999999999999999999999999888888888888888888888ccccccccc88888",
    "88888888888888888888888888888888888888888888888888ccccccccc88888",
    "8888bbbbbbbbbbbbbbbbbbbbbbbbb888888888888888888888ccccccccc88888",
    "8888bbbbbbbbbbbbbbbbbbbbbbbbb888888888888888888888ccccccccc88888",
    "88888888888888888888888888888888888888888888888888ccccccccc88888",
    "8888888888888888888888888888888888888888888888888888888888888888",
]

#: The doorway half-look (WALK[31], heading 7 at cell (12, 20)): the zigzag
#: room's near sawtooth spur filling the right half in striped/banded brown
#: and bright-yellow panels (V3), the channel receding left into the dark.
ZIGZAG_LOOK_FRAME = [
    "0000000000000000000000000000000000000000000000000000333333333333",
    "00000000000000000000000000000000000000000000000000333bbbbbbbbbbb",
    "00000000000000000000000000000000000000000000000033333bbbbbbbbbbb",
    "00000000000000000000000000000000000000000000003333333bbbbbbbbbbb",
    "0000000000000000000000000000000000000000000033333333333333333333",
    "00000000000000000000000000000000000000000033333333333bbbbbbbbbbb",
    "00000000000000000000000000000000000000003333333333333bbbbbbbbbbb",
    "00000000000000000000000000000000000000003333333333333bbbbbbbbbbb",
    "0000000000000000000000000000000000000000333333333333333333333333",
    "00000000000000000000000000000000000000003333333333333bbbbbbbbbbb",
    "00000000000000000000000000000000000000003333333333333bbbbbbbbbbb",
    "00000000000000000000000000000000000000003333333333333bbbbbbbbbbb",
    "0000000000000000000000000000000000000000333333333333333333333333",
    "00000000000000000000000000000000000000003333333333333bbbbbbbbbbb",
    "00000000000000000000000000000000000000003333333333333bbbbbbbbbbb",
    "77777777777777700000000000000000000000003333333333333bbbbbbbbbbb",
    "ff7777777ffffff7777777777777777777777777333333333333333333333333",
    "ff7777777fffffff777777fffff77777fffff7773333333333333bbbbbbbbbbb",
    "ff7777777fffffff777777fffff77777fffff7773333333333333bbbbbbbbbbb",
    "777777777777777f777777fffff77777fffff7773333333333333bbbbbbbbbbb",
    "ff7777777ffffff7777777777777777777777777333333333333333333333333",
    "ff7777777fffffff777777fffff77777fffff7773333333333333bbbbbbbbbbb",
    "ff7777777fffffff777777fffff77777fffff7773333333333333bbbbbbbbbbb",
    "777777777777777f777777fffff77777fffff7773333333333333bbbbbbbbbbb",
    "ff7777777ffffff7777777777777777777777777333333333333333333333333",
    "ff7777777ffffff88888888888888888888888883333333333333bbbbbbbbbbb",
    "88888888888888888888888888888888888888883333333333333bbbbbbbbbbb",
    "88888888888888888888888888888888888888883333333333333bbbbbbbbbbb",
    "8888888888888888888888888888888888888888333333333333333333333333",
    "88888888888888888888888888888888888888883333333333333bbbbbbbbbbb",
    "88888888888888888888888888888807708888883333333333333bbbbbbbbbbb",
    "8888888888888888888888888888807f770888883333333333333bbbbbbbbbbb",
    "8888888888888888888888888888807777088888333333333333333333333333",
    "88888888888888888888888888880077770088883333333333333bbbbbbbbbbb",
    "88888888888888888888888888807777777708883333333333333bbbbbbbbbbb",
    "88888888888888888888888888807788770888888833333333333bbbbbbbbbbb",
    "8888888888888888888888888888007787708888888833333333333333333333",
    "88888888888888888888888888888807787088888888883333333bbbbbbbbbbb",
    "88888888888888888888888888888807770888888888888833333bbbbbbbbbbb",
    "88888888888888888888888888888880770888888888888888333bbbbbbbbbbb",
    "7777777777777777777777777777777777777777777777777777777777777777",
    "88889999999999999999999999999888888888888888888888ccccccccc88888",
    "88889999999999999999999999999888888888888888888888ccccccccc88888",
    "88888888888888888888888888888888888888888888888888ccccccccc88888",
    "8888bbbbbbbbbbbbbbbbbbbbbbbb8888888888888888888888ccccccccc88888",
    "8888bbbbbbbbbbbbbbbbbbbbbbbb8888888888888888888888ccccccccc88888",
    "88888888888888888888888888888888888888888888888888ccccccccc88888",
    "8888888888888888888888888888888888888888888888888888888888888888",
]


def test_pinned_spawn_frame() -> None:
    assert d3.frames_for_commands(d3.WALK)[0] == SPAWN_FRAME


def test_pinned_zigzag_look_frame() -> None:
    assert d3.WALK[31] == d3.KEY_RIGHT, "the pin is the frame after the doorway half-look"
    assert d3.frames_for_commands(d3.WALK)[31] == ZIGZAG_LOOK_FRAME


def test_pinned_fire_frame() -> None:
    """WALK[10] FIREs at the armor pedestal: the recoil sprite, the muzzle
    flash blooming above it, and the ammo bar one round shorter."""
    assert d3.WALK[10] == d3.KEY_FIRE == 16
    fire = d3.frames_for_commands(d3.WALK)[10]
    state = d3.SPAWN
    for cmd in d3.WALK[:11]:
        state = d3.step(state, cmd)
    # The frame is exactly the fired render at the post-shot ammo count …
    assert fire == d3.render(state, fire=True, ammo=49, health=100)
    # … the viewport differs from the idle render only where the two sprites
    # differ (GUN_FIRE where it paints, GUN_IDLE's cells restored elsewhere) …
    plain = d3.render(state, ammo=49, health=100)
    fire_px = {(r, c + i) for r, c, cs in d3.GUN_FIRE for i in range(len(cs))}
    idle_px = {(r, c + i) for r, c, cs in d3.GUN_IDLE for i in range(len(cs))}
    diff = {
        (r, c)
        for r in range(d3.H3D) for c in range(d3.WIDTH)
        if fire[r][c] != plain[r][c]
    }
    assert diff <= (fire_px | idle_px)
    # … and the pin, hand-checked in the PNGs: the muzzle flash's bright
    # yellow/white bloom above the raised pistol's outlined slide.
    assert [row[29:36] for row in fire[26:30]] == [
        "88bffb8", "8bffffb", "88bffb8", "8077088"]


# ── the title screen ─────────────────────────────────────────────────────────
def test_title_frame_shape_and_runs_are_lossless() -> None:
    frame = d3.title_frame()
    assert len(frame) == d3.HEIGHT == 48
    hexdigits = set("0123456789abcdef")
    for row in frame:
        assert len(row) == d3.WIDTH == 64
        assert set(row) <= hexdigits
    # The RLE replays to exactly the frame, covering all 3072 pixels.
    runs = d3.title_runs()
    assert sum(n for _, n in runs) == d3.WIDTH * d3.HEIGHT
    replay = "".join("%x" % c * n for c, n in runs)
    assert replay == "".join(frame)
    assert all(n >= 1 and 0 <= c <= 15 for c, n in runs)


def test_title_words_are_pre_encoded_run_commands() -> None:
    """One command word per run: 8*(count*16 + colour) + C_RUN, ready to SND."""
    from randomfun2026solvers.lm1.store import DoomUnit

    words = d3.title_words()
    runs = d3.title_runs()
    assert len(words) == len(runs) == 968
    run = DoomUnit.CODES["RUN"]
    assert words == [8 * (n * 16 + c) + run for c, n in runs]
    # Replayed through the unit model, the words paint exactly the title frame.
    writes: list[tuple[int, int]] = []
    unit = DoomUnit(lambda p, v: writes.append((p, v)))
    for w in words:
        unit.send(w)
    unit.send(DoomUnit.CODES["COMMIT"])
    assert frames_from_writes(writes, width=d3.WIDTH, height=d3.HEIGHT) == [d3.title_frame()]


# ── boot data and the cases file ─────────────────────────────────────────────
def test_preamble_is_the_documented_tape_order() -> None:
    pre = d3.preamble_words()
    assert len(pre) == 295
    assert pre[0:256] == d3.map_words()                  # MAPB, slots 1..256
    assert pre[256:272] == [16 ** k for k in range(16)]  # POWB, slots 257..272
    assert pre[272:288] == d3.heading_table()            # HDGB, slots 273..288
    # Spawn scalars, slots 289..295: cell (42, 4), heading 4 = north.
    assert pre[288:] == [43520, 4608, 4, 0, 1024, 676, 0]
    assert all(w >= 0 for w in pre)  # the E1M1 spawn happens to need no negatives
    assert d3.input_words(d3.WALK) == pre + d3.title_words() + d3.WALK


def test_cases_json_shape_title_round_then_one_round_per_command() -> None:
    cases = d3.cases_json(d3.WALK)
    (case,) = cases["publicTestData"]
    assert case["name"] == "deadman-3d"
    rounds = case["rounds"]
    assert len(rounds) == len(d3.WALK) + 1
    # Round 0: the whole boot burst (preamble + title RLE), the title frame.
    boot = [str(w) for w in d3.preamble_words() + d3.title_words()]
    assert rounds[0]["in"] == boot
    assert rounds[0]["out"] == []
    assert rounds[0]["frames"] == [d3.title_frame()]
    frames = d3.frames_for_commands(d3.WALK)
    for k, rnd in enumerate(rounds[1:]):
        assert rnd["in"] == [str(d3.WALK[k])]
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
    assert (slots["MAPB"], slots["POWB"], slots["HDGB"]) == (1, 257, 273)
    assert slots["POSX"] == len(d3.preamble_words()) - 6 == 289
    # The scalars run consecutively after the boot data, PTR last (the V4
    # live-HUD scalars AMMO and HEALTH sit just before it).
    scalars = sorted(v for k, v in slots.items() if v >= slots["CMD"])
    assert scalars == list(range(296, 330))
    assert slots["AMMO"] == 327 and slots["HEALTH"] == 328
    assert slots["PTR"] == max(slots.values()) == 329


def test_registry_pins() -> None:
    """The demo borrows plotter's slug for registration; everything else is its own."""
    assert programs.problem_of("deadman-3d") == "plotter"
    assert "deadman-3d" in programs.DEMOS
    # The 64x48 panel comes from DISPLAY_OVERRIDE, not plotter's problem JSON …
    assert machine.display_for("deadman-3d") == (d3.WIDTH, d3.HEIGHT) == (64, 48)
    assert machine.display_for("plotter") == (32, 24)
    # … and the tape is highest .equ address + 1 (an exactly-sized tape stalls).
    assert machine.TAPE_SIZE["deadman-3d"] == max(d3.tape_slots().values()) + 1 == 330
    # 330 slots is past the rotating tape's practical cap, so the STORE rides
    # the men-v3 man-memory (~11 ticks an access, whatever n is) …
    assert machine.STORE_TIER["deadman-3d"] == "men-v3"
    # … as the 8x42 multi-column block: the one-column strip was 681x999 and set
    # BOTH dimensions of the old 756x1197 bbox; 8x42 (336 >= 330 cells) is the
    # shape that, jointly with the 42-row ROM fold, makes the machine an exact
    # 307x307 square — the viewer holds the full bounding rectangle, so
    # squareness is this demo's objective.
    assert machine.STORE_SHAPE["deadman-3d"] == (8, 42)
    # The router is the SINGLE looping block (v2's footprint): the CPU issues
    # reads ~1k ticks apart, so the walk home happens while the router idles —
    # measured tick-identical to the unrolled 8-block strip on the frame gate.
    assert machine.STORE_OPS["deadman-3d"] == 1
    # … at the recorded pad — the smallest that binds every pipe now that the
    # input pipe enters the north wall (INPUT_NORTH) instead of rivalling the
    # memory band from the west.
    assert machine.MEM_PAD["deadman-3d"] == 17
    # The frame-1 tick levers, all opt-in so other machines stay byte-identical:
    assert "deadman-3d" in machine.INPUT_NORTH
    assert "deadman-3d" in machine.STORE_TELEPORT
    # store_dy 3: each row shortens the serial request route one cell; 3 is
    # where height meets the 307-column width exactly (the 307x307 square).
    assert machine.MEM_PLACE["deadman-3d"] == ((0, 0), (0, 3))


def test_short_emulator_run_is_pixel_equal_to_golden() -> None:
    """Title, spawn view, a forward step, a turn, another step.

    Covers the title round, the no-op, move and turn arms plus the render
    pipeline end to end; the full walk and the fuzz walk are the slow tier.
    """
    cmds = [0, d3.KEY_FWD, d3.keys("wa "), d3.KEY_RIGHT]
    want = [d3.title_frame()] + d3.frames_for_commands(cmds)
    assert _emulator_frames(cmds, max_instructions=8_000_000) == want


@slow
def test_the_full_demo_walk_is_pixel_equal_to_golden() -> None:
    """The title plus all 35 WALK commands."""
    assert _emulator_frames(d3.WALK) == [d3.title_frame()] + d3.frames_for_commands(d3.WALK)


@slow
def test_a_seeded_fuzz_walk_is_pixel_equal_to_golden() -> None:
    """40 seeded key codes: every arm (fire included), wall bumps, diagonal
    headings — plus junk codes that must fall through to the no-op render."""
    rng = random.Random(2026)
    pool = [1, 2, 4, 8] * 2 + [16, 21, 26, 3, 12, 17, 0, 255, 100, -3]
    cmds = [rng.choice(pool) for _ in range(40)]
    assert any(d3.fire_bit(c) for c in cmds) and -3 in cmds and 255 in cmds
    assert _emulator_frames(cmds) == [d3.title_frame()] + d3.frames_for_commands(cmds)


def test_the_persistent_player_matches_a_from_scratch_run() -> None:
    """--play's resumable emulator (the phase-rewind trick) is pixel-exact."""
    cmds = [0, 1, 16, 4, 21, 8, 2, 255]  # every arm, fires, a chord, junk
    player = d3._MachinePlayer()
    fed = [player.feed(c) for c in cmds]
    from_scratch = _emulator_frames(cmds, max_instructions=8_000_000)
    assert player.title == from_scratch[0] == d3.title_frame()
    assert fed == from_scratch[1:]
    assert fed == d3.frames_for_commands(cmds)


def test_the_ansi_play_rendering_shape() -> None:
    """--play draws 24 half-block lines of 64 cells, reset at each line's end."""
    text = d3._ansi_frame(d3.render(d3.SPAWN))
    lines = text.split("\n")
    assert len(lines) == d3.HEIGHT // 2 == 24
    for line in lines:
        assert line.count("▀") == d3.WIDTH
        assert line.endswith("\x1b[0m")


@slow
def test_the_machine_synthesizes_with_the_men_v3_store() -> None:
    """`build_for` binds every pipe: the DOOM unit's 64x48 panel + men-v3 STORE."""
    m = machine.build_for("deadman-3d")
    assert m.mem_pad == machine.MEM_PAD["deadman-3d"]
    # The panel belongs to the DOOM unit now, not the CPU: the machine carries
    # exactly one display room, 64x48 plus its walls.
    _px, _py, pw, ph = m.regions["display"]
    assert (pw, ph) == (d3.WIDTH + 2, d3.HEIGHT + 2) == (66, 50)
    # The whole point of the 8x42 STORE_SHAPE + 42-row fold + store_dy 3: the
    # machine is an exact square (down from 756x1197), because the viewer holds
    # its full bounding rectangle.
    assert (max(len(r) for r in m.rows), len(m.rows)) == (307, 307)


# ── the DOOM unit: the column-painter coprocessor the CPU sends frames to ────
def test_doom_unit_codes_pin_the_trie() -> None:
    """The emulator model's command codes are read off the hardware trie, and
    the generated asm's C_COL must be 0 (the column send is a bare MULI 8)."""
    from randomfun2026solvers.lm1 import d3_unit
    from randomfun2026solvers.lm1.store import DoomUnit

    assert d3_unit.arm_codes() == DoomUnit.CODES == {
        "COL": 0, "CURS": 1, "RUN": 4, "GUN": 5, "GUNF": 6, "COMMIT": 7}
    # The unit's baked sprites are the golden model's, glyph for glyph.
    assert DoomUnit.GUN_IDLE == tuple(d3.GUN_IDLE)
    assert DoomUnit.GUN_FIRE == tuple(d3.GUN_FIRE)
    assert DoomUnit.MASKS == (7, 15, 15, 15)
    assert min(d3_unit.binding_margins().values()) >= 1
    blk = d3_unit.build_doom()
    assert blk.lengths["addr"] == blk.lengths["data"]
    assert blk.lengths["swap"] > blk.lengths["data"]


@slow
def test_doom_unit_probe_paints_like_the_model() -> None:
    """The placed block plus a feeder, judged on the native engine against the
    DoomUnit model's own frames — a negative-seed COL (top row 0) included."""
    from randomfun2026solvers.fast_littleman import FastLittleman
    from randomfun2026solvers.lm1 import d3_unit
    from randomfun2026solvers.lm1.store import DoomUnit

    codes = DoomUnit.CODES

    def col(top: int, bot: int, x: int, colour: int) -> int:
        seed = (top * 64 + x) * 16 + colour - 1024
        return d3_unit.word(codes["COL"], seed * 64 + (bot - top + 1))

    w = d3_unit.word
    cmds = [
        # frame 1 opens like the title round: RUNs at the cursor, then COMMIT
        w(codes["RUN"], 70 * 16 + 9), w(codes["RUN"], 1 * 16 + 12),
        w(codes["RUN"], 200 * 16 + 3), w(codes["COMMIT"], 0),
        # frame 2 is a gameplay frame: banded columns, the idle gun, then the
        # HUD flow — CURS to the strip, background RUNs, a CURS+RUN bar
        col(3, 10, 5, 12), col(0, 39, 0, 9), col(20, 20, 63, 7), col(5, 39, 30, 14),
        w(codes["GUN"], 0),
        w(codes["CURS"], 2560), w(codes["RUN"], 64 * 16 + 7),
        w(codes["RUN"], 55 * 16 + 8),
        w(codes["CURS"], 41 * 64 + 4), w(codes["RUN"], 25 * 16 + 9),
        w(codes["COMMIT"], 0),
        # frame 3: the recoil sprite over a wall column
        col(10, 30, 22, 11), w(codes["GUNF"], 0), w(codes["COMMIT"], 0),
    ]
    writes: list[tuple[int, int]] = []
    unit = DoomUnit(lambda p, v: writes.append((p, v)))
    for w in cmds:
        unit.send(w)
    expected = frames_from_writes(writes, width=64, height=48)
    assert len(expected) == 3
    rows, _dbg, _blk = d3_unit.build_probe(cmds)
    res = FastLittleman(rows).run([], frames=[expected], max_ticks=5_000_000)
    assert res.fatal is None, (res.fatal, res.fatal_pos)
    assert res.passed is True


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


def test_v2_artifact_copies_match_the_canonical_files() -> None:
    """littleman/examples/deadman-3d_v2.* are clearly-named byte-identical
    copies of the canonical artifacts (the user's "unmistakably new" set)."""
    for stem in ("man", "debug.html", "input.txt"):
        canon = (REPO / "littleman" / "examples" / f"deadman-3d.{stem}").read_bytes()
        v2 = (REPO / "littleman" / "examples" / f"deadman-3d_v2.{stem}").read_bytes()
        assert v2 == canon, f"deadman-3d_v2.{stem} drifted from the canonical file"


@slow
def test_the_first_rounds_judge_clean_on_the_native_engine() -> None:
    """Round-gated frame judging on the independent native validator: round 0
    (boot + the title screen) and round 1 (the first walk command).

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


# ── the taped store variant: the same demo with ~20 men instead of ~700 ──────
TAPED_MAN = REPO / "littleman" / "examples" / "deadman-3d_taped.man"


def test_taped_registry_pins() -> None:
    """The taped variant is opt-in: the canonical machine STAYS men-v3, and the
    taped build is the one-liner `build_for("deadman-3d", store="taped")`."""
    assert machine.STORE_TIER["deadman-3d"] == "men-v3"
    # Traffic-shaped plan: the hot high addresses (POWB/HDG at 257..288, then
    # POSX and the per-frame scalars up to PTR=329) get small cheap rings.
    assert machine.TAPED_BANKS["deadman-3d"] == (128, 128, 40, 33)
    assert sum(machine.TAPED_BANKS["deadman-3d"]) >= machine.TAPE_SIZE["deadman-3d"] - 1
    assert machine.TAPED_SKIP_BATCH["deadman-3d"] == 2
    # No deadman-3d_taped.input.txt: same program, same protocol, same input —
    # the canonical deadman-3d.input.txt drives both machines.
    assert not (TAPED_MAN.parent / "deadman-3d_taped.input.txt").exists()


@slow
def test_checked_in_taped_man_matches_the_machine_builder() -> None:
    rows = TAPED_MAN.read_text().rstrip("\n").split("\n")
    assert machine.build_for("deadman-3d", store="taped").rows == rows


@slow
def test_the_taped_machine_census_dims_and_first_round_gate() -> None:
    """The variant's whole reason: ~20 little men (the visualizer's metric)
    against the men-v3 store's ~700, in the same 307-wide bounding class —
    and the frames still judge pixel-clean on the native engine."""
    m = machine.build_for("deadman-3d", store="taped")
    src = "\n".join(m.rows)
    assert (max(len(r) for r in m.rows), len(m.rows)) == (307, 216)
    assert src.count("@") == 20  # no births: static men ARE the census
    from randomfun2026solvers.fast_littleman import FastLittleman

    case = d3.cases_json(d3.WALK[:1])["publicTestData"][0]
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    frames = [r["frames"] for r in case["rounds"]]
    res = FastLittleman(src).run(inp, frames=frames, max_ticks=300_000_000)
    assert res.fatal is None, res.fatal
    assert res.passed is True
