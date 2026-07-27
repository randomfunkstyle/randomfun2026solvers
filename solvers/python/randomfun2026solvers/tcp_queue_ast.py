#!/usr/bin/env python3
"""Packet reassembly as an AST-authored sender and paired FIFO scanner.

This is the literal two-room / two-tape architecture:

``sender``
    Read ``n`` once, then forward exactly ``n`` ``(seq, value)`` pairs.  The
    counted loop naturally blocks between judge rounds.

``scanner``
    Keep ``want`` (the next sequence number) in ``B``.  A packet for ``want`` is
    emitted immediately.  A future packet is appended to two aligned FIFO
    loops: its sequence number to the index queue and its value to the value
    queue.  After every emit, a ``-1``/``0`` fence pair is appended behind all
    queued packets.  Reads naturally block until every pair ahead of that fence
    returns.  A match is consumed; a mismatch is sent behind the fence.  If the
    pass emitted anything, the scanner adds a fresh fence and scans again.

The fence is the queue-length proof.  It removes both timing padding and the
incorrect assumption that ``q`` can count values still crossing the outgoing
leg or relay.  ``BP`` is only a one-bit "this pass emitted" flag.

The grid is authored as :mod:`manast` rooms and pipes.  ``Circuit`` is used only
as a collision-checking placer for each room's AST leaves; final bytes are
produced exclusively by :func:`manast.render`.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from randomfun2026solvers.circuit import Circuit, E, N, S, W
from randomfun2026solvers.dataflow_relay import relay
from randomfun2026solvers.man_debug import DebugMap
from randomfun2026solvers.manast import Ast, Joint, PipeNode, RoomNode, Run, render
from randomfun2026solvers.manmoves import reglyph, try_drop, try_squash

__all__ = [
    "MAX_DELAY",
    "MAX_QUEUED",
    "build",
    "build_ast",
    "debug_map",
    "main",
    "simulate",
]

MAX_DELAY = 16
MAX_QUEUED = MAX_DELAY - 1

SENDER_W, SENDER_H = 12, 8
WORKER_W, WORKER_H = 64, 29
RELAY_W, RELAY_H = 9, 4

INPUT_ID = 0
SENDER_ID = 1
WORKER_ID = 2
OUTPUT_ID = 3
INDEX_RELAY_ID = 4
VALUE_RELAY_ID = 5

INPUT_ORIGIN = (3, 0)
OUTPUT_ORIGIN = (15, 0)
SENDER_ORIGIN = (0, 15)
INDEX_RELAY_ORIGIN = (25, 8)
VALUE_RELAY_ORIGIN = (47, 8)
WORKER_ORIGIN = (0, 40)

# All six worker pipes attach to the north wall.  With north-wall anchors,
# nearest-pipe binding is decided only by column at every worker row.
PACKET_COL = 4
OUTPUT_COL = 15
INDEX_SEND_COL = 28
INDEX_RECV_COL = 33
VALUE_SEND_COL = 50
VALUE_RECV_COL = 55

# Proven AST compaction sequence.  The first two groups are global empty-line
# cuts, expressed in the base AST's coordinates and applied from high to low so
# their indices do not drift.  The repeated row-3 cuts shorten only the roomy IO
# conduits.  Finally, three worker-local squashes remove an empty band while
# growing the east-wall queue legs; that both narrows the worker and shortens its
# hot scan lap.
COMPACT_COLS = (63, 62, 60, 46, 45, 44, 43, 42, 41, 40, 39, 38, 24, 23, 22, 21)
COMPACT_ROWS = (67, 65, 63, 55, 54, 52, 49, 42)
IO_GAP_CUTS = 5
PIPE_BAND_CUTS = 13
WORKER_SQUASH_COL = 40
WORKER_SQUASHES = 3


@dataclass(frozen=True)
class QueueTrace:
    """One semantic-model checkpoint, useful in tests and debugger notes."""

    want: int
    indices: tuple[int, ...]
    values: tuple[int, ...]
    output: tuple[int, ...]
    failed: bool = False


def simulate(rounds: Iterable[Iterable[int]]) -> list[list[int]]:
    """Reference model of the paired FIFO algorithm, round by round."""
    want = 0
    indices: list[int] = []
    values: list[int] = []
    result: list[list[int]] = []
    failed = False

    for raw in rounds:
        packet = list(raw)
        if failed:
            result.append([])
            continue
        if len(packet) == 3:
            _, seq, value = packet
        elif len(packet) == 2:
            seq, value = packet
        else:
            raise ValueError(f"round must contain n,seq,value or seq,value: {packet}")

        emitted: list[int] = []
        if seq - want >= MAX_DELAY:
            emitted.append(-1)
            failed = True
            result.append(emitted)
            continue

        if seq == want:
            emitted.append(value)
            want += 1

            # A match starts another fence-delimited pass.  This is equivalent
            # to repeated full scans, but makes the mutation explicit.
            while True:
                match = next((i for i, queued in enumerate(indices) if queued == want), None)
                if match is None:
                    break
                indices.pop(match)
                emitted.append(values.pop(match))
                want += 1
        else:
            indices.append(seq)
            values.append(value)

        if len(indices) != len(values):
            raise AssertionError("paired queues lost alignment")
        if len(indices) > MAX_QUEUED:
            raise AssertionError("problem window permits at most 15 queued packets")
        result.append(emitted)

    return result


def _sender() -> Circuit:
    """Read n once and forward exactly n packet pairs."""
    c = Circuit(SENDER_W, SENDER_H, strict_corridors=True)
    c.run(0, 0, "@>rb")
    c.route((4, 0), E, [(5, 0)], (5, 1), E)
    exit_cell = c.counted_loop(5, 1, "rsrs")
    c.set(*exit_cell, "H")
    return c


def _worker() -> Circuit:  # noqa: PLR0915 - the control-flow graph is the artifact
    """Place INIT, packet dispatch, and the fence-delimited paired queue scan."""
    c = Circuit(WORKER_W, WORKER_H, strict_corridors=True)

    # INIT: B = want = 0, then merge into MAIN.
    c.run(0, 0, "@>0M")
    c.route((4, 0), E, [(8, 0), (8, 2), (1, 2)], (1, 3), E)

    # MAIN: A = seq - want.  d catches delta >= 16; X sends zero straight to
    # DIRECT and positive deltas south to STORE.
    c.set(1, 3, ">")
    c.horizontal(3, 1, PACKET_COL)
    c.set(PACKET_COL, 3, "r")
    c.run(PACKET_COL + 1, 3, "-b]]]]")
    c.set(11, 3, "d")
    c.set(12, 3, "X")

    # FAIL: the maximum-delay rule.
    c.run(11, 4, "1N", d=S)
    c.set(11, 6, "<")
    c.set(10, 6, "s")
    c.set(9, 6, "H")

    # DIRECT: receive and emit the value, then B = want + 1.  The long U enters
    # SCAN_START from the east; it is control routing, not a timing wait.
    c.set(13, 3, "r")
    c.set(14, 3, "s")
    c.run(15, 3, "0+1+M")
    c.route((20, 3), E, [(60, 3), (60, 12)], (57, 12), W)

    # SCAN_START: clear the per-pass match flag and append a fence pair behind
    # every queued packet.  -1 cannot be a legal sequence number; the value-side
    # zero is only an alignment token and is consumed with the fence.
    c.run(32, 12, "0b1N", d=W)
    c.set(INDEX_SEND_COL, 12, "s")
    c.set(27, 12, "^")
    c.vertical(27, 12, 10)
    c.set(27, 10, ">")
    c.horizontal(10, 27, 49)
    c.set(49, 10, "0")
    c.set(VALUE_SEND_COL, 10, "s")
    c.set(51, 10, "v")
    c.vertical(51, 10, 16)
    c.set(51, 16, "<")
    c.horizontal(16, 51, 33)
    c.set(33, 17, "v")  # mismatch/match feedback merges into this descent
    c.route(
        (33, 16),
        W,
        [(33, 18)],
        (33, 18),
        E,
    )

    # SCAN ITEM: A = queued index - want.
    #   negative: the -1 fence
    #   zero:     consume and emit the matching value
    #   positive: rotate the aligned pair behind the fence
    c.set(33, 18, ">")
    c.set(34, 18, "r")
    c.set(35, 18, "-")
    c.set(36, 18, "X")

    # MISMATCH: reconstruct seq, rotate it, then rotate its paired value.
    c.set(36, 19, "+")
    c.set(36, 20, "<")
    c.horizontal(20, 36, INDEX_SEND_COL)
    c.set(INDEX_SEND_COL, 20, "s")
    c.set(27, 20, "v")
    c.vertical(27, 20, 23)
    c.set(27, 23, ">")
    c.horizontal(23, 27, VALUE_RECV_COL)
    c.set(VALUE_RECV_COL, 23, "r")
    c.set(56, 23, "v")
    c.vertical(56, 23, 25)
    c.set(56, 25, "<")
    c.horizontal(25, 56, VALUE_SEND_COL)
    c.set(VALUE_SEND_COL, 25, "s")
    c.route(
        (49, 25),
        W,
        [(49, 27), (58, 27), (58, 15), (33, 15)],
        (33, 18),
        E,
    )

    # MATCH: consume the value, emit it, increment want, and set BP=1.  Continue
    # to the old fence so every item in this pass is accounted for exactly once.
    c.horizontal(18, 36, VALUE_RECV_COL)
    c.set(VALUE_RECV_COL, 18, "r")
    c.set(56, 18, "v")
    c.vertical(56, 18, 21)
    c.set(56, 21, "<")
    c.horizontal(21, 56, 14)
    c.set(14, 21, "s")
    c.run(13, 21, "0+1+M1b", d=W)
    c.route(
        (6, 21),
        W,
        [(6, 28), (58, 28), (58, 15), (33, 15)],
        (33, 18),
        E,
    )

    # FENCE: consume the paired dummy value.  BP=1 turns north back into
    # SCAN_START for another complete pass; BP=0 goes straight to MAIN.
    c.set(36, 17, ">")
    c.horizontal(17, 36, VALUE_RECV_COL)
    c.set(VALUE_RECV_COL, 17, "r")
    c.set(57, 17, "a")
    c.vertical(57, 17, 12)
    c.set(57, 12, "<")  # positive BP restarts at SCAN_START
    c.set(63, 17, "^")  # zero BP returns directly to MAIN

    # STORE: append a future packet and return immediately.  It cannot satisfy
    # the current want, and the next scan's fence will wait for it if necessary.
    c.set(12, 4, "+")
    c.set(12, 5, ">")
    c.horizontal(5, 12, INDEX_SEND_COL)
    c.set(INDEX_SEND_COL, 5, "s")
    c.set(29, 5, "v")
    c.vertical(29, 5, 7)
    c.set(29, 7, "<")
    c.horizontal(7, 29, PACKET_COL)
    c.set(PACKET_COL, 7, "r")
    c.set(3, 7, "v")
    c.vertical(3, 7, 9)
    c.set(3, 9, ">")
    c.horizontal(9, 3, VALUE_SEND_COL)
    c.set(VALUE_SEND_COL, 9, "s")
    c.route((51, 9), E, [(63, 9), (63, 2)], (1, 2), S)

    # The zero-match fence path merges into the same short MAIN return.
    c.route((58, 17), E, [(63, 17), (63, 2)], (1, 2), S)
    c.set(1, 3, ">")
    return c


def _circuit_children(
    circuit: Circuit,
    origin: tuple[int, int],
) -> list[Run | Joint]:
    """Turn every collision-checked room cell into an editable AST leaf."""
    ox, oy = origin
    joints = frozenset("<>^vVXdax")
    children: list[Run | Joint] = []
    for node_id, ((x, y), glyph) in enumerate(
        sorted(circuit.cell.items(), key=lambda item: (item[0][1], item[0][0]))
    ):
        if glyph == " ":
            continue
        gx, gy = ox + x + 1, oy + y + 1
        if glyph in joints:
            children.append(Joint(id=node_id, x=gx, y=gy, glyph=glyph))
        else:
            children.append(Run(id=node_id, x=gx, y=gy, glyphs=glyph, heading="E"))
    return children


def _art_children(rows: list[str], origin: tuple[int, int]) -> list[Run | Joint]:
    """Convert a room-shaped relay artwork into AST interior leaves."""
    ox, oy = origin
    c = Circuit(len(rows[0]) - 2, len(rows) - 2)
    for y, row in enumerate(rows[1:-1]):
        for x, glyph in enumerate(row[1:-1]):
            if glyph != " ":
                c.set(x, y, glyph)
    return _circuit_children(c, (ox, oy))


def _vertical(x: int, y0: int, y1: int) -> list[tuple[int, int]]:
    step = 1 if y1 > y0 else -1
    return [(x, y) for y in range(y0, y1 + step, step)]


def _pipe(
    node_id: int,
    path: list[tuple[int, int]],
    *,
    src: int,
    dst: int,
    direction: tuple[int, int],
    capacity: int,
) -> PipeNode:
    return PipeNode(
        id=node_id,
        x=min(x for x, _ in path),
        y=min(y for _, y in path),
        path=path,
        glyphs=reglyph(path, direction, direction),
        src=src,
        dst=dst,
        min_capacity=capacity,
        entry_dir=direction,
        exit_dir=direction,
    )


def _compact_ast(ast: Ast) -> Ast:
    """Apply the validated line-cut and worker-squash campaign to ``ast``."""
    queue_capacity = {
        (3, 4): MAX_QUEUED + 1,
        (5, 6): MAX_QUEUED + 1,
    }
    for axis, indices in (("row", COMPACT_ROWS), ("col", COMPACT_COLS)):
        for index in indices:
            compacted, report = try_drop(ast, axis, index, capacity=queue_capacity)
            if compacted is None:
                raise RuntimeError(f"cannot drop {axis} {index}: {report}")
            ast = compacted

    for _ in range(IO_GAP_CUTS):
        compacted, report = try_drop(ast, "row", 3, capacity=queue_capacity)
        if compacted is None:
            raise RuntimeError(f"cannot shorten IO gap at row 3: {report}")
        ast = compacted

    for _ in range(WORKER_SQUASHES):
        compacted, report = try_squash(
            ast,
            WORKER_ID,
            "col",
            WORKER_SQUASH_COL,
            capacity=queue_capacity,
        )
        if compacted is None:
            raise RuntimeError(f"cannot squash worker col {WORKER_SQUASH_COL}: {report}")
        ast = compacted

    # The authored minima, unlike a parsed grid's conservative inferred ring
    # capacity, prove that the long vertical pipe band has eleven spare cells.
    # Stop exactly when the packet conduit reaches its two-cell minimum.  Each
    # paired FIFO loop still has at least sixteen cells: fifteen packets plus
    # the fence that proves a complete scan.
    for _ in range(PIPE_BAND_CUTS):
        compacted, report = try_drop(ast, "row", 20, capacity=queue_capacity)
        if compacted is None:
            raise RuntimeError(f"cannot shorten queue pipe band at row 20: {report}")
        ast = compacted
    return ast


def build_ast() -> Ast:
    """Build the six rooms and seven pipes as a total authored AST."""
    sender = RoomNode(
        id=SENDER_ID,
        x=SENDER_ORIGIN[0],
        y=SENDER_ORIGIN[1],
        kind="compute",
        w=SENDER_W,
        h=SENDER_H,
        children=_circuit_children(_sender(), SENDER_ORIGIN),
        ports=[(4, 15), (5, 24)],
    )
    worker = RoomNode(
        id=WORKER_ID,
        x=WORKER_ORIGIN[0],
        y=WORKER_ORIGIN[1],
        kind="compute",
        w=WORKER_W,
        h=WORKER_H,
        children=_circuit_children(_worker(), WORKER_ORIGIN),
        ports=[(5, 40), (16, 40), (29, 40), (34, 40), (51, 40), (56, 40)],
    )
    input_room = RoomNode(
        id=INPUT_ID,
        x=INPUT_ORIGIN[0],
        y=INPUT_ORIGIN[1],
        kind="input",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=4, y=1, glyphs="I")],
        ports=[(4, 2)],
    )
    output_room = RoomNode(
        id=OUTPUT_ID,
        x=OUTPUT_ORIGIN[0],
        y=OUTPUT_ORIGIN[1],
        kind="output",
        w=1,
        h=1,
        rigid_size=True,
        children=[Run(id=0, x=16, y=1, glyphs="O")],
        ports=[(16, 2)],
    )

    relay_rows = relay(RELAY_W, RELAY_H)
    index_relay = RoomNode(
        id=INDEX_RELAY_ID,
        x=INDEX_RELAY_ORIGIN[0],
        y=INDEX_RELAY_ORIGIN[1],
        kind="compute",
        w=RELAY_W,
        h=RELAY_H,
        children=_art_children(relay_rows, INDEX_RELAY_ORIGIN),
        ports=[(29, 13), (34, 13)],
    )
    value_relay = RoomNode(
        id=VALUE_RELAY_ID,
        x=VALUE_RELAY_ORIGIN[0],
        y=VALUE_RELAY_ORIGIN[1],
        kind="compute",
        w=RELAY_W,
        h=RELAY_H,
        children=_art_children(relay_rows, VALUE_RELAY_ORIGIN),
        ports=[(51, 13), (56, 13)],
    )

    pipes = [
        _pipe(
            0,
            _vertical(4, 3, 14),
            src=INPUT_ID,
            dst=SENDER_ID,
            direction=S,
            capacity=2,
        ),
        _pipe(
            1,
            _vertical(5, 25, 39),
            src=SENDER_ID,
            dst=WORKER_ID,
            direction=S,
            capacity=2,
        ),
        _pipe(
            2,
            _vertical(16, 39, 3),
            src=WORKER_ID,
            dst=OUTPUT_ID,
            direction=N,
            capacity=2,
        ),
        _pipe(
            3,
            _vertical(29, 39, 14),
            src=WORKER_ID,
            dst=INDEX_RELAY_ID,
            direction=N,
            capacity=2,
        ),
        _pipe(
            4,
            _vertical(34, 14, 39),
            src=INDEX_RELAY_ID,
            dst=WORKER_ID,
            direction=S,
            capacity=2,
        ),
        _pipe(
            5,
            _vertical(51, 39, 14),
            src=WORKER_ID,
            dst=VALUE_RELAY_ID,
            direction=N,
            capacity=2,
        ),
        _pipe(
            6,
            _vertical(56, 14, 39),
            src=VALUE_RELAY_ID,
            dst=WORKER_ID,
            direction=S,
            capacity=2,
        ),
    ]
    return _compact_ast(
        Ast(
            rooms=[input_room, sender, worker, output_room, index_relay, value_relay],
            pipes=pipes,
        )
    )


def build() -> list[str]:
    """Render the authored AST."""
    return render(build_ast())


def _debug_point(x: int, y: int) -> tuple[int, int]:
    """Map a base-layout debugger coordinate through the AST compaction."""
    in_worker = y > WORKER_ORIGIN[1]
    x -= sum(cut < x for cut in COMPACT_COLS)
    y -= sum(cut < y for cut in (*COMPACT_ROWS, *range(3, 3 + IO_GAP_CUTS)))
    if y > 20:
        y -= min(PIPE_BAND_CUTS, y - 20)
    if in_worker:
        for _ in range(WORKER_SQUASHES):
            if x > WORKER_SQUASH_COL:
                x -= 1
    return x, y


def _debug_region(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0 = _debug_point(x, y)
    x1, y1 = _debug_point(x + w - 1, y + h - 1)
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def debug_map() -> DebugMap:
    """Named regions, lanes, and a drain scenario for the generated sidecars."""
    ast = build_ast()
    rooms = {room.id: room for room in ast.rooms}
    pipes = {pipe.id: pipe for pipe in ast.pipes}
    sender = rooms[SENDER_ID]
    worker = rooms[WORKER_ID]
    index_relay = rooms[INDEX_RELAY_ID]
    value_relay = rooms[VALUE_RELAY_ID]

    dbg = DebugMap("tcp — AST paired-queue packet sender")
    dbg.region(
        "packet sender",
        sender.x,
        sender.y,
        *sender.size,
        note="Read n once; forward exactly n (seq,value) pairs with a BP-counted loop.",
        color="#22c55e",
        tags=["ast", "sender"],
    )
    dbg.region(
        "queue scanner",
        worker.x,
        worker.y,
        *worker.size,
        note="B is curr_idx; direct hits emit immediately, future packets enter paired queues.",
        color="#38bdf8",
        tags=["ast", "worker"],
    )
    dbg.region(
        "index relay",
        index_relay.x,
        index_relay.y,
        *index_relay.size,
        note="FIFO turnaround for queued packet sequence numbers.",
        color="#f59e0b",
        tags=["queue", "index"],
    )
    dbg.region(
        "value relay",
        value_relay.x,
        value_relay.y,
        *value_relay.size,
        note="Identical FIFO turnaround; operation order keeps values aligned with indices.",
        color="#a78bfa",
        tags=["queue", "value"],
    )
    ox, oy = WORKER_ORIGIN[0] + 1, WORKER_ORIGIN[1] + 1
    dbg.region(
        "packet dispatch",
        *_debug_region(ox + 1, oy + 3, 20, 5),
        note="seq-want: >=16 fails, 0 emits, positive stores.",
        color="#60a5fa",
    )
    dbg.region(
        "paired store",
        *_debug_region(ox + 3, oy + 5, 60, 5),
        note="Append seq to the index queue and value to the value queue.",
        color="#fb923c",
    )
    dbg.region(
        "fence-delimited scan",
        *_debug_region(ox + 27, oy + 10, 32, 19),
        note="-1/0 fences one complete pass; BP records whether that pass emitted.",
        color="#14b8a6",
    )
    dbg.lane(
        "input to sender",
        pipes[0].path,
        kind="pipe",
        color="#22c55e",
    )
    dbg.lane(
        "packet pair pipe",
        pipes[1].path,
        kind="pipe",
        color="#60a5fa",
    )
    dbg.lane(
        "ordered output",
        pipes[2].path,
        kind="pipe",
        color="#a78bfa",
    )
    dbg.lane(
        "index queue forward",
        pipes[3].path,
        kind="pipe",
        color="#f59e0b",
    )
    dbg.lane(
        "index queue return",
        pipes[4].path,
        note="A scan blocks here until the next index or the -1 fence arrives.",
        kind="pipe",
        color="#f59e0b",
    )
    dbg.lane(
        "value queue forward",
        pipes[5].path,
        kind="pipe",
        color="#a78bfa",
    )
    dbg.lane(
        "value queue return",
        pipes[6].path,
        note="Same geometry as the index return, preserving pair order.",
        kind="pipe",
        color="#a78bfa",
    )
    dbg.lane(
        "mismatch feedback",
        [
            _debug_point(x, y)
            for x, y in [
                (ox + 36, oy + 18),
                (ox + 36, oy + 20),
                (ox + 28, oy + 20),
                (ox + 27, oy + 20),
                (ox + 27, oy + 23),
                (ox + 56, oy + 23),
                (ox + 56, oy + 25),
                (ox + 50, oy + 25),
                (ox + 49, oy + 25),
                (ox + 49, oy + 27),
                (ox + 58, oy + 27),
                (ox + 58, oy + 15),
                (ox + 33, oy + 15),
                (ox + 33, oy + 18),
            ]
        ],
        note="Put both fields behind the current fence, then read the next queued index.",
        kind="control",
        color="#f97316",
    )
    dbg.lane(
        "match and restart",
        [
            _debug_point(x, y)
            for x, y in [
                (ox + 36, oy + 18),
                (ox + 56, oy + 18),
                (ox + 56, oy + 21),
                (ox + 14, oy + 21),
                (ox + 6, oy + 21),
                (ox + 6, oy + 28),
                (ox + 58, oy + 28),
                (ox + 58, oy + 15),
                (ox + 33, oy + 15),
                (ox + 33, oy + 18),
            ]
        ],
        note="Consume, emit, increment curr_idx, set BP=1, and finish the fenced pass.",
        kind="control",
        color="#22c55e",
    )
    dbg.lane(
        "fence pair",
        [
            _debug_point(x, y)
            for x, y in [
                (ox + 57, oy + 12),
                (ox + 28, oy + 12),
                (ox + 27, oy + 12),
                (ox + 27, oy + 10),
                (ox + 51, oy + 10),
                (ox + 51, oy + 16),
                (ox + 33, oy + 16),
                (ox + 33, oy + 18),
            ]
        ],
        note="Append -1 and dummy 0; the scanner blocks until every earlier pair arrives.",
        kind="control",
        color="#facc15",
    )
    dbg.lane(
        "stored-pair immediate return",
        [
            _debug_point(x, y)
            for x, y in [
                (ox + 51, oy + 9),
                (ox + 63, oy + 9),
                (ox + 63, oy + 2),
                (ox + 1, oy + 2),
            ]
        ],
        note="A future packet cannot satisfy curr_idx, so STORE returns to MAIN immediately.",
        kind="control",
        color="#fb923c",
    )
    dbg.scenario(
        "queued pair unlocks",
        "4 2 30 / 0 10 / 1 20 / 3 40",
        0,
        20_000,
        watch=[
            "packet sender",
            "fence-delimited scan",
            "index queue return",
            "value queue return",
        ],
        note="Packet 2 waits; packet 1 later unlocks values 20 and 30 in one round.",
    )
    return dbg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--man", required=True, type=Path)
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    args = parser.parse_args(argv)

    rows = build()
    dbg = debug_map()
    for path in (args.man, args.html, args.json):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.man.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dbg.write_html(rows, args.html)
    dbg.write_json(args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
