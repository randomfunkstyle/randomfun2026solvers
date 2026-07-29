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


def test_word_layout_matches_hand_computed_bit_positions():
    """Pins the exact bit layout of words 0, 1 and 4, independent of unpack_image.

    Every other layout check in this file goes through a pack -> unpack round
    trip, so a bug that shifts a field the same wrong way in both pack_image
    and unpack_image would pass every one of them while silently breaking the
    on-grid format every later LM-1 task depends on -- "the heart of this
    task." This test computes its expected integers from bare shift
    arithmetic and never calls unpack_image, so a compensating bug in the pair
    cannot hide from it.
    """
    pixels = [0] * 64
    # The word 0 / word 1 boundary: pixel 14 is nibble 14 (the *top*, highest-
    # order nibble) of word 0 -- an off-by-one in the shift is invisible to a
    # round trip but not to a literal comparison. Pixel 15 is nibble 0 of word 1.
    pixels[14] = 3
    pixels[15] = 9
    # Word 4: pixels 60-63 occupy nibbles 0-3, and the label shares the same
    # word at nibble 4. Every field gets a distinct nonzero value so a swapped
    # or shifted field cannot coincidentally match.
    pixels[60] = 1
    pixels[61] = 2
    pixels[62] = 4
    pixels[63] = 8
    label = 7

    words = md.pack_image(pixels, label=label)

    assert words[0] == 3 << (4 * 14), "pixel 14 must sit in the top nibble (14) of word 0"
    assert words[1] == 9 << (4 * 0), "pixel 15 must sit in nibble 0 of word 1"
    expected_word4 = (
        (1 << (4 * 0)) | (2 << (4 * 1)) | (4 << (4 * 2)) | (8 << (4 * 3)) | (label << (4 * 4))
    )
    assert words[4] == expected_word4, "pixels 60-63 in nibbles 0-3 and the label in nibble 4"


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
