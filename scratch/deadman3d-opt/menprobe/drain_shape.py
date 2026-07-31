"""What a ladder+loop of each `unit_bits` actually costs and measures.

Prints the block, its footprint, and :func:`drain.cost` / :func:`drain.walk`
over the word counts a hires slab really sees — so the sweep is priced before it
is run, and the run only has to confirm the sign.

usage: drain_shape.py [bits ...]
"""
import sys

from common import setup  # noqa: F401  (path side effect)
from randomfun2026solvers.lm1 import drain as D


def main():
    bits = [int(a) for a in sys.argv[1:]] or [2, 3, 4, 5]
    ns = [2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 30, 40, 60, 100]
    print("  classic counted loop: 4 ticks a word\n")
    for t in bits:
        b = D.build_drain(0, unit_bits=t, even=True)
        cells = {}
        for (x, y), ch in b.cells.items():
            cells[(x, y)] = ch
        w = max(x for x, _ in cells) + 1
        h = max(y for _, y in cells) + 1
        print(f"  unit_bits={t}: {w}x{h} spine={b.spine} entry={b.entry} "
              f"exit={b.exit} unit={b.unit} reads={len(b.reads)} "
              f"']'={sum(1 for c in cells.values() if c == ']')} "
              f"'x'={sum(1 for c in cells.values() if c == 'x')}")
        for y in range(h):
            print("     " + "".join(cells.get((x, y), " ") for x in range(w)))
        row = []
        for n in ns:
            try:
                row.append(f"{n}:{D.walk(b, n)}")
            except Exception as exc:  # noqa: BLE001
                row.append(f"{n}:{type(exc).__name__}")
        print("     ticks by n: " + "  ".join(row))
        print()


if __name__ == "__main__":
    main()
