; matmul — C = A·B, with A being N×M and B being M×K, all row-major.
;
; NOT ISA v1: needs `MUL` (multiply by a STORE slot) and the two STREAM opcodes
; `SND`/`RCV` (see `stream.py`). Eight opcodes in total, which keeps the decode
; trie at depth **3** — half the lanes and half the decode walk of the 13-opcode
; version this replaces.
;
; ── why the program looks like this ─────────────────────────────────────────
; The obvious accumulator-machine matmul — three nested loops with a running sum
; in a tape slot — is *correct* and cannot be made to fit: it costs ~13 STORE
; accesses per multiply-accumulate, and 4096 MACs times ~800 ticks an access is
; 40M against a 5M cap. No amount of program cleverness recovers that, because
; the cost is memory traffic and the tape is the only memory.
;
; So the loop order is chosen for *streaming* instead, and the innermost loop is
; not in this file at all — it is a command to the STREAM block:
;
;     load A into ring A, row-major        (one command)
;     load B into ring B, row-major        (one command)
;     for i in 0..N-1:
;         zero the accumulator row         (one command)
;         for t in 0..M-1:
;             MAC K                        (one command: pops A[i][t], K MACs)
;             FWD K                        (one command: one lap of ring C)
;         EMIT K                           (one command: K values to the output)
;     drain ring B                         (one command)
;
; Two facts make it work. **B is not transposed**: the inner loop runs over j
; with t fixed, so B is walked row-major, exactly as it arrives, and `MAC K`
; rotating ring B by K advances it precisely one row of B — after M*K rotations
; it is back where it started, ready for the next row of A. **C is never stored**:
; a row of it circulates through the ADDER room, one partial sum per product.
;
; This file therefore executes ~9 instructions and 4 tape accesses per (i, t)
; pair — 256 of those at the largest shape — instead of ~13 tape accesses per
; multiply-accumulate. Everything below is loop scaffolding; the arithmetic is in
; the hardware.
;
; ── the command word ───────────────────────────────────────────────────────
; One word per command: `8 * arg + code`. The unit's decode trie reads the low
; three bits and recovers `arg` with a floored `/ 8`. Codes come from the trie's
; geometry (`stream.arm_codes`), not from a choice made here:
;   EMIT 0 · FILLB 1 · ZEROC 2 · FILLA 3 · FWD 4 · DRAINB 5 · MAC 6 · RDIN 7
; The four commands the inner loops use are pre-multiplied once per round, so the
; hot path is `LD`+`SND` and nothing else.
;
; ── no HALT, and no LDI ────────────────────────────────────────────────────
; The ROM is a *ring* (ARCH.md §5.3): after the last word it wraps to word 0, so
; the round loop needs no jump — falling off the end *is* the jump, which is the
; cheapest possible backward branch and removes the `JMPF` slab from the CPU.
; `LDI` is gone for the same reason `JMPF` is: a zeroed tape slot plus `ADDI`
; builds any constant, and every opcode dropped is a shorter decode walk.

.equ ZERO   1                ; never written: an unwritten tape cell reads as 0
.equ NN     2                ; N
.equ MM     3                ; M
.equ KK     4                ; K
.equ CI     5                ; row counter i
.equ CT     6                ; term counter t
.equ CMAC   7                ; the MAC command word,  8*K + 6
.equ CFWD   8                ; the FWD command word,  8*K + 4
.equ CZERO  9                ; the ZEROC command word, 8*K + 2
.equ CEMIT  10               ; the EMIT command word,  8*K + 0
.equ CFILLA 11               ; the FILLA command word, 8*N*M + 3
.equ CFILLB 12               ; the FILLB command word, 8*M*K + 1
.equ CDRAIN 13               ; the DRAINB command word, 8*M*K + 5
.equ EIGHT  14               ; the constant 8, so `MUL` can name it

; ── the only literal the round needs: 8, the command word's radix ───────────
round:  LD  ZERO
        ADDI 8
        ST  EIGHT

; ── read N, M, K through the unit (it owns the I room) ──────────────────────
        LD  ZERO
        ADDI 7                  ; RDIN
        SND
        RCV
        ST  NN
        LD  ZERO
        ADDI 7
        SND
        RCV
        ST  MM
        LD  ZERO
        ADDI 7
        SND
        RCV
        ST  KK

; ── the six command words for this round ───────────────────────────────────
        LD  KK
        MUL EIGHT              ; 8*K
        ST  CEMIT               ; EMIT  = 8*K + 0
        ADDI 2
        ST  CZERO               ; ZEROC = 8*K + 2
        ADDI 2
        ST  CFWD                ; FWD   = 8*K + 4
        ADDI 2
        ST  CMAC                ; MAC   = 8*K + 6

        LD  NN
        MUL MM
        MUL EIGHT              ; 8*N*M
        ADDI 3
        ST  CFILLA              ; FILLA = 8*N*M + 3

        LD  MM
        MUL KK
        MUL EIGHT              ; 8*M*K
        ADDI 1
        ST  CFILLB              ; FILLB = 8*M*K + 1
        ADDI 4
        ST  CDRAIN              ; DRAINB = 8*M*K + 5

; ── load both matrices: two commands, 512 input words, no loop here ────────
        LD  CFILLA
        SND
        LD  CFILLB
        SND

; ── one row of C per lap ───────────────────────────────────────────────────
        LD  ZERO
        ST  CI
irow:   LD  CZERO
        SND                     ; K zeros into the accumulator ring

        LD  CMAC
        SND                     ; t = 0: pops A[i][0], K multiply-accumulates
        LD  ZERO
        ADDI 1
        ST  CT
tloop:  LD  CFWD
        SND                     ; one lap: partial sums back to the ADDER's input
        LD  CMAC
        SND                     ; t: pops A[i][t], K more multiply-accumulates
        LD  CT
        ADDI 1
        ST  CT
        SUB MM
        BRN tloop

        LD  CEMIT
        SND                     ; K finished values straight to the output room

        LD  CI
        ADDI 1
        ST  CI
        SUB NN
        BRN irow

; ── ring B still holds this round's M*K values; the next round refills it ──
        LD  CDRAIN
        SND
                                ; the ROM ring wraps here, back to `round`
