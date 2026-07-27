; doom-screen — a general vector-display CPU: read coloured segments, draw one frame.
;
; This is `plotter.asm` refitted as a *vector display processor* (an ungraded demo,
; not a contest problem — the slug maps onto `plotter`'s problem JSON only for the
; 32x24 panel resolution). The demo input is the DOOM (1993) title screen decomposed
; into horizontal runs by `randomfun2026solvers/doom_vector.py`; the point is that we
; already have a plotter, so a full bitmap is just line data on its input pipe.
;
; Protocol, per segment: `x0 y0 x1 y1 colour` — five words, colour 0..15 — repeated,
; then a single negative word (the end-of-drawing sentinel). Three deltas from
; plotter, everything else (the packed-cursor Bresenham, the 4x unroll, the aliased
; tape slots) is unchanged and documented there:
;
; 1. **Sentinel.** The round header tests x0's sign: negative means "the drawing is
;    finished", and control falls out to `fin`.
; 2. **Colour is data.** Each round reads one extra word into COL, and every paint is
;    `LD COL` where plotter had `LDI 15`. That is a fifth tape access per pixel —
;    fine here, this machine is judged on nothing.
; 3. **One frame.** `final` paints the endpoint and jumps straight back to `round`
;    *without* touching SWAP, so segments accumulate in the display's `next` buffer
;    (which starts black — colour-0 runs are therefore never sent at all). `fin`
;    commits once with `DSPS` and HALTs. Seventeen opcodes, so a depth-5 trie —
;    the demo pays one trie level for its HALT and does not care.
;
; The machine that runs this is generated, not drawn:
;
;   python -m randomfun2026solvers.lm1.machine doom-screen \
;        --man littleman/examples/doom-screen-cpu.man
;   node littleman/tools/display-frames.mjs littleman/examples/doom-screen-cpu.man \
;        littleman/examples/doom-screen-cpu.cases.json 40000000

; ── tape slots (eleven names, eleven slots: 1..11 of the N=12 tape) ──────────
.equ ADDR1  1               ; 32*y1 + x1, the stop test
.equ A0     2               ; 32*y0 + x0 …
.equ Q      2               ; … then the packed cursor 2*err*1024 + addr - THR
.equ ADX    3               ; abs(x1 - x0)
.equ Y0     4               ; y0 …
.equ NDY    4               ; … then abs(y1 - y0), once dyr has been taken
.equ X1     5               ; x1 …
.equ DEL1   5               ; … then the q >= 0 arm's whole step
.equ X0     6               ; x0 …
.equ DEL2   6               ; … then the q < 0 arm's whole step
.equ SXV    7               ; sx
.equ SYV    8               ; 32*sy
.equ A2     9               ; 2048*dx  — the error delta of an x step, pre-shifted
.equ N2     10              ; 2048*|dy| — likewise for a y step
.equ COL    11              ; the segment's colour — live across the whole round,
                            ; so it aliases nothing

; ── per-round setup ──────────────────────────────────────────────────────────
round:  IN                  ; x0, or the negative end-of-drawing sentinel
        BRN fin
        ST  X0
        IN                  ; y0
        ST  Y0
        MULI 32
        ADD X0
        ST  A0              ; addr0 = 32*y0 + x0

        IN                  ; x1
        ST  X1
        IN                  ; y1
        SUB Y0              ; dyr = y1 - y0 — Y0 is dead from here, NDY takes its slot
        ST  NDY
        MULI 32
        ADD A0
        ADD X1
        SUB X0              ; addr1 = addr0 + 32*dyr + (x1 - x0)
        ST  ADDR1

        IN                  ; the segment's colour, 0..15
        ST  COL

        ; sx = (x0 < x1) ? 1 : -1, and dx = abs(x1 - x0), from one sign test
        LD  X1
        SUB X0              ; dxr — X0/X1 are dead from here, DEL1/DEL2 take their slots
        ST  ADX
        BRZ sxneg           ; dxr == 0 -> sx = -1 and dx = 0, already stored
        BRN sxflip
        LDI 1
        ST  SXV
        JMP ycalc
sxflip: NEG
        ST  ADX
sxneg:  LDI 0
        SUBI 1              ; the ROM holds no negative literal, so build -1
        ST  SXV

        ; sy = (y0 < y1) ? 1 : -1, kept pre-multiplied by the panel width
ycalc:  LD  NDY             ; still dyr at this point
        BRZ syneg
        BRN syflip
        LDI 32
        ST  SYV
        JMP scale
syflip: NEG
        ST  NDY
syneg:  LDI 0
        SUBI 32
        ST  SYV

scale:  LD  ADX
        MULI 2048
        ST  A2
        LD  NDY
        MULI 2048
        ST  N2
        LD  ADX
        SUB NDY
        BRN ymajor          ; dx < |dy| -> y is the major axis

        ; ── x-major: x steps every iteration, THR = (dx + 1) * 1024 ──────────
        LD  SXV
        SUB N2
        ST  DEL1            ; q >= 0: x only          — err += 2*dy, addr += sx
        LD  A2
        SUB N2
        ADD SXV
        ADD SYV
        ST  DEL2            ; q <  0: both            — err += 2*(dx+dy), addr += sx + 32*sy
        LD  ADX
        MULI 1024
        SUB N2
        SUBI 1024
        ADD A0              ; q = 1024*dx - 2048*|dy| - 1024 + addr0
        JMP plotA

        ; ── y-major: y steps every iteration, THR = dy * 1024 ────────────────
ymajor: LD  A2
        SUB N2
        ADD SXV
        ADD SYV
        ST  DEL1            ; q >= 0: both
        LD  A2
        ADD SYV
        ST  DEL2            ; q <  0: y only          — err += 2*dx, addr += 32*sy
        LD  NDY
        MULI 1024
        NEG
        ADD A2
        ADD A0              ; q = 2048*dx - 1024*|dy| + addr0
                            ; falls through into the loop with q in ACC

; ── the loop: one add per pixel (see plotter.asm on the unroll and the packing) ──
plotA:  ST  Q
        MODI 1024
        DSPA                ; cursor = addr
        SUB ADDR1
        BRZ final
        LD  COL
        DSPD                ; the segment's colour; the cursor advance is unused
        LD  Q
        BRN diagA
        ADD DEL1
        JMP plotB           ; over the other arm's single instruction
diagA:  ADD DEL2

plotB:  ST  Q
        MODI 1024
        DSPA
        SUB ADDR1
        BRZ final
        LD  COL
        DSPD
        LD  Q
        BRN diagB
        ADD DEL1
        JMP plotC
diagB:  ADD DEL2

plotC:  ST  Q
        MODI 1024
        DSPA
        SUB ADDR1
        BRZ final
        LD  COL
        DSPD
        LD  Q
        BRN diagC
        ADD DEL1
        JMP plotD
diagC:  ADD DEL2

plotD:  ST  Q
        MODI 1024
        DSPA
        SUB ADDR1
        BRZ final
        LD  COL
        DSPD
        LD  Q
        BRN diagD
        ADD DEL1
        JMP plotA
diagD:  ADD DEL2
        JMP plotA

final:  LD  COL
        DSPD                ; the endpoint pixel
        JMP round           ; NO DSPS: the segment stays in `next`, uncommitted

fin:    LDI 0
        DSPS                ; the one commit: next -> current, whole frame at once
        HALT
