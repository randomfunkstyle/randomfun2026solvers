# deadman-3d frame-1 optimization log

> ## Scope decision (2026-07-29): DOOM is out of contest scope
>
> `deadman-3d` and `deadman-3d_hires` are **not scored and not judged** — there is
> no problem named `deadman-3d`, which is why
> `test_public_cases_pass_on_the_real_interpreter[deadman-3d-600]` has always
> failed with `problem not found`. So the contest metric `max(w, h)**2 * ticks`
> **does not apply to this family**.
>
> **Optimise ticks. Footprint is not a goal.** A change that trades ticks for
> columns is a regression here however well it scores, and a change that costs
> columns to save ticks is free. The two rules that follow:
>
> * Do not quote `max**2 * ticks` as a result for this family.
> * Size still matters where it is a *constraint* rather than a score — the
>   taped machine's 300-column ceiling, and anything that must keep binding —
>   but never as something to minimise for its own sake.
>
> Applied immediately: the `wings` gate body (275x253, -7.13% on `max**2*ticks`)
> is **declined**, because it costs **+1.15% ticks** — 848,164,433 against the
> compact body's 838,511,442. Both bodies stay implemented and tested behind
> `TAPED_GATE_BODY`; `("deadman-3d", "taped")` keeps the compact one.


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

# M11b — the U-turn under the CPU: what it was for, and that it was for nothing

Taped tier only, opt-in via `machine.SEEK_TAKEN_DROP_EAST`. Baseline: the M11
grid, **838,353,122**.

Read off the shipped 287x253 grid, a taken `JMPS` went:

```
(58,133)  the JMPS lane's drop column          drop 13 rows
(58,146)  '<'  the slab's entry row            walk 15 WEST
(43,146)  'v'  slab_base                       drop  8 rows
(43,154)  '>'  the taken row                   walk 13 EAST
(56,154)  's'  the request goes out            -> (57,154) 'v', (57,155) '<'
```

— 15 west and then 13 east, to arrive 2 columns from where it started falling.

**What it was for.** The drop column is `slab_base[m]`, and for a *branch* slab
that is genuinely load-bearing: `base` is where `>`/`X` and the three arm rows of
the fan-out are built. A seek **jump** slab has no body at all — under the seek
drum its whole "slab" is one `v` and a column of `.` — so it inherited a column it
has no use for. Nothing else holds it either: the seek tail's only `r`/`s` glyphs
are the `s` at `e_s` and the flush/discard `r`s in columns 3..4, and none of them
move when the drop does.

**The fix.** Turn south at `struct_east + 1` (= `e_s - 1`), the last column that
still lands west of the `s`, clamped to the lane's own drop column so the entry
row's `<` keeps its cell. Being east of `struct_east` it is east of every slab
body by construction, so the drop crosses nobody; the generator asserts the column
is clear anyway. The walk becomes 3 west, 8 down, 1 east.

| | before | after |
|---|---|---|
| JMPS entry-row leg | 15 west + 13 east | **3 west + 1 east** |
| taped box | 287x253 | 287x253 (unmoved) |
| 116-round tour | 838,353,122 | **837,925,922** (**-0.051%**) |

24 ticks a taken `JMPS`, and the tour says ~17,800 of them over 116 rounds (~153 a
frame) — which is also the first direct count of taken jumps on this machine, and
it is *twice* the ~8,063 the seek-pipe derivative suggested. That is not a
contradiction: only the seeks whose transit outruns the drum's cascade walk pay
per pipe cell, so the pipe derivative counts pipe-bound seeks, not all of them.

## The *other* west leg, which is load-bearing and stays

After `s` the man turns south and walks row `t+1` west from column 57 to column 11
— 46 ticks, the longest single leg left on the seek path. It looks identical in
kind to the one just deleted. It is not:

* the flush loop's `r` and the remainder `r` must be nearest the **ROM** pipe,
  which attaches at the CPU's *west* wall (`_STRUCT_X0_SEEK`'s comment: "columns
  1..4 belong to the flush/remainder tail"), and
* even granting slack there — the ROM beats `mem_resp` for any `r` west of column
  ~39, so the tail could move ~28 columns east — the man still has to reach the
  CPU's **return riser**, which is at the west wall. Moving the tail east only
  moves the same 46-tick walk onto the collector row.

Measured negative by construction: the walk is conserved, so it is recorded rather
than built.

# M11c — `io:I` west: worth nothing by itself, worth 0.62% through `mem_pad`

Taped tier only, opt-in via `machine.INPUT_NORTH_WEST` and `machine.MEM_PAD_FOR`.
Baseline: the M11b grid, **837,925,922**.

## What pinned x=21

Nothing geometric. `build_cpu` sets `in_col = lane_x0`, so under `INPUT_NORTH` the
I room's pipe drops onto the IN lane's own `r` — a convention, not a constraint.
The band it lives in (`rom_bottom+1 .. CY-1`) is empty from column 2 to 285; the
ROM corridor descends in column 1 and nothing else is there at all. The westward
limit is the CPU's own north wall: `in_x >= CX + 1 = 9`, at which point the room's
west wall shares the CPU's west wall *column* (on different rows) and the pipe
still lands on a wall cell rather than the corner. That is 13 columns of travel.

## Moving it is worth exactly zero, measured

| | io:I x=21 | io:I x=8 |
|---|---|---|
| `input->cpu` | 2 cells | 2 cells |
| box | 287x253 | 287x253 |
| 116-round tour | 837,925,922 | **837,925,922** |

Bit-identical. The pipe is at its two-cell minimum either way, the box is
store-bound, and the room is the only thing that moves. `INPUT_NORTH` also costs a
fixed 5 rows of `cpu_gap` (3 room rows + 2 pipe rows, against `ROM_CPU_GAP = 1`)
and that is independent of the column, so no height comes back either.

## What it *does* buy: two columns of `mem_pad`

The input pipe is one of the three rivals `check_bindings` weighs every memory `r`
in the CPU against, and shrinking `mem_pad` walks the memory band **west, toward
it**. So the room's column is the pin on the pad, not on the layout.

Swept (build-only, then on the tour):

| `mem_pad` | I room at x=21 | I room at x=8 |
|---|---|---|
| 22 (`SEEK_MEM_PAD`, was shipped) | builds — 837,925,922 | builds — 837,925,922 |
| 18 | builds — **827,599,542** (-1.23%) | builds |
| 17 | `'r' at (41,103)` wants `mem_resp`, `in` is nearer | builds |
| 16 | fails on `in` | builds — **822,436,488** (-1.85%) |
| 15 | fails on `in` | fails on `rom` |

Two findings, and they are separable:

1. **`SEEK_MEM_PAD = 22` was four columns above its own floor.** Its docstring
   still claims 22 is where the classic slabs' `r` stops beating `mem_resp`; that
   was measured before `SEEK_SLAB_PITCH`, `STORE_ANSWER_WEST` and M10 moved every
   glyph it was weighed against. Re-swept, the floor with the room where it is
   today is **18**, and taking it is worth **-1.23%** on its own.
2. **The last two columns are the input room's**, and they are worth a further
   **-0.62%**. At 16 with the room moved the rival becomes the ROM pipe, which no
   amount of moving the I room affects — so 16 is a real floor, not another stale
   one.

| | before | after |
|---|---|---|
| `io:I` | x=21 | **x=8** |
| `mem_pad` (taped) | 22 | **16** |
| lane width (e.g. `cpu:lane:IN`) | 32 | **26** |
| taped box | 287x253 | 287x253 (unmoved) |
| 116-round tour | 837,925,922 | **822,436,488** (**-1.85%**) |

Keyed by `(slug, tier)` and gated on `seek`: the canonical machine keeps
`SEEK_MEM_PAD`'s 22 and its `f62d63fd` grid, and `build_for(..., seek=False)` keeps
`MEM_PAD`'s 17 (`test_lm1_slab_entry.py` pins that).

# M11 — the three levers together

| | 116-round tour | Δ |
|---|---|---|
| baseline `merge-staging` `2a20a64` | 838,511,442 | — |
| M11 seek teleport (437 -> 52 cells) | 838,353,122 | -0.019% |
| M11b JMPS U-turn deleted | 837,925,922 | -0.051% |
| M11c `io:I` west + `mem_pad` 22 -> 16 | **822,436,488** | **-1.85%** |
| **total** | | **-1.92%** |

Box unmoved at 287x253 throughout — every column of it is the store's.

**The ranking is the lesson.** The task ordered these by *pipe length* and the
longest pipe on the machine came last by two orders of magnitude. Traversal length
is a latency budget, not a tick count: a pipe shifts in O(1) a tick whatever its
length, so what a route costs is `length x frequency`, and the seek request is used
~150 times a frame against the store's ~15k reads. The cheap win here was not on
any pipe at all — it was six columns of lane walk that every memory instruction
pays twice.
## M12 — `adapter->store`: the leg every access pays, crossed as a room

The profile on `analysis-doom-profile` (`scratch/DOOM-PROFILE.md`) said the CPU
is **blocked on the store answer for 47.19% of the run** — 87,490 reads at 332
ticks each on a gated nine-round run — and that **20.22% of the run is pure pipe
transit** inside that wait. The biggest single term in it was `adapter->store`:
60 parsed cells that *every* access walks, reads and writes, all four banks.
Estimated **8.53%**.

Same lever as `STORE_ANSWER_WEST`, on the leg that was left. `R` has no distance
term (`SPEC.md` §Nearest — "nearest" only picks *which* pipe), so a `teleport_v`
hung in the corridor between the adapter's floor and the gate strip's roof
crosses the whole gap in one instruction:

```
       +------------+      the adapter, (63,112)-(76,117)
       |UX.........v|
       +------------+
          v                2 cells down off the SOUTH wall
       +----+
       |@>Rv|              teleport:REQ, (61,120)-(66,148)
       | ^s<|              27 interior rows, crossed for free
       |    |
       +----+
        v                  4 cells down, onto the gate's own 2-cell stub
        >>>|UbrM...        the gate strip, west wall, its own entry cell
```

`adapter->store` **58 drawn cells -> 6**; parsed **60 -> 8**. The corridor
x in [61,77], y in [118,147] was empty for its whole height, so the box does not
move: **287x253 before and after**.

Three things it deliberately does not do. It does not attach to the gate's
**north** wall — the gate's `U` turns away from the side it read from, so the
west wall is load-bearing. It does not delete a forwarder in favour of a plain
pipe (+4.14% when that was tried on the answer path). It does not build a relay
chain (one forwarder plus a short pipe beat three forwarders there). One room,
two stubs, and `memory_taped` is untouched.

### The measurement, and why it is 5.9% and not 8.5%

Native `fast_littleman`, checked-in 116-round tour, round-gated, `passed=True`:

| | ticks | box |
|---|---:|---|
| before (`merge-staging` 2a20a64, `mem_pad` 22) | 838,511,442 | 287x253 |
| after | **788,880,295** | 287x253 |
| | **-49,631,147 = -5.92%** | |

The estimate was 8.53% and the realised figure is 5.92%. Both halves of the gap
were measured rather than argued, by padding the room's in-stub and re-running
the tour:

**+20 cells on the in-stub costs +21,218,585 ticks — a derivative of 1,060,929
tour ticks per pipe cell on this leg.** So:

| | cells | ticks | % of 838,511,442 |
|---|---:|---:|---:|
| what the whole leg was worth (60 x 1,060,929) | 60 | 63,655,740 | **7.59%** |
| what removing 52 of those cells could return | 52 | 55,168,308 | 6.58% |
| what the room and its stubs cost back | ~5.2 | ~5,537,000 | ~0.66% |
| **realised** | | **49,631,147** | **5.92%** |

* **The profile's 8.53% was ~12% optimistic.** It priced the leg at one tick per
  cell per read (60 x 87,490) on the gated nine-round run. The tour's own
  derivative says 1.06M ticks a cell, not the ~1.19M that extrapolation implies
  — some of the request's transit does overlap the ring's seek, so not every
  cell-tick is on the critical path. The honest ceiling was 7.59%.
* **The room costs ~5 cells' worth, and it is the forwarder's loop, not its
  size.** A pipe is a 60-deep FIFO: the request's two words (three on a write)
  pipelined down it one tick apart. The room is one man on a six-cell cycle
  (`>Rv`/`^s<` — three cells from `R` to `s`, three back), so the words are
  re-serialised at one per six ticks and the second word waits a full service
  interval. That is the "free parallelism" the pipe was doing and the room
  cannot. Six is the minimum cycle that can hold both an `R` and an `s` (a 2x2
  loop is all corners), so this is the floor for one forwarder — and a second
  forwarder would reorder the words.

Net: **46.8 of the 52 removed cells came back.**

### Re-verified against `mem_pad` 16

The parallel JMPS-flow work (`MEM_PAD_FOR`, `INPUT_NORTH_WEST`, `SEEK_TELEPORT`)
walks the memory band six columns west, which is where this room's stubs have to
bind. Merged and re-measured — the room's placement is derived from the
adapter's floor and the block's own `in_cell`, so it follows without a re-sweep,
and `teleport:REQ` lands at the identical (61,120) 6x29:

| | ticks | box |
|---|---:|---|
| JMPS flow, request room **off** | 822,436,488 | 287x253 |
| JMPS flow, request room **on** | **773,267,928** | 287x253 |
| | **-49,168,560 = -5.98%** | |

49.17M against 49.63M at pad 22 — the two are additive, as disjoint legs should
be. `adapter->store` is 6 either way.

### What is left on this path

* **`reqK->bankK` (45/45/44/97 cells, 6.65%) does not fall out of this change.**
  Those arms are drawn inside `memory_taped.taped_store_block`, a different file
  with a different room pair, and they need the gate rooms themselves to reach
  their banks' walls. Left for a follow-up.
* **Two cells of the out-stub.** The exit descends four rows because the gate's
  `U` sits three rows into the gate strip and the room's south wall has to clear
  its roof. Attaching to the gate's west wall two rows higher would save 2 cells
  (~2.1M, ~0.25%) but needs the block's own request stub suppressed — a
  `memory_taped` change, so it belongs with the arms above.

### The follow-up is de-risked: `U` turns off the WALL, not the direction

`scratch/deadman3d-opt/probe_gate_grow.py`. The room here costs one forwarder
(~5.2 cells, ~0.66%) and its out-stub costs four more. Both would go away if the
**gate's own room** were grown north to meet the adapter instead — a two-cell
pipe, no forwarder, no man, no re-serialisation.

What made that non-obvious is that the gate's entry glyph is `U`, not `R`: *"on
success the man turns away from the side of the room he read from"*. Grow the
room and the pipe still lands on the **west** wall, but 33 rows above the man.
If "side" meant the direction from the man to the pipe he would turn *south* and
the gate would silently mis-route — exactly the failure mode this store has.

Measured, not argued. Gate 0's room pulled 30 rows north, the request fed into
its west wall two rows below the new roof, every address of the real 601-slot
plan written and read back individually through the live chain:

```
lift=30 plan=(352, 164, 15, 69) order=(3, 2, 0, 1)
  600 addresses, 0 wrong
```

So `U` reads the wall, and a gate room may be grown to its caller.

Two consequences, both for whoever owns `memory_taped.py` next:

* **This leg can lose its forwarder.** ~6 cells plus the ~5.5M forwarder cost is
  another ~11.9M, ~1.4% on top of M12. The sky is there: above gate 0's roof,
  rows 118..147, columns 67..93 are empty in the built grid (only this room's
  own east wall at 66 and a riser at 94).
* **It is the same move the `reqK->bankK` arms want** (45/45/44/97 cells, 6.65%)
  — a gate room grown to its *bank's* wall pays 2 cells instead of 45. That is
  why it was probed here rather than left to be discovered there.

Not taken in M12 on purpose: it is a `taped_store_block` parameter, not a
`lm1.machine` one, and `memory_taped.py` is being edited in parallel. The two
belong in one change on that file, not split across two branches.

---

## M13 — the DDA x-arm's redundant `LD WADDR` (the first *program* lever)

Every lever above this one moves cells. This one deletes an instruction, and it
beat all of them: **−4.37% for sixteen deleted lines.**

`scratch/DOOM-OPCODES.md` §5 named it from a stride-1, fully-attributed
per-opcode profile. The unrolled x-arm did

```asm
        LD  WADDR
        ADD S4X
        ST  WADDR      ; ISA op 8: "store[addr] = ACC (ACC preserved)"
        LD  WADDR      ; ...re-reads the word ACC already holds
        LDA            ; the x-side hit test
```

### The claim was checked in the implementation, not the docstring

* `isa.py` op 8 `ST`: micro `LIT1 SEND_MEM RING_READ SEND_MEM SWAP SEND_MEM SWAP`
  — the two `SWAP`s are a sandwich, so ACC comes back to where it started.
* `emulator._store` writes `em.b` (the accumulator) and assigns only `em.a`.
  It never touches `em.b`.
* `emulator._load_acc` (`LDA`, the consumer two lines down) takes its address
  from `em.b` and clobbers `em.a` first — so the `a = WADDR` that `ST` leaves
  behind, and which the deleted `LD` would have overwritten, is unobservable.
* The program already relies on this elsewhere and says so: `pclip: SUBI 1
  ; ST preserved ACC = perpWallDist`.

### Measured

116-round tour, native `fast_littleman`, taped tier:

| | ticks | box |
|---|---:|---|
| M12 | 773,267,928 | 287x253 |
| M13, the 16 reloads deleted | **739,507,401** | 287x253 |
| | **−33,760,527 = −4.366%** | |

**Predicted −4.32%; realised −4.37%.** The 0.05pt overshoot is a term the
prediction did not model: 5,536 × 470.9 counts only the deleted instructions'
own ticks, but 16 fewer instructions is also 32 fewer ROM words (P 4002 → 3970),
and a shorter ROM loop shaves the fetch wait of *every* instruction. Unlike the
two estimates that came in optimistic this session (`adapter->store` by 12%, the
"longest pipe" heuristic by two orders of magnitude), an exact per-opcode
attribution with a counted execution total lands on the nose.

### Why it is keyed to the tier

The `.asm` is shared between the two tiers and the canonical grid is byte-frozen
at `f62d63fd`, so the fix ships as `deadman3d_source(dda_acc_reload=False)`
behind a new `machine.TIER_PROGRAM`, keyed `(slug, tier)` exactly like
`TIER_LAYOUT` / `MEM_PAD_FOR` / `INPUT_NORTH_WEST`. Default is the reload, so
`deadman-3d.asm` regenerates byte for byte and `deadman-3d.man` / `_trim` /
`_v2` / `deadman-3d.input.txt` are untouched — verified, and the canonical build
still hashes `f62d63fd`. **The canonical machine would take the same −4.37%; it
is simply not allowed to move.**

### The same pattern elsewhere — the full census, taken and not

An `ST X` immediately followed by an unlabelled `LD X` is always a pure reload.
There are **20** in the canonical source; 16 are the x-arm's and are now gone.

| site | executions | worth | taken |
|---|---:|---|---|
| x-arm `ST WADDR` / `LD WADDR` ×16 | 5,536 / 9 frames | **4.32%** | **yes** |
| `ST HDG` / `LD HDG` (the turn block) | ≤1 a frame | ~0.01% | no |
| `ST NEWX` / `LD NEWX` (move, x commit) | ≤1 a frame | ~0.01% | no |
| `ST NEWY` / `LD NEWY` ×2 (move, y half-step and commit) | ≤2 a frame | ~0.02% | no |

The four movement-path sites are correct to delete and worth about **0.04% of
the run between them** — four executions a frame against the x-arm's 615. Left
in deliberately: each is a separate control-flow block to re-verify, and the
whole set is inside the measurement noise of a single tour run.

The y-arm has the pattern too, but **across a block boundary**, where it cannot
be deleted in place: `ywrd{k}` ends `ST WADDR` / `JMP hity{k}` and `hity{k}`
opens `LD WADDR`. `hity{k}` has four predecessors and the other three
(`ST PW`-then-fall-through, the `INCM WADDR` wrap, and the straight `ST PW`
path) genuinely need the load — `INCM` in particular leaves ACC holding the
*old* value (`emulator._inc_mem`: `em.a = old+1, em.b = old`). Taking it would
mean peeling a copy of the `hity` tail for the `ywrd` edge, and `ywrd` is the
*downward* quarter-column wrap: **60 executions in nine frames**, ~0.05%. Not
worth 16 more copies of a five-instruction tail. `ST POSY` / `JMP render` /
`render: LD POSY` is the same shape once a frame.

The `SDX` half of the cursor pair (521, 12,657 reads) is **not** this pattern
and is not free. `dda{k}` does `LD SDX` / `SUB SDY` / `BRN xarm{k}`, so ACC
holds `SDX − SDY` at the arm, not `SDX`; the arm's `LD SDX` is a real read.
Reconstructing it as `ADD SDY` trades an `LD` (470.9) for an `ADD` (421.8) —
5,536 × 49 = ~0.45% — but it is an arithmetic identity over a wrapping word
rather than a deletion, so it needs its own equivalence proof. Recorded, not
attempted.

### Gates

Emulator, both programs over the whole `WALK`, pixel-identical to the golden
frames — and **30,729 fewer instructions retired (2.39%)**, which is the deleted
`LD` count over the tour's length. `tests/test_deadman3d.py -m slow` **12/12**.
DOOM fast set **125 passed**. Rest of the default suite 2672 passed, 68 skipped;
the only `-m slow` failures are the three pre-existing ones AGENTS.md names
(`test_lm1_pipe_cost`, `test_lm1_grid_store`, `test_lm1_lane_order[deadman-3d]`),
all of which call `machine.build` directly and cannot see `TIER_PROGRAM`.
## M13 — the gate rooms reach their *callers*, and the request forwarder goes

M12 left two things on the table and de-risked both with one probe: `U` turns
away from the **wall** the pipe attaches to, not from the direction the pipe
comes from, so a gate room may be grown until it touches whoever feeds it and
the pipe between them stops existing rather than merely shrinking.

Both are taken here, in one change on `memory_taped.py`, behind two additive
knobs (`chain_reach`, `request_roof`) and two registries
(`machine.TAPED_CHAIN_REACH`, `machine.STORE_REQUEST_REACH`).

### What moved

| leg | before | after | who pays it |
|---|---:|---:|---|
| `adapter->store` | 6 drawn cells **+ a forwarder** | **2 drawn cells, no room** | every access |
| gate 0 -> gate 1 | 25 cells | **7** | 65.74% of accesses |
| gate 1 -> gate 2 | 25 cells | **7** | 9.55% |

Gate 0's roof comes up to one row under the adapter's floor, so the request is a
two-cell drop off the adapter's south wall onto the gate's own west wall. Gates
1 and 2 grow **west** until their walls stand one column east of the previous
bank's feed riser — the only thing in that corridor — so the link is the riser
hop and nothing else. Nothing else in the block moves: `pitch`, `gx` and `bx`
are computed from the *un*grown gates, so the banks, the box and the census are
where they were. **287x253 before and after, 20 men before and after.**

The traffic shares are `traffic.json` differenced per frame, mapped through
`TAPED_BANK_ORDER (3,2,0,1)` over `TAPED_BANKS (352,164,15,69)`: 11,222 reads
and 3,416 writes a frame, and `A > 0` means "not mine, pass downstream", so
link *j* is walked by every access bound for chain position *j+1* or later.

### The measurement

Native `fast_littleman`, checked-in 116-round tour, round-gated, `passed=True`
on all five:

| build | ticks | vs base |
|---|---:|---:|
| base (M12: forwarder on, no reach) | 773,267,928 | — |
| `chain_reach` only | 758,190,382 | **-15,077,546 = -1.950%** |
| `request_roof` only | 761,842,827 | **-11,425,101 = -1.478%** |
| **both (shipped)** | **746,765,665** | **-26,502,263 = -3.427%** |
| both + `chain_pad=5` | 750,953,570 | +4,187,905 |
| both + `chain_pad=15` | 759,329,892 | +12,564,227 |

The two halves are **additive to 384 ticks** — 15,077,546 + 11,425,101 =
26,502,647 against a realised 26,502,263 — which is what disjoint legs should
do and is the cheapest available check that neither knob is quietly moving the
other's pipe.

### The derivative, and why the estimate was 8% and the result is 3.4%

`chain_pad` leaves the grown gates that many columns short of their callers and
lengthens every link by exactly that much, moving nothing else — the same
instrument `resp_pad` is in `lm1.machine`, and the same one that priced
`adapter->store` in M12.

**+5 cells on each of the two links costs +4,187,905 ticks**; +15 costs
+12,564,227. Weighted by who walks them (`p * (0.6574 + 0.0955)` accesses-weighted
cells) those are **1,112,472** and **1,112,528** tour ticks per weighted pipe
cell — linear to five significant figures over a 3x range, and against M12's
independently measured 1,060,929 on `adapter->store` the same rate to 5%. So the
store's request legs really do cost one tick per cell per access that walks them,
and the arithmetic below is trustworthy rather than optimistic:

| | weighted cells | predicted | realised |
|---|---:|---:|---:|
| chain links (18 x (0.6574 + 0.0955)) | 13.55 | 15.07M | 15.08M |
| request leg (5 pipe cells + the forwarder's ~5.2) | ~10.2 | 11.35M | 11.43M |

**Both halves came in within 1% of prediction.** That is the difference from
M12, where the region profile was 12% optimistic: this time the per-cell rate
was measured first.

### So where did the predicted 8% go? It was never on this leg

The brief's 6.65% was the `reqK->bankK` arms — 45, 45, 44 and 97 cells from each
gate's *local* arm to its bank. **Those cannot be taken this way, and the reason
is worth writing down because it is not a routing problem.**

A gate has **two** outgoing pipes and they share the east wall; `s` sends into
the **nearest** (`SPEC.md` §Nearest, Manhattan from the instruction). The whole
design rests on the north arms being nearer the local pipe and the south arms
nearer the downstream one, and the margins are small — here are all eight `s`
glyphs of gate 0 (`UbrM`531`-X`, compact body, east wall at column 25):

```
      s at   d(local, row 1)   d(down, row 6)   margin
    (15,1)          11               16            5
    (17,1)           9               14            5
    (15,2)          12               15            3
    (17,2)          10               13            3
    (19,2)           8               11            3      <- tightest
    (16,5)          14               11            3
    (18,5)          12                9            3
    (20,5)          10                7            3
    (16,6)          15               10            5
    (18,6)          13                8            5
```

Move the local attachment `L` rows off the body and the tightest constraint is
`|c - 19| + L < 11`. **At `L = 4` the north write arm binds to the downstream
pipe and every local read is answered by the wrong bank, with no error.** The
33-row climb the arms actually want is `L = 31`.

Three ways round it were checked and all of them cost more than they save:

* **Widen the gate** so the downstream pipe is further from the north arms:
  `L < 8 + K` for `K` extra columns, so reaching the bank needs `K = 24` — a
  50-column gate, and the room would then have to be both 50 wide and 31 tall in
  a band where the only free columns are 0..32 (west of the first bank).
* **Grow the room symmetrically**, local on the north wall and downstream on the
  south wall at the same column: that one is *binding-clean at any `L`* (the
  margins become a constant 4 and 2). But it needs free space above **and**
  below, which only gate 0 has, and it puts gate 0's downstream pipe 31 rows
  below gate 1: link0 goes 25 -> ~45 cells on 65.74% of accesses, -12.9 weighted
  cells against +8.6 saved. **Net negative.**
* **Move gate 0's body up** to the bank's in-row instead: feed0 45 -> 13 is
  -10.2 weighted cells, chain link0 25 -> ~51 is +17. **Net negative**, and for
  the same reason — the gate's traffic is 34% local and 66% downstream, so the
  gate belongs next to the *majority* path.

The general statement, and it is the useful one: **a room can reach its caller
but not its callee.** The incoming side is free (`R`/`U` take from any incoming
pipe with no distance term); the outgoing side is not, the moment there is more
than one outgoing pipe.

### What is actually left on the bank arms, and what it would cost

The arms are worth 27 weighted cells (~28.6M, ~3.7%) and the only mechanism that
fits is the one M12 used: **a vertical forwarder room per arm**, in the corridor
between the bank's in-row and the gate strip. The free-column survey of the
built block, rows 19..50:

```
free column runs: (0..32), (80), (128), (176)
```

Only the band west of the first bank is wide enough today. A 6-column
`teleport_v` in each inter-bank gap needs `pitch` 48 -> 51, i.e. **+9 block
columns -> ~296x253**, four cells off the pinned 300-column ceiling, and it adds
four men (census 20 -> 24, ceiling 30). Estimated **-3.7% before the four
forwarders' own ~5.2 cells each**, so realistically ~-2.9%. Not taken here
because it is a different mechanism from the one this change is about and it
spends the machine's entire remaining width; it is the next thing to try.

### Correctness

The failure mode is silent — a mis-bound arm or a wrong literal answers from the
wrong bank rather than erroring — so every address of the live plan is written
and read back **individually**, through the same chain `lm1.machine` builds
(`TAPED_BANK_ORDER (3,2,0,1)` over `TAPED_BANKS (352,164,15,69)`, both gate
forms: positions 0 and 1 are `high` gates, position 2 is low):

```
scratch/deadman3d-opt/readback_reach.py 601 352,164,15,69 3,2,0,1
  shipped  224x60 in=(2, 54): 600 addresses, 0 wrong
  chain    224x60 in=(2, 54): 600 addresses, 0 wrong
  roof     224x60 in=(3, 22): 600 addresses, 0 wrong
  both     224x60 in=(3, 22): 600 addresses, 0 wrong
```

Plus every composed frame of the 116-round tour byte-identical (`passed=True`
above, on all five builds), `tests/test_deadman3d.py -m slow` 12/12, and the
byte-identity pins: `deadman-3d.man` / `_trim.man` / `_v2.man` still
`f62d63fd…`, `deadman-3d.input.txt` still `654d35d6…`. Only the taped family
moved, `c0db5a3f…` -> `bfae891f…`.

### M13b — the arms after all: a forwarder each, in a widened corridor

The arms cannot be taken by growing the gate (above), but they *can* be taken by
the mechanism M12 used on `adapter->store`: **a room with one pipe in and one
pipe out has none of the gate's binding problem** — `R` takes from any incoming
pipe and `s` has a single outgoing one to choose between. So each
`reqK->bankK` arm gets a `memory_men.teleport_v` in the corridor between the
banks (`machine.TAPED_FEED_TELEPORT`, `taped_store_block(feed_teleport=True)`).

The corridor is `pitch - bank_w + 1` columns wide — a tape block's own first
column is empty — so the shipped `pitch` 48 leaves four and `teleport_v` wants
six. **`pitch` 48 -> 50**, and that is the whole cost besides the men.

| leg | before | after | share of accesses |
|---|---:|---:|---:|
| `req0->bank0` | 45 | 4 + 6 stub cells, + one room | 34.26% |
| `req1->bank1` | 45 | 4 + 6 | 56.19% |
| `req2->bank2` | 44 | 3 + 6 | 6.45% |
| `req3->bank3` (the last gate's downstream) | 97 | 58 + 6 | 3.10% |

The chain links get one cell *shorter* on the way past (7 -> 6): the corridor
obstruction they have to clear is now the forwarder's own entry stub rather than
a riser, and it stands one column further east.

| build | ticks | vs previous | vs M12 base |
|---|---:|---:|---:|
| M13 (chain + roof) | 746,765,665 | — | -3.427% |
| **+ feed forwarders** | **715,447,099** | **-31,318,566 = -4.194%** | **-57,820,829 = -7.477%** |

Predicted from the same 1,112,500 ticks/weighted-cell: 29.7 weighted cells,
33.1M. Realised 31.3M — **6% under prediction**, which is the four forwarders'
own re-serialisation showing up as slightly more than the ~5.2 cells each that
M12's single room cost.

287x253 -> **293x253**, seven columns under the pinned ceiling, and 20 men -> 24
against the visualizer's 30. `cpu->drum` follows the block east, 52 -> 63 cells:
that is jump-notice latency on the longest pipe in the machine and worth 0.019%
per 437 cells (AGENTS.md trap 1), i.e. nothing.

One thing that bit, and it is a `SPEC.md` rule rather than a geometry mistake:
**a pipe's first arrowhead must point away from its source room**, so the climb
into the forwarder cannot start on the gate's own east-wall cell and turn north
there — the widest gate's east wall already sits against the corridor's first
column, so the climb uses the room's *second* interior column. The failure was a
`no-pipe` fatal three ticks in, not a wrong answer, so this one at least is loud.

### Where the taped store now stands

`773,267,928 -> 715,447,099` on the 116-round tour, **-7.48%**, against the
brief's estimate of ~8% combined. Every request leg in the block is now either a
two-cell stub or a room:

```
adapter -> gate0    2 cells, no room      (gate 0's roof reaches the adapter)
gate0   -> gate1    6 cells, no room      (gate 1's west wall reaches gate 0)
gate1   -> gate2    6 cells, no room
gateK   -> bankK    10 cells + one room   (the callee legs; a room is required)
bankK   -> CPU      6 cells + the collector
```

What is left on this path is the last arm, `req3->bank3`: 58 of its 64 cells are
one horizontal run under bank 2, because the last gate feeds two banks and only
one of them can be adjacent. It is 3.10% of accesses, so the run is worth ~2.0M
ticks (~0.28%) — the smallest lever this file has left.

---

# H1 — `deadman-3d_hires`: a tick baseline, and the levers it was missing

At `50277ab`. Everything here is the **hi-res** family; the three committed
64x48 families are untouched and hash-verified below.

## The obstacle, and how it was removed

This machine has never had a tick number. `FastLittleman`'s display judge
required *exactly one* display and the wall is four 64x48 panels, so gating was
refused outright; the reference wasm engine OOMs its 4 GB heap around ~10M
ticks and one hi-res frame costs ~50M. That left ungated runs, and an ungated
run has **no stopping condition** — the machine blocks on input and the engine
ticks to the cap. (The unrun `hires_gate2.py` an earlier agent left behind had
exactly this bug: `max_ticks=40e9` against a machine that stops doing work at
~1e9.)

`FastLittleman.run(frames=..., frame_tiles=(cols, rows))` now judges a tiled
wall. Each expected frame is cut into `cols*rows` tiles in reading order — which
is the order both engines discover display rooms in, verified against
`lm.mjs analyze` below — and panel *d* is compared against tile *d* on that
panel's *n*-th COMMIT. Round *n+1* is released once the **slowest** panel has
committed frame *n*: composition by index, the invariant
`display.tiled_frames_from_writes` already enforced on the emulator side. One
display is the `(1, 1)` case and behaves exactly as before (`test_fast_littleman`
green, `deadman-3d`'s own frame gate unchanged).

`FastResult.frame_ticks` carries the tick each *logical* frame landed on, so a
per-frame cost is a difference of two measured stamps rather than a total
divided by a count.

## The baseline

**Engine:** `fast_littleman` native backend (the independent C++ validator),
round-gated, `passed=True` — every pixel of every panel of all 21 frames matched
the model. **Method:** `scratch/deadman3d-opt/hires_gate2.py 21`, differencing
`FastResult.frame_ticks`. **Machine:** 500x348, `P=8863`, tape 902, at `50277ab`.

| frame | commit tick | cost | | frame | commit tick | cost |
|---:|---:|---:|---|---:|---:|---:|
| 0 | 35,991,674 | *35,991,674* | | 11 | 599,121,846 | 68,338,079 |
| 1 | 86,244,888 | 50,253,214 | | 12 | 665,442,057 | 66,320,211 |
| 2 | 135,927,732 | 49,682,844 | | 13 | 727,636,714 | 62,194,657 |
| 3 | 180,671,297 | 44,743,565 | | 14 | 783,671,891 | 56,035,177 |
| 4 | 225,446,629 | 44,775,332 | | 15 | 833,037,857 | 49,365,966 |
| 5 | 269,996,552 | 44,549,923 | | 16 | 875,588,144 | 42,550,287 |
| 6 | 311,276,032 | 41,279,480 | | 17 | 924,852,786 | 49,264,642 |
| 7 | 357,055,148 | 45,779,116 | | 18 | 977,716,206 | 52,863,420 |
| 8 | 406,749,432 | 49,694,284 | | 19 | 1,029,318,683 | 51,602,477 |
| 9 | 459,013,745 | 52,264,313 | | 20 | 1,072,188,070 | 42,869,387 |
| 10 | 530,783,767 | 71,770,022 | | | | |

**Frames 1..20: 1,036,196,396 ticks, mean 51,809,819 a frame.**
**Superseded by H3** — two program levers landed on the 64x48 machine after
this branch forked and both transfer: the table below is the machine as of
`50277ab`, and the current one is **990,990,612 / 49,549,530 a frame**.
Frame 0 is boot plus the title RLE and is *not* comparable — it renders no 3D at
all. Total for the 21-round tour: 1,072,188,070.

The spread is real geometry, not noise: frames 10..13 (66-72M) are where the
corridor opens and the ray count that reaches a far wall peaks; frame 6 (41.3M)
is the tightest view. Against the 64x48 machine's ~4.7M a frame this is ~11x for
4x the pixels — the extra is the router, the four panels' COMMIT traffic and the
per-column work that does not halve.

## What hires took, what it declined, and what each was worth

All on the same 21-round tour, comparing **frames 1..20** (`walk`) so the boot
frame cannot flatter anything. `scratch/deadman3d-opt/hires_opt.py`.

| lever | walk ticks | vs base | |
|---|---:|---:|---|
| base (`b78eafc`: compact gate + bank order) | 1,090,194,166 | — | |
| `dda_acc_reload=False` (the M13 program lever) | 1,042,173,023 | **-4.405%** | **taken** |
| `STORE_REQUEST_REACH` + `store_offset (-14, 0)` | 1,085,082,598 | **-0.469%** | **taken** |
| `TAPED_FEED_TELEPORT` | 1,087,081,434 | **-0.286%** | **taken** |
| `STORE_REQUEST_TELEPORT` (same offset) | 1,086,250,847 | -0.362% | declined |
| `TAPED_CHAIN_REACH` | 1,089,980,434 | -0.020% | declined |
| `store_offset (-14, 0)` alone (the control) | 1,091,072,532 | **+0.081%** | — |
| **all three shipped** | **1,036,196,396** | **-4.953%** | |
| shipped + `TAPED_CHAIN_REACH` | 1,036,018,295 | -4.969% | declined |

Additivity: -48,021,143 (acc) + -8,183,259 (roof+feed together) = -56,204,402
predicted against -53,997,770 realised, 4% short. That is the expected direction
— the ACC lever deletes store reads, and the roof and the forwarders shorten
what a store read costs, so they overlap by construction.

### `TIER_PROGRAM` cannot reach this family at all

`machine.TIER_PROGRAM` is read by `_tier_program`, which `build_for` calls
**only when no `program=` was passed**. `deadman3d_hires.build_local` always
passes one, because the level comes out of an IWAD at call time and there is no
checked-in `.asm` to load. An entry keyed `("deadman-3d_hires", "taped")` would
be inert config.

It is also unnecessary. The registry exists to keep a **byte-frozen** grid off a
program fix — `deadman-3d.asm` pins `f62d63fd…` and may not move. This family
commits nothing (`test_the_family_commits_nothing`), so `hires_source()` simply
passes `dda_acc_reload=False` itself. The generated assembly differs from the
old one by **exactly sixteen deleted `LD  WADDR` lines and nothing else**
(`diff` over the two builds), P 8,895 -> 8,863.

**-4.405% here against -4.37% on `deadman-3d`** (M13) — the same lever at four times
the pixels. That is what it should be: the deleted instruction is one store read
per DDA step, the DDA step count scales with the pixel count, and so does
everything it is measured against.

### `STORE_REQUEST_REACH` did not cost more here — it did not *build*

```
MachineError: the store's request column 101 is not under the adapter's
floor (81..92); the drop has nowhere to start
```

`deadman-3d` buys that overlap with a `TIER_LAYOUT` `store_offset` of
`(-20, 0)`; hires had no `TIER_LAYOUT` entry at all. Column `101 + dx` lands in
`81..92` for `dx` in `-20..-9`, and `scratch/deadman3d-opt/hires_roof.py` shows
**every value in that window binds** — including `-19` and `-20`, which the
`STORE_ANSWER_WEST` note records as failing. (No contradiction: those failed
that registry's own widened collector, not placement.)

Which value is chosen **does not matter at all**. The 21-round tour comes out at
1,085,082,598 ticks **to the tick** at `-9`, `-14` and `-20`. With the roof
reaching, the only thing crossing the gap is the two-cell drop and everything
else is translation; without it the request pipe costs a measurable 1,246 boot
ticks per column of westward move. `-14` is the middle of the window, so the
drop has five columns of margin either side.

The offset is not free on its own — **+0.081%** — so the roof's gross -0.550%
nets to -0.469%.

### The two declines have one root: hires' own bank order

`TAPED_BANK_ORDER["deadman-3d_hires"] = (3, 0, 1, 2)` puts the bank holding
**90.79% of reads and 99.85% of writes** at chain position **0**. A request for
position 0 walks *zero* chain links (0.13 gates a read, against `deadman-3d`'s
1.15), and that single fact explains both results:

* **`TAPED_CHAIN_REACH` is declined.** It shortens links 0 and 1 from 25 cells
  to 7. On `deadman-3d` those carry 68% and 12% of reads and it is worth -1.950%;
  here they carry ~4% and ~0.2% and it is worth **-0.020%** — and only -0.017%
  on top of the shipped set. It is free in box terms (500x348 either way) and is
  still not taken: two hundred-thousandths of a run does not buy pinning three
  gate rooms into a grown form that the next store change has to work around.
* **`TAPED_FEED_TELEPORT` is taken for the mirror-image reason.** ~91% of
  accesses walk `req0->bank0` and *nothing else* — the arm the chain lever
  cannot touch and this one shortens most. -0.286% against -4.194% on
  `deadman-3d`; the arm is the same ~45 cells, but a 128x96 frame is four times
  the work between two store accesses, so any one leg is a quarter of the share.
  Its two extra columns of pitch cost nothing: **this machine's width is the
  496-column wall's, not the store's.** 500x348 before and after.

`STORE_REQUEST_TELEPORT` is declined as **measured**, not as assumed superseded:
at the same offset, where both forms bind, the room gets -0.362% and the roof
-0.469%. The 0.107pp gap is the forwarder's own six-tick re-serialisation, which
is exactly the thing `STORE_REQUEST_REACH` was built to stop paying (M13).

`TAPED_BANK_ORDER` stays `(3, 0, 1, 2)` — derived from hires' own traffic over
its uniform quarters `(226, 226, 226, 223)`, never `deadman-3d`'s `(3, 2, 0, 1)`
over `(352, 164, 15, 69)`.

## Verification

* **Every composed frame byte-identical** to the pre-change build: all 27 PNGs
  `cmp`-clean, and `deadman-3d_hires.cases.json` (which *is* the expected-frame
  set) and `.input.txt` byte-identical too. Only the `.asm` moved, by the
  sixteen lines above.
* **The frames are also verified live**, which matters more than the `cmp`:
  the gated run above is `passed=True` on all 21 frames — every pixel of every
  panel against the model, across both seams. A wrong ACC assumption would show
  as corrupted geometry and it does not.
* `node littleman/lm.mjs analyze` — **4 displays at 64x48**, listed order ==
  reading order == tile order: `(366,241) (434,241) (366,297) (434,297)`.
  35 rooms, 62 pipes.
* `lm.mjs tick … 1000000` on the reference JS engine: 1,000,000 ticks,
  `halted:false`, **no fatal** (empty stderr; `lm.mjs` writes `fatal: …` there).
* `scratch/deadman3d-opt/packed_probe.py`: scattered and packed walls both
  `fatal None`, `frames [2, 2, 2, 2]`, composed images identical, all four
  corners painted. This is the routing check a rendered PNG cannot do.
* Committed families unmoved: `deadman-3d.man` / `_trim.man` / `_v2.man`
  `f62d63fd…`, `_taped.man` / `_m6_taped.man` `6a739e1c…`,
  `deadman-3d.input.txt` `654d35d6…`.
* Default suite 2,756 passed / 68 skipped; `tests/test_deadman3d.py -m slow`
  **12/12**.

## What is next, with the arithmetic already done

The store levers have run out on this machine, and the table says why: the three
that remain are worth 0.020%, and the two that were taken are worth 0.755%
between them. **95% of the shipped win is the one program lever.** At 128x96 the
store is simply not where the ticks are — the frame is four times the pixels
against the same 902-slot tape, so every store leg is a quarter of the share it
has on `deadman-3d`. The next lever for this family is in the DOOM unit or the
router, not the store block, and `frame_ticks` is now the instrument to find it
with.
## M14 — the DDA compares a *difference*: 12.5% fewer reads a frame

The first lever aimed at the quantity `scratch/DOOM-OPCODES.md` §5 named as the
one with room left — **how many store reads the program issues** — rather than
at what a read costs. Reads are reported here alongside ticks, because ticks
alone cannot tell a deleted read from a cheapened one.

### The identity

The DDA step's only use of `sideDistX` *inside the loop* is the sign of
`sideDistX - sideDistY`. The canonical step rebuilds that difference from both
scalars every iteration and then re-reads `SDX` inside the x-arm to increment
it:

```asm
dda{k}: LD  SDX          ; read 1
        SUB SDY          ; read 2
        BRN xarm{k}
xarm{k}: LD  SDX         ; read 3 — the same word again
        ADD DDX          ; read 4
        ST  SDX
```

Keep the **difference** in that slot instead (`SDD`, address 521 unchanged) and
`sideDistY` absolutely, and the same step is:

```asm
dda{k}: LD  SDD          ; read 1
        BRN xarm{k}      ; ACC survives a branch
xarm{k}: ADD DDX         ; read 2 — ACC is still SDD
        ST  SDD
```

`sideDistX += deltaDistX` is `SDD += deltaDistX`; `sideDistY += deltaDistY` is
`SDD -= deltaDistY`, which is the y-arm's one added `SUB`/`ST` pair. Two facts
make it legal, and both were checked in the implementation rather than the
docstring:

* **`BRN` preserves ACC.** `emulator._br_neg` reads `em.b` and assigns nothing,
  and the micro (`RING_READ SWAP SIGN_BRANCH THREE_WAY`) is a `W`…`W` sandwich.
  The program already depends on this *on the taken path*: the prologue's
  `DIV RDX` / `BRN ddxneg` / `ddxneg: NEG` negates the quotient `BRN` jumped on.
* **The difference is exact.** `emulator.wrap` is applied to every operation, so
  a difference maintained by `ADD`/`SUB` is the same 64-bit word as one
  recomputed from the two absolutes — not congruent-modulo-something, identical.
  `BRN` tests that word's sign, so it cannot distinguish the two forms. This is
  the equivalence proof M13 asked for before taking this.

What it costs, all outside the hot step: the two per-ray seed arms swap order so
`sidey` runs first (`sidex` then ends with ACC = sideDistX and folds the seed
difference in place, `SUB SDY` / `ST SDD` for one `ST SDX`), and the x-side hit
tail rebuilds `sideDistX = SDD + SDY` — 576 rays and 449 x-hits in nine frames
against 5,536 x-steps. Net +6 ROM words; **the DDA step itself is word-neutral**.

### Measured — reads first

Gated `WALK[:8]`, native `fast_littleman`, reads counted exactly off the four
bank→CPU pipes (`scratch/deadman3d-opt/reads_gate.py`):

| | reads / 9 frames | reads/frame | ticks | ticks/frame |
|---|---:|---:|---:|---:|
| before | 82,009 | 9,112 | 48,660,903 | 5,406,767 |
| after | **71,785** | **7,976** | **46,114,271** | **5,123,808** |
| delta | **-10,224 (-12.47%)** | -1,136 | -2,546,632 | **-5.23%** |

The read model predicted -10,047 (7,120 `SUB SDY` + 5,536 `LD SDX` deleted,
1,584 + 576 + 449 added); realised -10,224, a 1.8% miss. The DDA-scalar bank
carries all of it: its pipe goes 47,755 → 37,531 and the other three banks are
unchanged to the read.

**The marginal price of a read on today's machine is 2,546,632 / 10,224 = 249
ticks** — not the 470.9 `DOOM-OPCODES.md` measured, because M12/M13b have since
taken 7.5% off the store path. Any further read-count arithmetic should use 249,
and the profile's per-opcode means should be re-taken before they are trusted
again.

### The tour, and why it is smaller

| | ticks | box |
|---|---:|---|
| M13b | 683,820,497 | 293x253 |
| **M14** | **668,862,998** | 293x254 |
| | **-14,957,499 = -2.187%** | |

**-5.23% on the profiled gate but -2.19% on the 116-round tour**, and the
difference is workload, not model error: the tour's frames are *more* expensive
(5.90M against 5.41M before the change) because they run the sprite pass, the
shot ladder and the HUD hard, and none of that touches the DDA. The gate is
`WALK[:8]` — the opening walk, where the DDA is the frame. Both figures are
real; the tour is the one that ships.

A prediction made from `DOOM-OPCODES.md`'s 470.9 ticks a `LD` said -7.06% and
came in at -2.19% on the tour. Decomposed: about 40% of the gap is the read
getting cheaper since the profile (470.9 → 249), and the rest is the tour's
lower DDA share. The reads figure, which was predicted to 1.8%, is the one that
behaved — which is the argument for reporting it.

### Gates

Emulator over the whole `WALK`, pixel-identical to the golden frames
(`scratch/deadman3d-opt/dda_diff_gate.py`), and **36,663 fewer instructions
retired (2.93%)**. `tests/test_deadman3d.py -m slow` **12/12**. DOOM fast set
129 passed. `deadman-3d.man` / `_trim` / `_v2` still `f62d63fd…`,
`deadman-3d.input.txt` still `654d35d6…`, and `deadman3d_source()` with default
arguments still regenerates the checked-in `.asm` byte for byte — the lever
ships as `deadman3d_source(dda_diff=True)` through the existing
`machine.TIER_PROGRAM`, exactly as M13 did.

## M15 — the three loop laps were `BRN`/`BRZ`, and `seek_split` only takes `JMPF`

Found while costing M14's follow-ups, and it is the largest single item this
file has recorded since M12. **`machine.SEEK_OPS` is `("JMPF",)`** — the seek
split rewrites a *jump* and nothing else, so a `BRN`/`BRZ` keeps its classic
discard loop however far it goes. A **backward** branch's forward-skip count is
nearly the whole ring, so every lap of every loop recirculated ~P words at 8
ticks a word:

| lap | branch | laps/frame | words/lap | ticks/frame |
|---|---|---:|---:|---:|
| `xarm15` | `BRZ dda0` | 15.2 | 2,214 | **269,564** |
| `hity15` | `BRZ dda0` | 3.3 | 1,553 | 41,424 |
| `boot` | `BRN boot` | round 0 only | 3,828 | 1.71M one-off |
| `title` | `BRN title` | round 0 only | 3,877 | 1.64M one-off |

The DDA's lap alone was **5.3% of a frame**. Cross-checked against the grid
profile before building anything: `BRZ`'s whole measured slab bill is 2,384,475
ticks over nine frames and the two DDA laps predict 2,383,000 of it, so
essentially *all* of `BRZ`'s discard was the lap. (A ray averages 12.4 steps
against `DDA_UNROLL = 16`, which is why 861 steps a frame lap only 18.5 times.)

### The fix is three stubs

```asm
        BRZ lap15           ; forward, over `JMP whx`
        JMP whx
lap15:  JMP dda0            ; a JMP, so `seek_split` can make it a seek
```

`boot`/`title` need one more instruction each because their lap is a
fall-through loop: `BRN bootl` / `JMP bootd` / `bootl: JMP boot` / `bootd:`.
Five instructions, ten ROM words, and both DDA arms share one stub.

### Measured

| | tour (116 rounds) | gate (`WALK[:8]`) | ticks/frame | reads/frame |
|---|---:|---:|---:|---:|
| M14 | 668,862,998 | 46,114,271 | 5,123,808 | 7,976 |
| **M15** | **638,946,726** | **42,517,078** | **4,724,120** | 7,976 |
| | **-29,916,272 = -4.472%** | **-7.80%** | -7.80% | **unchanged** |

Reads are *unchanged*, which is the point of reporting them: this is a
control-flow lever, not a memory one, and the two are now separable in the log.
Predicted -5.5% on the tour from `(269,564 + 41,424) x 116 + 3.35M` against
668.9M; realised -4.47%, the shortfall being the seeks the stubs now pay
(18.5 a frame at ~1,008) plus ten more ROM words on every fetch.

The box goes back to **293x253** — M14's extra row was the ROM fold, and ten
words did not re-cross it.

### What is left of the discard bill

`scratch/deadman3d-opt/skip_sites.py` re-runs the census. After M15 every
remaining non-seekable discard is a `BRN dda{k} -> xarm{k}`: the x-step
stepping over the y-arm it did not take, ~64 words a step, **~330,000 ticks a
frame (7%) in total.** It cannot be turned into a seek — a seek costs 1,008 and
the skip costs 512 — so the only lever on it is *making the y-arm shorter*, and
the two candidates are costed in the commit message. Neither is taken here.

### Gates

Emulator over the whole `WALK`, pixel-identical to the golden frames. DOOM fast
set 130 passed, `tests/test_deadman3d.py -m slow` 12/12, default suite 2755
passed / 68 skipped. `deadman-3d.man` / `_trim` / `_v2` still `f62d63fd…`,
`deadman-3d.input.txt` still `654d35d6…`, `deadman3d_source()` with default
arguments still byte-identical to the checked-in `.asm`. Ships as
`deadman3d_source(lap_via_jump=True)` through `machine.TIER_PROGRAM`.

`test_every_loop_lap_is_a_jump_so_the_seek_split_can_take_it` pins the property
rather than the saving: after the rewrite **no `BRN`/`BRZ` in the program has a
backward target**, which is the condition under which none can pay a whole-ring
discard. It asserts both directions, so deleting the lever fails it.

## M16 — the DDA, emitted once per sign of stepY

M15 left one non-seekable discard on the table and named it: every `BRN dda{k}
-> xarm{k}` steps the x-arm over the y-arm it did not take, ~64 words at 8
ticks, **~330,000 ticks a frame (7%)**. It cannot become a seek — a seek is
1,008 and the skip is 512 — so the lever is a *shorter y-arm*.

The sign of stepY is a fact about the whole **ray**, fixed at the seed. The
canonical loop re-decides it on every y-step (`LD STPY` + `BRN`) and carries
both PW arms and both quarter-column wrap arms in all sixteen copies. Emitting
the DDA twice — once per sign — deletes all of that from the copy:

| per copy | before | after (up / down) |
|---|---:|---:|
| the compare head | 4 words | 4 |
| `LD STPY` + `BRN yneg` | 4 | **0** |
| the PW shift arms | 20 | 8 |
| the wrap arms | 20 | 6 / 10 |
| `hity` + the x-arm | 30 | 30 |
| **total** | **88** | **62 / 66** |

Two things fall out beyond the deleted read. The surviving wrap arm has nothing
to jump over, so it **falls straight through into the hit test** instead of
ending `JMP hity{k}`; and the ray's sign now picks which loop's *own stepX seed*
it enters, so `sidey` no longer writes `STPY` at all and neither does anything
else — the scalar is dead.

### The unroll had to be re-swept, because the copy changed size

Two loops at 16 would be ~576 more ROM words, and the drum's routing budget is
the constraint, not the 300-column ceiling: `rom_headroom.py` (raise
`DDA_UNROLL` and build) says **P=4,514 binds and P=4,602 does not**, at every
`ROM_ROWS` — the seek teleport runs out of clear column east of the drum. A
split copy is smaller, so the sweep is its own question. On the 116-round tour:

| `DDA_SPLIT_UNROLL` | P | box | ticks |
|---:|---:|---|---:|
| 11 | 3,972 | 293x253 | 614,341,715 |
| 12 | 4,096 | 293x254 | 614,817,361 |
| 13 | 4,220 | 293x253 | 612,124,654 |
| **14 (shipped)** | **4,344** | 293x254 | **611,021,810** |
| 15 | 4,468 | 293x253 | 611,591,730 |
| 16 | 4,600 | — | does not route |

The curve is flat to ±0.6% across the whole feasible range, which is the mark of
`lap_via_jump` having already taken the lap cost out: at M14 prices a shorter
unroll would have been ruinous.

### Measured

| | tour | gate (`WALK[:8]`) | ticks/frame | reads/frame |
|---|---:|---:|---:|---:|
| M15 | 638,946,726 | 42,517,078 | 4,724,120 | 7,976 |
| **M16** | **611,021,810** | **41,082,688** | **4,564,743** | **7,813** |
| | **-27,924,916 = -4.371%** | -3.37% | -3.37% | **-163 (-2.0%)** |

Note the split: reads fall only 2% (the deleted `LD STPY`, 163 a frame) while
ticks fall 4.4% on the tour. **Most of this lever is the discard, not the
read** — which is why it pays more on the tour (38.5% y-steps over the full
walk) than on the gate (22%), the exact opposite of M14. The two halves of the
session's work have opposite workload sensitivities, and between them the
measurement is stable.

Emulator over the whole `WALK` is pixel-identical to golden with **1,170,791
instructions retired against M15's 1,217,933 (-3.9%)**.

### Where the session ends

| | tour | vs start |
|---|---:|---:|
| session start (M13b) | 683,820,497 | — |
| M14, the DDA difference | 668,862,998 | -2.19% |
| M15, the loop laps | 638,946,726 | -6.56% |
| **M16, the stepY split** | **611,021,810** | **-10.65%** |

Reads a frame: **9,112 -> 7,813, -14.3%.** Box 293x254, six columns under the
pinned ceiling. `deadman-3d.man` / `_trim` / `_v2` `f62d63fd…`,
`deadman-3d.input.txt` `654d35d6…`, `deadman-3d_taped.man` `a11edcc6…`.

---

# H2 — the three answer rooms: re-measured against the shipped geometry, still declined

The user's observation is exact: hi-res carries **three** forward-only rooms on
the answer path where the 64x48 machine carries **one**.

```
(58, 93)  teleport:L   x 65..108  y 92..95    machine-side, wide
(66, 93)  teleport:U   x 57..62   y 92..105   machine-side, tall
(104,103) the collector           inside the store block (x 61..242 y 97..156)
```

`STORE_ANSWER_WEST` is exactly the collapse: it widens the **collector** (which
is already a teleport — `R` has no distance term, so widening one is free) until
its west end reaches the CPU, flips its exit stub from a north riser to a south
one, and `build_for` then drops `STORE_TELEPORT`'s two rooms. Three rooms to
one, and the surviving room is the collector.

**The previous two declines were measured against geometry that no longer
exists** — both predate hires having a `TIER_LAYOUT store_offset` at all, and
`STORE_REQUEST_REACH` gave it one this session. So it was re-asked, and the
windows really do intersect:

| | constraint | window |
|---|---|---:|
| the collapse | collector wall `-18 - store_dx`, guard wants `>= 1` | `dx <= -19` |
| the roof | request column `101 + dx` inside adapter floor `81..92` | `dx in -20..-9` |

**`dx` -19 and -20 satisfy both.** It still cannot be placed, and the reason is
no longer the one on record. Three constraints, peeled one at a time:

**1. Row 107, and it is the adapter.** At the shipped `answer_west`, all **80**
attempts — 40 `mem_pad`s x roof on/off, at both offsets
(`scratch/deadman3d-opt/hires_answer_pads.py`) — fail at **row 107**. The column
wanders with the pad (59..82); the row never moves. Row 107 is the two-tier
adapter's own top row. The collapse's `store->cpu` leg is a rigid three-segment
L, `[(tout_x, tout_y+1), (tout_x, resp_row), (CX+W+2, resp_row)]`, which assumes
the collector's south exit is *below* `resp_row`. On `deadman-3d` it is. On
hires it is **above**, so the same two corners describe a *climb* — the pipe
draws `^` — and the climb goes straight through the adapter. Nothing the store
can do moves that row: it is a CPU coordinate.

Going further west does not help either; past `dx -21` the failure changes into
the store block colliding with the CPU and starts tracking `dx`
(`(77,148)`, `(73,151)`, `(63,151)`, `(43,151)` at -21/-25/-30/-40/-60).

**2. Route around the adapter, and the collector is on the response row.** The
generator already owns the right instrument — the `compact or moved` branch
routes this leg with `constrained_route` instead of a fixed polyline. Trying the
straight leg first and falling back to a routed one clears row 107 (and keeps
`deadman-3d` byte-identical by construction, since its straight leg is clear).
What it exposes is the real constraint: hires' `resp_row` (104) sits **inside**
the widened collector's own row band (102..105), so the collector's west wall
lands on `CX+W+3` — precisely the cell the response pipe must occupy to enter
the CPU at `CX+W+2` from the east.

**3. Park the wall further east, and there is no route at all.** Freeing that
approach cell (a tunable gap in place of the hard-coded `+4`) removes the
forced-step failure and produces `no free route` at every gap from 5 to 12 —
**with the search box opened to the entire grid**, so it is the machine and not
the box. The attachment is enclosed: CPU to the west, collector to the east,
adapter below, and `_keepout`'s halo forbids running alongside any of them,
because two pipes in adjacent cells read as one and fail silently.

### So which room cannot be absorbed

**Neither of the two the collapse deletes — the collector itself.** `L` and `U`
are droppable; what cannot be done is put the collector *beside the CPU* and
*clear of its response row* at the same time, because on this machine those are
the same rows. On `deadman-3d` the CPU is taller and the response row is below
the block, which is the whole reason the collapse works there.

The exploratory generator changes (a routed fallback for the collapsed leg, a
`clear()` dry run on `_Grid`, and a tunable collector gap) were **reverted**:
they were correct and they are what produced the diagnosis above, but with the
collapse unplaceable no machine uses them, and an untested branch on the shared
build path is exactly the dead config this log has declined six times already.
`deadman-3d`'s grids were verified byte-identical at every step (`f62d63fd…`,
`a11edcc6…`).

---

# H3 — the three program levers from the 64x48 machine: two transfer, one does not

`worktree-compactor` was merged forward mid-session (`b5c6c8e`), bringing the
levers that took `deadman-3d` taped to 611M. All three are keyword-gated on
`deadman3d_source` and all three default to `False`, so **hires was unchanged by
the merge** and H1's baseline still described the shipped machine. They reach
hires through `hires_source()` — the one place this family's program knobs live,
which is what makes `TIER_PROGRAM`'s inability to key it a non-issue.

Same 21-round tour, frames 1..20, on top of the shipped store set:

| | walk ticks | vs shipped | |
|---|---:|---:|---|
| shipped (H1) | 1,036,196,396 | — | |
| `+ lap_via_jump` | 1,036,259,288 | **+0.006%** | declined |
| `+ dda_diff` | 997,775,049 | -3.708% | |
| `+ dda_diff + lap_via_jump` | 997,922,772 | -3.694% | |
| **`+ dda_diff + dda_stepy_split`** | **990,990,612** | **-4.362%** | **taken** |
| `+ all three` | 991,090,332 | -4.353% | |

**`lap_via_jump` is the one that did not transfer**, and it is worth -4.47% on
the 64x48 machine. It is a wash here in every combination — +0.006% alone,
+0.015pp when added to `dda_diff`, +0.010pp when added to both. Declined: it
costs 10-16 words of ROM for nothing measurable. Worth a note for whoever tries
it again — the lever's value is the ring recirculation a backward branch causes
per lap, and hires' ring is a different size against a much larger `P`, so the
per-lap saving does not scale the way the DDA levers do.

`dda_stepy_split` **cannot be taken alone at this geometry** — the second
emission collides on label `dda0` at hires' unroll factor. With `dda_diff` the
labels are distinct and it assembles, which is the combination `deadman-3d`
ships anyway, so this is a note rather than a limitation.

Cost: 514x348, up from 500x348 (`P` 8,863 -> 9,225). Fourteen columns for
4.36% is the trade `AGENTS.md` explicitly calls free for this family.

## The revised baseline

Same engine and method as H1 (native `fast_littleman`, `frame_tiles=(2, 2)`,
round-gated, `passed=True` on all 21 frames), on the 514x348 machine:

| frame | cost | | frame | cost | | frame | cost | | frame | cost |
|---:|---:|---|---:|---:|---|---:|---:|---|---:|---:|
| 0 | *37,108,356* | | 6 | 40,264,278 | | 11 | 63,393,702 | | 16 | 41,148,070 |
| 1 | 49,726,883 | | 7 | 43,830,154 | | 12 | 61,635,001 | | 17 | 47,179,438 |
| 2 | 49,027,604 | | 8 | 47,320,694 | | 13 | 58,425,729 | | 18 | 50,785,989 |
| 3 | 44,299,060 | | 9 | 49,517,866 | | 14 | 53,416,177 | | 19 | 49,117,682 |
| 4 | 44,152,339 | | 10 | 66,524,902 | | 15 | 46,743,991 | | 20 | 41,139,646 |
| 5 | 43,341,407 | | | | | | | | | |

**Frames 1..20: 990,990,612 ticks, mean 49,549,530 a frame** — the hi-res
baseline. Frame 0 is boot plus the title RLE and is not comparable. Total for
the 21-round tour 1,028,098,968.

Against H1's 1,036,196,396 that is -4.362%; against where this family started
the session (1,090,194,166 at `b78eafc`) it is **-9.100%**. The split is worth
keeping in view: the three store registries are 0.755pp of that and the three
program levers are 8.4pp. **Program beats placement on this machine by an order
of magnitude**, and the reason is the one H1 already gave — at 128x96 the frame
is four times the pixels against the same 902-slot store, so every store leg is
a quarter of the share it has on `deadman-3d` while every per-ray instruction is
worth four times as much. The next lever for hires is in the DOOM unit or the
raycaster, not the store block.
## M17 — the DOOM unit's dead columns: the ceiling first, then the -0.19%

The user, reading the block: *"in the stream units — too wide columns, and there
are DEAD columns from what I see."* They were right about the columns, and the
first job was to find out whether being right about the columns meant anything.

### The ceiling, measured before anything was built

The unit is a **write-only coprocessor**: the CPU sends a command and walks on,
and the paint loops run concurrently with the next raycast. So unit-internal
savings convert to tour ticks only where the CPU is actually *waiting* on it,
and `scratch/doom_pipes.py --rounds 8` says exactly how much that is:

```
critical path — the CPU is the only man on it:
  blocked on store:collector->cpu   16,614,151  40.44% of the run
  blocked on rom->cpu                  778,817   1.90%
  blocked on cpu->stream:unit          412,345   1.00%   <- the whole ceiling
  blocked on input->cpu                 36,748   0.09%
```

**1.00%**, up from the 0.72% M14 recorded only because the CPU has got faster
since. 1,742 commands over those eight rounds, so 237 ticks of CPU stall per
command — and whatever the unit saves per command comes off that 237 or off
nothing. This number is the reason the entry below is worth 0.19% and not more,
and it was known before a line of geometry moved.

### What the gaps were: a fixed pitch set by the widest arm

The same shape as `SLAB_PITCH`. The trie's eight leaves sat at
`LEAF0 + LEAF_PITCH*i` with the pitch at 20, and the pitch is set by the
**widest** arm — GUNF's 33-column Freedoom sprite chain, which needs two leaves
to hold it. Every other arm got 20 columns whether it wanted them or not:

| arm | real span | granted | traffic |
|---|---:|---:|---:|
| COMMIT | 1 | 20 | 0.55% |
| GUN | 23 | 40 | 0.48% |
| CURS | 1 | 20 | 17.4% |
| GUNF | 33 | 40 | 0.07% |
| RUN | 4 | 20 | 46.6% |
| COL | 11 | 14 | 34.9% |

**73 of 156 interior columns carry a cell at all**, and the two 20-column gaps
the user saw are leaves 2 and 5, which no arm reaches into (the sprite chains
ride over the near end of each). The trie walked straight through both.

And the pitch was **stale**: the docstring justified it by the V3 HUD arm's
serpentine field, which lived between RUN and COL. V4 replaced that arm with
CURS + RUN and the field went with it. Nothing had needed the pitch since.

### What the walk actually costs

Dispatch is one man: east from MAIN to the trie root, across three trie rows to
his leaf, down the arm, and then **the whole way back west** along the collector
row to column 1. That is roughly `2 x leaf_column`, on every command — 291 cells
for a COL word at leaf 143, against a 237-tick stall. It is the same quantity
the collector row has always been, but nobody had priced it against the pitch.

`COMPACT_LEAF_COLS = (3, 7, 27, 33, 37, 41, 73, 79)` gives each arm its own
width. `trie_nodes()` derives every internal column as the midpoint of its two
children (two cells a side is the floor — the branch writes `x` and then `]`,
the shift that advances the decode), so the leaves become a table rather than a
pitch. Interior **156 -> 92 columns**, block **235x101 -> 171x101**.

**No code moves.** `arm_codes()` already read the codes off the leaves' *rank* —
a west branch is a set bit — so re-spacing hands back the same dict,
`store.DoomUnit.CODES` and the same `.equ C_*`. That is what makes this a
re-spacing rather than a rebuild, and it is checked rather than argued.

### The east wall has to travel with the leaves

The one trap, and it is rule 2's. Every deep `r` must bind its ring return
rather than the `cmd` pipe, and it wins by `(east wall - x)` against
`(x - CMD_COL) + depth`. Compacting the arms westward while leaving the wall at
156 moves **both** terms the wrong way: COL's `r` would sit 86 cells from the
wall and 70 from `cmd`, and the arm would read the command word instead of its
own ring. Bringing the wall in restores the margin exactly (43), and `Cols.of()`
now does for the block's east side what `Rows.of()` does for its rows — relays,
panel and the three port pipes are all `EAST` plus a fixed offset, so all four
pipe lengths are unchanged and `build_doom`'s three assertions re-check it.

### What could NOT be done, and why it is the bigger number

The traffic is **inverted against the layout**: RUN 46.6% and COL 34.9%, the two
easternmost arms, pay the longest walk; GUN + GUNF carry 0.55% of words between
them and sit in the west. Ordering by traffic would be worth more than the
re-spacing. It cannot be had:

* **COL is pinned to leaf 7** — its code is 0, which is what makes the CPU's
  per-column send a bare `MULI 8`, and its loops spill ten columns east.
* **RUN is pinned to an eastern leaf** — its literal-free `/16` parks the
  argument in ring 1 and takes it back with an `r`, and rule 2 only lets an `r`
  beat `cmd`'s north-wall distance from the far east. At leaf 1 that `r` binds
  `cmd` and the arm reads the wrong word.

So the gaps are free and the order is not. (Merging GUN and GUNF was measured
and declined earlier for the same family of reason: it works and pays zero.)

### Measured

The probe is the unit alone — 18 commands, every arm, a negative-seed COL, the
banding masks, both sprites — so its step count is the unit's own service time,
dispatch walk included (`scratch/deadman3d-opt/unit_leaf_gate.py`):

| | steps | |
|---|---:|---:|
| shipped pitch, `loop_row` 27 | 45,447 | |
| **compact**, `loop_row` 27 | **43,507** | **-4.27%** |
| shipped pitch, `loop_row` 10 | 44,054 | |
| **compact**, `loop_row` 10 | **42,114** | **-4.40%** |

108 ticks a command, against the 237 the CPU stalls. On the 116-round tour:

| | ticks | box |
|---|---:|---|
| M16 | 611,021,810 | 293x254 |
| **M17** | **609,871,597** | 293x254 |
| | **-1,150,213 = -0.188%** | |

**The box does not move**: the block is 64 columns narrower and the machine is
not, because the taped store owns the width floor. The earlier finding that the
unit's east edge sits ~50 columns inside the machine still holds after the store
shrank — re-checked here rather than inherited.

`deadman-3d_hires`, 9 rounds, both built from scratch and round-gated
(`scratch/deadman3d-opt/hires_leaf.py`):

| | ticks | box |
|---|---:|---|
| pitch | 367,069,477 | 500x348 |
| **compact** | **367,029,420** | 490x348 |
| | **-0.011%** | |

Which is the honest answer for that family: hires is 68% blocked on its store
and parks on `cpu->stream` for 0.0003% of its run, so four blocks' worth of
shorter dispatch converts to nothing. Ten columns and a rounding error.

### Gates

`tests/test_deadman3d.py -m slow` **12/12** — every composed frame
byte-identical. Full fast suite 2,788 passed, 68 skipped, so no other slug on
`d3_unit` moved. Opt-in through `machine.DOOM_LEAF_COLS`, keyed exactly like
`DOOM_LOOP_ROW` and only on the taped tier: `deadman-3d.man` / `_trim` / `_v2`
still `f62d63fd…`, `deadman-3d.input.txt` still `654d35d6…`,
`deadman-3d_taped.man` `a11edcc6…` -> **`684e26e7…`**.

### The lesson, which is worth more than the 0.19%

The user called the dead columns *"more aesthetics"* halfway through, and the
measurement agrees with them: a write-only coprocessor's internal savings are
capped by how long its client waits on it, and here that cap was 1.00% before
anything started. Report the ceiling first. This one was worth taking because
the compaction turned out to be a re-spacing that moved no codes, no rows and no
pipe lengths — had it needed a re-order it would have been the wrong trade at
these prices.
# M17 — declined: rebuilding the decode trie out of `a`/`d` instead of `x`

# M18 — declined: rebuilding the decode trie out of `a`/`d` instead of `x`

**Nothing was built.** `machine.py` is untouched and every hash is where M17 left
it: `deadman-3d.man` / `_trim` / `_v2` `f62d63fd…`, `deadman-3d.input.txt`
`654d35d6…`, `deadman-3d_taped.man` `684e26e7…`. Two read-only probes were added
(`scratch/trie_probe.py`, `scratch/gap_probe.py`); they build the grid and walk
it, they do not change it. The tick figures below were taken on the `a11edcc6…`
taped grid, whose CPU room M17 did not touch — the trie and the lane band are
cell-for-cell the same, so the analysis carries.

The idea was that `x` "always turns" and so forces the CPU's lanes apart, while
`a`/`d` go **straight** on one side and would close the gaps — potentially
halving a trie walk that is 5,270,998 ticks, 8.74% of the run.

## 1. An `a`/`d` decoder is not a trie. It is a caterpillar

`x` turns on **BP's low bit**; `a`/`d` turn on **BP > 0**. The gap between those
two predicates is not a constant factor, it is a change of shape, and the reason
is that BP is a **one-way register**.

BP has exactly four writers (`SPEC.md`): `b` (BP = A), `q` (BP = pipe count),
`m` (BP −= 1) and `]` (BP >>= 1, arithmetic). Inside the decode only `m` and `]`
are available — after the fetch `>rbr`, **A holds the operand, not the opcode**,
so `b` has nothing useful to reload from. And both `m` and `]` map `BP <= 0` to
`BP <= 0`: `m` only decreases, and an arithmetic `]` on a negative value
converges to −1 and never crosses zero.

So **once BP <= 0 it can never become > 0 again**, and every `a`/`d` below that
point goes straight, forever. The straight branch is *terminal*. A decoder built
from `a`/`d` therefore cannot branch twice on the same path in both directions:
turn = continue, straight = leaf. That is a **caterpillar** — a linear chain
with a **unary** code, not a binary trie. 22 opcodes need 21 test sites, not
`ceil(log2 22) = 5` levels.

### Why "bring the tested bit to the sign" cannot be priced down

The obvious repair is to make bit *k* the sign at each level. It does not exist:

* `]`×j then a test gives `opcode >= 2^j` — a **threshold**, never "bit k is
  set". Thresholds are fine in principle (a binary search over 22 leaves is 5
  deep), but the shift is **destructive and cumulative along the path**: after
  `]`×j the `BP <= 0` branch holds only values in `{−1, 0}` and every remaining
  bit of the opcode is gone. The subtree under it cannot be built.
* The only way to get the bits back is to rewrite BP, and the only sources are
  `b` (needs the opcode back in A) and `q` (a pipe count). Getting the opcode
  back into A costs **a second ROM word plus an `r` plus a `b`, per level**.

That is the arithmetic that ends it. The fetch is `>rbr` — **4 ticks flat** — and
the whole trie it would replace is **26–34 ticks**. Paying one extra ROM read per
level is 5 extra ROM words per instruction against a 30-tick budget, before
counting the drum traffic (`rom->cpu` stall is already 2.79% of the run in
`scratch/DOOM-OPCODES.md`). The trie is cheaper than its own re-fetch.

## 2. The gaps are trie fan-out, not `_SLAB_PITCH` — and they are load-bearing

Measured on the built grid (`scratch/gap_probe.py`). The CPU lane band is rows
**100–142**; lanes sit on the **even** rows, the odd rows are the gaps. The pitch
of 2 is hard-coded — `row_of = {m: y0 + 2 * rank[...]}` and
`all_rows = [y0 + 2*i ...]` (`machine.py:1123`, `:1132`) — and `_uneven_trie`
consumes the odd rows (`xrow = slot_rows[min(down)] - 1`, `machine.py:991`).

Three facts from the census, all of which cut against the obvious reading:

1. **`_SLAB_PITCH` has nothing to do with it.** `_SLAB_PITCH = 13` is a *column*
   pitch for the structures band **below** the collector row. It does not touch
   the lane rows.
2. **The trie and the lane bodies never compete for a cell.** Every trie cell is
   in columns **13–21**; `lane_x0 = 22` and every micro-program starts there.
   Trie cells already appear *on lane rows* — row 104 carries trie `.` at 17,
   `.` at 19 and its entry `>` at 21 before ADDI's body starts at 22. So the gap
   rows are not there to keep the trie out of the lanes' way.
3. **What the gap row is actually for is the `x` node itself.** `x` fans out
   perpendicular on *both* sides, so a node needs its own row to sit on with a
   clear vertical run to each child. Nothing else is on the gap rows inside the
   CPU: no micro-program cell, no pipe glyph, no slab cell — only trie cells in
   13–21 and the drop columns passing through (which cross lane rows too).

**The two-sided fan-out is not pure waste.** It is what lets the fetch row sit in
the *middle* of the band: 11 lanes above row 121 and 11 below, so the riser is
**22 = half** the 43-row band. Any glyph that leaves the row in only one
direction puts every leaf on one side of the fetch row and the riser becomes the
**whole** band height. That is the hidden bill on the `a`/`d` idea, and it is
paid by every instruction: +22 ticks, 3.85M, 6.4% of the run.

## 3. What the gaps would be worth if they could go — the number to chase

Stage totals are measured (`scratch/DOOM-OPCODES.md`, and `trie_probe.py`
reproduces the trie line to the tick: **5,270,998**, mean 30.09). Every trie path
walks columns 13 → 22, so 9 × 175,153 = **1,576,377** of it is horizontal and
**3,694,621** (mean 21.1) is vertical travel inside the band.

Halving the band — 22 rows at pitch 1, rank order unchanged — halves every
vertical term at once:

| term | today | pitch 1 | saved | % run |
|---|---:|---:|---:|---:|
| trie, vertical component | 3,694,621 | 1,847,311 | 1,847,310 | 3.06% |
| drop (`collector − 1 − row`) | 3,301,798 | 1,650,899 | 1,650,899 | 2.74% |
| riser (22 flat → 12 flat) | 3,853,366 | 2,101,836 | 1,751,530 | 2.90% |
| **total** | | | **5,249,740** | **8.70%** |

**~8.7% of the run sits in the band's height, and none of it needs `a`/`d`.**
This is a derivative, not a measurement, and it is an upper bound: it assumes the
trie still routes and every pipe still binds. The known blockers are all binding,
not geometry — the store response pipe needs a **4-row** gap on the east wall or
the engine reads a spurious second adapter→CPU pipe (`machine.py:1731`), the
display/stream pitches are documented as only having to "exceed the 2-row lane
gap" (`machine.py:1159`), and `LANE_ORDER`'s own comment records that a
bottom-fill already failed binding once. That is where the work is.

## 4. The caterpillar, priced, so nobody re-derives it

For the record, the best `a`/`d` shape found. One `x` at the root picks the side
(so the fetch row stays centred and the riser stays 22), then a `d`-staircase
south and an `a`-staircase north, 11 leaves each. A step is `d` → `m` → `a`:
**3 ticks, 2 rows, 1 column**, and the turn at the second glyph is deterministic
because BP has not changed since the first. Decode cost is **4 + 3i** for rank
`i` on its side, entering the lane at column **16 + i**; opcode numbering becomes
`2*rank + side`, which `plan` would have to derive instead of `OPCODE_SLOTS`'
bit-reversal.

Against today's `t` (per-opcode, from `trie_probe.py`), and with `LANE_ORDER`
frozen — which already happens to put `LD` at south rank 0 and `ST` at north
rank 0, the two hottest opcodes:

* a lane with a MEM band pads out to `mem_x` anyway, so its extra columns are
  free: **Δ = 2i + 10 − t**, summing to **−2,049k**;
* a lane without one carries its drop column east with it and pays the return
  bus: **Δ = 4i − 2 − t**, summing to **−581k** (the immediates win, the deep
  structured lanes `JMPF`/`SND` lose).

**Predicted −2,630k ticks, −4.36% of the run.** Paper only — not built, not
measured, and it does *not* collect the 8.70% above, because each staircase step
is still 2 rows and the gaps survive.

It buys exactly one thing: the trie's vertical **zigzag**, and that quantity is
worth pricing on its own because it is the honest size of this whole direction.
Sum `|121 − row|` over the run — the vertical a decoder would walk if it went
straight at its leaf — and it is **1,107,405** ticks, mean 6.3. The trie actually
walks **3,694,621**, mean 21.1. **The zigzag is 2,587,216 ticks, 4.29% of the
run**, and it is not waste that better tuning removes: separating 22 rows in 5
balanced levels *forces* a level-1 step of ~11 rows, so every path pays the band
half-height whatever its leaf. That is the same fact `DOOM-OPCODES.md` records as
"the trie is rank-independent", priced as a cost instead of as a ceiling. It also
corroborates §4 from the other side: 4.29% against the caterpillar's 4.36%.

### The one thing `a`/`d` genuinely buys, and it is not the gaps

A caterpillar can be built from **`x` alone** — let one side of each node be the
leaf and the other continue, `x` → `>` → `]` → `x`, 3 ticks a step, the same
shape. Monotone descent is not an `a`/`d` property. What `a`/`d` uniquely gives
is the **encoding**: stepping with `m` makes the rank the opcode's *value*
(0–10, two digits on the drum), while stepping with `]` makes it a *bit
position*, so an `x` caterpillar needs `opcode = 2^i − 1` — up to 2,097,151, a
**seven-digit** opcode on every instruction the drum emits. Against
`OPCODE_SLOTS`, whose entire job is keeping opcodes under 10, that is
disqualifying. So if a caterpillar is ever wanted, it wants `a`/`d`; but the
caterpillar is worth 4.3% and the band height is worth 8.7%, and only the second
is reachable without re-deriving the ROM.

If dispatch is picked up again, **§3 is the lever, not §4.**
## M14 — "draw in runs" (the `plotter` idea): **declined, and here is the arithmetic**

**Nothing was built. `d3_unit.py` is untouched, no arm was added, and every hash
pin is where it was:** `deadman-3d.man` / `_trim` / `_v2` `f62d63fd…`,
`_taped.man` `6a739e1c…`, `deadman-3d.input.txt` `654d35d6…`. DOOM set **128
passed**, pixel gate **12/12**. Tour baseline for every percentage below:
**293x253, 683,820,497 ticks, `passed=True`** (`tour6.py - - -`).

### The proposal

> "We have the `plotter` implementation in the task set. In DOOM we have a lot of
> same-coloured pixels, especially vertically — so instead of drawing
> pixel-per-pixel, draw with runs. It could speed up drawing ~10% or more."

### The finding, first

**The painter is not on the critical path — at either resolution — and the
drawing is already in runs, in fact already in whole columns.** The premise the
idea rests on ("we draw pixel per pixel") is false for the 83% of the frame that
is the 3D viewport, and where per-pixel drawing does survive it is worth 1.7% at
its absolute ceiling. The three concrete forms of the idea cost, respectively,
**more than they save**, **0.28%**, and **≤1.7%**.

The CPU parks on the painter for **0.72%** of the run at 64x48 and — the case
that could have reopened this — for **0.0003%** at 128x96, where it is instead
parked on the store for **68%**. Hi-res does not invert the balance; it buries
it, because each of the four units still paints one 64x48 panel while the CPU
takes twice as long to feed it.

### 1. Where the frame's pixels go, and what they cost in command words

`scratch/doom_words.py` (new) censuses the unit's command stream on the
emulator — exact for the program, since it records the value of every `SND` —
and attributes each word to the label that emitted it. Over the checked-in
116-round tour (115 raycast frames plus the title):

| what it covers | pixels/frame | command words/frame | px per word |
|---|---:|---:|---:|
| **3D viewport**, rows 0–39 — walls, floor, ceiling | **2,560 (83%)** | **71.8 `COL`** | **35.7** |
| HUD strip, rows 40–47 — bar art, bars, mugshot, floor tints | 512 (17%) | 69.6 `RUN` + 15.9 `CURS` | 6.0 |
| **monster sprites**, overpainting the viewport | **14.5** | **14.5 `RUN` + 14.5 `CURS`** | **0.5** |
| gun sprite, frame boundary | (baked in the unit) | 0.9 `GUN`/`GUNF` + 1.0 `COMMIT` | — |
| **total** | **3,072** | **188.4** | 16.3 |

Read the first row again: **one command word paints a whole viewport column.**
`COL`'s argument is the unit's own loop seed, and the unit paints the wall run
*and then* the floor run from it (`deadman3d.py`, the `send:` block); the ceiling
costs nothing at all because `COMMIT` cleared the next buffer to black. 64
columns → 64 words → 2,560 pixels. The 7.8 extra `COL`s a frame are M5's nukage
flood repainting a floor run in green.

The third row is the whole proposal, isolated. It is the only per-pixel painting
left in the machine — a `CURS` + `RUN(1 px)` pair per opaque nibble, half a pixel
per word — and it covers **14.5 pixels a frame, 0.5% of the frame**.

### 2. What the CPU pays to *produce* those words

The unit is a write-only coprocessor that paints concurrently with the next
raycast, so the only painter cost on the critical path is (a) the CPU's own
arithmetic to build a word and (b) the CPU standing on `cpu->stream:unit`.
Instruction census over the tour, priced at `DOOM-OPCODES.md` §2's measured
per-opcode means (the pricing totals 794.3M against the native 683.8M, so it runs
~16% high and every percentage in this section is *of the estimate*, which makes
it conservative in the idea's favour):

| | instrs/frame | est ticks | % est run |
|---|---:|---:|---:|
| `send:` + `colnxt:` — building the 71.8 `COL` words | 1,257 | 40,228,779 | **5.1%** |
| sprite chain, all of it (`mbody` … `csk*`) | 1,592 | 52,823,355 | 6.7% |
| — of which the **per-pixel nibble walk** (`chain_h*` + `csk*`) | 415 | **13,673,402** | **1.7%** |
| HUD word emission | 76 | 127,080 | 0.02% |

The HUD line is the surprise and it matters: the HUD is **61% of all unit
traffic** and costs the CPU **nothing**, because its words are `LDI <const>` /
`SND` pairs constant-folded at assembly time. Word count is not cost here.

And the blocking, **re-measured on today's machine** rather than quoted from
`DOOM-PROFILE.md` (`scratch/doom_painter.py`, new; gated `WALK[:8]`,
`passed=True`, 48,455,100 ticks, wait sampled every 64):

| the CPU is parked on | ticks | % run |
|---|---:|---:|
| `store:collector->cpu` | 18,638,784 | **38.47%** |
| `rom->cpu` | 1,208,128 | 2.49% |
| **`cpu->stream:unit`** — the painter | **349,952** | **0.72%** |
| `input->cpu` | 34,688 | 0.07% |
| blocked, all pipes | 20,231,552 | 41.75% |
| walking his own dispatch | 28,223,548 | 58.25% |

The painter figure has moved, and the direction is worth understanding: the
profile's 0.55% was 340,824 ticks of 61.5M. Today it is 349,952 ticks of
48.5M — **the absolute painter block is unchanged (+2.7%) and the run around it
got 21% shorter**, so the fraction rose. The painter is a fixed cost that M12/M13
have made relatively larger by removing store latency around it. It is still
**under three quarters of one percent**, and `SND` in total — all 190.5 words a
frame of it — is 0.79% of the run, of which that block is nearly all.
**A painter that finished instantly would buy 0.72%.**

**The census is cross-checked against the native profiler.** `DOOM-OPCODES.md`
counted **175,153** instructions in its nine gated frames on the *pre-M13*
program; this census counts **169,671** on the same nine frames of the post-M13
taped program, and M13 deleted **5,536** executions of one instruction —
175,153 − 5,536 = 169,617, which is 54 instructions (0.03%) from what the
emulator counts. Two independent engines agree on the instruction stream, which
is what licenses pricing it. What they do *not* agree on is the price: the
per-opcode means are from that pre-M13 machine and are therefore ~4.4% stale on
top of the 16% the totals already differ by. Everything here is an upper bound.

### 3. The three concrete forms of the idea, each costed

#### 3a. A horizontal run merging identical adjacent columns — **net negative**

This is the strongest version: DOOM columns repeat, so paint *k* neighbouring
columns from one word. Decoding all 8,256 `COL` words of the tour back into
`(x, drawStart, count, colour)`:

* **4,855 of 8,256 (58.8%)** are pixel-identical to their left neighbour;
* mean run length **2.43 columns**.

That is a real 58.8%, and it is still not worth having:

```
saved   4,855 merged words x 3,779 est ticks to build a COL word  =  18.35M
```
```
cost    the merge must be decided BEFORE the word is built, so the
        comparison is paid on all 7,360 columns, not just the merged ones.
        The ISA has no compare: the cheapest sound test is two scalars
        (drawStart and colour — drawEnd is not a function of drawStart once
        the clip bites), i.e. 2 x (LD 470.9 + SUB 412.9 + BRZ 348.7)
        + 2 x ST 132 = 2,729 ticks
        7,360 x 2,729                                              =  20.08M
```

**Net −1.7M ticks — a loss — before a single cell of the new unit arm exists.**
Even the *unsound* floor (one scalar, no bookkeeping, wrong pixels at every
shade boundary) is 9.07M spent against 18.35M saved: 9.3M, **1.2% of the
estimate**, and it would also need the CPU to buffer a column and defer its send
until the run breaks, which adds a branch and a spill the model above does not
charge for. This is the version the proposal literally describes, and it does not
survive its own bookkeeping.

The reason is structural and worth stating plainly: **the CPU cannot skip the
raycast for a merged column.** It has to cast the column to discover the column
is a duplicate. Merging removes the *send*, which is 5.1%, and never touches the
DDA, which is most of the rest.

#### 3b. A run primitive carrying its own start position (deleting `CURS`) — **0.28% ceiling**

`CURS` is 30.5 words a frame, 16.2% of unit traffic, and pure cursor positioning.
If a run word carried its own address, all 3,504 of them over the tour would go.
Priced at what they actually cost the CPU:

| where | count | the CPU's cost each | total |
|---|---:|---:|---:|
| sprite chain (`LD ADDRV` / `SND`) | 1,664 | 743.5 | 1.24M |
| HUD, bars, floor tints (`LDI <const>` / `SND`) | 1,840 | 358.7 | 0.66M |
| | | | **1.90M = 0.28%** |

And 0.28% is the *ceiling*, reached only if the address becomes free. It does
not: the merged word still has to carry it, so the sprite side keeps its
`LD ADDRV` and gains a `MULI`/`ADD` to pack it — leaving roughly **0.1%** for a
new arm and a wider word encoding.

#### 3c. RLE sprites instead of nibble-per-pixel — **1.7% ceiling, the honest one**

The sprite chain is the only genuinely per-pixel painter left, and it is the one
place the proposal's diagnosis is correct. Its nibble walk (`chain_h5` 0.92M,
`chain_h9` 0.51M, `chain_h14` 0.32M, `csk0`…`csk13` 11.93M) is **13.67M est
ticks = 1.7%**, unpacking one nibble and advancing one row at a time whether the
pixel is opaque or transparent. An RLE encoding would skip transparent spans —
but it still has to advance the cursor, and the walk's other half (`mbody` alone
is 21.4M) is per-monster projection that no drawing primitive touches. **≤1.7%,
realistically well under half of it**, for a re-encoding of the sprite tables and
a new arm.

### 4. Hi-res, which was the one case that could have inverted the balance

128x96 is 4x the pixels across four concurrent units, so the balance was worth
re-deriving rather than assuming. Command-word census on the hires program
(`scratch/doom_words_hires.py`, IWAD-only, 20 rounds):

| | words/frame | px/frame | CPU instrs/frame |
|---|---:|---:|---:|
| 64x48 | 188.4 | 3,072 | 20,854 |
| **128x96** | **1,277.2** | 12,288 | **43,009** |
| ratio | **6.8x** | 4x | **2.06x** |

The word rate is where a hires argument for the idea would have to live, and it
does look promising for about one paragraph: through the **serial** stream the
rate rises **3.3x** (6.8x the words over 2.06x the frame time). If the per-word
block held at 64x48's ~195 ticks, hires' backpressure would land near 1.8% — the
only number in this investigation that would clear a percent.

Where the extra words go says why it does not: `COL` only goes 71.8 → 281.5 a
frame (the viewport is 80 rows against a 48-row tile, so *every* column crosses
the seam and costs one word per panel — 128 x 2 = 256, plus 25.5 of nukage
flood), while **`RUN` goes 84.1 → 729.4 and `CURS` 30.5 → 265.4**. That is the
HUD and the floor tints scaling with area at ~2.8 px a word — and it is exactly
the traffic that costs the CPU nothing to produce, because it is constant-folded.

#### Measured, and it is not close

`scratch/doom_painter.py hires 20 300000000`, on a 500x348 machine built from
the IWAD. Hires cannot be frame-gated (the display judge wants exactly one
display and there are four), so the run is ungated and **cut off inside the demo
at a tick budget** — which is also the strictest case, because an ungated CPU
never parks at `IN` and therefore hands the units work as fast as it ever will:

| the CPU is parked on | 64x48 (gated, 48.5M ticks) | **128x96 (ungated, 300M ticks)** |
|---|---:|---:|
| `store:collector->cpu` | 38.47% | **68.05%** |
| `rom->cpu` | 2.49% | 1.56% |
| **`cpu->stream:router`** — the painter | **0.72%** (349,952 t) | **0.0003%** (1,024 t) |
| blocked, all pipes | 41.75% | 69.62% |
| walking his own dispatch | 58.25% | 30.38% |

**Hi-res does not invert the balance — it buries it.** The painter is waited on
for **one thousand ticks in three hundred million**. And the reason is the first
bullet, not the second: the four units are each still a 64x48 LM-75 painting its
own ~3,072 pixels exactly as before, while the CPU now takes twice as long to
produce a frame and spends **68%** of it parked on the store — which is when the
router drains. Per unit, per unit of CPU time, hires asks the painter for
**0.83x** the work 64x48 does.

**Checked as a derivative, so it is not a title-screen artefact.** The title
frame is 4,004 command words, an order of magnitude more than any other, so a
single cut-off run could have been dominated by it. Cutting at 100M instead of
300M gives 192 ticks of painter block; the difference over the [100M, 300M)
window is **832 ticks in 200,000,000 = 0.0004%**, i.e. the steady state is the
same as the average. (The 100M point also shows the store at 57.00% climbing to
68.05% — the machine is genuinely rendering across the whole window.)

### 5. Verdict

**The painter is not on the critical path at either resolution, and the drawing
is already done in runs.** The best of the three concrete forms is 0.28% at its
ceiling on a family whose store is 38–68% of the run. Invest in memory and CPU.

Three things this cost, recorded so the next attempt does not re-pay them:

* `prof.wait` counts **samples, not ticks**. `doom_pipes.py` divides by the tick
  total directly, which is right only at stride 1, where the two coincide. At
  stride 64 the first cut of every figure above was 64x low.
* An **ungated run that reaches the end of its input does not stop** — the CPU
  parks on `IN` and the engine keeps ticking to the cap. A large `max_ticks`
  then measures the idle tail: the first 64x48 attempt reported "98.44% walking
  his own dispatch" and a painter block of 0.000%, both of which were the tail.
* The first bucketing of the instruction census caught the DDA's `hity{k}` arm
  joins on a `"hit"` prefix and reported **13.4% of the run as "monster
  painting"**. The real sprite chain is 6.7% and its per-pixel part is 1.7%.
  A label-prefix bucket is a guess until the labels are read.

# H2 — `stream:router`: the seam, and whether the demux is on the critical path

Two questions about the same room, and they turn out to have opposite answers.
The seam between the four panels is worth real work; the router's own width is
worth **58 ticks a frame**, and here is the measurement that says so.

## Is the router on the critical path? No — and by five orders of magnitude

The router is a one-man room the CPU's single `SND` lane feeds and four DOOM
units hang off, so its trie walk is charged to a frame **only if the CPU blocks
sending into it**. `FastLittleman.run(profile=True)` counts that exactly:
`FastProfile.send_blocked` is per pipe and not sampled.
`scratch/deadman3d-opt/router_load.py`, four rounds, 180.7M ticks:

| | before | after |
|---|---:|---:|
| command words a frame (`cpu->router` sends) | 2,544 | 2,544 |
| **times the CPU parked sending one** | **0** | **0** |
| router man: ticks in the room | 100% | 100% |
| ...of which blocked on `r` | 99.5% | 99.8% |
| router **working** (walking), share of run | 0.492% | 0.155% |
| **ticks of walk per command word** | **116.4** | **36.8** |

Zero parks in 180.7 million ticks. The room is idle waiting on `r` for 99.5% of
the run, and its walk overlaps the CPU's next instruction entirely — the CPU
takes ~20,000 ticks per command word and the router ~116. So the compaction's
whole recoverable cost is **tail latency**: the last COMMIT of a frame still has
to cross the room before the slowest panel commits, and that is what the frame
stamp measures.

Predicted saving: one walk's worth, ~80 ticks a frame. Measured: **58**. The
ceiling — if the CPU had been perfectly serialised behind the room, which the
zero parks say it was not — would have been `2,544 x 79.6 = 202,000` a frame,
0.39%. It is not.

## What the width was made of, and what was actually holding it

`LEAF_PITCH = 12` with eight leaves is 84 columns of fan, and the walk crosses
it twice: east to `TRIE_COL` (42 cells), down the trie (`2P + P + P/2` = 42),
then west along the collector to column 1 (up to 50). The docstring claimed the
pitch only had to "exceed twice the leaf row's distance to the south wall"; the
real binding floor is **1** — the outlets sit directly under the leaves, so
`_check_router`'s margin *is* the pitch.

What actually forced 12 was the **leg fan**, in an arrangement the wall no longer
has. With all four blocks west of the cluster the two north legs shared one lane
row, which needs T1's outlet east of T0's command port at column 33 — i.e.
`LEAF0 + 3 * LEAF_PITCH > 33`, so `P > 10`. With the blocks back either side of
the cluster every leg has its own lane, and the only surviving constraint is
`LEAF0 + 2 * LEAF_PITCH < 33`. Pitch 2, `LEAF0` 4 (the trie entry has to clear
the `M8W/WbW` unpack, which fills columns 1..10).

```
 >@rM8W/WbWv                >@rM8W/WbW                          v
       v  ]x]  v                             v                      ]x]
     v]x]v   v]x]v              v          ]x]          v            ...
    vxv vxv vxv vxv       v    ]x]    v           v    ]x]    v      ...
    s s s s S             s           s           s           s           S
 ^<<<<<<<<<<<            ^<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< ...
   21x8  (new)                        90x8  (old)
```

Room **92x10 -> 23x10**. `SEL` is read off the trie by :func:`leaf_codes` and is
pitch-independent, so the emitted `.asm` and the input stream hash unchanged.

## The seam: `gy` 6 -> 3, and why it decides the arrangement

The band between the two panel rows had drifted to six rows. It was not chosen:
the subsystem sweep that moved the cluster east of all four blocks scored on the
wall's **bounding box**, and a wider band shortened the leg fan enough to win on
that objective. The bounding box is not scored for this family, and the seam is
what the demo is a picture *of*.

`gy = 3` cannot be had in the east-of-all-four arrangement. Feeding the cluster
from one side means SE's ADDR, DATA and SWAP and SW's ADDR all enter the band as
eastbound lanes — four pipes turning south at four different columns, four rows.
Three of the four cannot be lifted out: SW's ADDR reaches the shared channel from
*below* the south blocks (row 188, the first free row under the block) and has to
leave it *into* the band (row ~168), so its channel span `[168, 188]` is **nested
inside** the span of every SE net that runs past the cluster — and a nested pair
has no west-of order at all (`_pack_order`; `ok(p,q)` fails in both directions).
Escaping that needs all three SE nets to come down the panel gutter from above,
i.e. five gutter columns, which widens the seam the other way. So the blocks go
back either side of the cluster, each panel is fed from the block beside it, and
the band carries only its four arrowheads.

## Where H1's baseline lands

Same 21-round tour, `hires_gate2.py 21`, native round-gated, `passed=True`,
frames 1..20 differenced.

| | machine | wall | ticks a frame | vs H1 |
|---|---|---|---:|---:|
| H1 baseline (`50277ab`) | 500x348 | 499x223 | 51,809,819 | — |
| seam back to 3 rows | 494x447 | 493x305 | 51,810,172 | **+0.00068%** |
| **+ router 92x10 -> 23x10** | 494x447 | 493x305 | **51,810,114** | **+0.00057%** |

The seam costs +353 a frame — the leg fan goes 9/166/123/280 -> 14/308/237/531
and the last COMMIT has further to travel. The router compaction gives 58 of it
back. Net **+295 ticks on 51.8 million**, five and a half thousandths of a
percent, for a seam that is half as wide.

Verified on the real engine at every step: all 27 composed frames byte-identical
to the H1 build and `.asm` `4fa5e682…` / `.input.txt` `847a0caf…` unmoved;
`packed_probe.py` drives both walls with the same 25 commands and the composed
128x96 images are identical, non-blank, all four seam corners painted;
`lm.mjs analyze` finds four 64x48 displays in reading order with **3 rows and 2
columns** between their wall boxes (`269..318`, `322..371`; `(188,253)`,
`(256,321)`); a 1M-tick run is `fatal: None`. The four leaf `s` glyphs bind pipes
whose destinations are NW/NE/SW/SE in tile order and `S` binds all four
(`FastLittleman._bindings`, the same nearest-pipe rule the engine uses).
`deadman-3d.man` / `_trim` / `_v2` `f62d63fd…`, `_taped.man` `a11edcc6…`,
`deadman-3d.input.txt` `654d35d6…`.

---

# M19 — the lane band's gap rows: what they are, and the floor under them

The lane band is **43 rows** carrying **21 lanes**. The user's reading of it was
that the gaps are slack the `x` trie leaves behind, and that a leg feeding a
later `>]x` could be shortened to close them. **Half of that is exactly right,
and the half that is wrong is the half that sets the floor.** The band came out
at 43 rows and it stays at 43; what the investigation produced instead is a
*measured* floor of 30, a priced ceiling of ~5%, and a permanent guard against
the silent failure that finding it exposed.

`machine.py` gained the mechanism (`lane_pitch`, `LANE_PITCH`) and an invariant
check; every grid is byte-identical. `deadman-3d.man` / `_trim` / `_v2`
`f62d63fd…`, `deadman-3d.input.txt` `654d35d6…`, `deadman-3d_taped.man`
`684e26e7…`. DOOM fast tier **135 passed**, the pixel gate **12/12**, and the
whole fast suite **2,788 passed / 68 skipped** — every other slug's hash pin
holds, which is the check that matters when `build_cpu` is the file being
touched.

## 1. What the band's depth is made of — and what it is not

Measured on the built grid (`scratch/gap_probe.py`, `scratch/cpu_pitch.py`):

| | |
|---|---|
| lane rows | 100, 102, … 142 — even, pitch 2, one hole at 134 where an opcode was retired |
| gap rows | 101, 103, … 141 — **odd, and every one carries exactly one `x` node** |
| what else is on a gap row | nothing. No micro-program cell, no pipe glyph, no slab cell. Only trie cells in columns 13–21, plus drop columns passing through — and those cross lane rows too |
| `_SLAB_PITCH` | **irrelevant.** It is 13 *columns* for the structures band **below** the collector. It does not touch a lane row |

So the depth is **trie fan-out geometry and nothing else**: 21 lanes + 20 `x`
nodes, one per row, 41 occupied rows in a 43-row band. It is already a perfect
1:1 packing. The gaps are not slack that squaring-off left behind — they are
the nodes.

## 2. The rule: right about internal legs, wrong about leaf legs

**Right, and it is a real structural fact.** A node at trie level `L` sits in
column `3 + 2L`, and *every* lane in its subtree is entered from a node at level
`> L`, so at column `>= 5 + 2L`. A node's column — and both its legs' column —
is therefore **strictly west of every lane entry beneath it**. A lane's man
starts east of it and never walks onto it, and a leg never lands inside a lane's
shift run. **A node can share a lane's row**, and a leg feeding a later `>]x` can
be shortened exactly as claimed.

**Wrong for a leg that feeds a lane, and that is the binding case.** `x` **always
turns** — clockwise is south, counter-clockwise is north, and there is no third
outcome that leaves the man on his own row. So a node's row must lie **strictly
between its two children's rows**. A node whose two children are both lanes — a
**cherry** — therefore needs a row between two *adjacent* lanes. No lane row can
serve it. That row is irreducible.

**This program's trie has 9 cherries** (`scratch/trie_embed.py`; 20 nodes, by
level 1/2/4/6/7).

### The proof, and why it had to be on the grid

`lane_pitch=1` was built and walked. The trie emitted **12 of its 20 `x` nodes** —
**exactly 9 lost**, each overwritten by its own up-lane's entry `>` at the same
cell. Every opcode routed through a lost node then walks east into the **wrong
lane**: no binding error, no collision, no crash. A pixel diff hundreds of frames
later is the only symptom.

That is now impossible to ship by accident. `_uneven_trie` records every branch
cell and re-checks it after laying the tree; a squeezed band raises instead of
emitting a decoder:

```
9 decode branch(es) overwritten at [(11, 12), (13, 1), (13, 3), (13, 5)]...:
an `x` needs a row strictly between its two children, so a node with two lane
children needs a row between two adjacent lanes.
```

## 3. The floor, and what it is worth

Leaves keep their rows; every non-cherry node can share one; every cherry cannot.

**Floor = 21 lanes + 9 cherries = 30 rows, against 43 today — 13 removable.**
Not 21. The band was never going to halve.

Priced on the current grid (`scratch/pitch_probe.py`; the trie walk reproduces
the profile's stage to the tick):

| stage | today | % run | at the 30-row floor | saved | % run |
|---|---:|---:|---:|---:|---:|
| trie descent | 5,234,638 | 8.68% | — of which 1,576,377 is horizontal and fixed | 1,105k | 1.83% |
| drop to the collector | 3,292,144 | 5.46% | scales with the band | 995k | 1.65% |
| riser (22 flat) | 3,826,702 | 6.34% | ~15 flat | 1,173k | 1.94% |
| **total** | **12,353,484** | **20.48%** | | **~3,273k** | **~5.42%** |

**~5% is the ceiling**, and it assumes *perfect* sharing — every one of the 11
non-cherry nodes finding a lane row that also satisfies betweenness and column
order. Feasibility only takes bites out of it.

## 4. Why it was not taken

Two costs, and the second is the one that decided it.

* **A new embedder.** `_uneven_trie` derives a node's row from its own subtree
  (`slot_rows[min(down)] - 1`). Sharing is a global assignment — which node takes
  which lane row — under betweenness *and* the column-order precondition. That is
  a different algorithm, not a parameter.
* **The room does not shrink where it stands.** With the CPU 20 rows shorter,
  `store_offset` dy was swept over **−30..+30** and **every value failed**
  (`scratch/pitch_sweep.py`). The failures are structural rather than marginal,
  and they name four different neighbours: block collisions at
  (63,106)/(65,106)/(105,86), `taped block collision at (4,5)`, `request roof is
  not above the gate strip`, and `no clear band below the store for the seek
  teleport`. The store block, the adapter, the gate strip and the seek teleport
  are all placed against the tall room, so this is not one offset being stale —
  it is a simultaneous re-tune of several placement registries.

  This is a **one-dimensional** sweep and therefore a lower bound on the work,
  not a proof of impossibility; a wider `(rom_rows, store_dy, mem_dy)` sweep was
  left running and had found nothing when this was written. A 13-row shrink is
  the same problem with a smaller number. That is the price against ~5%, on a
  decoder whose failure mode is silent.

The mechanism is left in place and inert — `LANE_PITCH` is empty, `lane_pitch`
defaults to 2, and every grid is byte-identical — so whoever picks this up starts
from the guard rather than from the bug.

---

# M20 — the band staggers: 43 rows to 31, **-4.04%**

M19 read the band's floor as 30 rows and did not take it, on two grounds that
both turned out to be soft. This takes it. **Tour 609,871,597 -> 585,257,450,
-4.04%, box 293x254 unchanged.** `deadman-3d.man` / `_trim` / `_v2` `f62d63fd…`,
input `654d35d6…`, taped moves to `51d356d6…`. DOOM tier **139 passed**, pixel
gate **12/12**.

## What the rule turned out to be

A gap row is owed to a node **only when its up half is a single lane**. Nine of
this trie's twenty nodes. The other eleven share the lane row above them, and
they can because of a column invariant that holds structurally:

> a node at trie level `L` sits in column `3 + 2L`, and every lane in its subtree
> is entered from a node at level `> L`, hence at column `>= 5 + 2L`. A node's
> column — and both its legs' column — is therefore strictly **west** of every
> lane entry beneath it.

So the lane's man begins east of the node and never walks onto it, and no leg
ever lands inside a lane's shift run. That is the user's rule, and it is right.

The nine that cannot share are forced by `x` itself: it **always turns**, so a
node's row must lie strictly *between* its two children's rows, and a node whose
up child is a lane needs a row between two **adjacent** lanes. `_uneven_gaps`
computes that set. **`_uneven_trie` needed no change at all** — its
`slot_rows[min(down)] - 1` already places a node correctly once the leaves are
spaced right, which is the tell that the gap was in the *spacing* and never in
the trie.

## Bottom-aligning it is what made it shippable

M19's blocker was that the shorter room does not place. It does not have to be
shorter. The eleven saved rows are left **blank above the band**, so the
collector, the whole structures band below it and the room's own height stay
exactly where they were — and so does every block placed against them.

This costs nothing that matters, because all three winnings measure from the
collector: the drop is `collector - 1 - row`, the riser is `collector - centre`,
and the trie descent is the band's own height. The fetch row simply moves from
121 to 127 and the band ends where it always did.

**Shrinking the room as well was built and measured, and rejected.** With the
CPU 12 rows shorter the machine is 293x242 and the tour is **579,543,849,
-4.97%** — 0.93% better. It needs the STORE to follow it north (`store_offset`
dy -5; the window is -10..-5, closed at -4 by the seek teleport's "no clear band
below the store"). That extra 0.93% is real and still on the table, but the
offset that fits the shipped grid does not fit a counterfactual build with a
differently shaped store block, and chasing it with a fallback search cost three
minutes a test run and still left two pins failing. Bottom-aligning gets 81% of
the win for none of that.

## Measured

Nine-frame profile case, stages walked on the emitted cells
(`scratch/pitch_probe.py`) — the win is near-perfect thirds, which is what you
expect when the thing removed is the band's height and all three terms are
vertical travel inside it:

| stage | pitch 2 | staggered | saved | % run |
|---|---:|---:|---:|---:|
| trie descent | 5,234,638 | 4,183,452 | 1,051,186 | **1.74%** |
| drop to the collector | 3,292,144 | 2,261,706 | 1,030,438 | **1.71%** |
| riser (22 flat -> 16 flat) | 3,826,702 | 2,783,056 | 1,043,646 | **1.73%** |
| **total** | **12,353,484** | **9,228,214** | **3,125,270** | **5.18%** |

| | tour | vs baseline |
|---|---:|---:|
| M19 baseline | 609,871,597 | — |
| **M20, staggered band** | **585,257,450** | **-4.04%** |
| (shrunk room, not taken) | 579,543,849 | -4.97% |

## Correctness

A mis-decode is silent — the wrong lane runs and the grid still loads — so this
is checked three ways rather than trusted. The branch-overwrite guard added in
M19 does **not** fire on the staggered band (it fires on a uniform one-row band,
losing all nine at once, which is pinned as a test). The decoder is walked on the
emitted cells and every opcode reaches a *distinct* lane row. And the pixel gate
is 12/12.

## What is left here

* **0.93%** in shrinking the room, if the store's placement is made to follow the
  CPU's height rather than be pinned against it.
* The floor is **30** rows and this reaches **31** — one row, because the band is
  laid from a single pass rather than by matching each node to the best of the
  two lane rows it may share. Worth ~0.16%, and not worth a solver.

# M21 — the slot map shapes the decode trie too, **-2.080%**

**Numbering note.** This finding was written as "M18" in commit `b4f94fd` and in
the `OPCODE_SLOTS` / `SEEK_TIER_LAYOUT` docstrings, because it and the `a`/`d`
decline (the real M18, above) were developed in parallel from the same base and
both reached for the next free number. It is **M21**; the docstrings still say
M18 and that is the only place the old number survives. See `AGENTS.md`
§"Optimisation work" — milestone numbers come from the integration branch, not
from the base you branched off.

`OPCODE_SLOTS`' docstring called it "a ROM-encoding knob and nothing else", and
its DP scored only the drum's opcode digits. But `_uneven_trie` splits the slot
*space* at each dyadic midpoint, so the slot values choose every branch of the
decode trie — and how many fall in `[0,16)` **is** the root's in-order position.
Writing the dispatch loop out, with the collector at `C`, the root at `F` and a
lane at `r`:

    cost(r) = 2C - 2*min(r, F) - 1 + zigzag(r)

Below the root the descent and the drop telescope, so the live quantities are the
**zigzag** and the **riser** (`collector - root`). Both are set by the trie's
shape. It was a two-objective problem being solved on one objective, and the two
are in real tension — the dispatch optimum alone costs 9,098 opcode cells against
the drum DP's 6,557.

| map | cells | dispatch | fold | box | tour ticks | |
|---|---:|---:|---:|---|---:|---:|
| drum DP (was shipped) | 6,557 | 12,426,204 | 81 | 293x254 | 609,871,597 | |
| cheapest drum that helps | 6,617 | 11,747,422 | 81 | 293x254 | 602,765,896 | -1.165% |
| dispatch-only optimum | 9,098 | **11,167,796** | 90 | 293x262 | 598,773,720 | -1.820% |
| **joint (shipped)** | 7,631 | **11,167,796** | 84 | 293x257 | **597,185,956** | **-2.080%** |

## Its optimum is stale, and deliberately not re-run here

This was searched against a **43-row lane band**. M20 then staggered that band to
**31 rows**, which moves the riser (22 -> 16 flat) — one of the two terms the
search minimises. The shipped map is therefore optimal for a machine that no
longer exists, and the two findings are **not additive**: both were measured from
the same 609,871,597 baseline, so neither number survives the other landing.

What was done instead of assuming: the artifacts were regenerated from the merged
builder rather than taking either side of the merge conflict, giving
`deadman-3d_taped.man` = `1bc5e791…` (M21 alone was `16d77f35…`, M20 alone
`51d356d6…`). Re-running `band_root_probe.search_joint` against the 31-row band is
the open item.
## The seek drum on `deadman-3d_hires` — **-36.04%**, the largest single win

`SEEK_DRUM` had never contained the hires slug. Not a considered decline: the set
has had exactly two values in its whole history, `set()` at `994b4f1` and
`{"deadman-3d"}` at `886ea07`, and there was no entry in this file either. Nobody
had measured it.

Harness `hires_seek.py` (build / lit / fold / run modes); `build_for` takes an
explicit `seek=`, so the whole sweep ran before a registry moved. Native
`fast_littleman`, 21-round tour, `frame_tiles=(2, 2)`, `frame_ticks[-1] -
frame_ticks[0]`, every row `fatal is None and passed is True`. Baseline is
`add1e25`'s classic hires machine, **365,333,921 ticks at 514x451**.

| build | box | `mem_pad` | tour ticks | vs baseline |
|---|---|---:|---:|---:|
| classic (baseline) | 514x451 | 15 | 365,333,921 | — |
| seek alone, fold 120 | 531x497 | 35 | 258,146,477 | -29.35% |
| + `SEEK_SLAB_PITCH` 11 | 517x496 | 18 | 236,380,143 | -35.30% |
| + `INPUT_NORTH_WEST` 13 / `MEM_PAD_FOR` 15 | 517x496 | 15 | 234,324,256 | -35.86% |
| + `SEEK_TAKEN_DROP_EAST` | 517x496 | 15 | 233,851,301 | -35.99% |
| + `SEEK_TELEPORT` (**shipped**) | 517x496 | 15 | **233,658,800** | **-36.04%** |

Each row is the shipped build with exactly one registry knocked back off, so
every entry is proved load-bearing rather than assumed.

### Why it is worth three times the parent's -11.0%

Scale, and it is the one part of this that transfers as an argument rather than
as a number. A taken long jump discards `2 * ((t - k - 1) mod n)` words of the
ROM man's lap, so the classic drum's cost per long jump is linear in `P` while
the seek ladder's is logarithmic in the fold. hires is P=9,225 against
`deadman-3d`'s ~4,300.

### Nothing else transferred, and one registry inverted

* **The fold is a different feasible region, not a re-pick.** `ROM_ROWS`' 88 does
  not build under the drum at all — a seek row addresses its words as
  `row*K + offset` with `K = 128`, and at 88 rows the first row holds 152. The
  fold has to *deepen*, the opposite direction to the classic width/height trade.
  110 is the shallowest that builds; **111 the shallowest that runs** — 110 and
  121..123 build and then fail `FastLittleman` with "numeric literal does not fit
  signed 64 bits", the reverse-reading hazard, landing in a different place than
  it does on `deadman-3d`. Box is 517 wide and pad 15 across the whole range, so
  the fold is a pure tick pick: 111 235,095,528 / 115 234,590,527 / 118
  234,768,727 / **119 233,658,800** / 120 234,200,405 / 124 235,121,255 / 128
  234,237,503 / 130 234,888,187. Span 0.63%, non-monotone — a pin, not a
  crossing.
* **`SEEK_SLAB_PITCH` inverted sign against its own docstring**, and is the
  second-largest thing here at **-8.85%**. `SLAB_PITCH` declines hires twice on
  the grounds that its width is the router wall's, not its CPU's, so there is
  nothing to trade CPU columns into. True, and the wrong question: what the pitch
  buys on a seek hires is the `mem_pad` **floor**. Pitch 13 -> 35, pitch 12 ->
  28, pitch 11 -> 18, and `INPUT_NORTH_WEST` takes it to 15 — which is where the
  *classic* build already binds. Twenty columns of memory band, walked twice by
  every memory instruction. On `deadman-3d` the same knob pushes the pad the
  other way (a narrower CPU is a closer rival to `mem_resp`); the rival that
  binds is simply not the same one on the two machines.
* **The slot map needed one name added.** A seek build grows a 22nd lane and
  `OPCODE_SLOTS[("deadman-3d_hires", "taped")]` is a 21-lane DP solution, so
  `build_for(..., seek=True)` failed outright with "opcode slot map does not name
  the used opcodes ['JMPS']". The DP was **not** re-run: the twenty-one
  assignments are untouched and `JMPS` takes a free slot in the only gap rank
  preservation leaves it, `JMPF`(24)..`SND`(28). All three candidates 25/26/27
  bit-reverse to a two-digit opcode (same 345 drum cells) and the 21-round tour
  is identical to the tick at all three, so the trie cannot separate them either.
  Naming it is inert for the classic build — `_relabel_slots` filters names the
  build does not use — which is what that function's docstring is for.

### What was left alone

`ROM_BUFFER` stays empty: it is antagonistic to seeking by construction
(`ROM-RECIRCULATION.md` §170) — the buffer's value is draining a pre-filled queue
during the discard loop, and seeking deletes the loop. `TIGHT_STRUCT_DROPS` can
never fire under seek (`not seek and name in ...`), so hires is not added to it.
`SEEK_OPS` stays `("JMPF",)`: splitting `BRZ`/`BRN` would need two further lanes
named in the map, and the parent's own table has it as a loss.
`TAPED_BANKS` / `TAPED_BANK_ORDER` untouched.

`deadman-3d.man` / `_trim` / `_v2` `f62d63fd…`, `deadman-3d.input.txt`
`654d35d6…`, `_taped.man` `16d77f35…`, all unmoved: every registry entry is keyed
to the hires slug or to `("deadman-3d_hires", "taped")`.
