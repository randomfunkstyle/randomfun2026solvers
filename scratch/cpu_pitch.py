"""Capture the CPU room alone at a chosen lane pitch, even when whole-machine
placement later fails, and check the trie/lane non-interference invariant:

    a node at trie level L sits in column 3 + 2L, and every lane in its subtree
    is entered from a node at level > L, i.e. at column >= 5 + 2L — so a node's
    column and its legs are strictly west of every lane entry beneath them.
"""

import sys

from randomfun2026solvers.lm1 import machine as M


def capture(pitch):
    """Build far enough to get the CPU room; ignore any later placement failure."""
    grabbed = []
    real = M.build_cpu

    def spy(*a, **kw):
        kw["lane_pitch"] = pitch
        c = real(*a, **kw)
        grabbed.append(c)
        return c

    M.build_cpu = spy
    try:
        M.build_for("deadman-3d", store="taped")
    except Exception:  # noqa: BLE001 - the CPU is all we need
        pass
    finally:
        M.build_cpu = real
    if not grabbed:
        raise RuntimeError("build_cpu never returned")
    return grabbed[0]


def audit(c):
    """Cells are in interior coordinates: `>rbr` starts at column 1, lanes at 4 + 2k."""
    G = c.cells
    fetch = (1, c.centre)
    lane_x0 = 14  # 4 + 2 * k, k = 5
    band = sorted({y for (x, y) in G if x >= lane_x0})
    nodes = [(x, y) for (x, y), ch in G.items() if ch == "x"]
    return G, fetch, lane_x0, band, nodes


if __name__ == "__main__":
    pitch = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    c = capture(pitch)
    G, fetch, lane_x0, band, nodes = audit(c)
    print(f"pitch {pitch}: cpu {c.width}x{c.height} centre={c.centre} "
          f"fetch={fetch} lane_x0={lane_x0}")
    print(f"lane rows {min(band)}..{max(band)} n={len(band)} "
          f"pitches={sorted({b - a for a, b in zip(band, band[1:])})}")
    print(f"trie nodes n={len(nodes)} cols={sorted({x for x, _ in nodes})}")
    shared = [(x, y) for x, y in nodes if y in band]
    print(f"nodes sharing a lane row: {len(shared)}")
    if len(sys.argv) > 2:
        for y in range(min(band) - 2, max(band) + 3):
            print(f"{y:3d} " + "".join(
                G.get((x, y), " ") for x in range(lane_x0 + 14)).replace(" ", "."))
