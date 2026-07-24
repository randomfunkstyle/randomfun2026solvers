; max-element — read a count n then n integers, output the largest.
;
; ISA v1 only. Three live values (remaining count, running max, current element),
; so two of them sit in STORE. The comparison is `SUB` + `BRN`, which is the only
; comparison LM-1 has: subtract and branch on the sign bit via `X`.

.equ N   0
.equ MAX 1
.equ X   2

        IN                  ; ACC = n
        SUBI 1              ; the first element is read unconditionally
        ST  N
        IN
        ST  MAX

loop:   LD  N
        BRZ done
        SUBI 1
        ST  N
        IN
        ST  X
        SUB MAX             ; ACC = x - max
        BRN skip            ; x < max -> keep max
        LD  X
        ST  MAX
skip:   JMP loop

done:   LD  MAX
        OUT
        HALT
