# deadman-3d_taped — where a CPU operation's ticks go, per opcode

Measurement only: **no machine behaviour was changed.** `deadman-3d.man` /
`_trim` / `_v2` are still `f62d63fd…`, `deadman-3d_taped.man` is still
`46b237a7…`, `deadman-3d.input.txt` is still `654d35d6…`, and the DOOM test set
(`test_deadman3d`, `test_deadman3d_hires`, `test_memory_taped`,
`test_wadimport`) is **122 passed** with the instrumentation off, which is its
default.

## Look here first

> **`LD` alone is 32.49% of the run, and 23.66% of the run is `LD` standing
> still on the store's answer pipe.**
> It executes 41,622 times in nine frames — 4,625 a frame — at **470.9 ticks
> each: 342.9 blocked on `store:collector->cpu` and 128.0 walking the CPU's own
> dispatch**, and **61% of those reads are two words**, `WADDR` (533) and `SDX`
> (521), the DDA's own cursor pair.

The two follow-ons matter as much as the headline:

* **Dispatch cost is not concentrated in any opcode.** Every instruction pays a
  **103-tick loop** — fetch 4, trie 26–34, drop 0–42, return bus 1–38, riser 22 —
  and 175,153 instructions × 103 is 29.91% of the run. Four of the five stages
  are constants or near-constants. `LANE_ORDER` and `OPCODE_SLOTS` can move only
  the drop and the rank, and the **absolute ceiling on both together is 3.46%**
  of the run (2.87 + 0.59), with feasibility taking bites out of that.
* **One line of the program is worth more than either layout knob.** The DDA's
  x-arm does `ST WADDR` then `LD WADDR`, and `ST` is documented "ACC preserved" —
  so the reload re-fetches the value already in the accumulator. It runs 5,536
  times in nine frames. **4.32% of the run**, from deleting one instruction from
  each of the 16 unrolled copies.

## Which engine produced these numbers

Everything here comes from the **native `fast_littleman` backend** (the C++
tick loop behind the Python engine, `fast_littleman_native.cpp`), running the
**gated** display-judged case `WALK[:8]` — 9 rounds, 9 frames,
**60,325,077 ticks**, `passed`. The reference Node/WASM engine cannot be used:
it OOMs its 4 GB Go heap before it can load a 287x253 grid.

Attribution is **exact, at stride 1, over every tick** — it is a state machine
over consecutive ticks, so it is exact or it is nothing:

```
attributed 60,325,078 of 60,325,078 runner-ticks (100.0000%)
outside the CPU room 0 · ambiguous focus 0 · unattributed segments 1 (25 ticks)
```

The single unattributed segment is the CPU man's walk from his spawn cell to his
first instruction fetch. Nothing else is dropped.

Reproduce:

```
uv run python scratch/doom_opcodes.py --out <dir>   # tables + JSON + index.html
uv run python scratch/doom_heatmap.py --out <dir>   # the region profile it builds on
uv run python scratch/doom_pipes.py                 # pipe traffic + critical path
```

### This is a different machine from `scratch/DOOM-PROFILE.md`

That profile was taken on `analysis-doom-profile` (base `2a20a64`) and measured
**61,555,215** ticks; `merge-staging` has since landed `io:I west`, the two
`mem_pad` columns and the `cpu->drum` teleport, and the same case now runs
**60,325,077**. Its coordinates are stale too — the hottest cell it names,
`(46,121)`, is a blank in today's grid; the `LD` lane's `r` is at `(40,121)`.
The *shape* of its conclusions survives (47.19% → 48.51% blocked on the store);
the coordinates and the totals do not. Every number below is today's.

### How a tick gets charged to an opcode

The CPU man's route is a closed loop, and the loop is the accounting:

```
(9,120) `>rbr`  fetch  ->  x=13..21  trie  ->  x=22..  the opcode's lane row
   ^                                                        |
   |  x=9, north, 22 cells                                  v  the lane's exit `v`
   +---- y=142, west, the collector row  <----  the drop column, south
```

The timeline is cut every time the man **enters** the fetch cells, and each
segment is folded into the one opcode whose lane it entered. So the trie descent
that *selected* `LD`, the drop out of the `LD` lane, and the walk back west are
all charged to `LD`, and the run's ticks partition across opcodes with no shared
bucket left over.

Two traps had to be handled, and both silently corrupt a naive attribution:

1. **A lane's rectangle is not the lane.** `cpu:lane:JMPS` spans x=22..58 at
   y=133, and five shorter lanes descend *through* it on their way to the
   collector row. So a cell is tagged per **arrival direction** — walked east it
   is `JMPS`'s lane, walked south it is somebody else's drop.
2. **The lanes' exits are stacked.** `ADD` (y=117) drops onto `ST`'s own exit
   `v` at (43,119); `MUL`, `LDA` and `DIV` all drop onto `SUB`'s at (44,115);
   the five immediate lanes drop onto `SND`'s at (25,141). Before this was
   handled, `ADD` was reported as `ST`, `MUL`/`LDA`/`DIV` as `SUB`, and every
   immediate as `SND` — twelve of the twenty-two opcodes had **zero recorded
   executions**.

The result is checkable, and it checks out three independent ways:

* per-stage costs come out as **exact integers matching cell geometry** — `LD`'s
  lane is 21 cells and its lane walk is 21.0 ticks; its drop is 141−121 = 20.0;
  its return bus is 42−9 = 33.0; the riser is 22.0 for every opcode;
* the reads implied by the opcode mix are **87,490**, the exact count the engine
  counted on `store:collector->cpu` independently;
* the store request stream is **144,016** values = 87,490 reads × 1 +
  28,263 writes × 2, and 28,263 is exactly `ST` + `INCM` + `MOVA` executions.

---

## 1. The run, in four parts

| part | ticks | % run |
|---|---:|---:|
| **memory stall** (blocked on `store:collector->cpu`) | 29,262,833 | **48.51%** |
| **dispatch walk** (fetch, trie, lane, drop, return) | 21,810,756 | **36.16%** |
| **slab work** (the branch discard loops and the seek) | 7,569,512 | 12.55% |
| other stall (`rom->cpu`, `cpu->stream`, `input->cpu`) | 1,681,977 | 2.79% |
| **total** | **60,325,078** | **100.00%** |

175,153 instructions (19,462 a frame), 87,490 store reads (9,721 a frame),
28,263 writes (3,140 a frame), 6,703k ticks a frame.

## 2. The per-opcode cost table

Every tick of the run, charged to the instruction that caused it.

| opcode | execs | /frame | ticks | % run | mean | dispatch walk | memory stall | slab work | other stall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **LD** | 41,622 | 4,625 | **19,599,292** | **32.49%** | 470.9 | 5,327,616 | **14,271,676** | 0 | 0 |
| ADD | 15,116 | 1,680 | 6,376,664 | 10.57% | 421.8 | 2,116,240 | 4,260,424 | 0 | 0 |
| BRN | 13,355 | 1,484 | 5,973,341 | 9.90% | 447.3 | 1,610,253 | 0 | 3,970,313 | 392,775 |
| LDA | 7,235 | 804 | 5,794,996 | 9.61% | **801.0** | 1,070,780 | 4,724,216 | 0 | 0 |
| SUB | 10,103 | 1,123 | 4,171,152 | 6.91% | 412.9 | 1,454,832 | 2,716,320 | 0 | 0 |
| BRZ | 11,205 | 1,245 | 3,907,518 | 6.48% | 348.7 | 1,241,373 | 0 | 2,400,139 | 266,006 |
| ST | 26,102 | 2,900 | 3,445,464 | 5.71% | 132.0 | 3,445,464 | **0** | 0 | 0 |
| DIV | 8,673 | 964 | 3,106,449 | 5.15% | 358.2 | 1,266,258 | 1,840,191 | 0 | 0 |
| JMPS | 1,212 | 135 | 1,416,524 | 2.35% | 1168.7 | 123,624 | 0 | 652,778 | 640,122 |
| MUL | 2,580 | 287 | 1,057,448 | 1.75% | 409.9 | 366,360 | 691,088 | 0 | 0 |
| JMPF | 4,782 | 531 | 1,004,567 | 1.67% | 210.1 | 454,290 | 0 | 546,282 | 3,995 |
| MODI | 7,961 | 885 | 732,412 | 1.21% | 92.0 | 732,412 | 0 | 0 | 0 |
| INCM | 1,201 | 133 | 695,650 | 1.15% | 579.2 | 187,356 | 508,294 | 0 | 32,468 |
| SND | 1,742 | 194 | 474,944 | 0.79% | 272.6 | 132,392 | 0 | 0 | 342,552 |
| LDI | 4,905 | 545 | 422,082 | 0.70% | 86.1 | 421,830 | 0 | 0 | 252 |
| DIVI | 4,490 | 499 | 422,060 | 0.70% | 94.0 | 422,060 | 0 | 0 | 0 |
| SUBI | 4,539 | 504 | 417,588 | 0.69% | 92.0 | 417,588 | 0 | 0 | 0 |
| MOVA | 960 | 107 | 402,304 | 0.67% | 419.1 | 151,680 | 250,624 | 0 | 0 |
| ADDI | 2,433 | 270 | 350,352 | 0.58% | 144.0 | 350,352 | 0 | 0 | 0 |
| MULI | 3,800 | 422 | 334,400 | 0.55% | 88.0 | 334,400 | 0 | 0 | 0 |
| IN | 889 | 99 | 180,166 | 0.30% | 202.7 | 143,891 | 0 | 0 | 36,275 |
| NEG | 248 | 28 | 39,680 | 0.07% | 160.0 | 39,680 | 0 | 0 | 0 |
| (unattributed) | 1 | 0 | 25 | 0.00% | 25.0 | 25 | 0 | 0 | 0 |

Per execution, and what one instruction is made of:

| opcode | ticks/exec | dispatch | mem stall | slab | other | reads/exec | ticks/read |
|---|---:|---:|---:|---:|---:|---:|---:|
| **LDA** | **801.0** | 148.0 | 653.0 | 0 | 0 | 1.00 | **653.0** |
| INCM | 579.2 | 156.0 | 423.2 | 0 | 9.8 | 1.00 | 423.2 |
| **LD** | 470.9 | 128.0 | **342.9** | 0 | 0 | **1.00** | 342.9 |
| ADD | 421.8 | 140.0 | 281.8 | 0 | 0 | 1.00 | 281.8 |
| MOVA | 419.1 | 158.0 | 261.1 | 0 | 0 | 1.00 | 261.1 |
| SUB | 412.9 | 144.0 | 268.9 | 0 | 0 | 1.00 | 268.9 |
| MUL | 409.9 | 142.0 | 267.9 | 0 | 0 | 1.00 | 267.9 |
| DIV | 358.2 | 146.0 | 212.2 | 0 | 0 | 1.00 | 212.2 |
| JMPS | 1168.7 | 102.0 | 0 | 538.6 | 528.2 | — | — |
| BRN | 447.3 | 120.6 | 0 | 297.3 | 29.4 | — | — |
| BRZ | 348.7 | 110.8 | 0 | 214.2 | 23.7 | — | — |
| JMPF | 210.1 | 95.0 | 0 | 114.2 | 0.8 | — | — |
| SND | 272.6 | 76.0 | 0 | 0 | 196.6 | — | — |
| ST | **132.0** | 132.0 | **0** | 0 | 0 | 0.00 | — |
| the immediates (MODI/DIVI/SUBI/MULI/LDI/ADDI) | 86–144 | all of it | 0 | 0 | 0 | 0.00 | — |

Three things fall straight out of this table:

* **`ST` never blocks.** A write is fire-and-forget: the CPU pushes address and
  data at the store and walks on. 26,102 writes cost 132 ticks each and *none*
  of it is stall. Only reads are on the critical path.
* **`LDA` is the most expensive instruction in the machine** — 801 ticks, 2.4x
  `LD` — because it is the only opcode that reads the *map* bank, whose ring is
  334+32 cells for 352 words and which sits at chain slot 2 behind two extra
  gates. On only 804 reads a frame it is **9.61% of the run**.
* **Every read-taking opcode costs the same 128–158 ticks of dispatch.** The
  spread across all 22 opcodes is 76 (`SND`) to 162 (`IN`).

---

## 3. Question 1 — is dispatch cost uniform, or does one opcode dominate?

**Uniform, and four of its five stages are not layout variables at all.**

| stage | ticks/instruction | total | % run | set by |
|---|---:|---:|---:|---|
| fetch `>rbr` | **4, constant** | 700,612 | 1.16% | nothing — it is four cells |
| trie descent | **26–34** | 5,270,998 | 8.74% | `OPCODE_SLOTS` (the leaf's rank) |
| drop to the bus | **0–42** | 3,301,798 | 5.47% | `LANE_ORDER` (141 − the lane's row) |
| return bus, west | 1–38 | 4,913,711 | 8.15% | the lane's exit column − 9 |
| riser, north | **22, constant** | 3,853,366 | 6.39% | the lane band's height |
| **total** | **103.0** | **18,040,485** | **29.91%** | 175,153 instructions |

The lane micro-programs themselves are the other 3,770,271 ticks (6.25%) of the
36.16% dispatch walk.

### The trie is rank-independent, and that is structural

`OPCODE_SLOTS` exists to move a leaf's rank. Measured, the trie descent costs
**26 to 34 ticks whatever the rank**: `MUL` (row 109) 26, `LD` (row 121) 28,
`SND` (row 141) 30, `SUB` (row 115) 34. That is not a coincidence — the trie is
a *balanced spatial* binary split of the lane band, so each level halves the
remaining vertical distance and every root-to-leaf path sums to about the same
travel. Rank cannot buy travel it does not cost.

### The one free variable is the row, and it is anti-correlated with heat today

`drop = 141 − the lane's row`, exactly, for every one of the 22 lanes. The hot
opcodes are in the *middle* of the band and the cold ones ring both ends:

| opcode | execs | row | drop | | opcode | execs | row | drop |
|---|---:|---:|---:|---|---|---:|---:|---:|
| LD | 41,622 | 121 | **20** | | JMPF | 4,782 | 139 | 2 |
| ST | 26,102 | 119 | **22** | | JMPS | 1,212 | 133 | 8 |
| ADD | 15,116 | 117 | **24** | | SND | 1,742 | 141 | **0** |
| SUB | 10,103 | 115 | 26 | | INCM | 1,201 | 105 | 36 |
| DIV | 8,673 | 113 | 28 | | MOVA | 960 | 103 | 38 |
| LDA | 7,235 | 111 | 30 | | IN | 889 | 99 | 42 |

`LD` at row 121 pays 20 ticks a shot for 41,622 shots (832,440 ticks, 1.38%);
`SND` at row 141 pays 0 for 1,742.

**Ceiling on any re-layout** — permute the opcodes over the lane slots they
already have, hottest onto cheapest. Feasibility (pipe bindings, slab adjacency,
lane width) only makes it worse:

| what a re-layout moves | today | % run | best | saved | % run |
|---|---:|---:|---:|---:|---:|
| trie — what `OPCODE_SLOTS`' rank changes | 5,270,998 | 8.74% | 4,913,262 | 357,736 | **0.59%** |
| drop — what `LANE_ORDER`'s row changes | 3,301,798 | 5.47% | 1,572,968 | 1,728,830 | **2.87%** |
| drop + return bus, if the exits moved too | 8,215,509 | 13.62% | 4,087,148 | 4,128,361 | 6.84% |

**Answer: `LANE_ORDER` is worth ~5x what `OPCODE_SLOTS`' rank is worth for
walk, and both together are capped at 3.46% of the run.** `OPCODE_SLOTS` should
stay tuned for ROM cells — the walk lever it also holds is 0.59%, and it cannot
be concentrated on the hot opcodes because the trie is flat.

Two caveats on the `LANE_ORDER` 2.87%:

* **The registry's own cost model does not match the measurement.** Its comment
  says "a row above the fetch row costs 2 ticks per row of height while every
  row below it costs a constant". Measured, the drop is `141 − row` on *both*
  sides of the fetch row — linear all the way, no discontinuity at y=120. Any
  re-sweep should re-derive the weights, not re-use them.
* **The bottom rows are occupied for a reason.** `JMPF`/`BRZ`/`BRN`/`JMPS` sit
  at rows 133–139 because their discard slabs are at rows 143–152 and a classic
  slab's discard `r` must stay nearer the ROM pipe than the store's response
  pipe (§7.1). Moving `LD` to row 141 means moving a branch lane up.

### The 17-cell walk east, which is neither knob

The part of a memory instruction that is neither trie nor drop is the lane
itself, and its shape is a *binding* artifact. `LD`'s lane row is
`.................srMv` — **seventeen no-op cells** before the `s`/`r` pair,
because x=39 is the first column where the store's request pipe is the nearest
pipe. Every memory lane has the same 16–17 cells of padding. The man walks them
east to reach the pipe and then walks the same distance back west along the
collector row, so the padding is paid **twice**: about
`16.5 x 2 x 113,592 = 3.75M ticks, 6.2% of the run`, inside a total east-out
west-back of **6,352,378 ticks (10.53%)** across the 113,592 memory
instructions. Neither `LANE_ORDER` nor `OPCODE_SLOTS` touches any of it.

---

## 4. Question 2 — how much of a store read's blocked time is unavoidable?

**334.5 ticks a read, 87,490 reads, 29,262,833 ticks = 48.51% of the run.**
(The prior profile's 332 is the same quantity on the previous machine.)

The distribution is measured exactly, one entry per blocked run:

| statistic | ticks |
|---|---:|
| **minimum ever observed** | **196** |
| p25 | 248 |
| p50 | 260 |
| p75 | 318 |
| p95 | 720 |
| p99 | 920 |
| **mean** | **334.5** |
| maximum | 3,386 |

Half of all reads land within 64 ticks of the floor. The mean is dragged up by a
long tail: p95 is 2.8x the floor.

### The split

| component | ticks/read | total | % run | how it is known |
|---|---:|---:|---:|---|
| **pipe transit** | **142.2** | 12,443,858 | **20.63%** | pipe lengths × reads, per bank |
| gate + adapter + bank **walking** | **~75** | ~6.6M | ~10.9% | fastest read ever seen (196) minus the nearest bank's 121 cells of pipe |
| **ring rotation + queueing** | **~117** | ~10.2M | ~17.0% | the remainder |

By bank — the chain order is `TAPED_BANK_ORDER = (3,2,0,1)`, so chain slot 0 is
address bank 3 (`PW`, `WADDR`, `TMP`…) and slot 1 is address bank 2 (the DDA
scalars):

| bank | reads | % | round trip | transit ticks | % run |
|---|---:|---:|---:|---:|---:|
| slot 1 (DDA scalars, 517–531) | 47,755 | 54.6% | 146 cells | 6,972,230 | 11.56% |
| slot 0 (`PW`/`WADDR`, 532–600) | 29,579 | 33.8% | 121 cells | 3,579,059 | 5.93% |
| slot 2 (the map, 1–352) | 7,023 | 8.0% | 170 cells | 1,193,910 | 1.98% |
| slot 3 (353–516) | 3,133 | 3.6% | 223 cells | 698,659 | 1.16% |

The per-bank floor is measurable, because each opcode's reads concentrate in one
bank: `LD`'s fastest read is 196 against 121 cells of transit → **75 ticks**;
`DIV`'s is 198 against 121 → 77; `ADD`'s is 226 against 146 → 80; `MUL`'s is 228
against 146 → 82. So the walking floor is **75–82 ticks and essentially
bank-independent** — that is the adapter man, the gate men and the bank man each
taking their turn, and no pipe change removes it.

`LDA` is the exception that proves it: its fastest read is 367 against 170 cells
→ a **197-tick** floor, because slot 2 is two gates further down the chain and
its ring is 366 cells for 352 words.

**Answer: of the 334.5 ticks, 142 is pipe transit (the part the teleports are
attacking), about 78 is an irreducible walk through the adapter/gate/bank men,
and about 117 is the tape rotating to the word plus queueing behind other
requests. Deleting every pipe in the store leaves 27.88% of the run still
blocked on it.**

---

## 5. Question 3 — which opcode would benefit most from being executed less often?

**`LD`, and specifically the DDA's cursor reads.**

The read census is exact for 85,329 of the 87,490 reads (97.5%): a read-only
opcode sends exactly one value per execution and blocks exactly once, so its
sends *are* addresses, which is checkable and checked. (The raw request stream
cannot be used — it interleaves write data with addresses.)

| addr | symbol | reads | % | /frame | by opcode |
|---:|---|---:|---:|---:|---|
| 533 | `WADDR` | 12,717 | 14.9% | 1,413 | **LD:12,717** |
| 521 | `SDX` | 12,657 | 14.8% | 1,406 | **LD:12,657** |
| 522 | `SDY` | 8,705 | 10.2% | 967 | SUB:6,950 LD:1,755 |
| 532 | `PW` | 8,673 | 10.2% | 964 | DIV:6,978 LD:1,695 |
| 523 | `DDX` | 6,219 | 7.3% | 691 | ADD:5,481 MUL:512 SUB:226 |
| 525 | `S4X` | 5,481 | 6.4% | 609 | ADD:5,481 |
| 524 | `DDY` | 2,267 | 2.7% | 252 | ADD:1,469 MUL:512 SUB:286 |
| 1–256 | the map words | 6,978 | 8.2% | 775 | **LDA:6,978** |

**The seven DDA scalars are 56,719 reads — 66.5% of the census, 6,302 a
frame.** Everything else in the program put together is a third.

Solving the census against the source gives the loop's shape exactly:
**7,121 DDA steps in nine frames (791 a frame, ~12.4 per screen column), of
which 5,536 take the x-arm and 1,584 the y-arm**, plus 60 downward
quarter-column wraps. That model predicts 12,716 `WADDR` reads against the
12,717 measured.

### The redundant reload

```asm
xarm0:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR      ; ISA: "store[addr] = ACC (ACC preserved)"  -> ACC = WADDR
        LD  WADDR      ; ...re-reads the value ACC already holds.  343 ticks.
        LDA            ; the x-side hit test
```

`ST` is documented **ACC-preserving**, and the program relies on that two lines
later in the y-arm (`ST PW` / `BRZ ywru0` only works if `ST` leaves ACC alone).
So the `LD WADDR` between `ST WADDR` and `LDA` is a pure reload. It appears at
**16 static sites** — one per unrolled x-arm — and runs **5,536 times in nine
frames, 615 a frame**.

At `LD`'s full measured cost of 470.9 ticks that is **2,606,902 ticks = 4.32% of
the run**, for deleting one instruction sixteen times. Three more sites of the
same shape exist in the movement path (`NEWY` ×2, `NEWX`, `HDG`) and are worth
almost nothing — they run once a frame.

### The ceiling above it

If `SDX`, `SDY`, `WADDR` and `PW` could live in registers across an unrolled DDA
step, the 56,719 scalar reads would go with them: at the machine's mean read
cost that is ~18.9M ticks, **31% of the run**. The CPU has ACC and one spill
slot, so that ceiling is not reachable — but it says how much room the program
level has compared to the layout level's 3.46%.

---

## 6. Ranked opportunities

**Nothing here was implemented.** This is the map, and this session has already
had three cases where the obvious target was wrong — so every row states what it
is measured against and what would falsify it.

### 1. Delete the DDA's redundant `LD WADDR` — est. **−4.0 to −4.3%**

5,536 executions × 470.9 ticks = 2,606,902 = 4.32%. One instruction, sixteen
unrolled copies, and the ISA's own documentation says the value is already in
ACC. Highest confidence and lowest cost of anything on this list; it is a
program change, so it needs no re-layout and no re-binding.

**What would falsify it:** if the assembler or the CPU's `ST` micro-program
clobbers ACC in practice despite the ISA text. Check by asserting the emulator
agrees before and after (`lm1.emulator`), then the pixel gate.

### 2. Teleport `adapter -> store:req->bank0` (60 cells) — est. **−8.5 to −8.7%**

5,249,400 ticks (**8.70%**) is one pipe: every one of the 87,490 CPU-blocking
reads walks all 60 of its cells, and so does every write's request. It is the
longest leg on the critical path and the only one *every* access pays. The
mechanism is the one that already worked twice: a room's `R`/`U` has no distance
term, so a grown room spans canvas for free.

This is unchanged from `DOOM-PROFILE.md` §4.1 and remains the largest single
lever. Its caveat is unchanged too: **deleting rooms in favour of a plain pipe
cost +4.14% on this same path** — this is "grow a room", never "remove one".

### 3. Shorten the `reqK -> bankK` arms (45/45/44/97) — est. **−4 to −6%**

4,092,943 ticks (**6.79%**). 77,334 of the 87,490 reads use a 45-cell arm. Same
lever and same mechanism as #2, on disjoint legs, so the two are additive.

### 4. Re-sweep `LANE_ORDER` against the *measured* drop model — est. **−1.5 to −2.8%**

Ceiling 1,728,830 ticks (**2.87%**), and the registry's current model is wrong
(it assumes rows below the fetch row cost a constant; they do not). Moving `LD`
alone from row 121 to the bottom of the band is 832,440 ticks (1.38%). The
constraint is that the bottom rows hold the branch lanes for their slabs'
sake — so the realistic move is a swap of the hot memory lanes with the cold
top-of-band lanes (`IN`, `NEG`, `MOVA`, `INCM`, `ADDI`), which is worth roughly
half the ceiling.

**What would falsify it:** the response pipe's binding. `DOOM-PROFILE.md` notes
a full bottom-fill was already tried and fails binding.

### 5. Give `LDA` a shorter path to the map bank — est. **−1.5 to −3.8%**

`LDA` is 9.61% of the run on 804 reads a frame, at **653 ticks a read against
the machine's 334.5** — the worst per-access cost in the machine by 2x. Its
floor alone is 197 ticks. Two separable causes: 170 cells of transit (49 more
than slot 0) and a 366-cell ring for 352 words. Bringing it to the machine mean
is 2,301,000 ticks (3.81%); the transit half of that is 0.59% and is a
`TAPED_BANK_ORDER` question, the ring half is a `TAPED_BANKS` question.

**Note the tension with `DOOM-PROFILE.md` §4.3**, which proposed promoting the
*slot-1* bank on request volume. On read *cost* the map bank is the one that
hurts. Both cannot lead; the measurement that settles it is 7,023 reads × 49
cells (map, 0.57%) versus 18,176 net reads × ~46 cells (scalars, 1.4%) — so the
existing proposal still wins on transit, and `LDA`'s case is about the **ring**,
not the chain.

### 6. `OPCODE_SLOTS` for walk — **do not**. Ceiling 0.59%, and unreachable.

The trie is a balanced spatial split; every leaf costs 26–34 ticks whatever its
rank. `OPCODE_SLOTS` is correctly tuned for ROM cells and should stay that way.
Recorded here so the knob is not re-swept in the belief that it holds the 30.4%
dispatch figure. It holds 8.74%, of which 0.59% is movable.

### 7. The 17-cell walk east on the memory lanes — ~6.2%, and no knob

The memory lanes pad 16–17 no-op cells eastward to reach the first column where
the store's request pipe is nearest, and pay the same distance again walking
back west — about 3.75M ticks, inside a total east-out west-back of 6,352,378
across 113,592 memory instructions. This is a *binding geometry* problem — it would
take moving the store's attachment point west, or a second return bus — and it
is the largest piece of dispatch that neither registry can reach. Flagged for
whoever looks at the CPU's shape rather than its ordering; not costed here.

### Not worth doing

* **Attacking `ST`.** It is 26,102 executions and 5.71% of the run, and *none*
  of it is stall. It is already the cheapest memory instruction at 132 ticks.
* **The branch slabs' discard loops** (`BRN` 3,970,313 + `BRZ` 2,400,139 +
  `JMPF` 546,282 = 11.4% of the run). That is the 16x-unrolled DDA paying for
  its own forward jumps, and it is the price of *not* paying `JMPS`'s 1,169
  ticks a seek. `JMPS` runs 135 times a frame and costs 2.35% of the run; the
  discard loops run 3,260 times a frame and cost 11.4%. The trade is already
  roughly balanced — re-tuning `seek_threshold` moves ticks between two lines of
  this table, and would need its own sweep to say which way.
* **`rom->cpu`**, at 1,303,150 ticks (2.16%) of CPU block, most of which is drum
  rotation after the seek arrives.

---

## Tooling and rendered output

| file | what |
|---|---|
| `scratch/doom_opcodes.py` | this profiler — tables, `opcodes.json`, `index.html` |
| `scratch/doom_case.py` | the shared harness: the gated case, the builder-pinned names |
| `scratch/doom_heatmap.py` | the region profile this builds on |
| `scratch/doom_pipes.py` | pipe traffic and the critical-path decomposition |

Rendered to
`/private/tmp/claude-502/-Users-ptaykalo-Projects-icfpc-2026-randomfun2026solvers/1c723037-d2b8-44f3-8710-1a17734c20ce/scratchpad/doom-opcodes/`:

| file | what |
|---|---|
| `index.html` | the readable page — hero, the four-part split, every table, the stall distribution |
| `opcodes.txt` | the same tables as text |
| `opcodes.json` | per-opcode × per-class ticks, the per-opcode stall histograms and address censuses, the lane geometry |

The engine change is **opt-in and off by default**. `OpcodeTags` is a new,
trailing argument to `FastLittleman.run(..., profile=True, opcodes=...)`;
omit it and both the native request string and its reply are byte-identical to
what `doom_heatmap.py` and `doom_pipes.py` already send and parse, and omitting
`profile=` too leaves them identical to what every ordinary caller sends. Default
suite after the change: **2748 passed, 68 skipped**; the DOOM set 122 passed and
the pixel gate 12/12.
