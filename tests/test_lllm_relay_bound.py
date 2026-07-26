"""Why `little-little-man`'s relay trick does not transfer to LLLM.

The fold that took LLM from `area2` 37,636 to 33,856 worked by moving a pipe
choice behind a seam. The obvious next move is to do the same for LLLM, whose
blocks are pinned to column bands by the same nearest-pipe rule. These pin the
two measurements that say not to.
"""

from __future__ import annotations

from randomfun2026solvers import lllm_layout as L
from randomfun2026solvers import lllm_ring as R
from randomfun2026solvers.lllm_relay_bound import block_widths, relay_bound


def test_lane_rows_are_the_majority_and_a_relay_cannot_touch_them():
    """The first reason. A relay moves *where a block sits*, never how it leaves.

    Deleting every seek is worth 17 rows of 168; the lanes are 88. Any argument
    that starts "if blocks could sit anywhere" has to clear this number first.
    """
    b = relay_bound()
    assert b["today_span"] == b["today_glyph_rows"] + b["lane_rows"]
    assert b["lane_rows"] > b["today_glyph_rows"], "lanes dominate the span"
    assert b["lane_rows"] / b["today_span"] > 0.5
    # A relay removes seeks and nothing else.
    assert b["relay_span"] == b["relay_glyph_rows"] + b["lane_rows"]
    assert b["seek_rows_saved"] == b["today_glyph_rows"] - b["relay_glyph_rows"]


def test_even_the_optimistic_pairing_ceiling_misses_the_target():
    """The bar for rewriting the allocator was 60-80 rows. The ceiling is 120,
    and it already assumes every pairing routes for free."""
    b = relay_bound()
    assert b["relay_paired_span_ceiling"] > 100
    assert b["relay_paired_span_ceiling"] < b["relay_span"]


def test_the_pinned_blocks_are_pinned_by_rings_not_by_ports():
    """The second reason, and the one that settles it.

    LLM's display ports are one-way sinks — nothing flows back and the pipe's
    length means nothing, so a relay may sit in front of them. LLLM's `ST` and
    `FI` are rotating loops where *the pipe is the data structure*: `lllm_ring`
    says "reading slot i means rotating i words", and a tick is one rotation of
    `FILE` and one lap of `STORE`. Splicing a relay into such a loop keeps FIFO
    order but lengthens the ring, and that latency is paid every tick.

    So the only relayable zone is `IO`, and almost nothing is pinned by `IO`
    alone.
    """
    geo = L.LLLM
    zones = {
        n: {geo.token_zone[t] for t in toks if t in geo.token_zone}
        for n, (toks, _s) in R.WORKER.items()
    }
    io_only = [n for n, z in zones.items() if z == {"IO"}]
    ring_bound = [n for n, z in zones.items() if z & {"ST", "FI"}]
    free = [n for n, z in zones.items() if not z]

    assert len(free) == 23
    assert len(ring_bound) == 38, "the rotating rings pin nearly every bound block"
    assert len(io_only) == 2, "relaying IO would free two blocks"
    assert len(io_only) + len(ring_bound) + len(free) == len(R.WORKER)


def test_block_widths_are_far_under_the_usable_row():
    """Why seeks, not glyph counts, are what wrap a row: the widest block is well
    inside the usable width, so nothing wraps for lack of room."""
    b = relay_bound()
    widths = block_widths()
    assert max(widths.values()) == b["widest_block"]
    assert b["widest_block"] < b["usable_width"]
    # Hence one glyph row per block once seeks are gone.
    assert b["relay_glyph_rows"] == len(R.WORKER)
