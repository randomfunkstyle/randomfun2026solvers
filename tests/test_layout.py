from __future__ import annotations

import pytest
from randomfun2026solvers.layout import (
    _ARROW,
    Container,
    Edge,
    Graph,
    LayoutError,
    layout_graph,
    score,
)


def _box(cid: str, w: int, h: int, *, inputs=None, outputs=None) -> Container:
    content = ["+" + "-" * (w - 2) + "+"]
    content += ["|" + " " * (w - 2) + "|" for _ in range(h - 2)]
    content += ["+" + "-" * (w - 2) + "+"]
    return Container(
        id=cid,
        width=w,
        height=h,
        content=content,
        inputs=inputs or [],
        outputs=outputs or [],
    )


def _all_path_cells(layout) -> list[tuple[int, int]]:
    cells: list[tuple[int, int]] = []
    for r in layout.routes:
        cells.extend(tuple(c) for c in r.path)
    return cells


def test_single_chain_connects_ports():
    # Ports sit inside the box; pipes attach at the nearest border cell.
    a = _box("A", 5, 3, outputs=[(3, 1)])
    b = _box("B", 5, 3, inputs=[(1, 1)])
    g = Graph(containers=[a, b], edges=[Edge(id="e0", src="A", dst="B")])

    layout = layout_graph(g)

    assert layout.grid and layout.height > 0
    assert len(layout.routes) == 1
    r = layout.routes[0]
    assert r.resolved_output == 0
    assert r.resolved_input == 0
    # First glyph points outward, last steps into the target.
    text = layout.render()
    first = _cell_char(text, r.path[0])
    last = _cell_char(text, r.path[-1])
    assert first in _ARROW.values()
    assert last in _ARROW.values()


def test_multi_input_resolves_to_intended_port():
    # D has two interior inputs; each source must land nearest its own.
    s0 = _box("S0", 5, 3, outputs=[(3, 1)])
    s1 = _box("S1", 5, 3, outputs=[(3, 1)])
    d = _box("D", 5, 5, inputs=[(1, 1), (1, 3)])
    g = Graph(
        containers=[s0, s1, d],
        edges=[
            Edge(id="e0", src="S0", dst="D", dst_input=0),
            Edge(id="e1", src="S1", dst="D", dst_input=1),
        ],
    )

    layout = layout_graph(g)

    resolved = {r.edge_id: r.resolved_input for r in layout.routes}
    assert resolved == {"e0": 0, "e1": 1}


def test_no_cell_is_shared_between_pipes():
    a = _box("A", 5, 3, outputs=[(3, 1)])
    b = _box("B", 5, 3, inputs=[(1, 1)], outputs=[(3, 1)])
    c = _box("C", 5, 3, inputs=[(1, 1)])
    g = Graph(
        containers=[a, b, c],
        edges=[
            Edge(id="e0", src="A", dst="B"),
            Edge(id="e1", src="B", dst="C"),
        ],
    )

    layout = layout_graph(g)

    cells = _all_path_cells(layout)
    # Pipes only share the fixed shared endpoints where they legitimately meet
    # a container; distinct pipes here touch different containers, so no repeats.
    assert len(cells) == len(set(cells))
    assert score(layout) > 0


def test_cycle_is_rejected():
    a = _box("A", 5, 3, inputs=[(1, 1)], outputs=[(3, 1)])
    b = _box("B", 5, 3, inputs=[(1, 1)], outputs=[(3, 1)])
    g = Graph(
        containers=[a, b],
        edges=[
            Edge(id="e0", src="A", dst="B"),
            Edge(id="e1", src="B", dst="A"),
        ],
    )
    with pytest.raises(LayoutError):
        layout_graph(g)


def test_bad_edge_port_rejected():
    a = _box("A", 5, 3, outputs=[(3, 1)])
    b = _box("B", 5, 3, inputs=[(1, 1)])
    with pytest.raises(LayoutError):
        Graph(containers=[a, b], edges=[Edge(id="e0", src="A", dst="B", dst_input=5)])


def _cell_char(text: str, cell) -> str:
    x, y = cell
    rows = text.split("\n")
    # render() trims and is offset to the canvas bbox origin; recompute via layout
    # is unnecessary here because the single-chain fixture starts at origin (0,0).
    if 0 <= y < len(rows) and 0 <= x < len(rows[y]):
        return rows[y][x]
    return " "
