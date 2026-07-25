; snake, on a coprocessor that owns the body ring and the panel — and answers nothing.
;
; Same game as `snake.asm`: see its header for the framebuffer contract, the cell
; encoding (`y * 16 + x`, which is also the display's ADDR word) and the DELTA/Q/R
; wall test. What changes is that the CPU no longer keeps the body, draws, or *reads
; anything back*.
;
; Why a coprocessor: measured on the engine, `snake.asm` spends 48.5% of its ticks
; blocked on the rotating tape at 523 ticks a read, and 26% of a case inside the
; self-collision `scan:` loop — where four reads in five are the loop cursor rather
; than body data, and every iteration also discards a near-full ROM lap. A tape read
; costs ~105 + 8.3N, so a 50-slot body ring also taxed every unrelated scalar read.
;
; Why it answers nothing, which is the load-bearing part: §7.1 says an `r` competes
; only with *incoming* pipes. A response pipe into the CPU is therefore a rival for
; every `r` in it, including the jump slab's ROM read — and 4,800 (fold, mem_pad,
; stream_pad) combinations all fail on exactly that binding. `matmul`, the only other
; machine with a unit, gets away with it by having no `JMPF` at all. Outgoing pipes
; have no such competition, and this machine already drives three of them.
;
; So the unit acts on what it finds instead of reporting it. Four commands, one word
; each, `8 * arg + code`:
;
;   GROW  cell            append; paint green; commit. The opening frame, and every
;                         tick that eats a fruit — growth *is* "do not drop the tail".
;   STEP  n * 256 + cell  the whole ordinary tick. Rotate n values, order preserved,
;                         comparing all but the *first* — the first is the tail, which
;                         is about to vacate, so skipping it is the rule "the tail
;                         moves before the head". No match: drop the oldest and paint
;                         it black, append `cell` and paint it green, commit. Match:
;                         the player has lost, so paint the whole body red, commit,
;                         and stop, leaving the body where it was.
;   FRUIT cell            paint red; commit.
;   RED   n               paint the whole body red; commit — the *wall* death, which
;                         the CPU detects arithmetically and the unit cannot see.
;
; The CPU is left with eight scalars, i.e. tape N=9 and ~180 ticks a read instead of
; ~653, eight reads and eighteen instructions a tick, and no display lanes.

.unit snake         ; SND talks to the body-ring coprocessor (lm1/snake_unit.py)

.equ H     1        ; head cell
.equ NH    2        ; candidate head cell for this tick
.equ DELTA 3        ; cell-index step of the current direction
.equ Q     4        ; wall test divisor
.equ R     5        ; wall test residue that means death (16 = unreachable)
.equ FRUIT 6        ; fruit cell, or 256 for "no fruit on the board"
.equ LEN   7        ; body length, in cells
.equ TMP   8        ; scratch: the x of a coordinate pair being read

; Command codes. The unit derives these from its decode trie's geometry
; (`lm1/snake_unit.py`), so they are named here and never spelled inline.
.equ C_STEP  0
.equ C_FRUIT 1
.equ C_RED   2
.equ C_GROW  3

; ── round 1: `sx sy`, one cell, moving right, one frame ──────────────────────
        LDI 256
        ST  FRUIT
        LDI 1
        ST  LEN
        ST  DELTA           ; right: +1 per cell
        ST  Q
        LDI 0
        ST  R               ; death iff nh % 16 == 0
        IN                  ; sx
        ST  TMP
        IN                  ; sy
        MULI 16
        ADD TMP             ; cell = sy * 16 + sx
        ST  H
        MULI 8
        ADDI C_GROW
        SND                 ; append, paint green, commit

; ── one round per lap ────────────────────────────────────────────────────────
round:  IN
        BRZ tick
        SUBI 1
        BRZ fruit
        SUBI 1
        BRZ up
        SUBI 1
        BRZ right
        SUBI 1
        BRZ down

left:   LDI 0
        SUBI 1
        ST  DELTA
        LDI 1
        ST  Q
        LDI 15
        ST  R
        JMP round

up:     LDI 0
        SUBI 16
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
        MULI 8
        ADDI C_FRUIT
        SND                 ; paint red, commit
        JMP round

; ── `0`: advance the game one tick ───────────────────────────────────────────
; `ST H` runs before the move is known to be legal, which is free (a write never
; blocks) and safe: every path that rejects the move ends the game, and no path after
; a death reads H again.
tick:   LD  H
        ADD DELTA
        ST  NH
        ST  H
        BRN dead            ; off the top, or left out of cell 0
        DIV Q
        MODI 16
        SUB R
        BRZ dead            ; through a side wall
        LD  FRUIT
        SUB NH
        BRZ eat             ; landing on the fruit grows: the tail stays put

        LD  LEN
        MULI 256
        ADD NH
        MULI 8
        ADDI C_STEP
        SND                 ; scan, then either move the snake or end the game
        JMP round

eat:    LDI 256
        ST  FRUIT           ; the fruit disappears
        LD  H
        MULI 8
        ADDI C_GROW
        SND                 ; append, paint green, commit — no tail drop
        INCM LEN
        JMP round

; ── the wall death: the snake does not move, and it is drawn red ─────────────
dead:   LD  LEN
        MULI 8
        ADDI C_RED
        SND
        IN                  ; block for ever: the test case ended with that frame, so
                            ; no input is coming. Cheaper than halting *and* one opcode
                            ; fewer — sixteen is a depth-4 decode trie, and a
                            ; seventeenth would cost a whole trie level plus its lane
