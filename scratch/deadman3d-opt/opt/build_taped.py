#!/usr/bin/env python3
"""Build `deadman-3d_hires` **taped** and dump everything the cost model needs.

Two products, both into /tmp (everything here is IWAD-derived):

* ``<out>.man``      -- the grid, so :mod:`profile_taped` can run it.
* ``<out>.geom.json``-- regions, the CPU room's cells, and the derived knobs
  (``lane_x0``, ``mem_x``, collector row, drop columns, lane rows, centre).

Knobs are passed as a JSON dict on the command line and restored afterwards, so
the module is left exactly as it shipped.

Usage:  python build_taped.py /tmp/taped '{"pad": 2}'
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "solvers" / "python"))

SLUG = "deadman-3d_hires"
TIER = "taped"
KEY = (SLUG, TIER)


def setup():
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.lm1 import machine as M
    from randomfun2026solvers.lm1.asm import assemble

    hires.install_wad(Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD")
    M.TAPE_SIZE[SLUG] = max(d3.tape_slots(d3.GEOM128).values()) + 1
    prog = assemble(hires.hires_source(), name=SLUG)
    return d3, hires, M, prog


def shipped(M):
    return {
        "pad": M.MEM_PAD_FOR.get(KEY),
        "in_west": M.INPUT_NORTH_WEST.get(KEY),
        "drop": M.ROM_TOUCH_DROP.get(KEY, 0),
        "squash": M.SQUASH_BAND.get(KEY, 0),
        "store_dy": M.TIER_LAYOUT[KEY]["store_offset"][1],
        "ranks": M.TRIE_SLACK_ROWS.get(KEY, "<unset>"),
        "lane_pitch": M.LANE_PITCH.get(KEY, "<unset>"),
    }


def relabel(M, order):
    """The rank-preserving ``OPCODE_SLOTS`` for a new north-south ``order``.

    ``build`` refuses a slot map that reorders the lanes, so a new
    :data:`LANE_ORDER` needs its map re-derived.  Keeping the *same slot set* and
    re-zipping it against the new order is the one relabelling that changes
    nothing geometric at all: the used slots decide the trie, so rows, ``T(y)``,
    ``lane_x0`` and every drop column stay exactly where they were, and only
    ``number = bitrev(slot)`` -- the ROM encoding -- moves.
    """
    slots = sorted(M.OPCODE_SLOTS[KEY].values())
    assert len(slots) == len(order), (len(slots), len(order))
    return dict(zip(order, slots))


def apply_knobs(M, kn):
    saved = {}
    lay = M.TIER_LAYOUT[KEY]
    saved["_store"] = lay["store_offset"]
    if "store_dy" in kn:
        lay["store_offset"] = (saved["_store"][0], kn["store_dy"])
    saved["_order"] = M.LANE_ORDER.get(SLUG, "<unset>")
    saved["_slots"] = dict(M.OPCODE_SLOTS[KEY])
    if "order" in kn:
        full = list(kn["order"])
        M.OPCODE_SLOTS[KEY] = relabel(M, full)
        # ``middle_order`` is the *unpinned* lanes only; plan() places INPUT and
        # the display band itself and rejects a permutation that names them.
        M.LANE_ORDER[SLUG] = tuple(m for m in full if m not in kn.get("pins", ("IN", "SND")))
    for name, table in (("pad", M.MEM_PAD_FOR), ("in_west", M.INPUT_NORTH_WEST),
                        ("ranks", M.TRIE_SLACK_ROWS), ("lane_pitch", M.LANE_PITCH)):
        saved[name] = table.get(KEY, "<unset>")
        if name in kn:
            v = kn[name]
            if v is None:
                table.pop(KEY, None)
            else:
                table[KEY] = v
    return saved


def restore(M, saved):
    M.TIER_LAYOUT[KEY]["store_offset"] = saved["_store"]
    M.OPCODE_SLOTS[KEY] = saved["_slots"]
    if saved["_order"] == "<unset>":
        M.LANE_ORDER.pop(SLUG, None)
    else:
        M.LANE_ORDER[SLUG] = saved["_order"]
    for name, table in (("pad", M.MEM_PAD_FOR), ("in_west", M.INPUT_NORTH_WEST),
                        ("ranks", M.TRIE_SLACK_ROWS), ("lane_pitch", M.LANE_PITCH)):
        v = saved[name]
        if v == "<unset>":
            table.pop(KEY, None)
        else:
            table[KEY] = v


def build(M, prog, kn, *, gate=True):
    """Real build with the real ``check_bindings`` unless ``gate`` is off."""
    saved = apply_knobs(M, kn)
    kw = {}
    if "drop" in kn:
        kw["rom_touch_drop"] = kn["drop"]
    if "squash" in kn:
        kw["squash_band"] = kn["squash"]
    grabbed = {}
    orig_cpu = M.build_cpu
    real_gate = M.check_bindings

    def spy(*a, **k):
        cpu = orig_cpu(*a, **k)
        grabbed.setdefault("cpu", cpu)
        grabbed["cpu_last"] = cpu
        return cpu

    M.build_cpu = spy
    if not gate:
        M.check_bindings = lambda g, t: None
    t0 = time.time()
    try:
        m = M.build_for(SLUG, program=prog, store=TIER, **kw)
    finally:
        M.build_cpu = orig_cpu
        M.check_bindings = real_gate
        restore(M, saved)
    return m, grabbed.get("cpu_last"), round(time.time() - t0, 1)


def cpu_geometry(m, cpu):
    """Derive the numbers the walk model is written in, from the CPU's own cells."""
    cells = cpu.cells
    w, h = cpu.width, cpu.height
    collector = max(
        y for y in range(h) if sum(1 for x in range(w) if cells.get((x, y)) == "<") > w // 3
    )
    lanes = {}
    for name, box in cpu.regions.items():
        if name.startswith("lane:"):
            lanes[name.split(":", 1)[1]] = list(box)
    rows = {y: "".join(cells.get((x, y), " ") for x in range(w)) for y in range(h)}
    return {
        "cpu_w": w,
        "cpu_h": h,
        "centre": list(cpu.centre) if isinstance(cpu.centre, tuple) else cpu.centre,
        "collector": collector,
        "regions": {k: list(v) for k, v in cpu.regions.items()},
        "lanes": lanes,
        "rows": rows,
        "grid_w": m.width,
        "grid_h": m.height,
        "mem_pad": m.mem_pad,
    }


def main():
    out = Path(sys.argv[1])
    kn = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    gate = "--nogate" not in sys.argv[3:]
    _, _, M, prog = setup()
    ship = shipped(M)
    print(f"shipped = {ship}", flush=True)
    m, cpu, secs = build(M, prog, kn, gate=gate)
    print(f"built {m.width}x{m.height} pad={m.mem_pad} in {secs}s", flush=True)
    man = "\n".join(m.rows) + "\n"
    Path(str(out) + ".man").write_text(man, encoding="utf-8")
    geo = cpu_geometry(m, cpu)
    geo["shipped"] = ship
    geo["knobs"] = kn
    geo["machine_regions"] = {k: list(v) for k, v in m.regions.items()}
    Path(str(out) + ".geom.json").write_text(json.dumps(geo), encoding="utf-8")
    print(f"cpu {geo['cpu_w']}x{geo['cpu_h']} centre={geo['centre']} "
          f"collector={geo['collector']} lanes={len(geo['lanes'])}", flush=True)


if __name__ == "__main__":
    main()
