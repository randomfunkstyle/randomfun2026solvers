; plotter — draw one Bresenham segment per round on the 32x24 LM-75, bright white.
;
; NOT ISA v1: needs `NEG` and the three LM-75 port opcodes `DSPA`/`DSPD`/`DSPS`.
; ARCH.md §6's `DSP p` cannot be built — it picks a pipe from its *operand*, and
; which pipe an `s` talks to is fixed by where the glyph sits (§7.1). One opcode
; per port gives each its own lane beside its own pipe.
;
; The cheap trick here is not to paint the frame. Writing 0 to the SWAP port
; commits *and clears* `next`, so every round starts on a black buffer and only
; the segment's own pixels need writing: reposition the cursor with `DSPA`
; (cursor = y*32 + x), drop a 15 with `DSPD`. That is <= 32 pixels a round instead
; of all 768, and it is why this fits in a tick budget at all.
;
; Straight from the spec's pseudocode, in its symmetric error form, and
; direction-sensitive: drawn from (x0, y0) to (x1, y1) as given.
;
; Addresses start at 1: the generated hardware encodes the operation in the *sign*
; of the address word, so slot 0 would be ambiguous and is left unused.

.equ X0  1
.equ Y0  2
.equ X1  3
.equ Y1  4
.equ DX  5                  ;  abs(x1 - x0)
.equ DY  6                  ; -abs(y1 - y0)
.equ SX  7
.equ SY  8
.equ ERR 9
.equ E2  10

round:  IN
        ST  X0
        IN
        ST  Y0
        IN
        ST  X1
        IN
        ST  Y1

        ; dx = abs(x1 - x0)
        LD  X1
        SUB X0
        BRN dxneg
        ST  DX
        JMP sxset
dxneg:  NEG
        ST  DX

        ; sx = (x0 < x1) ? 1 : -1
sxset:  LD  X0
        SUB X1
        BRN sxpos
        LDI 0               ; the ROM holds no negative literal, so build -1
        SUBI 1
        ST  SX
        JMP dyset
sxpos:  LDI 1
        ST  SX

        ; dy = -abs(y1 - y0): if y1 - y0 is already negative it *is* -abs
        dyset:  LD  Y1
        SUB Y0
        BRN dykeep
        NEG
        ST  DY
        JMP syset
dykeep: ST  DY

        ; sy = (y0 < y1) ? 1 : -1
syset:  LD  Y0
        SUB Y1
        BRN sypos
        LDI 0
        SUBI 1
        ST  SY
        JMP errset
sypos:  LDI 1
        ST  SY

errset: LD  DX
        ADD DY
        ST  ERR

        ; ── the loop: plot, test, step ───────────────────────────────────────
plot:   LD  Y0
        MULI 32
        ADD X0
        DSPA                ; cursor = y0 * width + x0
        LDI 15
        DSPD                ; bright white, cursor advances (unused, we reposition)

        LD  X0
        SUB X1
        BRZ xeq
        JMP step
xeq:    LD  Y0
        SUB Y1
        BRZ commit          ; both equal -> the segment is finished

step:   LD  ERR
        MULI 2
        ST  E2
        SUB DY              ; ST preserved ACC, so this is e2 - dy
        BRN skipx           ; e2 < dy -> no x step
        LD  ERR
        ADD DY
        ST  ERR
        LD  X0
        ADD SX
        ST  X0

skipx:  LD  DX
        SUB E2
        BRN skipy           ; e2 > dx -> no y step
        LD  ERR
        ADD DX
        ST  ERR
        LD  Y0
        ADD SY
        ST  Y0

skipy:  JMP plot

commit: LDI 0
        DSPS                ; next -> current, clear next, reset the cursor
        JMP round
