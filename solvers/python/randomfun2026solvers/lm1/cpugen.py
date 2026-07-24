"""The first working LM-1 CPU, generated: ROM + code ring + CPU + register cell + I/O.

Runs `triangle` as a *program* rather than a hand-drawn grid, on a 7-opcode ISA
(IN STR ADDI MULR DIVI OUT HALT) decoded by a depth-3 backpack trie. 6/6 public
cases, 46x31, 446 ticks -> score 943,736. The bespoke grid is 6,912, i.e. 137x
better, which is the price of generality (ARCH.md 1).

Two things make the geometry close, both worth keeping in the general generator:

* **Fixed-width 2-word instructions.** Every instruction is opcode + operand,
  operand ignored where unused, so the *fetch stage* is the only thing that ever
  touches the code ring. No lane competes for the ring pipes, which is what
  made ARCH.md 7.1's nearest-pipe constraint tractable: each lane only needs the
  one pipe its own micro-program uses. Costs ring words, buys a working CPU.
* **Opcode numbering as a layout variable** (ARCH.md 2.4). The trie sorts leaves
  bit-reversed, so opcode k lands on a known row; IN is opcode 0 (top row, by
  the north input pipe) and OUT is opcode 7 (bottom row, by the south output
  pipe). Picking the numbers is how each lane ends up next to the wall it needs.

Spill is one `register-cell` on the east bus, per ARCH.md 4.1's two-tier rule --
`triangle` needs exactly one live value besides ACC, because A dies on every
fetch.

Verify: node littleman/tools/route-check.mjs littleman/programs/triangle-cpu.man
        node littleman/tools/run-cases.mjs littleman/programs/triangle-cpu.man <cases> 200000 1
"""

W, H = 50, 34
g = {}


def put(x, y, ch):
    if (x, y) in g and g[(x, y)] != ch:
        raise SystemExit(f"collision at {(x, y)}: {g[(x, y)]!r} vs {ch!r}")
    g[(x, y)] = ch


def room(x0, y0, x1, y1):
    for x in range(x0 + 1, x1):
        put(x, y0, "-")
        put(x, y1, "-")
    for y in range(y0 + 1, y1):
        put(x0, y, "|")
        put(x1, y, "|")
    for c in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        put(*c, "+")


def text(x, y, s):
    for i, ch in enumerate(s):
        if ch != "\0":
            put(x + i, y, ch)


def pipe(cells):  # [(x,y,glyph), ...]
    for x, y, ch in cells:
        put(x, y, ch)


# ── ROM: 14 words, all single digits -> `@0s0s...H` on one row ───────────────
WORDS = [0, 0, 6, 0, 2, 1, 1, 0, 5, 2, 7, 0, 4, 0]  # IN STR ADDI1 MULR DIVI2 OUT HALT
rom = "@" + "".join(f"{w}s" for w in WORDS) + "H"
room(0, 0, len(rom) + 1, 2)
text(1, 1, rom)

# ── LOOP: recirculator, R (any incoming) + s ─────────────────────────────────
room(3, 12, 8, 15)
text(4, 13, "@>Rv")
text(4, 14, ".^s<")

# ── CPU: interior cols 11..34, rows 7..23 (local +10,+6) ─────────────────────
CX, CY = 10, 6
room(CX, CY, CX + 25, CY + 18)


def cpu(lx, ly, s):
    text(CX + lx, CY + ly, s)


cpu(1, 8, ">rsbrsx")  # re-orient, fetch opcode, write back, BP=op,
# fetch operand, write back, trie level 1
for ly, ch in ((7, "]"), (6, "."), (5, "."), (4, ">")):
    cpu(7, ly, ch)  # bit0=0 -> north
for ly, ch in ((9, "]"), (10, "."), (11, "."), (12, ">")):
    cpu(7, ly, ch)  # bit0=1 -> south
for base in (4, 12):  # trie level 2
    cpu(8, base, "x")
    cpu(8, base - 1, "]")
    cpu(8, base - 2, ">")
    cpu(8, base + 1, "]")
    cpu(8, base + 2, ">")
for base in (2, 6, 10, 14):  # trie level 3
    cpu(9, base, "x")
    cpu(9, base - 1, ">")
    cpu(9, base + 1, ">")

# lanes: A = operand, B = ACC on entry
LANES = {  # row: (micro at col 10, extra sites)
    1: ("rM", {}),  # op0 IN    : r<-input, ACC = it
    3: ("H", {}),  # op4 HALT
    5: ("+M", {}),  # op2 ADDI  : A=operand+ACC
    7: ("1", {20: "s", 21: "W", 22: "s", 23: "W"}),  # op6 STR -> cell
    9: ("1N", {20: "s", 21: "r", 22: "*", 23: "M"}),  # op1 MULR <- cell
    11: ("W/M", {}),  # op5 DIVI  : ACC / operand
    13: ("H", {}),  # op3 spare
    15: ("Ws W", {}),  # op7 OUT   (s at col 11 -> output pipe)
}
for row, (micro, extra) in LANES.items():
    cpu(10, row, micro.replace(" ", ""))
    for col, ch in extra.items():
        cpu(col, row, ch)

# return path: east along the lane -> col 24 -> south to row 17 -> west -> north
for row in list(LANES) + [16]:
    start = 10 + len(LANES[row][0].replace(" ", "")) if row in LANES else 3
    for c in range(start, 24):
        if (CX + c, CY + row) not in g and row not in (3, 13):
            cpu(c, row, ".")
for r in range(1, 17):
    cpu(24, r, "v")
cpu(24, 17, "<")
for c in range(2, 24):
    cpu(c, 17, ".")
cpu(1, 17, "^")
for r in range(9, 17):
    cpu(1, r, ".")
cpu(2, 16, "@")  # spawn walks east then round

# ── CELL: the 1-value register (store `1 v`, fetch `-1`) ─────────────────────
room(40, 10, 45, 16)
for i, s in enumerate(("vWs<", "v..W", ">@RX", "^..W", "^Wr<")):
    text(41, 11 + i, s)

# ── I / O rooms ─────────────────────────────────────────────────────────────
room(36, 0, 38, 2)
text(37, 1, "I")
room(22, 28, 24, 30)
text(23, 29, "O")

# ── pipes ───────────────────────────────────────────────────────────────────
pipe(
    [(1, 3, "v")]
    + [(1, y, "|") for y in range(4, 11)]
    + [(1, 11, ">"), (2, 11, "-"), (3, 11, "-"), (4, 11, "v")]
)  # ROM -> LOOP
pipe(
    [(7, 11, "^"), (7, 10, "|"), (7, 9, "|"), (7, 8, "|"), (7, 7, ">"), (8, 7, "-"), (9, 7, ">")]
)  # LOOP -> CPU
pipe(
    [
        (9, 17, "<"),
        (8, 17, "v"),
        (8, 18, "|"),
        (8, 19, "|"),
        (8, 20, "<"),
        (7, 20, "-"),
        (6, 20, "-"),
        (5, 20, "^"),
        (5, 19, "|"),
        (5, 18, "|"),
        (5, 17, "|"),
        (5, 16, "^"),
    ]
)  # CPU -> LOOP
pipe(
    [(37, 3, "v"), (37, 4, "<")]
    + [(x, 4, "-") for x in range(24, 37)]
    + [(23, 4, "v"), (23, 5, "v")]
)  # I -> CPU
pipe([(23, 25, "v"), (23, 26, "|"), (23, 27, "v")])  # CPU -> O
pipe([(36, 13, ">"), (37, 13, "-"), (38, 13, "-"), (39, 13, ">")])  # CPU -> CELL
pipe([(39, 15, "<"), (38, 15, "-"), (37, 15, "-"), (36, 15, "<")])  # CELL -> CPU

rows = ["".join(g.get((x, y), " ") for x in range(W)).rstrip() for y in range(H)]
while rows and not rows[-1]:
    rows.pop()
print("\n".join(rows))
