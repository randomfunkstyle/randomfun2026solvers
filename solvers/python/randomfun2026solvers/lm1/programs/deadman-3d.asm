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
; lm1/d3_unit.py), its input is its own, and its 328-slot STORE rides the
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
; POW16, the 16 packed heading words, spawn state — deadman3d.preamble_words())
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

; ── tape slots (deadman3d.tape_slots(); slots 1..295 are the boot data) ──────
.equ MAPB   1            ; ..256 packed map quarter-columns: word 4x+(y/16), nibble y mod 16
.equ POWB   257          ; ..272 16**k — the nibble-extraction divisors
.equ HDGB   273          ; ..288 packed headings: base-4096 digits dirX dirY planeX planeY, biased +1024
.equ POSX   289          ; player x, Q10 (lodev posX)
.equ POSY   290          ; player y, Q10 (lodev posY)
.equ HDG    291          ; heading 0..15 (22.5 deg steps, CCW from east)
.equ DIRX   292          ; lodev dirX
.equ DIRY   293          ; lodev dirY
.equ PLANEX 294          ; lodev planeX
.equ PLANEY 295          ; lodev planeY
.equ CMD    296          ; this round's command word
.equ XCOL   297          ; the column being rendered (lodev x)
.equ CAMX   298          ; lodev cameraX, Q10
.equ RDX    299          ; lodev rayDirX
.equ RDY    300          ; lodev rayDirY
.equ SDX    301          ; lodev sideDistX
.equ SDY    302          ; lodev sideDistY
.equ DDX    303          ; lodev deltaDistX
.equ DDY    304          ; lodev deltaDistY
.equ S4X    305          ; 4*stepX: the word address moves +-4 per x-step
.equ STPY   306          ; lodev stepY (the sign picks the PW shift arm)
.equ PERP   307          ; lodev perpWallDist
.equ HALFH  308          ; lodev lineHeight / 2
.equ DSTART 309          ; lodev drawStart
.equ DEND   310          ; lodev drawEnd
.equ COLOR  311          ; the wall type t, then the shaded colour
.equ PW     312          ; 16**(mapY mod 16), maintained incrementally across DDA steps
.equ WADDR  313          ; MAPB + 4*mapX + mapY/16, maintained incrementally too
.equ FRACX  314          ; posX mod 1024, hoisted per frame
.equ FRACY  315          ; posY mod 1024
.equ PW0    316          ; PW's per-frame seed (the player's own cell)
.equ WADDR0 317          ; WADDR's per-frame seed
.equ TMP    318          ; scratch (s, frac, packed word)
.equ TMP2   319          ; scratch (the cell lookup's quarter-column selector)
.equ NEWX   320          ; the candidate posX
.equ NEWY   321          ; the candidate posY
.equ BW     322          ; key bit 0 (1): W, forward
.equ BS     323          ; key bit 1 (2): S, backward
.equ BA     324          ; key bit 2 (4): A, turn left
.equ BD     325          ; key bit 3 (8): D, turn right
.equ FIRE   326          ; key bit 4 (16): space held — fire the pistol this frame
.equ AMMO   327          ; live rounds left: starts 50, -1 per shot, floor 0
.equ HEALTH 328          ; static 100 until the demo grows damage
.equ PTR    329          ; the boot loop's tape cursor

; ── the DOOM unit (lm1/d3_unit.py): 8*arg + code, codes read off its trie ────
.unit doom
.equ C_COL    0            ; arg=((top*64+col)*16+colour-1024)*64 + (bot-top+1): wall, then floor
.equ C_RUN    4            ; arg=count*16+colour: count pixels at the panel's own cursor
.equ C_CURS   1            ; arg=addr: reposition the panel cursor (the RLE painter's ADDR)
.equ C_GUN    3            ; arg=0: the baked idle pistol sprite (rows 30..39)
.equ C_GUNF   6            ; arg=0: the recoil pistol + muzzle flash (rows 25..38)
.equ C_COMMIT 7            ; arg=0: SWAP 0 — commit the frame, clear next, reset the cursor

; ── boot: round 0's data preamble -> tape slots 1..295, the loop unrolled 8x ──
; (a backward jump costs 8*(P - loop) ticks, so 36 laps beat 295; the last
; 7 slots are loaded straight-line at their own addresses)
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
        SUBI 289
        BRN boot            ; keep looping while PTR < 289
        IN
        ST  POSX
        IN
        ST  POSY
        IN
        ST  HDG
        IN
        ST  DIRX
        IN
        ST  DIRY
        IN
        ST  PLANEX
        IN
        ST  PLANEY


; ── title: Freedoom's title art (titlepic @ d14dbbe) — round 0's one frame ───
; The next 429 input words are PRE-ENCODED unit commands (title_words():
; one RUN word per RLE run of TITLE_HEX_ROWS, 8*(count*16 + colour) + C_RUN),
; so the CPU forwards each word untouched — IN; SND, 8 pairs per counted
; lap (53 laps + 5 straight-line pairs) — and the unit paints the runs at the panel's
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
        SUBI 53
        BRN title           ; keep looping while PTR < 53
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
        ST  HEALTH          ; static until the demo grows damage


; ── round: one key-bitmask word in, exactly one committed frame out ──────────
; The MUX decode: bits peeled low to high with a MODI 2 / DIVI 2 ladder, so
; every word — junk and high bits included — decodes exactly as the golden
; model's step() does.
round:  IN                  ; blocks here when the walk is over (the legal end)
        ST  CMD             ; ST preserves ACC
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

; ── render: lodev's per-column raycast, columns 0..63 ──────────────────────
; The per-frame prologue: everything that depends only on the player's position
; is computed once — the fractional position, and the cell-lookup seeds PW0 (the
; nibble divisor 16**(mapY mod 16)) and WADDR0 (the packed quarter-column's slot,
; MAPB + 4*mapX + mapY/16). The DDA then maintains PW/WADDR *incrementally*, so
; the per-step lookup is LDA/DIV/MODI instead of the full 16-instruction unpack.
render: LD  POSX
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
        MULI 32
        SUBI 1024
        ST  CAMX            ; cameraX = 2*x/w - 1 -> 32*x - 1024, exact at w = 64
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
        JMP nearck
pone:   LDI 1
        ST  PERP
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
lineh:  LDI 81920
        DIV PERP            ; lineHeight = h / perpWallDist -> (40*1024) / perp
        DIVI 2
        ST  HALFH
        LDI 20
        SUB HALFH
        ST  DSTART          ; drawStart = 20 - halfh
        BRN dslo
        JMP dehi
dslo:   LDI 0
        ST  DSTART          ; clamped at the top of the viewport
dehi:   LD  HALFH
        ADDI 20
        ST  DEND            ; drawEnd = 20 + halfh
        SUBI 40
        BRN send            ; drawEnd <= 39: no clamp
        LDI 39
        ST  DEND
        ; (no shade block: whx/why already picked the sunlit or dark colour)
        ; the whole column is ONE command word to the unit, which paints the wall
        ; run (drawStart..drawEnd in COLOR) and the floor run (drawEnd+1..39
        ; in 8) at stride 64 while the CPU raycasts the next column; the
        ; ceiling stays black because COMMIT cleared the next buffer. The arg is
        ; the unit's own loop seed: seed = (drawStart*64 + x)*16 + colour - 1024
        ; (its wall lap adds 1024 *before* painting), then arg = seed*64 + n_wall
send:   LD  DSTART
        MULI 64
        ADD XCOL
        MULI 16
        ADD COLOR
        SUBI 1024           ; seed (may go negative in the top row: that is fine)
        MULI 64
        ADD DEND
        SUB DSTART
        ADDI 1              ; arg = seed*64 + (drawEnd - drawStart + 1)
        MULI 8              ; the command word: 8*arg + C_COL, and C_COL == 0
        SND
colnxt: INCM XCOL           ; ACC = the old column number
        SUBI 63
        BRZ gun             ; that was column 63: the viewport is sent
        JMP colset

; ── the pistol (V4): ONE command word — the unit bakes both sprites ──────────
gun:    LD  FIRE
        BRZ gidle
        LDI C_GUNF
        SND                 ; the recoil frame, muzzle flash blooming above
        JMP hud
gidle:  LDI C_GUN
        SND                 ; the idle pistol, bottom-centre

; ── HUD (V4): cursor to slot 2560, the background as 14 pre-encoded RUN
; words (hud_bg_runs(): bezel, base field, the static blue armor block),
; then the LIVE bars over it — red health rows 41..42 (1px per 4), yellow
; ammo rows 44..45 (1px per 2), both from column 4; an empty bar
; sends nothing and the background shows through
hud:    LDI 20481
        SND                 ; CURS: the panel cursor to the strip's top-left
        LDI 8252
        SND                 ; RUN 64 x colour 7
        LDI 6468
        SND                 ; RUN 50 x colour 8
        LDI 1252
        SND                 ; RUN 9 x colour 12
        LDI 7108
        SND                 ; RUN 55 x colour 8
        LDI 1252
        SND                 ; RUN 9 x colour 12
        LDI 7108
        SND                 ; RUN 55 x colour 8
        LDI 1252
        SND                 ; RUN 9 x colour 12
        LDI 7108
        SND                 ; RUN 55 x colour 8
        LDI 1252
        SND                 ; RUN 9 x colour 12
        LDI 7108
        SND                 ; RUN 55 x colour 8
        LDI 1252
        SND                 ; RUN 9 x colour 12
        LDI 7108
        SND                 ; RUN 55 x colour 8
        LDI 1252
        SND                 ; RUN 9 x colour 12
        LDI 8900
        SND                 ; RUN 69 x colour 8

        LD  HEALTH
        DIVI 4
        ST  TMP             ; the health bar in pixels
        BRZ abar
        LDI 21025
        SND                 ; CURS: row 41, column 4
        LD  TMP
        MULI 16
        ADDI 9
        MULI 8
        ADDI C_RUN
        ST  TMP2            ; the bar's RUN word — reused for its second row
        SND
        LDI 21537
        SND
        LD  TMP2
        SND
abar:   LD  AMMO
        DIVI 2
        ST  TMP             ; the ammo bar in pixels
        BRZ cmit            ; clip empty: no bar at all
        LDI 22561
        SND
        LD  TMP
        MULI 16
        ADDI 11
        MULI 8
        ADDI C_RUN
        ST  TMP2
        SND
        LDI 23073
        SND
        LD  TMP2
        SND

; ── the commit: one command word ─────────────────────────────────────────────
cmit:   LDI C_COMMIT
        SND                 ; SWAP 0: commit THE one frame of this round
        JMP round
