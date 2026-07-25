; gradebook — N students x K subjects, then batches of GET/SET/AVG/TOP.
;
; Round 1 is the roster: `N K` then N records `id g1 .. gK`. Every later round is
; `O` followed by O operations:
;   1 id s      GET — emit student id's grade in subject s
;   2 id s v    SET — grade := v, no output
;   3 s         AVG — emit floor(sum of subject s over all students / N)
;   4 s         TOP — emit the id of the best grade in subject s, ties -> smallest id
;
; NOT ISA v1: needs `MOVA` (indexed store), `MUL`, `AND`, and `DIV` (see isa.py's
; row for it: the divisors here are runtime values, so `DIVI` cannot reach them).
; 16 opcodes exactly, which is the depth-4 decode trie's whole budget — every one
; below is load-bearing, and `ADDI` was *dropped* to make room for `MUL` (the
; roster fills its cells downwards so its cursor only ever needs `SUBI`).
;
; ── what the ticks are made of ──────────────────────────────────────────────
; Measured on the real engine, not modelled. Two costs dominate, and they are not
; the two `ARCH.md` §4.1 leads you to expect:
;
; * **A jump costs ~20 ticks per ROM word it skips, so the ROM word count is a
;   first-order cost of every loop iteration.** The image is fixed-width two
;   words per instruction and the ring is closed, so any jump from a to b skips
;   `(b - a) mod 2n` words: an operation that dispatches to a handler and returns
;   traverses the ring exactly once, i.e. pays ~`2n * 20` ticks *whatever it
;   does*. Padding this program with 60 unreachable `NOP`s cost 27,327 ticks per
;   added word on an 80-operation workload — 1.6M ticks for 60 instructions.
;   Consequence: **an inner loop is ~16x more expensive than the straight-line
;   code it saves**, because each of its iterations pays a whole ring lap. So the
;   three per-student scans are *fully unrolled* to 16 static blocks, and always
;   run all 16 — a slot for a student that does not exist holds 0, which loses
;   every comparison and adds nothing to a sum. There is no loop counter, no end
;   pointer and no `LDA`: every cell address is an immediate.
; * **A tape access costs ~14 ticks per tape slot**, so the slot count multiplies
;   the access count. Same workload: 94 slots -> 104 cost 106,638 ticks per slot.
;   Consequence: pack, and never keep a second array.
;
; ── the packed cell ─────────────────────────────────────────────────────────
; One tape slot per student holds the whole record:
;
;   cell(i) = packed(i) * 2^14 + (16384 - id(i))
;   packed(i) = g1 * 2^(11*(K-1)) + g2 * 2^(11*(K-2)) + ... + gK
;
; Fields are power-of-two aligned, so every extraction is one `AND` with a mask
; held in a tape slot, and three separate facts fall out for free:
;
; * **The ids array disappears.** `id(i) = 16384 - (cell(i) & 16383)`, so the scan
;   that looks a student up reads the same slot the grades live in. That is the
;   second access per student gone, and 16 slots off the tape.
; * **TOP needs no comparison of ids at all.** With subject s's field *above* the
;   id field, `key = cell & (grade-field(s) | 16383)` orders lexicographically by
;   (grade, 16384 - id) — so the largest key is the highest grade and, on a tie,
;   the *smallest* id, which is exactly the required tie-break. `16384 - id` is
;   also >= 6385 for every legal id, so a real student always beats an empty
;   slot's 0.
; * **AVG never looks at a student individually.** Sum the 16 raw cells with one
;   `LD` and fifteen `ADD`s — one access per student, no mask, no accumulator
;   slot — then `sum - IDSUM` is exactly `(sum of packed) * 2^14` (IDSUM being the
;   roster's sum of `16384 - id`, which is known at load time). Grade fields are
;   11 bits and 16 grades sum to at most 1600 < 2048, so the fields of that total
;   do not carry into each other and subject s's column total is one `AND 2047`
;   away. `DIV NN` then divides by the runtime roster size.
;
; Subject 1 sits at the *top* of the packed word rather than the bottom, which is
; what lets the roster build it with Horner's rule in reading order
; (`packed = packed * 2048 + g`) and so needs no runtime multiplier. The price is
; that a subject's field weight depends on K as well as s: it is
; `2^(14 + 11*(K-s))`, built per operation by a 4-way dispatch on `K - s` into a
; chain of `MULI`s. That dispatch is also why no literal here exceeds 999: the ROM
; image is one fixed digit width for every word, so a single 4-digit literal would
; widen all 822 of them.
;
; Widest cell: 100 * 2^(33+14) = 1.4e16, and 16 of them sum to 2.3e17 — both well
; inside the signed 64 bits `SPEC.md` gives every value.

; ── layout ──────────────────────────────────────────────────────────────────
.equ NN     1               ; N, the roster size (AVG divides by it)
.equ KK     2               ; K, the subject count
.equ TMP    3               ; scratch
.equ BEST   3               ;   = TMP: TOP's best key so far
.equ IDM    4               ; 16383 — the id field's mask
.equ M2047  5               ; 2047 — one grade field's mask
.equ IDSUM  6               ; sum over the roster of (16384 - id)
.equ F      7               ; this operation's field weight, 2^(14 + 11*(K-s))
.equ PACK   7               ;   = F: the roster's Horner accumulator
.equ GMASK  8               ; F * 2047 — subject s's grade field alone
.equ IDACC  8               ;   = GMASK: the roster's 16384 - id
.equ TMSK   9               ; GMASK | 16383 — TOP's comparison mask
.equ OP    10               ; the operation code, kept for the second dispatch
.equ TARG  11               ; 16384 - id: the id field a scan is looking for
.equ V     12               ; SET's new grade
.equ CUR   13               ; the cell a scan found
.equ RCNT  13               ;   = CUR: the roster's students-left counter
.equ PTR   14               ; that cell's address (roster: the fill cursor)
.equ OCNT  15               ; operations left in this batch round
;
; The 16 student cells. Nothing is stored per subject and nothing per id: this is
; the entire grade book, and the tape is 32 slots against the old layout's 94.
.equ C0  16
.equ C1  17
.equ C2  18
.equ C3  19
.equ C4  20
.equ C5  21
.equ C6  22
.equ C7  23
.equ C8  24
.equ C9  25
.equ C10 26
.equ C11 27
.equ C12 28
.equ C13 29
.equ C14 30
.equ C15 31

; ── constants ───────────────────────────────────────────────────────────────
; Both are built from three-digit literals rather than held as literals: 16383
; written out would widen every word of the ROM image (see the header).
        LDI 128
        MULI 128
        SUBI 1
        ST  IDM             ; 16383
        LDI 128
        MULI 16
        SUBI 1
        ST  M2047           ; 2047

; ── round 1: the roster ─────────────────────────────────────────────────────
; Cells are filled *downwards*, from C0 + N - 1 to C0, so the cursor only needs
; `SUBI 1` — which is what lets the opcode budget spend its 16th row on `MUL`
; instead of `ADDI`. Order does not matter: TOP's tie-break is in the id field,
; AVG's sum is commutative, and the cells above N - 1 keep their initial 0.
        IN
        ST  NN
        IN
        ST  KK
        LDI C0
        ADD NN
        SUBI 1
        ST  PTR
        LD  NN
        ST  RCNT

rstu:   IN                  ; id
        ST  TMP
        LDI 128
        MULI 128
        SUB TMP             ; 16384 - id
        ST  IDACC
        ADD IDSUM
        ST  IDSUM           ; AVG's correction term, accumulated as we read
        IN                  ; g1 — Horner's rule in reading order
        ST  PACK
        LD  KK
        SUBI 1
        BRZ rfin            ; K == 1: this record has no subject 2
        IN                  ; g2
        ST  TMP
        LD  PACK
        MULI 128
        MULI 16             ; PACK *= 2048 (no 4-digit literal)
        ADD TMP
        ST  PACK
        LD  KK
        SUBI 2
        BRZ rfin            ; K == 2: this record has no subject 3
        IN                  ; g3
        ST  TMP
        LD  PACK
        MULI 128
        MULI 16             ; PACK *= 2048 (no 4-digit literal)
        ADD TMP
        ST  PACK
        LD  KK
        SUBI 3
        BRZ rfin            ; K == 3: this record has no subject 4
        IN                  ; g4
        ST  TMP
        LD  PACK
        MULI 128
        MULI 16             ; PACK *= 2048 (no 4-digit literal)
        ADD TMP
        ST  PACK
rfin:   LD  PACK
        MULI 128
        MULI 128            ; make room for the id field
        ADD IDACC
        ST  TMP
        LD  PTR
        MOVA TMP            ; store[PTR] = the whole record
        LD  PTR
        SUBI 1
        ST  PTR
        LD  RCNT
        SUBI 1
        ST  RCNT
        BRZ round
        JMP rstu

; ── one operation ───────────────────────────────────────────────────────────
; Arguments are read in a single shared path — `op`, then `id` only for GET/SET,
; then `s`, then `v` only for SET — and the field weight is derived once here for
; all four handlers. Everything from `round`/`oploop` to `nextop` is exactly one
; trip round the ROM ring, which is the floor on an operation's cost; `nextop`
; sits at the *end* of the program so that a handler reaching it, and it reaching
; back to `oploop`, together close that one lap instead of opening a second.
round:  IN                  ; O, the batch's operation count
        ST  OCNT
oploop: IN
        ST  OP
        SUBI 3
        BRN rdid            ; GET/SET name a student
        JMP rds
rdid:   IN                  ; id
        ST  TMP
        LDI 128
        MULI 128
        SUB TMP
        ST  TARG            ; 16384 - id, the form the cells hold
rds:    IN                  ; s
        ST  TMP
        LD  OP
        SUBI 2
        BRZ rdv
        JMP have
rdv:    IN                  ; v
        ST  V
have:   LD  KK
        SUB TMP             ; K - s, the field's index from the bottom
        BRZ e0
        SUBI 1
        BRZ e1
        SUBI 1
        BRZ e2
e3:     LDI 128             ; F = 2^14 * 2048^(K-s)
        MULI 128
        MULI 128
        MULI 16
        MULI 128
        MULI 16
        MULI 128
        MULI 16
        JMP emask
e2:     LDI 128
        MULI 128
        MULI 128
        MULI 16
        MULI 128
        MULI 16
        JMP emask
e1:     LDI 128
        MULI 128
        MULI 128
        MULI 16
        JMP emask
e0:     LDI 128
        MULI 128
emask:  ST  F
        MULI 128
        MULI 16
        SUB F               ; F*2048 - F = F*2047, the grade field's mask
        ST  GMASK
        ADD IDM             ; the fields are disjoint, so + is |
        ST  TMSK
        LD  OP
        SUBI 3
        BRN hgs
        BRZ havg
        JMP htop

; ── GET (1 id s) and SET (2 id s v): one shared scan ────────────────────────
; Both have to find the student first, and the scan is 64 of this program's
; instructions, so they share it: it leaves the cell's *address* in PTR and its
; *value* in CUR, and only then does the second dispatch on OP split them. An
; empty slot's id field is 0 and TARG is never below 6385, so unused cells simply
; never match.
hgs:    LD  C0
        AND IDM
        SUB TARG
        BRZ h0
        LD  C1
        AND IDM
        SUB TARG
        BRZ h1
        LD  C2
        AND IDM
        SUB TARG
        BRZ h2
        LD  C3
        AND IDM
        SUB TARG
        BRZ h3
        LD  C4
        AND IDM
        SUB TARG
        BRZ h4
        LD  C5
        AND IDM
        SUB TARG
        BRZ h5
        LD  C6
        AND IDM
        SUB TARG
        BRZ h6
        LD  C7
        AND IDM
        SUB TARG
        BRZ h7
        LD  C8
        AND IDM
        SUB TARG
        BRZ h8
        LD  C9
        AND IDM
        SUB TARG
        BRZ h9
        LD  C10
        AND IDM
        SUB TARG
        BRZ h10
        LD  C11
        AND IDM
        SUB TARG
        BRZ h11
        LD  C12
        AND IDM
        SUB TARG
        BRZ h12
        LD  C13
        AND IDM
        SUB TARG
        BRZ h13
        LD  C14
        AND IDM
        SUB TARG
        BRZ h14
        LD  C15
        AND IDM
        SUB TARG
        BRZ h15
        JMP nextop          ; unreachable: every operation names a real id
h0:      LDI C0
        ST  PTR
        LD  C0
        JMP gsf
h1:      LDI C1
        ST  PTR
        LD  C1
        JMP gsf
h2:      LDI C2
        ST  PTR
        LD  C2
        JMP gsf
h3:      LDI C3
        ST  PTR
        LD  C3
        JMP gsf
h4:      LDI C4
        ST  PTR
        LD  C4
        JMP gsf
h5:      LDI C5
        ST  PTR
        LD  C5
        JMP gsf
h6:      LDI C6
        ST  PTR
        LD  C6
        JMP gsf
h7:      LDI C7
        ST  PTR
        LD  C7
        JMP gsf
h8:      LDI C8
        ST  PTR
        LD  C8
        JMP gsf
h9:      LDI C9
        ST  PTR
        LD  C9
        JMP gsf
h10:     LDI C10
        ST  PTR
        LD  C10
        JMP gsf
h11:     LDI C11
        ST  PTR
        LD  C11
        JMP gsf
h12:     LDI C12
        ST  PTR
        LD  C12
        JMP gsf
h13:     LDI C13
        ST  PTR
        LD  C13
        JMP gsf
h14:     LDI C14
        ST  PTR
        LD  C14
        JMP gsf
h15:     LDI C15
        ST  PTR
        LD  C15
        JMP gsf
gsf:    ST  CUR
        LD  OP
        SUBI 1
        BRZ hget
        LD  CUR             ; SET: replace subject s's field in place
        AND GMASK
        ST  TMP             ; the old grade, still scaled by F
        LD  V
        MUL F
        SUB TMP             ; (v - old) * F
        ADD CUR
        ST  TMP
        LD  PTR
        MOVA TMP
        JMP nextop
hget:   LD  CUR
        AND GMASK
        DIV F
        OUT
        JMP nextop

; ── AVG (3 s) ───────────────────────────────────────────────────────────────
; One access per student and no per-student arithmetic at all: see the header on
; why the raw cells can be summed and unpicked afterwards.
havg:   LD  C0
        ADD C1
        ADD C2
        ADD C3
        ADD C4
        ADD C5
        ADD C6
        ADD C7
        ADD C8
        ADD C9
        ADD C10
        ADD C11
        ADD C12
        ADD C13
        ADD C14
        ADD C15
        SUB IDSUM           ; = (sum of packed) * 2^14, exactly
        DIV F
        AND M2047           ; subject s's column total; 16*100 < 2048, so no carry
        DIV NN
        OUT
        JMP nextop

; ── TOP (4 s) ───────────────────────────────────────────────────────────────
; The masked cell *is* the answer's key (header), so the scan is a plain running
; maximum with no id lookup, no tie comparison and no seed student: 0 is a legal
; starting best because every real key is at least 6385.
htop:   LDI 0
        ST  BEST
        LD  C0
        AND TMSK
        SUB BEST
        BRN t1
        ADD BEST
        ST  BEST
t1:      LD  C1
        AND TMSK
        SUB BEST
        BRN t2
        ADD BEST
        ST  BEST
t2:      LD  C2
        AND TMSK
        SUB BEST
        BRN t3
        ADD BEST
        ST  BEST
t3:      LD  C3
        AND TMSK
        SUB BEST
        BRN t4
        ADD BEST
        ST  BEST
t4:      LD  C4
        AND TMSK
        SUB BEST
        BRN t5
        ADD BEST
        ST  BEST
t5:      LD  C5
        AND TMSK
        SUB BEST
        BRN t6
        ADD BEST
        ST  BEST
t6:      LD  C6
        AND TMSK
        SUB BEST
        BRN t7
        ADD BEST
        ST  BEST
t7:      LD  C7
        AND TMSK
        SUB BEST
        BRN t8
        ADD BEST
        ST  BEST
t8:      LD  C8
        AND TMSK
        SUB BEST
        BRN t9
        ADD BEST
        ST  BEST
t9:      LD  C9
        AND TMSK
        SUB BEST
        BRN t10
        ADD BEST
        ST  BEST
t10:     LD  C10
        AND TMSK
        SUB BEST
        BRN t11
        ADD BEST
        ST  BEST
t11:     LD  C11
        AND TMSK
        SUB BEST
        BRN t12
        ADD BEST
        ST  BEST
t12:     LD  C12
        AND TMSK
        SUB BEST
        BRN t13
        ADD BEST
        ST  BEST
t13:     LD  C13
        AND TMSK
        SUB BEST
        BRN t14
        ADD BEST
        ST  BEST
t14:     LD  C14
        AND TMSK
        SUB BEST
        BRN t15
        ADD BEST
        ST  BEST
t15:     LD  C15
        AND TMSK
        SUB BEST
        BRN tout
        ADD BEST
        ST  BEST
tout:   LD  BEST
        AND IDM
        ST  CUR
        LDI 128
        MULI 128
        SUB CUR             ; id = 16384 - the winning key's id field
        OUT
        JMP nextop

; ── the round's bookkeeping ─────────────────────────────────────────────────
; Last in the program on purpose: every handler falls out here, and `BRZ round` /
; `JMP oploop` then wrap forward over only the first ~20 instructions. Put this
; block at the top instead and each operation would pay a second ROM lap.
nextop: LD  OCNT
        SUBI 1
        ST  OCNT
        BRZ round           ; the round's replies are complete; unlock the next
        JMP oploop
