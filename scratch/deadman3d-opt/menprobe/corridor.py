"""Per-lane decomposition of the `cpu:return:high` corridor walk.

The corridor cost of one instruction is ``drop_x - fetch_x`` east plus
``drop_x - riser_x`` west. Both ends are pinned (§ the ledger), so the only
question is where ``drop_x`` is and what put it there. This splits it:

    drop_x - lane_x0  =  own_ops   (glyphs this micro-program actually runs)
                      +  pad       (`.` cells _flat_lane lays down pushing the
                                    MEM band out to ``mem_x``)
                      +  bump      (columns lost to `blocked` / `struct_cols`
                                    contention with lanes below)

``own_ops`` is irreducible without changing the micro-program; ``pad`` is the
``mem_x`` question; ``bump`` is the packing question. Weighted by measured
opcode share, the three columns say whether the corridor is reducible at all.

usage: corridor.py [men-v3|taped ...]
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402
from dropcols import SHARE  # noqa: E402

INSTR = 880_332


def cpu_of(M, prog, store):
    """Build, and steal the CpuRoom on the call that actually survives."""
    grabbed = {}
    orig = M.build_cpu

    def spy(*a, **k):
        cpu = orig(*a, **k)
        grabbed["cpu"] = cpu  # last write wins == the surviving pad trial
        return cpu

    M.build_cpu = spy
    try:
        m = M.build_for(SLUG, program=prog, store=store)
    finally:
        M.build_cpu = orig
    return m, grabbed["cpu"]


def analyse(m, cpu, store):
    R = m.regions
    fx, fy, fw, fh = R["cpu:fetch"]
    tx, ty, tw, th = R["cpu:trie"]
    rx = R["cpu:return:riser"][0]
    hi = R.get("cpu:return:high")
    col_y = R["cpu:return:collector"][1]
    lane_x0 = tx + tw
    # grid-absolute origin of the cpu room, so cpu-local cells line up with rows
    lanes = {n.split(":")[-1]: b for n, b in R.items() if n.startswith("cpu:lane:")}
    ox = min(b[0] for b in lanes.values()) - min(
        x for (x, y) in cpu.cells if cpu.cells[(x, y)] not in (" ",)
    ) if False else None

    print(f"\n===== {store}: grid {m.width}x{m.height}, cpu {cpu.width}x{cpu.height} =====")
    print(f"  fetch x={fx}..{fx+fw-1} row={fy}   trie x={tx}..{tx+tw-1}   lane_x0={lane_x0}")
    print(f"  riser x={rx}  hi_row={hi[1] if hi else None}  collector_row={col_y}")

    # cpu-local -> grid offset: the lane rows in `m.regions` are grid rows.
    # Find it by matching the fetch region against the cpu room's own regions.
    cfx = cpu.regions["fetch"][0] if "fetch" in cpu.regions else None
    dx = fx - cfx if cfx is not None else 0
    cfy = cpu.regions["fetch"][1] if "fetch" in cpu.regions else None
    dy = fy - cfy if cfy is not None else 0

    # mem_x: the column where the MEM band's first glyph lands. Detect it as the
    # modal column of the first non-`.` glyph after a `.` run, but the reliable
    # way is the widest common structure: report the run for each lane instead.
    print(f"\n  {'op':6s} {'row':>4} {'drop':>5} {'ops':>4} {'pad':>4} {'bump':>5} "
          f"{'share':>7} {'lane text (lane_x0 .. drop)'}")
    tot = {"ops": 0.0, "pad": 0.0, "bump": 0.0, "gap": 0.0, "share": 0.0}
    rows_out = []
    for name, (bx, by, bw, bh) in sorted(lanes.items(), key=lambda kv: kv[1][1]):
        r = by
        drop = next((x for x in range(lane_x0, m.width) if m.rows[r][x] == "v"), None)
        if drop is None:
            continue
        text = m.rows[r][lane_x0:drop + 1]
        ops = sum(1 for ch in text[:-1] if ch not in ". ")
        pad = sum(1 for ch in text[:-1] if ch in ". ")
        # `bump`: columns between the lane's last operation and the drop that are
        # blank -- i.e. contention, not this lane's own padding. Padding *inside*
        # the program (between two ops) is `pad`; blanks after the last op are the
        # bump plus any trailing MEM-band push.
        last_op = max((i for i, ch in enumerate(text[:-1]) if ch not in ". "), default=-1)
        trail = len(text) - 1 - (last_op + 1)
        inner_pad = pad - trail
        sh = SHARE.get(name, 0.0)
        rows_out.append((name, r, drop, ops, inner_pad, trail, sh, text))
        tot["ops"] += ops * sh
        tot["pad"] += inner_pad * sh
        tot["bump"] += trail * sh
        tot["share"] += sh
        print(f"  {name:6s} {r:>4} {drop:>5} {ops:>4} {inner_pad:>4} {trail:>5} "
              f"{sh:7.4f}  |{text}|")

    s = tot["share"]
    gap = lane_x0 - fx
    print(f"\n  weighted, per instruction (share total {s:.4f}):")
    print(f"    fetch_x -> lane_x0 (trie gap)      = {gap:8.3f}")
    print(f"    own operations                     = {tot['ops']/s:8.3f}")
    print(f"    padding inside the program (MEM)   = {tot['pad']/s:8.3f}")
    print(f"    blanks after the last op (bump)    = {tot['bump']/s:8.3f}")
    mean_drop = sum(d * sh for _, _, d, _, _, _, sh, _ in rows_out) / s
    print(f"    -> mean drop_x                     = {mean_drop:8.3f} "
          f"(check {gap+tot['ops']/s+tot['pad']/s+tot['bump']/s+lane_x0-gap:.3f})")
    east = mean_drop - fx
    west = mean_drop - rx
    print(f"\n    east leg (fetch->drop)  = {east:7.3f}")
    print(f"    west leg (drop->riser)  = {west:7.3f}")
    print(f"    horizontal round trip   = {east+west:7.3f} cells/instr")
    print(f"    one column off every drop is worth 2 cells/instr = "
          f"{2*880332/1e6:.3f}M ticks")
    return rows_out


def main():
    d3, hires, M, prog = setup()
    for store in (sys.argv[1:] or ["men-v3"]):
        m, cpu = cpu_of(M, prog, store)
        analyse(m, cpu, store)


if __name__ == "__main__":
    main()
