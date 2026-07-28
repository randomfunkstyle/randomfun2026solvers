# M7 — Monsters for deadman-3d: design document

Produced by the planning agent against the M6 state (Freedoom E1M1, 307×307
men-v3 machine, 304×216 taped variant). Implementation is queued behind the
M5-lite landing (nukage + health bar + HUD face + local WAD art overrides).

## 0. Architecture decision (the one big choice)

Two candidate architectures were evaluated for occluded billboards:

**A. Unit-side sprites** — new painter-unit arms holding baked sprite RLE plus
a depth compare. Rejected for the depth part: the unit has no addressable
memory (its state is two tiny value-ring FIFOs, A/B registers and BP), so a
64-entry z-buffer cannot live there, and a per-column depth compare in the
unit would need the CPU to ship the depth anyway. Also the trie has only 2
spare leaves (codes 2 and 5), and per-scale sprite arms would blow past them.

**B. CPU-side sprite pass over a store-resident z-buffer** — the raycaster
already computes `perpWallDist` per column (scalar `PERP`, currently
overwritten each column and kept nowhere); we persist it into a 64-slot `ZBUF`
in the men-v3 store, then run a sprite phase after the 64-column loop, painting
monster columns through the **existing** `CURS`+`RUN` unit commands (per-pixel,
vertical strips). **Chosen.** It requires *zero* unit changes for M7a, keeps
the hot DDA/column loop untouched, and matches the repo's "big data rides the
store, art rides the input preamble" doctrine. A one-word-per-pixel `VRUN`
unit arm is an optional M7b optimization (spare leaf 5), cutting sprite paint
cost ~30%.

Sprite scaling: **2–3 baked scale bands** (recommendation: 3), pre-quantized
at import time, nibble-packed one word per sprite column — *not* per-column
texture stepping. This removes all per-pixel MUL/DIV (band size == screen
size, 1:1 pixel walk with the same `POW16` nibble machinery the map uses).

## 1. Q1 — the z-buffer

`perpWallDist` today: computed into scalar `PERP` (slot table `_SCALARS` in
`deadman3d.py`), consumed by `pclip/nearck/lineh`, then clobbered by the next
column. It is not kept anywhere; the unit receives only the packed COL word
(top/bot/colour), never the depth.

**Design: `ZBUF`, 64 consecutive store slots.** In the asm's column tail,
immediately after `pclip`/`nearck` fix `PERP` (i.e. right before `lineh:`),
insert:

```
        LD  XCOL
        ADDI ZB          ; ZB = ZBUF base .equ
        MOVA PERP        ; store[ZB + XCOL] = PERP  (MOVA: store[ACC] = store[addr])
```

3 instructions × 64 columns ≈ 200 instructions/frame (~0.03M ticks) —
negligible. No initialization needed: all 64 slots are written every frame
before the sprite phase reads any. Golden model: `render()` already computes
`perpWallDist` per column; collect it into a local `zbuf` list in the same
clamped form (`PERP >= 1`, post-clamp value — the *exact* value the asm
stores).

## 2. Q2 — sprite projection, integer Q10 lowering

Standard lodev sprite pipeline, lowered with the repo's `div`/`sign_mod` only.
Per frame prologue (once):

```
DET = PLANEX*DIRY − DIRX*PLANEY        # Q20, ≈ +692,224 for every heading
```

`DET > 0` for all 16 headings because plane is dir rotated −90° — **assert
this over all headings in the golden model** (new test). Per monster at Q10
world position `(mx, my)` (cell centre: `mx = cx*1024 + 512`):

```
MDX = mx − POSX                       # Q10, |·| < 2^16
MDY = my − POSY
TYN = PLANEX*MDY − PLANEY*MDX         # Q20 camera depth numerator (MUL; MUL; SUB)
if TYN <= 0: cull                     # behind the plane
TXN = DIRY*MDX − DIRX*MDY             # Q20 camera x numerator
TY  = div(TYN * 1024, DET)            # Q10 depth — SAME units as PERP/ZBUF (MULI 1024; DIV DET)
if TY < NEAR_CULL (1024): cull        # player inside the monster
if TY − FAR_D >= 0: cull              # FAR_D = 12*1024 (walls go dark at NEAR_D=16 anyway)
SX  = 32 + div(32 * TXN, TYN)         # centre screen column; DETs cancel — one MUL, one DIV
SX0 = SX − div(W_band, 2);  SX1 = SX0 + W_band − 1
if SX1 < 0 or SX0 > 63: cull          # off screen
BOT = MID + div(div(81920, TY), 2)    # the floor line at depth TY == wall drawEnd there
if BOT > 39: BOT = 39                 # near clamp: sprite slides up, stays whole
TOP = BOT − H_band + 1                # never < 0: BOT >= 23 for TY < FAR_D, H_band <= 14
```

Overflow audit: `TYN*1024` ≤ 2^26·2^10 = 2^36; `32*TXN` ≤ 2^31 — all
comfortably inside a signed word. All `MODI` operands in the phase are
nonnegative (risk R9 pattern respected). Depth compare against walls is
`TY < ZBUF[x]` — both are Q10 perpendicular camera-space depths, directly
comparable; the golden model uses the identical expressions in identical
order (that ordering *is* the pixel contract).

## 3. Q3 — sprite scaling: baked bands (recommended)

Three bands, all heights ≤ 14 so **one word packs a whole sprite column**
(16 nibbles max; keep packed words < 2^63 by construction — heights ≤ 14
guarantee it):

| band | size (w×h) | chosen when | ≈ true height |
|---|---|---|---|
| 0 near | 10×14 | `TY < 3562` | ≥ ~11.5 rows |
| 1 mid | 6×9 | `TY < 5851` | ~7–11.5 |
| 2 far | 4×5 | `TY < 12288` | ~3.3–7 |

(Thresholds = `40960 // midpoint_height` with `MON_H_NUM = 40960` ≈ a
one-cell-tall monster against the 81920 two-cell wall; golden-model tunables
`BAND_T`.) Nibble packing: **bottom pixel = nibble 0**, colour `0` =
transparent (quantizer maps near-black opaque pixels to 8). Paint is a
bottom-up nibble walk: `c = Q % 16; Q //= 16` — MODI 16 / DIVI 16 per pixel,
no POW16 lookup, no MUL/DIV. Corpse frames are padded to the *same* band
dimensions (transparent top rows) so the paint chain needs no extra entry
points.

Per-column true texture stepping was costed (~+9 instr/pixel for the texY
MUL/DIV/POW16 chain, ~2–3M ticks worst frames) and rejected.

## 4. Q4 — visibility, culling, budget

- Monster table capped at `MAX_MON = 16` (E1M1 medium-skill monster count fits).
- Selection loop over all 16 each frame: unpack, `TYN>0`, `NEAR/FAR`,
  screen-span culls (§2) — ~40–60 instr per monster, ~800 instr (~0.1M ticks)
  constant.
- **At most 3 sprites drawn per frame**: a 3-slot insertion keeping the
  *nearest* three, stored **sorted far→first** (slot 0 = farthest); painting
  slots 0→1→2 is the painter's back-to-front order. Ties: earlier THINGS
  index wins (spell this in the golden model).

Tick budget (current frame ≈ 5.2–5.7M; avg ≈ 100–150 ticks/instruction;
every added ROM word taxes each of the ~130 backward-jump laps/frame by
8 ticks ⇒ ~1k ticks/frame per ROM word):

| item | cost/frame |
|---|---|
| ZBUF writes | ~0.03M |
| selection loop | ~0.1M |
| ROM growth ≈ 350–420 words (jump tax) | ~0.35–0.42M constant |
| paint, typical (1–2 mid-band sprites) | ~0.2M |
| paint, worst (3 near sprites, ~420 px, CURS+RUN) | ~0.9–1.2M |

Total: typical **+0.6–0.8M (~12%)**, worst **+1.6M (~30%)** — well inside the
2× ceiling. The M7b `VRUN` arm shaves ~0.3M off the worst case.

ROM discipline: the per-pixel paint is **one shared unrolled 14-block chain**
(each block: `LD Q; MODI 16; BRZ skip; …send CURS word from ADDRV; send RUN
word (colour*8+132); skip: LD Q; DIVI 16; ST Q; LD ADDRV; SUBI 64; ST ADDRV`
≈ 11–16 words) with **three static entry labels** `chain_h14/chain_h9/chain_h5`
(enter H blocks from the end; JMPF is skip-by-literal on this ISA, no computed
jumps, so entries are plain labels). ≈ 170–220 words instead of 3 per-band
copies.

## 5. Q5 — THINGS → grid, both art modes

`wadimport.rasterize` already parses THINGS and owns the `to_grid` transform.
Add to `Raster`/`Level`:

- Filter: `(flags & 2) != 0` (medium skill) and `(flags & 0x10) == 0` (not
  MP-only); type map `{3004: 0, 9: 0, 3001: 1}` (former humans → species 0 /
  POSS art, imps → species 1 / TROO art). Barrels/decorations: out of scope
  (one line to add later).
- Cell = `(int(gx), int(gy))` via the same `to_grid`; **drop** things whose
  cell is not in `open_cells` (report count in `stats`); dedupe cells; cap at
  16 (THINGS order).
- Emit `monsters: [[cx, cy, species], ...]` in `level.json` / the `Level`
  dataclass.

Sprite art (two-mode, following the pistol pattern):
- **Freedoom (committed)**: `root/sprites/possa1.png`, `root/sprites/trooa1.png`,
  a death frame (e.g. `possl0.png`) → `decode_png` → new
  `quantize_sprite(rgba, w, h)` — `quantize_title`'s block-Lab method plus
  alpha: a block whose source pixels are majority `a<128` → colour 0; opaque
  near-black → 8. Bake all three bands + padded corpse; the resulting nibble
  tables are **committed into `deadman3d.py`** as `MON_SPRITES` (like
  `GUN_IDLE` — generated data, checked in, WAD-free).
- **IWAD (local)**: `POSSA1`/`TROOA1`/`POSSL0` lumps via the existing
  `decode_picture` + `PLAYPAL`; `install_level()` grows `monsters=` and
  `sprites=` parameters and swaps module globals; outputs stay in
  `littleman/examples/local/`. No test touches the IWAD.

Golden model: monster world pos = cell centre `(cx*1024+512, cy*1024+512)`;
assert `map_cell(cx, cy) == 0` for every committed monster (import-time and
test-time).

## 6. Q6 — fire / hit test / state

- **State**: `MHP`, one mutable store slot per monster, boot-loaded from the
  preamble (initial HP; recommend 1 for species 0, 2 for species 1). `hp == 0`
  ⇒ corpse: still selected, painted with the corpse frame base, still
  z-tested, **never a hit candidate** — that is the whole of "stops occluding
  gameplay".
- **Live-shot gate**: new scalar `LIVE` set in the decode ladder exactly where
  `AMMO` decrements (dry fire never kills).
- **Hit test**: inside the per-slot paint loop, at column `x == 32` (the
  crosshair column), *after* the occlusion check `TY < ZBUF[32]` has passed:
  if `LIVE` and the slot carries `S_IDX ≠ 0` (alive monster index+1; corpses
  store 0), `ST` it into `HIT`. Because slots paint far→near, the last write
  wins ⇒ nearest visible monster. After the sprite phase (before `gun:`):
  `LD HIT; BRZ gun;` then `store[MHPB + HIT − 1] −= 1` via `LDA`/`MOVA`
  through `TMP`.
- **Timing semantics (define once, mirror everywhere)**: the hit is resolved
  with *this* frame's post-move geometry but applied to the monster list
  *after* this frame renders — the corpse appears from the next frame
  (DOOM-authentic and one-pass). Golden model: `frames_for_commands` computes
  `live`, renders, then applies `hit` returned by the render's sprite pass.

## 7. Q7 — test-ladder additions (`tests/test_deadman3d.py`)

1. **Model**: monster table pins (count, cells open, species legal); `DET > 0`
   for all 16 headings; sprite words round-trip (pack/extract, all < 2^63,
   heights ≤ 14); band thresholds monotone; selection ordering (hand-built
   4-monster scene → exact 3 slots, far-first).
2. **Pinned frames**: all existing pins (`SPAWN_FRAME`, `CAVERN_LOOK_FRAME`,
   fire-frame) **regenerate** (monsters change pixels); add two new pins — a
   monster half-occluded by a wall edge (per-column clip visible), and a kill:
   frame N shows the alive sprite + flash, frame N+1 the corpse and the
   monster no longer intercepting a second shot.
3. **Emulator equality**: the existing short/full-walk/fuzz tests inherit
   coverage automatically; add one targeted short run whose commands walk into
   view of a monster and fire twice.
4. **Native gate**: extend the round-gated `FastLittleman` test to include the
   first fire-at-monster round.
5. **Registry pins**: update `TAPE_SIZE`, `STORE_SHAPE`, `TAPED_BANKS`;
   machine dims move — assert the squareness/ceiling PROPERTIES only
   (per the no-exact-dimension-pins rule, commit a70da0f), not new numbers.
6. **Unit** (M7b only): pin `VRUN` in `arm_codes()`/`DoomUnit.CODES`; extend
   `test_doom_unit_probe_paints_like_the_model` with VRUN words.
7. **Artifact families**: `deadman-3d.asm/.man/.cases.json/.input.txt/_taped.*`
   all regenerate; re-sync the `_v2`/`_m6_taped` byte-copy families or freeze
   them out of the equality tests, and add the `_m7` family.
   `test_input_txt_is_the_flattened_cases_input` guards the grown preamble.
8. **wadimport**: `quantize_sprite` + THINGS-monster extraction tested on the
   existing synthetic in-memory WAD pattern (no IWAD, no Freedoom checkout at
   test time for the fast tier).

## 8. Q8 — milestone split

**M7a — static occluded billboards** (independently demoable; *no unit change,
no new unit codes*):
1. `wadimport.py`: monster extraction + `quantize_sprite` + Level/emit
   plumbing (both modes).
2. `deadman3d.py` golden: `MONSTERS`/`MON_SPRITES` committed tables, `DET`
   assert, `zbuf` in `render`, selection + far→near paint pass (no fire
   logic), preamble/`tape_slots` growth, `install_level` extension.
3. Asm generator: ZBUF store, `DET` prologue, selection loop, 3-slot
   insertion, shared paint chain with CURS+RUN pairs.
4. `machine.py`: `TAPE_SIZE`, `STORE_SHAPE`/`rom_rows`/`store_dy` re-sweep for
   min max(w,h) (existing `scratch/deadman3d-opt` sweep tooling),
   `TAPED_BANKS` re-tuned to cover the new tape (ZBUF is hot — give it a
   cheap bank).
5. Tests 1–3, 5, 7, 8 + artifact regeneration.

**M7b — shootable** (demoable: shoot a zombieman):
1. Golden: `LIVE`, hit resolution, `MHP` threading in `frames_for_commands`,
   corpse frames.
2. Asm: hit candidacy at x==32, post-phase HP decrement.
3. Optional-if-cheap: `VRUN` arm on spare leaf 5 in `d3_unit.py` (COL's wall
   lap minus mask ring and floor interlude: `r`,+1024,push,`/16` split, ADDR,
   DATA, drain) + `store.DoomUnit` model + `.equ C_VRUN` + probe test; switch
   the paint chain from CURS+RUN pairs to one VRUN word/pixel.
4. Walk re-choreography if needed so a committed WALK shot visibly kills (all
   50 frames regenerate anyway); tests 2 (kill pins), 4, 6.

## Data layout (tape/preamble, exact)

Preamble slots 1..295 unchanged. Appended (still input-borne, never ROM):

```
MONB   296..296+N−1   packed monster: ((cx*64)+cy)*2 + species   (N ≤ 16)
MHPB   ..+N           initial HP per monster (mutable after boot)
SPRB   ..+~60         sprite nibble columns: species0 bands 10+6+4,
                      species1 20, corpse (padded) 20 — frame base offsets are .equ constants
ZBUF   64 slots       per-column wall depth (written each frame, no init)
scalars (+~30)        DET, MI, HIT, LIVE, MDX, MDY, TXN, TYN, Q, ADDRV, BAND,
                      per-slot k∈0..2: S_TY, S_SX0, S_SX1, S_BASE, S_TOP, S_IDX
```

New tape ≈ **515–530 slots** (from 330). Store shape candidates for the sweep:
(12,43)=516, (12,44)=528, (11,48)=528. Preamble grows ~+92 words (boot loop is
generated, adapts automatically); cases/input files regenerate.

## Risks

- **R-squareness**: 307×307 will not survive +190 store slots at (8,42); the
  joint rom_rows × store-shape × store_dy sweep must be re-run (documented
  method, `scratch/deadman3d-opt/METRICS.md`); expect a slightly larger
  square (~315±).
- **R-jump-tax**: every sprite-phase ROM word costs ~1k ticks/frame forever;
  the shared chain + literal discipline is mandatory, and the frame-1 native
  gate should be re-measured before/after.
- **R-order-drift**: the sprite pass must be written in the golden model in
  asm operation order first (selection order, tie rules, far-first slots,
  bottom-up nibble walk) — the fuzz test is the safety net.
- **R-art**: colour 0 doubles as transparent ⇒ monsters cannot paint black;
  quantizer maps to 8. Top-nibble ≤ 7 not needed since heights ≤ 14 keep
  words < 2^63.
- **R-walk**: real Freedoom THINGS may leave the committed walk's sightlines
  empty; verify with the PNG dump and re-choreograph (all pinned frames
  regenerate regardless).
- **R-taped**: `TAPED_BANKS (128,128,40,33)` covers only 329 slots — must
  grow, and ZBUF/slot scalars are hot traffic; re-tune on the native frame
  gate.

## Critical files

- `solvers/python/randomfun2026solvers/deadman3d.py`
- `solvers/python/randomfun2026solvers/wadimport.py`
- `solvers/python/randomfun2026solvers/lm1/machine.py`
- `tests/test_deadman3d.py`
- `solvers/python/randomfun2026solvers/lm1/d3_unit.py` (M7b VRUN only; with
  its emulator twin in `lm1/store.py`)
