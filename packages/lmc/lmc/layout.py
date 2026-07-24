"""Layout emitter (M1): a single-man, single-row main room with optional I/O rooms.

    +-+  +----------+  +-+
    |I|>>|@<stream> |>>|O|
    +-+  +----------+  +-+

The man spawns at @, walks east executing the instruction stream, and halts on the
trailing H before reaching the right wall.
"""

from __future__ import annotations

from .frontend import Program


def emit_grid(prog: Program) -> str:
    interior = "@" + prog.stream
    main_w = len(interior)
    main_top = "+" + "-" * main_w + "+"
    main_mid = "|" + interior + "|"

    left0 = "+-+  " if prog.uses_input else ""
    left1 = "|I|>>" if prog.uses_input else ""
    left2 = "+-+  " if prog.uses_input else ""

    right0 = "  +-+" if prog.uses_output else ""
    right1 = ">>|O|" if prog.uses_output else ""
    right2 = "  +-+" if prog.uses_output else ""

    row0 = left0 + main_top + right0
    row1 = left1 + main_mid + right1
    row2 = left2 + main_top + right2
    return "\n".join([row0, row1, row2]) + "\n"
