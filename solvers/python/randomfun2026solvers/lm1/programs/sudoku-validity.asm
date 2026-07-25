; sudoku-validity — after every `r c v`, is the grid still duplicate-free?
;
; NOT ISA v1: needs `DIVI`, `MODI` (immediate divide/modulo) and `LDP`/`STP`
; (indexed load/store). 13 opcodes of the 16 the depth-4 decode trie has room for.
;
; ── the audit ────────────────────────────────────────────────────────────────
; One 9-bit set per unit, 27 units in all: rows 0-8, columns 0-8, boxes 0-8. Slot
; `RM+r` holds the digits already seen in row r as a bitmask (bit v = digit v), and
; likewise `CM+c` and `BOX+b`. A cell (r, c, v) is a duplicate iff bit v is already
; set in any of its three units, so one round is three test-and-sets and nothing
; else — no grid is stored and nothing is ever rescanned. 30 slots of tape, which
; matters: an access costs ~133 + 4.75N ticks, so the 243-slot (unit, digit) flag
; array and the 81-slot grid are both several times dearer per access.
;
; Test and set are the *same* addition. `mask + 2^v` sets bit v when it was clear
; and carries out of it when it was set, so `(mask + 2^v) / 2^v mod 2` is 1 exactly
; when the cell is new. No compare, no second copy of the mask, and the 1/0 it
; leaves in ACC *is* the verdict this round has to emit.
;
; ── addressing ───────────────────────────────────────────────────────────────
; `2^v` and `/2^v` must be immediates (the ISA has no divide-by-memory), so the
; body is specialised on v: one 9-way dispatch, nine copies of the three-unit
; check. The unit *addresses* stay dynamic, in RA/CA/BA, and are reached with
; `LDP`/`STP` — 2 tape accesses each, against 6 for the `LDA`/`MOVA` spelling of
; the same read-modify-write.
;
; The three addresses cost five accesses to build, and the trick that keeps it to
; five is choosing bases divisible by 3: with row masks at 3 and column masks at
; 12, `RA / 3 = 1 + r/3` and `CA / 3 = 4 + c/3`, so the box index 3*(r/3) + c/3
; falls out of the addresses already in hand. No table, no second copy of r or c,
; and no `BRN` — the whole program branches on zero only, which keeps the CPU's
; structures band down to a single slab.
;
; Addresses start at 1: the generated hardware encodes the operation in the *sign*
; of the address word (+a = read, -a = write), so slot 0 would be ambiguous.
; The tape is zero-filled at start-up, so every mask begins empty.

.equ RA  1                  ; address of this cell's row mask
.equ CA  2                  ; address of this cell's column mask
.equ BA  30                 ; address of this cell's box mask
.equ RM  3                  ; row masks    3..11   (r -> RM + r)
.equ CM  12                 ; column masks 12..20  (c -> CM + c)
.equ K1  14                 ; BOX - RM - CM/3, folding both bases into one ADDI
                            ; box masks 21..29  (b -> 21 + 3*(r/3) + c/3)

round:  IN                  ; ACC = r
        ADDI RM
        ST   RA             ; RA = RM + r
        DIVI 3              ; RM/3 + r/3
        MULI 3              ; RM + 3*(r/3)
        ADDI K1
        ST   BA             ; the row half of the box address
        IN                  ; ACC = c
        ADDI CM
        ST   CA             ; CA = CM + c
        DIVI 3              ; CM/3 + c/3
        ADD  BA
        ST   BA             ; BA = 21 + 3*(r/3) + c/3
        IN                  ; ACC = v
        SUBI 1              ; destructive chain: after k decrements ACC is v - k,
        BRZ  d1             ; so BRZ fires on v == k. v == 9 falls through.
        SUBI 1
        BRZ  d2
        SUBI 1
        BRZ  d3
        SUBI 1
        BRZ  d4
        SUBI 1
        BRZ  d5
        SUBI 1
        BRZ  d6
        SUBI 1
        BRZ  d7
        SUBI 1
        BRZ  d8
; ── v = 9 ─────────────────────────────────────────────────────────────────────
d9:     LDP  RA             ; ACC = row mask
        ADDI 512
        STP  RA             ; set bit 9 (or carry out of it); ACC = the new mask
        DIVI 512
        MODI 2              ; 1 = the digit was new here, 0 = duplicate
        BRZ  bad
        LDP  CA
        ADDI 512
        STP  CA
        DIVI 512
        MODI 2
        BRZ  bad
        LDP  BA
        ADDI 512
        STP  BA
        DIVI 512
        MODI 2
        BRZ  bad
        OUT                 ; ACC == 1: still consistent
        SUBI 1              ; ACC = 0, so the BRZ below is unconditional. Spending
        BRZ  round          ; an instruction to avoid `JMP` retires the jump slab:
                            ; 13 columns off the CPU and 13 ticks off *every*
                            ; instruction's walk back to the fetch site.
; ── v = 1 ─────────────────────────────────────────────────────────────────────
d1:     LDP  RA             ; ACC = row mask
        ADDI 2
        STP  RA             ; set bit 1 (or carry out of it); ACC = the new mask
        DIVI 2
        MODI 2              ; 1 = the digit was new here, 0 = duplicate
        BRZ  bad
        LDP  CA
        ADDI 2
        STP  CA
        DIVI 2
        MODI 2
        BRZ  bad
        LDP  BA
        ADDI 2
        STP  BA
        DIVI 2
        MODI 2
        BRZ  bad
        OUT                 ; ACC == 1: still consistent
        SUBI 1              ; ACC = 0, so the BRZ below is unconditional. Spending
        BRZ  round          ; an instruction to avoid `JMP` retires the jump slab:
                            ; 13 columns off the CPU and 13 ticks off *every*
                            ; instruction's walk back to the fetch site.
; ── v = 2 ─────────────────────────────────────────────────────────────────────
d2:     LDP  RA             ; ACC = row mask
        ADDI 4
        STP  RA             ; set bit 2 (or carry out of it); ACC = the new mask
        DIVI 4
        MODI 2              ; 1 = the digit was new here, 0 = duplicate
        BRZ  bad
        LDP  CA
        ADDI 4
        STP  CA
        DIVI 4
        MODI 2
        BRZ  bad
        LDP  BA
        ADDI 4
        STP  BA
        DIVI 4
        MODI 2
        BRZ  bad
        OUT                 ; ACC == 1: still consistent
        SUBI 1              ; ACC = 0, so the BRZ below is unconditional. Spending
        BRZ  round          ; an instruction to avoid `JMP` retires the jump slab:
                            ; 13 columns off the CPU and 13 ticks off *every*
                            ; instruction's walk back to the fetch site.
; ── v = 3 ─────────────────────────────────────────────────────────────────────
d3:     LDP  RA             ; ACC = row mask
        ADDI 8
        STP  RA             ; set bit 3 (or carry out of it); ACC = the new mask
        DIVI 8
        MODI 2              ; 1 = the digit was new here, 0 = duplicate
        BRZ  bad
        LDP  CA
        ADDI 8
        STP  CA
        DIVI 8
        MODI 2
        BRZ  bad
        LDP  BA
        ADDI 8
        STP  BA
        DIVI 8
        MODI 2
        BRZ  bad
        OUT                 ; ACC == 1: still consistent
        SUBI 1              ; ACC = 0, so the BRZ below is unconditional. Spending
        BRZ  round          ; an instruction to avoid `JMP` retires the jump slab:
                            ; 13 columns off the CPU and 13 ticks off *every*
                            ; instruction's walk back to the fetch site.
; ── v = 4 ─────────────────────────────────────────────────────────────────────
d4:     LDP  RA             ; ACC = row mask
        ADDI 16
        STP  RA             ; set bit 4 (or carry out of it); ACC = the new mask
        DIVI 16
        MODI 2              ; 1 = the digit was new here, 0 = duplicate
        BRZ  bad
        LDP  CA
        ADDI 16
        STP  CA
        DIVI 16
        MODI 2
        BRZ  bad
        LDP  BA
        ADDI 16
        STP  BA
        DIVI 16
        MODI 2
        BRZ  bad
        OUT                 ; ACC == 1: still consistent
        SUBI 1              ; ACC = 0, so the BRZ below is unconditional. Spending
        BRZ  round          ; an instruction to avoid `JMP` retires the jump slab:
                            ; 13 columns off the CPU and 13 ticks off *every*
                            ; instruction's walk back to the fetch site.
; ── v = 5 ─────────────────────────────────────────────────────────────────────
d5:     LDP  RA             ; ACC = row mask
        ADDI 32
        STP  RA             ; set bit 5 (or carry out of it); ACC = the new mask
        DIVI 32
        MODI 2              ; 1 = the digit was new here, 0 = duplicate
        BRZ  bad
        LDP  CA
        ADDI 32
        STP  CA
        DIVI 32
        MODI 2
        BRZ  bad
        LDP  BA
        ADDI 32
        STP  BA
        DIVI 32
        MODI 2
        BRZ  bad
        OUT                 ; ACC == 1: still consistent
        SUBI 1              ; ACC = 0, so the BRZ below is unconditional. Spending
        BRZ  round          ; an instruction to avoid `JMP` retires the jump slab:
                            ; 13 columns off the CPU and 13 ticks off *every*
                            ; instruction's walk back to the fetch site.
; ── v = 6 ─────────────────────────────────────────────────────────────────────
d6:     LDP  RA             ; ACC = row mask
        ADDI 64
        STP  RA             ; set bit 6 (or carry out of it); ACC = the new mask
        DIVI 64
        MODI 2              ; 1 = the digit was new here, 0 = duplicate
        BRZ  bad
        LDP  CA
        ADDI 64
        STP  CA
        DIVI 64
        MODI 2
        BRZ  bad
        LDP  BA
        ADDI 64
        STP  BA
        DIVI 64
        MODI 2
        BRZ  bad
        OUT                 ; ACC == 1: still consistent
        SUBI 1              ; ACC = 0, so the BRZ below is unconditional. Spending
        BRZ  round          ; an instruction to avoid `JMP` retires the jump slab:
                            ; 13 columns off the CPU and 13 ticks off *every*
                            ; instruction's walk back to the fetch site.
; ── v = 7 ─────────────────────────────────────────────────────────────────────
d7:     LDP  RA             ; ACC = row mask
        ADDI 128
        STP  RA             ; set bit 7 (or carry out of it); ACC = the new mask
        DIVI 128
        MODI 2              ; 1 = the digit was new here, 0 = duplicate
        BRZ  bad
        LDP  CA
        ADDI 128
        STP  CA
        DIVI 128
        MODI 2
        BRZ  bad
        LDP  BA
        ADDI 128
        STP  BA
        DIVI 128
        MODI 2
        BRZ  bad
        OUT                 ; ACC == 1: still consistent
        SUBI 1              ; ACC = 0, so the BRZ below is unconditional. Spending
        BRZ  round          ; an instruction to avoid `JMP` retires the jump slab:
                            ; 13 columns off the CPU and 13 ticks off *every*
                            ; instruction's walk back to the fetch site.
; ── v = 8 ─────────────────────────────────────────────────────────────────────
d8:     LDP  RA             ; ACC = row mask
        ADDI 256
        STP  RA             ; set bit 8 (or carry out of it); ACC = the new mask
        DIVI 256
        MODI 2              ; 1 = the digit was new here, 0 = duplicate
        BRZ  bad
        LDP  CA
        ADDI 256
        STP  CA
        DIVI 256
        MODI 2
        BRZ  bad
        LDP  BA
        ADDI 256
        STP  BA
        DIVI 256
        MODI 2
        BRZ  bad
        OUT                 ; ACC == 1: still consistent
        SUBI 1              ; ACC = 0, so the BRZ below is unconditional. Spending
        BRZ  round          ; an instruction to avoid `JMP` retires the jump slab:
                            ; 13 columns off the CPU and 13 ticks off *every*
                            ; instruction's walk back to the fetch site.
; A duplicate: ACC is 0 — that is what BRZ tested — so it is already the verdict.
bad:    OUT
        HALT
