; triangle — output the n-th triangular number (1 + 2 + ... + n), n <= 1000.
;
; ISA v1 only. Two live values (the counter and the running sum) do not fit in a
; single accumulator, so both live in STORE cells and every iteration pays four
; memory instructions. The closed form n(n+1)/2 would be five instructions total,
; but ISA v1 has neither a memory multiply nor any division — see the step-2
; report and `triangle-closed.asm` for what that costs.

.equ N   0
.equ SUM 1

        IN                  ; ACC = n
        ST  N
        LDI 0
        ST  SUM

loop:   LD  N
        BRZ done            ; counter exhausted
        ADD SUM             ; ACC = n_i + sum
        ST  SUM
        LD  N
        SUBI 1
        ST  N
        JMP loop            ; backward jump: n = P - L (ARCH 5.3)

done:   LD  SUM
        OUT
        HALT
