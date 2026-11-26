"""Novice's tool: flat or ball (How it is made) and how thick - no profile
list, which said the first over again and could be set against it."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

from gerber2rml.engine import method as fab
from gerber2rml.gui2 import tier
from gerber2rml.gui2.window import MainWindow

FIXT = Path(__file__).parent / "fixtures" / "mosfet_test"


@pytest.fixture
def win(qt_app, monkeypatch, tmp_path):
    from gerber2rml import platform as plat
    monkeypatch.setattr(plat, "documents_dir", lambda *a, **k: tmp_path)
    monkeypatch.setenv("SRM_CAM_MODE", "novice")    # conftest pins Pro
    w = MainWindow()
    w.show()
    w.load_folder(str(FIXT))
    yield w
    w.close()


def test_novice_has_a_bit_diameter_and_no_profile_list(win, monkeypatch):
    s = win.inspector.setup
    s.sync()
    assert not tier.is_full()
    assert s.preset_field.isHidden() and s.preset_row.isHidden()
    assert not s.bit_dia_field.isHidden()
    monkeypatch.setenv("SRM_CAM_MODE", "pro")
    win._sync_tier()
    s.sync()
    assert not s.preset_field.isHidden()
    assert s.bit_dia_field.isHidden()


@pytest.mark.parametrize("mid, kind", [(fab.BALL, "ball"), (fab.FLAT, "flat")])
def test_the_diameter_is_one_bit_for_the_whole_job(win, mid, kind):
    win.action_method(mid)
    s = win.inspector.setup
    s.sync()
    s.bit_dia.setValue(1.0)
    st = win.state
    assert (st.trace.bit_diameter, st.drill.bit_diameter,
            st.cutout.bit_diameter) == (1.0, 1.0, 1.0)
    assert win.plan.tools == [(kind, 1.0)]         # one bit: the run starts
    assert win.method_id() == mid


def test_a_ball_mills_channel_follows_its_diameter_and_depth(win):
    win.action_method(fab.BALL)
    s = win.inspector.setup
    s.sync()
    assert s.bit_dia.value() == pytest.approx(0.8)
    assert win.state.trace.effective_diameter() == pytest.approx(0.505,
                                                                 abs=0.001)
    s.bit_dia.setValue(1.0)
    assert 0.505 < win.state.trace.effective_diameter() < 1.0
