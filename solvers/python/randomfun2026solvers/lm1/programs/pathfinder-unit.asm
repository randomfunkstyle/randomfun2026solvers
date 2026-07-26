; pathfinder — a bit-parallel BFS over four 64-bit words, on the PATH unit.
;
; GENERATED (unroll=8 levels=2 walks=4 wordskip=0);
; see `littleman/ARCH.md` §8.3. Edit the generator, not this file.
;
; The board's 256 cells are four 64-bit words: word w covers rows 4w..4w+3, and
; bit b of it is cell `64*w + (63 - b)`. The reversal is not arbitrary — it is
; what lets the setup loop fold the input stream with `acc = 2*acc + v`. Building
; the un-reversed order would need a `1 << 63` literal, which the ROM cannot
; encode as a positive word, and it would drive the accumulator negative and
; break the one property the whole program rests on:
;
;   BIT 63 OF EVERY BITSET IS CLEAR, so every word is non-negative and `DIVI` is
;   a logical shift. Bit 63 of word w is cell 64w — row 4w, column 0 — and the
;   spec guarantees every border cell is a wall. `MULI` is a wrapping multiply,
;   so it is `<<` with no such caveat.
;
; One BFS level, per word, with the four neighbour-source masks:
;
;   Nsrc = (p >> 16) | (PREV[w-1] << 48)     c's up-neighbour is in the frontier
;   Esrc = (p << 1)                          c's right-neighbour is
;   Ssrc = (p << 16) | (PREV[w+1] >> 48)     c's down-neighbour is
;   Wsrc = (p >> 1)                          c's left-neighbour is
;
; Cross-word bleed on Esrc/Wsrc is ignored on purpose: the bits that cross a word
; boundary belong to columns 0 and 15, which are always walls.
;
; THE TIE-BREAK COSTS NOTHING. Taking the four directions in the order up, right,
; down, left and *consuming* AVAIL as each is taken means a cell reachable by two
; directions at one level keeps only the first — exactly the spec's "prefer up,
; then right, then down, then left". No complements, no priority masks.
;
; Two identities the arithmetic leans on, both exact and both asserted in
; `pathfinder_sim.py`: `AVAIL -= n` is `AVAIL & ~n`, because n is always a subset
; of AVAIL, and `Dd += n` is `Dd | n`, because the four direction masks are
; disjoint and no cell is ever added twice. Subtraction and addition are one glyph
; each; the masked forms would need a third register, which does not exist.

        .unit path

; command codes — must match `PathUnit.CODES`
        .equ C_CELL 0
        .equ C_ROBOT 1
        .equ C_FLAG 2
        .equ C_MOVE 3

; tape: 51 slots live, so TAPE_SIZE['pathfinder'] = 52
        .equ PREV 1
        .equ AVAIL 5
        .equ SAVE 9
        .equ HI 13
        .equ LO 17
        .equ DN 21
        .equ DE 25
        .equ DS 29
        .equ DW 33
        .equ FREE 37
        .equ ZERO 41
        .equ TN 42
        .equ RMASK 43
        .equ RWORD 44
        .equ RP 45
        .equ T 46
        .equ CNT 47
        .equ MB 48
        .equ E 49
        .equ FW 50
        .equ FM 51

; ══ setup round: 256 cells, then the robot ═════════════════════════════
        LDI 0
        ST  ZERO
; word 0: fold 64 cells into one bitset, painting each as it arrives.
; Buffer GROUP cells, then fold with the accumulator in ACC. `IN` clobbers
; ACC, so `acc = 2*acc + v` with the accumulator in the tape costs two reads a
; cell however it is written; buffering inverts that, a write being ~19 ticks
; against a read's ~400. The buffer reuses HI, idle until the first level — a
; new slot would tax every read in the program by 8 ticks (§4.1).
        LDI 0
        ST  T
        LDI 8
        ST  CNT
cell0:
        IN
        MULI 8
        SND
        DIVI 8
        ST  13
        IN
        MULI 8
        SND
        DIVI 8
        ST  14
        IN
        MULI 8
        SND
        DIVI 8
        ST  15
        IN
        MULI 8
        SND
        DIVI 8
        ST  16
        LD  T
        MULI 2
        ADD 13
        MULI 2
        ADD 14
        MULI 2
        ADD 15
        MULI 2
        ADD 16
        ST  T
        IN
        MULI 8
        SND
        DIVI 8
        ST  13
        IN
        MULI 8
        SND
        DIVI 8
        ST  14
        IN
        MULI 8
        SND
        DIVI 8
        ST  15
        IN
        MULI 8
        SND
        DIVI 8
        ST  16
        LD  T
        MULI 2
        ADD 13
        MULI 2
        ADD 14
        MULI 2
        ADD 15
        MULI 2
        ADD 16
        ST  T
        LD  CNT
        NEG
        ADDI 1
        NEG
        ST  CNT
        BRZ done0
        JMP cell0
done0:
; T folded the WALL bits, so complement: ~x is -1-x, i.e. NEG(x+1). Bit 63 of
; T is this word's first cell — row 4w column 0, a border wall — so it is 1,
; which is exactly what leaves the complement's bit 63 clear.
        LD  T
        ADDI 1
        NEG
        ST  37
; word 1: fold 64 cells into one bitset, painting each as it arrives.
; Buffer GROUP cells, then fold with the accumulator in ACC. `IN` clobbers
; ACC, so `acc = 2*acc + v` with the accumulator in the tape costs two reads a
; cell however it is written; buffering inverts that, a write being ~19 ticks
; against a read's ~400. The buffer reuses HI, idle until the first level — a
; new slot would tax every read in the program by 8 ticks (§4.1).
        LDI 0
        ST  T
        LDI 8
        ST  CNT
cell1:
        IN
        MULI 8
        SND
        DIVI 8
        ST  13
        IN
        MULI 8
        SND
        DIVI 8
        ST  14
        IN
        MULI 8
        SND
        DIVI 8
        ST  15
        IN
        MULI 8
        SND
        DIVI 8
        ST  16
        LD  T
        MULI 2
        ADD 13
        MULI 2
        ADD 14
        MULI 2
        ADD 15
        MULI 2
        ADD 16
        ST  T
        IN
        MULI 8
        SND
        DIVI 8
        ST  13
        IN
        MULI 8
        SND
        DIVI 8
        ST  14
        IN
        MULI 8
        SND
        DIVI 8
        ST  15
        IN
        MULI 8
        SND
        DIVI 8
        ST  16
        LD  T
        MULI 2
        ADD 13
        MULI 2
        ADD 14
        MULI 2
        ADD 15
        MULI 2
        ADD 16
        ST  T
        LD  CNT
        NEG
        ADDI 1
        NEG
        ST  CNT
        BRZ done1
        JMP cell1
done1:
; T folded the WALL bits, so complement: ~x is -1-x, i.e. NEG(x+1). Bit 63 of
; T is this word's first cell — row 4w column 0, a border wall — so it is 1,
; which is exactly what leaves the complement's bit 63 clear.
        LD  T
        ADDI 1
        NEG
        ST  38
; word 2: fold 64 cells into one bitset, painting each as it arrives.
; Buffer GROUP cells, then fold with the accumulator in ACC. `IN` clobbers
; ACC, so `acc = 2*acc + v` with the accumulator in the tape costs two reads a
; cell however it is written; buffering inverts that, a write being ~19 ticks
; against a read's ~400. The buffer reuses HI, idle until the first level — a
; new slot would tax every read in the program by 8 ticks (§4.1).
        LDI 0
        ST  T
        LDI 8
        ST  CNT
cell2:
        IN
        MULI 8
        SND
        DIVI 8
        ST  13
        IN
        MULI 8
        SND
        DIVI 8
        ST  14
        IN
        MULI 8
        SND
        DIVI 8
        ST  15
        IN
        MULI 8
        SND
        DIVI 8
        ST  16
        LD  T
        MULI 2
        ADD 13
        MULI 2
        ADD 14
        MULI 2
        ADD 15
        MULI 2
        ADD 16
        ST  T
        IN
        MULI 8
        SND
        DIVI 8
        ST  13
        IN
        MULI 8
        SND
        DIVI 8
        ST  14
        IN
        MULI 8
        SND
        DIVI 8
        ST  15
        IN
        MULI 8
        SND
        DIVI 8
        ST  16
        LD  T
        MULI 2
        ADD 13
        MULI 2
        ADD 14
        MULI 2
        ADD 15
        MULI 2
        ADD 16
        ST  T
        LD  CNT
        NEG
        ADDI 1
        NEG
        ST  CNT
        BRZ done2
        JMP cell2
done2:
; T folded the WALL bits, so complement: ~x is -1-x, i.e. NEG(x+1). Bit 63 of
; T is this word's first cell — row 4w column 0, a border wall — so it is 1,
; which is exactly what leaves the complement's bit 63 clear.
        LD  T
        ADDI 1
        NEG
        ST  39
; word 3: fold 64 cells into one bitset, painting each as it arrives.
; Buffer GROUP cells, then fold with the accumulator in ACC. `IN` clobbers
; ACC, so `acc = 2*acc + v` with the accumulator in the tape costs two reads a
; cell however it is written; buffering inverts that, a write being ~19 ticks
; against a read's ~400. The buffer reuses HI, idle until the first level — a
; new slot would tax every read in the program by 8 ticks (§4.1).
        LDI 0
        ST  T
        LDI 8
        ST  CNT
cell3:
        IN
        MULI 8
        SND
        DIVI 8
        ST  13
        IN
        MULI 8
        SND
        DIVI 8
        ST  14
        IN
        MULI 8
        SND
        DIVI 8
        ST  15
        IN
        MULI 8
        SND
        DIVI 8
        ST  16
        LD  T
        MULI 2
        ADD 13
        MULI 2
        ADD 14
        MULI 2
        ADD 15
        MULI 2
        ADD 16
        ST  T
        IN
        MULI 8
        SND
        DIVI 8
        ST  13
        IN
        MULI 8
        SND
        DIVI 8
        ST  14
        IN
        MULI 8
        SND
        DIVI 8
        ST  15
        IN
        MULI 8
        SND
        DIVI 8
        ST  16
        LD  T
        MULI 2
        ADD 13
        MULI 2
        ADD 14
        MULI 2
        ADD 15
        MULI 2
        ADD 16
        ST  T
        LD  CNT
        NEG
        ADDI 1
        NEG
        ST  CNT
        BRZ done3
        JMP cell3
done3:
; T folded the WALL bits, so complement: ~x is -1-x, i.e. NEG(x+1). Bit 63 of
; T is this word's first cell — row 4w column 0, a border wall — so it is 1,
; which is exactly what leaves the complement's bit 63 clear.
        LD  T
        ADDI 1
        NEG
        ST  40
; the robot: paint it, commit, and that is the setup round's one frame
        IN
        ST  T
        IN
        MULI 16
        ADD T
        ST  RP
        MULI 8
        ADDI C_ROBOT
        SND
        DIVI 8
; its word and one-hot, by binary decomposition — six conditional multiplies
; rather than a power-of-two table, because 64 table slots would tax every read
; in the program (§4.1: 8.06 ticks a slot) far beyond what they could save.
        ST  T
        DIVI 64
        ST  RWORD
        LD  T
        MODI 64
        NEG
        ADDI 63
        ST  E
        LDI 1
        ST  RMASK
        LD  E
        MODI 2
        BRZ rs0
        LD  RMASK
        MULI 2
        ST  RMASK
rs0:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ rs1
        LD  RMASK
        MULI 4
        ST  RMASK
rs1:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ rs2
        LD  RMASK
        MULI 16
        ST  RMASK
rs2:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ rs3
        LD  RMASK
        MULI 256
        ST  RMASK
rs3:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ rs4
        LD  RMASK
        MULI 65536
        ST  RMASK
rs4:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ rs5
        LD  RMASK
        MULI 4294967296
        ST  RMASK
rs5:
        LD  E
        DIVI 2
        ST  E

; ══ one pathfinding round ══════════════════════════════════════════════
round:
; AVAIL = FREE, and the direction masks and the frontier start empty
        LD  37
        ST  5
        LD  38
        ST  6
        LD  39
        ST  7
        LD  40
        ST  8
        LDI 0
        ST  21
        ST  22
        ST  23
        ST  24
        ST  25
        ST  26
        ST  27
        ST  28
        ST  29
        ST  30
        ST  31
        ST  32
        ST  33
        ST  34
        ST  35
        ST  36
        ST  1
        ST  2
        ST  3
        ST  4
; the flag: paint it, with no commit — the flag is not a frame of its own
        IN
        ST  T
        IN
        MULI 16
        ADD T
        MULI 8
        ADDI C_FLAG
        SND
        DIVI 8
        ST  T
        DIVI 64
        ST  FW
        LD  T
        MODI 64
        NEG
        ADDI 63
        ST  E
        LDI 1
        ST  FM
        LD  E
        MODI 2
        BRZ fs0
        LD  FM
        MULI 2
        ST  FM
fs0:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ fs1
        LD  FM
        MULI 4
        ST  FM
fs1:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ fs2
        LD  FM
        MULI 16
        ST  FM
fs2:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ fs3
        LD  FM
        MULI 256
        ST  FM
fs3:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ fs4
        LD  FM
        MULI 65536
        ST  FM
fs4:
        LD  E
        DIVI 2
        ST  E
        LD  E
        MODI 2
        BRZ fs5
        LD  FM
        MULI 4294967296
        ST  FM
fs5:
        LD  E
        DIVI 2
        ST  E
; seed the frontier with the flag and take it out of AVAIL. A four-way ladder,
; not an indexed write: `ST` cannot be indexed and MOVA would cost a read, where
; this costs only ROM.
        LD  FW
        BRZ fw0
        LD  FW
        NEG
        ADDI 1
        BRZ fw1
        LD  FW
        NEG
        ADDI 2
        BRZ fw2
        JMP fw3
fw0:
        LD  FM
        ST  1
        LD  FM
        NEG
        ADD 5
        ST  5
        JMP level
fw1:
        LD  FM
        ST  2
        LD  FM
        NEG
        ADD 6
        ST  6
        JMP level
fw2:
        LD  FM
        ST  3
        LD  FM
        NEG
        ADD 7
        ST  7
        JMP level
fw3:
        LD  FM
        ST  4
        LD  FM
        NEG
        ADD 8
        ST  8

; ══ one BFS level ══════════════════════════════════════════════════════
; Unrolled 2x. A backward jump recirculates `P - body` ROM words at ~5.9
; ticks each, so every iteration pays for all of the program's non-loop code
; whether it runs or not (§5.4, and §8.1's fourth lever). Copies divide that tax
; by their count and cost only ROM cells.
level:
; ── copy 0: pass 1, the cross-word shifts and AVAIL's snapshot
        LD  1
        DIVI 281474976710656
        ST  13
        LD  1
        MULI 281474976710656
        ST  17
        LD  5
        ST  9
        LD  2
        DIVI 281474976710656
        ST  14
        LD  2
        MULI 281474976710656
        ST  18
        LD  6
        ST  10
        LD  3
        DIVI 281474976710656
        ST  15
        LD  3
        MULI 281474976710656
        ST  19
        LD  7
        ST  11
        LD  4
        DIVI 281474976710656
        ST  16
        LD  4
        MULI 281474976710656
        ST  20
        LD  8
        ST  12
; pass 2: the four directions in priority order, word by word
        LD  1
        DIVI 65536
        OR  41
        AND 5
        BRZ n0x_0
        ST  TN
        NEG
        ADD 5
        ST  5
        LD  TN
        ADD 21
        ST  21
n0x_0:
        LD  1
        MULI 2
        AND 5
        BRZ e0x_0
        ST  TN
        NEG
        ADD 5
        ST  5
        LD  TN
        ADD 25
        ST  25
e0x_0:
        LD  1
        MULI 65536
        OR  14
        AND 5
        BRZ s0x_0
        ST  TN
        NEG
        ADD 5
        ST  5
        LD  TN
        ADD 29
        ST  29
s0x_0:
        LD  1
        DIVI 2
        AND 5
        BRZ w0x_0
        ST  TN
        NEG
        ADD 5
        ST  5
        LD  TN
        ADD 33
        ST  33
w0x_0:
        LD  2
        DIVI 65536
        OR  17
        AND 6
        BRZ n1x_0
        ST  TN
        NEG
        ADD 6
        ST  6
        LD  TN
        ADD 22
        ST  22
n1x_0:
        LD  2
        MULI 2
        AND 6
        BRZ e1x_0
        ST  TN
        NEG
        ADD 6
        ST  6
        LD  TN
        ADD 26
        ST  26
e1x_0:
        LD  2
        MULI 65536
        OR  15
        AND 6
        BRZ s1x_0
        ST  TN
        NEG
        ADD 6
        ST  6
        LD  TN
        ADD 30
        ST  30
s1x_0:
        LD  2
        DIVI 2
        AND 6
        BRZ w1x_0
        ST  TN
        NEG
        ADD 6
        ST  6
        LD  TN
        ADD 34
        ST  34
w1x_0:
        LD  3
        DIVI 65536
        OR  18
        AND 7
        BRZ n2x_0
        ST  TN
        NEG
        ADD 7
        ST  7
        LD  TN
        ADD 23
        ST  23
n2x_0:
        LD  3
        MULI 2
        AND 7
        BRZ e2x_0
        ST  TN
        NEG
        ADD 7
        ST  7
        LD  TN
        ADD 27
        ST  27
e2x_0:
        LD  3
        MULI 65536
        OR  16
        AND 7
        BRZ s2x_0
        ST  TN
        NEG
        ADD 7
        ST  7
        LD  TN
        ADD 31
        ST  31
s2x_0:
        LD  3
        DIVI 2
        AND 7
        BRZ w2x_0
        ST  TN
        NEG
        ADD 7
        ST  7
        LD  TN
        ADD 35
        ST  35
w2x_0:
        LD  4
        DIVI 65536
        OR  19
        AND 8
        BRZ n3x_0
        ST  TN
        NEG
        ADD 8
        ST  8
        LD  TN
        ADD 24
        ST  24
n3x_0:
        LD  4
        MULI 2
        AND 8
        BRZ e3x_0
        ST  TN
        NEG
        ADD 8
        ST  8
        LD  TN
        ADD 28
        ST  28
e3x_0:
        LD  4
        MULI 65536
        OR  41
        AND 8
        BRZ s3x_0
        ST  TN
        NEG
        ADD 8
        ST  8
        LD  TN
        ADD 32
        ST  32
s3x_0:
        LD  4
        DIVI 2
        AND 8
        BRZ w3x_0
        ST  TN
        NEG
        ADD 8
        ST  8
        LD  TN
        ADD 36
        ST  36
w3x_0:
; the next frontier is exactly what this level took out of AVAIL
        LD  5
        NEG
        ADD 9
        ST  1
        LD  6
        NEG
        ADD 10
        ST  2
        LD  7
        NEG
        ADD 11
        ST  3
        LD  8
        NEG
        ADD 12
        ST  4
; has the wave reached the robot?
        LD  RWORD
        ADDI PREV
        LDA
        AND RMASK
        BRZ more_0
        JMP walk
more_0:
; ── copy 1: pass 1, the cross-word shifts and AVAIL's snapshot
        LD  1
        DIVI 281474976710656
        ST  13
        LD  1
        MULI 281474976710656
        ST  17
        LD  5
        ST  9
        LD  2
        DIVI 281474976710656
        ST  14
        LD  2
        MULI 281474976710656
        ST  18
        LD  6
        ST  10
        LD  3
        DIVI 281474976710656
        ST  15
        LD  3
        MULI 281474976710656
        ST  19
        LD  7
        ST  11
        LD  4
        DIVI 281474976710656
        ST  16
        LD  4
        MULI 281474976710656
        ST  20
        LD  8
        ST  12
; pass 2: the four directions in priority order, word by word
        LD  1
        DIVI 65536
        OR  41
        AND 5
        BRZ n0x_1
        ST  TN
        NEG
        ADD 5
        ST  5
        LD  TN
        ADD 21
        ST  21
n0x_1:
        LD  1
        MULI 2
        AND 5
        BRZ e0x_1
        ST  TN
        NEG
        ADD 5
        ST  5
        LD  TN
        ADD 25
        ST  25
e0x_1:
        LD  1
        MULI 65536
        OR  14
        AND 5
        BRZ s0x_1
        ST  TN
        NEG
        ADD 5
        ST  5
        LD  TN
        ADD 29
        ST  29
s0x_1:
        LD  1
        DIVI 2
        AND 5
        BRZ w0x_1
        ST  TN
        NEG
        ADD 5
        ST  5
        LD  TN
        ADD 33
        ST  33
w0x_1:
        LD  2
        DIVI 65536
        OR  17
        AND 6
        BRZ n1x_1
        ST  TN
        NEG
        ADD 6
        ST  6
        LD  TN
        ADD 22
        ST  22
n1x_1:
        LD  2
        MULI 2
        AND 6
        BRZ e1x_1
        ST  TN
        NEG
        ADD 6
        ST  6
        LD  TN
        ADD 26
        ST  26
e1x_1:
        LD  2
        MULI 65536
        OR  15
        AND 6
        BRZ s1x_1
        ST  TN
        NEG
        ADD 6
        ST  6
        LD  TN
        ADD 30
        ST  30
s1x_1:
        LD  2
        DIVI 2
        AND 6
        BRZ w1x_1
        ST  TN
        NEG
        ADD 6
        ST  6
        LD  TN
        ADD 34
        ST  34
w1x_1:
        LD  3
        DIVI 65536
        OR  18
        AND 7
        BRZ n2x_1
        ST  TN
        NEG
        ADD 7
        ST  7
        LD  TN
        ADD 23
        ST  23
n2x_1:
        LD  3
        MULI 2
        AND 7
        BRZ e2x_1
        ST  TN
        NEG
        ADD 7
        ST  7
        LD  TN
        ADD 27
        ST  27
e2x_1:
        LD  3
        MULI 65536
        OR  16
        AND 7
        BRZ s2x_1
        ST  TN
        NEG
        ADD 7
        ST  7
        LD  TN
        ADD 31
        ST  31
s2x_1:
        LD  3
        DIVI 2
        AND 7
        BRZ w2x_1
        ST  TN
        NEG
        ADD 7
        ST  7
        LD  TN
        ADD 35
        ST  35
w2x_1:
        LD  4
        DIVI 65536
        OR  19
        AND 8
        BRZ n3x_1
        ST  TN
        NEG
        ADD 8
        ST  8
        LD  TN
        ADD 24
        ST  24
n3x_1:
        LD  4
        MULI 2
        AND 8
        BRZ e3x_1
        ST  TN
        NEG
        ADD 8
        ST  8
        LD  TN
        ADD 28
        ST  28
e3x_1:
        LD  4
        MULI 65536
        OR  41
        AND 8
        BRZ s3x_1
        ST  TN
        NEG
        ADD 8
        ST  8
        LD  TN
        ADD 32
        ST  32
s3x_1:
        LD  4
        DIVI 2
        AND 8
        BRZ w3x_1
        ST  TN
        NEG
        ADD 8
        ST  8
        LD  TN
        ADD 36
        ST  36
w3x_1:
; the next frontier is exactly what this level took out of AVAIL
        LD  5
        NEG
        ADD 9
        ST  1
        LD  6
        NEG
        ADD 10
        ST  2
        LD  7
        NEG
        ADD 11
        ST  3
        LD  8
        NEG
        ADD 12
        ST  4
; has the wave reached the robot?
        LD  RWORD
        ADDI PREV
        LDA
        AND RMASK
        BRZ level

; ══ walk the robot to the flag, one frame per move ══════════════════════
; The masks are read in the order they were built, so the walk needs no
; comparison at all: exactly one of them holds the robot's cell. Falling through
; all four means no direction was recorded here, which happens only on the flag —
; the one reached cell that never gets one — so that is the round's exit test,
; and it costs nothing.
walk:
ntry_0:
        LD  RWORD
        ADDI DN
        LDA
        AND RMASK
        BRZ etry_0
        LD  RP
        NEG
        ADDI 16
        NEG
        ST  RP
; a move of 16 can carry the one-hot out of its word; the shift
; vanishing to zero is exactly that event, and the pre-move mask
; shifted the other way by 48 is where the bit lands.
        LD  RMASK
        ST  MB
        MULI 65536
        BRZ nc_0
        ST  RMASK
        JMP emit_0
nc_0:
        LD  MB
        DIVI 281474976710656
        ST  RMASK
        LD  RWORD
        NEG
        ADDI 1
        NEG
        ST  RWORD
        JMP emit_0
etry_0:
        LD  RWORD
        ADDI DE
        LDA
        AND RMASK
        BRZ stry_0
        LD  RP
        ADDI 1
        ST  RP
        LD  RMASK
        DIVI 2
        ST  RMASK
        JMP emit_0
stry_0:
        LD  RWORD
        ADDI DS
        LDA
        AND RMASK
        BRZ wtry_0
        LD  RP
        ADDI 16
        ST  RP
; a move of 16 can carry the one-hot out of its word; the shift
; vanishing to zero is exactly that event, and the pre-move mask
; shifted the other way by 48 is where the bit lands.
        LD  RMASK
        ST  MB
        DIVI 65536
        BRZ sc_0
        ST  RMASK
        JMP emit_0
sc_0:
        LD  MB
        MULI 281474976710656
        ST  RMASK
        LD  RWORD
        ADDI 1
        ST  RWORD
        JMP emit_0
wtry_0:
        LD  RWORD
        ADDI DW
        LDA
        AND RMASK
        BRZ round
        LD  RP
        NEG
        ADDI 1
        NEG
        ST  RP
        LD  RMASK
        MULI 2
        ST  RMASK
        JMP emit_0
emit_0:
        LD  RP
        MULI 8
        ADDI C_MOVE
        SND
ntry_1:
        LD  RWORD
        ADDI DN
        LDA
        AND RMASK
        BRZ etry_1
        LD  RP
        NEG
        ADDI 16
        NEG
        ST  RP
; a move of 16 can carry the one-hot out of its word; the shift
; vanishing to zero is exactly that event, and the pre-move mask
; shifted the other way by 48 is where the bit lands.
        LD  RMASK
        ST  MB
        MULI 65536
        BRZ nc_1
        ST  RMASK
        JMP emit_1
nc_1:
        LD  MB
        DIVI 281474976710656
        ST  RMASK
        LD  RWORD
        NEG
        ADDI 1
        NEG
        ST  RWORD
        JMP emit_1
etry_1:
        LD  RWORD
        ADDI DE
        LDA
        AND RMASK
        BRZ stry_1
        LD  RP
        ADDI 1
        ST  RP
        LD  RMASK
        DIVI 2
        ST  RMASK
        JMP emit_1
stry_1:
        LD  RWORD
        ADDI DS
        LDA
        AND RMASK
        BRZ wtry_1
        LD  RP
        ADDI 16
        ST  RP
; a move of 16 can carry the one-hot out of its word; the shift
; vanishing to zero is exactly that event, and the pre-move mask
; shifted the other way by 48 is where the bit lands.
        LD  RMASK
        ST  MB
        DIVI 65536
        BRZ sc_1
        ST  RMASK
        JMP emit_1
sc_1:
        LD  MB
        MULI 281474976710656
        ST  RMASK
        LD  RWORD
        ADDI 1
        ST  RWORD
        JMP emit_1
wtry_1:
        LD  RWORD
        ADDI DW
        LDA
        AND RMASK
        BRZ round
        LD  RP
        NEG
        ADDI 1
        NEG
        ST  RP
        LD  RMASK
        MULI 2
        ST  RMASK
        JMP emit_1
emit_1:
        LD  RP
        MULI 8
        ADDI C_MOVE
        SND
ntry_2:
        LD  RWORD
        ADDI DN
        LDA
        AND RMASK
        BRZ etry_2
        LD  RP
        NEG
        ADDI 16
        NEG
        ST  RP
; a move of 16 can carry the one-hot out of its word; the shift
; vanishing to zero is exactly that event, and the pre-move mask
; shifted the other way by 48 is where the bit lands.
        LD  RMASK
        ST  MB
        MULI 65536
        BRZ nc_2
        ST  RMASK
        JMP emit_2
nc_2:
        LD  MB
        DIVI 281474976710656
        ST  RMASK
        LD  RWORD
        NEG
        ADDI 1
        NEG
        ST  RWORD
        JMP emit_2
etry_2:
        LD  RWORD
        ADDI DE
        LDA
        AND RMASK
        BRZ stry_2
        LD  RP
        ADDI 1
        ST  RP
        LD  RMASK
        DIVI 2
        ST  RMASK
        JMP emit_2
stry_2:
        LD  RWORD
        ADDI DS
        LDA
        AND RMASK
        BRZ wtry_2
        LD  RP
        ADDI 16
        ST  RP
; a move of 16 can carry the one-hot out of its word; the shift
; vanishing to zero is exactly that event, and the pre-move mask
; shifted the other way by 48 is where the bit lands.
        LD  RMASK
        ST  MB
        DIVI 65536
        BRZ sc_2
        ST  RMASK
        JMP emit_2
sc_2:
        LD  MB
        MULI 281474976710656
        ST  RMASK
        LD  RWORD
        ADDI 1
        ST  RWORD
        JMP emit_2
wtry_2:
        LD  RWORD
        ADDI DW
        LDA
        AND RMASK
        BRZ round
        LD  RP
        NEG
        ADDI 1
        NEG
        ST  RP
        LD  RMASK
        MULI 2
        ST  RMASK
        JMP emit_2
emit_2:
        LD  RP
        MULI 8
        ADDI C_MOVE
        SND
ntry_3:
        LD  RWORD
        ADDI DN
        LDA
        AND RMASK
        BRZ etry_3
        LD  RP
        NEG
        ADDI 16
        NEG
        ST  RP
; a move of 16 can carry the one-hot out of its word; the shift
; vanishing to zero is exactly that event, and the pre-move mask
; shifted the other way by 48 is where the bit lands.
        LD  RMASK
        ST  MB
        MULI 65536
        BRZ nc_3
        ST  RMASK
        JMP emit_3
nc_3:
        LD  MB
        DIVI 281474976710656
        ST  RMASK
        LD  RWORD
        NEG
        ADDI 1
        NEG
        ST  RWORD
        JMP emit_3
etry_3:
        LD  RWORD
        ADDI DE
        LDA
        AND RMASK
        BRZ stry_3
        LD  RP
        ADDI 1
        ST  RP
        LD  RMASK
        DIVI 2
        ST  RMASK
        JMP emit_3
stry_3:
        LD  RWORD
        ADDI DS
        LDA
        AND RMASK
        BRZ wtry_3
        LD  RP
        ADDI 16
        ST  RP
; a move of 16 can carry the one-hot out of its word; the shift
; vanishing to zero is exactly that event, and the pre-move mask
; shifted the other way by 48 is where the bit lands.
        LD  RMASK
        ST  MB
        DIVI 65536
        BRZ sc_3
        ST  RMASK
        JMP emit_3
sc_3:
        LD  MB
        MULI 281474976710656
        ST  RMASK
        LD  RWORD
        ADDI 1
        ST  RWORD
        JMP emit_3
wtry_3:
        LD  RWORD
        ADDI DW
        LDA
        AND RMASK
        BRZ round
        LD  RP
        NEG
        ADDI 1
        NEG
        ST  RP
        LD  RMASK
        MULI 2
        ST  RMASK
        JMP emit_3
emit_3:
        LD  RP
        MULI 8
        ADDI C_MOVE
        SND
        JMP walk
