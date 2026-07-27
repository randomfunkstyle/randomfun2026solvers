; deadman-3d — GENERATED from randomfun2026solvers/deadman3d.py, do not hand-edit.
; Regenerate with:
;   from randomfun2026solvers.deadman3d import deadman3d_source
;   from randomfun2026solvers.lm1.programs import PROGRAM_DIR
;   (PROGRAM_DIR / "deadman-3d.asm").write_text(deadman3d_source())
;
; lodev.org's raycaster_flat.cpp on the LM-1: 64x48 first person on the LM-75,
; one frame per input command (0 fwd, 1 back, 2 left, 3 right, >= 4 no-op).
; An ungraded demo — the slug borrows plotter's problem JSON for nothing but
; registration; its 64x48 panel is DISPLAY_OVERRIDE's, its input is its own.
;
; Round 0's input carries the whole data preamble (map rows, POW16, heading
; tables, spawn state — deadman3d.preamble_words()) followed by the first
; command: tables ride on INPUT because every ROM word taxes every backward
; jump by 8 ticks forever, and because the ROM cannot hold the negative
; components (planeY = -676 at spawn). The pixel contract is deadman3d.render():
; every expression below is that model's, in its exact operation order.
;
; The map-cell lookup floor(MAPROW[x] / 16**y) mod 16 is inlined at its three
; sites (no stack, no calls): the two move-collision tests and the DDA hit test.

; ── tape slots (deadman3d.tape_slots(); slots 1..71 are the boot data) ───────
.equ MAPB   1            ; ..16  packed map rows: nibble y of word x = map_cell(x, y)
.equ POWB   17           ; ..32  16**y — the nibble-extraction divisors
.equ DIRB   33           ; ..48  packed dir vectors, (dirX+1024)*4096 + (dirY+1024)
.equ PLNB   49           ; ..64  packed plane vectors, same packing
.equ POSX   65           ; player x, Q10 (lodev posX)
.equ POSY   66           ; player y, Q10 (lodev posY)
.equ HDG    67           ; heading 0..15 (22.5 deg steps, CCW from east)
.equ DIRX   68           ; lodev dirX
.equ DIRY   69           ; lodev dirY
.equ PLANEX 70           ; lodev planeX
.equ PLANEY 71           ; lodev planeY
.equ CMD    72           ; this round's command word
.equ XCOL   73           ; the column being rendered (lodev x)
.equ CAMX   74           ; lodev cameraX, Q10
.equ RDX    75           ; lodev rayDirX
.equ RDY    76           ; lodev rayDirY
.equ MAPX   77           ; lodev mapX
.equ MAPY   78           ; lodev mapY
.equ SDX    79           ; lodev sideDistX
.equ SDY    80           ; lodev sideDistY
.equ DDX    81           ; lodev deltaDistX
.equ DDY    82           ; lodev deltaDistY
.equ STPX   83           ; lodev stepX
.equ STPY   84           ; lodev stepY
.equ SIDE   85           ; lodev side (0 = x-side hit)
.equ PERP   86           ; lodev perpWallDist
.equ HALFH  87           ; lodev lineHeight / 2
.equ DSTART 88           ; lodev drawStart
.equ DEND   89           ; lodev drawEnd
.equ COLOR  90           ; the wall type t, then the shaded colour
.equ ADDRV  91           ; the paint cursor, row*64 + XCOL
.equ AEND   92           ; the paint loop's last address
.equ PW     93           ; 16**mapY during a cell lookup
.equ TMP    94           ; scratch (s, frac, packed word)
.equ NEWX   95           ; the candidate posX
.equ NEWY   96           ; the candidate posY
.equ PTR    97           ; the boot loop's tape cursor

; ── boot: round 0's data preamble -> tape slots 1..71 ────────────────────────
        LDI 1
        ST  PTR
boot:   IN                  ; the next preamble word (negatives arrive here)
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the word
        INCM PTR
        LD  PTR
        SUBI 72
        BRN boot            ; keep loading while PTR < 72

; ── round: one command word in, exactly one committed frame out ──────────────
round:  IN                  ; blocks here when the walk is over (the legal end)
        ST  CMD
        BRZ fwd             ; 0 = forward
        SUBI 1
        BRZ back            ; 1 = backward
        SUBI 1
        BRZ left            ; 2 = turn left  (CCW, +1 heading)
        SUBI 1
        BRZ right           ; 3 = turn right (-1 = +15 mod 16)
        JMP render          ; >= 4 = no-op: just render

; ── move arms (lodev: pos += dir * moveSpeed, collision per axis) ────────────
fwd:    LDI 1
        ST  TMP             ; s = +1
        JMP move
back:   LDI 0
        SUBI 1
        ST  TMP             ; s = -1 (no negative ROM literals)
move:   LD  DIRX
        MUL TMP
        DIVI 2              ; floor(dirX * s / 2) — the half-cell step
        ADD POSX
        ST  NEWX            ; newX
        ; collision X: map_cell(newX / 1024, posY / 1024), inlined
        LD  POSY
        DIVI 1024
        ADDI POWB
        LDA
        ST  PW              ; 16**mapY
        LD  NEWX
        DIVI 1024
        ADDI MAPB
        LDA                 ; the packed map row of newX's cell
        DIV PW
        MODI 16
        BRZ comx            ; empty -> commit posX
        JMP movey           ; wall -> posX unchanged
comx:   LD  NEWX
        ST  POSX
movey:  LD  DIRY
        MUL TMP
        DIVI 2
        ADD POSY
        ST  NEWY            ; newY
        ; collision Y: map_cell(posX / 1024, newY / 1024) — the UPDATED posX
        LD  NEWY
        DIVI 1024
        ADDI POWB
        LDA
        ST  PW
        LD  POSX
        DIVI 1024
        ADDI MAPB
        LDA
        DIV PW
        MODI 16
        BRZ comy
        JMP render
comy:   LD  NEWY
        ST  POSY
        JMP render

; ── turn arms: heading +-1 mod 16, dir/plane re-unpacked from the tables ─────
left:   LD  HDG
        ADDI 1
        MODI 16
        ST  HDG
        JMP unpk
right:  LD  HDG
        ADDI 15
        MODI 16
        ST  HDG
unpk:   LD  HDG
        ADDI DIRB
        LDA                 ; (dirX+1024)*4096 + (dirY+1024)
        ST  TMP
        MODI 4096
        SUBI 1024
        ST  DIRY
        LD  TMP
        DIVI 4096
        SUBI 1024
        ST  DIRX
        LD  HDG
        ADDI PLNB
        LDA
        ST  TMP
        MODI 4096
        SUBI 1024
        ST  PLANEY
        LD  TMP
        DIVI 4096
        SUBI 1024
        ST  PLANEX
        ; falls through to render

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
        ADDI POWB
        LDA
        ST  PW
        LD  MAPX
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
        BRZ hud             ; that was column 63: the viewport is painted
        JMP colset

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
