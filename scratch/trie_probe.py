"""Walk the CPU decode trie on the built grid and cost it per opcode.

Read-only probe: builds `deadman-3d_taped`, replays the CPU man's decode from
the fetch cell with BP = the opcode number, and reports ticks to the lane.
"""

from randomfun2026solvers.lm1 import machine

rows = machine.build_for("deadman-3d", store="taped").rows
G = {}
for y, r in enumerate(rows):
    for x, c in enumerate(r):
        G[(x, y)] = c

SLOTS = machine.OPCODE_SLOTS[("deadman-3d", "taped")]
K = 5


def bitrev(v, k):
    return int(format(v, f"0{k}b")[::-1], 2)


NUM = {m: bitrev(s, K) for m, s in SLOTS.items()}

DIRS = {"E": (1, 0), "W": (-1, 0), "N": (0, -1), "S": (0, 1)}
CW = {"E": "S", "S": "W", "W": "N", "N": "E"}
CCW = {"E": "N", "N": "W", "W": "S", "S": "E"}


def walk(bp, x=13, y=121, d="E", limit=400, stop_x=22):
    t = 0
    path = []
    while t < limit:
        g = G.get((x, y), " ")
        path.append((x, y, g))
        if g == "x":
            d = CW[d] if (bp & 1) else CCW[d]
        elif g == "]":
            bp >>= 1
        elif g == ">":
            d = "E"
        elif g == "<":
            d = "W"
        elif g == "^":
            d = "N"
        elif g in "vV":
            d = "S"
        elif g == "a":
            d = CCW[d] if bp > 0 else d
        elif g == "d":
            d = CW[d] if bp > 0 else d
        elif g in ". ":
            pass
        else:
            return t, (x, y), path
        dx, dy = DIRS[d]
        x += dx
        y += dy
        t += 1
        if x >= stop_x:
            return t, (x, y), path
    return None, None, path


EXEC = dict(
    LD=41622, ST=26102, ADD=15116, BRN=13355, BRZ=11205, SUB=10103, DIV=8673,
    MODI=7961, LDA=7235, LDI=4905, JMPF=4782, SUBI=4539, DIVI=4490, MULI=3800,
    MUL=2580, ADDI=2433, SND=1742, JMPS=1212, INCM=1201, MOVA=960, IN=889, NEG=248,
)

if __name__ == "__main__":
    tot = wsum = 0
    out = []
    for m, n in sorted(NUM.items(), key=lambda kv: -EXEC.get(kv[0], 0)):
        t, end, _ = walk(n)
        e = EXEC.get(m, 0)
        out.append((m, e, n, SLOTS[m], t, end[1]))
        wsum += e * t
        tot += e
    print(f"{'op':6} {'execs':>7} {'num':>4} {'slot':>4} {'trie':>5} {'row':>4}")
    for r in out:
        print(f"{r[0]:6} {r[1]:7d} {r[2]:4d} {r[3]:4d} {r[4]:5d} {r[5]:4d}")
    print("weighted mean trie ticks:", wsum / tot, "total", wsum, "instr", tot)


def zigzag_cost():
    """Trie vertical travel vs the direct |fetch_row - lane_row| a leaf needs."""
    fetch_row = 121
    tot = direct = trie = 0
    for m, n in NUM.items():
        t, end, _ = walk(n)
        e = EXEC.get(m, 0)
        tot += e
        direct += e * abs(fetch_row - end[1])
        trie += e * t
    horiz = 9 * tot  # every path walks columns 13 -> 22
    return dict(instructions=tot, trie_total=trie, trie_horizontal=horiz,
                trie_vertical=trie - horiz, direct_vertical=direct,
                zigzag=(trie - horiz) - direct)
