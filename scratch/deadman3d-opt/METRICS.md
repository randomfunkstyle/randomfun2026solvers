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

## Iteration artifacts
`iter-00-baseline.man` (the c834fe0 machine), `iter-04-side-boot8-storedy.*`,
`iter-06-final.*` (= the checked-in `littleman/examples/deadman-3d.*`).
