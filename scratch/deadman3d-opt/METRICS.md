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
