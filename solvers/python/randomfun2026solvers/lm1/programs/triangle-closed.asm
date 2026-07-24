; triangle-closed — the same problem in constant time, using two ISA extensions.
;
; NOT ISA v1: needs `MUL addr` (memory multiply) and `DIVI` (immediate divide),
; neither of which exists in ARCH.md 6. Kept as the measured argument for adding
; them: same answers, ~10 instructions instead of ~8000 for n = 1000.

.equ N 0

        IN                  ; ACC = n
        ST  N
        ADDI 1              ; ACC = n + 1
        MUL  N              ; ACC = n(n+1)          <- ext: memory multiply
        DIVI 2              ; ACC = n(n+1)/2        <- ext: immediate divide
        OUT
        HALT
