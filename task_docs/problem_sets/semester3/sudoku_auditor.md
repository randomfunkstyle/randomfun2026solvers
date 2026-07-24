Sudoku Auditor
Semester 3
Solve in editor →
Scoring: footprint-tick. See scoring and rounds for help.

Validate a Sudoku solution.

A Sudoku grid is a 9x9 square of numbers. In a correctly solved grid, each row, each column, and each of the nine 3x3 boxes (rows 0-2/3-5/6-8 crossed with columns 0-2/3-5/6-8) in the grid contains exactly the digits 1-9 without repetition.

Each round delivers three integers r c v describing the contents of one cell. r and c are the 0-indexed row and column of the cell, and v is the value (between 1 and 9) placed at that cell.

After reading each cell, output 1 if the grid is still valid (that is, if no row, column, or box in the grid contains a duplicate number) and 0 if the grid is no longer valid.

No cell is delivered more than once. Your program only needs to output 0 once: the test case ends as soon as an invalid value is delivered.

Definitions
r
row, 0-indexed, 0-8
c
column, 0-indexed, 0-8
v
value placed at (r, c), 1-9
verdict
1 if consistent so far, else 0
Format
Input. r — one integer (row, 0-indexed, 0-8), then c — one integer (column, 0-indexed, 0-8), then v — one integer.

input ⟶ r c v

e.g.
  round 1:  4 5 4
  round 2:  2 5 5
  round 3:  0 2 8
Output. verdict — one integer.

output ⟶ verdict

e.g.
  round 1:  1
  round 2:  1
  round 3:  1
Constraints
0 ≤ r, c ≤ 8
1 ≤ v ≤ 9
up to 81 rounds per test case — no cell is delivered more than once
Public test cases (6)
a valid grid
Round 1
in: 4 5 4
out: 1
Round 2
in: 2 5 5
out: 1
Round 3
in: 0 2 8
out: 1
Round 4
in: 8 1 4
out: 1
Round 5
in: 8 6 3
out: 1
Round 6
in: 6 6 1
out: 1
Round 7
in: 7 3 6
out: 1
Round 8
in: 2 1 1
out: 1
Round 9
in: 0 0 9
out: 1
Round 10
in: 1 1 7
out: 1
Round 11
in: 5 5 8
out: 1
Round 12
in: 8 7 7
out: 1
Round 13
in: 1 4 1
out: 1
Round 14
in: 3 7 1
out: 1
Round 15
in: 7 4 4
out: 1
Round 16
in: 2 6 7
out: 1
Round 17
in: 8 3 5
out: 1
Round 18
in: 1 3 4
out: 1
Round 19
in: 7 1 3
out: 1
Round 20
in: 5 4 5
out: 1
Round 21
in: 1 8 9
out: 1
Round 22
in: 6 8 4
out: 1
Round 23
in: 2 3 8
out: 1
Round 24
in: 8 0 6
out: 1
Round 25
in: 2 4 9
out: 1
Round 26
in: 2 2 6
out: 1
Round 27
in: 6 2 5
out: 1
Round 28
in: 6 0 8
out: 1
Round 29
in: 3 1 8
out: 1
Round 30
in: 1 6 5
out: 1
Round 31
in: 0 4 7
out: 1
Round 32
in: 4 6 8
out: 1
Round 33
in: 5 8 7
out: 1
Round 34
in: 0 6 6
out: 1
Round 35
in: 4 4 6
out: 1
Round 36
in: 8 4 8
out: 1
Round 37
in: 0 8 1
out: 1
Round 38
in: 1 5 6
out: 1
Round 39
in: 8 2 1
out: 1
Round 40
in: 0 1 5
out: 1
Round 41
in: 7 0 2
out: 1
Round 42
in: 5 7 3
out: 1
Round 43
in: 3 3 7
out: 1
Round 44
in: 7 6 9
out: 1
Round 45
in: 2 8 3
out: 1
Round 46
in: 1 7 8
out: 1
Round 47
in: 3 6 4
out: 1
Round 48
in: 6 5 7
out: 1
Round 49
in: 6 1 9
out: 1
Round 50
in: 4 2 3
out: 1
Round 51
in: 8 5 9
out: 1
Round 52
in: 5 6 2
out: 1
Round 53
in: 8 8 2
out: 1
Round 54
in: 6 7 6
out: 1
Round 55
in: 0 7 4
out: 1
Round 56
in: 3 4 2
out: 1
Round 57
in: 3 8 6
out: 1
Round 58
in: 7 7 5
out: 1
Round 59
in: 6 3 2
out: 1
Round 60
in: 2 7 2
out: 1
Round 61
in: 0 5 2
out: 1
Round 62
in: 3 0 5
out: 1
Round 63
in: 3 2 9
out: 1
Round 64
in: 5 1 6
out: 1
Round 65
in: 5 2 4
out: 1
Round 66
in: 0 3 3
out: 1
Round 67
in: 4 3 1
out: 1
Round 68
in: 2 0 4
out: 1
Round 69
in: 6 4 3
out: 1
Round 70
in: 4 0 7
out: 1
Round 71
in: 4 1 2
out: 1
Round 72
in: 7 5 1
out: 1
Round 73
in: 7 8 8
out: 1
Round 74
in: 1 2 2
out: 1
Round 75
in: 4 7 9
out: 1
Round 76
in: 1 0 3
out: 1
Round 77
in: 7 2 7
out: 1
Round 78
in: 5 3 9
out: 1
Round 79
in: 5 0 1
out: 1
Round 80
in: 4 8 5
out: 1
Round 81
in: 3 5 3
out: 1
early row violation
Round 1
in: 2 3 1
out: 1
Round 2
in: 2 8 9
out: 1
Round 3
in: 2 2 1
out: 0
late box violation
Round 1
in: 0 7 4
out: 1
Round 2
in: 3 4 4
out: 1
Round 3
in: 3 5 6
out: 1
Round 4
in: 1 6 7
out: 1
Round 5
in: 1 7 1
out: 1
Round 6
in: 0 8 9
out: 1
Round 7
in: 2 8 3
out: 1
Round 8
in: 8 5 8
out: 1
Round 9
in: 6 4 6
out: 1
Round 10
in: 3 8 5
out: 1
Round 11
in: 8 1 1
out: 1
Round 12
in: 3 1 8
out: 1
Round 13
in: 6 3 1
out: 1
Round 14
in: 5 2 4
out: 1
Round 15
in: 7 3 4
out: 1
Round 16
in: 5 3 5
out: 1
Round 17
in: 0 6 8
out: 1
Round 18
in: 1 1 3
out: 1
Round 19
in: 6 7 8
out: 1
Round 20
in: 6 6 3
out: 1
Round 21
in: 3 7 3
out: 1
Round 22
in: 3 2 2
out: 1
Round 23
in: 7 8 1
out: 1
Round 24
in: 4 3 8
out: 1
Round 25
in: 7 1 2
out: 1
Round 26
in: 6 8 2
out: 1
Round 27
in: 0 3 3
out: 1
Round 28
in: 5 0 6
out: 1
Round 29
in: 8 4 3
out: 1
Round 30
in: 0 4 5
out: 1
Round 31
in: 2 1 9
out: 1
Round 32
in: 7 0 8
out: 1
Round 33
in: 1 3 9
out: 1
Round 34
in: 0 1 6
out: 1
Round 35
in: 4 0 3
out: 1
Round 36
in: 6 2 9
out: 1
Round 37
in: 6 5 5
out: 1
Round 38
in: 5 4 1
out: 1
Round 39
in: 8 6 9
out: 1
Round 40
in: 3 6 1
out: 1
Round 41
in: 4 5 9
out: 1
Round 42
in: 0 0 1
out: 1
Round 43
in: 4 8 7
out: 1
Round 44
in: 6 1 4
out: 1
Round 45
in: 7 2 3
out: 1
Round 46
in: 1 0 2
out: 1
Round 47
in: 2 7 2
out: 1
Round 48
in: 8 2 6
out: 1
Round 49
in: 4 1 5
out: 1
Round 50
in: 5 8 8
out: 1
Round 51
in: 7 4 9
out: 1
Round 52
in: 7 5 7
out: 1
Round 53
in: 1 4 8
out: 1
Round 54
in: 8 3 2
out: 1
Round 55
in: 5 1 7
out: 1
Round 56
in: 4 4 2
out: 1
Round 57
in: 5 6 2
out: 1
Round 58
in: 8 8 4
out: 1
Round 59
in: 2 3 6
out: 1
Round 60
in: 1 8 6
out: 1
Round 61
in: 4 2 1
out: 1
Round 62
in: 2 5 1
out: 1
Round 63
in: 3 0 9
out: 1
Round 64
in: 5 5 3
out: 1
Round 65
in: 2 2 8
out: 1
Round 66
in: 1 2 5
out: 1
Round 67
in: 1 5 4
out: 1
Round 68
in: 7 6 6
out: 1
Round 69
in: 2 4 7
out: 1
Round 70
in: 7 7 5
out: 1
Round 71
in: 8 7 7
out: 1
Round 72
in: 5 7 9
out: 1
Round 73
in: 6 0 7
out: 1
Round 74
in: 0 5 2
out: 1
Round 75
in: 4 7 6
out: 1
Round 76
in: 2 6 4
out: 0
checksum-tie trap
Round 1
in: 3 0 1
out: 1
Round 2
in: 3 3 6
out: 1
Round 3
in: 3 6 8
out: 1
Round 4
in: 3 7 1
out: 0
col violation
Round 1
in: 3 1 3
out: 1
Round 2
in: 8 8 1
out: 1
Round 3
in: 7 6 7
out: 1
Round 4
in: 3 6 1
out: 1
Round 5
in: 2 5 5
out: 1
Round 6
in: 1 5 9
out: 1
Round 7
in: 1 6 5
out: 1
Round 8
in: 3 0 2
out: 1
Round 9
in: 7 7 3
out: 1
Round 10
in: 6 2 3
out: 1
Round 11
in: 7 4 2
out: 1
Round 12
in: 3 3 7
out: 1
Round 13
in: 8 4 8
out: 1
Round 14
in: 4 7 7
out: 1
Round 15
in: 5 3 6
out: 1
Round 16
in: 7 1 1
out: 1
Round 17
in: 0 2 1
out: 1
Round 18
in: 0 6 9
out: 1
Round 19
in: 7 5 4
out: 1
Round 20
in: 4 3 4
out: 1
Round 21
in: 4 4 3
out: 1
Round 22
in: 6 4 1
out: 1
Round 23
in: 6 6 4
out: 1
Round 24
in: 8 5 7
out: 1
Round 25
in: 8 7 9
out: 1
Round 26
in: 6 1 8
out: 1
Round 27
in: 3 4 9
out: 1
Round 28
in: 0 0 5
out: 1
Round 29
in: 1 8 4
out: 1
Round 30
in: 5 0 8
out: 1
Round 31
in: 8 6 6
out: 1
Round 32
in: 6 7 5
out: 1
Round 33
in: 4 0 1
out: 1
Round 34
in: 2 4 4
out: 1
Round 35
in: 6 0 7
out: 1
Round 36
in: 5 5 1
out: 1
Round 37
in: 6 5 6
out: 1
Round 38
in: 0 5 3
out: 1
Round 39
in: 5 7 4
out: 1
Round 40
in: 1 1 3
out: 0
violation on final cell
Round 1
in: 4 7 5
out: 1
Round 2
in: 8 0 6
out: 1
Round 3
in: 8 1 1
out: 1
Round 4
in: 5 8 8
out: 1
Round 5
in: 3 0 8
out: 1
Round 6
in: 2 4 1
out: 1
Round 7
in: 6 2 9
out: 1
Round 8
in: 8 8 5
out: 1
Round 9
in: 2 0 9
out: 1
Round 10
in: 4 5 2
out: 1
Round 11
in: 7 2 4
out: 1
Round 12
in: 6 8 2
out: 1
Round 13
in: 4 6 9
out: 1
Round 14
in: 4 1 7
out: 1
Round 15
in: 4 0 1
out: 1
Round 16
in: 7 8 1
out: 1
Round 17
in: 0 8 9
out: 1
Round 18
in: 1 1 2
out: 1
Round 19
in: 5 4 6
out: 1
Round 20
in: 3 7 1
out: 1
Round 21
in: 6 3 1
out: 1
Round 22
in: 0 4 2
out: 1
Round 23
in: 1 5 9
out: 1
Round 24
in: 4 3 8
out: 1
Round 25
in: 0 1 6
out: 1
Round 26
in: 2 5 7
out: 1
Round 27
in: 3 8 7
out: 1
Round 28
in: 0 6 5
out: 1
Round 29
in: 8 6 3
out: 1
Round 30
in: 0 0 7
out: 1
Round 31
in: 1 7 7
out: 1
Round 32
in: 6 6 8
out: 1
Round 33
in: 2 6 2
out: 1
Round 34
in: 5 7 2
out: 1
Round 35
in: 6 5 6
out: 1
Round 36
in: 1 3 5
out: 1
Round 37
in: 3 1 4
out: 1
Round 38
in: 7 6 7
out: 1
Round 39
in: 5 2 5
out: 1
Round 40
in: 3 4 5
out: 1
Round 41
in: 7 3 3
out: 1
Round 42
in: 2 8 4
out: 1
Round 43
in: 8 5 4
out: 1
Round 44
in: 7 4 9
out: 1
Round 45
in: 8 7 9
out: 1
Round 46
in: 2 7 8
out: 1
Round 47
in: 5 6 4
out: 1
Round 48
in: 4 8 3
out: 1
Round 49
in: 3 2 2
out: 1
Round 50
in: 6 1 3
out: 1
Round 51
in: 2 2 3
out: 1
Round 52
in: 1 6 1
out: 1
Round 53
in: 3 3 9
out: 1
Round 54
in: 8 3 2
out: 1
Round 55
in: 7 0 2
out: 1
Round 56
in: 5 0 3
out: 1
Round 57
in: 7 5 5
out: 1
Round 58
in: 6 0 5
out: 1
Round 59
in: 5 1 9
out: 1
Round 60
in: 0 7 3
out: 1
Round 61
in: 3 6 6
out: 1
Round 62
in: 4 2 6
out: 1
Round 63
in: 5 3 7
out: 1
Round 64
in: 1 0 4
out: 1
Round 65
in: 0 5 8
out: 1
Round 66
in: 8 4 8
out: 1
Round 67
in: 3 5 3
out: 1
Round 68
in: 2 3 6
out: 1
Round 69
in: 1 2 8
out: 1
Round 70
in: 5 5 1
out: 1
Round 71
in: 1 4 3
out: 1
Round 72
in: 0 3 4
out: 1
Round 73
in: 1 8 6
out: 1
Round 74
in: 4 4 4
out: 1
Round 75
in: 2 1 5
out: 1
Round 76
in: 0 2 1
out: 1
Round 77
in: 7 7 6
out: 1
Round 78
in: 6 7 4
out: 1
Round 79
in: 6 4 7
out: 1
Round 80
in: 8 2 7
out: 1
Round 81
in: 7 1 1
out: 0
