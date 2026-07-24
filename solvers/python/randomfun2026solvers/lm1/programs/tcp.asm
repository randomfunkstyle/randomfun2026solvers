; tcp — reassemble scrambled packets. Round 1 carries n then `seq val`; every
; later round carries one `seq val`. Emit values in seq order as they unlock; if a
; packet arrives 16 or more ahead of the one we want, emit -1 and stop.
;
; NOT ISA v1: needs `LDP`/`STP` (indexed load/store through a pointer cell), which
; in turn need the SPILL pipe. A 48-slot buffer addressed by `seq` cannot be
; expressed with ARCH.md's immediate-only `LD`/`ST` (see the step-2 report).
;
; Values are 1..999, so a slot reading 0 means "not yet received" and no separate
; presence bitmap is needed. `n` is read and discarded: the max-delay rule and the
; end of input terminate the program, so the length is never needed.

.equ WANT 0                 ; lowest seq not yet emitted
.equ S    1                 ; this packet's seq
.equ PTR  2                 ; scratch pointer for LDP/STP
.equ BUF  16                ; buffer base address (BUF .. BUF+47)

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

store:  LD  S
        ADDI BUF
        ST  PTR
        IN                  ; val
        STP PTR             ; buffer[seq] = val       <- ext (indexed store)

drain:  LD  WANT
        ADDI BUF
        ST  PTR
        LDP PTR             ; buffer[want]            <- ext (indexed load)
        BRZ main            ; a gap: wait for the next packet
        OUT
        LD  WANT
        ADDI 1
        ST  WANT
        JMP drain
