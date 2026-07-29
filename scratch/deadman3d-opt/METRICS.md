# deadman-3d frame-1 optimization log

Metric: native FastLittleman, round-gated WALK[:1] (boot + first frame), passed=True required.
Baseline: 31,080,274 ticks. Mandatory target (x5): <= 6,216,055. **Final: 5,213,912 (5.96x).**
Model: `a*instructions + 8*words_skipped`; `a` re-fitted from each native point
(157 on the display-heavy baseline mix, ~201 on the store-heavy final mix).

| iter | lever | P | machine WxH | instr | skipped | native t/frame1 | notes |
|---|---|---|---|---|---|---|---|
| 00 | baseline (c834fe0) | 1250 | 175x267 | 50,731 | 2,888,907 | 31,080,274 | CPU paints every pixel; backward-lap jumps = 74% of ticks |
| 01 | DOOM painter unit (`.unit doom`: COL/FLASH/HUD/COMMIT one word each) + DDA unroll 4 | 932 | — | 25,041 | 210,237 | — (model 5.6M) | paint loops, 8px FLASH and the 512px HUD unroll leave the CPU; unit paints concurrently |
| 02 | DDA unroll 8 + incremental PW/WADDR lookup (16→6 instr) + per-frame hoists + boot unroll 4 | 1286 | 173x667 | 25,243 | 152,639 | 6,987,271 | passed=True; model was optimistic — the new mix is ~228t/instr (store-bound) |
| 03 | INPUT_NORTH + mem_pad 39→18 (lane-band walk cut) | 1286 | 173x735 | 25,243 | 152,639 | 6,297,136 | −690k. The west-wall input pipe was the binding that forced the memory band 39 cols east; from the north wall it rivals nothing |
| 04 | per-arm DDA hit tails (SIDE scalar deleted, shade folded into whx/why) + boot unroll 8 + store_dy 10 | 1348 | 173x756 | 22,919 | 149,545 | 5,778,747 | −518k; TARGET MET (5.38x) |
| 05 | store→CPU **teleport** response (L+U rooms; pipe 59→6 cells; R has no distance term) | 1348 | 173x756 | 22,919 | 149,545 | 5,243,226 | −535k; ~14k reads/frame stop paying ~53 cells of transit each |
| 06 | LANE_ORDER frequency-tuned (cold lanes top, mem lanes pulled toward the fetch row, hot immediates + jumps in the constant-cost deep rows) + tape_n 137→136 | 1348 | 173x763 | 22,919 | 149,545 | **5,213,912** | −29k (drop-column suffix effects ate most of the estimate); final checked-in machine |

Final breakdown (frame 1): 22,919 instructions (~4.0M), 149,545 skipped words
×8 = 1.20M jump ticks, 42,849 store words (~14.3k accesses) on the ~45-tick
teleported grid store. Emulator default TickModel: 2,839,947.
Frames/tick: baseline 1/31,080,274 = 3.22e-8; final 1/5,213,912 = 1.92e-7.

## Levers assessed and not taken (with the arithmetic)

* **Full lane-band pack + slab-pitch shrink + box shrink** (est. −1.0..1.6M):
  requires the memory band to sit east of the CPU midline for binding, and the
  midline is set by the 3-slab structures band (`_SLAB_PITCH`×3 = 39 cols).
  INPUT_NORTH + pad 18 captured the cheap ~45% of it; the rest needs slab-band
  surgery with knife-edge `r` bindings on three sides. Skipped for risk after
  the target was passed.
* **Bottom-fill middle lanes into the trie's 11 spare slots** (est. −400k):
  implemented and reverted — it drags `mem_out_row`/`resp_row` down beside the
  slab band, and the slabs' discard `r` (base 28, third slab) must stay nearest
  the ROM pipe; every feasible pad re-inflates the walk it was meant to cut.
  The top-fill LANE_ORDER entry keeps the feasible fraction (iter 06).
* **Dead-lane removal / uneven trie**: 11 of 32 leaf slots are spare; deleting
  their rows needs a non-uniform trie (per-branch step = leaf count below) and
  moves the fetch row off `1<<k`, which everything in `build_cpu` is keyed to.
  Est. ~−500k (2 ticks per removed row per instruction); high-risk rewrite, skipped.
* **Top return bus**: interior row 0 is the CPU's own wall — there is no free
  row above lane 1, so a top bus means re-basing the whole band (+1 to every
  row, fetch row, trie). With hot lanes already placed below the fetch row
  (iter 06), upper lanes are the cold ones; residual value small. Skipped.
* **RAM-fetched program (PC over the grid store)**: jumps now cost 1.20M/frame
  (23%); RAM fetch adds 2 store round trips per instruction ≈ +90t × 22,919 ≈
  +2.1M to save at most 1.2M. Clearly loses; a hybrid only pays via code banks
  (ARCH §5.5), which the remaining jump share no longer justifies. Not built.

## Visuals (post-optimization feature work; native, round-gated)

| iter | lever | P | machine WxH | native ticks | notes |
|---|---|---|---|---|---|
| V1 | title screen boot: 64x48 DOOM-homage art as 968 RLE runs over INPUT, one pre-encoded RUN command word each (unit trie 2→3 bits, new RUN arm: count bare DATA sends at the panel cursor); CPU forwards IN/SND 8 per lap, one COMMIT = round 0's frame | 1379 | 231x770 | round 0 (boot+title) 1,610,005; rounds 0..1 6,647,712 → gameplay frame ≈ 5,037,707 | title ≈ 1.4M one-time; per-frame ≈ +0.5% from the +31-word P tax (5,213,912 was boot+frame combined). Codes now COL 0, HUD 1, FLASH 3, RUN 5, COMMIT 7; three leaves spare |
| V2 | 64x64 E1M1 (256 quarter-column words, preamble 295, TAPE 328, MULI 4 / S4X lookup, WALL_H=2, MOVE_NUM=2, WALK 50 chords) on the inherited men-v3 store; re-searched MEM_PAD 18→17 (smallest that binds under INPUT_NORTH+teleport+MEM_PLACE — a build()-defaults probe said 13, invalid), re-swept ROM_ROWS 24→40 (native round 0: 12→2.749M, 24→2.692M, 40→2.666M, 56/72 flat; height-bound machine, deeper fold narrows the box), re-swept DDA_UNROLL 8→16 (frame 1: 8→8,519,342, 12→8,380,261, 16→8,160,375) | 2103 | — | frame 1 = 8,160,375 (pre-texture) | ABOVE the 8M flag line: the rays legitimately doubled (~2x DDA steps); the optimizer's baseline at rom24/u8 was 10,548,825, the sweeps clawed back 2.4M |
| V4 | gun + live HUD: unit arms GUN/GUNF (original chunky outlined pistol, 10/14 one-descent-per-row runs, colours derived off B=8) replace the flash diamond; new CURS arm (bare cursor move) + the RUN arm let the CPU repaint the HUD per frame — 1 CURS + 14 pre-encoded RUN constants for the background (bezel, base, static blue armor block) + CURS/RUN pairs for the LIVE bars (red health 1px/4, yellow ammo 1px/2; AMMO scalar starts 50, -1 per shot, floor 0; HEALTH static 100). Arm roster: COL 0, CURS 1, RUN 4, GUN 5, GUNF 6, COMMIT 7 (RUN moved to an eastern leaf: its literal-free /16 pops the arg back from ring 1, and an `r` only beats the cmd pipe from the far east); RUN/COL unpacks went literal-free (16=8+8, 64=8*8) to dodge the engine's row-axis backtick pairing across the sprite columns' digit rows | 2268 | 770x1191 | round 0 = 2,724,185; frame 1 = 8,469,433; frame 2 = 9,122,275 | V4 costs ~+177k/frame (+2.1%): the gun word + 15 HUD sends + 4 bar sends + the ammo decode. TAPE 330 (AMMO/HEALTH at 327/328) |
| V3 | textures: CPU-side distance shading (NEAR_D=16 cells) + world-parity panel stripes folded into COLOR (~10 instr/column, stripe from the incremental lookup state: PW%17/16 and (WADDR-1)/4%2); unit-side period-4 seam banding — COL's wall lap pops a mask from a SECOND value ring ([7,15,15,15], FIFO rotation = the phase, reseeded per command so seams anchor at the wall top) and ANDs it into DATA | 2143 | 770x1191 | round 0 = 2,611,216; frame 1 = 8,292,651; frame 2 = 8,938,038 | texture cost ≈ +132k/frame (+1.6%); checked-in machine. Teleport audit (user request): all three store→CPU stubs are at the 2-cell pipe minimum, total route 6 cells; teleport L spans 691 columns (essential), U spans 10 rows replacing ~7 pipe cells on every read (~140k ticks/frame at ~20k reads) — neither is vestigial, both stay |

## Iteration artifacts
`iter-00-baseline.man` (the c834fe0 machine), `iter-04-side-boot8-storedy.*`,
`iter-06-final.*` (= the checked-in `littleman/examples/deadman-3d.*`).

## SQ — the square machine (bbox/viewer optimization; ticks second)

Objective change (user): the visualization tools hold and render the machine's
full bounding rectangle, so minimize max(w,h) hard — squareness dominant, tick
regressions acceptable well past the old ~5% line. Method as always: native
FastLittleman, round-gated WALK[:k], ticks = `.step` at output-settled,
passed=True required. Measured baseline (main @3513ac8, the checked-in machine
regenerated): **756x1197**, rounds 0..1 = **11,305,517** (matches the ops=8
sweep note in machine.py; the 8.3M figures in the V3/V4 rows above were taken
on an earlier machine state and are not comparable to this gate).

### Levers built
1. **`v3_store_grid_block(cols, rows, ops)`** (`memory_men_v3.py` +
   `memory_men_grid.build_grid(io=False)`): the multi-column men-v3 store as a
   placeable block. The standalone grid's answer leaves at the *bottom*; the
   block form adds an answer **riser** — a `teleport_v` room down the east side
   — so the outlet stays at the block's top where the machine's STORE teleport
   (L/U) expects every man-memory outlet. out_cell normalized onto the riser's
   real topmost pipe cell (the 709c62e off-by-one), exact pipe inventory
   `cols*(2*rows+2)+1`. Verified standalone: write+read all 330 slots through
   the stubs on the native engine (test_memory_men_v3.py), plus the machine
   gate below. One-column 330-cell block: 681x999 — it set BOTH dimensions of
   the old bbox. 8x42 block: 232x150.
2. **`STORE_OPS = 1`** (user directive): the single looping router block, v2's
   footprint. Measured delta vs ops=8 on the grid store, same geometry:
   **0 ticks** (11,368,202 both) — exactly the predicted "walk home is off the
   critical path" result: the CPU issues reads ~1k ticks apart, the router
   walks home while idling. The unrolled strip pays only for back-to-back
   streams this machine never generates.
3. **DOOM-block panel raise** (`d3_unit.PANEL_AT`): the panel's only geometric
   floor is ADDR's east-wall row (its descent must land on the top wall);
   R_SWAP+4 sat it 37 rows lower for nothing the routes needed. DATA_ROW's
   addr==data equality is py-independent (both grow 1 cell/row); the build's
   own length/planarity assertions re-checked it. Saves ~36 rows of machine
   height on a block that hangs below everything.

### Shape x fold sweep (store_dy=10, pre-panel-raise; build-only, native-gated at the knee)
| store | block WxH | rom rows | machine WxH | max | notes |
|---|---|---|---|---|---|
| 1x330 (old) | 681x999 | 40 | 756x1197 | 1197 | store sets both dims |
| 6x55 | 176x189 | 40 | 321x~390 | ~390 | store too tall |
| 7x48 | 204x168 | 40 | 321x366 | 366 | |
| 8x42 | 232x150 | 40 | 321x348 | 348 | ROM-bound width |
| 9x37 | 260x135 | 39 | 335x332 | 335 | store-bound width (75+260) |
| 10x33 | 288x123 | 40..80 | 363x321..361 | 363 | store-bound, too wide |
| 16x21 | 448x92 | 40 | ~523x~310 | ~523 | |

After the panel raise (−36 rows) the crossing moves to 8 columns:
| config | machine WxH | max | rounds 0..1 |
|---|---|---|---|
| 9x37 rom39 dy10 | 335x296 | 335 | 11,282,383 (−23k vs baseline) |
| 8x42 rom42 dy10 | 307x314 | 314 | 11,370,832 |
| 8x42 rom42 dy4  | 307x308 | 308 | 11,488,738 |
| **8x42 rom42 dy3 (chosen)** | **307x307** | **307** | **11,508,389** |
| 8x42 rom42 dy0 | 307x304 | 307 | 11,567,342 |

Width floor at 8 cols = CPU..store chain 75 + block 232 = 307 (rom 42 rows =
305 wide fits under it; 41 rows = 312, rom-bound). Each store_dy row shortens
the serial adapter->store request one cell (−19,651 ticks/frame, ~19.7k reads)
and costs one height row; dy=3 is where height meets the width floor exactly.
MEM_PAD re-searched under the final geometry: still 17.

### Final: 307x307 (exact square)
* Before: 756x1197 — max 1197, bbox 904,932. After: **307x307** — max **307**
  (3.9x), bbox **94,249** (9.6x smaller), aspect 1.00.
* Ticks (native, round-gated): round 0 = 2,742,129; rounds 0..1 =
  **11,508,389** (+202,872, **+1.79%** vs 11,305,517); rounds 0..2 =
  20,952,432 (frame 2 ≈ 9.44M). Delta decomposition: store shape +63k,
  ops=1 +0, squareness placement (dy 10->3) +140k.
* Registry: STORE_SHAPE (8,42), STORE_OPS 1, ROM_ROWS 42, MEM_PLACE
  ((0,0),(0,3)), MEM_PAD 17. Full ladder green: golden pixel-equality
  (title+walk+fuzz), tests/test_deadman3d.py -m "" 32/32, lm1+v3 fast tier
  711 passed; artifacts + _v2 copies regenerated byte-identical to build_for.

## TAPED — the few-men store variant (visualizer census; men first, ticks accepted)

User mission: the visualizer chokes on the man-memory's hundreds of little men,
so deadman-3d gets a TAPE-based store tier — while men-v3 stays available and
canonical. Both builds are one-liners off the same registry:
* canonical (ships, all deadman-3d.* / _v2.* artifacts): `build_for("deadman-3d")`
  — STORE_TIER "men-v3", the 307x307 square above.
* variant (own artifact family deadman-3d_taped.{man,debug.html,debug.json}):
  `build_for("deadman-3d", store="taped")` — no _taped.input.txt on purpose:
  same program, same protocol, the canonical input drives both.

### The tier (`memory_taped.py`, STORE tier "taped")
Banked rotating-pipe tapes behind a **gate chain**, drop-in V3Store contract so
`lm1.machine` places it through the men-v3 branch (adapter, teleports, pad —
all unchanged). Each gate is ONE man, four arms: `U` takes the op, `b` parks it
in the backpack, `r M \`M+1\` W - X` splits mine/downstream on the address, and
`d`/`a` split read/write on BP — the downstream arms REBASE (send addr-M), so
banks decode plain local addresses and the last bank needs no gate. A bank is
`tape_block` verbatim (2 men: worker + relay); answers rise into one collector
teleport and leave the block's top exactly where men-v3's outlet is.

### Bank-plan sweep (native gate, rounds 0..1; men = static @, no births)
| plan (sizes in address order) | sb | machine | men | ticks | vs men-v3 11,508,389 |
|---|---|---|---|---|---|
| 4 uniform (83,83,83,80) | 1 | 307x233 | 20 | 26,928,276 | 2.34x — over the 2x flag |
| 5 uniform | 1 | 307x233 | 23 | 24,777,656 | 2.15x |
| 4 uniform | 2 | 307x233 | 20 | 23,688,895 | 2.06x |
| (86,86,84,73) | 2 | 307x233 | 20 | 22,924,637 | 1.99x |
| (86,86,84,40,33) | 1 | 307x233 | 23 | 19,262,332 | 1.67x |
| (160,96,40,33) | 2 | 307x233 | 20 | 18,618,287 | 1.62x |
| **(128,128,40,33) (shipped)** | **2** | **307x233** | **20** | **18,620,300** | **1.62x** |

The lesson: a tape access costs the whole ring's lap (~5-8 t/slot), and the HOT
addresses are the high ones (POWB 257..272, HDG 273..288, POSX + per-frame
scalars to PTR=329) — giving them two small rings (40, 33) beat every uniform
plan by ~5M ticks/frame. Splitting further (32/41, 56/17) went the wrong way;
skip_batch 4 was wider AND slower; 5-6 banks at sb2 exceed the 307 width.

### Final numbers (taped variant vs canonical men-v3)
* **Census (engine-measured, python backend past ignition): 691 live men -> 20**
  (34.5x fewer; taped has no Y-births, static @ = census). Store men: 8 tape
  (4 workers + 4 relays) + 3 gates + 1 collector = 12 of the 20.
* Dims: 307x233 (max 307, same width class as the square; bbox 71,531 — smaller
  than the canonical 94,249). A 298x235 build exists at ROM_ROWS 44, not taken:
  the fold registry is shared with the canonical machine and max stays 307-class.
* Ticks (native, round-gated): rounds 0..1 = 18,620,300 (+62% vs men-v3's
  11,508,389 — the accepted ring tax); rounds 0..2 = 35,136,458 (frame 2
  ~16.5M vs 9.44M). Under the ~2x flag line (23.0M).
* Verification: memory_taped store probe (all 329 addresses through the chain,
  per-bank read passes — streamed cross-bank reads legally race; the machine's
  CPU serializes), deadman suite green under BOTH tiers (40 tests), fast tier
  940 green, canonical artifacts byte-identical, taped artifacts pinned by
  test_checked_in_taped_man_matches_the_machine_builder.

## M7b — the VRUN unit arm: measured, NOT taken

The design's optional M7b item 3 was a one-word-per-pixel `VRUN` arm on the
trie's spare leaf 5, replacing the paint chain's `CURS`+`RUN` pair. Costed
before building, on the shipped M7b walk:

| quantity | measured |
|---|---|
| CPU instructions per frame (emulator, title + 26 frames / 27) | 19,374 |
| sprite pixels actually painted, worst frame of the walk | 76 |
| sprite pixels actually painted, mean over the walk | 7.8 |
| instructions VRUN saves per opaque pixel (16 -> 12) | 4 |

Ceiling: 76 x 4 = 304 instructions on the *worst* frame = **1.6%**, and
7.8 x 4 = 31 instructions on the average one = **0.16%** — against the ~5%
bar, and against the cost of a new trie leaf (every command word's decode) plus
keeping `d3_unit.py` and its `store.py` twin in lockstep. The design's "~30%
off the sprite paint" was right about the *paint*; the paint is simply not a
big enough share of a frame at 3 billboards x <= 14 rows. **No-go.**

The measurement to repeat if the sprite budget ever grows (more slots, taller
bands, per-column texture stepping): count painted pixels per frame from the
golden model, multiply by 4, divide by the emulator's instructions/frame.

## M7c — the bounding-box re-sweep (user: "put the input left, reclaim the
## empty CPU column and bottom row")

Baseline, main @134f72f: canonical `build_for("deadman-3d")` **379x376**
(max 379), taped **395x231** (max 395). Method as always: build-only sweeps to
find the frontier, then native round-gated FastLittleman at the knee.

### What actually binds each dimension (measure first)
| machine | width is | height is |
|---|---|---|
| canonical (men-v3) | the **ROM** — rows 0..63 span 0..378; the store chain (75 + the 288-wide block) floors at 363, 16 columns clear | `rom_rows + 3*store_rows + 112` — the ROM fold, the 204-row store block, and the ~100-row DOOM stack under it |
| taped | the **store** — 75 + the 320-wide 6-bank block = 395; the ROM is 379 | the same sum with a 59-row block: 231, i.e. **164 rows of slack** |

Two closed forms fall out and drive everything below: `rom_w ≈ 22,740 / rom_rows`
(so one more fold row buys ~6 columns and costs exactly 1), and the taped
block is `48*banks + 32` columns wide **independent of the bank sizes** — the
bank COUNT is a layout number, the sizes are pure tick tuning.

### The three requested sub-changes, measured
1. **Input on the WEST wall.** It is *feasible* and **dimension-neutral**, not
   the pad disaster the M7-era note recorded. `in_north=False` fails to build
   at the shipped `ROM_CPU_GAP`, but the collision is not the input pipe — it
   is the STORE **teleport L** room, which lives at `rom_bottom+1..+4` and
   needs a corridor ≥6 rows deep. `INPUT_NORTH` is what forces that depth
   today, so the I room is riding for free. Forcing the gap to 6 and building
   west: **379x376 at mem_pad 15..28** — byte-for-byte the same box as north,
   and the historical "pad 39" rationale is dead (the teleport, not the wall,
   is what unbinds the memory `r` now). Dropping the teleport too gets the
   corridor to 1 row and the box to 379x371 (-5 rows, best case 373 after a
   re-fold), for iter 05's ~9% of frame ticks. **No-go: 4 pixels for 9%.**
2. **The empty rightmost CPU column.** Real — interior column 53 of the CPU
   box (widest lane BRN ends at 52), and `build_cpu`'s `width = ret_x + 1`
   makes it structural. Worth **0**: proxy-measured by walking the store west
   with `mem_pad` instead — pad 15..31 (store x 73..89) all build the
   *identical* 379x376, because the ROM is 16 columns wider than the store
   chain. On the taped tier the store *does* bind, but `store_offset` dx buys
   20 columns there against this one. **No-go.**
3. **The empty CPU bottom row.** Real, and already documented at
   `machine.py`'s `height = bottom` ("free on ten of the eleven machines and
   changes no footprint; load-bearing on matmul"). Worth **0** here for a
   different reason: the CPU box ends at row 123 and the machine's height is
   set 150 rows lower, by the store block plus the DOOM stack. **No-go.**

### What did win
| lever | canonical | taped |
|---|---|---|
| `ROM_ROWS` 60 -> **61** | 379x376 -> **373x377** (max 379 -> 377) | — |
| `TAPED_BANKS` 6 -> **4**, cold pairs merged `(256, 195, 64, 85)` | — | store 320 -> 224 cols |
| `TIER_LAYOUT[("deadman-3d","taped")]` = `rom_rows 83`, `store_offset (-20,0)` | — | 395x231 -> **279x258** (max 395 -> 279) |

`ROM_ROWS` sweep (10x60 store, build-only): 56 406x372 / 58 393x374 / 60
379x376 / **61 373x377** / 62 369x378 / 63 363x379, then width floors at 363
and only height grows. 61 is the crossing. Every other axis is dominated: 11
store columns floor the width at 391, 9 columns floor the height at ~397, and
600 slots at 10 columns force exactly 60 rows, so `store_h` is not free.

Taped joint frontier (bank plan x store dx x rom_rows, min max(w,h)):
6 banks 395 · 5 banks (128,128,195,64,85) 347 · **4 banks (256,195,64,85) 299
at dx 0, 283 at dx -16, 279 at dx -20** · 3 banks (256,195,149) 263 but +29%
ticks. dx -21 stops routing. The men-v3 store cannot take a negative dx at all
("cannot compact STORE request route"), which is why the offset lives in
`TIER_LAYOUT` and not in `MEM_PLACE`.

Merging the two COLD bank pairs is faster as well as narrower — 9-round native
gate at rom 78: 4 banks **90,157,275** vs 6 banks 93,649,383 (-3.7%). One
fewer gate on every access beats the merged cold rings' laps. Merging the HOT
pair (ZBUF 64 + the per-frame scalars 85) into 149 costs +29% and re-proves the
original traffic-shaping lesson.

### Final numbers (native, round-gated, `passed=True` throughout)
| gate | before | after | delta |
|---|---|---|---|
| canonical dims | 379x376 (max 379, bbox 142,504) | **373x377** (max **377**, bbox 140,621) | -2 / -1.3% |
| taped dims | 395x231 (max 395, bbox 91,245) | **279x258** (max **279**, bbox 71,982) | **-116 / -21%** |
| canonical, full 57-command walk (58 rounds) | 330,339,051 | **327,860,446** | **-0.75%** |
| taped, full walk | 659,297,504 | **654,884,941** | **-0.67%** |
| canonical, 115-frame tour (116 rounds) | 645,715,913 | **640,802,749** | **-0.76%** |
| taped, tour | 1,271,045,970 | **1,253,152,404** | **-1.41%** |

Both tiers got smaller *and* faster; nothing was traded. Taped census still 20
men. `deadman-3d.input.txt`, `.cases.json` and `_tour.input.txt` are unchanged
(the program never moved) — `plan_tour.py <out> 2 5` reproduces the checked-in
tour byte-identically. Test ceilings tightened 400 -> 390 (canonical/trim) and
400 -> 300 (taped).

## M9 — the two knobs the earlier passes left on the table

Both sweeps are on the taped tier only, native/fast engine, `passed=True`
throughout, and are opt-in per `(slug, tier)` — the canonical machine's
`deadman-3d.man`, `deadman-3d_trim.man` and `deadman-3d.input.txt` are
byte-identical across both.

Baseline for this pass (merge-staging `cf0effc`, taped, 115-frame tour,
116 rounds): **1,018,297,264 ticks at 289x269**. (Reproduce with
`scratch/deadman3d-opt/tour6.py`, which recovers the command list from
`littleman/examples/deadman-3d_tour.input.txt` instead of needing a chords
file. The 1,022,496,076 quoted in the hand-off is 0.4% off this and does not
reproduce; the checked-in `deadman-3d_taped.man` and a fresh `build_for` both
give 1,018,297,264 exactly, so the discrepancy is in the older harness, not
the machine.)

### The bank split, re-swept against per-ADDRESS traffic

The earlier passes profiled per BANK, which cannot see a seam in the wrong
place. `scratch/deadman3d-opt/traffic.py` counts on the emulator's abstract
wire per address (four-command run differenced against the boot round;
11,222 reads and 3,416 writes a gameplay frame):

| addresses | what | reads | writes |
|---|---|---|---|
| 517..531 `XCOL..COLOR` | the DDA inner loop | 56.2% | 56.2% |
| 532..533 `PW`, `WADDR` | the texture inner loop | 25.6% | 31.2% |
| 1..352 `MAPB`, `POSX..PLANEY` | the map, walked in address order | 8.4% | 0.0% |
| 353..516 `MONB`/`SPRB`/`ZBUF`/`CMD` | boot-mostly + the ZBUF | 3.5% | 2.0% |
| 534..600 `FRACX..PTR` | the rest of the scalars | 6.3% | 10.6% |

`(256, 195, 64, 85)`'s seam at 515/516 put all of that in one 85-slot ring.

8-command native gate, against `(256, 195, 64, 85)` + order `(3, 0, 1, 2)` =
75,782,738:

| plan | order | ticks | |
|---|---|---|---|
| **(352, 164, 15, 69)** | **(3, 2, 0, 1)** | **61,799,020** | **-18.5%** |
| (352, 164, 16, 68) | (3, 2, 0, 1) | 62,405,534 | `WADDR` into the small ring |
| (352, 164, 14, 70) | (3, 2, 0, 1) | 62,132,237 | |
| (352, 165, 14, 69) | (3, 2, 0, 1) | 63,382,964 | `XCOL` out of it |
| (352, 164, 17, 67) | (3, 2, 0, 1) | 63,237,686 | |
| (256, 260, 17, 67) | (3, 2, 0, 1) | 64,365,449 | the old bank-0 seam |
| (160, 356, 17, 67) | (3, 2, 0, 1) | 66,243,101 | |
| (352, 164, 15, 69) | (3, 2, 1, 0) | 61,979,795 | +0.29% |
| (352, 164, 15, 69) | (3, 0, 2, 1) | 63,602,816 | +2.9% |
| (352, 164, 15, 69) | address order | 67,253,690 | +8.8% |

Bank 0 wants to be BIG, which the `~8 ticks per slot per access` ring-tax model
gets exactly backwards (the model's own optimum, `(126, 390, 17, 67)`, does not
even build). The map is walked in address order, so its ring is already turned
to the next word and the tax is not paid. `b1` sweep at `b2=516, b3=531`:
300 62,614,448 · 310 62,461,570 · 330 62,146,070 · 340 61,988,320 ·
344 61,925,220 · 348 61,862,120 · **352 61,799,020** · 354 62,333,591 ·
356 63,269,295 · 358 65,506,462 · 360 66,641,970 · 370+ do not build.

Bank COUNT is fixed at four by geometry: five is `48*5+32 = 272` columns from
the store's west wall at x=61, an east edge of 333 against the 300-column
ceiling; three needs bank 0 to swallow everything below 517 (516 slots, block
66 rows) and does not route at any fold. Blocks deeper than 60 rows fail
`build`'s pipe binding at the store's own southwest corner (`collision at
(61..62, 149..179)`) at **every** fold, which is what caps bank 0 near 356.

Tour: **1,018,297,264 -> 838,732,969, -17.63%**, 289x269 both ways.

### The fold, re-swept onto the freed width

`SEEK_TIER_LAYOUT`'s taped `rom_rows` 80 was chosen when the width floored at
295. That floor was the ANSWER PATH's, not the store's — the STORE teleport L
room at `rom_bottom+1..+4` reached out to 293. `STORE_ANSWER_WEST` deleted the
room and `SEEK_SLAB_PITCH` narrowed the slabs; the floor is now **287** =
`TX 61 + 224 store columns + the east return pipe`, and 80 was one row short of
reaching it. Curve (build-only box; ticks on the 8-command gate under the new
bank plan):

| `rom_rows` | box | ticks |
|---|---|---|
| 76 | 304x265 | 61,698,016 |
| 78 | 299x266 | 61,613,459 |
| 79 | 292x268 | 61,714,266 |
| 80 | 289x269 | 61,799,020 (was shipped) |
| **81** | **287x271** | **61,826,043** |
| 82, 83 | 287x272 | build, do not run |
| 84 | 287x274 | 61,689,668 |
| 85..87 | 287x275/6 | build, do not run |
| 88 | 287x278 | 61,666,460 |
| 92 | 287x282 | 61,598,564 |
| 96 | 287x286 | 61,522,369 |
| 100 | 287x291 | width floored, height now over |

81 is the crossing. The fold is a size knob and nothing else — the whole 76..96
span is 0.5% of ticks. Tour at 81: 839,384,674 (287x271) against 838,732,969 at
80 (289x269), +0.08%.

Folds 82, 83, 85, 86 and 87 BUILD but do not RUN: at those depths a ROM literal
read in reverse exceeds 63 bits, and both readings of a backtick pair have to be
values ("every value in the language is a signed 64-bit integer"). That is why
84 — 0.2% faster than 81 and equally narrow — is not the pin.

### Combined

| | before | after |
|---|---|---|
| taped box | 289x269 (max 289) | **287x271** (max **287**) |
| taped, 115-frame tour | 1,018,297,264 | **839,384,674** (**-17.57%**) |
| taped census | 18 static men | 18 static men |
| width headroom vs the 300 ceiling | 11 | 13 |

## M10 — the ROM block: what its 4,304 words are made of, and one knob

Taped tier only, opt-in per `(slug, tier)`; `deadman-3d.man`, `deadman-3d_trim.man`
(both `f62d63fd`) and `deadman-3d.input.txt` (`654d35d6`) are byte-identical.
Baseline `merge-staging` `6a21275`: taped **287x271**, 8-command native gate
**61,826,043**, 115-frame tour **839,384,674**.

The full profile, the two other levers costed and rejected, and the packer
decomposition live in `littleman/ROM-RECIRCULATION.md` §"The drum's *contents*".
The short version:

* The drum was **4.626 cells a word**, not the 3.36 the old cost model quoted
  (that was `sudoku-validity`'s). Opcodes were **44.8%** of it: 22 opcodes need
  `k = 5`, and 1,542 of 2,152 opcode words were two-digit at 5 cells against a
  one-digit word's 2.
* Under `TRIM_DEAD_LANES` a lane's row is its slot's **rank**, so relabelling the
  slots is row-neutral. Ten of the 32 slots bit-reverse below ten;
  :data:`machine.OPCODE_SLOTS` spends the ten spare slots on the hot opcodes
  (DP over slot x rank, `scratch/rom-opt/slots.py`).

| quantity | before | after |
|---|---|---|
| one-digit opcode words | 610 / 2,152 | **1,401 / 2,152** |
| drum cells / word | 4.626 | **4.075** |
| ROM block | 284x94 (34.3% of the grid) | **252x93** |
| ROM lap | 23,048 cells | **20,060** |
| trie walk (execution-weighted) | 64,444 | **54,722** |
| taped box | 287x271 | **287x270** |
| 8-command gate | 61,826,043 | **61,570,950** (-0.41%) |
| 115-frame tour | 839,384,674 | **838,737,298** (**-0.077%**) |

**The number worth keeping is the last one.** A 13% shorter lap bought 0.077% of
the tour, and the control (one blank cell added per token: +18.2% of lap for
+1.09% on the 8-command gate) prices the whole drum at **~0.6% of tour ticks**.
`max(6 ticks CPU loop, ROM ticks/word)` is CPU-bound at 4.6 cells a word, and
DOOM's taped tier is store-bound besides — so ROM density is an **area** knob for
this machine and essentially nothing else.

And the area is not binding: the taped width floors at `TX 61 + 224 store columns
+ the east return pipe` = 287, so the drum's 284 was one column under the floor
and its 252 is 35 under. **The ROM's own width floor fell 286 -> 254.** That
reserve, not the 0.077%, is what this change is for: the next column of taped
width has to come from the store, and when it does the drum will not be what
stops it.

Rejected here, with the arithmetic (see ROM-RECIRCULATION.md):
* **Store-address renumbering** (961 static addresses, all 353..599, all three
  digits = 29% of the drum): worth ~3,400 cells, unsound — `LDA`/`MOVA` compute
  addresses at run time, and the `.asm` is shared with the canonical tier.
* **A smarter packer**: the 12% blank is the vertical-backtick parity rule
  (10 rows, 2,350 cells); the even-words-per-row rule costs **zero** and row ends
  321 cells. A lookahead packer returns the identical row count in 15 of 15
  width x depth combinations, so greedy leftmost is already optimal here.
* **`SEEK_K` 128 -> 64**: 39 cells, and unavailable anyway — the widest packed row
  holds 66 words.
# M10 — the DOOM unit (`stream:unit`), profiled and then lifted

Scripts: `unit_traffic.py` (per-arm command words per frame), `unit_occupancy.py`
(what each arm actually occupies against the 20 columns it is granted),
`unit_loop_gate.py` (a candidate `loop_row` judged pixel-for-pixel on the native
engine via `d3_unit.build_probe`'s standalone grid).

## The traffic profile: three narrow arms carry 99%

24 gameplay frames of `WALK`, bucketed by COMMIT, boot round separated.

| arm | words/frame | share | pixels/frame | frames used | columns it occupies |
|---|---|---|---|---|---|
| RUN | 85.54 | 46.6% | 641.6 | 24/24 | 4 |
| COL | 64.00 | 34.9% | 1,719.2 | 24/24 | 11 |
| CURS | 31.92 | 17.4% | 0 | 24/24 | 1 |
| COMMIT | 1.00 | 0.55% | 0 | 24/24 | 1 |
| GUN | 0.88 | 0.48% | 57.8 | 21/24 | 23 |
| GUNF | 0.12 | 0.07% | 11.0 | 3/24 | 33 |

The boot round (preamble + title screen) is 429 RUN + 1 COMMIT and touches
nothing else. COL is a flat 64 words — one per viewport column, every frame.
RUN and CURS track the HUD's live bars (16 CURS on a quiet frame, 79 on a busy
one). GUN and GUNF are mutually exclusive and exactly one fires per frame.

**The arms are inverted with respect to their traffic.** The three that carry
98.9% of the words occupy 16 columns between them; the two sprite arms carry
0.55% and occupy 56. That is not a defect — a baked sprite is one command word
instead of ~130 CPU sends, which is the whole reason the arms exist — but it
does mean the block's *width* is bought almost entirely by its coldest arms.

## Width: the block does not bind, so there is nothing to win

Real column span, summed over the arms: **73 of the 156-wide interior (47%)**.
COMMIT uses 1 of its 20, CURS 1 of 20, RUN 4 of 20; the sprite chains use 23 and
33 of the 40 each gets by riding over a spare leaf. A variable-pitch trie (the
codes are the west/east branch path, not the distances, so leaves may sit at
arbitrary columns) would pack the interior to roughly 80 and the block from
235 to about 160.

**It would be worth exactly zero.** The taped machine is 287 wide and the DOOM
block's east edge is column 235 — it is 50 columns clear of the box. The width
floor is the taped store's (`TX 61 + the block's 224 columns + the east return
pipe`, see `SEEK_TIER_LAYOUT`), and no amount of unit narrowing touches it. The
block would have to *grow* by 52 columns before its width mattered at all.

So: merging GUN/GUNF onto a shared slab (GUN_FIRE's rows 29..38 are literally
GUN_IDLE's rows 30..39 shifted up one row, so the body could be shared behind a
±64 address offset), merging COMMIT/CURS, and re-pitching the trie are all real
and all pay nothing on this machine. Measured negative — recorded so the next
person does not build it.

## Height: the block IS the machine's floor, and eight rows of it were empty

The DOOM block hangs below everything (`rom` rows 0..93, CPU/tape 94..168, the
block 170..270), so the machine's last row is the block's. The block's height is
`R_ADDR + PANEL_H + 6`: the panel hangs two rows below ADDR's band row, and the
SWAP under-run three below the panel. The unit's own interior bottom
(`R_COLLECT`) is *sixteen rows clear* of that — it hides in the panel's shadow —
so **ADDR alone sets the height**.

Every band row is a fixed offset from the loop corridor (`d3_unit.BELOW_LOOP`),
and that is forced rather than chosen: the two counted-loop bodies are rigid
ladders hung off `R_LOOP + 1`, and each band row is where one of their `r`/`s`
glyphs lands. So the whole lower half translates as one rigid piece, every
`_send_band` decision (a comparison of row *differences*) is invariant, and all
four pipe lengths — which depend on `ADDR-DATA` and `ADDR-SWAP`, not on ADDR —
are unchanged.

Rows 19..26 of the shipped map hold no cell at all, so 19 is free immediately.
Below that the floor moved twice:

**19 was RUN's arm.** COL has the longer unpack but its corridor cell sits in the
*climb* column one east, so its leaf column may carry machinery on the corridor
row; RUN's `>` was in its own leaf column directly under `/bW`, and 18 collided
on that `W`. Giving RUN the same climb — one turn east, one column up, and its
counted loop shifts one column into the 16 spare it already had — costs three
cells and unlocks nine more rows.

**10 is rule 1, and it is a real floor.** Nothing collides below 10; what fails
is binding. COL's seed push sits at a *fixed* row 20 — it is above the corridor,
anchored to `R_ARG` — while the bands rise with the corridor beneath it, so the
two are driven together. The push must stay nearer the ring band (`loop+3`) than
ADDR (`loop+19`), whose midpoint is `loop+11`, giving `20 < loop+11`. Swept: 10
builds with margin 2, 9 is the reading-order tie `_check_unit` refuses, and 8 and
below bind the seed push to ADDR outright — the wall seed would go to the panel.

| loop_row | block | probe steps | probe |
|---|---|---|---|
| 9 | — | — | refused (margin 0 at (143,20)) |
| 10 | 235x84 | 44,054 | PASS |
| 14 | 235x88 | 44,362 | PASS |
| 19 | 235x93 | 44,735 | PASS |
| 24 | 235x98 | 45,180 | PASS |
| 27 (shipped) | 235x101 | 45,447 | PASS |

Pipe lengths (addr 15, data 15, swap 35) and binding margins (min 2) are
identical at every value in 10..27. The probe mix is every arm, a negative-seed
COL, both sprites and the banding masks, judged against `store.DoomUnit`'s own
frames on the native engine. The lift is very slightly *cheaper* as well — the
arms' descents to the corridor are shorter.

The fold was re-checked and does not move: 78 -> 299x249, 79 -> 292x251,
80 -> 289x252, **81 -> 287x254**, 84 -> 287x257, 88 -> 287x261. 81 is still the
shallowest fold that reaches the 287 width floor, now at seventeen fewer rows.

## Combined (M10)

| | before | after |
|---|---|---|
| DOOM block | 235x101 | **235x84** |
| taped box | 287x271 | **287x254** |
| taped, 116-round tour | 839,384,674 | **839,158,874** (-0.03%) |
| `deadman-3d` / `_trim` | `f62d63fd…` | `f62d63fd…` (unmoved) |
| `deadman-3d_hires` wall | 572x228 | 572x228 (unmoved) |

### What is left

The unit's own interior bottom (`R_COLLECT` = `loop+55` = 65) is now 20 rows
clear of the panel's under-run, so it still is not binding — the next row would
still come off ADDR. Getting one needs the `ADDR-RET` gap of 19 to shrink, and
that gap is `BAND_BODY`'s own 18 ops of unpack before it can send ADDR. It has
exactly two blank cells (indices 17 and 21); removing the first moves the
mask-pop `r` one row closer to `ring_ret` and one further from `ring2_ret`, which
is a 21-vs-21 tie. So the next row costs a re-tune of the band body, for one row.

Opt-in via `machine.DOOM_LOOP_ROW`; absent means the shipped row 27, which is
what holds the canonical and hi-res families byte-identical. `deadman-3d_hires`
stacks four of these blocks two deep, so opting it in is worth ~16 rows there —
untested here, and its own layout would want a re-sweep.

# M11 — the seek request pipe: 437 cells, and what they were actually costing

Taped tier only, opt-in via `machine.SEEK_TELEPORT`. Baseline `merge-staging`
`2a20a64` regenerated: taped **287x253**, 116-round tour **838,511,442** (native
FastLittleman, round-gated, `passed=True`).

`cpu->drum` was the longest route on the machine by a factor of seven — 437 cells
against 58 for the next one — carrying `row*SEEK_K + rem` from the CPU's east wall
around the whole store block and back into the drum's east wall.

## The derivative, measured before building anything

Padding the pipe's eastward leg by 100 cells (machine 337x253, everything else
identical) moved the tour from 838,511,442 to **839,317,714**: **+8,063 ticks a
pipe cell**. Since one cell is one tick of transit per seek, that number *is* the
seek count over the tour — **~8,063 seeks, ~70 a frame**.

So the whole 437-cell pipe is worth at most ~3.5M ticks, **0.42% of the tour**.
That is the ceiling, and it is set by the seek *rate*: the store's teleported
answer path was worth more (-0.52%) off a **53**-cell pipe because reads happen
~15k times a frame. A seven-times-longer pipe on a two-hundred-times-rarer event
is worth less, not more.

## The build: two rooms, 437 -> 52 cells

`machine._seek_teleport`. `R` has no distance term (SPEC.md), so:

* **seek:H** — `(62,158)-(286,168)`, a 225-column room in the free band between the
  store block's floor (row 157) and the DOOM block's roof (row 169). Swallows the
  whole eastward crossing.
* **seek:V** — `(255,1)-(286,113)`, a 113-row room in the empty column east of the
  drum and north of the store. Swallows the northward climb *and* the westward
  return into the drum.

Three stubs are left: 6 cells from the CPU's own send cell down into H, 44 up
column 285 (the only column clear of the store block's east return pipe), and 2
from V onto the drum's own attachment cell. Both endpoints are the cells the plain
pipe used, so no `r`/`s`/`q` binding moves. Cost: two men (18 -> 20 static).

| | before | after |
|---|---|---|
| `cpu->drum` | 437 cells | **52** |
| taped box | 287x253 | 287x253 (unmoved) |
| 116-round tour | 838,511,442 | **838,353,122** (**-0.019%**) |

## -0.019%, not -0.37%, and the reason is `q`

The naive arithmetic says 385 cells x 8,063 = 3.1M. The measurement says 158,320.
The missing factor is the property the shipped comment already claimed and this is
now the number for: **`q` counts values anywhere in the pipe, not just at its
destination cell.** The drum's row gadgets `q` the request pipe, so with the plain
437-cell pipe the drum notices the seek *the tick the CPU sends it* and spends the
whole transit walking its cascade — down the gadget zigzag, west along the bottom
collector, up the seek riser to the station — and then parks on `r`. That walk is
~250 (zigzag) + 252 (collector) + ~93 (riser) cells of the drum's own geometry, so
the pipe and the walk ran **concurrently** and the seek cost `max(pipe, walk)`, not
`pipe + walk`. At 437 the pipe was barely the larger of the two.

Padding *up* therefore costs the full 8,063/cell (the pipe becomes the max), while
cutting *down* recovers only the ~20 ticks a seek by which 437 exceeded the walk.
Both are consistent with one model and both were measured.

**The lever this leaves for anyone continuing:** the seek path costs ~0.4% of the
tour and it is now entirely the drum's **cascade walk**, not the request pipe. The
252-cell westward run along the bottom collector row is the biggest single piece of
it. Shortening the pipe further is worth nothing; the pipe is below the floor.
