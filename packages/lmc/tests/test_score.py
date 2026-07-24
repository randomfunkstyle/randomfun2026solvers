"""Footprint (area) scoring — unit cases + a locked baseline.

Pure string math, so these run without `node`/`lm.mjs`. The locked constants are the
regression baseline any future layout minimizer must hold or beat.
"""

from __future__ import annotations

import pathlib

from lmc import bounding_box, footprint
from lmc.compile import compile_source
from lmc.demos import reverse_program
from lmc.router import render

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"


# --- bounding_box / footprint unit behaviour ---------------------------------

def test_bbox_basic_rectangle():
    grid = "abc\ndef\n"  # 3 wide, 2 tall, single trailing newline
    assert bounding_box(grid) == (3, 2)
    assert footprint(grid) == 3 ** 2  # max(3, 2) ** 2


def test_bbox_ragged_width_is_longest_row():
    grid = "a\nbbbbb\ncc\n"
    assert bounding_box(grid) == (5, 3)
    assert footprint(grid) == 5 ** 2


def test_bbox_trailing_whitespace_does_not_count():
    # trailing pad on a row must not inflate width (matches Canvas.render rstrip)
    grid = "abc     \nde\n"
    assert bounding_box(grid) == (3, 2)


def test_bbox_only_trailing_newline_dropped_once():
    # a blank interior line still counts toward height; only the final "" is dropped
    grid = "a\n\nb\n"
    assert bounding_box(grid) == (1, 3)


def test_bbox_no_trailing_newline():
    assert bounding_box("ab\ncde") == (3, 2)


def test_bbox_empty_string():
    # "" -> split -> [""] -> trailing "" dropped -> [] -> (width 0, height 0)
    assert bounding_box("") == (0, 0)
    assert footprint("") == 0


def test_footprint_is_square_of_larger_dim():
    tall = "x\nx\nx\nx\n"  # 1 wide, 4 tall
    assert bounding_box(tall) == (1, 4)
    assert footprint(tall) == 4 ** 2


# --- baseline: real generated grids (regression lock) ------------------------

def test_layout_triangle_footprint_baseline():
    grid = compile_source("n = recv()\nemit(n*(n+1)//2)")
    assert bounding_box(grid) == (24, 3)
    assert footprint(grid) == 576


def test_router_reverse_footprint_baseline():
    grid = render(*reverse_program(), ring_len=9)
    # bbox is stable across the router's (unseeded) z3 attachment placement --
    # attach cells live inside the CPU walls and never move the bounding box.
    assert bounding_box(grid) == (45, 21)
    assert footprint(grid) == 2025


def test_render_matches_committed_reverse_example_footprint():
    grid = render(*reverse_program(), ring_len=9)
    committed = (EXAMPLES / "reverse.man").read_text()
    assert bounding_box(grid) == bounding_box(committed)
