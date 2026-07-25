# Little Man primitives

This directory contains compact, `Y`-free bit-stream building blocks.  Each
`.man` file includes its own `I`/`O` harness; when composing hardware, reuse
the active room and connect its pipes to the neighbouring primitive instead of
duplicating the harness.

Unless noted otherwise, gate inputs are strict bits delivered serially and all
outputs are strict bits in the corresponding serial order.

## Data-path primitives

| Primitive | Artifact | Serial contract | Status |
|---|---|---|---|
| Controlled forwarding transistor | `transistor.man` | `(data, enable) -> data if enable else 0` | Original baseline. |
| Compact transistor | `transistor-compact.man` | Same contract. | Smaller, faster candidate. |
| Narrow compact transistor | `transistor-compact-narrow.man` | Same contract. | Narrower candidate; use as a geometry reference. |
| AND | `and-gate.man` | `(a, b) -> a & b` | Promoted terminal-bend `U` layout; 33 ticks for four input pairs. |
| AND baseline | `and-gate-ten-tick.man` | `(a, b) -> a & b` | Retained comparison candidate; 40 ticks for four pairs. |
| OR | `or-gate.man` | `(a, b) -> a | b` | Promoted streaming gate. |
| XOR | `xor-gate.man` | `(a, b) -> a ^ b` | Promoted streaming gate. |
| NOT | `not-gate.man` | `a -> 1 - a` | Promoted streaming inverter; 34 ticks for four bits. |
| NAND | `nand-gate.man` | `(a, b) -> 1 - (a & b)` | Promoted 9×9 layout; 56 ticks for four pairs. |
| Narrow NAND | `nand-gate-narrow.man` | Same contract. | 8×9 alternative retained for layout comparison. |

## State and selection

| Primitive | Artifact | Serial contract | Status |
|---|---|---|---|
| One-bit register | `bit-register.man` | `in[t] -> out[t - 1]`, with `out[0] = 0` | Promoted delay/state cell. |
| MUX | `mux-gate.man` | `(select, i1, i2) -> i1` when `select = 0`, otherwise `i2` | Canonical packed-I/O `U`/`a` layout: 10×10, 106 ticks and 17 walking ticks for the eight-row truth table. |
| Compact MUX backup | `mux-gate-select-first-user-u-compact.man` | Same select-first contract. | 9×9 alternative; 126 ticks and 31 walking ticks for the same table. |

The MUX input order is deliberately **select first**: `(select, i1, i2)`. It
lets the room keep the selector in its backpack while reading the two data
values into registers.

## Wiring primitive: FANOUT

No dedicated FANOUT room is needed. In a room with multiple outgoing pipes,
`S` atomically sends the current value in `A` to **every** outgoing pipe. Use
`S` as the signal splitter; the downstream rooms are the fanout branches.

`Y` remains excluded from every primitive in this directory.
