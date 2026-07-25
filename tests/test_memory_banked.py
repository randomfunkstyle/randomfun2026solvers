"""The ``Y`` split and ``addr - 50`` bank selector, on real engines."""

from __future__ import annotations

from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.memory_banked import build_bucket_probe


def test_y_bucket_probe_selects_exactly_one_local_address() -> None:
    machine = FastLittleman("\n".join(build_bucket_probe()))

    for addr in range(100):
        result = machine.run([addr], max_ticks=100)
        assert result.fatal is None, (addr, result.fatal)
        assert result.halted
        assert result.output == [addr % 50]


def test_y_bucket_probe_matches_reference_at_both_sides_of_the_cut() -> None:
    source = "\n".join(build_bucket_probe())

    for addr in (0, 49, 50, 99):
        reference = Littleman().run(source, input=[addr], max_ticks=100)
        assert reference.fatal is None
        assert list(reference.output) == [addr % 50]

