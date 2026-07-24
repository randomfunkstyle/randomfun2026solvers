Sort
Semester 1
Solve in editor →
Scoring: footprint-tick. See scoring and rounds for help.

Read a list of integers and print the same list sorted into ascending order.

Each input is a list: a count n, then n integers. Print the n values in ascending order. Duplicates are kept.

A test case contains multiple rounds, each of which contains a single list. Lists may vary in length between rounds. You won't receive the next list until you finish printing the current one.

Definitions
n
the length (an integer)
x
a number in the list
Format
Input. A length-prefixed run: a count n, then n integers.

input ⟶ n x₁ … xₙ

e.g.
  round 1:  1 42   (len 1, then 42)
  round 2:  5 -5 -2 0 3 8   (len 5, then -5 -2 0 3 8)
  round 3:  4 -9 -4 -4 1   (len 4, then -9 -4 -4 1)
Output. A run of integers, until it ends.

output ⟶ x*

e.g.
  round 1:  42
  round 2:  -5 -2 0 3 8
  round 3:  -9 -4 -4 1
Constraints
1 ≤ n ≤ 16 — numbers per list
-10000 ≤ x ≤ 10000
2–6 lists per test case

testcases

warm up
Round 1
in: 3 3 1 2
out: 1 2 3
Round 2
in: 4 5 -1 4 0
out: -1 0 4 5
Round 3
in: 5 7 2 9 -3 1
out: -3 1 2 7 9


already sorted
Round 1
in: 1 42
out: 42
Round 2
in: 5 -5 -2 0 3 8
out: -5 -2 0 3 8
Round 3
in: 4 -9 -4 -4 1
out: -9 -4 -4 1


all equal
Round 1
in: 4 7 7 7 7
out: 7 7 7 7
Round 2
in: 7 -3 -3 -3 -3 -3 -3 -3
out: -3 -3 -3 -3 -3 -3 -3


reverse sorted
Round 1
in: 6 9 4 2 0 -1 -6
out: -6 -1 0 2 4 9
Round 2
in: 2 100 50
out: 50 100


negatives and duplicates
Round 1
in: 5 -10000 -1 -1 -9999 0
out: -10000 -9999 -1 -1 0
Round 2
in: 8 -3 -3 2 2 -3 2 -1 5
out: -3 -3 -3 -1 2 2 2 5


min to max size
Round 1
in: 1 0
out: 0
Round 2
in: 16 10000 -10000 5 5 -5 -5 0 3 -3 10000 -10000 1 -1 2 -2 0
out: -10000 -10000 -5 -5 -3 -2 -1 0 0 1 2 3 5 5 10000 10000
Round 3
in: 2 4 -4
out: -4 4


long case
Round 1
in: 16 -1719 679 -3535 -2120 -3672 -6230 -422 5605 8281 -2208 -5234 8215 6404 -9921 -827 -9071
out: -9921 -9071 -6230 -5234 -3672 -3535 -2208 -2120 -1719 -827 -422 679 5605 6404 8215 8281
Round 2
in: 3 6290 -4342 -3734
out: -4342 -3734 6290
Round 3
in: 12 6623 2732 -4008 862 534 -6157 -7144 8054 -1469 7497 5389 -3284
out: -7144 -6157 -4008 -3284 -1469 534 862 2732 5389 6623 7497 8054
Round 4
in: 16 -2608 -3719 -7645 -7348 -3443 8353 4340 9507 7653 -7500 -6729 -3052 776 -7125 -6890 8903
out: -7645 -7500 -7348 -7125 -6890 -6729 -3719 -3443 -3052 -2608 776 4340 7653 8353 8903 9507


