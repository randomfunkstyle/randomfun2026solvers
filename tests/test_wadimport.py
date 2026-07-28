"""The WAD level importer: parser, supercover rasterizer, colour families and
the title quantizer — all on synthetic in-memory data (no real WAD needed; the
committed Freedoom level data in ``deadman3d.py`` is this pipeline's output,
and Mode B's IWAD outputs are local-only by design)."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers import wadimport as wi  # noqa: E402


# ── a tiny synthetic PWAD: one square room, a start, four textured walls ─────
def _lump(name: str, data: bytes) -> tuple[bytes, bytes]:
    return name.encode().ljust(8, b"\0"), data


def synthetic_wad() -> bytes:
    """A 256x256 one-room map: four one-sided linedefs (each with its own
    middle texture), a player-1 start at the centre facing north."""
    vertexes = b"".join(struct.pack("<hh", *v)
                        for v in [(0, 0), (256, 0), (256, 256), (0, 256)])
    # (v1, v2, flags, special, tag, right, left) — CW so the right side faces in
    linedefs = b"".join(struct.pack("<HHHHHHH", a, b, 1, 0, 0, s, 0xFFFF)
                        for s, (a, b) in enumerate([(0, 1), (1, 2), (2, 3), (3, 0)]))
    texnames = ["GRAYWALL", "REDWALL", "GREENIE", "BLUEFALL"]
    sidedefs = b"".join(
        struct.pack("<hh8s8s8sH", 0, 0, b"-", b"-", t.encode().ljust(8, b"\0"), 0)
        for t in texnames)
    things = struct.pack("<hhHHH", 128, 128, 90, 1, 7)
    sectors = struct.pack("<hh8s8shhh", 0, 128, b"FLAT1", b"FLAT2", 160, 0, 0)
    lumps = [
        _lump("E1M1", b""),
        _lump("THINGS", things),
        _lump("LINEDEFS", linedefs),
        _lump("SIDEDEFS", sidedefs),
        _lump("VERTEXES", vertexes),
        _lump("SECTORS", sectors),
    ]
    body = b""
    directory = b""
    offset = 12 + 16 * len(lumps)
    for name, data in lumps:
        directory += struct.pack("<II", offset + len(body), len(data)) + name
        body += data
    return struct.pack("<4sII", b"PWAD", len(lumps), 12) + directory + body


def test_wad_directory_and_map_lumps_parse() -> None:
    wad = wi.read_wad(synthetic_wad())
    assert wad.ident == "PWAD"
    assert [n for n, _ in wad.lumps][:3] == ["E1M1", "THINGS", "LINEDEFS"]
    m = wi.parse_map(wad, "E1M1")
    assert m.vertexes == [(0, 0), (256, 0), (256, 256), (0, 256)]
    assert len(m.linedefs) == 4 and all(ld[6] == wi.NO_SIDE for ld in m.linedefs)
    assert m.sidedefs[1][2] == "REDWALL"
    assert m.things == [(128, 128, 90, 1, 7)]


def test_rasterize_is_watertight_with_the_real_spawn() -> None:
    m = wi.parse_map(wi.read_wad(synthetic_wad()), "E1M1")
    r = wi.rasterize(m, grid=64, min_len=32.0)
    # The spawn is the room centre, heading north (angle 90 -> 4).
    assert r.spawn == (32, 32) and r.heading == 4
    assert r.spawn in r.open_cells
    # Watertight: nothing reachable touches the border, and after the fill
    # every cell is wall or reachable-open.
    for x, y in r.open_cells:
        assert 0 < x < 63 and 0 < y < 63
    assert all((c in r.cells) != (c in r.open_cells)
               for c in ((x, y) for x in range(64) for y in range(64)))
    # Each wall keeps its own side's texture.
    assert r.cells[(32, 1)] == "GRAYWALL"    # south wall (v 0->1)
    assert r.cells[(62, 32)] == "REDWALL"    # east
    assert r.cells[(32, 62)] == "GREENIE"    # north
    assert r.cells[(1, 32)] == "BLUEFALL"    # west
    assert r.bbox == (0, 0, 256, 256)


def test_supercover_blocks_diagonal_leaks() -> None:
    """A diagonal segment's cover admits no 4-connected crossing, corner
    crossings included (both side cells are added)."""
    cells = wi.supercover(0.0, 0.0, 8.0, 8.0)
    assert {(k, k) for k in range(8)} <= cells
    assert all((k, k - 1) in cells or (k - 1, k) in cells for k in range(1, 8))
    # An exact corner crossing covers both flanking cells.
    assert (0, 1) in cells or (1, 0) in cells


def test_family_of_is_hue_with_a_chroma_gate() -> None:
    assert wi.family_of(None) == 7
    assert wi.family_of((48, 46, 43)) == 7      # warm dark metal: gray
    assert wi.family_of((112, 81, 48)) == 3     # brown
    assert wi.family_of((27, 90, 19)) == 2      # green
    assert wi.family_of((0, 0, 92)) == 4        # blue
    assert wi.family_of((170, 20, 20)) == 1     # red
    assert wi.PALETTE == d3.PALETTE


# ── the PNG codec (the subset Freedoom's patches use) ────────────────────────
def _png(width: int, height: int, rows: bytes, *, ctype: int, bitd: int = 8,
         plte: bytes = b"", trns: bytes = b"") -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body)))

    out = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, bitd, ctype, 0, 0, 0))
    if plte:
        out += chunk(b"PLTE", plte)
    if trns:
        out += chunk(b"tRNS", trns)
    return out + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def test_png_decode_rgb_paeth_and_palette() -> None:
    # 2x2 RGB, filter 0 rows.
    raw = b"\x00" + bytes([255, 0, 0, 0, 255, 0]) + b"\x00" + bytes([0, 0, 255, 85, 85, 85])
    px = wi.decode_png(_png(2, 2, raw, ctype=2))
    assert px[0] == [(255, 0, 0, 255), (0, 255, 0, 255)]
    assert px[1] == [(0, 0, 255, 255), (85, 85, 85, 255)]
    # 4x1 palette at bit depth 4 with tRNS: indices 0..2, transparent 0.
    pal = bytes([10, 20, 30, 40, 50, 60, 70, 80, 90])
    raw = b"\x00" + bytes([0x01, 0x20])
    px = wi.decode_png(_png(4, 1, raw, ctype=3, bitd=4, plte=pal, trns=b"\x00"))
    assert px[0][0] == (10, 20, 30, 0)
    assert px[0][1] == (40, 50, 60, 255)
    assert px[0][2] == (70, 80, 90, 255)


def test_doom_picture_decode() -> None:
    """A 2x3 picture: one post per column, top-delta 1 in column 1."""
    playpal = bytes([0, 0, 0, 255, 0, 0, 0, 255, 0]) + bytes(759)
    col0 = bytes([0, 3, 0, 1, 2, 1, 0, 0xFF])  # rows 0..2 = pal 1,2,1
    col1 = bytes([1, 2, 0, 2, 2, 0, 0xFF])     # rows 1..2 = pal 2,2
    header = struct.pack("<HHhh", 2, 3, 0, 0) + struct.pack("<II", 16, 16 + len(col0))
    px = wi.decode_picture(header + col0 + col1, playpal)
    assert px[0] == [(255, 0, 0, 255), (0, 0, 0, 0)]
    assert px[1] == [(0, 255, 0, 255), (0, 255, 0, 255)]
    assert px[2] == [(255, 0, 0, 255), (0, 255, 0, 255)]


def test_title_quantize_shape_despeckle_and_brightness() -> None:
    # A flat dark-red field quantizes to dark red everywhere (the x1.6 lift
    # keeps it out of black), with one white dot despeckled away.
    rgba = [[(120, 0, 0, 255)] * 320 for _ in range(200)]
    rgba[100][160] = (255, 255, 255, 255)
    rows = wi.quantize_title(rgba)
    assert len(rows) == 48 and all(len(r) == 64 for r in rows)
    assert set("".join(rows)) == {"1"}


def test_install_level_swaps_the_module_and_restores() -> None:
    """deadman3d.install_level rebinds the map/spawn/title globals (the --wad
    path); everything downstream follows the new level."""
    keep = (d3.MAP_STR, d3._PRINTED_ROWS, d3._MAP_WORDS, d3.SPAWN,
            d3.TITLE_HEX_ROWS, d3._WAD_INSTALLED)
    m = wi.parse_map(wi.read_wad(synthetic_wad()), "E1M1")
    r = wi.rasterize(m)
    level = wi._finish_level(
        r, {"GRAYWALL": (100.0, 100.0, 100.0), "REDWALL": (170.0, 20.0, 20.0),
            "GREENIE": (27.0, 90.0, 19.0), "BLUEFALL": (0.0, 0.0, 92.0)},
        ["1" * 64] * 48, "synthetic")
    try:
        d3.install_level(level.map_rows, level.spawn, level.heading, level.title_rows)
        assert d3.SPAWN == d3.State(posX=32 * 1024 + 512, posY=32 * 1024 + 512, heading=4)
        assert d3.map_cell(32, 62) == 2 and d3.map_cell(62, 32) == 1
        assert d3.title_frame() == ["1" * 64] * 48
        # A render straight off the imported map: 48 rows, viewport + HUD.
        frame = d3.render(d3.SPAWN)
        assert len(frame) == 48 and all(len(row) == 64 for row in frame)
        # The one-room map: the north GREENIE wall is dead ahead (dark 2,
        # beyond NEAR_D) somewhere on the horizon rows.
        assert "2" in frame[d3.MID]
    finally:
        (d3.MAP_STR, d3._PRINTED_ROWS, d3._MAP_WORDS, d3.SPAWN,
         d3.TITLE_HEX_ROWS, d3._WAD_INSTALLED) = keep


def test_committed_freedoom_map_is_a_wadimport_shape() -> None:
    """Structural invariants the committed MAP_STR keeps from the pipeline:
    watertight border, spawn open, all families in 1..7."""
    for i in range(64):
        assert d3.map_cell(i, 0) > 0 and d3.map_cell(0, i) > 0
        assert d3.map_cell(i, 63) > 0 and d3.map_cell(63, i) > 0
    sx, sy = d3.SPAWN.posX // 1024, d3.SPAWN.posY // 1024
    assert d3.map_cell(sx, sy) == 0


# ── damage floors (M5): SECTORS + the region flood fill ──────────────────────
def two_room_wad(*, special: int = 7, floorpic: bytes = b"NUKAGE1") -> bytes:
    """Two 256x256 rooms joined by a two-sided linedef; room B's sector is a
    damage floor.  Wound like a real level: walking v1->v2, the right sidedef
    faces its own sector (y-up, right = (dy, -dx))."""
    verts = [(0, 0), (0, 256), (256, 256), (256, 0), (512, 256), (512, 0)]
    vertexes = b"".join(struct.pack("<hh", *v) for v in verts)
    NO = 0xFFFF
    #      v1 v2 flags special tag right left
    lds = [
        (0, 1, 1, 0, 0, 0, NO),   # A west
        (1, 2, 1, 0, 0, 1, NO),   # A north
        (3, 0, 1, 0, 0, 2, NO),   # A south
        (2, 3, 4, 0, 0, 3, 4),    # the JOIN: right faces A, left faces B
        (2, 4, 1, 0, 0, 5, NO),   # B north
        (4, 5, 1, 0, 0, 6, NO),   # B east
        (5, 3, 1, 0, 0, 7, NO),   # B south
    ]
    linedefs = b"".join(struct.pack("<HHHHHHH", *ld) for ld in lds)
    #             (sector of each sidedef above, in order)
    sd_sectors = [0, 0, 0, 0, 1, 1, 1, 1]
    sidedefs = b"".join(
        struct.pack("<hh8s8s8sH", 0, 0, b"-", b"-",
                    b"-" if i == 3 or i == 4 else b"STARTAN3", s)
        for i, s in enumerate(sd_sectors))
    things = struct.pack("<hhHHH", 128, 128, 0, 1, 7)
    sectors = (struct.pack("<hh8s8shhh", 0, 128, b"FLOOR4_8", b"CEIL3_5", 160, 0, 0)
               + struct.pack("<hh8s8shhh", -8, 128, floorpic, b"CEIL3_5", 160, special, 0))
    lumps = [
        _lump("E1M1", b""),
        _lump("THINGS", things),
        _lump("LINEDEFS", linedefs),
        _lump("SIDEDEFS", sidedefs),
        _lump("VERTEXES", vertexes),
        _lump("SECTORS", sectors),
    ]
    body = b""
    directory = b""
    offset = 12 + 16 * len(lumps)
    for name, data in lumps:
        directory += struct.pack("<II", offset + len(body), len(data)) + name
        body += data
    return struct.pack("<4sII", b"PWAD", len(lumps), 12) + directory + body


def test_sectors_parse_and_nukage_region_resolution() -> None:
    """The SECTORS lump decodes; the region flood fill marks room B's cells
    (special 7 — the 5% damage floor) and leaves room A clean."""
    m = wi.parse_map(wi.read_wad(two_room_wad()), "E1M1")
    assert m.sectors == [
        (0, 128, "FLOOR4_8", "CEIL3_5", 160, 0, 0),
        (-8, 128, "NUKAGE1", "CEIL3_5", 160, 7, 0),
    ]
    r = wi.rasterize(m, grid=64, min_len=32.0)
    assert r.nukage, "room B must resolve to nukage"
    assert r.nukage_stats["nukage_by"] == "specials"
    assert r.nukage_stats["unresolved_regions"] == 0
    # A cell deep in each room: B's is nukage, A's (the spawn's) is not.
    assert r.spawn not in r.nukage
    gx = round((384 / 512) * 62) + 1   # room B's centre, roughly, on the grid
    gy = round((128 / 512) * 62) + 8
    hits = [c for c in r.nukage if abs(c[0] - gx) <= 3]
    assert hits, f"no nukage near room B's centre column {gx}"
    # Every nukage cell is an open cell (never a wall).
    assert r.nukage <= r.open_cells


def monster_wad(things: list[tuple[int, int, int, int]]) -> bytes:
    """synthetic_wad's one room, with a chosen THINGS list appended after the
    player start: each entry is ``(x, y, type, flags)``."""
    base = synthetic_wad()
    wad = wi.read_wad(base)
    lumps = []
    for name, data in wad.lumps:
        if name == "THINGS":
            data = data + b"".join(
                struct.pack("<hhHHH", x, y, 0, mtype, flags)
                for x, y, mtype, flags in things)
        lumps.append(_lump(name, data))
    body = b""
    directory = b""
    offset = 12 + 16 * len(lumps)
    for name, data in lumps:
        directory += struct.pack("<II", offset + len(body), len(data)) + name
        body += data
    return struct.pack("<4sII", b"PWAD", len(lumps), 12) + directory + body


def test_monster_things_are_filtered_mapped_and_capped() -> None:
    """THINGS -> billboards: only the known monster types survive, and only
    those present on medium skill, outside the multiplayer-only set, standing
    on an open cell, one per cell, capped in THINGS order."""
    inside = [(96 + 8 * i, 96, 3004, 7) for i in range(3)]  # three zombiemen
    things = inside + [
        (160, 160, 3001, 7),    # an imp -> species 1
        (170, 170, 9, 7),       # a sergeant -> species 0
        (180, 180, 3004, 1),    # skill-1 only: flags & 2 clear -> dropped
        (190, 190, 3004, 0x17),  # multiplayer-only -> dropped
        (4, 4, 3004, 7),        # inside the south-west wall -> dropped
        (2001, 2001, 2005, 7),  # a chainsaw: not a monster type at all
    ]
    m = wi.parse_map(wi.read_wad(monster_wad(things)), "E1M1")
    r = wi.rasterize(m, grid=64, min_len=32.0)
    st = r.monster_stats
    assert st["monster_things"] == len(things) - 1  # the chainsaw never counts
    assert st["skill_dropped"] == 2
    assert st["closed_dropped"] == 1
    assert st["monsters_kept"] == len(r.monsters)
    # Species mapping, and every survivor on open floor.
    assert {s for _cx, _cy, s in r.monsters} == {0, 1}
    for cx, cy, species in r.monsters:
        assert (cx, cy) in r.open_cells
        assert species in (0, 1)
    # One billboard per cell: the three zombiemen 8 map units apart collapse
    # into however many distinct grid cells they land in, no more.
    assert len({(cx, cy) for cx, cy, _s in r.monsters}) == len(r.monsters)
    # Nothing is lost silently: every counted thing was kept or dropped for a
    # named reason.
    assert (st["monsters_kept"] + st["skill_dropped"] + st["closed_dropped"]
            + st["dupe_dropped"] + st["cap_dropped"]) == st["monster_things"]


def test_monster_things_cap_at_sixteen() -> None:
    """Past the cap the extra THINGS are counted, not silently forgotten."""
    many = [(48 + 24 * (i % 7), 48 + 24 * (i // 7), 3001, 7) for i in range(42)]
    r = wi.rasterize(wi.parse_map(wi.read_wad(monster_wad(many)), "E1M1"),
                     grid=64, min_len=32.0)
    assert len(r.monsters) == wi.MAX_MONSTERS == 16
    assert r.monster_stats["cap_dropped"] > 0
    assert (r.monster_stats["monsters_kept"] + r.monster_stats["cap_dropped"]
            + r.monster_stats["dupe_dropped"] + r.monster_stats["closed_dropped"]
            + r.monster_stats["skill_dropped"]) == r.monster_stats["monster_things"]


def test_nukage_flat_name_fallback_is_documented() -> None:
    """With no damage special anywhere but a NUKAGE* flat, the documented
    fallback marks the region by flat name and says so in the stats."""
    m = wi.parse_map(wi.read_wad(two_room_wad(special=0)), "E1M1")
    r = wi.rasterize(m, grid=64, min_len=32.0)
    assert r.nukage
    assert r.nukage_stats["nukage_by"] == "flats"


# ── sprite art (M5): the run splitter and the --wad art tables ───────────────
def _run_tokens(colors: str) -> tuple[int, int]:
    """(lead, body) token counts exactly as d3_unit._pixel_tokens builds them."""
    toks: list[str] = []
    cur = None
    for ch in colors:
        c = int(ch, 16)
        if ch != cur:
            toks += ["%d" % c] if c < 10 else ["%d" % (c - 8), "~"]
            cur = ch
        toks.append("s")
    first = toks.index("s")
    return first, len(toks) - first


def test_sprite_runs_split_to_the_descent_window() -> None:
    """A busy row splits so every run's body fits the DATA window (12 rows),
    the replay is lossless, and transparency breaks runs."""
    row: list[int | None] = [1, 2, 3, 4, 5, 10, 11, 12, 13, 14, 15, 0, 0, None, 7]
    runs = wi.sprite_runs([row], 30, 20)
    assert len(runs) >= 3
    replay: dict[int, int] = {}
    for r, c, colors in runs:
        assert r == 30
        lead, body = _run_tokens(colors)
        assert lead <= 2 and body <= 12
        for i, ch in enumerate(colors):
            replay[c + i] = int(ch, 16)
    assert replay == {20 + i: v for i, v in enumerate(row) if v is not None}


def test_gun_and_face_tables_fit_the_unit_budgets() -> None:
    """Synthetic pistol + flash quantize into <= 20 runs placed like the
    committed art (idle bottom row 39, flash above the recoiled gun), and the
    face tables are one full-width opaque run per row in the face box."""
    gun = [[(200, 60, 40, 255)] * 22 for _ in range(20)]
    flash = [[(255, 255, 120, 255)] * 8 for _ in range(6)]
    idle, fire = wi.gun_tables(gun, flash)
    assert 1 <= len(idle) <= 20 and 1 <= len(fire) <= 20
    assert max(r for r, _c, _s in idle) == 39          # bottom-anchored
    assert max(r for r, _c, _s in fire) == 38          # recoiled one row up
    assert min(r for r, _c, _s in fire) < min(r for r, _c, _s in idle) - 1
    face = wi.face_tables({"healthy": [[(180, 140, 100, 255)] * 24 for _ in range(29)]})
    runs = face["healthy"]
    assert [r for r, _c, _s in runs] == list(range(wi.FACE_ROW, wi.FACE_ROW + wi.FACE_H))
    assert all(c == wi.FACE_COL and len(s) == wi.FACE_W for _r, c, s in runs)


# ── the status bar (M8) ──────────────────────────────────────────────────────
def _flat_bar(grey: int = 70) -> list[list[tuple[int, int, int, int]]]:
    return [[(grey, grey, grey, 255)] * wi.STBAR_W for _ in range(wi.STBAR_H)]


def test_stbar_cells_scale_dooms_own_layout_onto_the_strip() -> None:
    """Every region is st_stuff.c's real placement over 5 horizontally and 4
    vertically — inside the strip, non-empty, and in DOOM's left-to-right
    order (ammo, health, ARMS, the mugshot, armor, keys, the ammo table)."""
    order = ["ammo", "health", "arms", "face", "armor", "keys", "ammos"]
    assert list(wi.STBAR_REGIONS) == order
    prev_x = -1
    for name in order:
        x0, y0, x1, y1 = wi.STBAR_REGIONS[name]
        assert 0 <= x0 < x1 <= wi.STBAR_W and 0 <= y0 < y1 <= wi.STBAR_H
        assert x0 > prev_x
        prev_x = x0
        c0, r0, c1, r1 = wi.stbar_cells(name)
        assert 0 <= c0 < c1 <= wi.HUD_W and 0 <= r0 < r1 <= wi.HUD_H
        # the scaling really is /5 and /4, to the nearest cell boundary
        assert (c0, r0, c1, r1) == (round(x0 / 5), round(y0 / 4),
                                    round(x1 / 5), round(y1 / 4))
    # The two number wells the demo draws in cannot overlap, and the mugshot
    # inset sits between the ARMS panel and the armor well.
    assert wi.stbar_cells("ammo")[2] <= wi.stbar_cells("health")[0]
    assert wi.stbar_cells("arms")[2] <= wi.stbar_cells("face")[0]
    assert wi.stbar_cells("face")[2] <= wi.stbar_cells("armor")[0]


def test_stbar_rows_quantize_the_bar_onto_the_strip() -> None:
    """A synthetic 320x32 bar lands as 8 rows of 64 hex digits, and the
    auto-exposure normalizes SOURCE BRIGHTNESS away: two flat bars of very
    different greys quantize to the same strip."""
    rows = wi.stbar_rows(_flat_bar(70))
    assert len(rows) == wi.HUD_H and all(len(r) == wi.HUD_W for r in rows)
    assert set("".join(rows)) <= set("0123456789abcdef")
    assert rows == wi.stbar_rows(_flat_bar(40)) == wi.stbar_rows(_flat_bar(110))
    # …and the exposure lands the mean where STBAR_MEAN_GREY asks: a flat bar
    # scaled to 95 is nearest ANSI 8 (85,85,85), not black.
    assert set("".join(rows)) == {"8"}


def test_stbar_rows_keep_the_bars_own_structure() -> None:
    """A bar with a dark inset where DOOM's mugshot sits quantizes with that
    inset still dark, at the cells stbar_cells("face") predicts."""
    src = _flat_bar(110)
    x0, y0, x1, y1 = wi.STBAR_REGIONS["face"]
    for y in range(y0, y1):
        for x in range(x0, x1):
            src[y][x] = (0, 0, 0, 255)
    rows = wi.stbar_rows(src)
    c0, r0, c1, r1 = wi.stbar_cells("face")
    for r in range(r0, r1 - 1):          # the inset's last row is a part-cell
        assert rows[r][c0:c1] == "0" * (c1 - c0)
    assert rows[0][:c0] == "8" * c0      # the field around it is untouched
    assert wi.stbar_rows(src) != wi.stbar_rows(_flat_bar(110))


def test_stbar_rows_reject_a_black_bar() -> None:
    with pytest.raises(ValueError):
        wi.stbar_rows([[(0, 0, 0, 255)] * wi.STBAR_W for _ in range(wi.STBAR_H)])


def test_iwad_stbar_prefers_the_whole_lump_over_the_v1_halves() -> None:
    """v1.2+ IWADs carry one 320x32 STBAR; v1.0/v1.1 shareware has only the
    104 + 216 halves.  iwad_stbar reads whichever is there, and both spell the
    same geometry."""
    playpal = bytes([0, 0, 0]) + bytes([200, 200, 200]) + bytes(762)

    def flat(w: int, h: int, idx: int) -> bytes:
        post = bytes([0, h]) + b"\0" + bytes([idx]) * h + b"\0" + b"\xff"
        offs, at = [], 8 + 4 * w
        for _ in range(w):
            offs.append(at)
            at += len(post)
        return (struct.pack("<HHhh", w, h, 0, 0)
                + b"".join(struct.pack("<I", o) for o in offs) + post * w)

    halves = wi.Wad("IWAD", [("STMBARL", flat(104, 32, 0)),
                             ("STMBARR", flat(216, 32, 1))])
    bar = wi.iwad_stbar(halves, playpal)
    assert len(bar) == wi.STBAR_H and len(bar[0]) == wi.STBAR_W
    assert bar[0][0][:3] == (0, 0, 0) and bar[0][104][:3] == (200, 200, 200)
    whole = wi.Wad("IWAD", [("STBAR", flat(320, 32, 1)),
                            ("STMBARL", flat(104, 32, 0)),
                            ("STMBARR", flat(216, 32, 0))])
    got = wi.iwad_stbar(whole, playpal)
    assert all(px[:3] == (200, 200, 200) for px in got[0]), "STBAR wins"


def test_iwad_art_extracts_the_named_lumps() -> None:
    """A synthetic IWAD carrying PLAYPAL + tiny picture lumps for the pistol
    and face family round-trips through iwad_art (Mode B's art override)."""
    playpal = bytes([0, 0, 0]) + bytes([180, 60, 40]) + bytes([250, 250, 120]) + bytes(759)

    def picture(w: int, h: int, idx: int) -> bytes:
        cols = []
        post = bytes([0, h]) + b"\0" + bytes([idx]) * h + b"\0" + b"\xff"
        header = struct.pack("<HHhh", w, h, 0, 0)
        offs = []
        at = 8 + 4 * w
        for _ in range(w):
            offs.append(at)
            at += len(post)
        return header + b"".join(struct.pack("<I", o) for o in offs) + post * w

    lumps = [_lump("PLAYPAL", playpal)]
    for key, name in wi.IWAD_ART_LUMPS.items():
        idx = 2 if key == "gun_flash" else 1
        lumps.append(_lump(name, picture(16, 16, idx)))
    # M8: no STBAR here — the v1.0 shareware halves path has to carry the bar.
    lumps.append(_lump(wi.IWAD_STBAR_HALVES[0], picture(104, 32, 1)))
    lumps.append(_lump(wi.IWAD_STBAR_HALVES[1], picture(216, 32, 2)))
    body = b""
    directory = b""
    offset = 12 + 16 * len(lumps)
    for name, data in lumps:
        directory += struct.pack("<II", offset + len(body), len(data)) + name
        body += data
    wad = struct.pack("<4sII", b"IWAD", len(lumps), 12) + directory + body
    tmp = Path(__file__).parent / "_tmp_art.wad"
    tmp.write_bytes(wad)
    try:
        art = wi.iwad_art(tmp)
    finally:
        tmp.unlink()
    assert set(art) == {"gun_idle", "gun_fire", "faces", "hud_bg",
                        "monster_sprites"}
    assert set(art["faces"]) == {"healthy", "hurt", "bloody", "grim"}
    # M8: the status bar came out of the v1.0 halves, composited 104 + 216.
    assert len(art["hud_bg"]) == wi.HUD_H
    assert all(len(r) == wi.HUD_W for r in art["hud_bg"])
    assert len(set(art["hud_bg"][0])) == 2, "the two halves must stay apart"
    # M7a: the same override path carries the monster billboards — one packed
    # word per sprite column, every word inside the signed range.
    assert all(0 <= w < 2**63 for w in art["monster_sprites"])
    assert 1 <= len(art["gun_idle"]) <= 20 and 1 <= len(art["gun_fire"]) <= 20
    for runs in art["faces"].values():
        assert len(runs) == wi.FACE_H and all(len(s) == wi.FACE_W for _r, _c, s in runs)


# ── M9: dithering (opt-in) and the hi-res art geometry ───────────────────────
def _gradient(w: int = 320, h: int = 200) -> list[list[tuple[int, int, int, int]]]:
    """A horizontal black->white ramp: the case a 16-colour palette bands."""
    return [[(round(255 * x / (w - 1)),) * 3 + (255,) for x in range(w)]
            for _ in range(h)]


def test_bayer_matrix_is_the_recursive_index_matrix() -> None:
    """M_2n = [[4M, 4M+2], [4M+3, 4M+1]]: every side is a permutation of
    0..n*n-1, so the thresholds are evenly spread."""
    assert wi.bayer_matrix(1) == [[0]]
    assert wi.bayer_matrix(2) == [[0, 2], [3, 1]]
    assert wi.bayer_matrix(4)[0] == [0, 8, 2, 10]
    for n in (2, 4, 8):
        m = wi.bayer_matrix(n)
        assert len(m) == n and all(len(row) == n for row in m)
        assert sorted(v for row in m for v in row) == list(range(n * n))
    with pytest.raises(ValueError):
        wi.bayer_matrix(6)


def test_dithering_is_opt_in_and_off_by_default() -> None:
    """The M9 path must never move a committed byte: the default call, an
    explicit ``dither="none"`` and ``strength=0`` all agree, and every
    quantizer defaults to off."""
    src = _gradient()
    base = wi.quantize_title(src)
    assert base == wi.quantize_title(src, dither="none")
    cells = wi.block_lab(src, 64, 48, brightness=wi.TITLE_BRIGHTNESS)
    flat = wi.dither_lab(cells, "none")
    for mode in ("fs", "bayer4", "bayer8"):
        assert wi.dither_lab(cells, mode, strength=0.0) == flat, mode
    sprite = [[(90, 40, 20, 255)] * 8 for _ in range(8)]
    assert wi.quantize_sprite(sprite, 4, 4) == \
        wi.quantize_sprite(sprite, 4, 4, dither="none")
    assert wi.stbar_rows(_flat_bar(70)) == wi.stbar_rows(_flat_bar(70), dither="none")
    with pytest.raises(ValueError):
        wi.dither_lab(cells, "sierra")


def test_dithering_buys_colours_on_a_gradient() -> None:
    """The point of the exercise: on a ramp the undithered art bands into flat
    slabs, and every dither mode MIXES the band boundaries (so 16 colours read
    as more) and reproduces the ramp's mean better."""
    src = _gradient()
    rows = {m: wi.quantize_title(src, dither=m) for m in wi.DITHER_MODES}

    def mixes(hexrows: list[str]) -> int:
        """Cells whose left and right neighbours agree with each other but not
        with them — a stipple; a banded ramp has none."""
        return sum(1 for row in hexrows for x in range(1, len(row) - 1)
                   if row[x - 1] == row[x + 1] != row[x])

    assert mixes(rows["none"]) == 0, "the committed method bands, it never mixes"
    for mode in ("fs", "bayer4", "bayer8"):
        assert mixes(rows[mode]) > 100, mode
        assert len(rows[mode]) == 48 and all(len(r) == 64 for r in rows[mode])

    def mean_l(hexrows: list[str]) -> float:
        vals = [wi._LAB[int(c, 16)][0] for row in hexrows for c in row]
        return sum(vals) / len(vals)

    want = sum(v[0] for row in wi.block_lab(src, 64, 48,
                                            brightness=wi.TITLE_BRIGHTNESS)
               for v in row) / (64 * 48)
    for mode in ("fs", "bayer4", "bayer8"):
        assert abs(mean_l(rows[mode]) - want) < abs(mean_l(rows["none"]) - want), mode


def test_a_flat_palette_colour_never_dithers() -> None:
    """A field that is EXACTLY a palette entry has nothing to mix, so no mode
    breaks it up — which is what keeps flat art's RLE runs intact."""
    for idx in (1, 7, 8):
        flat = [[wi.PALETTE[idx] + (255,)] * 64 for _ in range(64)]
        for mode in wi.DITHER_MODES:
            rows = wi.quantize_title(flat, 16, 16, brightness=1.0, dither=mode)
            assert set("".join(rows)) == {format(idx, "x")}, (idx, mode)
            assert wi.rom_words(rows) == 1


def test_ordered_dither_is_periodic_and_floyd_steinberg_is_not() -> None:
    """On a uniform field between two palette entries, Bayer repeats with the
    matrix period (the property that makes it RLE- and flicker-friendly);
    Floyd–Steinberg's diffusion does not."""
    mid = tuple(round((a + b) / 2) for a, b in zip(wi.PALETTE[8], wi.PALETTE[7], strict=True))
    field = [[mid + (255,)] * 128 for _ in range(128)]
    for mode, n in (("bayer4", 4), ("bayer8", 8)):
        rows = wi.quantize_title(field, 32, 32, brightness=1.0, dither=mode)
        assert len(set("".join(rows))) == 2, mode
        for y in range(32 - n):
            assert rows[y] == rows[y + n], mode
        assert rows[0][:n] * (32 // n) == rows[0]
    fs = wi.quantize_title(field, 32, 32, brightness=1.0, dither="fs")
    assert len(set("".join(fs))) >= 2
    assert wi.rom_words(fs) > wi.rom_words(
        wi.quantize_title(field, 32, 32, brightness=1.0, dither="bayer8"))


def test_dithered_sprites_keep_their_transparency() -> None:
    """A dithered sprite still reports ``None`` for a mostly-transparent
    block, so ``sprite_runs`` sees the same outline."""
    src = [[(200, 60, 30, 255 if 4 <= x < 12 else 0) for x in range(16)]
           for _ in range(16)]
    for mode in wi.DITHER_MODES:
        grid = wi.quantize_sprite(src, 8, 8, dither=mode)
        assert all(row[:2] == [None, None] and row[6:] == [None, None]
                   for row in grid), mode
        assert all(c is not None for row in grid for c in row[2:6]), mode


def test_rom_words_is_the_consumers_own_run_encoding() -> None:
    """The cost model dithering is priced against: one pre-encoded RUN command
    word per row-major RLE run — exactly ``deadman3d.title_words``."""
    assert wi.rle_runs(["11", "12"]) == [(1, 3), (2, 1)]
    assert wi.rom_words([]) == 0
    assert wi.rom_words(d3.TITLE_HEX_ROWS) == len(d3.title_words())
    assert [(c, n) for c, n in wi.rle_runs(d3.TITLE_HEX_ROWS)] == d3.title_runs()


def test_hires_art_geometry_doubles_the_panel() -> None:
    """The 128x96 constants and the face box derived for them: DOOM's own
    inset scaled onto a 16-row strip, roughly twice the 6x7 slot."""
    assert (wi.HIRES_W, wi.HIRES_H, wi.HIRES_H3D) == (128, 96, 80)
    assert (wi.HIRES_HUD_W, wi.HIRES_HUD_H) == (128, 16)
    assert wi.face_box() == (wi.FACE_COL, wi.FACE_ROW, wi.FACE_W, wi.FACE_H)
    assert wi.face_box() == (29, 40, 6, 7)
    col, row, fw, fh = wi.face_box(wi.HIRES_HUD_W, wi.HIRES_HUD_H, wi.HIRES_H3D)
    assert (col, row, fw, fh) == (58, 80, 13, 14)
    assert row == wi.HIRES_H3D and col + fw <= wi.HIRES_W
    assert fw >= 2 * wi.FACE_W - 1 and fh >= 2 * wi.FACE_H - 1
    # the hi-res title and strip come out at the sizes asked for
    rows = wi.quantize_title(_gradient(), wi.HIRES_W, wi.HIRES_H, dither="bayer8")
    assert len(rows) == wi.HIRES_H and all(len(r) == wi.HIRES_W for r in rows)
    bar = wi.stbar_rows(_flat_bar(70), wi.HIRES_HUD_W, wi.HIRES_HUD_H)
    assert len(bar) == wi.HIRES_HUD_H and all(len(r) == wi.HIRES_HUD_W for r in bar)


def test_face_tables_take_a_box_and_a_dither() -> None:
    """The hi-res face lands in the hi-res inset, one run string per row."""
    faces = {n: [[(150, 80, 60, 255)] * 24 for _ in range(29)]
             for n in ("healthy", "hurt", "bloody", "grim")}
    box = wi.face_box(wi.HIRES_HUD_W, wi.HIRES_HUD_H, wi.HIRES_H3D)
    tabs = wi.face_tables(faces, box=box, dither="bayer8")
    for runs in tabs.values():
        assert len(runs) == box[3]
        assert [r for r, _c, _s in runs] == list(range(box[1], box[1] + box[3]))
        assert all(c == box[0] and len(s) == box[2] for _r, c, s in runs)
