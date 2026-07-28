"""The WAD level importer: parser, supercover rasterizer, colour families and
the title quantizer — all on synthetic in-memory data (no real WAD needed; the
committed Freedoom level data in ``deadman3d.py`` is this pipeline's output,
and Mode B's IWAD outputs are local-only by design)."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

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
