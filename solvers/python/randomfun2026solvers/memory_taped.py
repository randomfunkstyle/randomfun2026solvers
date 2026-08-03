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

**The wire has a version.** Everything above describes ``v3``, the two-word
request. ``v4`` (:data:`TAPE_PROTOCOLS`) carries the op in the address's low
bit and sends **one** word, ``2*addr - op``, so the adapter, every gate in the
chain and every feed forwarder handle one fewer pipe transaction per access —
which is the read's *address-independent* floor, the term no routing lever
reaches. The banks are not on it: the feed forwarder unpacks with a single
``/`` (:func:`feed_unpack`), so every ring worker keeps the protocol it was
verified on. Measured on the ``deadman-3d_hires`` 21-round tour, same process,
same moment: **105,152,308 -> 101,523,077, -3.451%**, ``passed=True``,
``fatal=None``, box 620x403 -> 614x403 and every ``route_lengths`` entry
identical. On the 3-round tour the same pair gives mean read latency
**151.89 -> 141.15** against a floor (the histogram's minimum, which has no
routing in it at all) of **73 -> 69**; 10.74 ticks x 28,227 reads is 303,004
against 303,004 measured, so the conversion is exactly 1:1 and the whole saving
is read latency.
"""

from __future__ import annotations

from .memory_men_v3 import V3Store

__all__ = [
    "TAPE_PROTOCOLS",
    "bank_gate",
    "feed_relay",
    "feed_unpack",
    "gate_chain",
    "gate_rows",
    "taped_store_block",
    "taped_plan",
]

#: The store's wire formats. ``v3`` is the two-word request every tier shipped
#: on — ``0 addr`` for a read, ``1 addr value`` for a write. ``v4`` carries the
#: op in the address's **low bit** and sends **one** word, ``2*addr - op``, so
#: every stage from the adapter to the bank's own doorstep handles one fewer
#: pipe transaction per access. See :func:`bank_gate` for the arithmetic and
#: :func:`feed_unpack` for where the word is taken apart again.
#: ``v5`` keeps ``v4``'s wire and takes the **unpack out of the forwarder**: the
#: room stays — it is the corridor, and it crosses machine rows 160..195 in one
#: instruction because ``R`` has no distance term — but its body shrinks to a
#: bare receive-and-send (:func:`feed_relay`) and the ring worker takes the word
#: apart itself (``memory_tape.TAPE_WORKER_PROTOCOLS``). See
#: :data:`~.lm1.machine.TAPED_PROTOCOL` for what it measured.
TAPE_PROTOCOLS = ("v3", "v4", "v5")

#: The protocols whose gate chain and adapter speak the one-word packed wire.
_PACKED = ("v4", "v5")

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

#: Block-local row of the answer collector's **north wall**; its interior is the
#: two rows below and its south wall the row below those. Named because a caller
#: that wants to know where its own response row falls relative to the collector
#: has to compute this before the block exists — which is the same reason
#: ``answer_west`` and ``request_roof`` are stated in block coordinates.
COLLECTOR_ROW = 5

#: The v4 gate's **mine arm climbs the X's own column** instead of doglegging
#: onto the local row.
#:
#: The three-way ``X`` sends "this address is mine" counter-clockwise, i.e.
#: north, and the local row is two rows above the spine — so the man was always
#: going to stand on those two cells. Shipped he stood on a ``^`` and a ``>`` and
#: executed nothing on either, then turned east onto the arm's ``N s``. That is
#: ``UbW-X^>Ns`` = **9 cells** of pre-send walk against ``UbW-XNs`` = **7**, which
#: is the routed floor for seven ops. Both cells come off the request's own
#: critical path, on every read that terminates at a gate.
#:
#: The op branch moves to the row above the arm, which is the second cell: the
#: read's counter-clockwise exit off ``x`` there lands *on the descent column*
#: rather than one cell short of it, so the shipped ``>`` that turned it north is
#: gone too.
#:
#: Off is the shipped grid to the cell, which is what holds ``deadman-3d``'s
#: checked-in taped store byte-identical.
V4_GATE_MINE_UP = True

#: The answer collector takes :func:`~.memory_men.teleport`'s **latency** art
#: rather than its shortest lap: ``R`` and ``s`` adjacent, an 8-cell lap instead
#: of 6.
#:
#: The collector is the last room on every read -- the bank's ``S`` climbs a riser
#: into it and it forwards to the CPU -- and it is 97.7% idle. So its lap is worth
#: nothing and its ``R``-to-``s`` walk is worth a tick of read latency each. On
#: the 6-cell lap that walk is **three moves and cannot be fewer**, because a 3x2
#: rectangle's two non-corner cells are diagonally opposite and ``R`` and ``s``
#: are the two glyphs that cannot double as a corner. Four columns instead of
#: three put them side by side.
#:
#: This is the *documentation* anchor; the live selection is per ``(slug, tier)``
#: in :data:`~.lm1.machine.TAPED_COLLECTOR_FAST`, because ``deadman-3d``'s taped
#: store is byte-pinned to a checked-in ``.man`` and shares this block. The other
#: nine callers of ``teleport`` do not pass the flag and do not move either.
#:
#: **Measured, 21-round hi-res tour, same process, same moment, control
#: reproducing 85,522,204 to the tick: 85,522,204 -> 84,918,154, -0.706%**,
#: ``passed=True``, ``fatal=None``, box 614x403 and every ``route_lengths``
#: entry unchanged.

#: The **v4** body. Same seven rows as the compact v3 one, laid out around a
#: different branch order: the range test still splits mine (north) from
#: downstream (south), but each side now sends its **single** word first and
#: only then splits on the op — with ``x``, which turns on the backpack's low
#: bit and is exactly the parked request word's op. Two arms and two two-cell
#: tails replace four arms, and the value-passing tail is off the read's path
#: entirely.
#:
#: ::
#:
#:     y=1  mine read tail  ..........>        (walk east onto the descent)
#:     y=2  mine arm        > N s x            <- local pipe attaches here
#:     y=3  mine write tail       > r s
#:     y=4  spine           U b W - X v   >    (and the downstream read tail)
#:     y=5  elbow + down    > > W s x
#:     y=6  down write tail         > r s      <- downstream pipe attaches here
#:     y=7  floor           the return leg, unchanged
V4_GATE_H = 7
V4_GATE_IN_ROW = 4
V4_GATE_LOCAL_ROW = 2
V4_GATE_DOWN_ROW = 6

#: The east-wall row the **downstream pipe** attaches to, which is *not* the row
#: the downstream write tail stands on. Both are called "the down row" and the
#: two were the same number until it was noticed that they need not be.
#:
#: ``s`` binds by Manhattan distance to the attached segment, and both outgoing
#: pipes leave the **same column** — one cell east of the east wall — so ``|dx|``
#: cancels and the binding is purely a question of rows. The four ``s`` cells in
#: a v4 gate stand at rows 2 and 3 (the mine arm and its write tail) and 5 and 6
#: (the downstream arm and its write tail), and the requirement is only that the
#: first two are nearer the local attachment and the last two nearer the
#: downstream one. With the local pipe on row 2 that leaves the downstream pipe
#: free anywhere from row 3 down, and **row 4 — the spine's own row — is the one
#: that matters**, because the chain link leaves at this row and enters the next
#: gate at :data:`V4_GATE_IN_ROW`. Equal rows make it a straight horizontal run
#: and the two cells it used to climb disappear; the last gate's downstream feed
#: loses the same two off its riser.
#:
#: Row 3 is exactly the tight case and it is a **tie**, decided by SPEC rather
#: than by luck: the mine write tail's ``s`` at row 3 is one row from the local
#: attachment at row 2 and one row from the downstream one at row 4, and
#: "*ties break by reading order (top to bottom, left to right)*" gives it to row
#: 2 — the local pipe, which is the one the write must take. Row 3 for the
#: downstream pipe would lose that tail to the wrong bank in silence, and row 5
#: is the elbow's own row, so 4 is both the best and the only value that shortens
#: anything. Verified the way a silent mis-bind has to be: the 901-address
#: readback at every ``skip_batch``, which writes each address before it reads it.
V4_GATE_DOWN_PIPE_ROW = 4
#: The east wall both v4 forms are padded out to. The high form's return column
#: lands at 12 and the low form's at 13; padding the *wall* rather than moving
#: the *column* keeps every gate the same width — so ``taped_store_block``'s
#: bank row, and with it every feed riser, is the same length whichever form a
#: chain position happens to take — while the man still walks the tight loop.
#: (Moving the column instead is ``tight_return``, and it measured **+0.892%**.)
_V4_FLAT_CR = 13

#: The v4 feed forwarder's fixed body: the descent, its two tails and the spawn.
#: Anything taller is the same room with the read's return leg stretched.
V4_FEED_H = 14


def feed_unpack(height: int) -> tuple[list[str], list[tuple[int, int]]]:
    """The **v4** feed forwarder: one packed word in, the bank's two words out.

    A drop-in for :func:`~.memory_men.teleport_v` in the same four-column
    corridor and with the same ports — one incoming pipe on the south wall, one
    outgoing on the east — but it takes the wire apart instead of relaying it,
    so the *bank* keeps the two-word protocol it was measured and verified on
    and nothing in ``memory_tape`` moves by one cell.

    The unpack is one glyph. With ``2`` parked in B, ``/`` divides the wire word
    ``w = 2*addr - op`` and SPEC's floored division does the rest::

        read   w = 2a    ->  A = a,     B = 0     ->  op 0, and A+B = a
        write  w = 2a-1  ->  A = a - 1, B = 1     ->  op 1, and A+B = a

    so a single ``+`` restores the address on **both** arms, with no branch: the
    remainder that identifies the write is exactly the one the floor lost. The
    ``X`` at the bottom is only about the *value* word — it fires after both
    request words are already in the pipe, so a read never walks it.

    **The turn stands before the ``R``, not after it, and that is worth a tick on
    every access.** Only the cells between the ``R`` and the *last* ``s`` are on
    the wire: the forwarder is 94-99% blocked, so it is already standing on the
    ``R`` when the word arrives and everything before it — the climb, the reload,
    the entry — is spent inside the gap. The word's own descent is not. The first
    body turned south *after* reading (``> Rv`` over ``M  /``), which put the turn
    between the ``R`` and the ``/``; entering the descent from the north instead
    (``> v`` over ``M R``) moves that cell into the free half of the lap and hands
    the address to the bank one tick earlier. Measured on the 21-round hi-res
    tour, same process, same moment: 96,280,186 -> 95,979,598, **-0.312%**, and
    the same -0.315% again on top of :data:`~.lm1.machine.TAPED_BANK_WEST_GROW`.
    The probe that prices this leg agrees to three digits from the other side:
    padding it with nops between the ``W`` and the first ``s`` costs **+0.313%
    each** (4 give +1.250%, 8 give +2.504%, dead linear), so the tick is exactly
    where it is claimed to be.

    The ``N`` under the last ``W`` is what buys the room for that: with ``-op`` in
    A the write turns **counter-clockwise**, east, so the read's own turn west has
    the two cells it needs beside the climb. Both cells are past the second ``s``
    and cost nothing.

    The lap re-parks its own constant on the way home (``2`` then ``M``, walked
    north, so they stand in that order down the column), which is also what
    makes the **first** lap correct: the spawn stands on the return leg and
    reaches the work through it, facing east into the read tail's own ``<``.

    **The loop is fourteen rows and it stays fourteen rows however tall the room
    is**, which is the same property :func:`~.memory_men.teleport_v` has and for
    the same reason: ``R`` takes from any incoming pipe with no distance term, so
    a pipe entering the *south* wall is read by the man at the north end in one
    instruction.

    Letting the loop follow the room instead was built and measured first, and
    it is worth recording what it did: the forwarders' work went from **+9.26 to
    +36.8 ticks per access** over the two-word room, and the 21-round tour came
    out at **exactly the same tick, 101,523,077**. That is the cleanest evidence
    in this store of where its critical path is *not* — a feed forwarder is
    94-99% blocked, so its service time is spent inside the gap before the next
    request and none of it is on the wire. The compact loop is kept because
    headroom that is free today is not free at four times the traffic, but it
    bought nothing and is not claimed to have.
    """
    ih = max(V4_FEED_H, height)
    rows = [
        "> v ",  # (0,0) return leg turns east, then south INTO the descent
        "M R ",  # ... the reload's `M` on the way home; R takes the packed word
        "2 / ",  # ... and its `2` below it; `/` unpacks
        "^ W ",  # W puts the op in A
        "^ s ",  # send op
        "^ W ",  # A = addr - op, B = op
        "^ + ",  # A = addr
        "^ s ",  # send addr        <- the last cell on the wire
        "^ W ",  # A = op again
        "^ N ",  # ... negated, so the write turns the other way
        "^ Xv",  # write turns counter-clockwise (east); read goes straight on
        "^@<R",  # the read's turn west, the spawn on it, and the value
        "^  s",  # ... which the write passes through
        "^  <",  # and both tails walk back onto the climb
    ]
    rows += [" " * 4] * (ih - V4_FEED_H)  # the room under the loop, unused
    #: Every interior row down the **east** side is a legal attachment row.
    return rows, [(3, i) for i in range(ih)]


#: The v5 forwarder's fixed body: receive, send, and the op branch behind it.
V5_FEED_H = 8

#: **Instruments, not knobs.** Nop cells inserted into :func:`feed_relay`'s loop,
#: on one side of the send or the other, so the tick price of a cell in each half
#: of the lap can be measured on the real machine rather than argued from the
#: pipe lengths. ``RELAY_PRE_PAD`` adds one blank row between the ``R`` and the
#: ``s`` — one tick of *pipe* latency per access. ``RELAY_POST_PAD`` pushes the
#: return row that many rows south — the man descends that much further and
#: climbs that much further back, so **two** ticks per access, all of them after
#: the send. Both are 0 in every build; the block's walls, ports and every other
#: room are byte-identical at 0 (see ``taped_store_block``'s ``pipes`` count,
#: which does not move either).
#:
#: **They exist because the answer expires.** "After the send is free" is not a
#: property of the machine, it is a property of the machine *at a given
#: occupancy*: it holds while the gap between two requests to the same room is
#: longer than the man's walk home, and every round of this work shortens the
#: gap. So the numbers below carry the mean read latency they were taken at, and
#: the right response to a much faster machine is to run these again rather than
#: to inherit them.
#:
#: At **105.6 mean read latency** (this build), same process, same moment,
#: 21-round tour, control reproducing to the tick:
#:
#: | pad | ticks/access | tour |
#: |---|---|---|
#: | ``RELAY_PRE_PAD=1`` | +1 before the send | **+0.329%** |
#: | ``RELAY_PRE_PAD=4`` | +4 | +1.319% (0.330 each) |
#: | ``RELAY_POST_PAD=4`` | +8 after the send | **+0.000%** (+8 ticks total) |
#: | ``RELAY_POST_PAD=8`` | +16 | +0.000% (+16 ticks total) |
#: | ``RELAY_POST_PAD=16`` | +32 | +0.000% (+32 ticks total) |
#:
#: The post-send rows are not rounding: the tour grew by *exactly* twice the pad,
#: once, which is the single lap still in flight when the last frame lands. Per
#: access it is zero to the tick. The forwarder is 94-99% blocked and that has
#: not changed.
#:
#: **The bank worker is a different room and there the rule has already broken** —
#: see ``memory_tape.WORKER_V4_POST_PAD``, where a post-send tick now costs
#: 0.019%. The distinction that survives is not "before/after the send" but *how
#: idle the room is*: this one waits 94-99% of the time, the worker only 82%.
RELAY_PRE_PAD = 0
RELAY_POST_PAD = 0


def feed_relay(height: int) -> tuple[list[str], list[tuple[int, int]]]:
    """The **v5** feed forwarder: one word in, the same word out.

    :func:`feed_unpack`'s room with its arithmetic taken out. The wire word is
    now taken apart in the *bank*, in the backpack, at no cost at all
    (``memory_tape.TAPE_WORKER_PROTOCOLS``), so what is left here is the only
    part of the room that was ever on the wire::

        R   take the packed word
        s   send it on            <- one cell, where six used to stand

    **Six ticks of the read's critical path become one, and the measurement that
    prices them is symmetric.** Padding this leg with nops costs *+0.313% each*
    (4 give +1.250%, 8 give +2.504%, dead linear), while 27.5 t/access of extra
    service added *after* the send left the tour on the identical tick — the
    forwarder is 94-99% blocked, so only the cells between the ``R`` and the last
    ``s`` are ever on anyone's critical path.

    Everything after the send is therefore free, and the op branch goes there:
    ``b`` parks the word and ``x`` turns on its low bit, which *is* the op, so a
    write walks into its own ``R``/``s`` for the value word and a read walks
    straight home. That is the same branch :func:`_bank_gate_v4` makes and for
    the same reason — a packed wire needs no register to remember its op.

    Same four-column corridor, same two ports, and the loop is **eight rows and
    stays eight rows however tall the room is**: ``R`` takes from any incoming
    pipe with no distance term, so a pipe entering the *south* wall is read by
    the man at the north end in one instruction.

    **The walls are load-bearing and the empty cells are not waste.** On the
    hi-res block the room is ``4 x 35`` interior with **22 live cells** — the
    loop is the top eight rows and rows 8..34 are blank — and that is correct,
    because *this room is the corridor*. Its two ports are at opposite ends of
    it: the outgoing pipe leaves the **east** wall on the room's first interior
    row, which is the bank's own request stub (machine row 161), and the
    incoming pipe enters the **south** wall just above the gate strip (machine
    row 196). A room that stopped after eight rows could not touch both, and the
    feed would go back to being the 45-cell climb ``feed_teleport`` was built to
    delete (~+11%). The blank rows are what the man crosses in **one
    instruction** instead.

    So the only dimension that could give anything back is the **width**, and it
    was built and measured rather than argued. A three-column loop is legal —
    put the climb on the east and the value tail on the west, so ``x``'s fixed
    sense (a write turns clockwise) sends the write to the tail and the read
    straight home::

         v<        R takes the packed word at (1,1), sends at (1,2), and
         R^        `b`/`x` split behind the send exactly as here; the climb
         s^        runs up column 2 and the value tail down column 0.
         b^
        vx^
        R ^
        s ^
        >@^

    It builds, it answers all 901 addresses, and it takes the box to
    **604x403** — because the corridor is what sets ``nb_gap``, and ``nb_gap``
    sets the pitch at every one of eleven banks, so one column off the room is
    ten off the machine. **It also costs 856,034 ticks, +0.94%** (91,708,864
    against 90,852,830, same process, same moment): the pitch carries the whole
    bank row, and pulling the banks together moves every answer riser and every
    gate's west growth with it. Ticks are the goal, so the four-column room
    stays and its 27 blank rows stay with it.
    """
    body_h = V5_FEED_H + RELAY_PRE_PAD + RELAY_POST_PAD
    ih = max(body_h, height)
    rows = [
        "> v ",  # (0,0) return leg turns east, then south INTO the descent
        "^ R ",  # ... R takes the packed word
    ]
    # A padded cell here is a cell of *pipe* latency: the CPU is stopped for it.
    rows += ["^   "] * RELAY_PRE_PAD
    rows += [
        "^ s ",  # ... and sends it on   <- the only cell on the wire
        "^ b ",  # BP = the word, whose low bit is the op
        "^vxv",  # x: WRITE turns CW/west into the value tail, READ CCW/east
        "^R v",  # the write's value word ...
        "^s v",  # ... passed straight through
    ]
    # ... and a padded cell here is two ticks of walking, both of them behind the
    # send: the descent to the return row and the climb back off it.
    rows += ["^   "] * RELAY_POST_PAD
    rows += [
        "^<@<",  # both tails walk back onto the climb; the spawn stands on it
    ]
    rows += [" " * 4] * (ih - body_h)  # the room under the loop, unused
    #: Every interior row down the **east** side is a legal attachment row.
    return rows, [(3, i) for i in range(ih)]


#: The rotating forwarder's fixed body: the relay, the head update, and the spawn
#: that seeds it. Sixteen rows against :data:`V5_FEED_H`'s eight, and every one of
#: the eight extra is **behind the send**.
V5_ROT_FEED_H = 16


def feed_rotate(height: int) -> tuple[list[str], list[tuple[int, int]]]:
    """:func:`feed_relay` **carrying the bank's ring head in its off hand**.

    This is where the rotating worker's missing register went. A rotating bank
    needs ``ROT = (n + addr - head) % n`` and that wants four live values against
    three registers (:func:`~.memory_tape.worker_v2_rot` says why the shuffle
    cannot close, and that there is no ``BP -> A`` glyph to close it with). This
    man has slack for exactly one of them: he handles a single word, he keeps A,
    B and BP across laps like every little man, and he is **94-99% blocked**, so
    everything he does behind his own ``s`` is free (measured: ``RELAY_POST_PAD``
    at 4, 8 and 16 rows all came out at +0.000% per access).

    So B holds ``P = 2*head - 1`` from one access to the next and the wire word
    ``w = 2*addr - op`` leaves as ``D = w - P``. That is **one glyph** on the
    critical path — ``-`` is ``A = A - B`` and B is already the head — so the
    ``R``-to-``s`` leg goes from one cell to two, against the 0.313%/tick this
    leg prices at. Everything that makes the delta usable happens in the bank
    (:func:`~.memory_tape.worker_v2_rot`), off a single ``%`` against the ring
    size it already parks.

    **Why the head is ``2*head - 1`` and not ``head``.** The reduction has to
    give ``2*ROT + 1 - op`` for every sign of the delta *and* both ops, and the
    odd offset is exactly what keeps ``ROT == 0`` on a **write** from wrapping to
    a full lap. With ``P = 2*head`` that one case reduces to ``2n - 1`` and the
    bank skips ``n - 1`` words instead of ``0``; the ring survives it (the count
    is still exact) but the access costs a whole lap, which is the thing this
    body exists to delete.

    **The update, and why it needs the op.** The head after an access is
    ``addr + 1`` whatever the op was, and ``w`` encodes ``2*addr - op``, so
    recovering ``2*addr`` needs ``op`` — there is no encoding of the two in one
    word that avoids it. ``x`` already splits on it (a write's word is odd), so
    each tail knows its own constant statically and adds it: the read tail wants
    ``w + 1``, the write tail ``w + 2``, and both land on ``2*addr + 1``, which is
    ``2*(addr+1) - 1``. No reduction is needed on the way out — ``addr <= n-1``
    keeps ``P`` inside ``[3, 2n-1]`` and the bank's ``%`` absorbs the wrap at
    ``addr == n-1`` on its own.

    ``x``'s sense is **inverted** from :func:`feed_relay`'s here and that is not
    a choice: the word that reaches ``b`` is ``D``, not ``w``, and subtracting an
    odd ``P`` flips the parity. A **read** turns clockwise/west now, so the read
    tail is the west column and the value-word passthrough is the east one.

    **The spawn had to move.** ``feed_relay``'s ``@`` stands on the return leg,
    which every lap re-walks; a seed for B may not. It stands two rows below the
    loop instead, on a three-glyph run (``1 N`` then ``M``) that only the first
    man's first steps ever touch, and joins the return leg from the south. That
    is what puts ``head = 0`` — an untouched ring's head — into B before the
    first request arrives.

    Same four-column corridor and the same two ports as every other forwarder, so
    nothing in the block's floor plan moves; the room is the corridor and the
    rows below the loop stay blank exactly as they were.
    """
    ih = max(V5_ROT_FEED_H, height)
    rows = [
        "> v ",  # (0,0) the return leg turns east, then south into the descent
        "^ R ",  # A = w                                  [B = P = 2*head - 1]
        "^ - ",  # A = D = w - P                          <- the delta
        "^ s ",  # ... and out                            <- the only wire cells
        "^ b ",  # BP = D, whose low bit is 1 - op
        "^vxv",  # READ turns clockwise/west; WRITE counter-clockwise/east
        "^+ +",  # A = D + P = w again (B still holds P)
        "^M M",  # B = w
        "^1 R",  # READ: A = 1          | WRITE: the value word ...
        "^+ s",  # A = w + 1            | ... straight through
        "^M 2",  # B = 2*addr + 1  done | A = 2
        "^  +",  #                      | A = w + 2
        "^  M",  #                      | B = 2*addr + 1  done
        "^< <",  # both tails walk back west onto the climb
        "   M",  # the seed's own last glyph: B = -1, i.e. head 0
        "@1N^",  # ... and the spawn, which no lap ever returns to
    ]
    rows += [" " * 4] * (ih - V5_ROT_FEED_H)  # the room under the loop, unused
    #: Every interior row down the **east** side is a legal attachment row.
    return rows, [(3, i) for i in range(ih)]


#: The broadcast room's fixed body: :func:`feed_relay` with ``s`` written ``S``.
BCAST_H = 8

#: Interior columns of the descender that lifts the request past the bank row,
#: and the block column its **west wall** stands on. The block's north-west
#: corner is not free -- the machine tucks the adapter into it -- so where
#: this lands is a search, not a choice.
DESC_W = 4
DESC_X = 1
DESC_TOP = 0

#: The filter room's spine row, and the rows its four discard exits use.
FILTER_IN_ROW = 2


def bcast_room(span: int) -> list[str]:
    """The one room that shouts every request at **every** bank at once.

    This is :func:`feed_relay` with its two ``s`` glyphs written ``S``, and that
    single substitution is the whole of the mechanism. ``S`` binds
    ``tuple(room.outgoing)`` — *every* outgoing pipe, with no distance term and
    no nearest-wins rule — where ``s`` binds the Manhattan-nearest one.

    **That is why this room, alone in the block, may be long.** A gate may not
    grow toward its bank: its two outgoing pipes share a wall and ``s`` picks
    between them, so moving the room flips the binding and routes reads into the
    wrong tape in silence (see :func:`bank_gate`, "a room can reach its caller,
    but not its callee"). ``S`` has nothing to pick, so the room is free to span
    the whole strip and put its north wall directly under all eleven filters.
    Every riser off it is then two cells, and the fan-out costs no distance at
    all — which is the thing that makes broadcasting cheaper than the chain
    rather than more expensive.

    The lap is eight rows whatever ``span`` is: the art is the first four
    columns and the rest of the room is floor the man never walks, exactly as
    :func:`feed_relay`'s twenty-seven blank rows are.

    Two words go out per write and one per read, because the wire is what it
    always was — ``b`` parks the packed word, ``x`` turns on its low bit, and
    the write tail broadcasts the value behind the address. Every filter
    swallows both, which is what keeps ``S`` (all-or-nothing by definition) from
    wedging on a pipe that is one word behind.
    """
    if span < 4:
        raise ValueError(f"the broadcast room is at least its own art wide, not {span}")
    rows = [
        "> v ",  # the return leg turns east, then south into the descent
        "^ R ",  # A = the packed request word
        "^ S ",  # ... at every bank at once      <- the only cell on the wire
        "^ b ",  # BP = the word, whose low bit is the op
        "^vxv",  # x: WRITE turns CW/west into the value tail, READ CCW/east
        "^R v",  # the write's value word ...
        "^S v",  # ... broadcast behind it
        "^<@<",  # both tails walk back onto the climb; the spawn stands on it
    ]
    return [r + " " * (span - 4) for r in rows]


def filter_room(base: int, size: int) -> "Circuit":
    """One bank's share of the broadcast: swallow every word, forward only mine.

    The chain's gate answers "is this mine?" by *elimination* — it hands on what
    it does not want, so the bank at chain position ``j`` is ``j`` pass-throughs
    away and the whole store pays for the ordering. A filter answers the same
    question by *arithmetic*, in parallel with ten others, and nothing is ahead
    of anything.

    **The rebasing is free, and that is the structural payoff.** A gate rebases
    the space it hands on so its neighbour sees ``1..m``; here ``B`` is parked at
    ``2*base`` across laps and the spine's single ``-`` produces
    ``rebased = w - 2*base``, which *is* the bank's own local wire word. The
    glyph that decides the word is mine is the glyph that converts it.

    ``b`` follows immediately, so ``BP`` holds the rebased word — and therefore
    the op in its low bit — on **every** path out of the room. That is what makes
    the four discard exits share one lane: a discarded *write* still has to eat
    its value word or the pipe desyncs one word forever, and the lane's single
    ``x`` knows whether to.

    The high bound is one-sided because the constant is ``2*size + 1`` rather
    than ``2*size``: the wire is ``w = 2a - op``, so ``rebased == 2*size + 1``
    is a write of ``base + size + 1`` — already the next bank's — and ``A > 0``
    is exactly "mine" with no second case to merge.
    """
    from .circuit import Circuit, E, N, S
    from .memory_tape import lit

    if size < 1:
        raise ValueError(f"a bank filter covers at least one address, not {size}")
    if base < 0:
        raise ValueError(f"a bank's base is an address, not {base}")
    b2, cc = 2 * base, 2 * size + 1
    dig = str(b2)[::-1]
    arm2 = "M" + lit(cc) + "-X"
    IN = FILTER_IN_ROW
    x1 = 5                      # R at 2, - at 3, b at 4, X at 5
    x2 = x1 + len(arm2)         # the high-bound X
    D = x2 + 7                  # the column every discard comes home on
    cr = D + 4                  # the descent
    lane = IN + 4
    floor = lane + 2
    c = Circuit(cr + 1, floor + 1)

    # ── the lap: the floor reloads B = 2*base, column 1 climbs to the spine ─
    c.set(2, floor, "M")
    c.set(3, floor, "`")
    for i, ch in enumerate(dig):
        c.set(4 + i, floor, ch)
    c.set(4 + len(dig), floor, "`")
    c.set(cr, floor, "<")
    c.set(cr - 1, floor, "@")
    for x in range(5 + len(dig), cr - 1):
        c.set(x, floor, "<")
    for y in range(IN + 1, floor + 1):
        c.set(1, y, "^")
    c.turn(1, IN, E)

    # ── the spine: rebase, park the op, test the low bound ─────────────────
    c.run(2, IN, "R-bX")
    # ── the high bound, one-sided on `2*size + 1` ──────────────────────────
    c.turn(x1, IN + 1, E)
    c.run(x1 + 1, IN + 1, arm2)
    # ── mine: the rebased word is in B, so `W` fetches it back ─────────────
    c.turn(x2, IN + 2, E)
    c.run(x2 + 1, IN + 2, "Wsx")
    c.route((x2 + 3, IN + 1), N, [], (cr, IN + 1), S)          # READ: home
    c.turn(x2 + 3, IN + 3, E)
    c.run(x2 + 4, IN + 3, "Rs")                                # WRITE: the value
    c.route((x2 + 6, IN + 3), E, [], (cr, IN + 3), S)

    # ── every discard walks to column D and drops onto the shared lane ─────
    c.route((x1, IN - 1), N, [(D, IN - 1)], (D, lane), E)      # low,  A < 0
    c.route((x1 + 1, IN), E, [(D, IN)], (D, lane), E)          # low,  A == 0
    c.route((x2, IN), N, [(D, IN)], (D, lane), E)              # high, A < 0
    c.route((x2 + 1, IN + 1), E, [(D, IN + 1)], (D, lane), E)  # high, A == 0
    # ── the lane: BP is the rebased word, so `x` knows what to swallow ─────
    c.run(D + 1, lane, "x")
    c.route((D + 1, lane - 1), N, [], (cr, lane - 1), S)       # READ: nothing more
    c.turn(D + 1, lane + 1, E)
    c.run(D + 2, lane + 1, "R")                                # WRITE: eat the value
    c.route((D + 3, lane + 1), E, [], (cr, lane + 1), S)

    for y in range(IN - 1, floor):
        if c.free(cr, y):
            c.set(cr, y, "v")
    return c


def gate_rows(compact: bool = False, protocol: str = "v3") -> tuple[int, int, int, int]:
    """``(height, in row, local out row, downstream out row)`` for a bank gate.

    ``protocol="v4"`` returns the one-word body's rows; it has no non-compact
    form, because it was never the two-tier adapter and never had the spacers.

    ``compact=False`` is the shipped 12-row body, so every existing caller's
    grid stays byte-identical; ``True`` is the spacer-free 7-row one.
    """
    if protocol not in TAPE_PROTOCOLS:
        raise ValueError(f"unknown store protocol {protocol!r}; expected {TAPE_PROTOCOLS!r}")
    if protocol in _PACKED:
        # The caller draws pipes with this, so it gets the *attachment* row; the
        # write tail's row is the gate's own business (:data:`V4_GATE_DOWN_PIPE_ROW`).
        return (V4_GATE_H, V4_GATE_IN_ROW, V4_GATE_LOCAL_ROW, V4_GATE_DOWN_PIPE_ROW)
    if compact:
        return (
            COMPACT_GATE_H,
            COMPACT_GATE_IN_ROW,
            COMPACT_GATE_LOCAL_ROW,
            COMPACT_GATE_DOWN_ROW,
        )
    return GATE_H, GATE_IN_ROW, GATE_LOCAL_ROW, GATE_DOWN_ROW


def _bank_gate_v4(
    m: int,
    *,
    high: int | None = None,
    park_const: bool = False,
    zero_arm: bool = False,
    west_grow: int = 0,
    north_grow: int = 0,
) -> tuple[dict[tuple[int, int], str], int]:
    """One **v4** range gate: the one-word wire in, the one-word wire out.

    The wire is ``w = 2*addr - op`` (``2a`` for a read, ``2a-1`` for a write),
    and that particular packing is not a convention — it is the one that makes
    the gate's arithmetic *free*. A gate's whole job is one comparison and one
    rebasing, and both survive the doubling untouched:

    ==========  ===============================  ==============================
    .           low gate (``high`` is ``None``)  high gate
    ==========  ===============================  ==============================
    constant    ``2m + 1``                       ``2(high - m)``
    spine       ``Ub-X``  (``A = w - c``)        ``UbW-X`` (``A = c - w``)
    mine        ``A < 0``, i.e. ``addr <= m``    ``A < 0``, i.e. ``addr > high-m``
    local wire  ``A + c = w``      -> ``+``      ``-A = w - c``     -> ``N``
    forwarded   ``A + 1 = w - 2m`` -> ``M1+``    ``w``, untouched   -> ``W``
    ==========  ===============================  ==============================

    Every one of those is the *same glyph* the two-word gate used on the bare
    address, because ``2a - op`` is monotone in ``a`` with reads and writes of
    address ``a`` sitting at ``{2a-1, 2a}``: an address threshold is a wire
    threshold at twice the constant, and the high form's rebasing is still a
    plain negation. (``2a + op`` — the other obvious packing — is one ``+1``
    worse on the high form's local arm, which is the hot one.)

    **The op branch moved behind the send, and that is the second half of the
    win.** ``b`` parks the wire word, whose low bit *is* the op, and ``x`` turns
    on that bit — clockwise for a write, counter-clockwise for a read, always
    turning. So each side sends its single word and only then splits: the read
    walks one cell onto the return leg, the write walks into ``r s`` and passes
    the value on. Four arms become two arms and two tails, and a read never
    executes the value-passing code at all.

    Row 5 carries the elbow *and* the downstream arm, which the two-word gate
    could not do: with the op already in the backpack there is nothing to
    receive after the branch, so the merge cell can point straight into the
    arm. That is what keeps the body at seven rows.
    """
    if m < 1:
        raise ValueError(f"a bank must hold at least one slot, not {m}")
    if high is not None and high - m < 1:
        raise ValueError(f"a high gate over 1..{high} cannot hand {high - m} addresses on")
    if west_grow < 0 or north_grow < 0:
        raise ValueError(f"a gate room grows outward, not inward: {(west_grow, north_grow)}")
    h, in_row, local_row, down_row = V4_GATE_H, V4_GATE_IN_ROW, V4_GATE_LOCAL_ROW, V4_GATE_DOWN_ROW
    if high is None:
        const = 2 * m + 1
        spine = "Ub-X" if park_const else f"UbM`{const}`W-X"
        n_arm, s_arm = "+s", "M1+s"
    else:
        const = 2 * (high - m)
        spine = "UbW-X" if park_const else f"UbM`{const}`-X"
        n_arm, s_arm = "Ns", "Ws"
    cx = len(spine)  # the range test's X (the spine starts at column 1)
    xn = cx + 1 + len(n_arm)  # the mine arm's `x` (the flat arm's; the climbed
    # ``zero_arm`` gives the ``A == 0`` word its own copy of the arm, so the
    # elbow stops carrying a merge cell and the hot arm starts one column west.
    _zero = zero_arm and high is not None
    xs = cx + (1 if _zero else 2) + len(s_arm)  # the downstream arm's `x` (the
    #                                            climbed mine arm takes cr - 1)
    cr = xs + 3  # one east of the longest tail (`>rs` off the `x`)
    width = max(cr, _V4_FLAT_CR) + 2
    g: dict[tuple[int, int], str] = {}

    def put(x: int, y: int, ch: str) -> None:
        old = g.get((x, y))
        if old is not None and old != ch:
            raise ValueError(f"gate collision at {(x, y)}: {old!r} vs {ch!r}")
        g[(x, y)] = ch

    def text(x: int, y: int, s: str) -> None:
        for i, ch in enumerate(s):
            put(x + i, y, ch)

    # the spine: the wire word into the backpack, the range test, the three-way X
    text(1, in_row, spine)

    # A < 0 (mine): north to the local row, send, then split on the parked op
    if V4_GATE_MINE_UP:
        # **The two climb cells ARE the arm.** The ``X``'s counter-clockwise exit
        # lands on ``(cx, in_row - 1)`` and the local row is exactly two north of
        # the spine, so the man was already going to stand on those two cells --
        # shipped he stood on a ``^`` and a ``>`` and did nothing on either, then
        # turned east onto ``N s``. Putting the arm's own two glyphs there instead
        # deletes both cells from the walk, and ``UbW-XNs`` is seven, which is the
        # routed floor for seven ops in a row.
        #
        # The op branch then has to move off the local row, because ``x`` no
        # longer stands on it -- it goes to the row above, where the read's
        # counter-clockwise exit lands **directly on the descent column** and
        # saves the cell the shipped read spent turning north. The write's
        # clockwise exit walks west into ``r s`` and drops onto its own corridor.
        #
        # SPEC 7.1 is unmoved: both outgoing pipes are on the east wall four rows
        # apart, so an ``s`` anywhere on the local row is nearer the local pipe
        # than the downstream one by exactly four, wherever in the row it stands.
        if len(n_arm) != 2 or local_row != in_row - 2:
            raise ValueError(
                f"the climbed mine arm wants a two-glyph arm two rows above the "
                f"spine, not {n_arm!r} at {local_row} under {in_row}"
            )
        xn = cr - 1
        if xn - 3 <= cx:
            raise ValueError(
                f"the climbed mine arm's write tail starts at {xn - 3}, which is "
                f"not east of the spine's X at {cx}"
            )
        put(cx, in_row - 1, n_arm[0])
        put(cx, local_row, n_arm[1])
        put(cx, local_row - 1, ">")   # ... on north, then east to the branch
        put(xn, local_row - 1, "v")
        put(xn, local_row, "x")
        put(xn - 1, local_row, "r")   # write: west into the value pass
        put(xn - 2, local_row, "s")
        put(xn - 3, local_row, "v")
        put(xn - 3, local_row + 1, ">")
    else:
        put(cx, in_row - 1, "^")
        put(cx, local_row, ">")
        text(cx + 1, local_row, n_arm)
        put(xn, local_row, "x")
        put(xn, local_row - 1, ">")  # read: one cell north, then east onto the descent
        text(xn, local_row + 1, ">rs")  # write: one cell south, then pass the value

    # A == 0 goes straight and A > 0 turns south; they merge one column east and
    # one row down, which is the downstream arm's own row.
    elbow = in_row + 1
    if _zero:
        # **``A == 0`` is a whole read, and on a high gate it is always a
        # read.** The constant is ``2(high - m)`` — even — and the wire is
        # ``w = 2a - op``, so ``w == c`` forces ``op == 0``. That word therefore
        # needs no ``x``, no value-passing tail and no share of the hot arm: it
        # gets its own ``s_arm`` on the spine's own row, straight ahead of the
        # ``X``, and walks east onto the return leg from there.
        #
        # What that buys is the **merge cell**. Shipped, ``A == 0`` falls south
        # onto the downstream arm and the two paths join one column east of the
        # ``X``, so the ``A > 0`` man walks ``> >`` before he reaches ``W s``.
        # With the zero word gone the elbow is one ``>`` and the arm starts at
        # ``cx + 1``: ``UbW-X>Ws`` = **8** against nine, on the walk that a
        # request pays once per gate it is *not* addressed to.
        #
        # Low gates keep the merge: there the constant is ``2m + 1``, odd, so
        # ``A == 0`` is always a *write* and does need the tail it would lose.
        put(cx, elbow, ">")
        text(cx + 1, elbow, s_arm)
        text(cx + 1, in_row, s_arm)
    else:
        put(cx + 1, in_row, "v")
        put(cx, elbow, ">")
        put(cx + 1, elbow, ">")
        text(cx + 2, elbow, s_arm)
    put(xs, elbow, "x")
    put(xs, in_row, ">")  # read: north onto the spine's own row, east of it
    text(xs, down_row, ">rs")  # write: south, then pass the value

    # the return leg: every tail walks east onto the same descent, then the
    # floor runs west and the climb re-enters the spine's `U` from below
    for y in range(1, h):
        put(cr, y, "v")
    put(cr, h, "<")
    put(cr - 1, h, "@")
    for x in range(2, cr - 1):
        put(x, h, "<")
    if park_const:
        # Walked **west**, so the digits stand reversed and the west backtick is
        # the one that fires; `M` west of it parks the value for the next lap.
        digits = str(const)[::-1]
        if len(digits) + 3 > cr - 3:
            raise ValueError(f"the floor is too short to reload {const} in a gate of {cr}")
        g[(2, h)] = "M"
        g[(3, h)] = "`"
        for i, ch in enumerate(digits):
            g[(4 + i, h)] = ch
        g[(4 + len(digits), h)] = "`"
    put(1, h, "^")
    for y in range(in_row + 1, h):
        put(1, y, "^")

    # walls — the west and north ones as far out as the caller asked for
    x0, y0 = -west_grow, -north_grow
    ex = width - 1
    for x in range(x0, ex + 1):
        put(x, y0, "+" if x in (x0, ex) else "-")
        put(x, h + 1, "+" if x in (x0, ex) else "-")
    for y in range(y0 + 1, h + 1):
        put(x0, y, "|")
        put(ex, y, "|")
    return g, width


def bank_gate(
    m: int,
    *,
    compact: bool = False,
    high: int | None = None,
    tight_return: bool = False,
    return_slack: int | None = None,
    park_const: bool = False,
    south_reuse_b: bool = False,
    zero_arm: bool = False,
    west_grow: int = 0,
    north_grow: int = 0,
    protocol: str = "v3",
) -> tuple[dict[tuple[int, int], str], int]:
    """One range gate for a bank of ``m`` slots: cells (walls included), width.

    ``protocol="v4"`` builds the one-word gate instead — see
    :func:`_bank_gate_v4`, which is a different body rather than a knob on this
    one. It ignores the width and spacer levers below: they are all answers to
    the two-word body's own shape, and none of them survives it.

    Local coordinates: walls at column 0 / row 0, interior from (1, 1). The
    caller attaches the request pipe to the west wall at the in row and the two
    outgoing pipes to the east wall at the local / downstream rows — all three
    from :func:`gate_rows`, which is also where ``compact`` is described.

    ``west_grow`` / ``north_grow`` move the **west** and **north** walls that
    many cells further out, to ``x = -west_grow`` / ``y = -north_grow``; the art,
    the east wall and the floor do not move by one cell, so the returned width is
    still the art's and the caller places the room at ``gx - west_grow``. Both
    default to 0, so every existing caller's grid is byte-identical.

    The point is that **the request pipe may then attach to a wall the man is
    nowhere near, and the room swallows the distance a pipe would have walked.**
    ``U`` reads from *any* incoming pipe with no distance term, and it turns away
    from the **wall the pipe attaches to**, not from the direction the pipe comes
    from — measured, not argued, by feeding gate 0 through a west wall grown 33
    rows above its man and reading all 600 addresses back correctly
    (``scratch/deadman3d-opt/probe_gate_grow.py``). So a gate can be grown until
    it touches its **caller** — the adapter for gate 0, the previous gate for the
    rest — and the pipe between them stops existing rather than merely shrinking.

    Only those two walls, and that is not squeamishness. The two **outgoing**
    pipes share the east wall and ``s`` picks the *nearest* of them, so the north
    arms must stay closer to the local pipe than to the downstream one; the
    tightest of the eight ``s`` glyphs has three cells of margin. Moving either
    outgoing attachment away from the body — which is what growing the room
    *toward the bank* would need — flips that binding at four rows and routes
    reads into the wrong tape, silently. A room can reach its caller, but not its
    callee.

    ``tight_return`` puts the return column one east of whichever arm actually
    reaches furthest, instead of the shipped flat ``cx + 13``. That constant is
    the *low* gate's longest arm plus two spare columns, and the high gate's plus
    four — and the high gate is the form every hot bank uses. **Every spare
    column is a nop the man walks twice**, once east onto the descent and once
    west along the floor, on every request the gate handles; the room is an
    out-and-back loop, so its cost per access is close to twice its width.
    Measured on ``deadman-3d_hires``: the high gate goes 26 columns wide to 23,
    the low one 27 to 26, and the 3-round tour goes 12,248,581 -> 11,190,732
    ticks, **-8.64%**, with mean store read latency 221.44 -> 184.04. Nothing
    about the gate's *logic* moves — same glyphs, same rows, same arms.

    That is a much larger return than 6 walked cells, and the reason is that this
    store is a tandem queue of single-server rooms at ~20% utilisation each: what
    a read waits for is not congestion but the **write in front of it** clearing
    each room in turn, so a tick off a room's service time is taken off the
    critical path more than once. It is the same reason the ring's rotation costs
    nothing here — a 6-slot ring in a 154-cell pipe would stall for ~148 ticks a
    lap if laps were back to back, and it stalls for 6.3 (measured, ``hist_pipe``
    on the ring's return leg) because the bank is idle 86% of the time.

    ``False`` keeps the shipped width, so ``deadman-3d``'s checked-in
    ``deadman-3d_taped.man`` stays byte-identical.

    ``return_slack`` is the same move with the **east wall left where it was**:
    the return column stands ``return_slack`` columns east of the longest arm
    and the returned width is still ``max(cr, cx + 13) + 2``, so a caller that
    lays its banks out from ``gate_w`` sees no change at all. That distinction
    is the whole reason it exists. ``tight_return`` moves the wall *with* the
    column — the two are the same column in that form — and on a shallow chain
    that measured **+0.892%**, because ``taped_store_block``'s ``bx`` comes off
    the **max** gate width, so pulling the (narrower) high gates in three
    columns moved the bank row only one and grew every high gate's feed pipe by
    two cells *on the critical path*. Here the wall does not move, the feed pipe
    does not grow, and the six walked cells (three east onto the descent, three
    west along the floor) come off the room's **service time** for nothing.

    That is worth having even though none of it is on the critical path — it is
    all after the arm's ``s`` has sent. The store is a tandem queue and what a
    read waits for is the write in front of it clearing each room in turn, so
    occupancy is what the queueing term is made of.

    ``None`` keeps ``cx + 13``, so every existing caller's grid is
    byte-identical; ``tight_return`` wins if both are given.

    The two outgoing pipes' ``s`` bindings cannot notice either way: both attach
    to the **same** east wall, so ``cr`` enters every one of the ten distances as
    the same column term and cancels — which is the module docstring's §7.1
    argument, and ``test_every_gate_send_still_binds_to_the_pipe_it_means``
    re-checks it at whatever width this returns.

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
    if protocol not in TAPE_PROTOCOLS:
        raise ValueError(f"unknown store protocol {protocol!r}; expected {TAPE_PROTOCOLS!r}")
    if protocol in _PACKED:
        return _bank_gate_v4(
            m,
            high=high,
            park_const=park_const,
            zero_arm=zero_arm,
            west_grow=west_grow,
            north_grow=north_grow,
        )
    if m < 1:
        raise ValueError(f"a bank must hold at least one slot, not {m}")
    if high is not None and high - m < 1:
        raise ValueError(f"a high gate over 1..{high} cannot hand {high - m} addresses on")
    if west_grow < 0 or north_grow < 0:
        raise ValueError(f"a gate room grows outward, not inward: {(west_grow, north_grow)}")
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
    const = m + 1 if high is None else high - m
    if high is None:
        spine = "Ubr-X" if park_const else f"UbrM`{m + 1}`W-X"
        n_read_arm, n_write_arm = "+M0sWs", "+M1sWsrs"  # restore addr
        s_read_arm, s_write_arm = "M1+M0sWs", "M1+M1sWsrs"  # rebase to addr - m
    elif park_const:
        # ``W`` reaches exactly the state ``M `const``` did — A the constant, B
        # the address — in one glyph, because B already holds the constant. So
        # all four arms are the shipped ones, including the two south arms that
        # find ``addr`` in B. (``-N`` also reaches the right A, but leaves the
        # constant in B and costs those two arms two cells each to rebuild it.)
        spine = "UbrW-X"
        n_read_arm, n_write_arm = "NM0sWs", "NM1sWsrs"  # negate to addr - (high-m)
        s_read_arm, s_write_arm = "WM0sWs", "WM1sWsrs"  # addr is already in B
        if south_reuse_b:
            # The comment above is truer than the glyphs: the address really is
            # already in B on arrival, so the arm's leading ``W`` (fetch it into
            # A) and the ``M`` behind it (park it straight back into B, over the
            # op digit) are a round trip to nowhere. Drop both and let the digit
            # land on A while B is left holding the address::
            #
            #     WM0sWs   A=addr B=c-a | B=addr | A=0 | send | A=addr | send
            #       0sWs                | A=0 B=addr    | send | A=addr | send
            #
            # Same two words in the same order out of the same pipe — moving an
            # ``s`` **west** along its own row shifts its distance to *both*
            # source cells by the same amount, so §7.1's nearest-pipe tie cannot
            # flip (``test_every_gate_send_still_binds_to_the_pipe_it_means``).
            # Two cells off the **forward** leg of both forwarding arms.
            s_read_arm, s_write_arm = "0sWs", "1sWsrs"
    else:
        spine = f"UbrM`{high - m}`-X"
        n_read_arm, n_write_arm = "NM0sWs", "NM1sWsrs"  # negate to addr - (high-m)
        s_read_arm, s_write_arm = "WM0sWs", "WM1sWsrs"  # addr is already in B
    cx = len(spine)  # the range test's X (the spine starts at column 1)
    # The return column: ``cx + 13`` shipped, or one east of whichever arm
    # actually reaches furthest (the north pair starts at ``cx + 1``, the south
    # pair at ``cx + 2``).
    cr_tight = 1 + max(
        cx + len(n_read_arm),
        cx + len(n_write_arm),
        cx + 1 + len(s_read_arm),
        cx + 1 + len(s_write_arm),
    )
    if tight_return:
        cr = cr_tight
    elif return_slack is not None:
        if return_slack < 0:
            raise ValueError(f"return_slack stands east of the longest arm: {return_slack}")
        cr = cr_tight + return_slack
    else:
        cr = cx + 13  # the longest arm plus slack
    # The east wall follows the return column only in the ``tight_return`` form;
    # ``return_slack`` holds it at the shipped ``cx + 15`` so ``gate_w`` — and
    # with it every bank's column — cannot move.
    width = cr + 2 if tight_return else max(cr, cx + 13) + 2
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
    if park_const:
        # The reload, walked **west**, so the digits stand reversed: the engine
        # pairs a row's backticks once and hands the closing mark the forward
        # value and the opening mark the reversed one, keyed by the direction the
        # man arrives from. He meets the east mark first (a nop westbound), then
        # each digit as a bare one-digit literal, and the west mark finally puts
        # the whole number in A; ``M`` west of it parks it in B for the next lap.
        digits = str(const)[::-1]
        if len(digits) + 3 > cr - 3:
            raise ValueError(f"the floor is too short to reload {const} in a gate of {cr}")
        g[(2, h)] = "M"
        g[(3, h)] = "`"
        for i, ch in enumerate(digits):
            g[(4 + i, h)] = ch
        g[(4 + len(digits), h)] = "`"
    put(1, h, "^")
    for y in range(in_row + 1, h):
        put(1, y, "^")

    # walls — the west and north ones as far out as the caller asked for
    x0, y0 = -west_grow, -north_grow
    ex = width - 1  # the east wall's column; ``cr + 1`` unless slack pinned it
    for x in range(x0, ex + 1):
        put(x, y0, "+" if x in (x0, ex) else "-")
        put(x, h + 1, "+" if x in (x0, ex) else "-")
    for y in range(y0 + 1, h + 1):
        put(x0, y, "|")
        put(ex, y, "|")
    return g, width


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
    skip_batch: int | None = 1,
    jump_threshold: int = 128,
    answer_west: int | None = None,
    answer_exit_west: bool = False,
    compact_gate: bool = False,
    tight_gate: bool = False,
    gate_return_slack: int | None = None,
    gate_park_const: bool = False,
    gate_south_reuse_b: bool = False,
    gate_zero_arm: bool = False,
    broadcast: bool = False,
    tape_park_const: bool = False,
    tape_tight_ring: bool = False,
    order: tuple[int, ...] | None = None,
    chain_reach: bool = False,
    chain_pad: int = 0,
    request_roof: int | None = None,
    request_tuck: bool = False,
    feed_teleport: bool = False,
    feed_share_riser: bool = False,
    bank_lift: int = 0,
    feed_tuck: int = 0,
    bank_west_grow: int = 0,
    rotate_banks: tuple[int, ...] | frozenset[int] = (),
    collector_fast: bool = False,
    protocol: str = "v3",
) -> V3Store:
    """The banked-tape store as a placeable block, in men-v3's clothes.

    ``n`` is the machine's ``TAPE_SIZE`` (slot count; usable addresses
    ``1..n-1``). ``banks`` is the man knob: the block employs ``2*banks``
    tape men plus ``banks-1`` gate men, against the man-memory's two per slot.
    Returns the same :class:`V3Store` contract the men-v3 blocks use — request
    stub west, answer stub rising out of the top, exact pipe inventory — so
    ``lm1.machine`` places it through the identical branch, teleports and all.

    ``skip_batch`` picks the ring worker, and ``None`` picks **one per bank**:
    :func:`~.lm1.machine.tape_block` then takes batch 2 for a bank of at least
    ``jump_threshold`` slots and batch 1 below it. A block whose banks differ by
    two orders of magnitude in size wants exactly that, because the two workers
    trade a fixed cost against a per-slot one and the shipped cut straddles the
    crossover. Measured on the `deadman-3d_hires` 3-round tour, focusing the
    opcode profiler on one bank's worker room (he is the only man in it, so his
    non-blocked ticks are that bank's service time exactly) and dividing by the
    lap count its ring pipe reports::

        bank slots   6      7      9     21        fit
        batch 1    131.7  140.3  157.5  260.8   ~ 80 + 8.6 * slots
        batch 2    156.9  160.0  169.6  244.3   ~122 + 5.8 * slots

    The crossover is **~15 slots** and it is a property of the two workers, not
    of any one cut, which is why this is a threshold rather than a per-bank list.
    Batching pays for a long ring and costs 42 ticks of setup on a short one; the
    shipped hires cut has four banks of 6..9 slots, which is where the hot
    addresses are deliberately put (:data:`~.lm1.machine.TAPED_BANK_ORDER` — hot
    traffic goes in *small* rings), and they were paying the setup on every
    access. Nothing moves in the floor plan —
    the batch-1 block is *narrower* (33 columns against 45) and no taller, and
    ``bank_w``/``bank_h`` are maxima over the banks, so the big rings still set
    the pitch and the block's own dimensions do not change.

    ``answer_west`` moves the **answer collector's west wall** to that interior
    column and turns its exit stub from a north riser into a south one. The
    collector is already a teleport — ``R`` has no distance term, so widening it
    is free — and the block's whole west end is empty for these rows, so pulling
    the wall west carries the answer to the caller's doorstep for nothing. It is
    what lets ``lm1.machine`` drop its own two forwarding rooms: with the answer
    already beside the CPU, a stub pipe finishes the job. ``None`` keeps the
    shipped north riser, so every existing caller's grid is byte-identical.

    ``answer_exit_west`` turns that exit stub through ninety degrees again, out
    of the collector's **west wall** on its first interior row, and is what a
    caller whose response row lands *inside* the collector's own row band needs.
    South only works while the caller is below the room: a caller level with it
    would have its riser climb back around the outside of the room it just left,
    past the adapter, to reach a cell one column from where it started. Leaving
    west instead makes the stub a single cell — and that cell can be the
    caller's own response attachment, so the response pipe is not merely short,
    it is the one cell the plain pipe already ended on and no ``r`` binding
    moves at all. Needs ``answer_west >= 2`` (the stub owns the column west of
    the wall) and is ignored without ``answer_west``.

    ``compact_gate`` builds the gates from :data:`COMPACT_GATE_H`'s spacer-free
    body instead of the shipped 12-row one — five fewer rows per gate and a
    shorter walk through every one of them. The gate strip is the block's floor,
    so the block loses those five rows too. ``False`` keeps the shipped body, so
    every existing caller's grid is byte-identical.

    ``gate_return_slack`` is :func:`bank_gate`'s ``return_slack`` for every gate
    in the chain — the return column moves in while the east wall, and therefore
    ``gate_w``, ``bx`` and every bank's column, stay exactly where they are. It
    is the form of the tightening that costs no critical-path pipe cells; see
    :func:`bank_gate`. ``None`` keeps the shipped column.

    ``tight_gate`` is :func:`bank_gate`'s ``tight_return`` for every gate in the
    chain: the return column moves in to whichever arm actually reaches
    furthest, which is three columns on the high-end form the hot banks all use.
    ``False`` keeps the shipped width, so every existing caller's grid is
    byte-identical.

    ``order`` is the **chain** order over ``banks``' address-order sizes — see
    :func:`gate_chain`, which also says which permutations exist. ``None`` is
    address order, so every existing caller's grid is byte-identical. The banks
    are then laid out west to east in chain order, which is what keeps the floor
    plan (and the block's dimensions) exactly as they were.

    ``chain_reach`` grows every gate but the first **west** until its wall stands
    beside the previous gate's, so the request stops walking the chain link and
    crosses a room instead (:func:`bank_gate` says why that is legal and why it
    is only ever west/north). The link is a hop over the gap the previous bank's
    feed riser needs and no more: **25 cells become 7**, on every access bound
    for a bank at chain position ``k`` or later — and DOOM's traffic is lopsided
    enough that the first link alone carries 68% of the reads. Nothing else
    moves: ``pitch``, ``gx``, ``bx`` and therefore the block's own width are
    computed from the *un*grown gates, so the banks are where they were.

    ``chain_pad`` leaves every grown gate that many columns short of its caller,
    so each chain link is exactly that many cells longer and **nothing else in
    the grid moves**. It is an instrument, not a knob: it is how the leg's tick
    derivative gets measured on a real machine rather than argued from pipe
    lengths (``resp_pad`` in ``lm1.machine`` exists for the same reason, and this
    family has already been burned twice by the difference — see AGENTS.md's
    three measurement traps).

    ``request_roof`` is the block-local row the **first** gate's roof is pulled
    up to, so its west wall reaches the caller — for ``lm1.machine`` that is the
    adapter's floor, and it deletes the whole request forwarder that used to
    bridge the corridor. The block's request stub is then a single ``>`` on the
    west wall at ``request_roof + 2`` (its ``in_cell``) rather than a two-cell
    run at the gate strip's entry row, because the caller arrives from *above*.
    ``None`` keeps the shipped stub, so every existing caller's grid is
    byte-identical.

    ``feed_teleport`` puts a **vertical forwarder** on every ``reqK->bankK`` arm,
    in the corridor between the banks. Those arms are 45/45/44/97 cells and every
    access walks one, which makes them the block's largest remaining term — but
    they are the one leg a grown gate room cannot take, because they run to the
    gate's *callee*: the two outgoing pipes share the east wall and ``s`` takes
    the nearest, so moving the local attachment more than four rows off the body
    binds the north arms to the downstream pipe and answers reads from the wrong
    bank in silence (:func:`bank_gate`). A separate one-in/one-out room has no
    such constraint, and it is the same lever ``lm1.machine`` already pulled on
    the answer and request legs.

    The price is a man per bank and two columns of pitch: the room is
    ``memory_men.teleport_v``'s six cells in a 6-wide corridor, and the shipped
    pitch leaves four. Everything else is where it was — the room hangs entirely
    between bank ``k-1``'s east edge and bank ``k``'s empty first column, above
    the gate strip and below the bank's own request stub row.

    ``feed_share_riser`` consolidates the **two riser columns** every corridor
    between a gate and its successor carries into one. The corridor holds the
    feed's climb into the forwarder room (rows: the gate's local row up to the
    room's floor) and the chain link's climb from the downstream row back to the
    spine row; they are in disjoint rows and were only in different columns
    because the feed took the forwarder's *second* interior column. Taking the
    first one instead puts both climbs in the same column and hands the next one
    back to ``chain_reach``, so **every feed pipe and every chain link loses one
    cell** — the block's width, pitch and bank columns do not move at all, and
    the only room that changes shape is each grown gate, one column wider west.
    It needs ``feed_teleport`` (there is no forwarder room otherwise) and it
    needs the first column to be free, which is what ``lead`` leaves; the feed
    raises rather than silently mis-drawing when it is not.

    ``bank_lift`` raises the whole bank row (and, with it, the gate strip and the
    block's own height) that many rows toward the answer collector, which is the
    one thing between them: every bank's answer climbs a riser from its own ``^``
    stub to the collector's south wall, and the riser is pure transit that ~87k
    reads a frame each pay in full. Five is the ceiling and it is exact — a tape
    block carries a two-cell ``^`` stub above its own roof, so at ``bank_lift=5``
    that stub *is* the riser, standing directly on the collector's floor, and the
    connecting pipe draws nothing at all. Nothing else in the block is measured
    from the bank row — ``COLLECTOR_ROW`` is a constant and the request port comes
    off ``request_roof`` — so the geometry ``lm1.machine`` selects
    ``answer_exit_west`` on does not move, which a ``store_offset`` dy would.

    ``feed_tuck`` slides every bank that many columns west of where the pitch
    would put it, so the feed room's east wall stands *inside* the bank's own
    west margin and the ``reqK->bankK`` stub shortens by the same amount — and
    because the pitch shrinks with it, the block loses ``feed_tuck`` columns per
    bank. It is 0 on this machine and the reason is exact rather than cautious:
    a tape block's west margin is only empty in its **first** column. Columns
    1..6 carry the ring's relay room (``lm1.machine.tape_block`` stamps it at
    ``x = 1``), five rows of it, and those rows fall inside the corridor the feed
    room has to span, so the room's east wall crosses the relay's own walls at
    any tuck at all. See :data:`~.lm1.machine.TAPED_FEED_TUCK` for the collision
    cell and for why raising the room's floor above the relay instead trades four
    cells for thirteen.

    ``bank_west_grow`` takes **the same cells off the same stub from the other
    end**: rather than carry the feed room east into the bank's margin, it carries
    the bank's own west *wall* west to meet it
    (:func:`~.lm1.machine.tape_block`). What ``feed_tuck`` dies on is a **row**
    collision — the feed room spans the whole corridor vertically, so it meets the
    relay at block rows 29..33 — and the worker room is only rows 7..26, so this
    wall crosses nothing at all on its way west. At 4 the whole ``reqK->bankK``
    leg is the block's own two-cell stub and the pipe drawn below is empty.

    ``rotate_banks`` names banks — **in address order**, the index into
    ``banks`` — whose ring worker skips the *rotational delta* instead of the
    address (``memory_tape.worker_v2_rot``) and whose feed forwarder therefore
    carries that ring's head in its off hand (:func:`feed_rotate`). Empty is the
    shipped grid to the cell; on, nothing in the floor plan moves at all,
    because both rotating bodies stand in the same rooms with the same ports as
    the bodies they replace — the block's box, its man census and its pipe
    inventory are identical either way.

    It is a per-bank list rather than a flag because the ring turns **one way**:
    a delta that runs backwards costs a near-full lap where the address-skip
    cost only the address, so a bank whose traffic walks backwards often on a
    ring that was short anyway loses outright, and applying it to every bank of
    ``deadman-3d_hires`` measures **+5.3%** against **-1.8%** for the four that
    win (:data:`~.lm1.machine.TAPED_ROTATE_BANKS` has the per-bank table).

    Three things are refused rather than built: a bank that resolves to the
    **batch-1** worker (there is no narrow rotating body), a block without
    ``feed_teleport`` (the head has nowhere to live) and a two-word ``protocol``
    (the head arithmetic rides the packed wire).

    ``collector_fast`` gives the answer collector :func:`~.memory_men.teleport`'s
    **latency** art instead of its shortest lap — see
    :data:`TAPED_COLLECTOR_FAST`. ``False`` is the shipped grid to the cell, which
    is what holds ``deadman-3d``'s checked-in store byte-identical.

    ``protocol`` picks the block's **wire format**. ``v3`` is the two-word
    request every shipped grid was built on. ``v4`` carries the op in the
    address's low bit and sends one word — ``2*addr - op`` — from the block's
    own request stub all the way to each bank's feed forwarder, which is where
    it is taken apart again (:func:`feed_unpack`). The banks therefore keep the
    protocol they were verified on and ``memory_tape`` does not move by one
    cell; what changes is that the adapter, every gate in the chain and every
    feed room handle **one fewer pipe transaction per access**. It needs
    ``feed_teleport`` — the unpack lives in that room — and it is not
    byte-compatible with anything, which is the point of giving it a number.
    """
    from .lm1.machine import tape_block

    if protocol not in TAPE_PROTOCOLS:
        raise ValueError(f"unknown store protocol {protocol!r}; expected {TAPE_PROTOCOLS!r}")
    if protocol in _PACKED and not feed_teleport:
        raise ValueError(
            "the v4 wire is unpacked in the feed forwarder, so there has to be "
            "one: pass feed_teleport=True"
        )
    _gate_h, gate_in_row, gate_local_row, gate_down_row = gate_rows(compact_gate, protocol)
    plan = taped_plan(n, banks)
    chain = gate_chain(plan, order)
    sizes = [plan[k] for k, _ in chain]
    # ``rotate_banks`` names banks in **address order** (the index into ``plan``,
    # which is what ``lm1.machine.TAPED_ROTATE_BANKS`` and the trace both key on);
    # everything below runs in chain order, so resolve it once here.
    rot = frozenset(rotate_banks)
    if rot - set(range(len(plan))):
        raise ValueError(
            f"rotate_banks {sorted(rot)} names a bank outside 0..{len(plan) - 1}"
        )
    rot_at = [k in rot for k, _ in chain]
    if rot:
        from .lm1.machine import _resolve_tape_skip_batch

        if protocol not in _PACKED:
            raise ValueError(
                "the rotating bank's head rides the packed wire's own forwarder; "
                f"protocol {protocol!r} has none"
            )
        if not feed_teleport:
            raise ValueError(
                "a rotating bank keeps its ring head in the feed forwarder's B "
                "register, so there has to be one: pass feed_teleport=True"
            )
        for j, size in enumerate(sizes):
            if rot_at[j] and _resolve_tape_skip_batch(
                size + 1, skip_batch, jump_threshold
            ) != 2:
                raise ValueError(
                    f"bank {chain[j][0]} resolves to the batch-1 worker at "
                    f"{size + 1} slots; there is no rotating narrow body"
                )
    tapes = [
        tape_block(
            size + 1,
            skip_batch=skip_batch,
            jump_threshold=jump_threshold,
            park_const=tape_park_const,
            tight_ring=tape_tight_ring,
            west_grow=bank_west_grow,
            protocol="v4" if protocol == "v5" else "v3",
            rotate=rot_at[j],
        )
        for j, size in enumerate(sizes)
    ]
    bank_w = max(t.width for t in tapes)
    bank_h = max(t.height for t in tapes)
    if broadcast:
        if protocol not in _PACKED:
            raise ValueError(
                f"the broadcast store speaks the one-word wire only, not {protocol!r}"
            )
        if not feed_teleport:
            raise ValueError(
                "a filter hands the bank its rebased word through the forwarder; "
                "pass feed_teleport=True"
            )
        if chain_reach or chain_pad or request_roof is not None:
            raise ValueError(
                "chain_reach / chain_pad / request_roof all measure a chain, and "
                "a broadcast store has none"
            )
        if order is not None:
            raise ValueError(
                "a broadcast store has no chain position, so no order to pick: "
                "every filter tests its own range and nothing is ahead of anything"
            )
        # ``feed_share_riser`` puts the feed climb in the same column as the
        # **chain link** out of the same gate, which is free only because the two
        # never meet. There is no chain link to share with here, so the column it
        # saves buys nothing and the filter's own east wall needs it back.
        feed_share_riser = False
        # Address order, because a filter's range is its own and the layout is
        # what decides how far each answer has to climb -- not who is asked first.
        bases, acc = [], 0
        for m in sizes:
            bases.append(acc)
            acc += m
        raw = [filter_room(b, m).rows() for b, m in zip(bases, sizes, strict=True)]
        # Every filter is padded to the same box, because `_room` puts its walls
        # at the art's own extent and the strip's risers and feeds are drawn off
        # one set of columns for all eleven.
        filter_h = max(len(f) for f in raw)
        filter_w = max(len(r) for f in raw for r in f)
        filters = [
            [r.ljust(filter_w) for r in f] + [" " * filter_w] * (filter_h - len(f))
            for f in raw
        ]
        gate_w = filter_w + 2  # ... plus its two walls
        gates = []
    else:
        gates = [
            bank_gate(
                plan[k],
                compact=compact_gate,
                tight_return=tight_gate,
                return_slack=gate_return_slack,
                park_const=gate_park_const,
                south_reuse_b=gate_south_reuse_b,
                zero_arm=gate_zero_arm,
                high=top,
                protocol=protocol,
            )
            for k, top in chain[:-1]
        ]
        gate_w = max(w for _, w in gates)
        filters, filter_h = [], 0

    # ── the floor plan ───────────────────────────────────────────────────────
    # Banks in one row on top, the gate strip below; bank k sits a half pitch
    # east of gate k so each feed riser climbs the clear column between banks.
    nb = len(sizes)
    # The corridor between two banks is ``pitch - bank_w + 1`` columns wide (a
    # tape block's own first column is empty). Three spare is what the riser
    # needed; a forwarder room wants six.
    nb_gap = 5 if feed_teleport else 3
    if feed_share_riser and not feed_teleport:
        raise ValueError(
            "feed_share_riser moves the forwarder room's own climb; feed_teleport is off"
        )
    _riser_off = 1 if feed_share_riser else 2
    if feed_tuck and not feed_teleport:
        raise ValueError("feed_tuck tucks the feed room into the bank; feed_teleport is off")
    if feed_tuck + bank_west_grow > 4:
        raise ValueError(
            f"feed_tuck {feed_tuck} and bank_west_grow {bank_west_grow} shorten the "
            f"same stub from opposite ends and would meet inside it"
        )
    if not 0 <= feed_tuck <= nb_gap - 1:
        raise ValueError(
            f"feed_tuck {feed_tuck} is not in 0..{nb_gap - 1}: the feed room is six "
            f"columns and its west wall already shares the gate's east one"
        )
    pitch = max(bank_w + nb_gap - feed_tuck, gate_w + 8)
    coll_y = COLLECTOR_ROW  # collector interior rows 6..7, walls 5 and 8
    # The banks' own row, and how close to the collector it may come: the riser
    # runs from the tape's `^` stub down to the collector's south wall, so the
    # lift is spent when the stub lands on that wall and the pipe is all stub.
    lift_max = 9 - (coll_y + 4 - min(t.out_cell[1] for t in tapes))
    if not 0 <= bank_lift <= lift_max:
        raise ValueError(
            f"bank_lift {bank_lift} is not in 0..{lift_max}: at {lift_max} the "
            f"bank's own answer stub already ends on the collector's south wall"
        )
    bank_y = 9 - bank_lift
    gate_y = bank_y + bank_h + 2  # one clear row under the banks
    # A broadcast room spans the strip, so it cannot grow north to meet its
    # caller the way `request_roof` grows gate 0 -- the bank row is in the
    # way. The descender does it instead, and it needs its own columns.
    x0 = max(4, DESC_X + DESC_W + 4) if broadcast else 4
    gx = [x0 + k * pitch for k in range(nb if broadcast else nb - 1)]
    # The feed room's west wall stands at ``bx[k] - 5 + feed_tuck``, which is the
    # column the **widest** gate's east wall already occupies — harmless for every
    # gate but the first, because the feed rooms live entirely above the gate
    # strip and only gate 0 grows north into their rows (``request_roof``). So the
    # first gate must be strictly narrower than the widest one, and when it is not
    # the bank row steps one column east rather than the corner being overdrawn.
    # ``max(0, ...)`` is what keeps every shipped grid byte-identical: on the
    # shipped chain gate 0 is 26 columns against a 27-column maximum.
    lead = (
        max(0, gates[0][1] - (gate_w - 1))
        if (request_roof is not None and not broadcast)
        else 0
    )
    bx = [x0 + gate_w + 4 + lead - feed_tuck + k * pitch for k in range(nb)]

    # ── how far each gate room reaches back toward its caller ────────────────
    # West wall of gate k lands one column east of bank k-1's own feed riser
    # (``bx[k-1] - 2``, which runs from the strip up to the bank and is the only
    # thing in that corridor), so the link is the riser hop and nothing more.
    # Gate 0's caller is outside the block, so it grows north instead.
    west_grow = [0] * (nb - 1)
    north_grow = [0] * (nb - 1)
    if chain_pad < 0:
        raise ValueError(f"chain_pad lengthens a link, it cannot shorten one: {chain_pad}")
    if chain_pad and not chain_reach:
        raise ValueError("chain_pad measures how far the gates reach; chain_reach is off")
    if chain_reach:
        for k in range(1, nb - 1):
            # ... one column east of whatever the previous bank's feed put in the
            # corridor: the riser itself, or the forwarder's own entry stub, and
            # the chain link's own climb, which is always two east of the wall
            # it leaves. ``feed_share_riser`` is exactly the case where the first
            # two coincide and the third is what the reach then stops against.
            if feed_teleport:
                corridor = bx[k - 1] - 5 + feed_tuck + _riser_off
            else:
                corridor = bx[k - 1] - 2
            corridor = max(corridor, gx[k - 1] + gates[k - 1][1] + 1)
            west_grow[k] = gx[k] - (corridor + 1) - chain_pad
            if west_grow[k] < 0:
                raise ValueError(
                    f"gate {k} cannot reach bank {k - 1}'s riser with chain_pad={chain_pad}"
                )
    if request_roof is not None:
        if not 0 <= request_roof < gate_y:
            raise ValueError(f"request roof {request_roof} is not above the gate strip")
        north_grow[0] = gate_y - request_roof
    if any(west_grow) or any(north_grow):
        gates = [
            bank_gate(
                plan[k],
                compact=compact_gate,
                tight_return=tight_gate,
                return_slack=gate_return_slack,
                park_const=gate_park_const,
                south_reuse_b=gate_south_reuse_b,
                zero_arm=gate_zero_arm,
                high=top,
                protocol=protocol,
                west_grow=west_grow[j],
                north_grow=north_grow[j],
            )
            for j, (k, top) in enumerate(chain[:-1])
        ]

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

    for k in range(nb - 1) if not broadcast else ():
        blit(gx[k], gate_y, gates[k][0])
    for k, t in enumerate(tapes):
        blit(bx[k], bank_y, t.cells)

    # the room facade memory_men._room draws through (also used by the collector)
    from .memory_men import _room, teleport, teleport_v

    class _Grid:
        def set(self, x: int, y: int, ch: str) -> None:
            if ch != " ":
                put(x, y, ch)

    def feed(source: tuple[int, int], k: int) -> None:
        """Carry a request from ``source`` (a gate's own east-wall cell) to bank
        ``k``'s stub — down the corridor as a pipe, or across a room."""
        tin = (bx[k] + tapes[k].in_cell[0], bank_y + tapes[k].in_cell[1])
        if not feed_teleport:
            riser = bx[k] - 2  # the clear column west of bank k
            # end ON the bank's own first stub cell, so the joining cell is drawn
            pipe([source, (riser, source[1]), (riser, tin[1]), tin])
            return
        # The room fills the corridor from just above the bank's stub row down to
        # just above the gate strip, and is crossed in one instruction: ``R``
        # takes from any incoming pipe with no distance term, and with one pipe
        # each way neither it nor ``s`` has anything to choose between. What is
        # left is the stub off the gate and the stub into the bank.
        # ... and under ``v4`` the same room is where the packed word is taken
        # apart, so the bank's own two-word protocol never changes.
        rx0, ry0, ry1 = bx[k] - 5 + feed_tuck, tin[1] - 1, gate_y - 1
        art = {"v4": feed_unpack, "v5": feed_relay}.get(protocol, teleport_v)
        floor = {
            "v4": V4_FEED_H,
            "v5": V5_FEED_H + RELAY_PRE_PAD + RELAY_POST_PAD,
        }.get(protocol, 2)
        if rot_at[k]:
            # This bank's ring head lives in *this* man's off hand; the room and
            # its two ports are otherwise identical (:func:`feed_rotate`).
            art, floor = feed_rotate, V5_ROT_FEED_H
        if ry1 - ry0 - 1 < floor:
            raise ValueError(
                f"bank {k}'s feed corridor is {ry1 - ry0 - 1} rows and the "
                f"{protocol} forwarder needs {floor}"
            )
        _room(_Grid(), rx0 + 1, ry0 + 1, art(ry1 - ry0 - 1)[0])
        # The climb uses the room's *second* interior column, not its first: a
        # pipe must leave the gate heading east (SPEC.md — the first arrowhead's
        # backward cell is the source room's border), and the widest gate's own
        # east wall already sits against the first one.
        #
        # ``feed_share_riser`` takes the first column instead, which is legal
        # exactly when the gate's east wall stops short of the room's west wall —
        # ``lead`` is what leaves that column free — and which then puts this
        # climb in the **same** column as the chain link out of the same gate.
        # They never meet: this one runs from the gate's local row (2) up to the
        # room's floor at ``gate_y - 1``, and the link runs from the downstream
        # row (6) up to the spine row (4), so the whole of row 3 stands between
        # them. What it buys is the column east of it, which the next gate then
        # reaches one further west into (see ``chain_reach`` above).
        riser_x = rx0 + _riser_off
        if source[0] >= riser_x:
            raise ValueError(
                f"bank {k}'s feed leaves the gate at column {source[0]} and cannot "
                f"turn north at {riser_x}: a pipe's first cell must head east"
            )
        pipe([source, (riser_x, source[1]), (riser_x, ry1)])
        pipe([(bx[k] + feed_tuck + 1, tin[1]), tin])

    # ── the broadcast strip: one room shouting into nb filters, each its own ─
    if broadcast:
        # The filters stand where the gates stood, and the room that feeds them
        # spans the whole strip so every riser off it is the two cells a pipe is
        # required to be. `S` is what allows that (see `bcast_room`).
        for k, art in enumerate(filters):
            _room(_Grid(), gx[k] + 1, gate_y + 1, art)
        f_south = gate_y + filter_h + 1          # the filters' shared south wall
        bcy = f_south + 3                        # ... the broadcast room's north
        span = gx[-1] + gate_w - gx[0] - 2
        _room(_Grid(), gx[0] + 1, bcy + 1, bcast_room(span))
        for k in range(nb):
            # straight up out of the roof, into the filter directly above
            pipe([(gx[k] + 3, bcy - 1), (gx[k] + 3, f_south)])
            east = gx[k] + gate_w - 1            # this filter's east wall
            feed((east + 1, gate_y + FILTER_IN_ROW + 3), k)
        # One cell, like the roofed chain's: whatever hands the request over has
        # to descend to this row anyway, and `R` reads it from any wall.
        # **The descender: `feed_relay` used as nothing but a lift.** `R` takes
        # from any incoming pipe with no distance term, so a room whose north
        # wall is up beside the caller and whose south wall is down beside the
        # broadcast room crosses every row between them in ONE instruction. The
        # 26 rows the request would otherwise walk as pipe -- and pipe is a
        # shift register, one tick a cell -- become an `R` and an `s`.
        # ... starting clear of the answer collector, whose west end
        # `answer_west` may have pulled into these very columns.
        dtop = max(coll_y + 5, bank_y - 2) + DESC_TOP
        dh = bcy + 2 - dtop
        _room(_Grid(), DESC_X + 1, dtop, feed_relay(dh)[0])
        pipe([(DESC_X + DESC_W + 2, bcy + 1), (gx[0], bcy + 1)])
        in_y = dtop + 1
        put(DESC_X - 1, in_y, ">")
        in_cell = (DESC_X - 1, in_y)

    # ── feeds: gate k's local arm into bank k, its downstream into gate k+1 ──
    for k in range(nb - 1) if not broadcast else ():
        east = gx[k] + gates[k][1] - 1  # this gate's east wall column
        feed((east + 1, gate_y + gate_local_row), k)
        down_y = gate_y + gate_down_row
        if k + 1 < nb - 1:
            # chain: east two cells, up to the next gate's entry row, straight in
            pipe(
                [
                    (east + 1, down_y),
                    (east + 2, down_y),
                    (east + 2, gate_y + gate_in_row),
                    (gx[k + 1] - west_grow[k + 1], gate_y + gate_in_row),
                ]
            )
        else:
            # the last gate's downstream IS the last bank's wire, rebased: it
            # runs east under the empty last-gate slot and takes the same feed
            feed((east + 1, down_y), nb - 1)

    # ── answers: every bank rises into one collector teleport ────────────────
    coll_x0 = bx[0] + tapes[0].out_cell[0] - 2
    coll_x1 = bx[-1] + tapes[-1].out_cell[0] + 2
    if answer_west is not None:
        if not 1 <= answer_west <= coll_x0:
            raise ValueError(f"answer_west {answer_west} is not west of the collector")
        if answer_exit_west and answer_west < 2:
            raise ValueError(
                f"a west exit stub owns the column outside the wall, so the "
                f"collector's interior cannot start at column {answer_west}"
            )
        coll_x0 = answer_west
    coll_rows, _ = teleport(coll_x1 - coll_x0 + 1, fast=collector_fast)
    _room(_Grid(), coll_x0, coll_y + 1, coll_rows)
    for k, t in enumerate(tapes):
        ax = bx[k] + t.out_cell[0]
        # extend the bank's own `^` stub up to the collector's south wall
        pipe([(ax, bank_y + t.out_cell[1] - 1), (ax, coll_y + 3)])
    out_x = coll_x0 + 2
    if answer_west is None:
        pipe([(out_x, coll_y - 1), (out_x, 0)])
    elif answer_exit_west:
        # West instead of south: the caller's response row *is* this room's own
        # first interior row, so there is nothing to climb and nothing to walk
        # back — the answer leaves through the west wall and the single cell
        # beyond it is already the caller's attachment. The four bank answers
        # attach to the south wall further east and `s` still has exactly one
        # outgoing pipe to choose from.
        put(coll_x0 - 2, coll_y + 1, "<")
    else:
        # South instead of north: the collector's west end is now beside the
        # caller's response row, which is *below* it, so the riser would only
        # climb to be walked back down. One cell clear of the south wall is
        # enough — the four bank answers attach to the same wall further east
        # and `s` has one outgoing pipe to choose from either way.
        pipe([(out_x, coll_y + 4), (out_x, coll_y + 5)])

    # ── the block's own ports ────────────────────────────────────────────────
    if broadcast:
        pass  # the broadcast room's own west stub, drawn with the strip above
    elif request_roof is None:
        in_y = gate_y + gate_in_row
        pipe([(gx[0] - 2, in_y), (gx[0], in_y)])
        in_cell = (gx[0] - 2, in_y)
    else:
        # The roof came up to meet the caller, so the request arrives from above
        # and the last cell before the west wall is all the block owns. One cell,
        # because whatever hands it over has to descend to this row anyway.
        #
        # ``request_tuck`` takes the **first** interior row instead of the second.
        # The stub stands outside the west wall, so any interior row will do, and
        # the gate's ``U`` receives from any incoming pipe with no distance term —
        # the same clause that lets the entry sit 33 rows north of the man who
        # reads it. What it buys is one row off the caller's drop, which is then
        # the two cells a pipe is required to be and cannot shorten again.
        # ``False`` keeps every existing caller's grid byte-identical.
        in_y = request_roof + (1 if request_tuck else 2)
        put(gx[0] - 1, in_y, ">")
        in_cell = (gx[0] - 1, in_y)
    if answer_west is None:
        ox, oy = out_x, 0
        while (ox, oy) not in cells:
            oy += 1  # the stub draw stops one short: name the real topmost cell
    elif answer_exit_west:
        ox, oy = coll_x0 - 2, coll_y + 1  # ... and westward it is the only one
    else:
        ox, oy = out_x, coll_y + 4  # ... and southward it is the bottommost

    width = max(x for x, _ in cells) + 1
    height = max(y for _, y in cells) + 1
    # Pipes the block owns outright (the in/out stubs merge with the machine's
    # request and response runs, men-v3's convention): per bank two ring legs
    # and one feed and one answer, plus the gate-to-gate chain links. A feed
    # crossing a forwarder is two pipes, not one.
    # Per bank two ring legs, an answer and the two halves of a feed crossing the
    # forwarder. The chain then adds a link between consecutive gates; the
    # broadcast adds one riser per filter, which is a pipe the chain never had.
    # ... the broadcast adds one riser per filter and the descender's own
    # leg into the broadcast room, both pipes the chain never had.
    pipes = nb * (5 if feed_teleport else 4) + (nb + 1 if broadcast else nb - 2)
    return V3Store(
        cells=cells,
        width=width,
        height=height,
        in_cell=in_cell,
        out_cell=(ox, oy),
        pipes=pipes,
    )
