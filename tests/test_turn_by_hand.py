"""Turning the board with the mouse: the round handle above it on the bed.
A quarter turn at a time; with Control held, freely (half a degree at a
time). The board turns about its own middle, the toolpaths follow, and the
angle survives a setup saved and loaded."""
import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QMouseEvent

from gerber2rml.gui2.window import MainWindow

FIXT = Path(__file__).parent / "fixtures" / "mosfet_test"


@pytest.fixture
def win(qt_app, monkeypatch, tmp_path):
    from gerber2rml import platform as plat
    monkeypatch.setattr(plat, "documents_dir", lambda *a, **k: tmp_path)
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.load_folder(str(FIXT))
    qt_app.processEvents()
    yield w
    w.close()


def _ev(kind, pos, mods=Qt.NoModifier):
    t = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove,
         "release": QEvent.MouseButtonRelease}[kind]
    held = Qt.NoButton if kind == "release" else Qt.LeftButton
    return QMouseEvent(t, pos, pos, Qt.LeftButton, held, mods)


def _turn(win, deg, mods=Qt.NoModifier):
    sg = win.stage
    hp, (cx, cy) = sg._turn_handle()
    sg.mousePressEvent(_ev("press", hp, mods))
    start = sg.to_mm(hp)
    a = math.atan2(start.y() - cy, start.x() - cx) + math.radians(deg)
    to = sg.to_px(cx + 30 * math.cos(a), cy + 30 * math.sin(a))
    sg.mouseMoveEvent(_ev("move", to, mods))
    sg.mouseReleaseEvent(_ev("release", to, mods))


def _centre(win):
    b = win.work_bounds()
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def test_a_drag_round_the_handle_turns_a_quarter_turn_about_the_middle(win):
    before = _centre(win)
    _turn(win, 80)
    assert win.state.rotate == 90
    assert _centre(win) == pytest.approx(before, abs=0.01)


def test_control_turns_it_freely(win):
    for mods in (Qt.MetaModifier, Qt.ControlModifier):   # Mac's Control, PC's
        win.action_rotate(0)
        _turn(win, 12.3, mods)
        assert win.state.rotate == pytest.approx(12.5)
    paths = win._toolpaths_for(next(s for s in win.plan.steps
                                     if s.op == "traces"))[0]
    assert paths                                  # it still cuts


def test_a_free_angle_survives_a_setup_saved_and_loaded(win, tmp_path):
    win.action_rotate(33.5, fine=True)
    path = tmp_path / "turned.srmcam"
    win.write_setup(path)
    win.action_rotate(0)
    win.load_setup(path)
    assert win.state.rotate == pytest.approx(33.5)


def test_undo_takes_a_turn_back(win):
    _turn(win, 12.3, Qt.MetaModifier)
    win.action_undo()
    assert win.state.rotate == 0
