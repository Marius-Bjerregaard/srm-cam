"""A run on the laser page is the job it started with, ends at STOP, and
leaves the laser stopped after a failure (review, 2026-11-20). The lasers
here are fakes: nothing is sent anywhere."""
import re
import threading
import time

import pytest

from gerber2rml.engine import laser_focus as lf
from gerber2rml.engine import xtool


def _wait(qt_app, page, limit=20):
    t0 = time.monotonic()
    while page._locked() and time.monotonic() - t0 < limit:
        qt_app.processEvents()
        time.sleep(0.01)
    if page._worker is not None:
        page._worker.wait(10000)
    qt_app.processEvents()


def _until(qt_app, cond, limit=10):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < limit:
        qt_app.processEvents()
        time.sleep(0.01)
    assert cond()


class Laser:
    """Answers like the F1 Ultra; ``gate`` holds band ``hold`` running until
    it is set (STOP sets it)."""

    def __init__(self, hold=None):
        self.log = []
        self.gate = threading.Event()
        self.hold = hold
        self.state = "P_IDLE"
        self.head = 10.0

    def _json(self, method, path, body=None, **_k):
        self.log.append(("mode", body["state"]))

    def _goto_z(self, z):
        self.head = round(z, 3)
        self.log.append(("z", self.head))
        return z

    def z(self):
        return self.head

    def mode(self):
        return self.state

    def run(self, text):
        self.log.append(("run", text))
        self.state = "P_WORK"

    def wait(self, timeout, progress=None):
        if self.hold is not None and self.runs() == self.hold:
            self.gate.wait(20)
        self.state = "P_IDLE"

    def stop(self):
        self.log.append(("stop",))
        self.state = "P_IDLE"
        self.gate.set()

    def runs(self):
        return sum(1 for e in self.log if e[0] == "run")


@pytest.fixture
def page(qt_app, monkeypatch):
    from shapely.geometry import box
    from shapely.ops import unary_union
    from gerber2rml.gui2 import stencil_laser as sl
    monkeypatch.setattr(sl, "_SETTINGS", ("SRM-CAM-tests", "laser-run-safety"))
    cu = unary_union([box(5, 5, 12, 8), box(40, 30, 55, 33)])   # not symmetric
    art = xtool.traces_art(cu, box(0, 0, 60, 40), [], side="top", mirror=False)
    d = sl.StencilLaserDialog(art)
    d.photo_after.setChecked(False)
    d.x.setValue(80)
    d.y.setValue(90)
    d._settle()
    ticks = []
    d._tell_host_done = lambda key=None: ticks.append(key)
    d.ticks = ticks
    yield d
    if d._worker is not None:
        d._worker.wait(10000)
    d.close()


def _tilted(page):
    """A map two bands deep across X, measured where the job lies."""
    page._focus_map = lf.FocusMap([(80, 90, 0.0), (140, 90, 1.0),
                                   (80, 130, 0.0), (140, 130, 1.0)], 10.0)
    from gerber2rml.gui2 import laser_focus_ui as lfu
    page._focus_map_bounds = lfu.job_bounds(page)


def test_stop_ends_a_banded_run_and_ticks_nothing(qt_app, page):
    """STOP in band 1 of 2: the laser was idle after it, and the run took
    that for the band done, sent band 2, said Done and ticked the rail."""
    laser = page.laser = Laser(hold=1)
    _tilted(page)
    page.cut()
    _until(qt_app, lambda: laser.runs() == 1)
    page.stop()
    _wait(qt_app, page)
    assert laser.runs() == 1                         # band 2 never sent
    assert page.status.text().startswith("Stopped in band 1 of 2")
    assert page.ticks == []
    assert laser.head == 10.0                        # back where it was measured


def test_stop_ends_a_plain_run_without_a_tick(qt_app, page):
    laser = page.laser = Laser(hold=1)
    page.cut()
    _until(qt_app, lambda: laser.runs() == 1)
    page.stop()
    _wait(qt_app, page)
    assert page.status.text().startswith("Stopped")
    assert page.ticks == []


def test_a_run_finished_ticks_the_row_it_started_on(qt_app, page):
    """The rail row is taken at the start: a face changed mid-run (it can
    be, from the main window) ticked the other face."""
    from gerber2rml.gui2.projectpage import TOP_TRACES
    laser = page.laser = Laser(hold=1)
    page.cut()
    _until(qt_app, lambda: laser.runs() == 1)
    page._trace_side = "bottom"
    laser.gate.set()
    _wait(qt_app, page)
    assert page.ticks == [("laser_traces", TOP_TRACES)]


def test_nothing_that_changes_the_job_works_mid_run(qt_app, page):
    laser = page.laser = Laser(hold=1)
    _tilted(page)
    page.cut()
    _until(qt_app, lambda: laser.runs() == 1)
    for w in (page.mirror_box, page.flip_box, page.undo_btn, page.redo_btn,
              page.offsets, page.trace_kerf, page.density, page.spot_drills,
              page.skip_auto, page.field_test_btn, page.copper_side,
              page.job_switch):
        assert not w.isEnabled(), w
    # Whatever gets through (a shortcut, the main window) is refused...
    from shapely.geometry import box
    other = xtool.LaserArt(kind="traces", outline=box(0, 0, 30, 30),
                           apertures=[box(5, 5, 9, 9)], mirrored=False)
    assert page.show_job(other, "another board") is False
    assert page.switch_kind("mask") is False and page.kind == "traces"
    art_now = page.art
    page.undo.push(page._state())
    page.undo_place()
    assert page.art is art_now
    # ...and the band still to run was built when the run started: a
    # Mirror that does get through changes the page, not the job.
    page.mirror_box.setChecked(not page.mirror_box.isChecked())
    qt_app.processEvents()
    laser.gate.set()
    _wait(qt_app, page)
    runs = [e[1] for e in laser.log if e[0] == "run"]
    assert len(runs) == 2
    xs = [float(m) for m in re.findall(r"G1X([-\d.]+)Y", runs[1])]
    # The copper at X 40-55 of the job, placed at X 80: 120-135 on the bed
    # (mirrored it would be 85-100).
    assert min(xs) > 115 and max(xs) < 140
    assert page.undo_btn.isEnabled()                 # back after the run


def test_a_lost_link_mid_band_stops_the_laser_before_the_head_moves(
        qt_app, page):
    """The wait timed out with the band still etching: the page put the head
    back and unlocked under a running laser."""
    laser = page.laser = Laser()
    _tilted(page)

    def wait(timeout, progress=None):
        raise xtool.LaserError("No answer from the laser (timed out).")
    laser.wait = wait
    page.cut()
    _wait(qt_app, page)
    kinds = [e[0] for e in laser.log]
    first_run = kinds.index("run")
    assert "stop" in kinds[first_run:]
    stop_at = kinds.index("stop", first_run)
    assert ("z", 10.0) in laser.log[stop_at:]        # the head back after it
    assert laser.runs() == 1
    assert "No answer" in page.status.text()
    assert not page._locked()


def test_no_answer_at_all_leaves_the_head_and_says_it_may_run(
        qt_app, page, monkeypatch):
    real = xtool.make_idle
    monkeypatch.setattr(xtool, "make_idle",
                        lambda laser: real(laser, timeout=0.3, poll=0.05))
    laser = page.laser = Laser()
    _tilted(page)

    def gone(*_a, **_k):
        raise xtool.LaserError("No answer from the laser (timed out).")
    laser.wait = gone
    laser.mode = gone
    page.cut()
    _wait(qt_app, page)
    zs = [e[1] for e in laser.log if e[0] == "z"]
    assert zs == [zs[0]]                             # only the first band's
    assert "may still be running" in page.status.text()


def test_one_band_runs_at_the_maps_height(qt_app, page):
    """A flat map: one band, at the map's Z and the band's, not wherever the
    head is now; and back to the map's Z after."""
    laser = page.laser = Laser()
    laser.head = 12.0                                # moved since the map
    page._focus_map = lf.FocusMap([(80, 90, 0.01), (140, 90, 0.01),
                                   (80, 130, 0.01)], 10.0)
    from gerber2rml.gui2 import laser_focus_ui as lfu
    page._focus_map_bounds = lfu.job_bounds(page)
    page.cut()
    _wait(qt_app, page)
    zs = [e[1] for e in laser.log if e[0] == "z"]
    assert zs == [10.01, 10.0]
    assert laser.runs() == 1


def test_a_map_from_somewhere_else_is_refused(qt_app, page):
    laser = page.laser = Laser()
    _tilted(page)
    page.x.setValue(150)                             # another place on the bed
    page.cut()
    _wait(qt_app, page)
    assert laser.runs() == 0
    assert "measured somewhere else" in page.status.text()


def test_another_face_or_board_drops_the_map_and_the_boxes(qt_app, page):
    from shapely.geometry import box
    _tilted(page)
    page.rework_boxes = [(85, 95, 90, 100)]
    page._take_traces_art(page._source)              # what a face switch does
    assert page._focus_map is None and page._focus_map_bounds is None
    assert page.rework_boxes == []
    _tilted(page)
    page.rework_boxes = [(85, 95, 90, 100)]
    page.show_job(xtool.LaserArt(kind="mask", outline=box(0, 0, 30, 30),
                                 apertures=[box(5, 5, 9, 9)]), "board 2")
    assert page._focus_map is None and page.rework_boxes == []


def test_a_done_that_fails_unlocks_the_page(qt_app, page):
    def done(_r):
        raise RuntimeError("no photo")
    page._start(lambda _p: 1, "Working…", done)
    _wait(qt_app, page)
    assert not page._locked() and page.cut_btn.isEnabled()
    assert "no photo" in page.status.text()


def test_escape_does_not_hide_a_running_window(qt_app, page):
    page.show()
    page._busy(True)
    page.reject()
    assert page.isVisible()
    assert "STOP" in page.status.text()
    page._busy(False)
    page.reject()
    assert not page.isVisible()


def test_the_rail_does_not_open_another_laser_job_mid_run():
    """A rail row clicked mid-run switched the page's job or face, and a
    banded run built its next band from it."""
    from types import SimpleNamespace
    from gerber2rml.gui2.window import MainWindow
    said = []
    opened = []
    win = SimpleNamespace(laser_view=SimpleNamespace(is_running=lambda: True),
                          say=lambda level, text: said.append(text),
                          state=SimpleNamespace(board=object()),
                          _laser_board=lambda: opened.append("board"))
    win._laser_busy = lambda: MainWindow._laser_busy(win)
    MainWindow._open_laser_kind(win, "mask")
    assert opened == [] and "laser is running" in said[0]
