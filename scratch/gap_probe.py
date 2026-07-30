"""What occupies the CPU's odd (gap) rows between lane rows.

Read-only census over the built `deadman-3d_taped` grid. Prints, per gap row,
the non-blank interior cells and which structure they belong to.
"""

from randomfun2026solvers.lm1 import machine

rows = machine.build_for("deadman-3d", store="taped").rows
BAND = range(100, 143)  # lane rows are even, gap rows odd
TRIE_COLS = set(range(13, 22))  # `col = 3 + 2*level` in interior coords, +8 offset

for y in BAND:
    line = rows[y]
    cells = [(x, c) for x, c in enumerate(line) if c not in " |" and 8 < x < len(line) - 1]
    kind = "LANE" if y % 2 == 0 else "gap "
    trie = [(x, c) for x, c in cells if x in TRIE_COLS]
    rest = [(x, c) for x, c in cells if x not in TRIE_COLS]
    print(f"{y:3d} {kind} trie={''.join(c for _, c in trie):9s} "
          f"cols={[x for x, _ in trie]} | other={rest[:12]}")
