"""Step a man through the real worker cells and count the tape words he moves.

The interpreter is the oracle for whether a grid runs; this is the oracle for
what it *costs* and whether the count is exact, and it is the only way to check
every count rather than the ones a tour happens to use.
"""
import sys
sys.path.insert(0, "/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/.claude/worktrees/agent-ae0ef37bbc0eff687/solvers/python")
import randomfun2026solvers.memory_tape as mt

E, W, N, S = (1, 0), (-1, 0), (0, -1), (0, 1)
CW = {E: S, S: W, W: N, N: E}
CCW = {v: k for k, v in CW.items()}
DIR = {">": E, "<": W, "^": N, "v": S}


def walk(cell, start, d, bp, stop, limit=200000):
    """Return (path, moves, ticks). `moves` counts `r`+`s` pairs actually done."""
    x, y = start
    path, r_seen, moves, ticks = [], 0, 0, 0
    while (x, y) != stop:
        ch = cell.get((x, y), " ")
        path.append((x, y, ch))
        if ch == "x":
            d = CW[d] if bp & 1 else CCW[d]
        elif ch == "]":
            bp >>= 1
        elif ch == "d":
            if bp > 0:
                bp_test = True
                d = CW[d]
            # BP <= 0 falls straight through
        elif ch == "m":
            bp -= 1
        elif ch == "r":
            r_seen += 1
        elif ch == "s":
            moves += 1
        elif ch in DIR:
            d = DIR[ch]
        elif ch != " ":
            raise SystemExit(f"unexpected glyph {ch!r} at {(x, y)} after {ticks} ticks")
        x, y = x + d[0], y + d[1]
        ticks += 1
        if ticks > limit:
            raise SystemExit(f"did not terminate for bp={bp}")
    return path, moves, ticks


def render(path, lo, hi):
    return " ".join(f"{ch if ch != ' ' else '.'}@{x},{y}" for x, y, ch in path[lo:hi])


mt.JUMP_V4_P2_BATCH = 4
c = mt.worker_v2_jump(143, park_const=True, protocol="v4")
cell = c.cell
ENTRY, EXIT = (18, 11), (31, 17)   # `>` into the first tail; the ring's one exit

print("── exactness: every count 0..300 ──")
bad = []
for n in range(0, 301):
    _p, moves, ticks = walk(cell, ENTRY, E, n, EXIT)
    if moves != n:
        bad.append((n, moves))
print("counts wrong:", bad if bad else "none — moves == count for all 301")
for n in (0, 1, 2, 3, 4, 18, 53, 124, 300):
    _p, moves, ticks = walk(cell, ENTRY, E, n, EXIT)
    old = 5 * n + 4
    print(f"  count {n:>4}: {moves:>4} words in {ticks:>5} ticks "
          f"({ticks/max(1,n):.2f} t/word)   shipped ring ~{old} ({old/max(1,n):.2f})")

print("\n── stage 0 (bit 0, one word): polarity and merge ──")
for bit in (0, 1):
    p, _m, _t = walk(cell, ENTRY, E, bit, EXIT)
    print(f"  BP low bit {bit}: ", render(p, 0, 9))
print("\n── stage 1 (bit 1, two words): polarity and merge ──")
for bits in (0, 2):
    p, _m, _t = walk(cell, ENTRY, E, bits, EXIT)
    print(f"  BP = {bits}: ", render(p, 7, 18))

print("\n── the odd tail is reachable and merges ──")
for n in (4, 8):        # BP>>2 = 1 (odd) and 2 (even)
    p, _m, t = walk(cell, ENTRY, E, n, EXIT)
    ring = [step for step in p if step[1] >= 14]
    print(f"  count {n} (BP into ring {n >> 2}): ", render(ring, 0, 6), " ...",
          render(ring, len(ring) - 6, len(ring)))

print("\n── the relay: one word per R/S pair ──")
from randomfun2026solvers.dataflow_relay import flat_relay, relay
for art, tag in ((relay(4, 3), "shipped relay(4,3)"), (flat_relay(12, caps=True), "flat_relay(12) caps")):
    body = [row[1:-1] for row in art[1:-1]]
    cells = {(x, y): ch for y, row in enumerate(body) for x, ch in enumerate(row)}
    start = next(k for k, v in cells.items() if v == "@")
    x, y = start
    d, ticks, words, r_open = E, 0, 0, 0
    while True:
        ch = cells.get((x, y), " ")
        if ch in DIR:
            d = DIR[ch]
        elif ch in "rR":
            r_open += 1
        elif ch in "sS":
            assert r_open > 0, f"{tag}: an `{ch}` with nothing received"
            r_open -= 1
            words += 1
        x, y = x + d[0], y + d[1]
        ticks += 1
        if (x, y) == start:
            break
    print(f"  {tag}: {ticks} cells a lap, {words} words, "
          f"{ticks/words:.2f} t/word, never sends before it receives")
