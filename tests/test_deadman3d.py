"""The ``deadman-3d`` golden model: the imported Freedoom E1M1 map data (real
level geometry, ``wadimport``-generated), the packed heading table, the Q10
raycaster, the HUD, the demo walk, the cases-file shape — and the generated
asm: generator/registry pins plus emulator runs pixel-equal to the golden
model.

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
from randomfun2026solvers import wadimport as wi  # noqa: E402
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


def test_the_map_is_freedoom_e1m1s_landmarks() -> None:
    """The imported level's fixed points: spawn, hall, waterfall, slime fall."""
    # Spawn: Freedoom E1M1's real player-1 start — the west start hall,
    # facing east (heading 0), the hall open ten steps ahead.
    assert (d3.SPAWN.posX, d3.SPAWN.posY, d3.SPAWN.heading) == (5632, 27136, 0)
    assert all(d3.map_cell(x, 26) == 0 for x in range(5, 26))
    assert d3.map_cell(3, 26) == 7  # the gray wall at the player's back
    # The blue WFALL waterfall running down the south nukage room's west wall.
    assert d3.map_cell(17, 12) == d3.map_cell(25, 18) == 4
    # The green slime fall / MCSTAT screens on the cavern's north rim — the
    # walk's finale target — and its gold-brown ZIMMER flank to the west.
    assert d3.map_cell(45, 49) == d3.map_cell(46, 48) == 2
    assert d3.map_cell(40, 48) == d3.map_cell(41, 49) == 3
    # The walk's finale cell is open, the fall one cell beyond it.
    assert d3.map_cell(45, 46) == 0 and d3.map_cell(45, 48) == 0


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


def _bar_px(rows: list[str], cols: tuple[int, int]) -> int:
    """How many cells of a readout well the live bar has claimed."""
    col0, col1 = cols
    return rows[d3.BAR_ROWS[0] - d3.H3D][col0:col1].count(f"{d3.BAR_COLOR:x}")


def test_hud_background_and_live_bars() -> None:
    """The HUD strip: DOOM's real STBAR as the background, plus the
    proportional readouts inside the bar's OWN ammo and health wells."""
    bg = d3.hud_bg_rows()
    assert len(bg) == 8 and all(len(r) == d3.WIDTH for r in bg)
    # The background RLE is a faithful re-encoding — it is the asm's constant
    # table (one pre-encoded RUN word per run behind one CURS).
    replay = "".join("%x" % c * n for c, n in d3.hud_bg_runs())
    assert replay == "".join(bg)
    assert sum(n for _, n in d3.hud_bg_runs()) == 8 * d3.WIDTH
    # The table must stay COMPACT (every run costs two ROM words on a loop the
    # machine re-runs each frame). The exact count is an art outcome; the
    # ceiling is the contract.
    assert len(d3.hud_bg_runs()) <= 64
    # The wells are DOOM's, not invented: wadimport scales st_stuff.c's own
    # placement constants off the 320x32 bar, and the model reads that scaling.
    assert d3.AMMO_BAR_COLS == wi.stbar_cells("ammo")[0::2]
    assert d3.HEALTH_BAR_COLS == wi.stbar_cells("health")[0::2]
    assert d3.BAR_ROWS == tuple(d3.H3D + r for r in wi.stbar_cells("ammo")[1::2])
    # Neither bar can spill out of its own well, at any legal scalar value.
    for ammo in range(d3.AMMO_START + 1):
        assert d3.div(ammo, d3.AMMO_PER_PX) <= d3.AMMO_BAR_COLS[1] - d3.AMMO_BAR_COLS[0]
    for hp in range(d3.HEALTH_START + 1):
        assert (d3.div(hp, d3.HEALTH_PER_PX)
                <= d3.HEALTH_BAR_COLS[1] - d3.HEALTH_BAR_COLS[0])
    # Full readouts at the spawn state, in the digits' own red, over the bar.
    full = d3.hud_rows(100, 50)
    assert _bar_px(full, d3.AMMO_BAR_COLS) == d3.div(50, d3.AMMO_PER_PX)
    assert _bar_px(full, d3.HEALTH_BAR_COLS) == d3.div(100, d3.HEALTH_PER_PX)
    for row in range(*d3.BAR_ROWS):     # the whole well band, not just its top
        assert full[row - d3.H3D][d3.AMMO_BAR_COLS[0]:
                                  d3.AMMO_BAR_COLS[0] + 8] == "1" * 8
        assert full[row - d3.H3D][d3.HEALTH_BAR_COLS[0]:
                                  d3.HEALTH_BAR_COLS[0] + 10] == "1" * 10
    # A drained clip paints NO ammo bar: the status bar art shows through.
    empty = d3.hud_rows(100, 0)
    assert _bar_px(empty, d3.AMMO_BAR_COLS) == 0
    assert empty[d3.BAR_ROWS[0] - d3.H3D][slice(*d3.AMMO_BAR_COLS)] == \
        bg[d3.BAR_ROWS[0] - d3.H3D][slice(*d3.AMMO_BAR_COLS)]
    assert _bar_px(empty, d3.HEALTH_BAR_COLS) == 10
    # The render wires them through: twelve shots in, the bar is one px shorter.
    assert d3.render(d3.SPAWN)[d3.H3D:] == d3.hud_rows(100, 50)
    assert d3.render(d3.SPAWN, ammo=48)[d3.H3D:] == d3.hud_rows(100, 48)
    assert _bar_px(d3.hud_rows(100, 42), d3.AMMO_BAR_COLS) == 7


def test_walk_is_its_chords_and_keys_encodes_the_mux() -> None:
    """The walk is spelled as chords; keys() encodes held-key bitmasks."""
    assert d3.WALK == [d3.keys(ch) for ch in d3.WALK_CHORDS]
    assert len(d3.WALK) == 57
    assert (d3.KEY_FWD, d3.KEY_BACK, d3.KEY_LEFT, d3.KEY_RIGHT, d3.KEY_FIRE) == (
        1, 2, 4, 8, 16)
    assert d3.keys("wa ") == 21 and d3.keys(".") == 0 and d3.keys("ww") == 1
    # The FIRE beats: M7b's corridor gallery (two shots to drop the first imp,
    # then one more at the same spot, through its corpse, into the second), the
    # cavern's south rim, the step OUT of the slime moat, and standing clean
    # before the fall.
    assert [i for i, c in enumerate(d3.WALK) if d3.fire_bit(c)] == [
        19, 20, 22, 33, 55, 56]
    assert d3.WALK[55] == d3.keys("w ") == 17  # fire while moving: the MUX
    # The M5 beats: three held frames standing in the moat before stepping out.
    assert d3.WALK[52:55] == [0, 0, 0]


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
    assert len(frames) == len(d3.WALK) == 57


def test_walk_stays_inside_open_cells() -> None:
    state = d3.SPAWN
    for cmd in d3.WALK:
        state = d3.step(state, cmd)
        cx, cy = d3.div(state.posX, d3.UNITS), d3.div(state.posY, d3.UNITS)
        assert d3.map_cell(cx, cy) == 0, f"walk entered wall cell {(cx, cy)}"
    # And the finale is where the demo promises: on the cavern's north rim,
    # facing the bright green slime fall.
    assert state.heading == 4
    cx, cy = d3.div(state.posX, d3.UNITS), d3.div(state.posY, d3.UNITS)
    assert (cx, cy) == (45, 46)
    assert d3.map_cell(cx, cy + 3) == 2


# ── nukage: the damage floors (M5) ───────────────────────────────────────────
def test_nukage_plane_round_trips_and_stays_off_walls() -> None:
    """The 64-word bit plane rebuilds NUKAGE_STR exactly; every nukage cell is
    open (the map words never carry it — a nonzero nibble would be a wall to
    the DDA) and bit 63 is never set (2**63 would leave the signed word)."""
    words = d3.nukage_words()
    assert len(words) == 64 and all(0 <= w < 2 ** 63 for w in words)
    printed = d3.NUKAGE_STR.splitlines()
    count = 0
    for x in range(d3.MAP_SIZE):
        for y in range(d3.MAP_SIZE):
            n = d3.nukage_cell(x, y)
            assert n == (1 if printed[d3.MAP_SIZE - 1 - y][x] == "N" else 0)
            if n:
                assert d3.map_cell(x, y) == 0
                count += 1
    assert count == 112  # Freedoom E1M1's slime moat, wadimport-measured


def test_walk_fords_the_moat_and_health_drains() -> None:
    """The walk stands on nukage for exactly 14 frames (the cavern crossing
    and the finale's soak), and the model threads health 100 -> 30 into the
    red bar, the floor colour and the face bands."""
    state = d3.SPAWN
    nuk_idx = []
    for i, cmd in enumerate(d3.WALK):
        state = d3.step(state, cmd)
        if d3.nukage_cell(d3.div(state.posX, d3.UNITS), d3.div(state.posY, d3.UNITS)):
            nuk_idx.append(i)
    assert nuk_idx == [36, 37, 38, 39, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54]
    frames = d3.frames_for_commands(d3.WALK)

    def health_px(fr: list[str]) -> int:
        return _bar_px(fr[d3.H3D:], d3.HEALTH_BAR_COLS)

    assert health_px(frames[35]) == 10          # full bar before the moat
    assert health_px(frames[39]) == 8           # 4 frames in: health 80
    assert health_px(frames[54]) == 3           # 14 frames in: health 30
    assert health_px(frames[56]) == 3           # out of the moat: no more drain
    # The floor floods green exactly on the standing-in-slime frames.
    assert frames[50][39][5] == "2" and frames[44][39][5] == "8"
    # And the face degrades: healthy at the spawn, bloodied in the soak.
    r, c, colours = d3.FACE_HEALTHY[0]
    assert frames[0][r][c:c + len(colours)] == colours
    r, c, colours = d3.FACE_BLOODY[-1]
    assert frames[54][r][c:c + len(colours)] == colours


def test_install_art_swaps_gun_face_bar_and_unit_model() -> None:
    """install_art (Mode B's art override) rebinds the golden's sprite tables,
    the asm generator's face AND status-bar constants, and the emulator unit
    model — and a restore puts the committed Freedoom art back exactly."""
    from randomfun2026solvers.lm1.store import DoomUnit

    keep_gun = (list(d3.GUN_IDLE), list(d3.GUN_FIRE))
    keep_faces = {"healthy": list(d3.FACE_HEALTHY), "hurt": list(d3.FACE_HURT),
                  "bloody": list(d3.FACE_BLOODY), "grim": list(d3.FACE_GRIM)}
    keep_bg = list(d3.HUD_BG_ROWS)
    idle = [(30, 30, "7")]
    fire = [(25, 31, "9"), (29, 30, "7")]
    faces = {k: [(40 + i, 29, "444444") for i in range(7)]
             for k in ("healthy", "hurt", "bloody", "grim")}
    # A synthetic bar whose RLE is a DIFFERENT length from the committed one:
    # the background table is CPU-side RUN constants, so the asm just carries
    # however many the imported art needs.
    bg = ["5" * d3.WIDTH] * 4 + ["6" * d3.WIDTH] * 4
    try:
        d3.install_art(idle, fire, faces, None, bg)
        assert DoomUnit.GUN_IDLE == tuple(idle)  # the emulator model follows
        assert len(d3.hud_bg_runs()) == 2 != len(keep_bg)
        frame = d3.render(d3.SPAWN)
        assert frame[30][30] == "7"              # the swapped gun paints
        assert frame[40][29:35] == "444444"      # the swapped face paints
        assert frame[47] == "6" * d3.WIDTH       # the swapped bar paints
        # … and the generated asm carries the swapped face's RUN constant
        # and the swapped bar's own two runs.
        src = d3.deadman3d_source()
        assert f"LDI {8 * (6 * 16 + 4) + DoomUnit.CODES['RUN']}" in src
        assert f"LDI {8 * (4 * d3.WIDTH * 16 + 6) + DoomUnit.CODES['RUN']}" in src
    finally:
        d3.install_art(keep_gun[0], keep_gun[1], keep_faces, None, keep_bg)
    assert DoomUnit.GUN_IDLE == tuple(d3.GUN_IDLE)
    assert d3.HUD_BG_ROWS == keep_bg
    r, c, colours = d3.FACE_HEALTHY[0]
    assert d3.render(d3.SPAWN)[r][c:c + len(colours)] == colours


def test_face_bands_and_grimace() -> None:
    """face_for: FIRE wins, else the health bands at 67/34 — exactly the asm's
    branch ladder — and hud_rows paints the chosen face into its box."""
    assert d3.face_for(100, False) is d3.FACE_HEALTHY
    assert d3.face_for(67, False) is d3.FACE_HEALTHY
    assert d3.face_for(66, False) is d3.FACE_HURT
    assert d3.face_for(34, False) is d3.FACE_HURT
    assert d3.face_for(33, False) is d3.FACE_BLOODY
    assert d3.face_for(0, False) is d3.FACE_BLOODY
    assert d3.face_for(100, True) is d3.FACE_GRIM
    rows = d3.hud_rows(100, 50)
    for r, c, colors in d3.FACE_HEALTHY:
        assert rows[r - d3.H3D][c:c + len(colors)] == colors
    hurt = d3.hud_rows(50, 50)
    for r, c, colors in d3.FACE_HURT:
        assert hurt[r - d3.H3D][c:c + len(colors)] == colors


# ── monsters: the depth-sorted billboards (M7a) ──────────────────────────────
def test_monster_table_is_legal_and_stands_on_open_floor() -> None:
    """Every THINGS-derived monster is a legal species on an open cell, and no
    two share one — a monster inside a wall would be permanently occluded by
    the very column it stands in."""
    assert 0 < len(d3.MONSTERS) <= d3.MAX_MON
    cells = set()
    for cx, cy, species in d3.MONSTERS:
        assert 0 <= cx < d3.MAP_SIZE and 0 <= cy < d3.MAP_SIZE
        assert species in (0, 1)
        assert d3.map_cell(cx, cy) == 0, f"monster inside wall at {(cx, cy)}"
        assert (cx, cy) not in cells, f"two monsters share cell {(cx, cy)}"
        cells.add((cx, cy))
    # One HP word per monster, from the per-species table.
    hp = d3.monster_hp_words()
    assert len(hp) == len(d3.MONSTERS)
    assert hp == [d3.MON_HP[s] for _cx, _cy, s in d3.MONSTERS]


def test_the_sprite_divisor_is_positive_for_every_heading() -> None:
    """DET = planeX*dirY - dirX*planeY divides the camera transform, and DIV
    by 0 is 0 on this CPU — a heading where DET vanished (or went negative)
    would fold every sprite onto column 0 instead of failing loudly."""
    for h in range(d3.HEADINGS):
        assert d3.det_for(h) > 0


def test_sprite_words_pack_one_column_each_inside_the_signed_word() -> None:
    """Each band is one packed word per sprite column, bottom pixel in nibble
    0. Heights <= 14 keep the top nibble below 2**60, so no word can reach the
    2**63 that would leave the signed range."""
    widths = [w for w, _h in d3.MON_BANDS]
    heights = [h for _w, h in d3.MON_BANDS]
    assert all(h <= 14 for h in heights)
    assert d3.MON_STRIDE == sum(widths)
    assert d3.MON_BAND_OFF == (0, widths[0], widths[0] + widths[1])
    # Three sprite frames share the stride: species 0, species 1, the corpse.
    assert len(d3.MON_SPRITES) == 3 * d3.MON_STRIDE
    assert all(0 <= w < 2 ** 63 for w in d3.MON_SPRITES)
    # Every column's nibbles fit its band's height, and colour 0 is the
    # transparency the paint chain skips (so a fully-clear column is 0).
    for frame in range(3):
        for band, (bw, bh) in enumerate(d3.MON_BANDS):
            off = frame * d3.MON_STRIDE + d3.MON_BAND_OFF[band]
            for word in d3.MON_SPRITES[off:off + bw]:
                assert word < 16 ** bh


def test_band_thresholds_bracket_the_cull_range() -> None:
    """Nearest band first: the thresholds ascend and sit strictly inside the
    near/far culls, so every surviving depth picks exactly one band."""
    assert list(d3.BAND_T) == sorted(d3.BAND_T)
    assert d3.MON_NEAR < d3.BAND_T[0] < d3.BAND_T[-1] < d3.MON_FAR
    assert len(d3.BAND_T) == len(d3.MON_BANDS) - 1


def _painted_and_hidden(state: d3.State) -> tuple[set, set]:
    """Sprite pixels the wall depth test let through, and the ones it cut."""
    real = d3._paint_monsters
    cap: dict[str, list] = {}

    def spy(cols, zbuf, *args):
        base = [c[:] for c in cols]
        drawn = [c[:] for c in cols]
        real(drawn, zbuf, *args)
        free = [c[:] for c in cols]
        real(free, [10 ** 9] * len(zbuf), *args)  # walls infinitely far away
        cap["px"] = [base, drawn, free]
        cols[:] = drawn

    d3._paint_monsters = spy
    try:
        d3.render(state)
    finally:
        d3._paint_monsters = real
    base, drawn, free = cap["px"]
    at = {(x, y) for x in range(len(drawn)) for y in range(len(drawn[0]))}
    painted = {p for p in at if drawn[p[0]][p[1]] != base[p[0]][p[1]]}
    unclipped = {p for p in at if free[p[0]][p[1]] != base[p[0]][p[1]]}
    return painted, unclipped - painted


def test_walls_occlude_monsters_per_column() -> None:
    """The z-buffer is load-bearing, not decorative: at WALK[24] a monster is
    cut down the middle — some of its columns pass the wall depth test and
    some do not — and pretending the walls are infinitely far away paints the
    pixels the wall is hiding."""
    state = d3.SPAWN
    for cmd in d3.WALK[:25]:
        state = d3.step(state, cmd)
    painted, hidden = _painted_and_hidden(state)
    assert painted and hidden
    # The same columns carry both: this is one sprite clipped, not one sprite
    # drawn and a second one wholly behind a wall.
    assert {x for x, _y in painted} & {x for x, _y in hidden}


# ── M7b: the shot, the hit test, the corpse ──────────────────────────────────
#: The corridor gallery: the walk stands at (25, 34) and the imp at (25, 38) is
#: dead under the crosshair.  ``KILL`` is the frame whose shot takes it to 0 HP
#: — it still shows the living sprite, because the hit is applied only after
#: the frame that resolved it — and ``KILL + 1`` is the first corpse frame.
KILL = 20
GALLERY = 0     # the imp the walk drops: MONSTERS[0], hit id 1
BEHIND = 1      # the second imp in the queue, MONSTERS[1], hit id 2


def _own_pixels(state: d3.State, hp: list[int], which: int) -> set:
    """The screen cells monster ``which`` paints in this state: the frame with
    it in the table against the same frame with it never placed."""
    hit: list[int] = []
    with_it = d3.render(state, hp=hp, hit_out=hit)
    words = d3._MON_WORDS
    d3._MON_WORDS = [w for i, w in enumerate(words) if i != which]
    try:
        without = d3.render(state, hp=[h for i, h in enumerate(hp) if i != which])
    finally:
        d3._MON_WORDS = words
    return {(x, y) for y in range(d3.H3D) for x in range(d3.WIDTH)
            if with_it[y][x] != without[y][x]}


def _state_after(n: int) -> d3.State:
    state = d3.SPAWN
    for cmd in d3.WALK[:n]:
        state = d3.step(state, cmd)
    return state


def test_monster_hp_and_species_tables_pin_the_ledger() -> None:
    """One HP word per monster by species, and a third sprite stripe for the
    corpse every species falls into — the shared frame the paint chain reaches
    with no extra entry point."""
    assert d3.MON_HP == (1, 2)  # a zombieman drops to one shot, an imp to two
    hp = d3.monster_hp_words()
    assert hp == [d3.MON_HP[sp] for _cx, _cy, sp in d3.MONSTERS]
    assert all(h > 0 for h in hp), "a monster may not boot dead"
    # The ledger is boot-loaded (input-borne, never ROM) right after MONB …
    slots = d3.tape_slots()
    pre = d3.preamble_words()
    assert pre[slots["MHPB"] - 1:slots["MHPB"] - 1 + len(hp)] == hp
    assert slots["MHPB"] == slots["MONB"] + len(d3.MONSTERS)
    # … and the corpse rides stripe 2, the same 20 words wide as a species.
    assert len(d3.MON_SPRITES) == 3 * d3.MON_STRIDE
    corpse = d3.MON_SPRITES[2 * d3.MON_STRIDE:]
    assert any(corpse), "the corpse stripe cannot be blank"
    for band, (bw, bh) in enumerate(d3.MON_BANDS):
        off = d3.MON_BAND_OFF[band]
        assert all(w < 16 ** bh for w in corpse[off:off + bw])


def test_the_frame_that_kills_still_shows_the_living_monster() -> None:
    """The timing contract: the shot is resolved against THIS frame's geometry
    and applied after it renders, so the kill frame carries the standing sprite
    under the muzzle flash and the corpse arrives on the next one."""
    beats = d3.walk_beats(d3.WALK)
    frames = [b[0] for b in beats]
    # Two shots into the same imp: HP 2 -> 1 -> 0, both crediting hit id 1.
    assert d3.fire_bit(d3.WALK[KILL - 1]) and d3.fire_bit(d3.WALK[KILL])
    assert beats[KILL - 1][2] == beats[KILL][2] == GALLERY + 1
    assert beats[KILL - 1][3][GALLERY] == 1
    assert beats[KILL][3][GALLERY] == 0, "the second shot drops it"
    assert not d3.fire_bit(d3.WALK[KILL + 1]), "the corpse frame holds still"
    # The kill frame is the LIVE render: it paints exactly what the same frame
    # painted before the ledger moved, and the flash is on.
    stand = _state_after(KILL + 1)
    alive_px = _own_pixels(stand, [1] * len(d3.MONSTERS), GALLERY)
    assert alive_px, "the imp must be visible when it is shot"
    assert all(frames[KILL][y][x] == d3.render(
        stand, fire=True, ammo=48, health=100)[y][x] for x, y in alive_px)
    for r, c, colours in d3.GUN_FIRE:       # the muzzle flash, over the top
        assert frames[KILL][r][c:c + len(colours)] == colours
    r, c, colours = d3.FACE_GRIM[-1]
    assert frames[KILL][r][c:c + len(colours)] == colours
    # The next frame is the corpse: the standing sprite's pixels are gone …
    corpse_px = _own_pixels(stand, list(beats[KILL][3]), GALLERY)
    assert corpse_px != alive_px
    assert len(corpse_px) < len(alive_px), "a corpse is a heap, not a body"
    # … and what remains sits at the sprite's feet, on the floor line.
    assert min(y for _x, y in corpse_px) > min(y for _x, y in alive_px)
    assert corpse_px <= alive_px | {(x, y) for x, y in corpse_px}
    assert all(frames[KILL + 1][y][x] == d3.render(
        stand, ammo=48, health=100, hp=list(beats[KILL][3]))[y][x]
        for x, y in corpse_px)


def test_a_corpse_can_never_be_shot_again() -> None:
    """hp == 0 is the whole of "stops occluding gameplay": the corpse is still
    selected, still painted and still z-tested, but it is not a hit candidate,
    so the next round at the very same crosshair carries on into the imp queued
    behind it."""
    beats = d3.walk_beats(d3.WALK)
    assert beats[KILL][3][GALLERY] == 0
    # The walk fires again from the identical stand two frames later …
    assert d3.WALK[KILL + 2] == d3.KEY_FIRE
    assert _state_after(KILL + 1) == _state_after(KILL + 3)
    # … and the round goes THROUGH the corpse into the second imp.
    assert beats[KILL + 2][2] == BEHIND + 1
    assert beats[KILL + 2][3][GALLERY] == 0, "the corpse takes no more damage"
    assert beats[KILL + 2][3][BEHIND] == d3.MON_HP[1] - 1
    # The corpse is still on screen while that happens (painted, z-tested).
    assert _own_pixels(_state_after(KILL + 3), list(beats[KILL][3]), GALLERY)
    # And with the corpse alone in the table, the same shot hits nothing.
    hit: list[int] = []
    words = d3._MON_WORDS
    d3._MON_WORDS = [words[GALLERY]]
    try:
        d3.render(_state_after(KILL + 1), fire=True, hp=[0], live=True,
                  hit_out=hit)
    finally:
        d3._MON_WORDS = words
    assert hit == [0]


def test_a_dry_fire_kills_nothing() -> None:
    """LIVE is set exactly where AMMO is spent: an empty clip still flashes the
    muzzle, and nothing on the far end of the crosshair notices."""
    stand = _state_after(KILL + 1)
    alive = [1] * len(d3.MONSTERS)
    armed: list[int] = []
    d3.render(stand, fire=True, ammo=1, hp=alive, live=True, hit_out=armed)
    assert armed == [GALLERY + 1], "the stand must have a monster under the sight"
    for kwargs in ({"fire": True, "ammo": 0}, {"fire": False}, {}):
        dry: list[int] = []
        d3.render(stand, hp=alive, live=False, hit_out=dry, **kwargs)
        assert dry == [0]
    # The pixels do not care either way: the hit test is pure bookkeeping.
    assert d3.render(stand, fire=True, ammo=0, hp=alive, live=True) == \
        d3.render(stand, fire=True, ammo=0, hp=alive, live=False)


@slow
def test_an_emptied_clip_stops_killing() -> None:
    """The same gate end to end: park on the gallery stand and hold fire until
    the clip runs dry — the last live round kills, the dry ones do not."""
    cmds = list(d3.WALK[:KILL - 1]) + [d3.KEY_FIRE] * (d3.AMMO_START + 2)
    beats = d3.walk_beats(cmds)
    live = [i for i, b in enumerate(beats) if b[1]]
    assert len(live) == d3.AMMO_START, "50 rounds, then the clip is empty"
    hits = [b[2] for b in beats]
    assert hits[live[-1] + 1:] == [0] * (len(beats) - live[-1] - 1)
    assert any(hits[i] for i in live), "the live rounds did land"
    # Both corridor imps end up dead, and the dry rounds change no HP at all.
    assert beats[-1][3][GALLERY] == beats[-1][3][BEHIND] == 0
    assert beats[live[-1]][3] == beats[-1][3]


def test_a_wall_at_the_crosshair_saves_the_monster() -> None:
    """The hit test rides on the far side of the ZBUF compare, so a wall
    between the pistol and the monster is a miss — even though the billboard
    is centred on the crosshair and paints all its other columns."""
    stand = _state_after(KILL + 1)
    args = (stand.posX, stand.posY,
            *d3.unpack_heading(d3._HDG_WORDS[stand.heading]))
    alive = [1] * len(d3.MONSTERS)
    free = [10 ** 9] * d3.WIDTH
    open_cols = [[0] * d3.H3D for _ in range(d3.WIDTH)]
    assert d3._paint_monsters(open_cols, free, *args, alive, True) == GALLERY + 1
    walled = list(free)
    walled[d3.CROSSHAIR] = 1                    # one wall column, at the sight
    cols = [[0] * d3.H3D for _ in range(d3.WIDTH)]
    assert d3._paint_monsters(cols, walled, *args, alive, True) == 0
    # It is only that column that goes: the rest of the sprite still paints.
    painted = {(x, y) for x in range(d3.WIDTH) for y in range(d3.H3D)
               if cols[x][y]}
    assert painted and all(x != d3.CROSSHAIR for x, _y in painted)
    # And on the real map: a stand where a wall stands between the pistol and
    # a zombieman that the crosshair otherwise covers.
    blocked = d3.State(7 * 1024 + 512, 45 * 1024 + 512, 2)
    shot: list[int] = []
    d3.render(blocked, fire=True, hp=alive, live=True, hit_out=shot)
    assert shot == [0]
    cols = [[0] * d3.H3D for _ in range(d3.WIDTH)]
    assert d3._paint_monsters(
        cols, free, blocked.posX, blocked.posY,
        *d3.unpack_heading(d3._HDG_WORDS[blocked.heading]), alive, True) == 10


# ── pinned frames (hand-checked against the scratchpad PNGs) ─────────────────
#: The spawn view (WALK[0] is a no-op): east down Freedoom E1M1's start hall,
#: the striped white/gray BASE2 panels flanking both sides with their seam
#: rows (V3), the hall receding to the junction beyond NEAR_D — dark '7' —
#: with the brown '3' concrete accent at the hall's end.
SPAWN_FRAME = [
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "7770000000000000000000000000000000000000000000000000000000000077",
    "fff77000000000000000000000000000000000000000000000000000000077ff",
    "fff77770000000000000000000000000000000000000000000000000007777ff",
    "fff77777700000000000000000000000000000000000000000000000777777ff",
    "7777777777700000000000000000000000000000000000000000007777777777",
    "fff77777777700000000000000000000000000000000000000007777777777ff",
    "fff7777777777700000000000000000000000000000000000007f777777777ff",
    "fff777777777ff7700000000000000000000000000000000077ff777777777ff",
    "777777777777ffff000000000000000000000000000000000ffff77777777777",
    "fff777777777ffff000000000000000000000000000000000fff7777777777ff",
    "fff77777777777ff000000000000000000000000000000000ff7f777777777ff",
    "fff777777777ff7770000000000000000000000000000000077ff777777777ff",
    "777777777777fffff77700000000000000000000000000000ffff77777777777",
    "fff777777777ffffff7777700000000000000000000000000fff7777777777ff",
    "fff77777777777ffff77f7f77300000000000000077777777ff7f777777777ff",
    "fff777777777ff777f77f7f77337777777777777777777f7f77ff777777777ff",
    "777777777777fffff777f7f77337777777777777777777f7fffff77777777777",
    "fff777777777ffffff7777777337777777777777777777f7ffff7777777777ff",
    "fff77777777777ffff77f7f77388888888888888877777777ff7f777777777ff",
    "fff777777777ff777f77f7f8888888888888888888888888877ff777777777ff",
    "777777777777fffff77788888888888888888888888888888ffff77777777777",
    "fff777777777fffff88888888888888888888888888888888fff7777777777ff",
    "fff77777777777ff888888888888888888888888888888888ff7f777777777ff",
    "fff777777777ff7788888888888888888888888888888888877ff777777777ff",
    "777777777777ffff888888888888888888888888888888888ffff77777777777",
    "fff777777777ffff888888888888888888888888888888888fff7777777777ff",
    "fff7777777777788888888888888888878888888888888888887f777777777ff",
    "fff7777777778888888888888888888770888888888888888888f777777777ff",
    "7777777777788888888888888888887777088888888888888888887777777777",
    "fff77777788888888888888888888770000788888888888888888888777777ff",
    "fff77778888888888888888888888710101788888888888888888888887777ff",
    "fff77888888888888888888888888777777088888888888888888888888877ff",
    "7778888888888888888888888888770000778888888888888888888888888877",
    "8888888888888888888888888888330880338888888888888888888888888888",
    "8888888888888888888888888880333333338888888888888888888888888888",
    "8888888888888888888888888880333333880888888888888888888888888888",
    "8888888888888888888888888888888800888888888888888888888888888888",
    "1111111188111111111188888888800000088888888888888888888888888888",
    "1111111188111111111188888888703338088888888888887788788888888888",
    "1111111188111111111188888888783733088888888888888887788888888887",
    "1111111188111111111188888888783333888888888888888877788888888887",
    "7777777777777777777777777777783333888877777777778888788888888888",
    "8877777888877878788888878877888888888877777778888888888888888888",
    "0888888800088888888888888888888888888888888888888888888888888888",
]

#: The cavern half-look (WALK[42] holds after the ``d`` turn: heading 15 at
#: cell (41, 40)): the great cavern's north-east rim — the green MCSTAT
#: screens and gold-brown ZIMMER cliffs banded across the horizon, the
#: cavern floor sweeping to the dark distance.  By this frame the walk has
#: forded the moat's west lobe (frames 36..39): health 80, so the bar in
#: STBAR's health well is 8 of its 11 cells, the face still in its healthy
#: band.
CAVERN_LOOK_FRAME = [
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
    "2222200000000000000000000000000000000000000000000000000007777777",
    "aaaa200000000000000000000000000000000000000000222222222277777fff",
    "aaaa2333333333333333333322222222222222222222222aa222222277777fff",
    "aaaa2333333333333bb333332aaa222aaaa22222aaaaa22aa222222277777fff",
    "22222333333333333bb333332aaa222aaaa22222aaaaa22aa222222277777777",
    "aaaa2333333333333bb333332aaa222aaaa2222211aaa2222222222211777fff",
    "aaaa2333333333333333333322222222222222223322222aa222222233777fff",
    "aaaa2888888888888888888888888888888888883388882aa222222233777fff",
    "2222288888888888888888888888888888888888338888888888888833777777",
    "8888888888888888888888888888888888888888338888888888888833888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888888888888888888888888888888888888",
    "8888888888888888888888888888888878888888888888888888888888888888",
    "8888888888888888888888888888888770888888888888888888888888888888",
    "8888888888888888888888888888887777088888888888888888888888888888",
    "8888888888888888888888888888877000078888888888888888888888888888",
    "8888888888888888888888888888871010178888888888888888888888888888",
    "8888888888888888888888888888877777708888888888888888888888888888",
    "8888888888888888888888888888770000778888888888888888888888888888",
    "8888888888888888888888888888330880338888888888888888888888888888",
    "8888888888888888888888888880333333338888888888888888888888888888",
    "8888888888888888888888888880333333880888888888888888888888888888",
    "8888888888888888888888888888888800888888888888888888888888888888",
    "1111111888111111118888888888800000088888888888888888888888888888",
    "1111111888111111118888888888703338088888888888887788788888888888",
    "1111111888111111118888888888783733088888888888888887788888888887",
    "1111111888111111118888888888783333888888888888888877788888888887",
    "7777777777777777777777777777783333888877777777778888788888888888",
    "8877777888877878788888878877888888888877777778888888888888888888",
    "0888888800088888888888888888888888888888888888888888888888888888",
]


def test_pinned_spawn_frame() -> None:
    assert d3.frames_for_commands(d3.WALK)[0] == SPAWN_FRAME


def test_pinned_cavern_look_frame() -> None:
    assert d3.WALK[41] == d3.KEY_RIGHT, "the pin is the held frame of the half-look"
    assert d3.WALK[42] == 0
    assert d3.frames_for_commands(d3.WALK)[42] == CAVERN_LOOK_FRAME


def test_pinned_fire_frame() -> None:
    """WALK[33] FIREs at the cavern's sunlit south rim: the recoil sprite,
    the muzzle flash blooming above it, and the ammo bar one round shorter.

    Four rounds are already gone by then — M7b's three corridor shots plus
    this one — and the shot hits nothing: the rim is empty, so the HP ledger
    is untouched and the render is the plain fired frame."""
    assert d3.WALK[33] == d3.KEY_FIRE == 16
    fire = d3.frames_for_commands(d3.WALK)[33]
    state = d3.SPAWN
    for cmd in d3.WALK[:34]:
        state = d3.step(state, cmd)
    hp = list(d3.monster_hp_words())
    hp[0] = 0  # the corridor imp the walk already dropped
    hp[1] = 1  # … and the one behind it, wounded through its corpse
    # The frame is exactly the fired render at the post-shot ammo count …
    hit: list[int] = []
    assert fire == d3.render(state, fire=True, ammo=46, health=100, hp=hp,
                             live=True, hit_out=hit)
    assert hit == [0], "nothing stands on the south rim"
    # … the viewport differs from the idle render only where the two sprites
    # differ (GUN_FIRE where it paints, GUN_IDLE's cells restored elsewhere) …
    plain = d3.render(state, ammo=46, health=100, hp=hp)
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
        "8bffffb", "3bffffb", "889ff98", "8887888"]


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
    # The Freedoom titlepic RLEs to 429 runs (the old homage art took 968:
    # round 0's input SHRANK when the real art landed).
    assert len(words) == len(runs) == 429
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
    assert len(pre) == 451
    assert pre[0:256] == d3.map_words()                  # MAPB, slots 1..256
    assert pre[256:272] == [16 ** k for k in range(16)]  # POWB, slots 257..272
    assert pre[272:288] == d3.heading_table()            # HDGB, slots 273..288
    assert pre[288:352] == d3.nukage_words()             # NUKB, slots 289..352
    # Spawn scalars, slots 353..359: cell (5, 26), heading 0 = east.
    assert pre[352:359] == [5632, 27136, 0, 1024, 0, 0, -676]
    # M7a: the monster table, its HP block and the packed sprite columns.
    assert pre[359:375] == d3.monster_words()            # MONB, slots 360..375
    assert pre[375:391] == d3.monster_hp_words()         # MHPB, slots 376..391
    assert pre[391:] == d3.MON_SPRITES                   # SPRB, slots 392..451
    # planeY = -676 (east spawn) is the preamble's ONE negative word — legal,
    # because the preamble rides input, never ROM literals.
    assert [w for w in pre if w < 0] == [-676]
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
    assert (slots["MAPB"], slots["POWB"], slots["HDGB"], slots["NUKB"]) == (
        1, 257, 273, 289)
    assert slots["POSX"] == 353
    # M7a: the monster/sprite boot blocks end the preamble, then the 64
    # per-frame ZBUF slots (never boot-loaded), then the scalars.
    assert (slots["MONB"], slots["MHPB"], slots["SPRB"]) == (360, 376, 392)
    assert slots["ZBUF"] == len(d3.preamble_words()) + 1 == 452
    # The scalars run consecutively after ZBUF, PTR last; the sprite pass's
    # slot file is field-major triples (slot k's field at base + k).
    scalars = sorted(v for k, v in slots.items() if v >= slots["CMD"])
    assert scalars == list(range(452 + 64, 452 + 64 + len(d3._SCALARS)))
    assert slots["AMMO"] == 547 and slots["HEALTH"] == 548 and slots["NUKE"] == 549
    # M7b's shot scalars sit right after the frame counters they extend.
    assert slots["LIVE"] == 550 and slots["HIT"] == 551
    assert slots["STY1"] == slots["STY0"] + 1 and slots["SID2"] == slots["SID0"] + 2
    assert slots["PTR"] == max(slots.values()) == 599


def test_registry_pins() -> None:
    """The demo borrows plotter's slug for registration; everything else is its own."""
    assert programs.problem_of("deadman-3d") == "plotter"
    assert "deadman-3d" in programs.DEMOS
    # The 64x48 panel comes from DISPLAY_OVERRIDE, not plotter's problem JSON …
    assert machine.display_for("deadman-3d") == (d3.WIDTH, d3.HEIGHT) == (64, 48)
    assert machine.display_for("plotter") == (32, 24)
    # … and the tape is highest .equ address + 1 (an exactly-sized tape stalls).
    assert machine.TAPE_SIZE["deadman-3d"] == max(d3.tape_slots().values()) + 1
    # The tape is far past the rotating tape's practical cap, so the STORE rides
    # the men-v3 man-memory (~11 ticks an access, whatever n is) …
    assert machine.STORE_TIER["deadman-3d"] == "men-v3"
    # … as a multi-column block whose cells cover the tape. The exact shape is
    # a sweep outcome (it has moved with every milestone: 1-wide, 8x42, 9x44,
    # 10x60); what must hold is that it FITS — a short block silently drops
    # the top slots — and that it stays a block, not the 681x999 strip that
    # once set both dimensions of the bounding box.
    cols, rows = machine.STORE_SHAPE["deadman-3d"]
    assert cols * rows >= machine.TAPE_SIZE["deadman-3d"]
    assert cols > 1
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
    # store_dy: each row shortens the hot serial request route one cell and
    # costs one row of height, so the sweep pushes it as deep as the machine's
    # height slack allows. Pin the structure (the store moves down, never
    # sideways — a nonzero dx would cross the request route) and let the depth
    # itself be whatever the current sweep chose.
    (unit_dx, unit_dy), (store_dx, store_dy) = machine.MEM_PLACE["deadman-3d"]
    assert (unit_dx, unit_dy) == (0, 0)
    assert store_dx == 0 and store_dy >= 0


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
    """The title plus all 57 WALK commands — the corridor gallery and the moat
    crossing included, so this covers the kill, the corpse, the green floor,
    the damage drain and the face bands on the real machine."""
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


@slow
def test_a_seeded_gallery_fuzz_shoots_the_monsters() -> None:
    """The scripted climb into the corridor plus 24 seeded chords fired at the
    imps queued in it: kills, corpses, shots through corpses and misses, all
    pixel-equal on the emulator — the net under M7b's hit ladder."""
    rng = random.Random(5)
    pool = [16, 16, 17, 1, 4, 8, 21, 0, 255]
    cmds = list(d3.WALK[:19]) + [rng.choice(pool) for _ in range(24)]
    beats = d3.walk_beats(cmds)
    assert sum(1 for b in beats if b[2]) >= 4, "the fuzz must land shots"
    assert any(h == 0 for h in beats[-1][3]), "and it must kill something"
    assert _emulator_frames(cmds) == [d3.title_frame()] + [b[0] for b in beats]


@slow
def test_a_seeded_moat_fuzz_stands_in_nukage() -> None:
    """The scripted walk to the moat plus 20 seeded chords stirred inside it:
    random keys while standing on damage floors — green floods, the drain,
    the face bands, fire-in-slime — all pixel-equal on the emulator."""
    rng = random.Random(45)
    pool = [0, 1, 2, 4, 8, 16, 17, 21, 3, 255]
    cmds = list(d3.WALK[:52]) + [rng.choice(pool) for _ in range(20)]
    state, nuk_frames = d3.SPAWN, 0
    for cmd in cmds:
        state = d3.step(state, cmd)
        nuk_frames += d3.nukage_cell(
            d3.div(state.posX, d3.UNITS), d3.div(state.posY, d3.UNITS))
    assert nuk_frames >= 12, "the fuzz must actually soak in the moat"
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
    # The STORE_SHAPE + ROM fold + store_dy sweep exists for squareness (down
    # from 756x1197), because the viewer holds the full bounding rectangle.
    # Assert the property, not any particular number — exact-dimension pins
    # went stale on every layout retune without ever catching a bug. The
    # committed artifact pins the actual bytes.
    w, h = max(len(r) for r in m.rows), len(m.rows)
    assert max(w, h) <= 400, (w, h)
    assert max(w, h) - min(w, h) <= max(w, h) // 10, (w, h)


# ── the DOOM unit: the column-painter coprocessor the CPU sends frames to ────
def test_doom_unit_codes_pin_the_trie() -> None:
    """The emulator model's command codes are read off the hardware trie, and
    the generated asm's C_COL must be 0 (the column send is a bare MULI 8)."""
    from randomfun2026solvers.lm1 import d3_unit
    from randomfun2026solvers.lm1.store import DoomUnit

    assert d3_unit.arm_codes() == DoomUnit.CODES == {
        "COL": 0, "CURS": 1, "RUN": 4, "GUN": 3, "GUNF": 6, "COMMIT": 7}
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
    (boot + the title screen) through the corridor gallery — the two shots that
    drop the imp and the corpse frame after them, judged one round at a time.

    This is the one place gating is enforced for real: the Python emulator
    releases every display-problem round up front. The reference wasm session
    API cannot host this machine at all — it retains memory per tick and the
    Go heap dies at 4 GB around ~10M ticks, while one 64x48 frame costs tens
    of millions. FastLittleman streams the committed frames against the
    expected rounds natively, which is exactly the judge's compare.
    """
    from randomfun2026solvers.fast_littleman import FastLittleman

    case = d3.cases_json(d3.WALK[:KILL + 2])["publicTestData"][0]
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    frames = [r["frames"] for r in case["rounds"]]
    res = FastLittleman(MAN).run(inp, frames=frames, max_ticks=400_000_000)
    assert res.fatal is None, res.fatal
    assert res.output == []
    assert res.passed is True


# ── the taped store variant: the same demo with ~20 men instead of ~700 ──────
TAPED_MAN = REPO / "littleman" / "examples" / "deadman-3d_taped.man"


def test_taped_registry_pins() -> None:
    """The taped variant is opt-in: the canonical machine STAYS men-v3, and the
    taped build is the one-liner `build_for("deadman-3d", store="taped")`."""
    assert machine.STORE_TIER["deadman-3d"] == "men-v3"
    # Traffic-shaped plan: the hot high addresses (POWB/HDG at 257..288 plus
    # M5's once-a-frame nukage plane, then POSX and the per-frame scalars up
    # to PTR=394) get small cheap rings.
    # The property, not the split: every slot must land in some bank (a plan
    # that under-covers the tape stalls silently). The exact bank tuple is a
    # tuning outcome that moves with every re-sweep.
    assert sum(machine.TAPED_BANKS["deadman-3d"]) >= machine.TAPE_SIZE["deadman-3d"] - 1
    assert machine.TAPED_SKIP_BATCH["deadman-3d"] == 2
    # No deadman-3d_taped.input.txt: same program, same protocol, same input —
    # the canonical deadman-3d.input.txt drives both machines.
    assert not (TAPED_MAN.parent / "deadman-3d_taped.input.txt").exists()


@slow
def test_checked_in_taped_man_matches_the_machine_builder() -> None:
    rows = TAPED_MAN.read_text().rstrip("\n").split("\n")
    assert machine.build_for("deadman-3d", store="taped").rows == rows


def test_m6_taped_artifact_copies_match_the_taped_files() -> None:
    """littleman/examples/deadman-3d_m6_taped.* are clearly-named byte-identical
    copies of the taped artifacts — the M6 Freedoom level in the 20-man form
    the web-editor workflow watches, shipped as its own family from day one."""
    for stem in ("man", "debug.html", "debug.json"):
        taped = (REPO / "littleman" / "examples" / f"deadman-3d_taped.{stem}").read_bytes()
        m6 = (REPO / "littleman" / "examples" / f"deadman-3d_m6_taped.{stem}").read_bytes()
        assert m6 == taped, f"deadman-3d_m6_taped.{stem} drifted from the taped file"


@slow
def test_the_taped_machine_census_dims_and_first_round_gate() -> None:
    """The variant's whole reason: ~20 little men (the visualizer's metric)
    against the men-v3 store's ~700, in a smaller bounding box than the
    canonical square — and the frames still judge pixel-clean on the native
    engine."""
    m = machine.build_for("deadman-3d", store="taped")
    src = "\n".join(m.rows)
    # Properties, not frozen numbers: the box stays in the canonical size
    # class, and the census stays "a couple dozen men", not the store's ~700.
    assert max(max(len(r) for r in m.rows), len(m.rows)) <= 400
    assert src.count("@") <= 30  # no births: static men ARE the census
    from randomfun2026solvers.fast_littleman import FastLittleman

    case = d3.cases_json(d3.WALK[:1])["publicTestData"][0]
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    frames = [r["frames"] for r in case["rounds"]]
    res = FastLittleman(src).run(inp, frames=frames, max_ticks=300_000_000)
    assert res.fatal is None, res.fatal
    assert res.passed is True


def test_input_txt_is_the_flattened_cases_input() -> None:
    """input.txt must be EVERYTHING the program reads — preamble, title runs,
    commands — i.e. exactly the cases file's rounds flattened. A hand-rolled
    writer once dropped the title words and shipped a 345-word file that left
    the machine blocked at the title painter forever."""
    import json

    rounds = json.loads(
        (REPO / "littleman" / "examples" / "deadman-3d.cases.json").read_text()
    )["publicTestData"][0]["rounds"]
    flat = [w for r in rounds for w in r["in"]]
    txt = (REPO / "littleman" / "examples" / "deadman-3d.input.txt").read_text().split()
    assert txt == flat
    assert txt == [str(w) for w in d3.input_words(list(d3.WALK))]
