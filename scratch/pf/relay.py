"""A flat turnaround room, and a probe that measures its ticks per word.

`value_ring.RELAY_NORTH` turns one word per 6-cell walking cycle.  A flat two-
row room walks east along the top and west along the bottom, and every cell
that is not a corner can be half of an `r`/`s` pair::

    +----------+
    |>@rsrsrsrv|
    |^.srsrsrs<|
    +----------+

Two cells are deliberately *not* pairs.  `@` at (1,0) is the spawn: the man's
first act must be `r`, never an `s` that would inject a spurious 0 into the
ring, and `@` is a nop he re-walks harmlessly every lap.  `.` at (1,1) keeps
the body length even, so the walk ends on an `s` -- an odd body would leave a
value in A that the next lap's first `r` overwrites, losing a word silently.

Cycle 2W cells, W-3 pairs, so 2W/(W-3) ticks per word: 2.86 at W=10 against
RELAY_NORTH's 6.
"""
from __future__ import annotations


def flat_relay(w: int = 10) -> list[str]:
    if w < 6 or w % 2:
        raise ValueError("width must be even and at least 6")
    top = [">", "@"] + ["rs"[i % 2] for i in range(w - 3)] + ["v"]
    bot = ["^", "."] + ["sr"[i % 2] for i in range(w - 3)] + ["<"]
    body = "".join(top), "".join(bot)
    assert len(body[0]) == len(body[1]) == w, body
    # walk the cycle and check r/s alternate starting with r
    seq = [c for c in body[0][2:w - 1]] + [body[1][x] for x in range(w - 2, 1, -1)]
    assert seq == ["r", "s"] * ((w - 3)), seq
    return ["+" + "-" * w + "+", "|" + body[0] + "|", "|" + body[1] + "|",
            "+" + "-" * w + "+"]


def probe(w: int = 10, n: int = 6) -> list[str]:
    """input room -> relay -> output room, so the engine can measure it."""
    rows = flat_relay(w)
    grid = [[" "] * (w + 8) for _ in range(16)]
    for y, r in enumerate(["+-+", "|I|", "+-+"]):
        for x, c in enumerate(r):
            grid[y][x + 1] = c
    grid[3][2] = grid[4][2] = "v"          # 2 cells: a 1-cell pipe is a load error
    for y, r in enumerate(rows):           # relay walls at rows 5..8
        for x, c in enumerate(r):
            grid[5 + y][x] = c
    grid[9][5] = grid[10][5] = "v"
    for y, r in enumerate(["+-+", "|O|", "+-+"]):
        for x, c in enumerate(r):
            grid[11 + y][x + 4] = c
    return ["".join(r).rstrip() for r in grid]


if __name__ == "__main__":
    print("\n".join(probe()))
