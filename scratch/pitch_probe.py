"""Build the taped CPU at a chosen lane pitch and cost its dispatch loop.

Walks the decode trie on the real grid for every opcode, then adds the drop and
the riser, so the whole vertical circuit is priced from the emitted cells rather
than from a model of them.
"""

import sys

from randomfun2026solvers.lm1 import machine

EXEC = dict(
    LD=41622, ST=26102, ADD=15116, BRN=13355, BRZ=11205, SUB=10103, DIV=8673,
    MODI=7961, LDA=7235, LDI=4905, JMPF=4782, SUBI=4539, DIVI=4490, MULI=3800,
    MUL=2580, ADDI=2433, SND=1742, JMPS=1212, INCM=1201, MOVA=960, IN=889, NEG=248,
)
RUN = 60_325_078  # ticks in the gated WALK[:8] case the profile was taken on

DIRS = {"E": (1, 0), "W": (-1, 0), "N": (0, -1), "S": (0, 1)}
CW = {"E": "S", "S": "W", "W": "N", "N": "E"}
CCW = {"E": "N", "N": "W", "W": "S", "S": "E"}


def build(pitch):
    machine.LANE_PITCH.pop(("deadman-3d", "taped"), None)
    if pitch != 2:
        machine.LANE_PITCH[("deadman-3d", "taped")] = pitch
    m = machine.build_for("deadman-3d", store="taped")
    machine.LANE_PITCH.pop(("deadman-3d", "taped"), None)
    return m


def geometry(m):
    """(grid, fetch cell, first trie column, lane_x0, collector row)."""
    G = {(x, y): c for y, r in enumerate(m.rows) for x, c in enumerate(r)}
    fetch = next(
        (x, y) for (x, y), c in G.items()
        if c == ">" and "".join(G.get((x + i, y), " ") for i in range(4)) == ">rbr"
    )
    return G, fetch


def walk(G, bp, x, y, d="E", stop_x=10**9, limit=600):
    t = 0
    while t < limit:
        g = G.get((x, y), " ")
        if g == "x":
            d = CW[d] if (bp & 1) else CCW[d]
        elif g == "]":
            bp >>= 1
        elif g == ">":
            d = "E"
        elif g in ". ":
            pass
        else:
            return t, (x, y)
        dx, dy = DIRS[d]
        x += dx
        y += dy
        t += 1
        if x >= stop_x:
            return t, (x, y)
    raise RuntimeError("decode did not terminate")


def report(pitch):
    m = build(pitch)
    G, (fx, fy) = geometry(m)
    lane_x0 = fx + 13  # `>rbr` at fx..fx+3, trie columns fx+4.., lanes at fx+13
    numbers = {
        op.mnemonic: n
        for op, n in _numbers(m).items()
    }
    rows, tries = {}, {}
    for mn, n in numbers.items():
        t, (_, y) = walk(G, n, fx + 4, fy, stop_x=lane_x0)
        tries[mn], rows[mn] = t, y
    collector = max(rows.values()) + 1
    tot = sum(EXEC[k] for k in tries)
    trie = sum(EXEC[k] * tries[k] for k in tries)
    drop = sum(EXEC[k] * (collector - 1 - rows[k]) for k in tries)
    riser = tot * (collector - fy)
    return dict(pitch=pitch, size=f"{m.width}x{m.height}", fetch_row=fy,
                collector=collector, band=max(rows.values()) - min(rows.values()) + 1,
                trie=trie, drop=drop, riser=riser, total=trie + drop + riser,
                rows=rows, tries=tries)


def _numbers(m):
    """The opcode number the ROM emits, per op, read back off the plan."""
    from randomfun2026solvers.lm1 import machine as M

    prog = M._tier_program("deadman-3d", "taped")
    p = M.plan(prog, middle_order=M.LANE_ORDER.get("deadman-3d"),
               slots=M.OPCODE_SLOTS.get(("deadman-3d", "taped")))
    return {op: p.number[op.mnemonic] for op in prog.ops_used}


if __name__ == "__main__":
    a = report(2)
    b = report(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
    print(f"{'':10} {'pitch 2':>14} {'pitch 1':>14} {'delta':>12} {'% run':>8}")
    for key in ("size", "fetch_row", "collector", "band"):
        print(f"{key:10} {str(a[key]):>14} {str(b[key]):>14}")
    for key in ("trie", "drop", "riser", "total"):
        d = b[key] - a[key]
        print(f"{key:10} {a[key]:14,} {b[key]:14,} {d:12,} {d / RUN * 100:7.2f}%")
