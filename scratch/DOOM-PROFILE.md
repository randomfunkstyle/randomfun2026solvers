# deadman-3d_taped — heatmap, pipe traffic, and what they say

Measurement only: **no machine behaviour was changed**. `deadman-3d.man` /
`_trim` / `_v2` are still `f62d63fd…`, `_taped.man` / `_m6_taped.man` still
`ba754ad1…`, `deadman-3d.input.txt` still `654d35d6…`, and the default suite is
still 2748 passed / 68 skipped.

## Look here first

> **The CPU man spends 29,048,119 ticks — 47.19% of the whole run — standing
> still on the memory lanes' `r`, blocked on `store:collector->cpu`.**
> That is one glyph column, `x = 46`, in eight of the CPU's lane rows, and
> `cpu:lane:LD` alone is 22.87% of the run.

Everything else is second order. The other half of the run (50.06%) is the CPU
walking his own dispatch — trie, lane, return bus — and only 2.75% of the run is
spent blocked on anything that is not the store (2.14 of it instruction fetch).

`cpu->drum`, the 437-cell pipe, is **0.03% of pipe traffic and at most 0.86% of
the run**. It is the longest pipe in the machine and very nearly the cheapest.

## Which engine produced these numbers

Every number below comes from the **native `fast_littleman` backend** (the C++
tick loop behind the Python engine, `fast_littleman_native.cpp`), running the
**gated** display-judged case `WALK[:8]` — 9 rounds, 61,555,215 ticks, `passed`.
`littleman/tools/heatmap.mjs` could not be used: it drives `littleman.wasm`,
which OOMs its 4 GB Go heap before it can load this machine. The two design
properties that make the .mjs profiler sound were kept — sample men's
*positions* (so a man blocked on `r` is counted every sample), and run **gated**
(ungated, the judge releases every round at once and you measure a jam).

* pipe **sends / receives / blocks** are **exact** — counted in the engine.
* the heatmap is sampled at **stride 1**, so its counts are exact runner-ticks
  too, not an estimate. Per-region totals sum to 61,555,216 = the run.
* the engine parks a blocked man on a wait list, so *why* he is standing still
  is known rather than inferred from "sampled twice in the same cell".

The 9-round case is 61.5M ticks = 6.84M/round; the 116-round tour is
838,737,298 = 7.23M/round. Same machine, same mix — the percentages carry.

Region and room names come from the builder
(`lm1.machine.build_for("deadman-3d", store="taped")`), which the harness
asserts reproduces the checked-in grid byte for byte.

Reproduce:

```
uv run python scratch/doom_heatmap.py --out <dir>   # heatmap + PNG/HTML + tables
uv run python scratch/doom_pipes.py                 # pipe traffic + critical path
```

Rendered output: `heat.png`, `wait.png`, `index.html`, `heatmap.txt`,
`pipes.txt` (see "Rendered output" at the end).

---

## 1. The run's tick budget — the CPU man, by region

The CPU is alive for every tick, so his own split **is** the run. This is the
"where to look first" table.

| region | ticks | % run | blocked | % run | walking | % run |
|---|---:|---:|---:|---:|---:|---:|
| `cpu:lane:LD` | 15,202,044 | **24.70%** | 14,078,250 | 22.87% | 1,123,794 | 1.83% |
| `cpu:return:collector` | 5,616,680 | 9.12% | 0 | 0.00% | 5,616,680 | 9.12% |
| `cpu:trie` | 5,270,998 | 8.56% | 0 | 0.00% | 5,270,998 | 8.56% |
| `cpu:lane:LDA` | 4,939,044 | 8.02% | 4,724,216 | 7.67% | 214,828 | 0.35% |
| `cpu:lane:ADD` | 4,683,204 | 7.61% | 4,259,956 | 6.92% | 423,248 | 0.69% |
| `cpu:other` | 4,633,652 | 7.53% | 652,054 | 1.06% | 3,981,598 | 6.47% |
| `cpu:slab:BRN` | 4,068,633 | 6.61% | 392,775 | 0.64% | 3,675,858 | 5.97% |
| `cpu:return:riser` | 3,853,366 | 6.26% | 0 | 0.00% | 3,853,366 | 6.26% |
| `cpu:lane:SUB` | 3,018,012 | 4.90% | 2,704,104 | 4.39% | 313,908 | 0.51% |
| `cpu:slab:BRZ` | 2,375,712 | 3.86% | 266,006 | 0.43% | 2,109,706 | 3.43% |
| `cpu:lane:DIV` | 2,101,400 | 3.41% | 1,837,635 | 2.99% | 263,765 | 0.43% |
| `cpu:lane:MUL` | 768,149 | 1.25% | 690,896 | 1.12% | 77,253 | 0.13% |
| `cpu:lane:ST` | 745,972 | 1.21% | 0 | 0.00% | 745,972 | 1.21% |
| `cpu:fetch` | 700,864 | 1.14% | 252 | 0.00% | 700,612 | 1.14% |
| `cpu:lane:BRN` | 623,636 | 1.01% | 0 | 0.00% | 623,636 | 1.01% |
| `cpu:lane:INCM` | 542,966 | 0.88% | 502,438 | 0.82% | 40,528 | 0.07% |
| `cpu:lane:BRZ` | 535,031 | 0.87% | 0 | 0.00% | 535,031 | 0.87% |
| `cpu:slab:JMPF` | 407,525 | 0.66% | 3,995 | 0.01% | 403,530 | 0.66% |
| `cpu:lane:SND` | 373,487 | 0.61% | 340,824 | 0.55% | 32,663 | 0.05% |
| `cpu:lane:JMPF` | 305,444 | 0.50% | 0 | 0.00% | 305,444 | 0.50% |
| `cpu:lane:MOVA` | 282,480 | 0.46% | 250,624 | 0.41% | 31,856 | 0.05% |
| `cpu:lane:JMPS` | 187,700 | 0.30% | 0 | 0.00% | 187,700 | 0.30% |
| remaining lanes (`ADDI`, `IN`, `LDI`, `MULI`, `MODI`, `SUBI`, `DIVI`, `NEG`, `slab:JMPS`) | 319,217 | 0.52% | 35,903 | 0.06% | 283,314 | 0.46% |
| **total** | **61,555,216** | **100%** | **30,739,928** | **49.94%** | **30,815,288** | **50.06%** |

Read it as two halves:

* **49.94% blocked**, of which 47.19 points is the store answer. The blocked
  cells are the `r` at `x = 46` on every lane that takes a memory operand:
  `LD` 22.87%, `LDA` 7.67%, `ADD` 6.92%, `SUB` 4.39%, `DIV` 2.99%, `MUL` 1.12%,
  `INCM` 0.82%, `MOVA` 0.41%.
* **50.06% walking**: `return:collector` 9.12 + `trie` 8.56 + `return:riser`
  6.26 + `other` 6.47 = 30.4% is pure dispatch overhead (fetch → trie →
  lane → return), and the four branch slabs are another 10.7%.

### All men, for context

87% of *all* runner-ticks are parked, which sounds alarming and is not: sixteen
of the eighteen men are servants that idle on an `r` until work arrives. Only
the CPU's parked time is a cost. Working ticks per man:

| man | working ticks | % of all work | parked | hottest cell |
|---|---:|---:|---:|---|
| `cpu` #2 | 30,815,288 | 21.22% | 50% | (46,121) `'r'` 23% |
| `store:bank0` #5 | 20,616,837 | 14.20% | 67% | (105,117) `'r'` 67% |
| `store:ring->bank0` #9 | 14,702,452 | 10.12% | 76% | (100,137) `'r'` 77% |
| `store:bank2` #7 | 13,917,977 | 9.58% | 77% | (201,117) `'r'` 77% |
| `store:bank1` #6 | 13,639,585 | 9.39% | 78% | (153,117) `'r'` 78% |
| `store:ring->bank2` #12 | 13,017,037 | 8.96% | 79% | (195,145) `'s'` 43% |
| `rom` #0 | 9,678,194 | 6.66% | 84% | (201,21) `'s'` 1% |
| `store:req->bank0` #13 | 6,393,861 | 4.40% | 90% | (66,152) `'U'` 90% |
| … 10 more, all under 4% each | | | | |

---

## 2. Pipe traffic — every pipe, gated 9-round run

`sends` = stores into the pipe (`s`/`S`, plus the input room's own injection);
`recvs` = reads out of it (`r`/`R`/`U`, plus the output room's and the display's
own takes); `traversals` = recvs × length.

| # | pipe | len | sends | recvs | traversals | %trav | send-blk | recv-blk | wait (ticks) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 11 | `bank2 -> ring2` | 334 | 2,603,728 | 2,603,408 | 869,538,272 | 48.47% | 0 | 33 | 462 |
| 13 | `ring0 -> bank0` | 98 | 2,940,490 | 2,940,420 | 288,161,160 | 16.06% | 0 | 0 | 0 |
| 9 | `bank0 -> ring0` | 56 | 2,940,490 | 2,940,490 | 164,667,440 | 9.18% | 0 | 174,923 | 46,852,764 |
| 12 | `bank3 -> ring3` | 194 | 621,555 | 621,419 | 120,555,286 | 6.72% | 0 | 29 | 310 |
| 14 | `ring1 -> bank1` | 98 | 1,001,712 | 1,001,696 | 98,166,208 | 5.47% | 0 | 0 | 0 |
| 16 | `ring2 -> bank2` | 32 | 2,603,407 | 2,603,375 | 83,308,000 | 4.64% | 29,678 | 0 | 48,537,717 |
| 0 | **`rom -> cpu`** | 34 | 1,797,774 | 1,797,740 | 61,123,160 | 3.41% | 209,755 | 201,990 | 53,180,172 |
| 10 | `bank1 -> ring1` | 56 | 1,001,712 | 1,001,712 | 56,095,872 | 3.13% | 0 | 285,997 | 56,546,654 |
| 15 | `ring3 -> bank3` | 28 | 621,418 | 621,390 | 17,398,920 | 0.97% | 14,817 | 0 | 58,447,812 |
| 8 | **`adapter -> req0`** | 60 | 259,769 | 259,769 | 15,586,140 | 0.87% | 0 | 144,017 | 55,161,355 |
| 18 | `req1 -> bank1` | 45 | 140,063 | 140,063 | 6,302,835 | 0.35% | 0 | 60,149 | 47,915,631 |
| 17 | `req0 -> bank0` | 45 | 96,439 | 96,439 | 4,339,755 | 0.24% | 0 | 26,953 | 40,938,379 |
| 21 | `req0 -> req1` | 25 | 163,330 | 163,330 | 4,083,250 | 0.23% | 0 | 73,748 | 57,352,127 |
| 23 | `req2 -> bank3` | 97 | 8,165 | 8,165 | 792,005 | 0.04% | 0 | 3,056 | 57,940,793 |
| 19 | `req2 -> bank2` | 44 | 15,102 | 15,102 | 664,488 | 0.04% | 0 | 7,375 | 47,637,239 |
| 2 | **`collector -> cpu`** | 7 | 87,490 | 87,490 | 612,430 | 0.03% | 0 | 87,490 | 29,048,119 |
| 22 | `req1 -> req2` | 25 | 23,267 | 23,267 | 581,675 | 0.03% | 0 | 11,142 | 60,902,494 |
| 20 | **`cpu -> drum` (rom)** | 437 | 1,212 | 1,212 | 529,644 | 0.03% | 0 | 569 | 11,932 |
| 4 | `bank1 -> collector` | 7 | 47,755 | 47,755 | 334,285 | 0.02% | 0 | 87,491 | 61,030,273 |
| 28 | `stream -> display` | 15 | 21,308 | 21,308 | 319,620 | 0.02% | 0 | 0 | 0 |
| 7 | **`cpu -> adapter`** | 2 | 144,016 | 144,016 | 288,032 | 0.02% | 0 | 115,754 | 58,489,097 |
| 3 | `bank0 -> collector` | 7 | 29,579 | 29,579 | 207,053 | 0.01% | 0 | 87,491 | 61,030,273 |
| 27 | `stream -> display` | 15 | 12,924 | 12,924 | 193,860 | 0.01% | 0 | 0 | 0 |
| 5 | `bank2 -> collector` | 7 | 7,023 | 7,023 | 49,161 | 0.00% | 0 | 87,491 | 61,030,273 |
| 25 | `relay -> stream` | 3 | 15,217 | 15,217 | 45,651 | 0.00% | 0 | 1,545 | 1,545 |
| 26 | `stream -> relay` | 3 | 15,217 | 15,217 | 45,651 | 0.00% | 0 | 15,218 | 61,463,910 |
| 29 | `relay2 -> stream` | 3 | 7,376 | 7,376 | 22,128 | 0.00% | 5,840 | 0 | 850,016 |
| 30 | `stream -> relay2` | 3 | 7,376 | 7,376 | 22,128 | 0.00% | 0 | 5,841 | 60,660,940 |
| 6 | `bank3 -> collector` | 7 | 3,133 | 3,133 | 21,931 | 0.00% | 0 | 87,491 | 61,030,273 |
| 24 | `cpu -> stream` | 11 | 1,742 | 1,742 | 19,162 | 0.00% | 490 | 676 | 59,771,958 |
| 1 | **`input -> cpu`** | 2 | 888 | 888 | 1,776 | 0.00% | 0 | 9 | 35,903 |
| 31 | `stream -> display` | 35 | 9 | 9 | 315 | 0.00% | 0 | 0 | 0 |
| | **TOTAL** | | **17,240,686** | **17,240,050** | **1,794,077,293** | | 260,580 | 1,566,478 | |

Cross-check against the builder's named routes
(`lm1.machine.Machine.route_lengths`): `rom->cpu` 34 = pipe 0, `input->cpu` 2 =
pipe 1, `cpu->adapter` 2 = pipe 7, `adapter->store` 58 ≈ pipe 8 (60 parsed
cells), `store->cpu` 6 ≈ pipe 2 (7 parsed cells), `cpu->drum` 437 = pipe 20.
The two off-by-one/two entries are the builder counting a drawn route where the
parser counts path cells; the parsed length is the one that costs ticks.

Two things this table does **not** mean:

* **Traversals are not ticks.** The engine shifts every pipe once per tick, in
  parallel, whatever its length. What a long pipe costs is *latency*: a value
  takes `length` ticks to arrive. Traversals are therefore a **latency budget**,
  and they only turn into run time where somebody is blocked waiting for that
  value.
* **`wait` is not a bottleneck.** Sixteen of these men are servants; a `wait`
  near 61.5M just means "this man idles almost always". `bank3 -> collector` has
  the maximum possible wait (61,030,273) and 3,133 receives.

The 48% of traversals on `bank2 -> ring2` is a **storage medium spinning**, not
a cost: those are the store's own words recirculating in their ring. A ring's
length is its capacity, and its rotational latency is what a read waits through
— which is real, but it is already inside the CPU-blocked number below.

---

## 3. Joining them: the critical path

The CPU is the only man on it. Exact ticks:

| the CPU is blocked on | ticks | % of the run |
|---|---:|---:|
| `store:collector->cpu` (the store answer) | 29,048,119 | **47.19%** |
| `rom->cpu` (instruction fetch starving) | 1,315,082 | 2.14% |
| `cpu->stream:unit` (painter backpressure) | 340,824 | 0.55% |
| `input->cpu` | 35,903 | 0.06% |
| **blocked, all pipes** | **30,739,928** | **49.94%** |
| walking his own dispatch | 30,815,287 | 50.06% |

**87,490 store reads** in the 9-round run (9,721 a frame) → **332 ticks blocked
per read on the answer pipe** (351 counting every pipe the CPU blocks on). Decomposing that by pipe length is sound here precisely because the
CPU blocks: the request is issued and nothing else happens on that man until the
answer lands, so every leg's cells are paid serially.

| bank (chain slot) | reads | request cells | round trip | transit ticks | % run | request path |
|---|---:|---:|---:|---:|---:|---|
| slot 1 | 47,755 | 132 | 146 | 6,972,230 | 11.33% | `cpu->adapter`(2) → `adapter->req0`(60) → `req0->req1`(25) → `req1->bank1`(45) |
| slot 0 | 29,579 | 107 | 121 | 3,579,059 | 5.81% | `cpu->adapter`(2) → `adapter->req0`(60) → `req0->bank0`(45) |
| slot 2 | 7,023 | 156 | 170 | 1,193,910 | 1.94% | … → `req0->req1`(25) → `req1->req2`(25) → `req2->bank2`(44) |
| slot 3 | 3,133 | 209 | 223 | 698,659 | 1.14% | … → `req0->req1`(25) → `req1->req2`(25) → `req2->bank3`(97) |
| **TOTAL** | **87,490** | | | **12,443,858** | **20.22%** | |

So of the CPU's 30.74M blocked ticks:

* **12.44M (40%, = 20.22% of the run) is pure pipe transit** — cells the answer
  and the request walk. This is the part a room/teleport deletes.
* **18.30M (60%, = 29.7% of the run) is ring seek plus the gate/adapter men's
  own walking** — the part only a shorter ring, fewer accesses, or a cheaper
  gate touches.

Per-leg transit, ranked (the same 12.44M, split by which pipe it is spent in):

| leg | cells | CPU-blocking reads through it | transit ticks | % run |
|---|---:|---:|---:|---:|
| `adapter -> req0` | 60 | 87,490 (all) | 5,249,400 | **8.53%** |
| `reqK -> bankK` (45/45/44/97) | — | per-bank | 4,092,943 | 6.65% |
| `req0 -> req1`, `req1 -> req2` | 25 each | 57,911 / 10,156 | 1,701,675 | 2.76% |
| `bankK -> collector` + `collector -> cpu` | 7 + 7 | 87,490 | 1,224,860 | 1.99% |
| `cpu -> adapter` | 2 | 87,490 | 174,980 | 0.28% |
| `cpu -> drum` (seek target) | 437 | 1,212 sends | ≤529,644 | ≤0.86% |

`store->cpu` was already collapsed from ~59 cells to 7, and this is the receipt:
the answer legs together are 1.99% of the run. Had they stayed at 59 cells the
same 87,490 reads would pay 5.2M ticks more — the same size as the biggest
remaining pipe term. The teleport was worth exactly what it claimed.

---

## 4. Ranked improvements

Ordered by measured value. **None of these were implemented** — this task's
deliverable is the analysis.

### 1. Teleport `adapter -> store` (60 cells) — est. −8.0 to −8.5%

5,249,400 ticks (8.53% of the run) is one pipe: every one of the 87,490
CPU-blocking reads walks all 60 cells of it, and so does every write's request
(144,016 requests, 259,769 words). It is the second-longest pipe on the critical
path and the only one every single access pays.

The mechanism is the one that already worked once: `R`/`U` take from the pipe's
destination cell **wherever the man is standing** — "nearest" only picks *which*
pipe, it is not a walk (`SPEC.md` §Nearest). So a wide/tall room spans canvas
distance for free, and the pipe attached to it can be 2 cells. That is precisely
what the store answer path does today (the collector is a 191-column room whose
four banks each attach a 7-cell pipe at their own x).

Geometrically it looks available: the adapter is `(63,112)-(76,117)` and the
first gate is `(65,149)-(90,157)`, and the corridor between them — x ∈ [61,77],
y ∈ [118,147] — is **empty in the checked-in grid** except for the very pipe
being replaced (the `|` column at x = 78). Extending the adapter room south to
y ≈ 148, or the gate room north, makes the request pipe ~2 cells.

**Caveat that must be respected**: deleting rooms in favour of a plain pipe cost
+4.14% on this same path. The room *is* the mechanism. This is "add a room /
grow a room", never "remove one".

### 2. Shorten the `reqK -> bankK` arms (45/45/44/97) — est. −4 to −6%

4,092,943 ticks (6.65%). Each gate hands its own bank a 45-cell pipe, and the
terminal bank a 97-cell one. Same lever as #1 and the same mechanism: a gate
room that reaches its bank's wall pays 2 cells instead of 45. The 97-cell
`req2 -> bank3` is the worst per-access (223-cell round trip) but only 3,133
reads use it; the 45s are where the volume is (77,334 of the 87,490 reads).

Attacking #1 and #2 together would take the average round trip from ~142 cells
to ~30 and recover most of the 20.22%. They are strongly *sub-additive with
nothing* — unlike the gate-compaction/reorder pair, these are disjoint legs.

### 3. Put the read-hottest bank first in the chain — est. −0.7 to −1.4%

`TAPED_BANK_ORDER[("deadman-3d","taped")] = (3, 2, 0, 1)` puts address bank 3
(PW/WADDR scalars) at chain slot 0 and address bank 2 (the DDA scalars) at slot
1. Measured on the wire here, **slot 1 is the busier of the two**: 140,063
requests / 47,755 reads against slot 0's 96,439 / 29,579. Every slot-1 access
pays one extra gate — 25 cells of pipe plus the gate's ~19–23-cell pass-through
arm. Swapping them is worth ≈ 18,176 net reads × ~46 cells ≈ 0.84M ticks
(1.4%), or 0.45M (0.74%) counting the pipe alone.

**But `(2, 3, 0, 1)` is not expressible** — a gate peels a bank off an *end* of
the remaining contiguous space, and 2 is not an end of {0,1,2,3}. Getting the
DDA scalars to lead needs `TAPED_BANKS` re-cut so they sit at the top of the
address space, i.e. an address reassignment, not a registry one-liner. The
registry's own sweep lists `(3,2,1,0)`, `(3,0,2,1)`, `(0,3,2,1)` and address
order — it never tested leading with the DDA bank, because the split makes it
unreachable. Worth re-cutting the split, not worth re-sweeping the order.

### 4. Fewer store reads — the biggest ceiling, the largest change

9,721 reads a frame at 351 ticks blocked each **is** the 47.19%. Any caching of
hot scalars in the CPU's own registers, or fusing `LD`-then-op into one access,
converts at 332 ticks a read: 1,000 reads a frame removed is −3.0M ticks over
the 9 rounds, −4.9%. `cpu:lane:LD` alone (22.87% of the run) is the single
opcode to look at, with `LDA` (7.67%) next.

### 5. `cpu -> drum` (437 cells) — already in hand, and worth ≤0.86%

Measured for completeness, since another agent is teleporting it: 1,212 sends in
9 rounds, 529,644 traversal-ticks, and the CPU's *entire* blocked time on the
fetch path (`rom->cpu`) is 1,315,082 ticks = 2.14% of the run. So the teleport's
ceiling is 0.86% and its realistic value is less — the drum still has to rotate
to the seek target after the seek arrives, and that rotation is the rest of the
2.14%. Correct change, small prize. **Not proposed here, not implemented here.**

### 6. Dispatch walking — 30.4% of the run, no pipe involved

`return:collector` 9.12% + `trie` 8.56% + `return:riser` 6.26% + `cpu:other`
6.47%. This is the fetch → trie → lane → return walk, and it is the other half
of the machine. It is not a pipe problem and nothing in this profile suggests a
cheap lever on it; `scratch/deadman3d-opt/METRICS.md` iter-06 already swept
`LANE_ORDER` against it. Noted so the 50/50 split is not mistaken for
50% blocked / 50% idle.

### Not worth doing

* **The ring pipes** (`bank2->ring2` 334 cells, 48% of all traversals). That
  traffic is the tape spinning in place; shortening a ring means a smaller bank,
  and the ring lengths already track the bank sizes `(352,164,15,69)`.
* **`rom->cpu` (34 cells, 1.8M receives, 61M traversals, 3.41%)**. It is a
  *stream*: values pipeline, so the 34-cell latency is paid once per burst, not
  1.8M times. The CPU's real cost here is 2.14% total and most of it is drum
  rotation.
* **The display and stream pipes** (27/28/31, ≤0.02% traversals each). The
  painter unit keeps up: `cpu->stream:unit` blocks the CPU 0.55% of the run.

---

## Rendered output

Written to
`/private/tmp/claude-502/-Users-ptaykalo-Projects-icfpc-2026-randomfun2026solvers/1c723037-d2b8-44f3-8710-1a17734c20ce/scratchpad/doom-heatmap/`:

| file | what |
|---|---|
| `index.html` | the readable page — hero numbers, both heatmaps, all ranked tables |
| `heat.png` | per-cell occupancy at machine scale (287x253 ×4), log blue ramp, 8 hottest cells ringed |
| `wait.png` | the blocked-only subset, log orange ramp |
| `heatmap.txt` | every table as text |
| `heatmap.json` | per-cell counts, per-region, per-room, per-park |
| `pipes.txt` / `pipes.json` | the pipe table and the critical-path accounting |

## Tooling

| file | what |
|---|---|
| `scratch/doom_case.py` | shared harness: the gated case, the builder-pinned region/room/pipe names |
| `scratch/doom_heatmap.py` | the sampling profiler + PNG/HTML renderer |
| `scratch/doom_pipes.py` | the pipe-traffic profiler + critical-path accounting |

The engine change is **opt-in and off by default**:
`FastLittleman.run(..., profile=True, profile_stride=N)` adds two trailing
integers to the native request and a trailing section to its reply. Omit it and
the request string and the reply are byte-identical to what every existing
caller already sends and parses; the tick loop's only addition is a
`if (c.profile)` test on paths that were already branching. Default suite after
the change: **2748 passed, 68 skipped**.
