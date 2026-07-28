#!/usr/bin/env python3
"""The **taped** STORE tier: banked rotating-pipe tapes behind a gate chain.

The man-memory tiers pay one-to-two little men *per slot* — ``deadman-3d``'s
330-slot men-v3 store is ~680 live men, and the visualizer chokes on them. A
pipe tape stores a word as a *value in a rotating ring* and staffs the whole
bank with two men (worker + relay) at any size; its price is the ring tax,
``~8.0 ticks per slot per access`` (ARCH.md §4.1). One 330-slot ring would be
~2.8k ticks a read — dead on arrival for a demo making ~20k reads a frame — so
the store is split into ``N`` banks of ``M`` slots and a read pays ``~8.0 * M``
plus a few gate hops instead.

The parts, west to east and top to bottom::

        out ^                                    (the block's top, like men-v3)
    [ collector teleport ......................] R takes from ANY incoming pipe
        ^         ^         ^         ^          each bank answers upward
      [B0]      [B1]      [B2]      [B3]         tape_block banks, 2 men each
    [G0] -> [G1] -> [G2] ----------^             gate chain, 1 man each
     in

**The gate** is the load-bearing room. It speaks the tape's own wire protocol
(``0 addr`` read / ``1 addr value`` write — what :data:`lm1.machine._ADAPTER`
emits) on *both* sides, so the chain composes: each gate owns the next ``M``
addresses and **rebases** what it forwards, so every bank decodes plain local
addresses ``1..M`` and the last bank needs no gate at all — the last gate's
downstream arm is already speaking its wire, rebased.

One man, four arms. ``U`` takes the op and ``b`` parks it in the backpack (the
op is 0/1, exactly a BP flag), which frees both hands for the range test —
``r M `M+1` W - X`` leaves ``A = addr-(M+1)`` and ``X`` splits negative (mine)
from zero/positive (downstream, the zero being the first downstream address).
Each side then splits once more on the backpack — ``d``/``a`` turn on BP
without consuming it — into read/write arms::

    local  read   + M 0 s W s          restore addr; send "0 addr"
    local  write  + M 1 s W s r s      ... and pass the value through
    down   read   M 1 + M 0 s W s      A = addr-M; send "0 addr-M"
    down   write  M 1 + M 1 s W s r s  ... and pass the value through

Binding needs no argument anywhere: the room has ONE incoming pipe (every
``U``/``r`` takes from it) and two outgoing pipes on the **same wall**, so §7.1's
column term cancels and the row decides — the local pipe leaves beside the
rows-1/3 arms, the downstream pipe beside the rows-9/11 arms.

The answer side has no logic at all. At most one request is in flight (the CPU
blocks on every read), so the banks' answers all rise into one **collector
teleport** — ``R`` has no distance term — and leave the block's top exactly
where the men-v3 block's outlet is. The block is a drop-in :class:`V3Store`,
so ``lm1.machine`` places it with the men-v3 branch, teleports and all.

``deadman-3d_taped.input.txt`` is deliberately not generated: the taped machine
runs the same program, protocol and preamble as the canonical men-v3 one, so
its input is byte-identical to ``deadman-3d.input.txt``.
"""

from __future__ import annotations

from .memory_men_v3 import V3Store

__all__ = ["bank_gate", "taped_store_block", "taped_plan"]

#: The gate's interior height; rows 1..12 like the two-tier adapter it descends
#: from, with the same return loop (east column down, floor west, climb to ``U``).
GATE_H = 12
#: West-wall row the request pipe enters (the ``U`` of the spine).
GATE_IN_ROW = 6
#: East-wall rows the two outgoing pipes attach to: local bank, downstream.
GATE_LOCAL_ROW = 2
GATE_DOWN_ROW = 10


def bank_gate(m: int) -> tuple[dict[tuple[int, int], str], int]:
    """One range gate for a bank of ``m`` slots: cells (walls included), width.

    Local coordinates: walls at column 0 / row 0, interior from (1, 1). The
    caller attaches the request pipe to the west wall at :data:`GATE_IN_ROW`
    and the two outgoing pipes to the east wall at :data:`GATE_LOCAL_ROW` /
    :data:`GATE_DOWN_ROW`.
    """
    if m < 1:
        raise ValueError(f"a bank must hold at least one slot, not {m}")
    lit = f"`{m + 1}`"
    cx = 7 + len(lit)  # the range test's X
    cr = cx + 13  # the return column, east of the longest arm plus slack
    g: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, ch: str) -> None:
        old = g.get((x, y))
        if old is not None and old != ch:
            raise ValueError(f"gate collision at {(x, y)}: {old!r} vs {ch!r}")
        g[(x, y)] = ch

    def text(x: int, y: int, s: str) -> None:
        for i, ch in enumerate(s):
            put(x + i, y, ch)

    # the spine: op -> backpack, then A = addr - (m+1), then the three-way X
    text(1, GATE_IN_ROW, f"UbrM{lit}W-X")

    # A < 0 (mine): north, splitting on the parked op at `d`
    put(cx, 5, ".")
    put(cx, 4, ".")
    put(cx, 3, "d")  # BP > 0 (write): turn right of northbound = east
    text(cx + 1, 3, "+M1sWsrs")
    put(cx, 2, ".")
    put(cx, 1, ">")  # BP == 0 (read): straight through to the top row
    text(cx + 1, 1, "+M0sWs")

    # A == 0 goes straight and A > 0 turns south; they merge one column east
    # (the zero IS the first downstream address, so the merge is correct).
    put(cx + 1, GATE_IN_ROW, "v")
    put(cx, 7, ">")
    put(cx + 1, 7, "v")
    put(cx + 1, 8, ".")
    put(cx + 1, 9, "a")  # BP > 0 (write): turn left of southbound = east
    text(cx + 2, 9, "M1+M1sWsrs")
    put(cx + 1, 10, ".")
    put(cx + 1, 11, ">")
    text(cx + 2, 11, "M1+M0sWs")

    # the return leg: every arm walks east onto the same descent, then the
    # floor runs west and the climb re-enters the spine's `U` from below
    for y in range(1, GATE_H):
        put(cr, y, "v")
    put(cr, GATE_H, "<")
    put(cr - 1, GATE_H, "@")
    for x in range(2, cr - 1):
        put(x, GATE_H, "<")
    put(1, GATE_H, "^")
    for y in range(GATE_IN_ROW + 1, GATE_H):
        put(1, y, "^")

    # walls
    for x in range(0, cr + 2):
        put(x, 0, "+" if x in (0, cr + 1) else "-")
        put(x, GATE_H + 1, "+" if x in (0, cr + 1) else "-")
    for y in range(1, GATE_H + 1):
        put(0, y, "|")
        put(cr + 1, y, "|")
    return g, cr + 2


def taped_plan(n: int, banks: int | tuple[int, ...]) -> list[int]:
    """Slot counts per bank covering addresses ``1..n-1`` (slot 0 is unused —
    the CPU wire is sign-biased, and ``tape_block``'s own rule is highest
    address + 1).

    An ``int`` gives uniform banks (the last takes the remainder). A tuple
    states the sizes outright — the ring tax is ``~8 * local`` per access, so
    a hot address range deserves a small bank of its own; the sizes must sum
    to at least ``n - 1``.
    """
    top = n - 1  # highest address
    if isinstance(banks, tuple):
        if len(banks) < 2:
            raise ValueError("one bank is just tape_block; the taped tier wants >= 2")
        if any(m < 1 for m in banks) or sum(banks) < top:
            raise ValueError(f"bank plan {banks} does not cover addresses 1..{top}")
        return list(banks)
    if banks < 2:
        raise ValueError("one bank is just tape_block; the taped tier wants >= 2")
    m = -(-top // banks)  # ceil
    sizes = [m] * (banks - 1)
    last = top - m * (banks - 1)
    if last < 1:
        raise ValueError(f"{banks} banks of {m} leave the last bank empty; use fewer")
    return sizes + [last]


def taped_store_block(
    n: int,
    banks: int | tuple[int, ...],
    *,
    skip_batch: int = 1,
    answer_west: int | None = None,
) -> V3Store:
    """The banked-tape store as a placeable block, in men-v3's clothes.

    ``n`` is the machine's ``TAPE_SIZE`` (slot count; usable addresses
    ``1..n-1``). ``banks`` is the man knob: the block employs ``2*banks``
    tape men plus ``banks-1`` gate men, against the man-memory's two per slot.
    Returns the same :class:`V3Store` contract the men-v3 blocks use — request
    stub west, answer stub rising out of the top, exact pipe inventory — so
    ``lm1.machine`` places it through the identical branch, teleports and all.

    ``answer_west`` moves the **answer collector's west wall** to that interior
    column and turns its exit stub from a north riser into a south one. The
    collector is already a teleport — ``R`` has no distance term, so widening it
    is free — and the block's whole west end is empty for these rows, so pulling
    the wall west carries the answer to the caller's doorstep for nothing. It is
    what lets ``lm1.machine`` drop its own two forwarding rooms: with the answer
    already beside the CPU, a stub pipe finishes the job. ``None`` keeps the
    shipped north riser, so every existing caller's grid is byte-identical.
    """
    from .lm1.machine import tape_block

    sizes = taped_plan(n, banks)
    tapes = [tape_block(size + 1, skip_batch=skip_batch) for size in sizes]
    bank_w = max(t.width for t in tapes)
    bank_h = max(t.height for t in tapes)
    gates = [bank_gate(m) for m in sizes[:-1]]
    gate_w = max(w for _, w in gates)

    # ── the floor plan ───────────────────────────────────────────────────────
    # Banks in one row on top, the gate strip below; bank k sits a half pitch
    # east of gate k so each feed riser climbs the clear column between banks.
    nb = len(sizes)
    pitch = max(bank_w + 3, gate_w + 8)
    coll_y = 5  # collector interior rows 6..7, walls 5 and 8
    bank_y = 9
    gate_y = bank_y + bank_h + 2  # one clear row under the banks
    gx = [4 + k * pitch for k in range(nb - 1)]
    bx = [4 + gate_w + 4 + k * pitch for k in range(nb)]

    cells: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, ch: str) -> None:
        old = cells.get((x, y))
        if old is not None and old != ch:
            raise ValueError(f"taped block collision at {(x, y)}: {old!r} vs {ch!r}")
        cells[(x, y)] = ch

    def blit(x0: int, y0: int, block: dict[tuple[int, int], str]) -> None:
        for (x, y), ch in block.items():
            put(x0 + x, y0 + y, ch)

    def pipe(points: list[tuple[int, int]]) -> None:
        arrow = {(1, 0): ">", (-1, 0): "<", (0, 1): "v", (0, -1): "^"}
        path: list[tuple[int, int]] = []
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            sx = (x1 > x0) - (x1 < x0)
            sy = (y1 > y0) - (y1 < y0)
            cur = (x0, y0)
            while cur != (x1, y1):
                path.append(cur)
                cur = (cur[0] + sx, cur[1] + sy)
        path.append(points[-1])
        for (x, y), (nx, ny) in zip(path, path[1:], strict=False):
            put(x, y, arrow[(nx - x, ny - y)])

    for k in range(nb - 1):
        blit(gx[k], gate_y, gates[k][0])
    for k, t in enumerate(tapes):
        blit(bx[k], bank_y, t.cells)

    # ── feeds: gate k's local arm into bank k, its downstream into gate k+1 ──
    for k in range(nb - 1):
        east = gx[k] + gates[k][1] - 1  # this gate's east wall column
        riser = bx[k] - 2  # the clear column west of bank k
        # end ON the bank's own first stub cell, so the joining cell is drawn
        tin = (bx[k] + tapes[k].in_cell[0], bank_y + tapes[k].in_cell[1])
        pipe(
            [
                (east + 1, gate_y + GATE_LOCAL_ROW),
                (riser, gate_y + GATE_LOCAL_ROW),
                (riser, tin[1]),
                tin,
            ]
        )
        down_y = gate_y + GATE_DOWN_ROW
        if k + 1 < nb - 1:
            # chain: east two cells, up to the next gate's entry row, straight in
            pipe(
                [
                    (east + 1, down_y),
                    (east + 2, down_y),
                    (east + 2, gate_y + GATE_IN_ROW),
                    (gx[k + 1], gate_y + GATE_IN_ROW),
                ]
            )
        else:
            # the last gate's downstream IS the last bank's wire, rebased:
            # run east under the empty last-gate slot, then up its feed riser
            lt = tapes[-1]
            ltin = (bx[-1] + lt.in_cell[0], bank_y + lt.in_cell[1])
            pipe([(east + 1, down_y), (bx[-1] - 2, down_y), (bx[-1] - 2, ltin[1]), ltin])

    # ── answers: every bank rises into one collector teleport ────────────────
    from .memory_men import _room, teleport

    coll_x0 = bx[0] + tapes[0].out_cell[0] - 2
    coll_x1 = bx[-1] + tapes[-1].out_cell[0] + 2
    if answer_west is not None:
        if not 1 <= answer_west <= coll_x0:
            raise ValueError(f"answer_west {answer_west} is not west of the collector")
        coll_x0 = answer_west
    coll_rows, _ = teleport(coll_x1 - coll_x0 + 1)

    class _Grid:  # the tiny facade memory_men._room draws through
        def set(self, x: int, y: int, ch: str) -> None:
            if ch != " ":
                put(x, y, ch)

    _room(_Grid(), coll_x0, coll_y + 1, coll_rows)
    for k, t in enumerate(tapes):
        ax = bx[k] + t.out_cell[0]
        # extend the bank's own `^` stub up to the collector's south wall
        pipe([(ax, bank_y + t.out_cell[1] - 1), (ax, coll_y + 3)])
    out_x = coll_x0 + 2
    if answer_west is None:
        pipe([(out_x, coll_y - 1), (out_x, 0)])
    else:
        # South instead of north: the collector's west end is now beside the
        # caller's response row, which is *below* it, so the riser would only
        # climb to be walked back down. One cell clear of the south wall is
        # enough — the four bank answers attach to the same wall further east
        # and `s` has one outgoing pipe to choose from either way.
        pipe([(out_x, coll_y + 4), (out_x, coll_y + 5)])

    # ── the block's own ports ────────────────────────────────────────────────
    in_y = gate_y + GATE_IN_ROW
    pipe([(gx[0] - 2, in_y), (gx[0], in_y)])
    in_cell = (gx[0] - 2, in_y)
    if answer_west is None:
        ox, oy = out_x, 0
        while (ox, oy) not in cells:
            oy += 1  # the stub draw stops one short: name the real topmost cell
    else:
        ox, oy = out_x, coll_y + 4  # ... and southward it is the bottommost

    width = max(x for x, _ in cells) + 1
    height = max(y for _, y in cells) + 1
    # Pipes the block owns outright (the in/out stubs merge with the machine's
    # request and response runs, men-v3's convention): per bank two ring legs
    # and one feed and one answer, plus the gate-to-gate chain links.
    pipes = nb * 4 + (nb - 2)
    return V3Store(
        cells=cells,
        width=width,
        height=height,
        in_cell=in_cell,
        out_cell=(ox, oy),
        pipes=pipes,
    )
