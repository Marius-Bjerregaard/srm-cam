"""A project file: the whole job in one file - the Gerber files, the setup
and its measurements, and what the Run it page holds - that opens again on
a computer that has none of it."""
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from gerber2rml.engine import bracket as br
from gerber2rml.engine.fiducial import Transform
from gerber2rml.gui2 import workspace
from gerber2rml.gui2.window import MainWindow

FIXT = Path(__file__).parent / "fixtures" / "mosfet_test"


@pytest.fixture
def win(qt_app, monkeypatch, tmp_path):
    from gerber2rml import platform as plat
    monkeypatch.setattr(plat, "documents_dir", lambda *a, **k: tmp_path)
    workspace.set_setting("autorun/nogo", "")
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.autorun_page.set_bracket(br.DEFAULT)
    yield w
    w.autorun_page._clear_nogo()
    w.close()


def _job(win, tmp_path):
    """A job with something of everything: its own Gerber folder, numbers
    changed, a measured flip, zones, a clamp, a remembered copper height."""
    src = tmp_path / "my board"
    shutil.copytree(FIXT, src)
    win.load_folder(str(src))
    win.action_double_sided(True)
    win.state.trace.cut_depth = 0.17
    win._top_fit = Transform(theta=0.01, scale=1.0, tx=0.3, ty=-0.2)
    win._fid_measured = [(10.0, 11.0), None]
    page = win.autorun_page
    page.w_spin.setValue(123.0)
    page.h_spin.setValue(97.0)
    page.level_margin_spin.setValue(7.5)
    page.centre_chk.setChecked(False)   # Centre on would place it itself
    page._nogo = [(5.0, 6.0, 15.0, 16.0), (80.0, 12.0, 26.0)]   # box, clamp
    page._save_nogo()
    workspace.set_setting("autorun/surface_z", "-41.250")
    # Last: ticking Centre puts the board in the middle of the sheet.
    win.state.set_placement(31.0, 22.0)
    return src


def test_a_project_holds_the_gerbers_and_opens_without_the_folder(
        win, tmp_path):
    src = _job(win, tmp_path)
    out = tmp_path / "out" / "job.srmproj"
    out.parent.mkdir()
    assert win.export_project(str(out))
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        data = json.loads(z.read("setup.srmcam"))
    # The Gerber files themselves, and a setup that points at them beside it.
    assert any(n.startswith("gerber/0_") and n.endswith(".drl") for n in names)
    assert not Path(data["gerber_dir"]).is_absolute()
    assert len([n for n in names if n.startswith("gerber/")]) == len(
        [f for f in src.iterdir() if f.is_file()])
    # The original folder is gone, and everything on this PC is different.
    shutil.rmtree(src)
    page = win.autorun_page
    page._clear_nogo()
    page.w_spin.setValue(50.0)
    page.h_spin.setValue(50.0)
    page.level_margin_spin.setValue(2.0)
    workspace.set_setting("autorun/surface_z", "")
    win._top_fit = None
    win.action_double_sided(False)
    win.state.trace.cut_depth = 0.1

    assert win.import_project(str(out))
    st = win.state
    assert st.board is not None and st.board.holes
    assert Path(st.gerber_dir).is_dir()
    assert workspace.workspace_root() in Path(st.gerber_dir).parents
    assert (st.place_x, st.place_y) == pytest.approx((31.0, 22.0))
    assert st.trace.cut_depth == pytest.approx(0.17)
    assert win._double
    assert win._top_fit.tx == pytest.approx(0.3)
    assert win._fid_measured == [(10.0, 11.0), None]
    assert (page.w_spin.value(), page.h_spin.value()) == (123.0, 97.0)
    assert page.level_margin_spin.value() == 7.5
    assert not page.centre_chk.isChecked()
    assert page._nogo == [(5.0, 6.0, 15.0, 16.0), (80.0, 12.0, 26.0)]
    assert float(workspace.setting("autorun/surface_z")) == -41.25


def test_opening_the_same_project_twice_keeps_both_copies(win, tmp_path):
    _job(win, tmp_path)
    out = tmp_path / "job.srmproj"
    assert win.export_project(str(out))
    assert win.import_project(str(out))
    first = Path(win.state.gerber_dir)
    assert win.import_project(str(out))
    assert Path(win.state.gerber_dir) != first and first.is_dir()


def test_a_file_that_is_not_a_project_is_refused(win, tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(win, "report_error", lambda *a, **k: said.append(a))
    bad = tmp_path / "bad.srmproj"
    bad.write_text("not a zip")
    assert win.import_project(str(bad)) is False
    # Nor one whose names climb out of the project's folder.
    evil = tmp_path / "evil.srmproj"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("setup.srmcam", "{}")
        z.writestr("../outside.txt", "x")
    assert win.import_project(str(evil)) is False
    assert not (workspace.workspace_root() / "outside.txt").exists()
    assert len(said) == 2
