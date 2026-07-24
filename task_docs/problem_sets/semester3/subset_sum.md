Subset Sum
Semester 3
Solve in editor →
Scoring: footprint-tick. Tick cap: 15,000,000 per test case. See scoring for help.

Find a set of integers in a list that sum to a target number.

Each test case provides a count n, then n values v_0 .. v_(n-1), then a target t.

You should output a count k and then k values that sum to the target t, ordered by their original index.

If no subset of the list sums to t you should output 0 and nothing else.

If more than one subset sums to t, output the subset whose chosen indices are lexicographically smallest. E.g. the set 0, 4 beats the set 1, 3 and the set 1, 2, 4 beats the sets 1, 3 and 2, 3, 4.

Example. values = [3, 5, 2, 6], target = 8. Two subsets sum to 8: indices {0, 1} (values 3 + 5) and {2, 3} (values 2 + 6). The indices [0, 1] beat [2, 3] at the first position, and the chosen set has 2 elements, so the output is 2 3 5.

Definitions
n
the length (an integer)
v
one value in the list, at the input position (0-indexed) it appears here
t
the target sum
k
the length (an integer)
subset
k int₁ … intₖ — the chosen values, in increasing order of their original index; empty (k = 0) if no subset of the values sums to t
Format
Input. A length-prefixed run: a count n, then n integers, then t — one integer (the target sum).

input ⟶ n v₁ … vₙ t

e.g.
  10 62554 40915 24211 27558 54959 22322 76841 33232 83608 97109 62554   (len 10, then 62554 40915 24211 27558 54959 22322 76841 33232 83608 97109 62554)
  10 120 180 200 150 100 90 80 70 300 60 300   (len 10, then 120 180 200 150 100 90 80 70 300 60 300)
  10 500 500 500 300 300 700 900 500 300 700 1000   (len 10, then 500 500 500 300 300 700 900 500 300 700 1000)
Output. subset — a length-prefixed run: a count k, then k integers.

output ⟶ subset

e.g.
  1 62554   (len 1, then 62554)
  2 120 180   (len 2, then 120 180)
  2 500 500   (len 2, then 500 500)
Constraints
10 ≤ n ≤ 20 — values per test case
1 ≤ v ≤ 99999
100 < t < 1000000; t is roughly 10%–60% of the value sum

tiny warm up
in: 10 35598 41872 81980 98583 65116 96540 10035 60706 14417 64505 248550
out: 5 35598 41872 96540 10035 64505
multiple solutions, lex pin
in: 10 120 180 200 150 100 90 80 70 300 60 300
out: 2 120 180
no solution
in: 14 59 89720 63262 24662 73570 35930 83954 41901 92098 37536 35156 701 33952 7954 240322
out: 0
single-element subset
in: 10 62554 40915 24211 27558 54959 22322 76841 33232 83608 97109 62554
out: 1 62554
last-index-required
in: 12 1864 1519 695 1825 290 253 1919 302 1542 1283 1486 16687 16687
out: 1 16687
duplicate values
in: 10 500 500 500 300 300 700 900 500 300 700 1000
out: 2 500 500
near-total-sum, 20 values
in: 20 58443 79693 37155 15450 57084 20590 29841 13454 91581 60485 36863 169 33749 20147 72090 52216 92490 97963 96043 90230 633441
out: 11 58443 79693 15450 57084 20590 13454 91581 36863 72090 97963 90230
