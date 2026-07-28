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
emits) on *both* sides, so the chain composes: each gate owns ``M`` addresses
off one **end** of the space it is handed and **rebases** what it forwards, so
every bank decodes plain local addresses ``1..M`` and the last bank needs no
gate at all — the last gate's downstream arm is already speaking its wire,
rebased.

Which end is :func:`bank_gate`'s ``high``, and it is what lets the chain visit
the banks in an order other than address order (:func:`gate_chain`). That
matters because a gate forwards everything that is not its own, so the chain is
a **linear scan**: the bank at chain position ``j`` is ``j`` pass-throughs away.
Putting the hottest bank first is worth far more than anything inside a gate.

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
column term cancels and the row decides — the local pipe leaves beside the two
north arms, the downstream pipe beside the two south ones.

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

__all__ = ["bank_gate", "gate_chain", "gate_rows", "taped_store_block", "taped_plan"]

#: The gate's interior height; rows 1..12 like the two-tier adapter it descends
#: from, with the same return loop (east column down, floor west, climb to ``U``).
GATE_H = 12
#: West-wall row the request pipe enters (the ``U`` of the spine).
GATE_IN_ROW = 6
#: East-wall rows the two outgoing pipes attach to: local bank, downstream.
GATE_LOCAL_ROW = 2
GATE_DOWN_ROW = 10

#: The same gate with its five nop spacers taken out. The gate inherited its
#: 12-row body from the two-tier adapter, which needed the slack; this room does
#: not. Northbound out of the ``X`` the man only has to reach the ``d`` and then
#: the ``>``, so two rows do what five did; southbound past the elbow he only has
#: to reach the ``a`` and then the ``>``, so two rows do what four did. Every
#: deleted cell is a ``.`` — a nop the man *walks*, so it is a tick each way, and
#: the return leg's descent and climb shorten with the body.
#:
#: Nothing else moves: the arms keep their glyphs and their columns, so ``cx``
#: and ``cr`` are unchanged and every ``s``'s Manhattan distance to the two
#: outgoing pipes stays ``m``-independent (see :func:`bank_gate`).
COMPACT_GATE_H = 7
COMPACT_GATE_IN_ROW = 3
COMPACT_GATE_LOCAL_ROW = 1
COMPACT_GATE_DOWN_ROW = 6


def gate_rows(compact: bool = False) -> tuple[int, int, int, int]:
    """``(height, in row, local out row, downstream out row)`` for a bank gate.

    ``compact=False`` is the shipped 12-row body, so every existing caller's
    grid stays byte-identical; ``True`` is the spacer-free 7-row one.
    """
    if compact:
        return (
            COMPACT_GATE_H,
            COMPACT_GATE_IN_ROW,
            COMPACT_GATE_LOCAL_ROW,
            COMPACT_GATE_DOWN_ROW,
        )
    return GATE_H, GATE_IN_ROW, GATE_LOCAL_ROW, GATE_DOWN_ROW


def bank_gate(
    m: int, *, compact: bool = False, high: int | None = None
) -> tuple[dict[tuple[int, int], str], int]:
    """One range gate for a bank of ``m`` slots: cells (walls included), width.

    Local coordinates: walls at column 0 / row 0, interior from (1, 1). The
    caller attaches the request pipe to the west wall at the in row and the two
    outgoing pipes to the east wall at the local / downstream rows — all three
    from :func:`gate_rows`, which is also where ``compact`` is described.

    ``high`` turns the gate around: instead of claiming the **first** ``m``
    addresses of the space it is handed, it claims the **last** ``m`` of
    ``1..high``. The room does not change shape by one cell, because the ISA
    happens to be symmetric exactly where it has to be:

    ==========  =====================  ============================
    .           low gate (``high``     high gate
                is ``None``)
    ==========  =====================  ============================
    spine       ``UbrM`m+1`W-X``       ``UbrM`high-m`-X``
                ``A = addr - (m+1)``   ``A = (high-m) - addr``
    mine        ``A < 0``, i.e.        ``A < 0``, i.e.
                ``addr <= m``          ``addr > high-m``
    local addr  ``A + (m+1) = addr``   ``-A = addr - (high-m)``
    forwarded   ``A + 1 = addr - m``   ``addr``, untouched
    ==========  =====================  ============================

    Both forms therefore put **mine on the north arm** — the counter-clockwise
    side of the ``X``, which is what the block's floor plan needs, because the
    local pipe has to climb to a bank sitting *above* the gate strip while the
    downstream pipe runs east under it, and those two paths cross if the local
    one leaves below the spine. And both keep ``A == 0`` on the downstream side:
    for the low gate zero is the first address downstream, for the high gate it
    is the last, and either way it merges into the elbow correctly.

    The arms cost the same or less. ``N`` (negate, ``SPEC.md``) does in one glyph
    what the low gate's ``+`` does, so the north arms are the same width; the
    high gate's south arms only have to ``W`` the untouched address back into A,
    where the low gate's have to ``M1+`` it, so they are two cells *shorter*.
    """
    if m < 1:
        raise ValueError(f"a bank must hold at least one slot, not {m}")
    if high is not None and high - m < 1:
        raise ValueError(f"a high gate over 1..{high} cannot hand {high - m} addresses on")
    h, in_row, _local_row, _down_row = gate_rows(compact)
    # The four arm rows. The shipped body leaves nop spacers between the
    # stations (two above the `d`, one everywhere else); compact leaves none, so
    # each station sits on the row the man reaches next.
    gap = 0 if compact else 1
    turn = in_row + 1  # the A > 0 elbow, merging into column cx + 1
    n_write = in_row - 1 - 2 * gap  # the `d` that splits north on the parked op
    n_read = n_write - 1 - gap  # ... and the read arm above it (row 1 either way)
    s_write = turn + 1 + gap  # the `a` that splits south on the parked op
    s_read = s_write + 1 + gap  # ... and the read arm below it
    # the spine, and the four arms it hands A to (see the docstring's table)
    if high is None:
        spine = f"UbrM`{m + 1}`W-X"
        n_read_arm, n_write_arm = "+M0sWs", "+M1sWsrs"  # restore addr
        s_read_arm, s_write_arm = "M1+M0sWs", "M1+M1sWsrs"  # rebase to addr - m
    else:
        spine = f"UbrM`{high - m}`-X"
        n_read_arm, n_write_arm = "NM0sWs", "NM1sWsrs"  # negate to addr - (high-m)
        s_read_arm, s_write_arm = "WM0sWs", "WM1sWsrs"  # addr is already in B
    cx = len(spine)  # the range test's X (the spine starts at column 1)
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

    # the spine: op -> backpack, then the range test, then the three-way X
    text(1, in_row, spine)

    # A < 0 (mine): north, splitting on the parked op at `d`
    for y in range(n_read + 1, in_row):
        put(cx, y, "d" if y == n_write else ".")  # BP > 0 (write): right = east
    text(cx + 1, n_write, n_write_arm)
    put(cx, n_read, ">")  # BP == 0 (read): straight through to the top row
    text(cx + 1, n_read, n_read_arm)

    # A == 0 goes straight and A > 0 turns south; they merge one column east
    # (the zero is the first downstream address on a low gate and the last one
    # on a high gate, so either way it belongs with the southbound stream).
    put(cx + 1, in_row, "v")
    put(cx, turn, ">")
    put(cx + 1, turn, "v")
    for y in range(turn + 1, s_read):
        put(cx + 1, y, "a" if y == s_write else ".")  # BP > 0 (write): left = east
    text(cx + 2, s_write, s_write_arm)
    put(cx + 1, s_read, ">")
    text(cx + 2, s_read, s_read_arm)

    # the return leg: every arm walks east onto the same descent, then the
    # floor runs west and the climb re-enters the spine's `U` from below
    for y in range(1, h):
        put(cr, y, "v")
    put(cr, h, "<")
    put(cr - 1, h, "@")
    for x in range(2, cr - 1):
        put(x, h, "<")
    put(1, h, "^")
    for y in range(in_row + 1, h):
        put(1, y, "^")

    # walls
    for x in range(0, cr + 2):
        put(x, 0, "+" if x in (0, cr + 1) else "-")
        put(x, h + 1, "+" if x in (0, cr + 1) else "-")
    for y in range(1, h + 1):
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


def gate_chain(
    sizes: list[int] | tuple[int, ...], order: tuple[int, ...] | None = None
) -> list[tuple[int, int | None]]:
    """The chain, position by position: ``(bank index, high-gate top or None)``.

    ``sizes`` is :func:`taped_plan`'s **address order**; ``order`` is the order
    the chain visits those banks in, defaulting to address order. The last entry
    is the terminal bank, which has no gate — its ``top`` is always ``None``.

    Why an arbitrary permutation is *not* available, and which ones are: a gate
    hands its downstream neighbour one **contiguous** address space, rebased to
    start at 1, and the test it can do on the way is one-sided (the ``X`` splits
    on a sign). So each gate takes a bank off one **end** of the space it was
    handed — :func:`bank_gate`'s low form off the bottom, its ``high`` form off
    the top — and the reachable orders are exactly the end-peelings. That is
    enough for the thing worth doing: putting the hottest bank first.

    Traversal cost is why. ``A > 0`` means "not mine, pass downstream", so a
    request for the bank at chain position ``j`` walks ``j`` gates' south arms
    before it walks its own north one — the chain is a linear scan, and address
    order is not traffic order.
    """
    nb = len(sizes)
    ord_ = tuple(range(nb)) if order is None else tuple(order)
    if sorted(ord_) != list(range(nb)):
        raise ValueError(f"chain order {ord_} is not a permutation of 0..{nb - 1}")
    lo, hi, top = 0, nb - 1, sum(sizes)
    out: list[tuple[int, int | None]] = []
    for k in ord_[:-1]:
        if k == lo:
            out.append((k, None))
            lo += 1
        elif k == hi:
            out.append((k, top))
            hi -= 1
        else:
            raise ValueError(
                f"chain order {ord_} asks for bank {k} while the space still holds "
                f"banks {lo}..{hi}; a gate can only claim an END of what it is handed"
            )
        top -= sizes[k]
    out.append((ord_[-1], None))
    return out


def taped_store_block(
    n: int,
    banks: int | tuple[int, ...],
    *,
    skip_batch: int = 1,
    answer_west: int | None = None,
    compact_gate: bool = False,
    order: tuple[int, ...] | None = None,
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

    ``compact_gate`` builds the gates from :data:`COMPACT_GATE_H`'s spacer-free
    body instead of the shipped 12-row one — five fewer rows per gate and a
    shorter walk through every one of them. The gate strip is the block's floor,
    so the block loses those five rows too. ``False`` keeps the shipped body, so
    every existing caller's grid is byte-identical.

    ``order`` is the **chain** order over ``banks``' address-order sizes — see
    :func:`gate_chain`, which also says which permutations exist. ``None`` is
    address order, so every existing caller's grid is byte-identical. The banks
    are then laid out west to east in chain order, which is what keeps the floor
    plan (and the block's dimensions) exactly as they were.
    """
    from .lm1.machine import tape_block

    _gate_h, gate_in_row, gate_local_row, gate_down_row = gate_rows(compact_gate)
    plan = taped_plan(n, banks)
    chain = gate_chain(plan, order)
    sizes = [plan[k] for k, _ in chain]
    tapes = [tape_block(size + 1, skip_batch=skip_batch) for size in sizes]
    bank_w = max(t.width for t in tapes)
    bank_h = max(t.height for t in tapes)
    gates = [bank_gate(plan[k], compact=compact_gate, high=top) for k, top in chain[:-1]]
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
                (east + 1, gate_y + gate_local_row),
                (riser, gate_y + gate_local_row),
                (riser, tin[1]),
                tin,
            ]
        )
        down_y = gate_y + gate_down_row
        if k + 1 < nb - 1:
            # chain: east two cells, up to the next gate's entry row, straight in
            pipe(
                [
                    (east + 1, down_y),
                    (east + 2, down_y),
                    (east + 2, gate_y + gate_in_row),
                    (gx[k + 1], gate_y + gate_in_row),
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
    in_y = gate_y + gate_in_row
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
