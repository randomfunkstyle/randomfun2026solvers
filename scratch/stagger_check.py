"""Prove the staggered band decodes: every opcode reaches its own lane row.

Captures the CPU room even when whole-machine placement fails afterwards, walks
the decode trie for each opcode number, and checks the map is the identity onto
the lane rows the layout intended.
"""

import sys

from randomfun2026solvers.lm1 import machine as M

DIRS = {"E": (1, 0), "W": (-1, 0), "N": (0, -1), "S": (0, 1)}
CW = {"E": "S", "S": "W", "W": "N", "N": "E"}
CCW = {"E": "N", "N": "W", "W": "S", "S": "E"}


def capture(pitch):
    """Return (cpu room, the slot->row map the builder actually handed the trie)."""
    got, real = [], M.build_cpu
    seen, real_trie = [], M._uneven_trie

    def trie_spy(k, slot_rows, lane_x0):
        seen.append(dict(slot_rows))
        return real_trie(k, slot_rows, lane_x0)

    def spy(*a, **kw):
        kw["lane_pitch"] = pitch
        c = real(*a, **kw)
        got.append(c)
        return c

    M._uneven_trie = trie_spy
    M.build_cpu = spy
    try:
        M.build_for("deadman-3d", store="taped")
    except Exception:  # noqa: BLE001 - the CPU room is all this needs
        pass
    finally:
        M.build_cpu = real
        M._uneven_trie = real_trie
    if not got:
        raise RuntimeError("build_cpu never returned a room")
    return got[0], seen[0]


def plan():
    prog = M._tier_program("deadman-3d", "taped")
    return prog, M.plan(prog, middle_order=M.LANE_ORDER.get("deadman-3d"),
                        slots=M.OPCODE_SLOTS.get(("deadman-3d", "taped")))


def intended_rows(p, pitch, y0=1):
    slots = sorted((p.row[m] - 1) // 2 for m in p.number)
    rank = {s: i for i, s in enumerate(slots)}
    if pitch == 1:
        gaps = M._uneven_gaps(p.k, slots)
        at = [y0]
        for i in range(len(slots) - 1):
            at.append(at[-1] + (2 if i in gaps else 1))
    else:
        at = [y0 + 2 * i for i in range(len(slots))]
    return {m: at[rank[(p.row[m] - 1) // 2]] for m in p.number}, at


def walk(cells, bp, x, y, lane_x0=14, limit=400):
    d, t = "E", 0
    while t < limit:
        g = cells.get((x, y), " ")
        if g == "x":
            d = CW[d] if (bp & 1) else CCW[d]
        elif g == "]":
            bp >>= 1
        elif g == ">":
            d = "E"
        elif g not in ". ":
            return y, t
        dx, dy = DIRS[d]
        x, y, t = x + dx, y + dy, t + 1
        if x >= lane_x0:
            return y, t
    raise RuntimeError("decode did not terminate")


def check(pitch):
    c, slot_rows = capture(pitch)
    _, p = plan()
    # The builder's own slot -> row map is the intent; walking must reproduce it.
    want = {m: slot_rows[(p.row[m] - 1) // 2] for m in p.number if (p.row[m] - 1) // 2 in slot_rows}
    got = {m: walk(c.cells, n, 5, c.centre) for m, n in p.number.items()}
    bad = {m: (got[m][0], want[m]) for m in want if got[m][0] != want[m]}
    rows = sorted(slot_rows.values())
    trie = {m: t for m, (_, t) in got.items()}
    return dict(band=rows[-1] - rows[0] + 1, centre=c.centre, height=c.height,
                collector=rows[-1] + 1, bad=bad, rows=want, trie=trie,
                lanes=len(slot_rows))


if __name__ == "__main__":
    for pitch in (2, int(sys.argv[1]) if len(sys.argv) > 1 else 1):
        r = check(pitch)
        print(f"pitch {pitch}: {r['lanes']} lanes, band {r['band']} rows, "
              f"fetch row {r['centre']}, collector {r['collector']}, "
              f"cpu height {r['height']}, mis-decoded {len(r['bad'])}", flush=True)
        if r["bad"]:
            print("   ", sorted(r["bad"].items())[:6])
