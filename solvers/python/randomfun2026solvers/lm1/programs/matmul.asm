; matmul — C = A·B, with A being N×M and B being M×K, all row-major.
;
; NOT ISA v1: needs `LDA` (load through ACC), `MOVA` (store at ACC) and `MUL`
; (multiply by a STORE slot). Every address here is computed at run time from
; N/M/K, so ARCH.md's immediate-only `LD`/`ST` cannot reach the matrices at all.
;
; 13 opcodes, so the decode trie stays at depth 4 (16 lanes):
;   IN ST LDI LD ADD SUB ADDI MUL LDA MOVA OUT BRN HALT
; There is deliberately no `JMPF` and no `BRZ`: every loop counts *up* and tests
; `cursor - end < 0` with `BRN`, which is one instruction shorter than the
; count-down/`BRZ`/`JMP` idiom *and* removes two structures-band slabs from the
; generated CPU (a `BRZ` slab is 8 rows, a `JMPF` slab 5) — pure footprint win.
;
; Addresses start at 1: the generated hardware encodes the operation in the
; *sign* of the address word (+a = read, -a = write), so slot 0 is ambiguous.
;
; ── layout ──────────────────────────────────────────────────────────────────
; A goes in row-major at ABASE..ABASE+N*M-1.
; B goes in **transposed** at BT..BT+M*K-1, i.e. B^T[j][t] = B[t][j] lives at
; BT + j*M + t. Transposing costs nothing at load time (the write address is
; computed either way) and it is what makes the inner product walk *both*
; operands with stride 1: A[i][t] at PA and B[t][j] at PA + DELTA, where
; DELTA = (BT + j*M) - (ABASE + i*M) is constant for the whole t loop. One
; cursor, one `ADD DELTA`, and the cursor's own bump doubles as the loop test —
; 12 STORE accesses per multiply-accumulate instead of the 16 a two-cursor
; row-major version needs. STORE traffic is ~70 % of the tick count, so that is
; the only optimisation in here that matters.
;
; ── the tick/tape wall (measured, see tests/test_lm1_matmul.py) ─────────────
; This program is *correct* for every legal N,M,K, but the generated machine
; cannot run the big cases:
;   * `machine.tape_block` tops out at **108 slots** (fold=0; every larger fold
;     makes the return pipe shorter, not longer), and 16×16×16 needs 512 slots
;     for A and B alone. The tape simply cannot hold it.
;   * At the maximum tape, a STORE access costs 105 + 8.3·107 ≈ 993 ticks
;     (ARCH.md §4.1), so the 5 M tick cap buys ~5 000 accesses — about 420
;     multiply-accumulates. 16×16×16 needs 4 096.
; Packing several entries per slot fixes the first wall and makes the second one
; worse: with one accumulator every unpack temporary is itself a STORE slot.
; matmul needs the banked/FIFO STORE that ARCH.md §4.1 lists as future work.

.equ NN     1                ; N
.equ MM     2                ; M
.equ KK     3                ; K
.equ MKS    4                ; M*K
.equ ATOP   5                ; ABASE + N*M — one past A, and B^T's base
.equ BTOP   6                ; ATOP + M*K — one past B^T
.equ PA     7                ; A cursor, and the inner loop's only cursor
.equ PB     8                ; B^T write cursor (load phase)
.equ PBROW  9                ; B^T write cursor's row start (load phase)
.equ PBEND  10               ; where the current B row's writes stop
.equ SUM    11               ; the dot product being accumulated
.equ TMPA   12               ; A[i][t], staged so `MUL` can name it
.equ AEND   13               ; one past row i of A
.equ AROW   14               ; base of row i of A
.equ DELTA  15               ; PB - PA for the current (i, j)
.equ BTCOL  16               ; base of column j of B^T
.equ CNT    17               ; generic count-up loop counter
.equ IN1    18               ; input word staged for MOVA
.equ ABASE  19               ; A starts here — every slot below is named above

        IN
        ST  NN
        IN
        ST  MM
        IN
        ST  KK

; ── MKS = M*K, by K additions (no MULI, no multiply lane needed here) ───────
        LDI 0
        ST  MKS
        LDI 0
        ST  CNT
mkloop: LD  MKS
        ADD MM
        ST  MKS
        LD  CNT
        ADDI 1
        ST  CNT
        SUB KK
        BRN mkloop

; ── ATOP = ABASE + N*M, by N additions ─────────────────────────────────────
        LDI ABASE
        ST  ATOP
        LDI 0
        ST  CNT
nmloop: LD  ATOP
        ADD MM
        ST  ATOP
        LD  CNT
        ADDI 1
        ST  CNT
        SUB NN
        BRN nmloop

        LD  ATOP
        ADD MKS
        ST  BTOP

; ── load A row-major into ABASE..ATOP-1 ────────────────────────────────────
        LDI ABASE
        ST  PA
loadA:  IN
        ST  IN1
        LD  PA
        MOVA IN1                ; A[..] = the word just read
        LD  PA
        ADDI 1
        ST  PA
        SUB ATOP
        BRN loadA

; ── load B transposed: row t of B scatters down column t of B^T ────────────
; B arrives row-major, so for each t the K writes land at BT+t, +M, +2M, …
        LD  ATOP
        ST  PBROW
btrow:  LD  PBROW
        ST  PB
        ADD MKS
        ST  PBEND
btcol:  IN
        ST  IN1
        LD  PB
        MOVA IN1
        LD  PB
        ADD MM
        ST  PB
        SUB PBEND
        BRN btcol
        LD  PBROW
        ADDI 1
        ST  PBROW
        SUB ATOP
        SUB MM
        BRN btrow

; ── C[i][j] = sum_t A[i][t] * B^T[j][t], emitted as it is finished ──────────
        LDI ABASE
        ST  AROW
irow:   LD  AROW
        ADD MM
        ST  AEND
        LD  ATOP
        ST  BTCOL

jcol:   LDI 0
        ST  SUM
        LD  AROW
        ST  PA
        LD  BTCOL
        SUB AROW
        ST  DELTA

tloop:  LD  PA
        LDA                     ; A[i][t]
        ST  TMPA
        LD  PA
        ADD DELTA
        LDA                     ; B^T[j][t] == B[t][j]
        MUL TMPA
        ADD SUM
        ST  SUM
        LD  PA
        ADDI 1
        ST  PA
        SUB AEND                ; the bump doubles as the loop test
        BRN tloop

        LD  SUM
        OUT

        LD  BTCOL
        ADD MM
        ST  BTCOL
        SUB BTOP
        BRN jcol

        LD  AEND                ; PA already walked to AEND
        ST  AROW
        SUB ATOP
        BRN irow
        HALT
