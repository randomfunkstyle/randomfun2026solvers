; snake — Semester 4. Simulate Snake on the 16x16 LM-75 and commit a frame per round.
;
; The whole point of this program is what it does *not* do: it never repaints the
; panel. Writing 1 to SWAP commits `next` into `current` while keeping `next` and
; the cursor (verified on the engine), so the panel is a persistent framebuffer.
; A tick therefore costs exactly two pixels — erase the tail, draw the head —
; instead of 256, and a fruit spawn costs one.
;
; Frames are the output, so the round gating is on commits: exactly one SWAP per
; start / fruit / tick round, and *none* on a direction round (2/3/4/5). Committing
; on a direction round desynchronises every later frame; see GRADING.md § Rounds.
;
; State lives on cell indices, not (x, y): a cell is `y * 16 + x`, which is also
; the display's own ADDR word, so the head needs no unpacking to be drawn.
;
; Direction is three slots — DELTA, Q, R — because the wall test has to be a
; single compare. Moving off the grid is:
;
;     up    (-16)  nh < 0                     Q=1,  R=16  (R=16 never fires)
;     right (+1)   nh % 16 == 0               Q=1,  R=0
;     down  (+16)  nh / 16 % 16 == 0          Q=16, R=0
;     left  (-1)   nh % 16 == 15              Q=1,  R=15
;
; so `BRN` catches every negative wrap (up off the top, left out of cell 0) and
; `(nh / Q) % 16 == R` catches the other three. One test, no dispatch on direction.
;
; The body is a FIFO of cells in a 50-slot ring of the tape. T and HD are *unbounded*
; counters and the wrap is done on the address (`MODI 50`), not on the counter: MODI
; is free where masking a stored counter would cost two more tape reads a tick.
; 50 is exactly the longest snake the constraints allow — a growth needs a spawn round
; *and* a tick round, so the 99 rounds after the start give at most 49 of them — and
; the ring's size is a *first-order* tick cost, not just area: one tape read costs
; ~105 + 8.3N ticks, measured as 2.17M against 2.62M ticks on the longest public case
; at N=66 against N=90. Every slot the body does not need makes every read cheaper.
;
; Only *reads* cost. A tape write never blocks the CPU (measured: 19 ticks against a
; read's 523), which is why `ST` appears freely below and every optimisation here is
; about deleting reads.
;
; `NEG` earns its lane here rather than building -16 as `LDI 0` / `SUBI 16`: dropping
; the opcode was measured *worse* (16,900 footprint against 16,641), because one fewer
; lane re-folds the ROM onto a wider machine. Lane count is not monotone in footprint.
;
; Collision is a scan of BODY[T .. HD), done *after* the tail has been popped —
; which is exactly the rule that "moving to where the tail just was is legal".
; A scan iteration costs a ROM lap (ARCH.md §5.4) *and* five tape accesses, which
; together are ~68% of this machine's ticks — so this is the part a body-ring
; coprocessor replaces. The loop control is a countdown rather than a compare against
; HD, because `DECM` leaves the *pre*-decrement value in ACC and so tests and moves
; the cursor in one tape access instead of two.

.equ H     1        ; head cell
.equ NH    2        ; candidate head cell for this tick
.equ DELTA 3        ; cell-index step of the current direction
.equ Q     4        ; wall test divisor
.equ R     5        ; wall test residue that means death (16 = unreachable)
.equ FRUIT 6        ; fruit cell, or 256 for "no fruit on the board"
.equ T     7        ; body FIFO tail counter (oldest cell)
.equ HD    8        ; body FIFO head counter (one past the newest cell)
.equ I     9        ; loop cursor over the body
.equ TMP   10       ; scratch (x of a coordinate pair being read)
.equ CNT   11       ; scan countdown
.equ BODY  16       ; BODY .. BODY+63: the body ring

; ── round 1: `sx sy`, one cell, moving right, one frame ──────────────────────
        LDI 256
        ST  FRUIT
        LDI 0
        ST  T
        LDI 1
        ST  HD
        ST  DELTA           ; right: +1 per cell
        ST  Q               ; Q = 1
        LDI 0
        ST  R               ; death iff nh % 16 == 0
        IN                  ; sx
        ST  TMP
        IN                  ; sy
        MULI 16
        ADD TMP             ; cell = sy * 16 + sx
        ST  H
        ST  BODY            ; body[0]; T = 0, HD = 1
        DSPA                ; ST preserves ACC, so the cell is still in hand
        LDI 10
        DSPD                ; green
        LDI 1
        DSPS                ; commit, keep `next` and the cursor

; ── one round per lap ────────────────────────────────────────────────────────
round:  IN
        BRZ tick
        SUBI 1
        BRZ fruit
        ; 2/3/4/5 -> up/right/down/left, and ACC is already v - 1.
        SUBI 1
        BRZ up
        SUBI 1
        BRZ right
        SUBI 1
        BRZ down

left:   LDI 1
        NEG
        ST  DELTA
        LDI 1
        ST  Q
        LDI 15
        ST  R
        JMP round

up:     LDI 16
        NEG
        ST  DELTA
        LDI 1
        ST  Q
        LDI 16
        ST  R               ; unreachable: BRN catches every up-off-the-top
        JMP round

right:  LDI 1
        ST  DELTA
        ST  Q
        LDI 0
        ST  R
        JMP round

down:   LDI 16
        ST  DELTA
        ST  Q
        LDI 0
        ST  R
        JMP round

; ── `1 fx fy`: a fruit appears, the game does not tick, commit a frame ───────
fruit:  IN                  ; fx
        ST  TMP
        IN                  ; fy
        MULI 16
        ADD TMP
        ST  FRUIT
        DSPA
        LDI 9
        DSPD                ; red
        LDI 1
        DSPS
        JMP round

; ── `0`: advance the game one tick ───────────────────────────────────────────
tick:   LD  H
        ADD DELTA
        ST  NH
        BRN dead            ; off the top, or left out of cell 0
        DIV Q
        MODI 16
        SUB R
        BRZ dead            ; through a side wall
        LD  FRUIT
        SUB NH
        BRZ eat             ; landing on the fruit grows: the tail stays put

        ; The tail moves first, so erase it and drop it before the body is scanned.
        LD  T
        MODI 50
        ADDI BODY
        LDA
        DSPA
        LDI 0
        DSPD                ; black
        INCM T

        LD  T
        ST  I
        LD  HD
        SUB T
        ST  CNT             ; cells still occupied after the tail left
scan:   DECM CNT            ; DECM leaves the *old* value, so this tests before it moves
        BRZ push            ; the whole body is behind us: the cell is free
        LD  I
        MODI 50
        ADDI BODY
        LDA
        SUB NH
        BRZ crash           ; the head hit the body it still occupies
        INCM I
        JMP scan

eat:    LDI 256
        ST  FRUIT           ; the fruit disappears; no tail pop, so the snake grows

push:   LD  HD
        MODI 50
        ADDI BODY
        MOVA NH             ; body[HD] = nh
        INCM HD
        LD  NH
        ST  H
        DSPA
        LDI 10
        DSPD                ; green
        LDI 1
        DSPS
        JMP round

; ── the player loses: the snake does not move, and it is drawn red ───────────
; `crash` arrives with the tail already popped and its pixel already black, so it
; puts the tail back before the repaint covers it again.
crash:  DECM T
dead:   LD  T
        ST  I
paint:  LD  I
        SUB HD
        BRZ shown
        LD  I
        MODI 50
        ADDI BODY
        LDA
        DSPA
        LDI 9
        DSPD                ; red
        INCM I
        JMP paint
shown:  LDI 1
        DSPS
        HALT
