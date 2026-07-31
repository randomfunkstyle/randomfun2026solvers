"""Per-lane drop column on the REAL hires build, paired with measured opcode
frequency, and the closed-loop horizontal arithmetic for moving the fetch east.

usage: dropcols.py [store]
"""
import sys

from common import setup, SLUG

# measured opcode shares (frequency over the tour) -- geometry-independent
SHARE = {
    "IN": 0.0054, "INCM": 0.0052, "MOVA": 0.0035, "DIV": 0.0347,
    "ST": 0.1581, "SUB": 0.0301, "ADD": 0.0811, "LDA": 0.0277,
    "MUL": 0.0121, "DIVI": 0.0319, "LD": 0.1891, "MODI": 0.0366,
    "NEG": 0.0011, "SUBI": 0.0503, "ADDI": 0.0287, "MULI": 0.0516,
    "LDI": 0.0473, "BRN": 0.0644, "BRZ": 0.0641, "JMPF": 0.0324,
    "JMPS": 0.0098, "SND": 0.0393,
}


def analyse(m, store):
    print(f"\n===== {store}: {m.width}x{m.height} =====")
    reg = m.regions
    rows = m.rows
    fx, fy, fw, fh = reg["cpu:fetch"]
    tx, ty, tw, th = reg["cpu:trie"]
    hi = reg.get("cpu:return:high")
    col = reg["cpu:return:collector"]
    riser = reg["cpu:return:riser"]
    lane_x0 = min(x for n, (x, y, w, h) in reg.items() if n.startswith("cpu:lane:"))
    print(f"  fetch_x={fx} fetch_row={fy}  trie x={tx}..{tx+tw-1}  lane_x0={lane_x0}")
    print(f"  riser col={riser[0]}  high_row={hi[1] if hi else None}  collector_row={col[1]}")

    # per lane: row, drop column (the 'v' on that lane's row, east of lane_x0)
    lanes = []
    for n, (x, y, w, h) in reg.items():
        if not n.startswith("cpu:lane:"):
            continue
        mn = n.split(":")[-1]
        r = rows[y]
        drop = None
        for xx in range(lane_x0, len(r)):
            if r[xx] == "v":
                drop = xx
                break
        lanes.append((mn, y, x + w - 1, drop, SHARE.get(mn, 0.0)))
    lanes.sort(key=lambda t: t[1])
    print(f"\n  {'op':6s} {'row':>5s} {'end':>5s} {'drop':>5s} {'share':>7s}")
    for mn, y, end, drop, sh in lanes:
        print(f"  {mn:6s} {y:5d} {end:5d} {str(drop):>5s} {sh:7.4f}")

    good = [(mn, drop, sh) for mn, y, end, drop, sh in lanes if drop is not None]
    tot = sum(sh for _, _, sh in good)
    meanD = sum(d * s for _, d, s in good) / tot
    print(f"\n  weighted mean drop column = {meanD:.2f}   (total share {tot:.4f})")

    # ---- model A: today. fetch west of lanes, trie runs east.
    #      horizontal = (drop - fetch_x) east  +  (drop - riser_x) west
    cur = sum(s * ((d - fx) + (d - riser[0])) for _, d, s in good) / tot
    print(f"\n  MODEL A (shipped): fetch_x={fx}, riser={riser[0]}, trie eastward")
    print(f"    horizontal round trip = {cur:.3f} cells/instr")
    print(f"      east leg  (fetch->drop) = {sum(s*(d-fx) for _,d,s in good)/tot:.3f}")
    print(f"      west leg  (drop->riser) = {sum(s*(d-riser[0]) for _,d,s in good)/tot:.3f}")

    # ---- model B: fetch moved east to m, trie MIRRORED to run westward from the
    #      fetch down to lane_x0 (unchanged), riser/corridor return to column m.
    #      horizontal = (m - lane_x0) west + (drop - lane_x0) east + |drop - m|
    trie_w = tw + (fx + fw - tx if fx + fw > tx else 0)
    need = lane_x0 - fx  # columns the fetch+trie occupy today
    print(f"\n  MODEL B (fetch east, trie mirrored westward); fetch+trie need "
          f"{need} columns, so m >= lane_x0 + {need} = {lane_x0+need}")

    def costB(mm):
        return sum(s * ((mm - lane_x0) + (d - lane_x0) + abs(d - mm))
                   for _, d, s in good) / tot

    lo = lane_x0 + need
    best = min(range(lo, lo + 40), key=costB)
    print(f"    m={lo} (tightest): {costB(lo):.3f}   ({costB(lo)-cur:+.3f} vs shipped)")
    print(f"    best m={best}: {costB(best):.3f}   ({costB(best)-cur:+.3f} vs shipped)")
    for mm in range(lo, lo + 16, 2):
        print(f"      m={mm:3d}  {costB(mm):7.3f}  ({costB(mm)-cur:+7.3f})")

    # ---- model C: the task's premise -- move fetch east, trie stays eastward,
    #      so lane_x0 and every drop shift east by the same amount. Riser follows.
    print("\n  MODEL C (task's premise: fetch east, trie still eastward)")
    print("    every drop shifts east with the fetch; drop-riser is invariant:")
    for shift in (0, 5, 10, 23):
        c = sum(s * ((d + shift - (fx + shift)) + (d + shift - (riser[0] + shift)))
                for _, d, s in good) / tot
        print(f"      shift={shift:3d}  horizontal = {c:.3f}  ({c-cur:+.3f})")


def main():
    d3, hires, M, prog = setup()
    for store in (sys.argv[1:] or ["men-v3", "taped"]):
        m = M.build_for(SLUG, program=prog, store=store)
        analyse(m, store)


if __name__ == "__main__":
    main()
