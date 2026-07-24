Packet Reassembly
Semester 2
Solve in editor →
Scoring: footprint-tick. See scoring and rounds for help.

Reassemble a stream of packets.

A test case carries one stream of n packets, numbered seq = 0 .. n-1, each with a value val. Round 1 delivers n, then the first packet; every later round delivers one more packet — seq val — and the packets arrive in some scrambled order.

Output the packets in the correct order, as early as possible. For example:

Your program begins. You are waiting for packet 0
Packet (2, 30) arrives. No output - you're waiting for 0
Packet (0, 10) arrives. Output 10 because 0 arrived. You are waiting for 1
Packet (1, 20) arrives. Output 20 30 because 1 arrived and you already know the value for 2
Packet (3, 40) arrives. Output 40 because you are now waiting for 3.
At any moment, you are waiting for the lowest-numbered packet you haven't seen yet. When it arrives, you should output its value (and so on, until you hit a gap). As you can see, a single arrival can produce no output, one value of output, or several values of output at once. You won't see the next packet until you've output everything the current one unlocks.

Maximum delay: If a packet arrives with a seq that is 16 or more above the seq you are waiting for, output -1 and stop. For example, if the first packet you receive is (16, 1) you should output -1 and stop.

Format
Input. A run of integers, until it ends.

Round 1 carries n (the stream length), then the first packet as seq val; every later round carries one packet: seq val.

input ⟶ int*

e.g.
  round 1:  17 15 900
  round 2:  0 100
  round 3:  1 101
Output. A run of integers, until it ends.

The values this round's packet drained, in seq order (possibly none); -1 means the stream is lost, and is always the test case's final output.

output ⟶ int*

e.g.
  round 2:  100
  round 3:  101
Constraints
1 ≤ n ≤ 48 — stream length
0 ≤ seq < n, distinct within a test case
1 ≤ val ≤ 999


Public test cases (6)

in-order stream
Round 1
in: 6 0 100
out: 100
Round 2
in: 1 101
out: 101
Round 3
in: 2 102
out: 102
Round 4
in: 3 103
out: 103
Round 5
in: 4 104
out: 104
Round 6
in: 5 105
out: 105


single max-displacement swap
Round 1
in: 17 15 900
out: (none)
Round 2
in: 0 100
out: 100
Round 3
in: 1 101
out: 101
Round 4
in: 2 102
out: 102
Round 5
in: 3 103
out: 103
Round 6
in: 4 104
out: 104
Round 7
in: 5 105
out: 105
Round 8
in: 6 106
out: 106
Round 9
in: 7 107
out: 107
Round 10
in: 8 108
out: 108
Round 11
in: 9 109
out: 109
Round 12
in: 10 110
out: 110
Round 13
in: 11 111
out: 111
Round 14
in: 12 112
out: 112
Round 15
in: 13 113
out: 113
Round 16
in: 14 114
out: 114 900
Round 17
in: 16 999
out: 999



drain burst
Round 1
in: 16 15 215
out: (none)
Round 2
in: 14 214
out: (none)
Round 3
in: 13 213
out: (none)
Round 4
in: 12 212
out: (none)
Round 5
in: 11 211
out: (none)
Round 6
in: 10 210
out: (none)
Round 7
in: 9 209
out: (none)
Round 8
in: 8 208
out: (none)
Round 9
in: 7 207
out: (none)
Round 10
in: 6 206
out: (none)
Round 11
in: 5 205
out: (none)
Round 12
in: 4 204
out: (none)
Round 13
in: 3 203
out: (none)
Round 14
in: 2 202
out: (none)
Round 15
in: 1 201
out: (none)
Round 16
in: 0 200
out: 200 201 202 203 204 205 206 207 208 209 210 211 212 213 214 215



loss case
Round 1
in: 20 1 301
out: (none)
Round 2
in: 2 302
out: (none)
Round 3
in: 3 303
out: (none)
Round 4
in: 4 304
out: (none)
Round 5
in: 5 305
out: (none)
Round 6
in: 6 306
out: (none)
Round 7
in: 7 307
out: (none)
Round 8
in: 8 308
out: (none)
Round 9
in: 9 309
out: (none)
Round 10
in: 10 310
out: (none)
Round 11
in: 11 311
out: (none)
Round 12
in: 12 312
out: (none)
Round 13
in: 13 313
out: (none)
Round 14
in: 14 314
out: (none)
Round 15
in: 15 315
out: (none)
Round 16
in: 16 316
out: -1


shortest stream
in: 1 0 42
out: 42


block-reversed n=32
Round 1
in: 32 15 337
out: (none)
Round 2
in: 14 423
out: (none)
Round 3
in: 13 400
out: (none)
Round 4
in: 12 169
out: (none)
Round 5
in: 11 612
out: (none)
Round 6
in: 10 680
out: (none)
Round 7
in: 9 842
out: (none)
Round 8
in: 8 145
out: (none)
Round 9
in: 7 448
out: (none)
Round 10
in: 6 352
out: (none)
Round 11
in: 5 968
out: (none)
Round 12
in: 4 974
out: (none)
Round 13
in: 3 317
out: (none)
Round 14
in: 2 955
out: (none)
Round 15
in: 1 879
out: (none)
Round 16
in: 0 462
out: 462 879 955 317 974 968 352 448 145 842 680 612 169 400 423 337
Round 17
in: 31 104
out: (none)
Round 18
in: 30 133
out: (none)
Round 19
in: 29 268
out: (none)
Round 20
in: 28 907
out: (none)
Round 21
in: 27 249
out: (none)
Round 22
in: 26 641
out: (none)
Round 23
in: 25 620
out: (none)
Round 24
in: 24 566
out: (none)
Round 25
in: 23 362
out: (none)
Round 26
in: 22 501
out: (none)
Round 27
in: 21 233
out: (none)
Round 28
in: 20 426
out: (none)
Round 29
in: 19 320
out: (none)
Round 30
in: 18 272
out: (none)
Round 31
in: 17 985
out: (none)
Round 32
in: 16 190
out: 190 985 272 320 426 233 501 362 566 620 641 249 907 268 133 104
