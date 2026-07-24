; atoi — each round carries a length-prefixed ASCII digit string; output its value.
;
; ISA v1 only. Round-based: the outer `JMP round` never terminates, which is fine
; (GRADING.md: a program need not halt) and is how every multi-round problem has
; to be written, since LM-1 cannot ask whether more input exists.

.equ N     0
.equ VALUE 1
.equ D     2

round:  IN                  ; ACC = n, the digit count
        ST  N
        LDI 0
        ST  VALUE

digit:  LD  N
        BRZ emit
        SUBI 1
        ST  N
        IN                  ; ASCII digit
        SUBI 48             ; '0'
        ST  D
        LD  VALUE
        MULI 10
        ADD D
        ST  VALUE
        JMP digit

emit:   LD  VALUE
        OUT
        JMP round           ; next round's input unlocks once this OUT lands
