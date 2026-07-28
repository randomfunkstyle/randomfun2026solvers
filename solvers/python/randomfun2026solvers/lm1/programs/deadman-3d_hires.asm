; deadman-3d — GENERATED from randomfun2026solvers/deadman3d.py, do not hand-edit.
; Regenerate with:
;   from randomfun2026solvers.deadman3d import deadman3d_source
;   from randomfun2026solvers.lm1.programs import PROGRAM_DIR
;   (PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())
;
; lodev.org's raycaster_flat.cpp on the LM-1: Freedoom Phase 1's E1M1 (real
; level geometry, imported from levels/e1m1.wad @ d14dbbe by wadimport.py),
; walked first person at 64x48 on the LM-75 — one frame per input
; word, and each word is a MUX of the keys held that frame: bit0 (1) W fwd,
; bit1 (2) S back, bit2 (4) A left, bit3 (8) D right, bit4 (16) space FIRE
; (muzzle-flash overlay); 0 idle, higher bits ignored. Turn first (A/D
; cancel), then move along the new heading (W/S cancel), then render.
; An ungraded demo — the slug borrows plotter's problem JSON for nothing
; but registration; its 64x48 panel belongs to the DOOM unit (.unit doom,
; lm1/d3_unit.py), its input is its own, and its 670-slot STORE rides the
; men-v3 man-memory (STORE_TIER), ~11 ticks an access.
;
; The CPU never touches the display: each viewport column is ONE command
; word to the write-only column-painter unit — 8*arg + code, code COL=0,
; arg = ((drawStart*64 + drawEnd)*64 + x)*16 + colour — and the unit paints
; the wall run and the floor run (stride 64) while the CPU raycasts the
; next column, seaming every 4th wall row via its mask ring. The pistol
; (GUN idle / GUNF recoil+flash), each cursor move (CURS), each RLE run
; (RUN) and COMMIT (SWAP 0) are one command word each; the ceiling stays
; black because COMMIT clears the next buffer.
;
; Round 0's input carries the whole data preamble (256 packed map quarter-columns,
; POW16, the 16 packed heading words, the 64-word nukage bit plane, the spawn
; state, the 16-monster table with its HP block and the 60 packed sprite
; columns — deadman3d.preamble_words())
; followed by the title screen's RLE (deadman3d.title_words(): one pre-encoded
; RUN command word per run, forwarded IN/SND and committed as round 0's one
; frame): tables and art ride on INPUT because every ROM word taxes every
; backward jump by 8 ticks forever. The pixel contract is deadman3d.render()
; (and TITLE_HEX_ROWS for the title): every expression below is that model's,
; in its exact operation order.
;
; The map-cell lookup floor(MAPW[4x + y/16] / 16**(y mod 16)) mod 16 is
; inlined at its three sites (no stack, no calls): the two move-collision
; tests and the DDA hit test.

; ── tape slots (deadman3d.tape_slots(); slots 1..451 are the boot data) ──────
.equ MAPB   1            ; ..256 packed map quarter-columns: word 4x+(y/16), nibble y mod 16
.equ POWB   257          ; ..272 16**k — the nibble-extraction divisors
.equ HDGB   273          ; ..288 packed headings: base-4096 digits dirX dirY planeX planeY, biased +1024
.equ NUKB   289          ; ..352 the nukage bit plane: word x, bit y — 1 = damage floor (M5)
.equ POSX   353          ; player x, Q10 (lodev posX)
.equ POSY   354          ; player y, Q10 (lodev posY)
.equ HDG    355          ; heading 0..15 (22.5 deg steps, CCW from east)
.equ DIRX   356          ; lodev dirX
.equ DIRY   357          ; lodev dirY
.equ PLANEX 358          ; lodev planeX
.equ PLANEY 359          ; lodev planeY
.equ MONB   360          ; ..375 monster table (M7a): ((cx*64)+cy)*2 + species
.equ MHPB   376          ; ..391 initial monster HP (M7b's hit ledger)
.equ SPRB   392          ; ..451 packed sprite columns: nibble 0 = bottom px, 0 = clear
.equ ZBUF   452          ; ..579 per-column wall depth, rewritten whole every frame
.equ CMD    580          ; this round's command word
.equ XCOL   581          ; the column being rendered (lodev x)
.equ CAMX   582          ; lodev cameraX, Q10
.equ RDX    583          ; lodev rayDirX
.equ RDY    584          ; lodev rayDirY
.equ SDX    585          ; lodev sideDistX
.equ SDY    586          ; lodev sideDistY
.equ DDX    587          ; lodev deltaDistX
.equ DDY    588          ; lodev deltaDistY
.equ S4X    589          ; 4*stepX: the word address moves +-4 per x-step
.equ STPY   590          ; lodev stepY (the sign picks the PW shift arm)
.equ PERP   591          ; lodev perpWallDist
.equ HALFH  592          ; lodev lineHeight / 2
.equ DSTART 593          ; lodev drawStart
.equ DEND   594          ; lodev drawEnd
.equ COLOR  595          ; the wall type t, then the shaded colour
.equ PW     596          ; 16**(mapY mod 16), maintained incrementally across DDA steps
.equ WADDR  597          ; MAPB + 4*mapX + mapY/16, maintained incrementally too
.equ FRACX  598          ; posX mod 1024, hoisted per frame
.equ FRACY  599          ; posY mod 1024
.equ PW0    600          ; PW's per-frame seed (the player's own cell)
.equ WADDR0 601          ; WADDR's per-frame seed
.equ TMP    602          ; scratch (s, frac, packed word)
.equ TMP2   603          ; scratch (the cell lookup's quarter-column selector)
.equ NEWX   604          ; the candidate posX
.equ NEWY   605          ; the candidate posY
.equ BW     606          ; key bit 0 (1): W, forward
.equ BS     607          ; key bit 1 (2): S, backward
.equ BA     608          ; key bit 2 (4): A, turn left
.equ BD     609          ; key bit 3 (8): D, turn right
.equ FIRE   610          ; key bit 4 (16): space held — fire the pistol this frame
.equ AMMO   611          ; live rounds left: starts 50, -1 per shot, floor 0
.equ HEALTH 612          ; live health: starts 100, nukage -5 a frame, floor 0
.equ NUKE   613          ; 1 when this frame stands on nukage: green floor, health drain
.equ LIVE   614          ; 1 when this frame's FIRE actually spent a round (M7b): a dry fire flashes but kills nothing
.equ HIT    615          ; the monster the crosshair (column 64) caught, index + 1; 0 = none
.equ DET    616          ; planeX*dirY - dirX*planeY (Q20) — the projection divisor, > 0
.equ MI     617          ; the selection loop's monster index
.equ MSP    618          ; the candidate's species (0 POSS / 1 TROO)
.equ MDX    619          ; monster cell centre - posX, Q10
.equ MDY    620          ; … - posY
.equ TXN    621          ; camera x numerator dirY*MDX - dirX*MDY (Q20)
.equ TYN    622          ; camera depth numerator planeX*MDY - planeY*MDX (Q20)
.equ CTY    623          ; the candidate's depth TY = TYN*1024/DET — ZBUF's own units
.equ CBAND  624          ; the candidate's scale band 0/1/2
.equ COFF   625          ; the band's column offset in the sprite stripe (0/10/16)
.equ CHW    626          ; the band's half width (5/3/2)
.equ CW1    627          ; the band's width - 1 (9/5/3)
.equ CSX0   628          ; the candidate's first screen column (may be < 0)
.equ CSX1   629          ; the candidate's last screen column (may be > 63)
.equ CBOT   630          ; the candidate's bottom row: the floor line at TY, clamped 39
.equ CBASE  631          ; SPRB + frame*20 + band offset: the column words' base slot
.equ CID    632          ; the candidate's hit id: monster index + 1, or 0 for a corpse
.equ STY0   633          ; slot 0 (farthest kept) depth; FAR = empty
.equ STY1   634          ; slot 1 depth
.equ STY2   635          ; slot 2 (nearest kept) depth
.equ SSX0   636          ; slot 0 first column
.equ SSX1   637          ; slot 1 first column
.equ SSX2   638          ; slot 2 first column
.equ SEX0   639          ; slot 0 last column
.equ SEX1   640          ; slot 1 last column
.equ SEX2   641          ; slot 2 last column
.equ SBA0   642          ; slot 0 sprite base
.equ SBA1   643          ; slot 1 sprite base
.equ SBA2   644          ; slot 2 sprite base
.equ SBO0   645          ; slot 0 bottom row
.equ SBO1   646          ; slot 1 bottom row
.equ SBO2   647          ; slot 2 bottom row
.equ SBN0   648          ; slot 0 band
.equ SBN1   649          ; slot 1 band
.equ SBN2   650          ; slot 2 band
.equ SID0   651          ; slot 0 hit id: monster index + 1, 0 = a corpse (M7b)
.equ SID1   652          ; slot 1 hit id
.equ SID2   653          ; slot 2 hit id
.equ SLOT   654          ; the paint loop's slot cursor 0..2 (far -> near)
.equ WTY    655          ; the painting slot's depth (the ZBUF compare term)
.equ WX     656          ; the painting column
.equ WX1    657          ; the painting slot's last column
.equ WPTR   658          ; the painting column's sprite word slot (BASE + column)
.equ WBOT   659          ; the painting slot's bottom row
.equ WBAND  660          ; the painting slot's band — picks the chain entry
.equ Q      661          ; the column's packed nibbles, shifted down as the chain climbs
.equ ADDRV  662          ; the pre-encoded CURS word of the pixel being painted
.equ PTR    663          ; the boot loop's tape cursor
.equ TXT    664          ; the column inside its panel: x mod 64
.equ TSELT  665          ; router selector for the panel above the seam at row 48
.equ TSELB  666          ; router selector for the panel below it
.equ TTE    667          ; the wall run's last row on the top panel (clipped at the seam)
.equ TBS    668          ; the wall run's first row on the bottom panel
.equ TBE    669          ; its last row; -1 when the wall ended above the seam

; ── the tiled wall (lm1/d3_router.py): four DOOM units behind a 1-of-4 ──────
; router. A command word is the unit's own 8*arg + code with the router's tile
; selector shifted in underneath it: 8*(8*arg + code) + sel. The panel is still
; 64x48 and so is every stride, bias and radix below —
; only which panel a word reaches is new.
.unit doom4
.equ C_GUN    27         ; the unit's baked pistol — unused at this geometry
.equ C_GUNF   51         ; ... nor its recoil frame (see _pistol_asm)
.equ C_RUN    4            ; arg=count*16+colour: count pixels at the panel's own cursor
.equ C_CURS   1            ; arg=addr: reposition the panel cursor (the RLE painter's ADDR)
.equ C_COMMIT 62           ; SWAP 0 on ALL four panels at once (the router's broadcast leaf)

; ── boot: round 0's data preamble -> tape slots 1..451, the loop unrolled 8x ──
; (a backward jump costs 8*(P - loop) ticks, so 56 laps beat 451; the last
; 3 slots are loaded straight-line at their own addresses)
        LDI 1
        ST  PTR
boot:   IN                  ; the next preamble word
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        IN
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        IN
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        IN
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        IN
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        IN
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        IN
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        IN
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        LD  PTR
        SUBI 449
        BRN boot            ; keep looping while PTR < 449
        IN
        ST  449
        IN
        ST  450
        IN
        ST  451


; ── title: Freedoom's title art (titlepic @ d14dbbe) — round 0's one frame ───
; The next 981 input words are PRE-ENCODED unit commands (title_words():
; one RUN word per RLE run of TITLE_HEX_ROWS, 8*(count*16 + colour) + C_RUN),
; so the CPU forwards each word untouched — IN; SND, 8 pairs per counted
; lap (122 laps + 5 straight-line pairs) — and the unit paints the runs at the panel's
; own auto-advancing cursor, concurrently. One COMMIT ends round 0.
        LDI 0
        ST  PTR             ; PTR now counts title laps
title:  IN                  ; the next pre-encoded RUN word
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        INCM PTR
        LD  PTR
        SUBI 122
        BRN title           ; keep looping while PTR < 122
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        IN
        SND
        LDI C_COMMIT
        SND                 ; commit: the title screen is round 0's frame
        LDI 50
        ST  AMMO            ; a full clip (V4's live HUD)
        LDI 100
        ST  HEALTH          ; full health — nukage drains it (M5)


; ── round: one key-bitmask word in, exactly one committed frame out ──────────
; The MUX decode: bits peeled low to high with a MODI 2 / DIVI 2 ladder, so
; every word — junk and high bits included — decodes exactly as the golden
; model's step() does.
round:  IN                  ; blocks here when the walk is over (the legal end)
        ST  CMD             ; ST preserves ACC
        LDI 0
        ST  LIVE            ; both cleared every frame: only a round actually
        ST  HIT             ; spent arms the shot, and HIT is this frame's
        LD  CMD
        MODI 2
        ST  BW              ; bit 0 (1): W, forward
        LD  CMD
        DIVI 2
        ST  TMP
        MODI 2
        ST  BS              ; bit 1 (2): S, backward
        LD  TMP
        DIVI 2
        ST  TMP
        MODI 2
        ST  BA              ; bit 2 (4): A, turn left
        LD  TMP
        DIVI 2
        ST  TMP
        MODI 2
        ST  BD              ; bit 3 (8): D, turn right
        LD  TMP
        DIVI 2
        MODI 2
        ST  FIRE            ; bit 4 (16): space — higher bits fall off here
        BRZ turn0           ; ST preserved ACC = the fire bit
        LD  AMMO
        BRZ turn0           ; dry-fire on an empty clip: the counter stays 0
        SUBI 1
        ST  AMMO            ; one live round spent — the HUD bar shrinks
        LDI 1
        ST  LIVE            ; … and THIS is the shot that can kill (M7b)

; ── turn first (lodev's order): heading += A - D, cancelling when both held ──
turn0:  LD  BA
        SUB BD
        BRZ mvchk           ; no net turn: dir/plane stay as they are
        ADD HDG
        MODI 16
        ST  HDG             ; heading + (BA - BD), MODI's floored sign wraps -1
        LD  HDG             ; re-unpack the packed heading word
        ADDI HDGB
        LDA                 ; base-4096 digits dirX dirY planeX planeY, +1024 each
        ST  TMP
        MODI 4096
        SUBI 1024
        ST  PLANEY
        LD  TMP
        DIVI 4096
        ST  TMP
        MODI 4096
        SUBI 1024
        ST  PLANEX
        LD  TMP
        DIVI 4096
        ST  TMP
        MODI 4096
        SUBI 1024
        ST  DIRY
        LD  TMP
        DIVI 4096
        SUBI 1024
        ST  DIRX

; ── then move, along the NEW heading: s = W - S, cancelling when both held ───
; A move is 2 cells, so each axis checks TWO cells — the half-way cell
; (delta DIVI 2, floored like the model's div) and the destination — real level
; geometry has one-cell walls, and a destination-only check would tunnel.
mvchk:  LD  BW
        SUB BS
        BRZ render          ; no net move: just render
        ST  TMP             ; s = +1 forward, -1 backward
        LD  DIRX
        MUL TMP
        MULI 2
        DIVI 1              ; deltaX = floor(dirX * s * 2 / 1)
        DIVI 2
        ADD POSX
        ST  NEWX            ; midX = posX + deltaX/2, the half-way cell
        ; collision X (half-way): map_cell(midX / 1024, posY / 1024), inlined
        LD  POSY
        DIVI 1024
        ST  TMP2            ; mapY (ST preserves ACC)
        MODI 16
        ADDI POWB
        LDA
        ST  PW              ; 16**(mapY mod 16)
        LD  TMP2
        DIVI 16
        ST  TMP2            ; the quarter-column selector, mapY / 16
        LD  NEWX
        DIVI 1024
        MULI 4
        ADD TMP2
        ADDI MAPB
        LDA                 ; the packed quarter-column of midX's cell
        DIV PW
        MODI 16
        BRZ okx             ; half-way open -> check the destination
        JMP movey           ; wall -> posX unchanged
okx:    LD  DIRX
        MUL TMP
        MULI 2
        DIVI 1
        ADD POSX
        ST  NEWX            ; newX = posX + deltaX
        ; collision X (destination): map_cell(newX / 1024, posY / 1024)
        LD  NEWX
        DIVI 1024
        MULI 4
        ADD TMP2            ; PW and the selector still hold posY's row
        ADDI MAPB
        LDA
        DIV PW
        MODI 16
        BRZ comx            ; empty -> commit posX
        JMP movey           ; wall -> posX unchanged
comx:   LD  NEWX
        ST  POSX
movey:  LD  DIRY
        MUL TMP
        MULI 2
        DIVI 1              ; deltaY
        DIVI 2
        ADD POSY
        ST  NEWY            ; midY = posY + deltaY/2
        ; collision Y (half-way): map_cell(posX / 1024, midY / 1024) — the UPDATED posX
        LD  NEWY
        DIVI 1024
        ST  TMP2
        MODI 16
        ADDI POWB
        LDA
        ST  PW
        LD  TMP2
        DIVI 16
        ST  TMP2
        LD  POSX
        DIVI 1024
        MULI 4
        ADD TMP2
        ADDI MAPB
        LDA
        DIV PW
        MODI 16
        BRZ oky
        JMP render
oky:    LD  DIRY
        MUL TMP
        MULI 2
        DIVI 1
        ADD POSY
        ST  NEWY            ; newY = posY + deltaY
        ; collision Y (destination): map_cell(posX / 1024, newY / 1024) — PW/selector
        ; must be newY's own row, so the whole lookup is redone
        LD  NEWY
        DIVI 1024
        ST  TMP2
        MODI 16
        ADDI POWB
        LDA
        ST  PW
        LD  TMP2
        DIVI 16
        ST  TMP2
        LD  POSX
        DIVI 1024
        MULI 4
        ADD TMP2
        ADDI MAPB
        LDA
        DIV PW
        MODI 16
        BRZ comy
        JMP render
comy:   LD  NEWY
        ST  POSY
        JMP render

; ── nukage (M5): the player's cell's bit of the 1-bit damage plane ───────────
; bit y of plane word NUKB+x is  word / 16**(y/4) / 2**(y mod 4) mod 2 — the
; high bits of the shift ride the POWB table (16**k == 2**4k), the low two
; come off a 4-way divisor ladder (there is no POW2 table on the tape).
; Standing on nukage: HEALTH -5, floor 0, and NUKE=1 makes every
; column's floor repaint green (the overlay COL words below).
render: LD  POSY
        DIVI 1024
        ST  TMP             ; mapY = the plane word's bit index
        DIVI 4
        ADDI POWB
        LDA                 ; 16**(mapY / 4)
        ST  TMP2
        LD  POSX
        DIVI 1024
        ADDI NUKB
        LDA                 ; the player's column's plane word
        DIV TMP2
        ST  NUKE            ; parked: the word shifted down 4*(mapY/4) bits
        LD  TMP
        MODI 4              ; the low two bits pick the 1/2/4/8 divisor
        BRZ nkm0
        SUBI 1
        BRZ nkm1
        SUBI 1
        BRZ nkm2
        LD  NUKE
        DIVI 8
        JMP nkbit
nkm1:   LD  NUKE
        DIVI 2
        JMP nkbit
nkm2:   LD  NUKE
        DIVI 4
        JMP nkbit
nkm0:   LD  NUKE
nkbit:  MODI 2
        ST  NUKE            ; 1 = this frame stands on a damage floor
        BRZ prolog          ; clean floor: no damage
        LD  HEALTH
        SUBI 5
        BRN hzero
        ST  HEALTH          ; the red bar shrinks on this very frame
        JMP prolog
hzero:  LDI 0
        ST  HEALTH          ; floor 0: the bar empties, no death mechanics yet

; ── render: lodev's per-column raycast, columns 0..127 ──────────────────────
; The per-frame prologue: everything that depends only on the player's position
; is computed once — the fractional position, and the cell-lookup seeds PW0 (the
; nibble divisor 16**(mapY mod 16)) and WADDR0 (the packed quarter-column's slot,
; MAPB + 4*mapX + mapY/16). The DDA then maintains PW/WADDR *incrementally*, so
; the per-step lookup is LDA/DIV/MODI instead of the full 16-instruction unpack.
prolog: LD  POSX
        MODI 1024
        ST  FRACX           ; posX - mapX*1024, hoisted out of sidex
        LD  POSY
        MODI 1024
        ST  FRACY
        LD  POSY
        DIVI 1024
        ST  TMP             ; mapY
        MODI 16
        ADDI POWB
        LDA
        ST  PW0             ; 16**(mapY mod 16)
        LD  TMP
        DIVI 16
        ST  TMP2            ; the quarter-column selector, mapY / 16
        LD  POSX
        DIVI 1024
        MULI 4
        ADD TMP2
        ADDI MAPB
        ST  WADDR0          ; the packed quarter-column's tape slot
        LDI 0
        ST  XCOL
colset: LD  XCOL
        MULI 16
        SUBI 1024
        ST  CAMX            ; cameraX = 2*x/w - 1 -> 16*x - 1024, exact at w = 128
        LD  PLANEX
        MUL CAMX
        DIVI 1024
        ADD DIRX
        ST  RDX             ; rayDirX = dirX + planeX*cameraX
        LD  PLANEY
        MUL CAMX
        DIVI 1024
        ADD DIRY
        ST  RDY             ; rayDirY = dirY + planeY*cameraX
        LD  PW0
        ST  PW              ; the ray starts in the player's cell
        LD  WADDR0
        ST  WADDR
        ; deltaDistX = abs(1/rayDirX) -> |1048576 / rayDirX|; DIV by 0 is 0 on
        ; this CPU, so a zero ray substitutes BIG = 2**30 (plan risk R2)
        LD  RDX
        BRZ ddxinf
        LDI 1048576
        DIV RDX
        BRN ddxneg          ; the quotient's sign is rayDirX's
        ST  DDX
        JMP ddy
ddxneg: NEG
        ST  DDX
        JMP ddy
ddxinf: LDI 1073741824
        ST  DDX
ddy:    LD  RDY             ; deltaDistY, the same three arms
        BRZ ddyinf
        LDI 1048576
        DIV RDY
        BRN ddyneg
        ST  DDY
        JMP sidex
ddyneg: NEG
        ST  DDY
        JMP sidex
ddyinf: LDI 1073741824
        ST  DDY
        ; stepX / sideDistX from the fractional position (lodev's two arms);
        ; stepX itself is only ever used to move the word address, so the arm
        ; records S4X = 4*stepX instead of stepX
sidex:  LD  RDX
        BRN sxneg
        LDI 4
        ST  S4X             ; stepX = 1 -> the quarter-column slot moves +4
        LDI 1024
        SUB FRACX
        MUL DDX
        DIVI 1024
        ST  SDX             ; sideDistX = (1024 - fracX) * deltaDistX / 1024
        JMP sidey
sxneg:  LDI 0
        SUBI 4
        ST  S4X             ; stepX = -1 -> -4
        LD  FRACX
        MUL DDX
        DIVI 1024
        ST  SDX             ; sideDistX = fracX * deltaDistX / 1024
sidey:  LD  RDY             ; stepY / sideDistY, the same two arms
        BRN syneg
        LDI 1
        ST  STPY
        LDI 1024
        SUB FRACY
        MUL DDY
        DIVI 1024
        ST  SDY
        JMP dda0
syneg:  LDI 0
        SUBI 1
        ST  STPY
        LD  FRACY
        MUL DDY
        DIVI 1024
        ST  SDY
        ; the DDA, unrolled 16x: a backward jump costs 8*(P - loop) ticks on
        ; this machine, so only every 16th empty step pays a full lap; a
        ; sideDist tie goes to the Y arm (lodev's else — risk R5)

dda0:   LD  SDX
        SUB SDY
        BRN xarm0           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg0
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru0           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity0
yneg0:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd0           ; ... and 1/16 floors to 0
        JMP hity0
ywru0:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity0
ywrd0:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity0
hity0:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda1            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm0:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda1            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda1:   LD  SDX
        SUB SDY
        BRN xarm1           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg1
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru1           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity1
yneg1:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd1           ; ... and 1/16 floors to 0
        JMP hity1
ywru1:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity1
ywrd1:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity1
hity1:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda2            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm1:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda2            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda2:   LD  SDX
        SUB SDY
        BRN xarm2           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg2
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru2           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity2
yneg2:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd2           ; ... and 1/16 floors to 0
        JMP hity2
ywru2:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity2
ywrd2:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity2
hity2:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda3            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm2:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda3            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda3:   LD  SDX
        SUB SDY
        BRN xarm3           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg3
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru3           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity3
yneg3:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd3           ; ... and 1/16 floors to 0
        JMP hity3
ywru3:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity3
ywrd3:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity3
hity3:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda4            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm3:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda4            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda4:   LD  SDX
        SUB SDY
        BRN xarm4           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg4
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru4           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity4
yneg4:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd4           ; ... and 1/16 floors to 0
        JMP hity4
ywru4:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity4
ywrd4:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity4
hity4:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda5            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm4:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda5            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda5:   LD  SDX
        SUB SDY
        BRN xarm5           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg5
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru5           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity5
yneg5:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd5           ; ... and 1/16 floors to 0
        JMP hity5
ywru5:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity5
ywrd5:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity5
hity5:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda6            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm5:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda6            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda6:   LD  SDX
        SUB SDY
        BRN xarm6           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg6
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru6           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity6
yneg6:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd6           ; ... and 1/16 floors to 0
        JMP hity6
ywru6:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity6
ywrd6:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity6
hity6:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda7            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm6:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda7            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda7:   LD  SDX
        SUB SDY
        BRN xarm7           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg7
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru7           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity7
yneg7:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd7           ; ... and 1/16 floors to 0
        JMP hity7
ywru7:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity7
ywrd7:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity7
hity7:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda8            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm7:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda8            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda8:   LD  SDX
        SUB SDY
        BRN xarm8           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg8
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru8           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity8
yneg8:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd8           ; ... and 1/16 floors to 0
        JMP hity8
ywru8:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity8
ywrd8:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity8
hity8:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda9            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm8:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda9            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda9:   LD  SDX
        SUB SDY
        BRN xarm9           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg9
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru9           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity9
yneg9:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd9           ; ... and 1/16 floors to 0
        JMP hity9
ywru9:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity9
ywrd9:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity9
hity9:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda10            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm9:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda10            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda10:   LD  SDX
        SUB SDY
        BRN xarm10           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg10
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru10           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity10
yneg10:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd10           ; ... and 1/16 floors to 0
        JMP hity10
ywru10:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity10
ywrd10:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity10
hity10:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda11            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm10:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda11            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda11:   LD  SDX
        SUB SDY
        BRN xarm11           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg11
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru11           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity11
yneg11:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd11           ; ... and 1/16 floors to 0
        JMP hity11
ywru11:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity11
ywrd11:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity11
hity11:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda12            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm11:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda12            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda12:   LD  SDX
        SUB SDY
        BRN xarm12           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg12
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru12           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity12
yneg12:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd12           ; ... and 1/16 floors to 0
        JMP hity12
ywru12:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity12
ywrd12:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity12
hity12:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda13            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm12:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda13            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda13:   LD  SDX
        SUB SDY
        BRN xarm13           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg13
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru13           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity13
yneg13:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd13           ; ... and 1/16 floors to 0
        JMP hity13
ywru13:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity13
ywrd13:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity13
hity13:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda14            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm13:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda14            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda14:   LD  SDX
        SUB SDY
        BRN xarm14           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg14
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru14           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity14
yneg14:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd14           ; ... and 1/16 floors to 0
        JMP hity14
ywru14:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity14
ywrd14:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity14
hity14:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda15            ; empty -> the next unrolled step
        JMP why             ; a y-side wall: t is dark
xarm14:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda15            ; empty -> the next unrolled step
        JMP whx             ; an x-side wall: t is sunlit

dda15:   LD  SDX
        SUB SDY
        BRN xarm15           ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  STPY            ; mapY += stepY, kept as PW/WADDR increments
        BRN yneg15
        LD  PW
        MULI 16             ; mapY += 1: the nibble divisor shifts up ...
        ST  PW
        BRZ ywru15           ; ... and 16**15 * 16 wraps to exactly 0 (64-bit)
        JMP hity15
yneg15:  LD  PW
        DIVI 16             ; mapY -= 1: the divisor shifts down ...
        ST  PW
        BRZ ywrd15           ; ... and 1/16 floors to 0
        JMP hity15
ywru15:  LDI 1
        ST  PW
        INCM WADDR          ; mapY crossed into the upper quarter-column word
        JMP hity15
ywrd15:  LDI 1152921504606846976
        ST  PW
        LD  WADDR
        SUBI 1
        ST  WADDR           ; mapY crossed into the lower quarter-column word
        JMP hity15
hity15:  LD  WADDR          ; the y-side hit test (its own tail: no side flag)
        LDA                 ; the packed quarter-column word at (mapX, mapY)
        DIV PW
        MODI 16
        BRZ dda0            ; empty -> the backward lap
        JMP why             ; a y-side wall: t is dark
xarm15:  LD  SDX
        ADD DDX
        ST  SDX
        LD  WADDR
        ADD S4X
        ST  WADDR           ; mapX += stepX is the quarter-column slot moving +-4
        LD  WADDR
        LDA                 ; the x-side hit test
        DIV PW
        MODI 16
        BRZ dda0            ; empty -> the backward lap
        JMP whx             ; an x-side wall: t is sunlit

; Which arm found the wall picks the whole tail — and, since V3, the texture
; stripe: the parity of the map coordinate ALONG the wall face, read straight
; off the incremental lookup state. x-side: mapY & 1 is (PW % 17) / 16
; (16 = -1 mod 17, so 16^k % 17 is 1 or 16), inverted so a sunlit face and a
; neighbouring shadow face keep their corner contrast.
whx:    ST  COLOR           ; the wall type t — the dark base
        LD  PW
        MODI 17
        DIVI 16
        ST  TMP             ; mapY & 1
        LDI 1
        SUB TMP
        ST  TMP             ; stripe = 1 - (mapY & 1)
        LD  SDX
        SUB DDX
        ST  PERP
        JMP pclip
why:    ST  COLOR           ; y-side: stripe = mapX & 1 = (WADDR - 1) / 4 % 2
        LD  WADDR
        SUBI 1
        DIVI 4
        MODI 2
        ST  TMP
        LD  SDY
        SUB DDY
        ST  PERP
pclip:  SUBI 1              ; ST preserved ACC = perpWallDist
        BRN pone
        JMP zstore
pone:   LDI 1
        ST  PERP
; the z-buffer (M7a): the column's final clamped depth, persisted for the
; sprite pass's occlusion compare — store[ZBUF + XCOL] = PERP
zstore: LD  XCOL
        ADDI ZBUF
        MOVA PERP
; distance shading + the panel stripe (V3): COLOR steps up to the bright
; variant t + 8 exactly when the wall is NEAR (perp < 16384) and this
; column's stripe bit is set; a far wall keeps the dark base whatever its face
nearck: LD  PERP
        SUBI 16384
        BRN strck
        JMP lineh           ; far: the dark base stands
strck:  LD  TMP
        BRZ lineh           ; the dark panel of the stripe pair
        LD  COLOR
        ADDI 8
        ST  COLOR
lineh:  LDI 163840
        DIV PERP            ; lineHeight = h / perpWallDist -> (80*1024) / perp
        DIVI 2
        ST  HALFH
        LDI 40
        SUB HALFH
        ST  DSTART          ; drawStart = 40 - halfh
        BRN dslo
        JMP dehi
dslo:   LDI 0
        ST  DSTART          ; clamped at the top of the viewport
dehi:   LD  HALFH
        ADDI 40
        ST  DEND            ; drawEnd = 40 + halfh
        SUBI 80
        BRN send            ; drawEnd <= 79: no clamp
        LDI 79
        ST  DEND
        ; ── the tiled send: one COL word per panel this column touches ────────
        ; The frame is four 64x48 panels, so a viewport column crosses the seam
        ; at row 48. TXT is the column inside its panel; TSELT and TSELB are the
        ; router selectors for the panel above the seam and the one below it.
        ; Every other number here is the single-panel machine's, unchanged:
        ; stride 64, lap bias 1024, argument radix 64.
send:   LD  XCOL
        SUBI 64
        BRN sleft           ; x < 64: the left pair, panels T0/T2
        ST  TXT             ; the right pair: TXT = x - 64, panels T1/T3
        LDI 1
        ST  TSELT
        LDI 3
        ST  TSELB
        JMP stop
sleft:  LD  XCOL
        ST  TXT
        LDI 5
        ST  TSELT
        LDI 7
        ST  TSELB
        ; ── the top panel: wall rows drawStart..min(drawEnd, 47) ─────────
stop:   LD  DSTART
        SUBI 48
        BRN stwall          ; drawStart < 48: this panel carries wall
        JMP sbot            ; the wall starts below the seam: all ceiling here
stwall: LD  DEND
        SUBI 47
        BRN stkeep
        LDI 47
        ST  TTE             ; clipped at the seam
        JMP stsend
stkeep: LD  DEND
        ST  TTE
stsend: LD  DSTART
        MULI 64
        ADD TXT
        MULI 16
        ADD COLOR
        SUBI 1024
        MULI 64
        ADD TTE
        SUB DSTART
        ADDI 1              ; arg = seed*64 + n_wall
        MULI 8              ; the unit word: 8*arg + C_COL, C_COL == 0
        MULI 8
        ADD TSELT
        SND                 ; ... and the router word: 8*unit + sel
        LD  NUKE
        BRZ sbot            ; clean floor: the COL word's gray floor stands
        LD  TTE
        SUBI 47
        BRZ sbot            ; wall to the panel's last row: no floor to flood
        LD  TTE
        ADDI 1
        MULI 64
        ADD TXT
        MULI 16
        ADDI 2
        SUBI 1024
        MULI 64
        ADDI 47
        SUB TTE
        MULI 8
        MULI 8
        ADD TSELT
        SND
        ; ── the bottom panel: viewport rows 48..79 = its own 0..31 ──
sbot:   LD  DEND
        SUBI 48
        BRN sbfl            ; the wall ended above the seam: all floor down here
        LD  DSTART
        SUBI 48
        BRN sbs0
        ST  TBS             ; the wall starts below the seam
        JMP sbe
sbs0:   LDI 0
        ST  TBS             ; the wall crosses it: this panel starts at row 0
sbe:    LD  DEND
        SUBI 48
        ST  TBE
        LD  TBS
        MULI 64
        ADD TXT
        MULI 16
        ADD COLOR
        SUBI 1024
        MULI 64
        ADD TBE
        SUB TBS
        ADDI 1
        MULI 8
        MULI 8
        ADD TSELB
        SND
        JMP sbnuk
sbfl:   LDI 0
        SUBI 1
        ST  TBE             ; -1: the flood below must cover row 0 too
        LD  TXT
        MULI 16
        ADDI 8
        SUBI 1024
        MULI 64
        ADDI 1              ; one seed pixel at row 0 — the unit floors the rest
        MULI 8
        MULI 8
        ADD TSELB
        SND
        ; The seed pixel is the wall loop's FIRST lap, and lap 0 is the banding
        ; seam: its mask is 7, and 8 & 7 == 0. The floor colour is the one colour
        ; that cannot survive the mask (the nukage flood picks 2 precisely
        ; because it can), so row 0 would be a black scanline right along the
        ; seam. Repaint it with a CURS + RUN pair, which the mask never touches.
        LD  TXT
        MULI 8
        ADDI C_CURS
        MULI 8
        ADD TSELB
        SND                 ; CURS: the bottom panel's row 0, this column
        LDI 196
        MULI 8
        ADD TSELB
        SND                 ; RUN 1 x colour 8 — the seam pixel, unmasked
sbnuk:  LD  NUKE
        BRZ colnxt
        LD  TBE
        SUBI 31
        BRZ colnxt          ; wall to the panel's last row: no floor to flood
        LD  TBE
        ADDI 1
        MULI 64
        ADD TXT
        MULI 16
        ADDI 2
        SUBI 1024
        MULI 64
        ADDI 31
        SUB TBE
        MULI 8
        MULI 8
        ADD TSELB
        SND
colnxt: INCM XCOL           ; ACC = the old column number
        SUBI 127
        BRZ spsel           ; that was column 127: the viewport is sent
        JMP colset

; ── no sprite phase: see Geom.sprites ──
spsel:

; ── the pistol: CURS + RUN words, the CPU's own (see _pistol_asm) ───────────
; The sprite straddles the seam at x = 64, so a run is up to two panels'
; spans; the words below are constants and the FIRE bit picks the variant.
gun:    LD  FIRE
        BRZ gidle
        JMP gfire
gidle:  LDI 49163          ; the idle pistol
        SND
        LDI 2531
        SND
        LDI 53259
        SND
        LDI 2531
        SND
        LDI 61327
        SND
        LDI 2535
        SND
        LDI 57355
        SND
        LDI 2531
        SND
        LDI 2083
        SND
        LDI 65423
        SND
        LDI 2535
        SND
        LDI 61451
        SND
        LDI 2531
        SND
        LDI 2083
        SND
        LDI 69391
        SND
        LDI 4583
        SND
        LDI 65547
        SND
        LDI 4579
        SND
        LDI 2083
        SND
        LDI 73487
        SND
        LDI 4583
        SND
        LDI 69643
        SND
        LDI 4579
        SND
        LDI 2083
        SND
        LDI 77455
        SND
        LDI 4583
        SND
        LDI 2087
        SND
        LDI 73739
        SND
        LDI 6179
        SND
        LDI 2531
        SND
        LDI 81551
        SND
        LDI 4583
        SND
        LDI 2087
        SND
        LDI 77835
        SND
        LDI 6179
        SND
        LDI 2531
        SND
        LDI 85647
        SND
        LDI 2535
        SND
        LDI 2151
        SND
        LDI 2087
        SND
        LDI 81931
        SND
        LDI 2147
        SND
        LDI 2083
        SND
        LDI 2147
        SND
        LDI 89743
        SND
        LDI 2535
        SND
        LDI 2151
        SND
        LDI 2087
        SND
        LDI 86027
        SND
        LDI 2147
        SND
        LDI 2083
        SND
        LDI 2147
        SND
        LDI 82315
        SND
        LDI 2531
        SND
        LDI 86411
        SND
        LDI 2531
        SND
        LDI 93839
        SND
        LDI 6631
        SND
        LDI 90123
        SND
        LDI 6627
        SND
        LDI 2083
        SND
        LDI 97935
        SND
        LDI 6631
        SND
        LDI 94219
        SND
        LDI 6627
        SND
        LDI 2083
        SND
        LDI 101903
        SND
        LDI 4583
        SND
        LDI 4135
        SND
        LDI 98315
        SND
        LDI 4131
        SND
        LDI 4579
        SND
        LDI 105999
        SND
        LDI 4583
        SND
        LDI 4135
        SND
        LDI 102411
        SND
        LDI 4131
        SND
        LDI 4579
        SND
        LDI 110095
        SND
        LDI 4327
        SND
        LDI 2087
        SND
        LDI 2599
        SND
        LDI 106507
        SND
        LDI 2595
        SND
        LDI 2083
        SND
        LDI 4323
        SND
        LDI 114191
        SND
        LDI 4327
        SND
        LDI 2087
        SND
        LDI 2599
        SND
        LDI 110603
        SND
        LDI 2595
        SND
        LDI 2083
        SND
        LDI 4323
        SND
        LDI 118159
        SND
        LDI 2087
        SND
        LDI 8423
        SND
        LDI 114699
        SND
        LDI 8419
        SND
        LDI 2595
        SND
        LDI 122255
        SND
        LDI 2087
        SND
        LDI 8423
        SND
        LDI 118795
        SND
        LDI 8419
        SND
        LDI 2595
        SND
        LDI 126351
        SND
        LDI 2087
        SND
        LDI 8423
        SND
        LDI 122891
        SND
        LDI 4323
        SND
        LDI 4643
        SND
        LDI 130447
        SND
        LDI 2087
        SND
        LDI 8423
        SND
        LDI 126987
        SND
        LDI 4323
        SND
        LDI 4643
        SND
        LDI 123403
        SND
        LDI 2083
        SND
        LDI 127499
        SND
        LDI 2083
        SND
        JMP hud
gfire:  LDI 12175          ; recoil + muzzle flash
        SND
        LDI 2663
        SND
        LDI 8203
        SND
        LDI 4835
        SND
        LDI 2659
        SND
        LDI 16271
        SND
        LDI 2663
        SND
        LDI 12299
        SND
        LDI 4835
        SND
        LDI 2659
        SND
        LDI 20239
        SND
        LDI 2791
        SND
        LDI 3047
        SND
        LDI 16395
        SND
        LDI 7139
        SND
        LDI 2787
        SND
        LDI 24335
        SND
        LDI 2791
        SND
        LDI 3047
        SND
        LDI 20491
        SND
        LDI 7139
        SND
        LDI 2787
        SND
        LDI 28303
        SND
        LDI 2279
        SND
        LDI 2791
        SND
        LDI 3047
        SND
        LDI 24587
        SND
        LDI 7139
        SND
        LDI 32399
        SND
        LDI 2279
        SND
        LDI 2791
        SND
        LDI 3047
        SND
        LDI 28683
        SND
        LDI 7139
        SND
        LDI 24971
        SND
        LDI 2787
        SND
        LDI 2275
        SND
        LDI 29067
        SND
        LDI 2787
        SND
        LDI 2275
        SND
        LDI 36751
        SND
        LDI 2663
        SND
        LDI 32779
        SND
        LDI 5091
        SND
        LDI 2659
        SND
        LDI 40847
        SND
        LDI 2663
        SND
        LDI 36875
        SND
        LDI 5091
        SND
        LDI 2659
        SND
        LDI 40971
        SND
        LDI 2531
        SND
        LDI 45067
        SND
        LDI 2531
        SND
        LDI 53135
        SND
        LDI 2535
        SND
        LDI 49163
        SND
        LDI 2531
        SND
        LDI 2083
        SND
        LDI 57231
        SND
        LDI 2535
        SND
        LDI 53259
        SND
        LDI 2531
        SND
        LDI 2083
        SND
        LDI 61199
        SND
        LDI 4583
        SND
        LDI 57355
        SND
        LDI 4579
        SND
        LDI 2083
        SND
        LDI 65295
        SND
        LDI 4583
        SND
        LDI 61451
        SND
        LDI 4579
        SND
        LDI 2083
        SND
        LDI 69263
        SND
        LDI 4583
        SND
        LDI 2087
        SND
        LDI 65547
        SND
        LDI 6179
        SND
        LDI 2531
        SND
        LDI 73359
        SND
        LDI 4583
        SND
        LDI 2087
        SND
        LDI 69643
        SND
        LDI 6179
        SND
        LDI 2531
        SND
        LDI 77455
        SND
        LDI 2535
        SND
        LDI 2151
        SND
        LDI 2087
        SND
        LDI 73739
        SND
        LDI 2147
        SND
        LDI 2083
        SND
        LDI 2147
        SND
        LDI 81551
        SND
        LDI 2535
        SND
        LDI 2151
        SND
        LDI 2087
        SND
        LDI 77835
        SND
        LDI 2147
        SND
        LDI 2083
        SND
        LDI 2147
        SND
        LDI 74123
        SND
        LDI 2531
        SND
        LDI 78219
        SND
        LDI 2531
        SND
        LDI 85647
        SND
        LDI 6631
        SND
        LDI 81931
        SND
        LDI 6627
        SND
        LDI 2083
        SND
        LDI 89743
        SND
        LDI 6631
        SND
        LDI 86027
        SND
        LDI 6627
        SND
        LDI 2083
        SND
        LDI 93711
        SND
        LDI 4583
        SND
        LDI 4135
        SND
        LDI 90123
        SND
        LDI 4131
        SND
        LDI 4579
        SND
        LDI 97807
        SND
        LDI 4583
        SND
        LDI 4135
        SND
        LDI 94219
        SND
        LDI 4131
        SND
        LDI 4579
        SND
        LDI 101903
        SND
        LDI 4327
        SND
        LDI 2087
        SND
        LDI 2599
        SND
        LDI 98315
        SND
        LDI 2595
        SND
        LDI 2083
        SND
        LDI 4323
        SND
        LDI 105999
        SND
        LDI 4327
        SND
        LDI 2087
        SND
        LDI 2599
        SND
        LDI 102411
        SND
        LDI 2595
        SND
        LDI 2083
        SND
        LDI 4323
        SND
        LDI 109967
        SND
        LDI 2087
        SND
        LDI 8423
        SND
        LDI 106507
        SND
        LDI 8419
        SND
        LDI 2595
        SND
        LDI 114063
        SND
        LDI 2087
        SND
        LDI 8423
        SND
        LDI 110603
        SND
        LDI 8419
        SND
        LDI 2595
        SND
        LDI 118159
        SND
        LDI 2087
        SND
        LDI 8423
        SND
        LDI 114699
        SND
        LDI 4323
        SND
        LDI 4643
        SND
        LDI 122255
        SND
        LDI 2087
        SND
        LDI 8423
        SND
        LDI 118795
        SND
        LDI 4323
        SND
        LDI 4643
        SND
        LDI 115211
        SND
        LDI 2083
        SND
        LDI 119307
        SND
        LDI 2083
        SND

; ── HUD (M8): cursor to slot 10240, then DOOM's REAL status bar as
; 41 pre-encoded RUN words (hud_bg_runs() — STBAR block-quantized 5x4
; onto the strip), then the LIVE readouts in the bar's OWN number wells:
; ammo columns 0..8 (1px per 6 rounds), health
; columns 10..20 (1px per 10), both on rows 41..44
; in the digits' own red 1; an empty bar sends nothing and
; the bar art shows through
hud:    LDI 131087
        SND                 ; CURS: the panel cursor to the strip's top-left
        LDI 131083
        SND                 ; CURS: the panel cursor to the strip's top-left
        LDI 320039
        SND                 ; RUN 312 x colour 8
        LDI 295459
        SND                 ; RUN 288 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 4579
        SND                 ; RUN 4 x colour 7
        LDI 64039
        SND                 ; RUN 62 x colour 8
        LDI 4643
        SND                 ; RUN 4 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 2531
        SND                 ; RUN 2 x colour 7
        LDI 64039
        SND                 ; RUN 62 x colour 8
        LDI 55843
        SND                 ; RUN 54 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 4579
        SND                 ; RUN 4 x colour 7
        LDI 64039
        SND                 ; RUN 62 x colour 8
        LDI 4643
        SND                 ; RUN 4 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 2531
        SND                 ; RUN 2 x colour 7
        LDI 64039
        SND                 ; RUN 62 x colour 8
        LDI 61987
        SND                 ; RUN 60 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 4579
        SND                 ; RUN 4 x colour 7
        LDI 64039
        SND                 ; RUN 62 x colour 8
        LDI 21027
        SND                 ; RUN 20 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 2531
        SND                 ; RUN 2 x colour 7
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 39459
        SND                 ; RUN 38 x colour 8
        LDI 59879
        SND                 ; RUN 58 x colour 7
        LDI 4579
        SND                 ; RUN 4 x colour 7
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 21027
        SND                 ; RUN 20 x colour 8
        LDI 59879
        SND                 ; RUN 58 x colour 7
        LDI 2531
        SND                 ; RUN 2 x colour 7
        LDI 10791
        SND                 ; RUN 10 x colour 8
        LDI 37411
        SND                 ; RUN 36 x colour 8
        LDI 10727
        SND                 ; RUN 10 x colour 7
        LDI 6627
        SND                 ; RUN 6 x colour 7
        LDI 8743
        SND                 ; RUN 8 x colour 8
        LDI 21027
        SND                 ; RUN 20 x colour 8
        LDI 4583
        SND                 ; RUN 4 x colour 7
        LDI 2531
        SND                 ; RUN 2 x colour 7
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 37411
        SND                 ; RUN 36 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 6627
        SND                 ; RUN 6 x colour 7
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 21027
        SND                 ; RUN 20 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 2531
        SND                 ; RUN 2 x colour 7
        LDI 12839
        SND                 ; RUN 12 x colour 8
        LDI 12835
        SND                 ; RUN 12 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 20963
        SND                 ; RUN 20 x colour 7
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 8739
        SND                 ; RUN 8 x colour 8
        LDI 4583
        SND                 ; RUN 4 x colour 7
        LDI 2531
        SND                 ; RUN 2 x colour 7
        LDI 12839
        SND                 ; RUN 12 x colour 8
        LDI 35363
        SND                 ; RUN 34 x colour 8
        LDI 10727
        SND                 ; RUN 10 x colour 7
        LDI 20963
        SND                 ; RUN 20 x colour 7
        LDI 8743
        SND                 ; RUN 8 x colour 8
        LDI 8739
        SND                 ; RUN 8 x colour 8
        LDI 4583
        SND                 ; RUN 4 x colour 7
        LDI 2531
        SND                 ; RUN 2 x colour 7
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 35363
        SND                 ; RUN 34 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 14819
        SND                 ; RUN 14 x colour 7
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 51747
        SND                 ; RUN 50 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 14819
        SND                 ; RUN 14 x colour 7
        LDI 12839
        SND                 ; RUN 12 x colour 8
        LDI 170531
        SND                 ; RUN 166 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 4583
        SND                 ; RUN 4 x colour 7
        LDI 8743
        SND                 ; RUN 8 x colour 8
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 14887
        SND                 ; RUN 14 x colour 8
        LDI 6183
        SND                 ; RUN 6 x colour 0
        LDI 43559
        SND                 ; RUN 42 x colour 8
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 14887
        SND                 ; RUN 14 x colour 8
        LDI 6183
        SND                 ; RUN 6 x colour 0
        LDI 43559
        SND                 ; RUN 42 x colour 8
hbar:   LD  HEALTH
        DIVI 5
        ST  TMP             ; the health bar in pixels
        BRZ abar           ; nothing to paint: the bar art shows through
        LDI 140559
        SND                 ; CURS: row 82, column 20
        LD  TMP
        MULI 16
        ADDI 1
        MULI 8
        ADDI C_RUN
        MULI 8
        ADDI 7    ; ... to panel T2
        ST  TMP2            ; the bar's RUN word — reused for its other rows
        SND
        LDI 144655
        SND                 ; CURS: row 83, column 20
        LD  TMP2
        SND
        LDI 148751
        SND                 ; CURS: row 84, column 20
        LD  TMP2
        SND
        LDI 152847
        SND                 ; CURS: row 85, column 20
        LD  TMP2
        SND
        LDI 156943
        SND                 ; CURS: row 86, column 20
        LD  TMP2
        SND
        LDI 161039
        SND                 ; CURS: row 87, column 20
        LD  TMP2
        SND
        LDI 165135
        SND                 ; CURS: row 88, column 20
        LD  TMP2
        SND
        LDI 169231
        SND                 ; CURS: row 89, column 20
        LD  TMP2
        SND
abar:   LD  AMMO
        DIVI 3
        ST  TMP             ; the ammo bar in pixels
        BRZ face           ; nothing to paint: the bar art shows through
        LDI 139279
        SND                 ; CURS: row 82, column 0
        LD  TMP
        MULI 16
        ADDI 1
        MULI 8
        ADDI C_RUN
        MULI 8
        ADDI 7    ; ... to panel T2
        ST  TMP2            ; the bar's RUN word — reused for its other rows
        SND
        LDI 143375
        SND                 ; CURS: row 83, column 0
        LD  TMP2
        SND
        LDI 147471
        SND                 ; CURS: row 84, column 0
        LD  TMP2
        SND
        LDI 151567
        SND                 ; CURS: row 85, column 0
        LD  TMP2
        SND
        LDI 155663
        SND                 ; CURS: row 86, column 0
        LD  TMP2
        SND
        LDI 159759
        SND                 ; CURS: row 87, column 0
        LD  TMP2
        SND
        LDI 163855
        SND                 ; CURS: row 88, column 0
        LD  TMP2
        SND
        LDI 167951
        SND                 ; CURS: row 89, column 0
        LD  TMP2
        SND

; ── the face (M5/M8): the Freedoom mugshot, 12x14 in STBAR's own inset —
; rows 80..93, columns 58..69;
; four baked variants (face_for), each a constant list of
; CURS + RLE RUN words; the branch ladder picks FIRE's grimace first, then
; the HEALTH band (> 66 healthy, > 33 hurt, else bloodied)
face:   LD  FIRE
        BRZ fband           ; not firing: the health band picks the face
        JMP fgrim
fband:  LD  HEALTH
        SUBI 67
        BRN fb2
        JMP fwell           ; health > 66: the healthy face
fb2:    LD  HEALTH
        SUBI 34
        BRN fbld            ; health <= 33: the bloodied face
        JMP fhurt
fwell:  LDI 134799          ; healthy (stfst00)
        SND                 ; CURS: face row 80, column 58
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 131083
        SND                 ; CURS: face row 80, column 64
        LDI 4131
        SND                 ; RUN 4 x colour 0
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 138895
        SND                 ; CURS: face row 81, column 58
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 135179
        SND                 ; CURS: face row 81, column 64
        LDI 4131
        SND                 ; RUN 4 x colour 0
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 142991
        SND                 ; CURS: face row 82, column 58
        LDI 6183
        SND                 ; RUN 6 x colour 0
        LDI 139275
        SND                 ; CURS: face row 82, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 147087
        SND                 ; CURS: face row 83, column 58
        LDI 6183
        SND                 ; RUN 6 x colour 0
        LDI 143371
        SND                 ; CURS: face row 83, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 151183
        SND                 ; CURS: face row 84, column 58
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 147467
        SND                 ; CURS: face row 84, column 64
        LDI 2275
        SND                 ; RUN 2 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 155279
        SND                 ; CURS: face row 85, column 58
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 151563
        SND                 ; CURS: face row 85, column 64
        LDI 2275
        SND                 ; RUN 2 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 159375
        SND                 ; CURS: face row 86, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 155659
        SND                 ; CURS: face row 86, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 163471
        SND                 ; CURS: face row 87, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 159755
        SND                 ; CURS: face row 87, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 167567
        SND                 ; CURS: face row 88, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 163851
        SND                 ; CURS: face row 88, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 171663
        SND                 ; CURS: face row 89, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 167947
        SND                 ; CURS: face row 89, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 175759
        SND                 ; CURS: face row 90, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 172043
        SND                 ; CURS: face row 90, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 179855
        SND                 ; CURS: face row 91, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 176139
        SND                 ; CURS: face row 91, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 183951
        SND                 ; CURS: face row 92, column 58
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 180235
        SND                 ; CURS: face row 92, column 64
        LDI 6691
        SND                 ; RUN 6 x colour 8
        LDI 188047
        SND                 ; CURS: face row 93, column 58
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 184331
        SND                 ; CURS: face row 93, column 64
        LDI 6691
        SND                 ; RUN 6 x colour 8
        JMP cmit
fhurt:  LDI 134799          ; hurt (stfst20)
        SND                 ; CURS: face row 80, column 58
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 131083
        SND                 ; CURS: face row 80, column 64
        LDI 4131
        SND                 ; RUN 4 x colour 0
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 138895
        SND                 ; CURS: face row 81, column 58
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 135179
        SND                 ; CURS: face row 81, column 64
        LDI 4131
        SND                 ; RUN 4 x colour 0
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 142991
        SND                 ; CURS: face row 82, column 58
        LDI 6183
        SND                 ; RUN 6 x colour 0
        LDI 139275
        SND                 ; CURS: face row 82, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 147087
        SND                 ; CURS: face row 83, column 58
        LDI 6183
        SND                 ; RUN 6 x colour 0
        LDI 143371
        SND                 ; CURS: face row 83, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 151183
        SND                 ; CURS: face row 84, column 58
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 147467
        SND                 ; CURS: face row 84, column 64
        LDI 2275
        SND                 ; RUN 2 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 155279
        SND                 ; CURS: face row 85, column 58
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 151563
        SND                 ; CURS: face row 85, column 64
        LDI 2275
        SND                 ; RUN 2 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 159375
        SND                 ; CURS: face row 86, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 155659
        SND                 ; CURS: face row 86, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 163471
        SND                 ; CURS: face row 87, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 159755
        SND                 ; CURS: face row 87, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 167567
        SND                 ; CURS: face row 88, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 163851
        SND                 ; CURS: face row 88, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 171663
        SND                 ; CURS: face row 89, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 167947
        SND                 ; CURS: face row 89, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 175759
        SND                 ; CURS: face row 90, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 172043
        SND                 ; CURS: face row 90, column 64
        LDI 2659
        SND                 ; RUN 2 x colour 9
        LDI 2275
        SND                 ; RUN 2 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 179855
        SND                 ; CURS: face row 91, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 176139
        SND                 ; CURS: face row 91, column 64
        LDI 2659
        SND                 ; RUN 2 x colour 9
        LDI 2275
        SND                 ; RUN 2 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 183951
        SND                 ; CURS: face row 92, column 58
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 180235
        SND                 ; CURS: face row 92, column 64
        LDI 2147
        SND                 ; RUN 2 x colour 1
        LDI 4643
        SND                 ; RUN 4 x colour 8
        LDI 188047
        SND                 ; CURS: face row 93, column 58
        LDI 6695
        SND                 ; RUN 6 x colour 8
        LDI 184331
        SND                 ; CURS: face row 93, column 64
        LDI 2147
        SND                 ; RUN 2 x colour 1
        LDI 4643
        SND                 ; RUN 4 x colour 8
        JMP cmit
fbld:   LDI 134799          ; bloodied (stfst40)
        SND                 ; CURS: face row 80, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4135
        SND                 ; RUN 4 x colour 0
        LDI 131083
        SND                 ; CURS: face row 80, column 64
        LDI 4131
        SND                 ; RUN 4 x colour 0
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 138895
        SND                 ; CURS: face row 81, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4135
        SND                 ; RUN 4 x colour 0
        LDI 135179
        SND                 ; CURS: face row 81, column 64
        LDI 4131
        SND                 ; RUN 4 x colour 0
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 142991
        SND                 ; CURS: face row 82, column 58
        LDI 6183
        SND                 ; RUN 6 x colour 0
        LDI 139275
        SND                 ; CURS: face row 82, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 147087
        SND                 ; CURS: face row 83, column 58
        LDI 6183
        SND                 ; RUN 6 x colour 0
        LDI 143371
        SND                 ; CURS: face row 83, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 151183
        SND                 ; CURS: face row 84, column 58
        LDI 4135
        SND                 ; RUN 4 x colour 0
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 147467
        SND                 ; CURS: face row 84, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 155279
        SND                 ; CURS: face row 85, column 58
        LDI 4135
        SND                 ; RUN 4 x colour 0
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 151563
        SND                 ; CURS: face row 85, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 159375
        SND                 ; CURS: face row 86, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 155659
        SND                 ; CURS: face row 86, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 163471
        SND                 ; CURS: face row 87, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 159755
        SND                 ; CURS: face row 87, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 167567
        SND                 ; CURS: face row 88, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2663
        SND                 ; RUN 2 x colour 9
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 163851
        SND                 ; CURS: face row 88, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 171663
        SND                 ; CURS: face row 89, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2663
        SND                 ; RUN 2 x colour 9
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 167947
        SND                 ; CURS: face row 89, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 175759
        SND                 ; CURS: face row 90, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 172043
        SND                 ; CURS: face row 90, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 179855
        SND                 ; CURS: face row 91, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 4327
        SND                 ; RUN 4 x colour 3
        LDI 176139
        SND                 ; CURS: face row 91, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 183951
        SND                 ; CURS: face row 92, column 58
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 2663
        SND                 ; RUN 2 x colour 9
        LDI 180235
        SND                 ; CURS: face row 92, column 64
        LDI 2659
        SND                 ; RUN 2 x colour 9
        LDI 4643
        SND                 ; RUN 4 x colour 8
        LDI 188047
        SND                 ; CURS: face row 93, column 58
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 2663
        SND                 ; RUN 2 x colour 9
        LDI 184331
        SND                 ; CURS: face row 93, column 64
        LDI 2659
        SND                 ; RUN 2 x colour 9
        LDI 4643
        SND                 ; RUN 4 x colour 8
        JMP cmit
fgrim:  LDI 134799          ; the FIRE grimace (stfevl0)
        SND                 ; CURS: face row 80, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 131083
        SND                 ; CURS: face row 80, column 64
        LDI 4131
        SND                 ; RUN 4 x colour 0
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 138895
        SND                 ; CURS: face row 81, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 135179
        SND                 ; CURS: face row 81, column 64
        LDI 4131
        SND                 ; RUN 4 x colour 0
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 142991
        SND                 ; CURS: face row 82, column 58
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 139275
        SND                 ; CURS: face row 82, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 147087
        SND                 ; CURS: face row 83, column 58
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 2087
        SND                 ; RUN 2 x colour 0
        LDI 143371
        SND                 ; CURS: face row 83, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 151183
        SND                 ; CURS: face row 84, column 58
        LDI 4135
        SND                 ; RUN 4 x colour 0
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 147467
        SND                 ; CURS: face row 84, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 155279
        SND                 ; CURS: face row 85, column 58
        LDI 4135
        SND                 ; RUN 4 x colour 0
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 151563
        SND                 ; CURS: face row 85, column 64
        LDI 6179
        SND                 ; RUN 6 x colour 0
        LDI 159375
        SND                 ; CURS: face row 86, column 58
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 155659
        SND                 ; CURS: face row 86, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 163471
        SND                 ; CURS: face row 87, column 58
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 159755
        SND                 ; CURS: face row 87, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2083
        SND                 ; RUN 2 x colour 0
        LDI 167567
        SND                 ; CURS: face row 88, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 163851
        SND                 ; CURS: face row 88, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 171663
        SND                 ; CURS: face row 89, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 167947
        SND                 ; CURS: face row 89, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 175759
        SND                 ; CURS: face row 90, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 172043
        SND                 ; CURS: face row 90, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 179855
        SND                 ; CURS: face row 91, column 58
        LDI 2599
        SND                 ; RUN 2 x colour 8
        LDI 2279
        SND                 ; RUN 2 x colour 3
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 176139
        SND                 ; CURS: face row 91, column 64
        LDI 4323
        SND                 ; RUN 4 x colour 3
        LDI 2595
        SND                 ; RUN 2 x colour 8
        LDI 183951
        SND                 ; CURS: face row 92, column 58
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 180235
        SND                 ; CURS: face row 92, column 64
        LDI 6691
        SND                 ; RUN 6 x colour 8
        LDI 188047
        SND                 ; CURS: face row 93, column 58
        LDI 4647
        SND                 ; RUN 4 x colour 8
        LDI 2535
        SND                 ; RUN 2 x colour 7
        LDI 184331
        SND                 ; CURS: face row 93, column 64
        LDI 6691
        SND                 ; RUN 6 x colour 8

; ── the commit: one command word ─────────────────────────────────────────────
cmit:   LDI C_COMMIT
        SND                 ; SWAP 0: commit THE one frame of this round
        JMP round
