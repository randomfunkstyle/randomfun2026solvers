# General Primitive Composer Implementation Plan

**Goal:** Compile ordered scalar-bit circuit netlists into cropped, multi-room Little Man artifacts by composing the checked-in primitives.

## Decisions already validated

- Keep every primitive as its standalone `.man` artifact with its `I`/`O`
  harness; extract its active room while composing.
- Contracts describe semantic rules, not the coordinates in standalone
  harnesses: ordered serial frames, port multiplicity, required/allowed sides,
  side-sensitive control flow, fanout safety, corner restrictions, and minimum
  clearance.
- AND/XOR input must arrive from the north because `U` turns away from its
  input pipe. Their output may fan out by safely changing terminal `s` to `S`.
- The initial acceptance circuit is
  `product = AND(a,b); result = XOR(product,b)`, whose output frame for
  `00, 01, 10, 11` is `0, 1, 0, 0`.
- A two-field packer/join is validated by the reference WASM:

  ```text
  +-----+
  |@>rsv|
  |.^sr<|
  +-----+
  ```

  It receives field 0, sends it, receives field 1, sends it, then loops.
  A fanout room is likewise validated:

  ```text
  +-----+
  |@>rSv|
  | ^  <|
  +-----+
  ```

## Implementation sequence

1. Add semantic primitive contracts and tests. Start with AND/XOR, then cover
   OR, NOT, NAND, bit register, transistor variants, and MUX. Do not record
   harness offsets or fixed stubs.
2. Add a netlist model that validates ordered scalar DAGs, tracks each signal's
   producer and consumers, and derives dependency levels.
3. Implement active-room extraction structurally: select the closed room
   containing `@`, rather than relying on file position.
4. Implement generated adapters: input frame demux, scalar fanout, two-field
   packer, and ordered output join. Place their ports so every `r`/`s` has a
   unique nearest intended pipe.
5. Place rooms by dependency level in padded footprints. Select non-corner
   wall attachment cells according to contract side rules, reserve two-cell
   stubs, keepouts, and routing aisles.
6. Route between exterior stub anchors with occupancy-aware search. Route the
   most constrained edges first and retry deterministically on conflicts.
   Validate nearest-pipe bindings with `Littleman.route`.
7. Crop the resulting source to its non-whitespace bounding box and write the
   resulting `.man` only when requested.
8. Pass the dependent-chain regression, restore the parallel half-adder as a
   generated regression, and run `uv run pytest`.

## Scope limits

The first backend supports planar scalar DAGs. A non-planar graph must produce
a clear unroutable error until a serial crossover adapter or a time-multiplexed
bus is implemented.
