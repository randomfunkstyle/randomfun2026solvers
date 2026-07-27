; deadman-3d — GENERATED from randomfun2026solvers/deadman3d.py, do not hand-edit.
; Regenerate with:
;   from randomfun2026solvers.deadman3d import deadman3d_source
;   from randomfun2026solvers.lm1.programs import PROGRAM_DIR
;   (PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())
;
; lodev.org's raycaster_flat.cpp on the LM-1: DOOM's E1M1, quantized to a
; 32x32 grid, walked first person at 64x48 on the LM-75 — one frame per input
; word, and each word is a MUX of the keys held that frame: bit0 (1) W fwd,
; bit1 (2) S back, bit2 (4) A left, bit3 (8) D right, bit4 (16) space FIRE
; (muzzle-flash overlay); 0 idle, higher bits ignored. Turn first (A/D
; cancel), then move along the new heading (W/S cancel), then render.
; An ungraded demo — the slug borrows plotter's problem JSON for nothing
; but registration; its 64x48 panel is DISPLAY_OVERRIDE's, its input is its
; own, and its 136-slot STORE rides the grid_block man-memory (STORE_TIER),
; ~31 ticks an access.
;
; Round 0's input carries the whole data preamble (64 packed map half-columns,
; POW16, the 16 packed heading words, spawn state — deadman3d.preamble_words())
; followed by the first command: tables ride on INPUT because every ROM word
; taxes every backward jump by 8 ticks forever. The pixel contract is
; deadman3d.render(): every expression below is that model's, in its exact
; operation order.
;
; The map-cell lookup floor(MAPW[2x + y/16] / 16**(y mod 16)) mod 16 is
; inlined at its three sites (no stack, no calls): the two move-collision
; tests and the DDA hit test.

; ── tape slots (deadman3d.tape_slots(); slots 1..103 are the boot data) ──────
.equ MAPB   1            ; ..64  packed map half-columns: word 2x+(y/16), nibble y mod 16
.equ POWB   65           ; ..80  16**k — the nibble-extraction divisors
.equ HDGB   81           ; ..96  packed headings: base-4096 digits dirX dirY planeX planeY, biased +1024
.equ POSX   97           ; player x, Q10 (lodev posX)
.equ POSY   98           ; player y, Q10 (lodev posY)
.equ HDG    99           ; heading 0..15 (22.5 deg steps, CCW from east)
.equ DIRX   100          ; lodev dirX
.equ DIRY   101          ; lodev dirY
.equ PLANEX 102          ; lodev planeX
.equ PLANEY 103          ; lodev planeY
.equ CMD    104          ; this round's command word
.equ XCOL   105          ; the column being rendered (lodev x)
.equ CAMX   106          ; lodev cameraX, Q10
.equ RDX    107          ; lodev rayDirX
.equ RDY    108          ; lodev rayDirY
.equ MAPX   109          ; lodev mapX
.equ MAPY   110          ; lodev mapY
.equ SDX    111          ; lodev sideDistX
.equ SDY    112          ; lodev sideDistY
.equ DDX    113          ; lodev deltaDistX
.equ DDY    114          ; lodev deltaDistY
.equ STPX   115          ; lodev stepX
.equ STPY   116          ; lodev stepY
.equ SIDE   117          ; lodev side (0 = x-side hit)
.equ PERP   118          ; lodev perpWallDist
.equ HALFH  119          ; lodev lineHeight / 2
.equ DSTART 120          ; lodev drawStart
.equ DEND   121          ; lodev drawEnd
.equ COLOR  122          ; the wall type t, then the shaded colour
.equ ADDRV  123          ; the paint cursor, row*64 + XCOL
.equ AEND   124          ; the paint loop's last address
.equ PW     125          ; 16**(mapY mod 16) during a cell lookup
.equ TMP    126          ; scratch (s, frac, packed word)
.equ TMP2   127          ; scratch (the cell lookup's half-column selector)
.equ NEWX   128          ; the candidate posX
.equ NEWY   129          ; the candidate posY
.equ BW     130          ; key bit 0 (1): W, forward
.equ BS     131          ; key bit 1 (2): S, backward
.equ BA     132          ; key bit 2 (4): A, turn left
.equ BD     133          ; key bit 3 (8): D, turn right
.equ FIRE   134          ; key bit 4 (16): space held — paint FLASH over this frame
.equ PTR    135          ; the boot loop's tape cursor

; ── boot: round 0's data preamble -> tape slots 1..103 ───────────────────────
        LDI 1
        ST  PTR
boot:   IN                  ; the next preamble word
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        LD  PTR
        SUBI 104
        BRN boot            ; keep loading while PTR < 104

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

; ── turn first (lodev's order): heading += A - D, cancelling when both held ──
        LD  BA
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
mvchk:  LD  BW
        SUB BS
        BRZ render          ; no net move: just render
        ST  TMP             ; s = +1 forward, -1 backward
        LD  DIRX
        MUL TMP
        DIVI 1              ; floor(dirX * s * 1 / 1) — the whole-cell step
        ADD POSX
        ST  NEWX            ; newX
        ; collision X: map_cell(newX / 1024, posY / 1024), inlined
        LD  POSY
        DIVI 1024
        ST  TMP2            ; mapY (ST preserves ACC)
        MODI 16
        ADDI POWB
        LDA
        ST  PW              ; 16**(mapY mod 16)
        LD  TMP2
        DIVI 16
        ST  TMP2            ; the half-column selector, mapY / 16
        LD  NEWX
        DIVI 1024
        MULI 2
        ADD TMP2
        ADDI MAPB
        LDA                 ; the packed half-column of newX's cell
        DIV PW
        MODI 16
        BRZ comx            ; empty -> commit posX
        JMP movey           ; wall -> posX unchanged
comx:   LD  NEWX
        ST  POSX
movey:  LD  DIRY
        MUL TMP
        DIVI 1
        ADD POSY
        ST  NEWY            ; newY
        ; collision Y: map_cell(posX / 1024, newY / 1024) — the UPDATED posX
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
        MULI 2
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
render: LDI 0
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
        LD  POSX
        DIVI 1024
        ST  MAPX            ; mapX = int(posX)
        LD  POSY
        DIVI 1024
        ST  MAPY            ; mapY = int(posY)
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
        ; stepX / sideDistX from the fractional position (lodev's two arms)
sidex:  LD  POSX
        MODI 1024
        ST  TMP             ; fracX = posX - mapX*1024
        LD  RDX
        BRN sxneg
        LDI 1
        ST  STPX            ; stepX = 1
        LDI 1024
        SUB TMP
        MUL DDX
        DIVI 1024
        ST  SDX             ; sideDistX = (1024 - fracX) * deltaDistX / 1024
        JMP sidey
sxneg:  LDI 0
        SUBI 1
        ST  STPX            ; stepX = -1
        LD  TMP
        MUL DDX
        DIVI 1024
        ST  SDX             ; sideDistX = fracX * deltaDistX / 1024
sidey:  LD  POSY            ; stepY / sideDistY, the same two arms
        MODI 1024
        ST  TMP
        LD  RDY
        BRN syneg
        LDI 1
        ST  STPY
        LDI 1024
        SUB TMP
        MUL DDY
        DIVI 1024
        ST  SDY
        JMP dda
syneg:  LDI 0
        SUBI 1
        ST  STPY
        LD  TMP
        MUL DDY
        DIVI 1024
        ST  SDY
        ; the DDA; a sideDist tie goes to the Y arm (lodev's else — risk R5)
dda:    LD  SDX
        SUB SDY
        BRN xarm            ; sideDistX < sideDistY -> step in x
        LD  SDY
        ADD DDY
        ST  SDY
        LD  MAPY
        ADD STPY
        ST  MAPY
        LDI 1
        ST  SIDE            ; side = 1 (y-side)
        JMP hit
xarm:   LD  SDX
        ADD DDX
        ST  SDX
        LD  MAPX
        ADD STPX
        ST  MAPX
        LDI 0
        ST  SIDE            ; side = 0 (x-side)
hit:    LD  MAPY            ; the inlined cell lookup at (mapX, mapY)
        ST  TMP2
        MODI 16
        ADDI POWB
        LDA
        ST  PW              ; 16**(mapY mod 16)
        LD  TMP2
        DIVI 16
        ST  TMP2            ; the half-column selector, mapY / 16
        LD  MAPX
        MULI 2
        ADD TMP2
        ADDI MAPB
        LDA
        DIV PW
        MODI 16
        BRZ dda             ; empty -> keep stepping (the backward lap)
        ST  COLOR           ; hit: the wall type t in 1..7
        ; perpWallDist = sideDist - deltaDist of the hit side, clamped to >= 1
        LD  SIDE
        BRZ perpx
        LD  SDY
        SUB DDY
        ST  PERP
        JMP pclip
perpx:  LD  SDX
        SUB DDX
        ST  PERP
pclip:  SUBI 1              ; ST preserved ACC = perpWallDist
        BRN pone
        JMP lineh
pone:   LDI 1
        ST  PERP
lineh:  LDI 40960
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
        BRN shade           ; drawEnd <= 39: no clamp
        LDI 39
        ST  DEND
shade:  LD  SIDE            ; lodev halves y-side colours; here x-side is t + 8
        BRZ sunlit
        JMP paint
sunlit: LD  COLOR
        ADDI 8
        ST  COLOR           ; the sunlit (bright) variant
paint:  LD  DSTART          ; the wall run: rows drawStart..drawEnd, stride 64
        MULI 64
        ADD XCOL
        ST  ADDRV
        LD  DEND
        MULI 64
        ADD XCOL
        ST  AEND
wallp:  LD  ADDRV
        DSPA
        LD  COLOR
        DSPD
        LD  ADDRV
        ADDI 64
        ST  ADDRV
        SUB AEND
        BRN wallp           ; next row while ADDRV <= AEND
        BRZ wallp
        ; the floor run: rows drawEnd+1..39 paint colour 8 (ceiling stays
        ; black — SWAP 0 cleared the next buffer)
        LDI 2496
        ADD XCOL
        ST  AEND            ; row 39, this column
floorp: LD  AEND
        SUB ADDRV
        BRN colnxt          ; ADDRV past row 39: the column is done
        LD  ADDRV
        DSPA
        LDI 8
        DSPD
        LD  ADDRV
        ADDI 64
        ST  ADDRV
        JMP floorp
colnxt: INCM XCOL           ; ACC = the old column number
        SUBI 63
        BRZ flash           ; that was column 63: the viewport is painted
        JMP colset

; ── muzzle flash: FLASH's 8 pixels, only when this round FIREd ────────────
flash:  LD  FIRE
        BRZ hud
        LDI 2271
        DSPA                ; row 35, column 31
        LDI 11
        DSPD
        DSPD
        LDI 2334
        DSPA                ; row 36, column 30
        LDI 11
        DSPD
        LDI 15
        DSPD
        DSPD
        LDI 11
        DSPD
        LDI 2399
        DSPA                ; row 37, column 31
        LDI 11
        DSPD
        DSPD

; ── HUD strip (rows 40..47): RLE runs generated from hud_runs() ──────────
hud:    LDI 2560
        DSPA                ; park the cursor at row 40, column 0
        LDI 7               ; a run of 64
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 4
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 9               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 15
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 11               ; a run of 8
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 14
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 12               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 9               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 15
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 11               ; a run of 8
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 14
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 12               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 9               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 15
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 11               ; a run of 8
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 14
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 12               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 9               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 15
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 11               ; a run of 8
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 14
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 12               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 9               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 15
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 11               ; a run of 8
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 14
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 12               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 9               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 15
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 11               ; a run of 8
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 14
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 12               ; a run of 9
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        LDI 8               ; a run of 69
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD
        DSPD

        LDI 0
        DSPS                ; commit THE one frame of this round
        JMP round
