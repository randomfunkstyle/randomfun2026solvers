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
;
; Three things make this the *short* version of that idea. Each removes hot
; instructions from a loop that runs once per character, and instruction count is
; the cost: fetch + decode + return is 38 ticks before an opcode does anything.
;
; 1. **Arithmetic classification, not a compare chain.** The obvious code tests
;    the character against all six literals — `LD C; SUBI k; BRZ ...` six times,
;    re-loading C each time because `SUBI` destroys ACC. That was 20% of every
;    executed instruction. Two closed forms replace it:
;      * `t = (c - a) / 41` is the *type* (0 = `()`, 1 = `[]`, 2 = `{}`) for
;        openers **and** closers alike, because a type's two characters differ by
;        less than 41 while consecutive types differ by more than 41.
;      * `(c - a) mod 4 == 0` iff `c` is a closer. The closers 41, 93, 125 are all
;        1 mod 4 and the openers 40, 91, 123 are 0, 3, 3, so the closers are the
;        only one of the two sets that forms a single residue class — which is why
;        the branch has to test *closer*, not opener.
;    The **37** is what makes those share one subtraction. Any `a` in 30..43 with
;    `a = 1 (mod 4)` satisfies both at once; `a = 37` is the one that lands the
;    six values on 3, 0, 54, 56, 86, 88, so `mod 4` gives 3,0,2,0,2,0 (closers are
;    the zeros) and `/41` gives 0,0,1,1,2,2 (the type). Storing `c - 37` instead
;    of `c` therefore deletes the extra `SUBI 1` the obvious `a = 40` needs.
;    A compare-and-branch opcode could not have fixed any of this: it needs both a
;    constant and a label, and ARCH §5.2 allows one operand word.
;
; 2. **No DEPTH cell — the stack carries a sentinel.** `STK` starts at 1 rather
;    than 0, so "empty" is a value instead of a separate counter, which deletes
;    `LD DEPTH; ADDI 1; ST DEPTH` from every push and its mirror from every pop.
;    Underflow needs no test of its own either: `1 / 3 = 0`, so the one closer
;    that can pop the sentinel (`]`, because 1 mod 3 == 1) lands on STK = 0 and
;    `ST STK; BRZ fail` catches it — `ST` leaves ACC alone, so the test is free.
;    The other two closers fail the type test outright.
;
; 3. **No POS cell.** `POS = N0 - N` is recoverable at the two sites that need
;    it, so the per-character `LD POS; ADDI 1; ST POS` goes away entirely.
;
; The type test itself is `t - STK mod 3 == 0` rather than `STK mod 3 == t`: it
; is the same predicate, but it needs no second STORE slot to park `t` in, so the
; closer path never spends an `ST`.

; Addresses start at 1: the generated hardware encodes the operation in the
; *sign* of the address word (+a = read, -a = write), so slot 0 would be
; ambiguous and is left unused.
.equ N     1                ; characters left to read
.equ N0    2                ; the original n, for POS = N0 - N
.equ STK   3                ; base-3 packed stack, 1 = empty (see note 2)
.equ C     4                ; current character, then its bracket type

        IN                  ; ACC = n
        ST  N
        ST  N0
        LDI 1               ; the empty-stack sentinel
        ST  STK

loop:   DECM N              ; N -= 1, and ACC = the *old* N          <- ext
        BRZ eos             ; the old N was 0: the string is consumed
        IN                  ; ACC = c, and ACC survives the ST below
        SUBI 37
        ST  C               ; d = c - 37 (see the note on 37 above)
        MODI 4
        BRZ closer          ; d mod 4 == 0  <=>  c is a closer

; ── opener: STK = STK*3 + t ─────────────────────────────────────────────────
        LD  C
        DIVI 41             ; t                                       <- ext
        ST  C               ; C is dead; reuse it for t
        LD  STK
        MULI 3
        ADD C
        ST  STK
        JMP loop

; ── closer: the top must be the same type, then pop ─────────────────────────
closer: LD  C
        DIVI 41             ; t                                       <- ext
        SUB STK
        MODI 3              ; 0 iff STK == t (mod 3)                  <- ext
        BRZ okpop
        JMP fail            ; wrong-type closer, or a closer with nothing open

okpop:  LD  STK
        DIVI 3              ; pop                                     <- ext
        ST  STK             ; ACC survives, so the underflow test is free
        BRZ fail            ; popped the sentinel: nothing was open
        JMP loop

; ── end of string ───────────────────────────────────────────────────────────
; `DECM` ran once more than there were characters, so N == -1 here — which makes
; `N0 - N` equal n + 1, exactly the answer for unclosed openers. So `eos` falls
; straight into `fail` and the program needs no `ADDI` at all. Dropping that
; opcode is what keeps the used-opcode count at 15 once `DECM` joins, i.e. keeps
; the decode trie at depth 4 and the lane block at 16 rows.
;
; And `STK - 1` is *already* the answer 0 when the string balances, so the two
; exits share one `OUT`/`HALT` and there is no second `LDI 0` anywhere. Three
; instructions saved off the ring, which every backward jump pays for: a taken
; jump recirculates every word it skips, so the whole tail's length is billed
; once per loop iteration.
eos:    LD  STK
        SUBI 1
        BRZ out             ; back at the sentinel: ACC is already the 0 to emit

fail:   LD  N0              ; the 1-based position of the char just read
        SUB N               ; at eos N == -1, so this is n + 1

out:    OUT
        HALT
