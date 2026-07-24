; hello-world — emit the eleven bytes of "hello world", then stop.
;
; Pure ROM: no input, no memory, no branches. `.ascii` is an assembler macro that
; expands to one `LDI c` / `OUT` pair per byte, so this is the cheapest possible
; shape for LM-1 and uses ISA v1 only.

.ascii "hello world"

HALT
