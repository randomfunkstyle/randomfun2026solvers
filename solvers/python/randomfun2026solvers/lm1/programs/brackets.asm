; brackets — is a length-prefixed bracket string balanced? 0, or the 1-based
; position of the first offending character (n + 1 if openers are left unclosed).
;
; NOT ISA v1: needs `DIVI` and `MODI` (immediate divide/modulo).
;
; The stack is packed into a single STORE cell as a base-3 integer: push is
; `STK = STK*3 + t`, top is `STK mod 3`, pop is `STK / 3`. Depth <= 32 and
; t in {0,1,2}, so STK < 3^32 = 1.9e15 and never overflows 64 bits. That is the
; only way to keep a stack without indexed memory — and it needs division, which
; ARCH.md's ISA does not have at all (see the step-2 report). The alternative is
; `LDP`/`STP` (indexed) with the stack at STORE[DEPTH]: also an extension.

; Addresses start at 1: the generated hardware encodes the operation in the
; *sign* of the address word (+a = read, -a = write), so slot 0 would be
; ambiguous and is left unused.
.equ N     1                ; characters left to read
.equ POS   2                ; 1-based index of the character just read
.equ STK   3                ; base-3 packed stack
.equ DEPTH 4                ; stack depth
.equ C     5                ; current character
.equ T     6                ; current bracket type: 0 = (), 1 = [], 2 = {}

        IN                  ; ACC = n
        ST  N
        LDI 0
        ST  POS
        ST  STK
        ST  DEPTH

loop:   LD  N
        BRZ eos
        SUBI 1
        ST  N
        LD  POS
        ADDI 1
        ST  POS
        IN
        ST  C

        SUBI 40             ; '('
        BRZ push0
        LD  C
        SUBI 91             ; '['
        BRZ push1
        LD  C
        SUBI 123            ; '{'
        BRZ push2
        LD  C
        SUBI 41             ; ')'
        BRZ pop0
        LD  C
        SUBI 93             ; ']'
        BRZ pop1
        LDI 2               ; '}' by elimination
        ST  T
        JMP popcheck

push0:  LDI 0
        ST  T
        JMP dopush
push1:  LDI 1
        ST  T
        JMP dopush
push2:  LDI 2
        ST  T
        JMP dopush

pop0:   LDI 0
        ST  T
        JMP popcheck
pop1:   LDI 1
        ST  T
        JMP popcheck

dopush: LD  STK
        MULI 3
        ADD T
        ST  STK
        LD  DEPTH
        ADDI 1
        ST  DEPTH
        JMP loop

popcheck:
        LD  DEPTH
        BRZ fail            ; a closer with nothing open
        LD  STK
        MODI 3              ; top of stack            <- ext
        SUB T
        BRZ okpop
        JMP fail            ; wrong-type closer
okpop:  LD  STK
        DIVI 3              ; pop                     <- ext
        ST  STK
        LD  DEPTH
        SUBI 1
        ST  DEPTH
        JMP loop

eos:    LD  DEPTH
        BRZ balanced
        LD  POS             ; POS == n here; unclosed openers answer n + 1
        ADDI 1
        OUT
        HALT

balanced:
        LDI 0
        OUT
        HALT

fail:   LD  POS
        OUT
        HALT
