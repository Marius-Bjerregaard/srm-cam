"""The automatic run's progress on the Run it page and the machine bar.

The arithmetic is runclock.py's and is tested there; this is that the page
shows it: a bar, the step in hand, the time left and the time of day, the
same on the machine bar, and a clock that stops while the run waits."""
from pathlib import Path

import pytest

from gerber2rml.engine import bracket as br
from gerber2rml.gui2.runclock import RunClock
from gerber2rml.gui2.window import MainWindow

FIXT = Path(__file__).parent / "fixtures" / "mosfet_test"


class T:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def page(qt_app, monkeypatch, tmp_path):
    from gerber2rml import platform as plat
    monkeypatch.setattr(plat, "documents_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(plat, "capabilities",
                        lambda p=None: plat.Capabilities(machine_link=True))
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    w.autorun_page.set_bracket(br.DEFAULT)
    w.load_folder(str(FIXT))
    w.select_step("autorun")
    yield w.autorun_page
    w.autorun_page._clock = None
    w.close()


def _clocked(page):
    t = T()
    page._clock = RunClock([("stock", "Find the stock", 40),
                            ("drill_run", "Drill", 200),
                            ("traces_run", "Isolation traces", 760)], now=t)
    return t


def test_every_step_of_the_plan_gets_an_estimate(page):
    from gerber2rml.gui2.autorun import WindowSource
    est = page._estimates(WindowSource(page.ctl, page._gui))
    assert [k for k, _l, _s in est] == [k for k, _l in page._plan_rows()]
    by = {k: s for k, _l, s in est}
    assert by["stock"] == 45.0
    assert all(s > 0 for k, s in by.items() if k != "flip")
    # A cutting step is timed from its own toolpaths, not a flat guess.
    cuts = [s for k, s in by.items() if k.endswith("_run")]
    assert cuts and len(set(round(s) for s in cuts)) > 1


def test_the_line_says_the_step_the_percent_and_the_time_left(page):
    t = _clocked(page)
    page._clock.step("stock", "running")
    t.t = 40; page._clock.step("stock", "done")
    page._clock.step("drill_run", "running")
    t.t = 140                                        # half of the drill
    page._tick()
    # The step in hand over the bar, its percent at the bar's end...
    assert page.progress_head.text() == "Step 2 of 3 · Drill"
    assert page.progress_pct.text() == "50 %"
    assert page.progress.value() == 500
    rows = {k: v.text() for k, (_n, v) in page._progress_rows.items()}
    assert rows["step"] == "1m 40s left"
    # ...and the whole run and the time so far in rows of their own.
    assert rows["run"].startswith("14m 20s left") and "done about " in rows["run"]
    assert rows["elapsed"] == "2m 20s"
    assert [n.text() for n, _v in page._progress_rows.values()] == [
        "This step", "Whole run", "Elapsed"]


def test_the_machine_bar_says_it_too_while_the_run_holds_the_machine(page):
    bar = page.ctl.bar
    bar.set_held_by_run(True)
    t = _clocked(page)
    page._clock.step("stock", "running")
    t.t = 20
    page._tick()
    note = bar.offline_note.text()
    assert "Step 1 of 3 · Find the stock" in note and "left" in note
    assert "STOP or Esc stops it" in note
    bar.set_held_by_run(False)
    assert "Step 1 of 3" not in bar.offline_note.text()


def test_the_bar_is_left_alone_when_no_run_holds_the_machine(page):
    bar = page.ctl.bar
    before = bar.offline_note.text()
    _clocked(page)
    page._tick()
    assert bar.offline_note.text() == before


def test_waiting_at_the_flip_says_so_and_stops_the_clock(page):
    t = _clocked(page)
    page._clock.step("stock", "running")
    t.t = 20
    page._clock_wait(True)
    t.t = 620
    page._tick()
    assert "Waiting for you" in page._progress_rows["step"][1].text()
    assert page.progress.value() == 500              # where it was
    assert "left" in page._progress_rows["run"][1].text()
    page._clock_wait(False)
    page._tick()
    assert "aiting" not in page.progress_text()


def test_a_step_past_its_estimate_says_at_least(page):
    t = _clocked(page)
    page._clock.step("stock", "running")
    t.t = 400
    page._tick()
    assert page._progress_rows["step"][1].text() == "past its estimate"
    assert page._progress_rows["run"][1].text().startswith("at least ")


def test_a_finished_run_leaves_one_line_in_place_of_the_rows(page):
    t = _clocked(page)
    page._progress_running(True)
    page._clock.step("stock", "running")
    t.t = 40
    page._tick()
    page._on_finished(True, "The board is done.")
    assert page.progress_lbl.text() == "Done in 40s."
    assert not page.progress_lbl.isHidden()
    assert page.progress_head.isHidden()
    assert all(v.isHidden() for _n, v in page._progress_rows.values())
    assert page.progress.value() == 1000 and page.progress_pct.text() == "100 %"


def test_a_run_started_part_way_is_timed_without_what_it_skips(page):
    """'Cut out the board' after a stop said the whole board's 26 minutes:
    the steps it skips were counted until the run reached them."""
    from gerber2rml.engine import autorun as ar
    from gerber2rml.gui2.autorun import WindowSource
    src = WindowSource(page.ctl, page._gui)
    whole = page._estimates(src)
    cut = [k for k, _l, _s in whole if "cutout" in k][0]
    page.start_box.setCurrentIndex(page.start_box.findData(cut))
    part = page._estimates(src)
    keys = [k for k, _l, _s in part]
    assert keys == ["stock", "level", cut]          # found and probed, then cut
    assert sum(s for *_x, s in part) < sum(s for *_x, s in whole) / 2
    page.start_box.setCurrentIndex(page.start_box.findData(ar.START_AT_CHECK))
    assert [k for k, _l, _s in page._estimates(src)] == keys
    page.start_box.setCurrentIndex(0)
    assert page._estimates(src) == whole


def test_the_steps_a_start_skips_follow_the_run():
    from gerber2rml.engine import autorun as ar
    bottom = [("drill_run", "Drill"), ("traces_run", "Traces"),
              ("cutout_run", "Cut out")]
    top = [("top_traces_run", "Top traces"), ("top_cutout_run", "Cut out")]
    f = ar.skipped_by_start
    assert f(None, bottom, top) == set()
    assert f("traces_run", bottom, top) == {"drill_run"}
    assert f("top_cutout_run", bottom, top) == {
        "drill_run", "traces_run", "cutout_run", "level", "flip",
        "top_traces_run"}
    assert f(ar.START_AT_FIDUCIALS, bottom, top) == {
        "drill_run", "traces_run", "cutout_run", "level", "flip"}
    assert f(ar.START_AT_CHECK, bottom, []) == {"drill_run", "traces_run"}
    assert f(ar.START_AT_CHECK, bottom, top) == {
        "drill_run", "traces_run", "cutout_run", "level", "flip",
        "top_traces_run"}
    assert f(ar.START_AT_CHECK, bottom[:2], []) == {"drill_run", "traces_run"}
    assert f("no_such_step", bottom, top) == set()
