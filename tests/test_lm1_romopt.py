"""Fast policy tests for deterministic ROM reshaping."""

from randomfun2026solvers.lm1.romopt import neighbor_rows


def test_auto_narrows_a_width_bound_rom_first() -> None:
    assert neighbor_rows(5, width=100, height=80) == (6, 4)


def test_auto_widens_a_height_bound_rom_first() -> None:
    assert neighbor_rows(5, width=80, height=100) == (4, 6)


def test_explicit_shape_and_one_row_floor() -> None:
    assert neighbor_rows(1, width=80, height=100, shape="widen") == ()
    assert neighbor_rows(1, width=100, height=80, shape="narrow") == (2,)
