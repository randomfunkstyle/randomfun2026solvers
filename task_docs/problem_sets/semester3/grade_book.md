Grade Book
Semester 3
Solve in editor →
Scoring: footprint-tick. See scoring and rounds for help.

Process operations over student grades across several subjects.

A grade book tracks N students across K subjects. Subjects are numbered 1 through K. A student record is a unique id followed by one grade per subject, in subject order: id g1 g2 ... gK. Round 1 provides N K, then the N student records.

An operation is an integer op naming an action to perform, followed by that action's arguments. Rounds 2 and beyond provide a count O and then O operations. Your program should process each operation in order, outputting data as it is requested.

There are 4 operations, so op is between 1 and 4. The operations are:

GET (op=1) - 1 id s - output student id's grade in subject s
SET (op=2) - 2 id s v - set student id's grade in subject s to v
AVG (op=3) - 3 s - output the average grade in subject s rounded down
TOP (op=4) - 4 s - output the id of the student with the highest grade in subject s
Your program does not need to output anything for SET operations. If multiple students are tied for the highest grade in a subject, TOP should return the smallest such student id.

Format
Input. A run of integers, until it ends.

Round 1 is the roster: N K, then the N student records id g1 ... gK back-to-back. Every later round is a batch: a count O, then O operations back-to-back.

input ⟶ int*

e.g.
  round 1:  4 1 1222 51 2774 23 8603 44 2303 76
  round 2:  2 1 1222 1 1 2774 1
  round 3:  2 2 1222 1 77 1 1222 1
Output. A run of integers, until it ends.

A round's output is its GET/AVG/TOP replies, in operation order. The roster round and SET operations produce no output.

output ⟶ int*

e.g.
  round 2:  51 23
  round 3:  77
Constraints
4 ≤ N ≤ 16 — students
1 ≤ K ≤ 4 — subjects
student ids distinct, 1000 ≤ id ≤ 9999, not dense, not sorted
0 ≤ g ≤ 100 — grades, initial or set
every operation references an existing id and a subject in 1..K
1–10 batch rounds per test case, 1–8 operations per batch
one roster per test case (no reload)
Public test cases (7)
tiny roster walkthrough
Round 1
in: 4 1 1222 51 2774 23 8603 44 2303 76
out: (none)
Round 2
in: 2 1 1222 1 1 2774 1
out: 51 23
Round 3
in: 2 2 1222 1 77 1 1222 1
out: 77
TOP demotion
Round 1
in: 5 1 7099 24 6818 39 6935 79 3928 78 1455 45
out: (none)
Round 2
in: 2 2 6935 1 69 4 1
out: 3928
Round 3
in: 4 2 3928 1 68 1 1455 1 4 1 4 1
out: 45 6935 6935
Round 4
in: 1 2 6935 1 67
out: (none)
Round 5
in: 1 4 1
out: 3928
tie-break
Round 1
in: 6 2 9091 59 50 3820 63 68 9523 28 32 3890 83 3 8927 65 12 7697 63 23
out: (none)
Round 2
in: 4 2 3820 1 88 2 8927 1 88 2 9523 1 60 4 1
out: 3820
Round 3
in: 4 2 3820 1 40 4 1 2 9523 1 88 4 1
out: 8927 8927
floor rounding
Round 1
in: 5 2 5681 16 93 7095 77 38 7308 11 74 1024 54 7 9206 99 66
out: (none)
Round 2
in: 6 2 5681 1 10 2 7095 1 20 2 7308 1 33 2 1024 1 41 2 9206 1 7 3 1
out: 22
Round 3
in: 3 3 1 2 5681 1 100 3 1
out: 22 40
mixed batch
Round 1
in: 8 2 4205 7 34 8263 25 72 8532 47 5 6439 53 55 3939 29 54 5517 35 93 7017 15 43 4626 27 43
out: (none)
Round 2
in: 8 1 4205 1 2 8263 2 95 3 1 4 2 2 8532 1 12 1 8532 1 4 1 3 2
out: 7 29 8263 12 6439 52
K=1 minimal
Round 1
in: 4 1 8587 72 2979 75 1555 84 8564 83
out: (none)
Round 2
in: 2 4 1 3 1
out: 1555 78
Round 3
in: 3 2 8587 1 100 4 1 3 1
out: 8587 85
N=16 K=4 max
Round 1
in: 16 4 3392 67 40 100 30 5656 68 93 74 91 1788 15 43 19 5 5231 98 7 14 65 8367 71 23 9 90 8717 11 69 63 23 6813 82 64 45 64 7421 75 43 47 55 8772 9 58 25 82 9975 68 50 4 100 5538 47 92 89 67 1186 26 29 80 27 8602 70 25 56 25 1077 57 10 45 73 6012 12 88 21 49 2168 29 84 88 78
out: (none)
Round 2
in: 8 2 8772 3 2 1 5656 2 2 5538 1 60 1 5231 4 2 8367 4 99 2 5656 4 41 1 6813 3 1 8367 4
out: 93 65 45 99
Round 3
in: 4 3 3 2 3392 3 83 1 6012 3 4 3
out: 47 21 5538
Round 4
in: 2 4 4 3 2
out: 9975 51
Round 5
in: 3 2 6813 2 63 3 4 1 2168 3
out: 55 88
