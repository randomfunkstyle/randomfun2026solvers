"""Rendering a block that does not load yet, so its topology can be looked at.

``manpng`` needs a complete ``.man``; the geometry that costs rounds is a block
under construction, whose pipes are still wrong. ``manview`` pads a builder's
sparse ``{(x, y): glyph}`` mapping into a rectangle and hands it to ``manpng``.

What is pinned here is the padding contract, because that is what a picture's
coordinates are read against: if ``as_rows`` cropped the empty margin, the space
the missing pipes still have to occupy would vanish from the image, which is
exactly the space being reasoned about.
"""

from __future__ import annotations

import pytest
from randomfun2026solvers import manview


def test_as_rows_places_each_glyph_at_its_own_coordinate():
    rows = manview.as_rows({(0, 0): "+", (3, 0): "+", (0, 2): "|", (3, 2): "|"})
    assert rows[0] == "+  +"
    assert rows[2] == "|  |"
    assert rows[1] == "", "an untouched row is blank, not missing"


def test_as_rows_honours_a_box_larger_than_the_occupied_extent():
    """The empty margin is where the unrouted pipes go; cropping it hides the problem."""
    rows = manview.as_rows({(1, 1): "x"}, width=10, height=4)
    assert len(rows) >= 5
    assert rows[1] == " x"


def test_as_rows_of_nothing_is_nothing():
    assert manview.as_rows({}) == []


def test_a_negative_coordinate_is_refused_rather_than_wrapped():
    """Python indexing would happily write to grid[-1]; that is a silent wrong picture."""
    with pytest.raises(ValueError, match="negative cell coordinate"):
        manview.as_rows({(-1, 0): "x"})


def test_show_refuses_an_empty_mapping():
    with pytest.raises(ValueError, match="empty"):
        manview.show({}, "/tmp/manview-should-not-exist")


@pytest.mark.slow
def test_show_writes_a_man_and_a_png_that_agree(tmp_path):
    """The .man is kept beside the .png so a coordinate in the picture can be checked."""
    from randomfun2026solvers.lm1 import stream

    block = stream.build_stream(a_slots=200, b_slots=200, c_slots=16, trie_bits=3)
    man, png = manview.show(
        block.cells, tmp_path / "block3", width=block.width, height=block.height
    )
    assert man.exists() and png.exists()
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    text = man.read_text()
    assert text.endswith("\n")
    assert max(len(line) for line in text.splitlines()) <= block.width + 1
