; tcp — reassemble scrambled packets. Round 1 carries n then `seq val`; every
; later round carries one `seq val`. Emit values in seq order as they unlock; if a
; packet arrives 16 or more ahead of the one we want, emit -1 and stop.
;
; NOT ISA v1: needs `LDA` (load through ACC) and `MOVA` (store at ACC from another
; slot). A 48-slot buffer addressed by `seq` cannot be expressed with ARCH.md's
; immediate-only `LD`/`ST`.
;
; These two replace the `LDP`/`STP` pair the first version used, and the point is
; that they need **no SPILL slot**. `LDP`/`STP` park a pointer in the spill pipe
; because the `0`/`1` glyph that opens a STORE request clobbers A while B still
; holds ACC — ARCH.md §6.1 calls that "a real hole in §5.1". Keeping the address in
; ACC dodges it: `W` hands the address over *after* the request marker is gone, and
; `MOVA` fetches its value from the store *after* the destination is sent, so a
; pointer and a value are never live at the same time. One glyph in place of a
; whole block and two pipes — and it keeps every memory lane on the east bus only,
; which is what makes the CPU's pipe binding tractable (ARCH.md §7.1).
;
; Addresses start at 1: the generated hardware encodes the operation in the
; *sign* of the address word (+a = read, -a = write), so slot 0 would be
; ambiguous and is left unused.
;
; Values are 1..999, so a slot reading 0 means "not yet received" and no separate
; presence bitmap is needed. `n` is read and discarded: the max-delay rule and the
; end of input terminate the program, so the length is never needed.

.equ WANT 1                 ; lowest seq not yet emitted
.equ S    2                 ; this packet's seq
.equ VAL  3                 ; this packet's value, staged for MOVA
.equ BUF  4                 ; buffer base address (BUF .. BUF+47 = 4 .. 51)

        IN                  ; n, discarded
        LDI 0
        ST  WANT

main:   IN                  ; seq
        ST  S
        SUB WANT
        SUBI 16
        BRN store           ; seq - want < 16 -> in window
        LDI 0               ; the ROM cannot hold a negative literal, so build -1
        SUBI 1
        OUT
        HALT

store:  IN                  ; val
        ST  VAL
        LD  S
        ADDI BUF
        MOVA VAL            ; buffer[seq] = VAL

drain:  LD  WANT
        ADDI BUF
        LDA                 ; ACC = buffer[want]
        BRZ main            ; a gap: wait for the next packet
        OUT
        LD  WANT
        ADDI 1
        ST  WANT
        JMP drain
