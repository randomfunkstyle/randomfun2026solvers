"""MNIST, downsampled to 8x8 at 4 bits and nibble-packed for an LM-1 ROM.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §3.

Why 8x8 at 4 bits: the model this feeds (a tiny CNN trained *inside* a littleman
grid) pays for every pixel twice — once as ROM area, once as an unrolled
instruction. 28x28x8-bit MNIST would blow both budgets, so the image is
centre-cropped, average-pooled down to 8x8, and quantised to a nibble.

Why fifteen nibbles a word, not sixteen: sixteen nibbles is 64 bits exactly, and
every littleman value is a *signed* 64-bit integer while a ROM literal must
additionally be non-negative (``lm1.rom.digit_width`` raises on a negative word).
An all-white 8x8 image packed at 16 nibbles/word would be ``2**64 - 1`` —
representable in neither. Fifteen nibbles is 60 bits, max
``1_152_921_504_606_846_975`` (19 decimal digits), comfortably inside
``2**63 - 1``. 64 pixels + 1 label is 65 nibbles, so an image is five words with
ten nibble slots spare; the label goes in the first spare slot, nibble 4 of word 4.

Why the fetch is a CLI subcommand and not import-time or test-time work: tests
must never touch the network (AGENTS.md, "never touch production from a test").
``fetch_and_vendor`` downloads and writes a checked-in artifact once; everything
else, including the whole test suite, reads that artifact and is therefore
offline and byte-reproducible.
"""

from __future__ import annotations

import gzip
import hashlib
import struct
import sys
import urllib.request
from pathlib import Path

__all__ = [
    "IMG",
    "NIBBLE_BITS",
    "NIBBLES_PER_WORD",
    "WORDS_PER_IMAGE",
    "N_TRAIN",
    "N_VAL",
    "downsample",
    "pack_image",
    "unpack_image",
    "load_packed",
    "fetch_and_vendor",
    "main",
]

IMG = 8  # downsampled image side, in pixels
NIBBLE_BITS = 4
NIBBLES_PER_WORD = 15  # not 16 -- see module docstring
WORDS_PER_IMAGE = 5  # 64 pixel nibbles + 1 label nibble = 65, spare 10
N_TRAIN = 2000
N_VAL = 1000

_RAW_SIDE = 28  # MNIST's native image side
_CROP = 24  # centre-cropped square before pooling
_CROP_OFFSET = (_RAW_SIDE - _CROP) // 2  # 2
_POOL = _CROP // IMG  # 3x3 blocks

_MAX_WORD = 2**63 - 1  # signed 64-bit, and rom.digit_width additionally rejects <0

_MIRROR = "https://storage.googleapis.com/cvdf-datasets/mnist/"
_TRAIN_IMAGES = "train-images-idx3-ubyte.gz"
_TRAIN_LABELS = "train-labels-idx1-ubyte.gz"
_VAL_IMAGES = "t10k-images-idx3-ubyte.gz"
_VAL_LABELS = "t10k-labels-idx1-ubyte.gz"

_DATA_DIR = Path(__file__).with_name("data")
_ARTIFACT = _DATA_DIR / "mnist-8x8-2000-1000.bin"
_MAGIC = b"LM1MNIST"


def downsample(raw: bytes) -> list[int]:
    """784 raw MNIST bytes (28x28, row-major) -> 64 nibbles (0..15), 8x8 row-major.

    Centre-crop 24x24 out of 28x28 (offset 2 on each axis), average each 3x3
    block, then ``>> 4`` to a nibble. Integer arithmetic only, per the module's
    no-numpy constraint: ``sum(block) // 9 >> 4``.
    """
    if len(raw) != _RAW_SIDE * _RAW_SIDE:
        raise ValueError(f"expected {_RAW_SIDE * _RAW_SIDE} bytes, got {len(raw)}")
    pixels: list[int] = []
    for by in range(IMG):
        for bx in range(IMG):
            total = 0
            for dy in range(_POOL):
                y = _CROP_OFFSET + by * _POOL + dy
                row = y * _RAW_SIDE
                x0 = _CROP_OFFSET + bx * _POOL
                total += raw[row + x0] + raw[row + x0 + 1] + raw[row + x0 + 2]
            pixels.append((total // 9) >> 4)
    return pixels


def pack_image(pixels: list[int], label: int) -> list[int]:
    """64 nibbles (0..15) + a label -> five ROM-safe words.

    Pixel ``p`` lives in nibble ``p % 15`` of word ``p // 15``: words 0-3 hold
    fifteen pixels each, word 4 holds pixels 60-63 in its low four nibbles and
    the label in nibble 4 (the first slot free once the pixels are placed).
    """
    if len(pixels) != IMG * IMG:
        raise ValueError(f"expected {IMG * IMG} pixels, got {len(pixels)}")
    if not all(0 <= p <= 15 for p in pixels):
        raise ValueError("pixel values must be nibbles in 0..15")
    if not (0 <= label <= 15):
        raise ValueError("label must be a nibble in 0..15")

    words = [0] * WORDS_PER_IMAGE
    for p, pixel in enumerate(pixels):
        words[p // NIBBLES_PER_WORD] |= pixel << (NIBBLE_BITS * (p % NIBBLES_PER_WORD))
    words[4] |= label << (NIBBLE_BITS * 4)

    for w in words:
        # The reason for fifteen nibbles, not sixteen: this must never fire.
        if not (0 <= w <= _MAX_WORD):
            raise ValueError(f"packed word {w} is not a representable ROM literal")
    return words


def unpack_image(words: list[int]) -> tuple[list[int], int]:
    """Inverse of :func:`pack_image`."""
    if len(words) != WORDS_PER_IMAGE:
        raise ValueError(f"expected {WORDS_PER_IMAGE} words, got {len(words)}")
    mask = (1 << NIBBLE_BITS) - 1
    pixels = [
        (words[p // NIBBLES_PER_WORD] >> (NIBBLE_BITS * (p % NIBBLES_PER_WORD))) & mask
        for p in range(IMG * IMG)
    ]
    label = (words[4] >> (NIBBLE_BITS * 4)) & mask
    return pixels, label


def _read_idx_images(data: bytes) -> list[bytes]:
    magic, n, rows, cols = struct.unpack(">IIII", data[:16])
    if magic != 2051:
        raise ValueError(f"not an idx3 image file (magic {magic})")
    size = rows * cols
    body = data[16:]
    return [body[i * size : (i + 1) * size] for i in range(n)]


def _read_idx_labels(data: bytes) -> list[int]:
    magic, n = struct.unpack(">II", data[:8])
    if magic != 2049:
        raise ValueError(f"not an idx1 label file (magic {magic})")
    return list(data[8 : 8 + n])


def _pack_split(images: list[bytes], labels: list[int], n: int) -> list[int]:
    words: list[int] = []
    for img, label in zip(images[:n], labels[:n], strict=False):
        words.extend(pack_image(downsample(img), label))
    return words


def _write_artifact(dest: Path, train_words: list[int], val_words: list[int]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        f.write(_MAGIC)
        f.write(struct.pack("<II", len(train_words), len(val_words)))
        for w in train_words:
            f.write(struct.pack("<Q", w))
        for w in val_words:
            f.write(struct.pack("<Q", w))


def _read_artifact(src: Path) -> tuple[list[int], list[int]]:
    data = src.read_bytes()
    magic, data = data[:8], data[8:]
    if magic != _MAGIC:
        raise ValueError(f"{src}: not an LM1MNIST artifact")
    n_train, n_val = struct.unpack("<II", data[:8])
    data = data[8:]
    train = list(struct.unpack(f"<{n_train}Q", data[: n_train * 8]))
    data = data[n_train * 8 :]
    val = list(struct.unpack(f"<{n_val}Q", data[: n_val * 8]))
    return train, val


def load_packed() -> tuple[list[int], list[int]]:
    """Read the vendored artifact: ``(train_words, val_words)``.

    Lengths are ``N_TRAIN * WORDS_PER_IMAGE`` and ``N_VAL * WORDS_PER_IMAGE``.
    """
    if not _ARTIFACT.exists():
        raise FileNotFoundError(
            f"{_ARTIFACT} is missing. Run "
            "`cd solvers/python && uv run python -m randomfun2026solvers.mnist_data fetch` "
            "to download and vendor it."
        )
    return _read_artifact(_ARTIFACT)


def _download(name: str) -> bytes:
    with urllib.request.urlopen(_MIRROR + name) as resp:  # noqa: S310 - fixed https mirror
        return resp.read()


def fetch_and_vendor(dest: Path) -> str:
    """Download MNIST, preprocess, pack, and write the artifact at ``dest``.

    Train subset is the first ``N_TRAIN`` of the official train split; validation
    is the first ``N_VAL`` of the official test split — no seed needed, since the
    selection is a deterministic prefix. Returns the sha256 of the four
    concatenated *source* idx files (before any preprocessing), so the sidecar
    hash pins exactly what was downloaded, not just what was written.
    """
    raw_train_images = _download(_TRAIN_IMAGES)
    raw_train_labels = _download(_TRAIN_LABELS)
    raw_val_images = _download(_VAL_IMAGES)
    raw_val_labels = _download(_VAL_LABELS)

    digest = hashlib.sha256(
        raw_train_images + raw_train_labels + raw_val_images + raw_val_labels
    ).hexdigest()

    train_images = _read_idx_images(gzip.decompress(raw_train_images))
    train_labels = _read_idx_labels(gzip.decompress(raw_train_labels))
    val_images = _read_idx_images(gzip.decompress(raw_val_images))
    val_labels = _read_idx_labels(gzip.decompress(raw_val_labels))

    train_words = _pack_split(train_images, train_labels, N_TRAIN)
    val_words = _pack_split(val_images, val_labels, N_VAL)

    dest = Path(dest)
    _write_artifact(dest, train_words, val_words)
    dest.with_name(dest.name + ".sha256").write_text(digest + "\n")
    return digest


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m randomfun2026solvers.mnist_data fetch``."""
    args = sys.argv[1:] if argv is None else argv
    if args != ["fetch"]:
        print("usage: python -m randomfun2026solvers.mnist_data fetch", file=sys.stderr)
        return 2
    digest = fetch_and_vendor(_ARTIFACT)
    print(f"wrote {_ARTIFACT} ({_ARTIFACT.stat().st_size} bytes), sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
