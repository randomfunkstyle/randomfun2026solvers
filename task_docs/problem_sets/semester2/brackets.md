Brackets
Semester 2
Solve in editor →
Scoring: footprint-tick. See scoring and ascii for help.

Read a string of bracket characters and report whether it is balanced.

Each input is one length-prefixed string: a count n, then n bytes, each the decimal ASCII code of one character drawn from ( ) [ ] { }.

A string is balanced if every opener ( [ { is matched by a closer ) ] } of the same type. Bracket pairs may only be nested or concatenated, never interleaved — e.g., [()] and []() are valid but [{]} is not. The empty string is balanced.

Formal definition of balanced strings (in BNF):

s := ε
   | ( s )
   | [ s ]
   | { s }
   | s s
Output one integer:

0 if the string is balanced.
Otherwise, the 1-based position of the first offending character: the first closer that doesn't match the most recently opened, still-unclosed opener (or that appears with nothing open), or n + 1 if the string ends with openers still unclosed.
In ([)] the ) at position 3 is the first offending character — the most recently opened bracket there is [, not (. In ([ nothing offends inside the string, but both openers are left unclosed, so the answer is n + 1 = 3.

Definitions
n
the length (an integer)
byte
the decimal ASCII code of one character (0–255)
brackets
the string to check
Format
Input. A length-prefixed run: a count n, then n bytes, each the decimal ASCII code of one character.

input ⟶ n brackets₁ … bracketsₙ

e.g.
  0   (len 0 (empty))
  1 41   (len 1, then ")")
  2 40 93   (len 2, then "(" "]")
Output. result — one integer.

output ⟶ result

e.g.
  0
  1
  2
Constraints
0 ≤ n ≤ 64 — string length
each character is one of ( ) [ ] { }
nesting depth ≤ 32


Public test cases (9)

balanced simple
in: 6 40 41 91 93 123 125
out: 0

empty string
in: 0
out: 0

wrong-type close
in: 2 40 93
out: 2

close-with-nothing-open
in: 1 41
out: 1

unclosed openers
in: 3 40 91 123
out: 4

deep nesting
in: 10 40 40 40 40 40 41 41 41 41 41
out: 0

crossed brackets
in: 4 40 91 41 93
out: 3

position of first offense
in: 4 40 91 41 41
out: 3

balanced, full length
in: 64 40 41 40 41 40 91 123 40 91 123 40 91 123 40 91 123 40 91 123 40 91 123 40 91 123 40 91 123 40 91 123 40 91 123 125 93 41 125 93 41 125 93 41 125 93 41 125 93 41 125 93 41 125 93 41 125 93 41 125 93 41 125 93 41
out: 0
