; sudoku-validity — each round delivers one cell `r c v`; output 1 while the grid
; stays legal, 0 the first time a row, column or 3x3 box repeats a digit.
;
; NOT ISA v1: needs `AND`, plus `DIVI` for the box index and `LDA`/`MOVA` for
; indexed access.
;
; The natural encoding is a set-membership array — seen[r][v], seen[c][v],
; seen[box][v], 3 x 9 x 9 = 243 cells. **That does not fit.** `memory_tape`'s
; verified tape is geometrically capped at 103 slots in its 32x32 box (build_v2
; tries folds in order and fold 0 gives the most; N=103 builds, N=104 does not),
; so 243 is out of reach by a factor of two.
;
; So each of the 27 sets is a **bitmask in one cell**: bit v-1 of ROW+r is set iff
; digit v already appears in row r. 27 cells instead of 243, and a membership test
; is a single `AND`.
;
; `AND` is the only opcode this needs, which is what made it worth adding:
;   * no `OR` — a bit is only ever set after `AND` proved it clear, so the set is
;     a plain `ADD` of the same power of two;
;   * no shift — 2^(v-1) is a `MULI 2` loop of at most 8 iterations (v <= 9).
; `&` is a native littleman glyph, so the opcode is one cell of micro-program —
; exactly what `ADD` costs.
;
; Addresses start at 1: the generated hardware encodes the operation in the *sign*
; of the address word (+a = read, -a = write), so slot 0 is ambiguous and unused.
; Highest address used is BOX+8 = 36, comfortably inside the tape's 103 slots.

.equ R    1                 ; this cell's row
.equ C    2                 ; this cell's column
.equ V    3                 ; this cell's digit, 1..9
.equ P    4                 ; 2^(V-1), the bit being tested and set
.equ NEW  5                 ; staged mask value for MOVA (which stores from a slot)
.equ K    6                 ; countdown for the power-of-two loop
.equ IR   7                 ; address of this cell's row mask
.equ IC   8                 ; address of this cell's column mask
.equ IB   9                 ; address of this cell's box mask
.equ ROW  10                ; ROW+r for r in 0..8 -> 10..18
.equ COL  19                ; COL+c for c in 0..8 -> 19..27
.equ BOX  28                ; BOX+b for b in 0..8 -> 28..36

; The 27 masks start at zero and the tape self-zeroes, so there is no init loop
; and no roster round to consume — fall straight into the per-cell loop.

cell:   IN                  ; r
        ST  R
        IN                  ; c
        ST  C
        IN                  ; v
        ST  V

; ── P = 2^(V-1) ─────────────────────────────────────────────────────────────
        SUBI 1
        ST  K
        LDI 1
        ST  P
pow:    LD  K
        BRZ addrs
        SUBI 1
        ST  K
        LD  P
        MULI 2
        ST  P
        JMP pow

; ── the three mask addresses, computed once and kept ────────────────────────
addrs:  LD  R
        ADDI ROW
        ST  IR
        LD  C
        ADDI COL
        ST  IC
        LD  R
        DIVI 3
        MULI 3              ; 3 * (r/3)
        ST  IB
        LD  C
        DIVI 3
        ADD IB              ; b = 3*(r/3) + c/3
        ADDI BOX
        ST  IB

; ── three membership tests; a set bit anywhere means a duplicate ────────────
        LD  IR
        LDA                 ; ACC = mask[IR]
        AND P
        BRZ tcol
        JMP bad
tcol:   LD  IC
        LDA
        AND P
        BRZ tbox
        JMP bad
tbox:   LD  IB
        LDA
        AND P
        BRZ set
        JMP bad

; ── all clear: set the bit in each of the three masks ───────────────────────
; ADD rather than OR is sound precisely because the tests above proved it clear.
set:    LD  IR
        LDA
        ADD P
        ST  NEW
        LD  IR
        MOVA NEW            ; mask[IR] |= P
        LD  IC
        LDA
        ADD P
        ST  NEW
        LD  IC
        MOVA NEW
        LD  IB
        LDA
        ADD P
        ST  NEW
        LD  IB
        MOVA NEW
        LDI 1
        OUT
        JMP cell

bad:    LDI 0
        OUT
        HALT
