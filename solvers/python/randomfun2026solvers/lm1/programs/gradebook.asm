; gradebook — N students x K subjects, then batches of GET/SET/AVG/TOP.
;
; Round 1 is the roster: `N K` then N records `id g1 .. gK`. Every later round is
; `O` followed by O operations:
;   1 id s      GET — emit student id's grade in subject s
;   2 id s v    SET — grade := v, no output
;   3 s         AVG — emit floor(sum of subject s over all students / N)
;   4 s         TOP — emit the id of the best grade in subject s, ties -> smallest id
;
; NOT ISA v1: needs `LDA`/`MOVA` (indexed load/store through ACC) and `DIVI`.
; A grade book is two arrays and there is no way to address an array with
; ARCH.md's immediate-only `LD`/`ST`. `LDA`/`MOVA` rather than `LDP`/`STP` for the
; reason tcp.asm gives: keeping the address in ACC means no SPILL block is needed,
; and every memory glyph stays on the CPU's east bus (ARCH.md §7.1).
;
; Addresses start at 1: the generated hardware encodes the operation in the *sign*
; of the address word (+a = read, -a = write), so slot 0 would be ambiguous.
;
; ── layout ──────────────────────────────────────────────────────────────────
; Grades get a **fixed stride 4** row per student rather than stride K, so a grade
; address is `GRD + 4*i + s - 1` and needs no runtime multiply. Dropping `MULI`
; keeps the opcode count at 15, i.e. the depth-4 decode trie and 16 lanes the
; generator budgets for; the wasted cells cost nothing, since N <= 16 and K <= 4
; means 64 slots either way once the tape is sized from the constraints.
;
; ── what the ticks are made of ──────────────────────────────────────────────
; Two costs dwarf everything else, and every choice below is one of them:
;
; * **A tape access is ~105 + 8.3*94 = 885 ticks** (ARCH.md §4.1) and *every*
;   variable lives in the tape, since A dies on each fetch and B is the only
;   register. So loops are bounded by pointer equality against a precomputed end
;   address (one access a lap) instead of by a counter (two), the cursor bump is
;   fused into the read that the lane needs anyway (`LD p / ADDI d / ST p / SUBI d`
;   is one access cheaper than reading `p` twice), and there is exactly **one**
;   cursor: the id of student `i` is reached as `IDS + (PG - GRD)/4`, which is
;   arithmetic on a value already in ACC rather than a second pointer to keep.
; * **A backward jump costs a whole ROM lap** — the discard loop recirculates
;   `2*(n - body)` words, so with n instructions every loop iteration pays ~12n
;   ticks whatever it does. That makes the *total* instruction count part of the
;   cost of every inner loop, which is why GET/SET/AVG/TOP share the addressing
;   idiom rather than each carrying its own.
;
; AVG divides by N, which is a *runtime* value, and the ISA has only immediate
; division. Repeated subtraction is O(sum/N) ~ 400 laps and shift-and-subtract long
; division is ~110 accesses plus 11 laps per AVG; a 13-way dispatch on N (4..16 by
; the constraints) into 13 `DIVI k` sites costs one tape read plus a straight-line
; compare chain — ~50x cheaper in ticks, at 128 ROM words that are *free* here
; because the machine is 112 columns wide before the ROM is folded at all.

.equ N     1                ; student count
.equ KK    2                ; subject count K
.equ PG    3                ; the one cursor: a grade address, always GRD + 4*i + c
.equ CNT   4                ; students left (roster) / operations left (batch)
.equ T     5                ; subjects left for this student (roster only)
.equ TID   6                ; the id an operation names
.equ S     7                ; the subject an operation names, 1..K
.equ V     8                ; value staged for MOVA (the only way to write it)
.equ SUM   9                ; AVG accumulator
.equ BEST 10                ; TOP: best grade seen
.equ BID  11                ; TOP: id owning it (smallest on a tie)
.equ END  12                ; cursor value that ends a subject scan
.equ GEND 13                ; GRD + 4*N — one past the last grade row
.equ IDS  14                ; ids[i]                  at IDS + i         (14..29)
.equ GRD  30                ; grade of i in subject s at GRD + 4*i + s-1 (30..93)

; ── round 1: load the roster ────────────────────────────────────────────────
; PG is the only cursor here too: the id slot is `IDS + (PG - GRD)/4`, and at the
; head of a student's row that quotient is exactly i.
        IN                  ; N
        ST  N
        IN                  ; K
        ST  KK
        LDI GRD
        ST  PG
        LD  N
        ST  CNT

rstu:   IN                  ; id
        ST  V
        LD  PG
        SUBI GRD
        DIVI 4              ; ACC = i
        ADDI IDS
        MOVA V              ; ids[i] = id

        LD  KK
        ST  T
rgrd:   IN                  ; g_s
        ST  V
        LD  PG
        ADDI 1
        ST  PG              ; bump first, then step back: one access saved
        SUBI 1
        MOVA V              ; grades[i][s] = g_s
        LD  T
        SUBI 1
        ST  T
        BRZ rstep
        JMP rgrd

rstep:  LDI 4               ; the row is stride 4 but only K grades were read
        SUB KK
        ADD PG
        ST  PG              ; PG = next student's row
        LD  CNT
        SUBI 1
        ST  CNT
        BRZ rdone
        JMP rstu

rdone:  LD  PG
        ST  GEND            ; = GRD + 4*N

; ── batch rounds: O then O operations ───────────────────────────────────────
round:  IN                  ; O
        ST  CNT
oploop: IN                  ; op
        SUBI 1
        BRZ opget
        SUBI 1
        BRZ opset
        SUBI 1
        BRZ opavg
        JMP optop           ; op == 4 by elimination

nextop: LD  CNT
        SUBI 1
        ST  CNT
        BRZ round           ; the round's output is complete; unlock the next
        JMP oploop

; ── GET: 1 id s ─────────────────────────────────────────────────────────────
; The scan walks the *grade* rows and reaches the id it is testing through
; `IDS + (PG - GRD)/4`, so the hit needs no index -> address conversion: the grade
; is `PG + s - 1` on the row the scan stopped on.
opget:  IN
        ST  TID
        IN
        ST  S
        LDI GRD
        ST  PG
gscan:  LD  PG
        ADDI 4
        ST  PG
        SUBI 4              ; ACC = the row under test
        SUBI GRD
        DIVI 4              ; ACC = i
        ADDI IDS
        LDA                 ; ACC = ids[i]
        SUB TID
        BRZ ghit
        JMP gscan
ghit:   LD  PG
        SUBI 4              ; back onto the hit's row
        ADD S
        SUBI 1              ; ACC = GRD + 4i + s - 1
        LDA
        OUT
        JMP nextop

; ── SET: 2 id s v ───────────────────────────────────────────────────────────
opset:  IN
        ST  TID
        IN
        ST  S
        IN
        ST  V
        LDI GRD
        ST  PG
sscan:  LD  PG
        ADDI 4
        ST  PG
        SUBI 4
        SUBI GRD
        DIVI 4
        ADDI IDS
        LDA
        SUB TID
        BRZ shit
        JMP sscan
shit:   LD  PG
        SUBI 4
        ADD S
        SUBI 1
        MOVA V              ; grades[i][s] = v
        JMP nextop

; ── AVG: 3 s ────────────────────────────────────────────────────────────────
opavg:  IN
        ST  S
        LDI 0
        ST  SUM
        LDI GRD
        ADD S
        SUBI 1
        ST  PG              ; first student's grade in subject s
        LD  GEND
        ADD S
        SUBI 1
        ST  END             ; one row past the last
ascan:  LD  PG
        LDA
        ADD SUM
        ST  SUM
        LD  PG
        ADDI 4
        ST  PG
        SUB END
        BRZ adiv
        JMP ascan

; floor(SUM / N) with N runtime and only immediate division: dispatch on N.
; The `SUBI 1` chain keeps N - k in ACC, so the whole chain costs one tape read.
adiv:   LD  N
        SUBI 4
        BRZ d4
        SUBI 1
        BRZ d5
        SUBI 1
        BRZ d6
        SUBI 1
        BRZ d7
        SUBI 1
        BRZ d8
        SUBI 1
        BRZ d9
        SUBI 1
        BRZ d10
        SUBI 1
        BRZ d11
        SUBI 1
        BRZ d12
        SUBI 1
        BRZ d13
        SUBI 1
        BRZ d14
        SUBI 1
        BRZ d15
        LD  SUM             ; N == 16 by elimination (4 <= N <= 16)
        DIVI 16
        JMP aout
d4:     LD  SUM
        DIVI 4
        JMP aout
d5:     LD  SUM
        DIVI 5
        JMP aout
d6:     LD  SUM
        DIVI 6
        JMP aout
d7:     LD  SUM
        DIVI 7
        JMP aout
d8:     LD  SUM
        DIVI 8
        JMP aout
d9:     LD  SUM
        DIVI 9
        JMP aout
d10:    LD  SUM
        DIVI 10
        JMP aout
d11:    LD  SUM
        DIVI 11
        JMP aout
d12:    LD  SUM
        DIVI 12
        JMP aout
d13:    LD  SUM
        DIVI 13
        JMP aout
d14:    LD  SUM
        DIVI 14
        JMP aout
d15:    LD  SUM
        DIVI 15
aout:   OUT
        JMP nextop

; ── TOP: 4 s ────────────────────────────────────────────────────────────────
; Seeded from student 0 rather than from a sentinel: the sentinel a tie needs is
; "larger than any id" = 10000, and a five-digit ROM literal would widen *every*
; word of the fixed-width ROM image. Re-visiting student 0 in the loop is a tie
; against itself and changes nothing.
;
; The scan keeps only the grade cursor. Reaching the id costs three instructions of
; arithmetic on ACC and is only done when the best actually changes, so the common
; lap is 6 accesses rather than the 8 a second cursor would need.
optop:  IN
        ST  S
        LDI GRD
        ADD S
        SUBI 1
        ST  PG
        LD  GEND
        ADD S
        SUBI 1
        ST  END
        LDI IDS
        LDA
        ST  BID             ; ids[0]
        LD  PG
        LDA
        ST  BEST            ; student 0's grade in subject s

tscan:  LD  PG
        LDA
        SUB BEST
        BRN tstep           ; worse
        BRZ ttie            ; equal: keep the smaller id
        LD  PG              ; strictly better: take grade, then id
        LDA
        ST  BEST
        JMP tid
ttie:   LD  PG
        SUBI GRD
        SUB S
        ADDI 1
        DIVI 4              ; ACC = i
        ADDI IDS
        LDA
        SUB BID
        BRN tid
        JMP tstep
tid:    LD  PG
        SUBI GRD
        SUB S
        ADDI 1
        DIVI 4
        ADDI IDS
        LDA
        ST  BID
tstep:  LD  PG
        ADDI 4
        ST  PG
        SUB END
        BRZ tout
        JMP tscan
tout:   LD  BID
        OUT
        JMP nextop
