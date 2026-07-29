"""The MNIST data path: 8x8 at 4 bits, nibble-packed five words to an image.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §3.

These are pure functions over bytes, so they belong in the fast tier. Nothing here
touches the network: the fetch is a CLI subcommand and the tests read the vendored
artifact (AGENTS.md, "never touch production from a test").
"""

from __future__ import annotations

import pytest
from randomfun2026solvers import mnist_data as md


def test_pack_round_trips_every_nibble_value():
    pixels = [i % 16 for i in range(64)]
    words = md.pack_image(pixels, label=7)
    assert len(words) == md.WORDS_PER_IMAGE
    assert all(w >= 0 for w in words), "ROM literals are digits only, so words must be non-negative"
    got_pixels, got_label = md.unpack_image(words)
    assert got_pixels == pixels
    assert got_label == 7


def test_every_packed_word_is_a_representable_rom_literal():
    """The reason a word holds fifteen nibbles and not sixteen.

    Sixteen nibbles is 64 bits, so an all-white image would pack to 2**64 - 1 —
    not a signed 64-bit value and not a non-negative ROM literal, and
    rom.digit_width raises on it. Fifteen nibbles is 60 bits.
    """
    words = md.pack_image([15] * 64, label=9)
    assert len(words) == 5
    for w in words:
        assert 0 <= w <= 2**63 - 1, f"{w} is not a representable ROM literal"
    assert md.unpack_image(words) == ([15] * 64, 9)


def test_packing_uses_fifteen_nibbles_per_word():
    """Pixel k lives in nibble k % 15 of word k // 15."""
    pixels = [0] * 64
    pixels[15] = 7  # first nibble of the second word
    words = md.pack_image(pixels, label=0)
    assert words[0] == 0
    assert words[1] == 7


def test_downsample_averages_three_by_three_blocks():
    """A uniform image stays uniform; 3x3 average pooling of a constant is that constant."""
    raw = bytes([255] * 784)
    pixels = md.downsample(raw)
    assert len(pixels) == 64
    assert set(pixels) == {15}, "255 >> 4 == 15"


def test_downsample_is_in_nibble_range():
    raw = bytes(range(256)) * 3 + bytes(16)
    pixels = md.downsample(raw)
    assert len(pixels) == 64
    assert all(0 <= p <= 15 for p in pixels)


def test_pack_image_rejects_out_of_range_pixels():
    pixels = [0] * 64
    pixels[0] = 16
    with pytest.raises(ValueError):
        md.pack_image(pixels, label=0)


def test_vendored_artifact_loads_at_the_declared_shape():
    train, val = md.load_packed()
    assert len(train) == md.N_TRAIN * md.WORDS_PER_IMAGE
    assert len(val) == md.N_VAL * md.WORDS_PER_IMAGE
    assert all(w >= 0 for w in train + val)


def test_vendored_labels_are_all_ten_classes():
    """Label extraction goes through unpack_image, not a raw word read.

    Word 4's low nibbles hold pixels 60-63 (design doc §3.1), so the raw word
    equals the bare label only when those pixels happen to be zero -- not
    guaranteed, and empirically false for some real MNIST images. Reading the
    label through unpack_image is what the packing format actually promises.
    """
    train, _ = md.load_packed()
    labels = {
        md.unpack_image(train[i * md.WORDS_PER_IMAGE : (i + 1) * md.WORDS_PER_IMAGE])[1]
        for i in range(md.N_TRAIN)
    }
    assert labels == set(range(10)), "2000 MNIST images should cover every digit"
