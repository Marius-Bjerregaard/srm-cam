"""Edge.Cuts with windows inside the board - an octagonal cut-out and round
holes drawn on Edge.Cuts - beside an outer edge with half-round notches.

The window closed as a polygon before the outer edge did (its arcs only
close once snapped), and the largest polygon of that first attempt was
taken for the board: the octagon became the outline and the board round it
was lost (2026-11-20). Plotted by KiCad 10.0.3's kicad-cli."""
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from gerber2rml import loader
from gerber2rml.config import CutoutJob
from gerber2rml.engine.cutout import cut_outline

FIXT = Path(__file__).parent / "fixtures" / "edge_windows"


@pytest.fixture(scope="module")
def board():
    return loader.load_board(str(FIXT))


def test_the_outline_is_the_whole_board_not_a_window(board):
    x0, y0, x1, y1 = board.outline.bounds
    assert (x1 - x0, y1 - y0) == pytest.approx((70.0, 35.0), abs=0.05)


def test_the_windows_are_holes_in_it(board):
    windows = sorted(Polygon(h).area for h in board.outline.interiors)
    assert len(windows) == 4                     # the octagon and 3 circles
    assert windows[-1] == pytest.approx(229.1, abs=0.5)   # the octagon
    # The board's own area: 70 x 35, less the two notches and the windows.
    assert board.outline.area == pytest.approx(2148.5, abs=1.0)


def test_the_windows_are_cut_out(board):
    paths = cut_outline(board.outline, CutoutJob())
    xs = [m.x for p in paths for m in p]
    # Some cut runs inside the octagon's 18 mm span round its centre.
    cx = (board.outline.bounds[0] + board.outline.bounds[2]) / 2.0
    assert len(paths) > 4 and any(abs(x - (cx - 10)) < 9 for x in xs)
