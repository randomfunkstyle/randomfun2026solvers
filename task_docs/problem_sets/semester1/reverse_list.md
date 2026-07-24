Reverse a List
Semester 1
Solve in editor →
Scoring: footprint-tick. See scoring and rounds for help.

Read a list of integers and print the same list in reverse order.

Each input is a list: a count n, then n integers. Print the n values in reverse order.

A test case contains one or more rounds, each of which carries a single list. Lists may vary in length between rounds. You won't receive the next list until you finish printing the current one.

Definitions
n
the length (an integer)
x
an integer in the list
Format
Input. A length-prefixed run: a count n, then n integers.

input ⟶ n x₁ … xₙ

e.g.
  round 1:  1 -1000000   (len 1, then -1000000)
  round 2:  1 0   (len 1, then 0)
  round 3:  1 7   (len 1, then 7)
Output. A run of integers, until it ends.

output ⟶ x*

e.g.
  round 1:  -1000000
  round 2:  0
  round 3:  7
Constraints
1 ≤ n ≤ 16 — per list
-1000000 ≤ x ≤ 1000000
1–3 lists per test case

test cases

warm up
Round 1
in: 1 42
out: 42
Round 2
in: 2 100 -100
out: -100 100
Round 3
in: 3 10 20 30
out: 30 20 10


a single list
in: 7 8 6 7 5 3 0 9
out: 9 0 3 5 7 6 8
mixed sizes
Round 1
in: 5 1 2 3 4 5
out: 5 4 3 2 1
Round 2
in: 4 -7 0 7 -14
out: -14 7 0 -7


repeats and palindromes
Round 1
in: 5 4 4 4 4 4
out: 4 4 4 4 4
Round 2
in: 5 1 2 3 2 1
out: 1 2 3 2 1
Round 3
in: 5 9 1 1 9 5
out: 5 9 1 1 9


extreme values
Round 1
in: 2 1000000 -1000000
out: -1000000 1000000
Round 2
in: 5 -1000000 0 999999 -999999 1000000
out: 1000000 -999999 999999 0 -1000000
singletons
Round 1
in: 1 -1000000
out: -1000000
Round 2
in: 1 0
out: 0
Round 3
in: 1 7
out: 7


climbing lengths
Round 1
in: 2 5 10
out: 10 5
Round 2
in: 7 2 4 6 8 10 12 14
out: 14 12 10 8 6 4 2
Round 3
in: 12 1 -1 2 -2 3 -3 4 -4 5 -5 6 -6
out: -6 6 -5 5 -4 4 -3 3 -2 2 -1 1


full size
Round 1
in: 16 675866 -469990 -316526 -586202 -495649 -594977 725024 970614 -758697 -386951 -1260 998677 169990 282058 -501276 -694925
out: -694925 -501276 282058 169990 998677 -1260 -386951 -758697 970614 725024 -594977 -495649 -586202 -316526 -469990 675866
Round 2
in: 9 165787 322570 386470 49875 -994912 784925 -412923 -940544 752013
out: 752013 -940544 -412923 784925 -994912 49875 386470 322570 165787
Round 3
in: 16 42599 -637865 661864 705729 -598961 63916 752576 -185145 -616486 -304818 -325803 -753987 -817166 155504 633092 -453999
out: -453999 633092 155504 -817166 -753987 -325803 -304818 -616486 -185145 752576 63916 -598961 705729 661864 -637865 42599
