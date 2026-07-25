"""max-element as one hand-authored 2D block: counted loop with a sign branch."""
from pathlib import Path

# interior, 15 wide x 7 tall.  Running max lives in B for the whole program.
#   row0 prologue: r=n, b=BP=n, load -1000001 into B as "no max yet"
#   row2 loop test `d`; east of it the epilogue W s H
#   col3 body: r (value), - (value-max), X (branch on sign)
#   X: A>0 -> west  (+ then M : B = value)      A<0 -> east      A==0 -> south
#   row6 collects all three heading east; col8 returns north through `m`
INTERIOR = [
    "@rb`1000001`NMv",
    "v       <     <",
    ">  dWsH        ",
    "   r           ",
    "   -    m      ",
    "vM+Xv          ",
    ">  >>   ^      ",
]

def build() -> list[str]:
    W = max(len(r) for r in INTERIOR)
    H = len(INTERIOR)
    g: dict[tuple[int, int], str] = {}
    # worker box (0,0)-(W+1,H+1)
    for x in range(W + 2):
        g[(x, 0)] = g[(x, H + 1)] = "-"
    for y in range(H + 2):
        g[(0, y)] = g[(W + 1, y)] = "|"
    for c in ((0, 0), (W + 1, 0), (0, H + 1), (W + 1, H + 1)):
        g[c] = "+"
    for y, row in enumerate(INTERIOR):
        for x, ch in enumerate(row):
            if ch != " ":
                g[(x + 1, y + 1)] = ch
    bottom = H + 1
    # input pipe: I room below, flowing NORTH into the worker's bottom wall
    ix = 2
    g[(ix, bottom + 1)] = "^"
    g[(ix, bottom + 2)] = "^"
    for dy, r in enumerate(["+-+", "|I|", "+-+"]):
        for dx, ch in enumerate(r):
            g[(ix - 1 + dx, bottom + 3 + dy)] = ch
    # output pipe: O room below, flowing SOUTH out of the worker
    ox = 12
    g[(ox, bottom + 1)] = "v"
    g[(ox, bottom + 2)] = "v"
    for dy, r in enumerate(["+-+", "|O|", "+-+"]):
        for dx, ch in enumerate(r):
            g[(ox - 1 + dx, bottom + 3 + dy)] = ch
    mx = max(x for x, _ in g)
    my = max(y for _, y in g)
    return ["".join(g.get((x, y), " ") for x in range(mx + 1)).rstrip() for y in range(my + 1)]

rows = build()
print("\n".join(rows))
w = max(len(r) for r in rows); h = len(rows)
print(f"\ngrid {w}x{h} factor {max(w,h)**2}")
Path("/private/tmp/claude-502/-Users-ptaykalo-Projects-icfpc-2026-randomfun2026solvers/06a79396-3474-4883-ac12-4fbf5b10b810/scratchpad/maxel.man").write_text("\n".join(rows) + "\n")
