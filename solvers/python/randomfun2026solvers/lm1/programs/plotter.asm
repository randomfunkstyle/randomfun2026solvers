; plotter — draw one Bresenham segment per round on the 32x24 LM-75, bright white.
;
; NOT ISA v1: needs `NEG`, `MODI` and the three LM-75 port opcodes
; `DSPA`/`DSPD`/`DSPS`. ARCH.md §6's `DSP p` cannot be built — it picks a pipe from
; its *operand*, and which pipe an `s` talks to is fixed by where the glyph sits
; (§7.1). One opcode per port gives each its own lane beside its own pipe.
;
; The cheap trick here is not to paint the frame. Writing 0 to the SWAP port
; commits *and clears* `next`, so every round starts on a black buffer and only
; the segment's own pixels need writing: reposition the cursor with `DSPA`
; (cursor = y*32 + x), drop a 15 with `DSPD`. That is <= 32 pixels a round instead
; of all 768.
;
; ── why the inner loop looks nothing like the spec's pseudocode ──────────────
;
; Measured on the engine, one tape access costs ~316 ticks against ~45 for an
; instruction and 8 per ROM word a taken jump recirculates. The spec's loop, written
; out literally, touches the tape ~20 times per pixel (five variables, three of them
; read-modify-written) and that was 75% of the bill — 5,311,321 ticks for the 20
; rounds the constraints allow, against a 5,000,000 step cap. So the whole design
; here is about *tape accesses per pixel*, and it gets them to four.
;
; Three transformations, each verified exhaustively against the spec's pseudocode
; over all 589,824 endpoint pairs (`test_plotter_draws_exactly_bresenham...`):
;
; 1. **One cursor, not two coordinates.** Bresenham's state (x0, y0) only ever
;    reaches the display as `addr = 32*y0 + x0`, and the loop's stop test
;    `x0 == x1 and y0 == y1` is exactly `addr == addr1` because the map is injective
;    on the panel. So carry `addr` and step it by `sx` / `32*sy` instead.
;
; 2. **Split on the major axis, so only one of the two error tests is live.** When
;    `dx >= |dy|` the test `e2 >= dy` is always true (x steps every iteration) and
;    when `|dy| >= dx` the test `e2 <= dx` always is. Whichever remains has two arms
;    — "step the major axis only" and "step both" — and each arm's whole effect is
;    *one addition of a per-round constant* to (err, addr). Both arms' constants are
;    computed once per round in the setup below.
;
; 3. **Pack err and addr into one word, so that addition is one add.** With
;    `q = 2*err*1024 + addr - THR` and `THR` a multiple of 1024:
;      * `addr = q mod 1024` — `MODI 1024`, no tape access, because addr < 1024;
;      * the surviving error test is exactly `sign(q)`, because THR is chosen as the
;        threshold ((dx+1)*1024 for x-major, dy*1024 for y-major) and shifting err by
;        a whole multiple of the radix cannot disturb the low field;
;      * an arm is `q += DEL1` or `q += DEL2`, a single word each.
;
; What is left per pixel is `ST Q` · `SUB ADDR1` · `LD Q` · `ADD DEL` — four accesses
; and eleven instructions — plus one taken jump. That jump is the other half of the
; cost: a backward jump recirculates `P - body` words at 8 ticks each, i.e. the ROM
; lap costs every iteration the *whole setup*, whether it runs or not (ARCH.md §5.4).
; Hence UNROLL below: two copies of the body halve the number of laps for the same
; setup, and cost only ROM cells.
;
; Addresses start at 1: the generated hardware encodes the operation in the *sign*
; of the address word, so slot 0 would be ambiguous and is left unused — an
; ``N``-slot tape therefore addresses 1..N-1. Ten names share ten slots by aliasing
; every pair whose live ranges do not overlap (`Y0`/`NDY`, `X1`/`DEL1`, `X0`/`DEL2`,
; `A0`/`Q`), which is what keeps this inside the N=11 tape: the tape is a rotating
; ring, so a slot is not free even though its footprint is.
;
; The machine that runs this is generated, not drawn:
;
;   python -m randomfun2026solvers.lm1.machine plotter --out tasks/solutions/plotter_cpu.man
;   node littleman/tools/display-frames.mjs tasks/solutions/plotter_cpu.man \
;        tasks/problems/plotter.json
;
; It never halts: after the last round `IN` simply blocks, which is a legal end
; state (GRADING.md — you do not have to halt).

; ── tape slots (ten names, ten slots: 1..10 of the N=11 tape) ────────────────
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

; ── per-round setup ──────────────────────────────────────────────────────────
round:  IN                  ; x0
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

; ── the loop: one add per pixel ──────────────────────────────────────────────
; `plotA`..`plotD` are four identical copies of the same eleven instructions, and
; the duplication is the point: only the *last* copy jumps backwards, so one ROM lap
; (and so one `8 * setup_words` tax) now covers four pixels instead of one. Measured
; on the engine over the worst legal 20-round load: 2,485,405 ticks at one copy,
; 2,075,485 at two, 1,894,525 at four, 1,846,233 at six — so four is where the curve
; flattens, and it is also where `footprint x avg_ticks` bottoms out.
;
; The `q < 0` arm is one instruction and sits immediately before the next copy, so it
; falls through for free and only the `q >= 0` arm pays a (two-word) jump.
;
; The stop test sits *before* the paint, which is what lets `addr` stay in ACC across
; it — `LDI 15` would clobber it. The endpoint pixel is therefore painted by `final`
; on the way out.
plotA:  ST  Q
        MODI 1024
        DSPA                ; cursor = addr
        SUB ADDR1
        BRZ final
        LDI 15
        DSPD                ; bright white, cursor advances (unused, we reposition)
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
        LDI 15
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
        LDI 15
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
        LDI 15
        DSPD
        LD  Q
        BRN diagD
        ADD DEL1
        JMP plotA
diagD:  ADD DEL2
        JMP plotA

final:  LDI 15
        DSPD                ; the endpoint pixel
        LDI 0
        DSPS                ; next -> current, clear next, reset the cursor
                            ; the ROM wraps here, straight back into `round`
