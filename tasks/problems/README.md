# Contest problems (ICFP 2026, Little Man)

Each `<slug>.json` contains the task description, `io` grammar and constraints,
scoring mode, tick cap, and `publicTestData` I/O examples. `_index.json` is the
problem catalogue. Private test data is not included.

Rules and scoring: [`../../littleman/GRADING.md`](../../littleman/GRADING.md).
Language: [`../../littleman/SPEC.md`](../../littleman/SPEC.md).

| Set | Slug | Name | Scoring | Public cases | Private | Display | Summary |
|---|---|---|---|---|---|---|---|
| Semester 1 | `memory` | Memory | footprint-tick | 7 | 0 |  | Simulate a 100-cell memory. |
| Semester 1 | `reverse-a-list` | Reverse a List | footprint-tick | 8 | 0 |  | Read a list of integers and print the same list in reverse order. |
| Semester 1 | `sort-numbers` | Sort | footprint-tick | 7 | 0 |  | Read a list of integers and print the same list sorted into ascending order. |
| Semester 1 | `triangle` | Triangle | footprint-tick | 6 | 0 |  | Output the *n*-th triangular number. |
| Semester 2 | `brackets` | Brackets | footprint-tick | 9 | 0 |  | Read a string of bracket characters and report whether it is balanced. |
| Semester 2 | `history-lesson` | History Lesson | footprint | 1 | 0 |  | This problem has no input. Your program must output an |
| Semester 2 | `plotter` | Plotter | footprint-tick | 6 | 0 | 32x24 | Graph line segments on a display. |
| Semester 2 | `tcp` | Packet Reassembly | footprint-tick | 6 | 0 |  | Reassemble a stream of packets. |
| Semester 3 | `gradebook` | Grade Book | footprint-tick | 7 | 0 |  | Process operations over student grades across several subjects. |
| Semester 3 | `matmul` | Matrix Multiply | footprint-tick | 7 | 0 |  | Multiply two matrices. |
| Semester 3 | `subset-sum` | Subset Sum | footprint-tick | 7 | 0 |  | Find a set of integers in a list that sum to a target number. |
| Semester 3 | `sudoku-validity` | Sudoku Auditor | footprint-tick | 6 | 0 |  | Validate a Sudoku solution. |
| Practice Problems (Ungraded) | `atoi` | atoi | footprint-tick | 2 | 0 |  | Read a string of ASCII digits and output the integer it denotes. |
| Practice Problems (Ungraded) | `hello-world` | Hello World | footprint-tick | 1 | 0 |  | Output the eleven bytes of `hello world` (lowercase, a single space, no |
| Practice Problems (Ungraded) | `max-element` | Max Element | footprint-tick | 10 | 0 |  | Output the largest number in a list. |
| Practice Problems (Ungraded) | `palette` | Palette | footprint-tick | 1 | 0 | 8x8 | Show all sixteen palette colors on the display. |

All problems currently have `tickCap: null`, i.e. the default 5,000,000-step cap.
`status: practice` problems are ungraded.
