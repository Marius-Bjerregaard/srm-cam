"""Regression tests for the review of 2026-11-20 - one per fix, each named
for what used to go wrong."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def test_a_jog_during_a_run_does_not_cancel_its_stop():
    """STOP during a run, then Page Up (which the bar itself suggests): the
    Page Up used to clear the abort the run polls, and the run cut on."""
    from gerber2rml.gui2.machine import MachineLink
    link = MachineLink()
    failed = []
    link.op_failed.connect(lambda n, m: failed.append((n, m)))
    link.mark_external(True)                 # the run has the port
    assert link.stop_now()
    assert link.should_abort()
    link.jog_by(dz_mm=1.0)
    link.jog_z(1.0)
    link.jog_to(10.0, 10.0)
    assert link.should_abort()               # the STOP still stands
    assert [n for n, _m in failed] == ["jog_by", "jog_z", "jog_xy"]
    # ...and no step was left waiting to block the next jog.
    assert not link._jog_queued and link._jog_pending == [0.0, 0.0, 0.0]


def test_a_jog_without_a_link_leaves_no_step_waiting():
    from gerber2rml.gui2.machine import MachineLink
    link = MachineLink()
    link.jog_by(dz_mm=1.0)
    assert not link._jog_queued and link._jog_pending == [0.0, 0.0, 0.0]


class _Printer:
    def __init__(self):
        self.purged = 0

    def send(self, data, name="job"):
        return 1

    def pending(self):
        return 0

    def purge(self):
        self.purged += 1
        return True


def test_a_link_failure_mid_job_purges_the_job(monkeypatch):
    """The Arduino's USB dropping mid-job used to leave run() with the job
    still in the spooler: over on the screen, cutting on at the mill."""
    import pytest
    from gerber2rml.engine import runner, spi_probe

    def boom(_ser):
        raise OSError("the USB lead came out")
    monkeypatch.setattr(spi_probe, "machine_status", boom)
    for name in ("stop_moving_now", "cancel_job", "spindle_off", "send_abort"):
        monkeypatch.setattr(spi_probe, name, lambda _s: None)
    p = _Printer()
    r = runner.JobRunner(object(), p, lambda: False)
    with pytest.raises(OSError):
        r.run("PU0,0;", "traces", end_mm=(0.0, 0.0))
    assert p.purged == 1


def test_a_silent_arduino_is_not_a_finished_job(monkeypatch):
    """No status and no position read as "not moving, at the end": two
    seconds of a dead link ended the job while the mill cut on."""
    import pytest
    from gerber2rml.engine import runner, spi_probe
    monkeypatch.setattr(spi_probe, "machine_status", lambda _s: None)
    monkeypatch.setattr(spi_probe, "query_position", lambda _s: None)
    for name in ("stop_moving_now", "cancel_job", "spindle_off", "send_abort"):
        monkeypatch.setattr(spi_probe, name, lambda _s: None)
    monkeypatch.setattr(runner.JobRunner, "BLIND_S", 0.3)
    p = _Printer()
    r = runner.JobRunner(object(), p, lambda: False, poll_s=0.01)
    with pytest.raises(runner.RunStopped, match="stopped answering"):
        r.run("PU0,0;", "traces", end_mm=(0.0, 0.0))
    assert p.purged == 1


def test_a_touch_over_a_hole_does_not_step_down_into_the_bed():
    """With the copper's height measured this run, a clean miss means no
    copper here (a hole): stepping 5.5 mm on down drove the bit through the
    hole into the bed."""
    import math
    import pytest
    from gerber2rml.engine import simmachine, stockfind
    m = simmachine.machine(angle_deg=0.0, corner=(20000, 20000))
    cx, cy = 50000, 40000                    # a 3.2 mm hole through the sheet
    for r in range(0, 1300, 200):
        for k in range(24):
            a = 2 * math.pi * k / 24
            u, v = cx + r * math.cos(a), cy + r * math.sin(a)
            m.cuts.setdefault((int(u // 200), int(v // 200)), []).append(
                (u, v, 400, (0, 1)))
    ser = simmachine.serial_for("SIM")
    x, y = m.to_machine(cx, cy)
    lows = []
    orig = m.move_to

    def spy(x_, y_, z_, *a, **k):
        lows.append(z_)
        return orig(x_, y_, z_, *a, **k)
    m.move_to = spy
    with pytest.raises(stockfind.StockNotFound, match="over a hole"):
        stockfind.seek_surface(ser, int(x), int(y), hint_um=m.surface_z,
                               hint_fresh=True)
    assert m.surface_z - min(lows) <= 1500    # never past the one probe


def test_a_holes_reference_touch_keeps_off_the_next_hole(monkeypatch):
    """Header pins are 2.54 mm apart: the touch 2.5 mm out landed in the next
    hole, 3 mm below the copper. Holes to avoid are skipped, and the touch
    goes further out instead."""
    import types
    import pytest
    from shapely.geometry import Point
    from gerber2rml.engine import autorun as ar, stockfind
    from gerber2rml.engine.spi_probe import ProbeError
    run = ar.AutoRun.__new__(ar.AutoRun)
    run.nogo, run.ser = None, None
    run.should_abort = lambda: False
    run.pose = run.pose_top = types.SimpleNamespace(z=-40.0)
    run._hop = lambda x, y: None
    touched = []

    def ref(_ser, x, y, _abort, _z):
        touched.append((x / 1000.0, y / 1000.0))
        raise ProbeError("no copper")
    monkeypatch.setattr(stockfind, "reference_at", ref)
    nxt = Point(52.54, 30.0).buffer(0.5 + 0.8 + 0.4)   # the next pin's hole
    with pytest.raises(ar.RunFailed):
        run._locate(50.0, 30.0, avoid=nxt)
    assert touched and (52.5, 30.0) not in touched
    assert not any(nxt.intersects(Point(p)) for p in touched)


def test_a_turned_surface_grid_keeps_its_bow(monkeypatch):
    """Turned by a found angle, the grid's points were no longer on rows
    and columns, and the map fell back to a plane: a 0.2 mm bow became
    0.03 mm. Built on the nominal grid and asked through the turn, the bow
    is kept."""
    import math
    from gerber2rml.engine import autorun as ar
    from gerber2rml.engine.leveling import HeightMap
    nx, ny = ar.LEVEL_GRID
    nominal, _ = ar.level_grid((30, 30, 130, 110), 2.0)
    t = ar.about(20, 20, 1.2)
    pts = [t.apply(*p) for p in nominal]
    bump = {i: 0.2 for i in range(len(pts))
            if 0 < i % nx < nx - 1 and 0 < i // nx < ny - 1}
    flat = HeightMap.from_points(
        [(nominal[i][0], nominal[i][1], bump.get(i, 0.0))
         for i in range(len(pts))], nx, ny)
    h = HeightMap(lambda x, y: flat(*ar._undo(t, x, y)))
    i = next(iter(bump))
    assert math.isclose(h(*pts[i]), 0.2, abs_tol=1e-9)
    for k, p in enumerate(nominal):                  # _undo inverts apply
        u, v = ar._undo(t, *t.apply(*p))
        assert math.isclose(u, p[0], abs_tol=1e-9)
        assert math.isclose(v, p[1], abs_tol=1e-9)


def _window(monkeypatch, tmp_path):
    from gerber2rml import platform as plat
    from gerber2rml.engine import bracket as br
    from gerber2rml.gui2 import workspace
    from gerber2rml.gui2.window import MainWindow
    monkeypatch.setattr(plat, "documents_dir", lambda *a, **k: tmp_path)
    workspace.set_setting("autorun/nogo", "")
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.autorun_page.set_bracket(br.DEFAULT)
    return w


FIXT = os.path.join(os.path.dirname(__file__), "fixtures", "mosfet_test")


def test_a_one_sided_setup_loads_one_sided_after_a_laser_board(
        monkeypatch, tmp_path):
    """Double-sided, then laser, then Load setup of a one-sided setup: the
    laser's "it was double-sided" belonged to the last board and ticked
    double-sided on the new one."""
    from gerber2rml.engine import method as fab
    w = _window(monkeypatch, tmp_path)
    try:
        w.load_folder(FIXT)
        w.action_double_sided(False)
        single = tmp_path / "single.srmcam"
        w.write_setup(single)
        w.action_double_sided(True)
        w.action_method(fab.LASER)
        assert not w._double                 # the laser board is one-sided
        w.load_setup(single)
        assert not w._double
        assert not w.inspector.setup.double.isChecked()
    finally:
        w.close()


def test_the_board_stays_put_while_a_run_is_going(monkeypatch, tmp_path):
    """A drag, an undo or Load setup mid-run swapped the geometry under the
    steps still to cut."""
    w = _window(monkeypatch, tmp_path)
    try:
        w.load_folder(FIXT)
        w.state.set_placement(40.0, 30.0)
        w.autorun_page._thread = object()    # a run is going
        w.action_place(60.0, 50.0)
        w._on_drag(5.0, 5.0)
        w.action_rotate(90)
        assert (w.state.place_x, w.state.place_y) == (40.0, 30.0)
        assert w.state.rotate == 0
    finally:
        w.autorun_page._thread = None
        w.close()


def test_both_kinds_of_project_file_open_from_either_menu(
        monkeypatch, tmp_path):
    """The archive (File > Export the whole project) and the Project page's
    file both ended in .srmproj, and each open refused the other's."""
    from gerber2rml import project as pj
    w = _window(monkeypatch, tmp_path)
    try:
        w.load_folder(FIXT)
        arc = tmp_path / ("job" + w.ARCHIVE_SUFFIX)
        assert w.export_project(str(arc))
        assert w.import_project(str(arc))
        page = tmp_path / ("p" + pj.SUFFIX)
        w.project_ctl.new(str(page))
        w.project_ctl.leave_current()
        assert w.import_project(str(page))       # routed to the Project page
        assert w.project_ctl.project is not None
    finally:
        w.close()
