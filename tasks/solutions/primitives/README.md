# Primitive notes

## FANOUT

No dedicated FANOUT artifact is needed: in a room with multiple outgoing
pipes, `S` atomically sends the current value in `A` to every one of them.
Use it as the signal splitter when composing rooms; the downstream rooms are
the fanout branches.

`Y` remains excluded from these primitives.
