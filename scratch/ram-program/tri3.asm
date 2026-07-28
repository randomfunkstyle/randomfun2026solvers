; three-slab smoke test: JMPF + BRZ + BRN in one loop.
; input n: counts down; emits 9 when the counter goes negative (never for n>=0
; via the BRZ exit), else emits the 0 at the end. n=3 -> 0; the BRN arm fires
; only if SUBI overshoots, so expected output for n>=1 is 0, for n=0 is 0.
.equ X 1

        IN
        ST  X
loop:   LD  X
        SUBI 1
        ST  X
        BRN neg
        BRZ done
        JMP loop

neg:    LDI 9
        OUT
        HALT

done:   LD  X
        OUT
        HALT
