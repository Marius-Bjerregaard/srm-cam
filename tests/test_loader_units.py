"""The loader in mm whatever unit the files are in, curved copper, and one
board's holes only (review of 2026-11-20).

The boards are written here as text: small enough to read, and each says in
its own header which unit it is in.
"""
import math
import warnings

import pytest

from gerber2rml.loader import load_board

# 30 x 20 mm board, plotted in mm.
EDGE_MM = """%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.100000*%
D10*
X0Y0D02*
X30000000Y0D01*
X30000000Y20000000D01*
X0Y20000000D01*
X0Y0D01*
M02*
"""

# A 0.25 mm track and a 1.6 mm round pad at (10, 10), in mm.
BCU_MM = """%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.250000*%
%ADD11C,1.600000*%
D10*
X2000000Y2000000D02*
X8000000Y2000000D01*
D11*
X10000000Y10000000D03*
M02*
"""

# The same board plotted in inches: 1.5 x 1.0 in outline, a 0.010 in track,
# a 0.100 x 0.050 in rectangular pad at (0.5, 0.5) in.
EDGE_IN = """%FSLAX46Y46*%
%MOIN*%
%ADD10C,0.004000*%
D10*
X0Y0D02*
X1500000Y0D01*
X1500000Y1000000D01*
X0Y1000000D01*
X0Y0D01*
M02*
"""

BCU_IN = """%FSLAX46Y46*%
%MOIN*%
%ADD10C,0.010000*%
%ADD11R,0.100000X0.050000*%
D10*
X100000Y100000D02*
X400000Y100000D01*
D11*
X500000Y500000D03*
M02*
"""

# An inch drill file as KiCad writes one when its drill dialog is set to
# inches: a 1.0 mm (0.0394 in) hole at (0.5, 0.5) in and a 0.8 mm
# (0.0315 in) one at (1.0, 0.25) in.
DRL_IN = """M48
; DRILL file {KiCad 8.0.0}
; FORMAT={-:-/ absolute / inch / decimal}
FMAT,2
INCH
T1C0.0394
T2C0.0315
%
G90
G05
T1
X0.5Y0.5
T2
X1.0Y0.25
M30
"""


def _write(folder, files):
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text)
    return folder


def _load(folder):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")       # gerbonara's bare-INCH note
        return load_board(folder, mirror=False)


def _assert_inch_holes(holes):
    holes = sorted(holes)
    assert len(holes) == 2
    (x1, y1, d1), (x2, y2, d2) = holes
    assert (x1, y1) == pytest.approx((12.7, 12.7), abs=1e-6)
    assert d1 == pytest.approx(0.0394 * 25.4, abs=1e-6)
    assert (x2, y2) == pytest.approx((25.4, 6.35), abs=1e-6)
    assert d2 == pytest.approx(0.0315 * 25.4, abs=1e-6)


def test_an_inch_drill_file_beside_mm_gerbers_is_read_in_mm(tmp_path):
    b = _load(_write(tmp_path, {"t-Edge_Cuts.gbr": EDGE_MM,
                                "t-B_Cu.gbr": BCU_MM,
                                "t-PTH.drl": DRL_IN}))
    assert b.outline.bounds == pytest.approx((0, 0, 30, 20), abs=1e-6)
    _assert_inch_holes(b.holes)


def test_an_all_inch_board_is_read_in_mm(tmp_path):
    b = _load(_write(tmp_path, {"t-Edge_Cuts.gbr": EDGE_IN,
                                "t-B_Cu.gbr": BCU_IN,
                                "t-PTH.drl": DRL_IN}))
    assert b.outline.bounds == pytest.approx((0, 0, 38.1, 25.4), abs=1e-6)
    _assert_inch_holes(b.holes)
    # The track: 2.54 .. 10.16 mm long, 0.254 mm wide, round ends.
    x0, y0, x1, y1 = b.copper.bounds
    assert x0 == pytest.approx(2.54 - 0.127, abs=1e-3)
    assert y0 == pytest.approx(2.54 - 0.127, abs=1e-3)
    # The pad: 2.54 x 1.27 mm centred on (12.7, 12.7).
    assert x1 == pytest.approx(12.7 + 1.27, abs=1e-6)
    assert y1 == pytest.approx(12.7 + 0.635, abs=1e-6)


def test_a_mm_board_loads_as_before(tmp_path):
    b = _load(_write(tmp_path, {"t-Edge_Cuts.gbr": EDGE_MM,
                                "t-B_Cu.gbr": BCU_MM}))
    assert b.copper.bounds == pytest.approx((1.875, 1.875, 10.8, 10.8),
                                            abs=1e-3)


# A curved track (KiCad 6+): 0.3 mm wide, a half circle of radius 5 mm
# from (15, 5) round to (15, 15) about (15, 10).
BCU_ARC = """%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.300000*%
D10*
X5000000Y5000000D02*
X15000000Y5000000D01*
G75*
G03*
X15000000Y15000000I0J5000000D01*
G01*
X5000000Y15000000D01*
M02*
"""

# A filled region whose right edge is a half circle: a 10 x 10 mm square
# with a semicircle of radius 5 mm on it.
BCU_ARC_REGION = """%FSLAX46Y46*%
%MOMM*%
G36*
X0Y0D02*
X10000000Y0D01*
G75*
G03*
X10000000Y10000000I0J5000000D01*
G01*
X0Y10000000D01*
X0Y0D01*
G37*
M02*
"""


def test_a_curved_track_is_copper(tmp_path):
    b = _load(_write(tmp_path, {"a-Edge_Cuts.gbr": EDGE_MM.replace(
        "X30000000", "X40000000"), "a-B_Cu.gbr": BCU_ARC}))
    # The arc's far point is (20, 10); the track there is copper, 0.15 mm
    # either side of the curve. Skipped, the two straight tracks ended at
    # x=15 and the isolation cut ran across the curve.
    assert b.copper.bounds[2] == pytest.approx(20.15, abs=0.01)
    from shapely.geometry import Point
    assert b.copper.contains(Point(20.0, 10.0))
    assert b.copper.contains(Point(15 + 5 * math.cos(math.radians(45)),
                                   10 - 5 * math.sin(math.radians(45))))


def test_a_region_with_an_arc_edge_follows_the_arc(tmp_path):
    b = _load(_write(tmp_path, {"r-Edge_Cuts.gbr": EDGE_MM,
                                "r-B_Cu.gbr": BCU_ARC_REGION}))
    # 100 mm2 of square + 39.27 mm2 of half disc; a chord would give 100.
    assert b.copper.area == pytest.approx(100 + math.pi * 25 / 2, rel=1e-3)
    assert b.copper.bounds[2] == pytest.approx(15.0, abs=0.01)


# Two boards plotted into one folder, each with its own drill file.
DRL_MM = """M48
; DRILL file {KiCad 8.0.0}
; FORMAT={-:-/ absolute / metric / decimal}
METRIC
T1C1.000
%
G90
G05
T1
X@X@Y@Y@
M30
"""


def _drl(x, y):
    return DRL_MM.replace("@X@", str(x)).replace("@Y@", str(y))


def test_two_boards_in_one_folder_keep_their_own_holes(tmp_path):
    folder = _write(tmp_path, {
        "a-Edge_Cuts.gbr": EDGE_MM, "a-B_Cu.gbr": BCU_MM,
        "a-PTH.drl": _drl(5.0, 5.0),
        "b-Edge_Cuts.gbr": EDGE_MM, "b-B_Cu.gbr": BCU_MM,
        "b-PTH.drl": _drl(25.0, 15.0),
    })
    b = _load(folder)                          # the first board: "a"
    assert [(round(x, 3), round(y, 3)) for x, y, _ in b.holes] == [(5.0, 5.0)]


def test_one_drill_file_of_another_name_is_still_read(tmp_path):
    folder = _write(tmp_path, {
        "a-Edge_Cuts.gbr": EDGE_MM, "a-B_Cu.gbr": BCU_MM,
        "drills.drl": _drl(5.0, 5.0),
    })
    b = _load(folder)
    assert [(round(x, 3), round(y, 3)) for x, y, _ in b.holes] == [(5.0, 5.0)]
