"""The binding-band tool must agree with a grid nobody generated from it.

`reverse_pairring.zone_map` is only useful if it predicts where a `r` or an `s`
actually binds.  Checking it against a machine *this* module produced would be
circular, so it is checked against the shipped `reverse_list_ast` worker, whose
block placement was chosen by hand long before the tool existed: its load body
reads the input pipe, its rotate body reads the ring, and its emit `s` writes
the output pipe.
"""

from __future__ import annotations

from randomfun2026solvers.reverse_pairring import AST_WORKER, binding, zone_map


def test_the_shipped_worker_binds_where_its_blocks_stand() -> None:
    # `counted_loop_horizontal(6, 1, "rs")` puts LOAD's r/s on circuit row 2.
    assert binding(AST_WORKER, 8, 2) == ("input", "ring_out")
    # `counted_loop_horizontal(6, 7, "rs")` puts ROTATE's r/s on row 8.
    assert binding(AST_WORKER, 8, 8) == ("ring_in", "ring_out")
    # EMIT reads at (8, 9) and, after walking west, sends at (4, 9).
    assert binding(AST_WORKER, 8, 9) == ("ring_in", "output")
    assert binding(AST_WORKER, 4, 9) == ("ring_in", "output")


def test_east_wall_anchors_collapse_the_map_to_rows() -> None:
    """Every anchor on one wall means the column term cancels: bands, not
    quadrants — which is why that worker has no cell where `r` is the input
    pipe and `s` is the output pipe."""
    rows = zone_map(AST_WORKER).splitlines()
    assert all(len(set(row)) == 1 for row in rows), rows
    assert "T" not in zone_map(AST_WORKER)
