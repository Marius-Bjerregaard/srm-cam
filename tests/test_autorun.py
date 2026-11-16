"""The whole autonomous run against the simulated machine: a stock turned
on the bed and off the origin, found, cut, flipped, found again, fitted."""
import math

import time

import pytest

from gerber2rml.engine import simmachine as sm
from gerber2rml.engine import spi_probe, stockfind
from gerber2rml.engine.autorun import (HOLES_AGAIN, START_AT_CHECK,
                                       START_AT_FIDUCIALS, AutoRun, Events,
                                       JobSource, RunFailed, about)
from gerber2rml.engine.fidfind import hole_test
from gerber2rml.engine.fiducial import Transform, apply_to_toolpaths
from gerber2rml.engine.runner import RunStopped
from gerber2rml.toolpath import Move

FAST = {"still_s": 0.0, "poll_s": 0.0}


def ring(x, y, r=0.4, z=-1.9):
    """A plunge and a circle: a hole of 2 * (r + bit radius)."""
    pts = [Move(x + r * math.cos(k / 8 * math.pi),
                y + r * math.sin(k / 8 * math.pi), z) for k in range(17)]
    return [Move(x + r, y, 2, rapid=True)] + pts + [Move(x + r, y, 2, rapid=True)]


class Board(JobSource):
    """A 40 x 30 board: two holes, a trace, an outline, four fiducials."""
    FID = [(2, 2), (38, 2), (38, 28), (2, 28)]

    def __init__(self, double):
        self.double = double
        self.off = (0.0, 0.0)
        self.fit = None

    def _p(self, x, y):
        return x + self.off[0], y + self.off[1]

    def place(self, pose, margin_mm):
        self.off = (pose.x + margin_mm, pose.y + margin_mm)

    def bottom_steps(self):
        steps = [("drill", "Drilling"), ("traces", "Traces")]
        if self.double:
            return [("align", "Fiducials")] + steps
        return steps + [("cutout", "Cut-out")]

    def top_steps(self):
        return [("top_traces", "Top traces"), ("cutout", "Cut-out")] \
            if self.double else []

    def toolpaths(self, key):
        p = self._p
        if key == "align":
            paths = [ring(*p(x, y)) for x, y in self.FID]
        elif key == "drill":
            paths = [ring(*p(15, 15)), ring(*p(25, 15))]
        elif key in ("traces", "top_traces"):
            a, b = p(10, 10), p(30, 10)
            paths = [[Move(*a, 2, rapid=True), Move(*a, -0.1), Move(*b, -0.1),
                      Move(*b, 2, rapid=True)]]
            if key == "top_traces":
                paths = apply_to_toolpaths(paths, self.fit)
        else:
            c = [p(0, 0), p(40, 0), p(40, 30), p(0, 30), p(0, 0)]
            paths = [[Move(*c[0], 2, rapid=True)]
                     + [Move(x, y, -1.9) for x, y in c]
                     + [Move(*c[-1], 2, rapid=True)]]
            if self.double:
                paths = apply_to_toolpaths(paths, self.fit)
        return paths, 4.0, 1.0

    def area(self, side):
        x0, y0 = self._p(0, 0)
        x1, y1 = self._p(40, 30)
        if side == "top" and self.fit:
            xs, ys = zip(*(self.fit.apply(x, y) for x, y in
                           ((x0, y0), (x1, y0), (x1, y1), (x0, y1))))
            return min(xs), min(ys), max(xs), max(ys)
        return x0, y0, x1, y1

    def fiducials(self):
        bottom = [self._p(x, y) for x, y in self.FID]
        cx = self._p(20, 0)[0]
        return bottom, [(2 * cx - x, y) for x, y in bottom]

    def fiducial_clearance_um(self):
        return 400

    def set_top_fit(self, t):
        self.fit = t


class Log(Events):
    def __init__(self):
        self.steps = []

    def step(self, key, state):
        self.steps.append((key, state))


def _run(double, **kw):
    m = sm.machine(speedup=1e6, corner=(41300, 27800), angle_deg=1.2,
                   size=(100000, 80000))
    ser = spi_probe.open_link("SIM")
    board, log = Board(double), Log()
    run = AutoRun(ser, sm.printer(), board, log, click_mm=(90, 70),
                  stock_mm=(100, 80), double=double, runner_opts=FAST, **kw)
    return m, ser, board, log, run


def test_a_single_sided_board_runs_start_to_finish():
    m, ser, board, log, run = _run(False)
    assert run.run() is True
    assert [k for k, s in log.steps if s == "done"] == [
        "stock", "level", "drill", "traces", "cutout"]
    # The hole is where the design put it, turned with the stock.
    x, y = run.rotation.apply(*board._p(15, 15))
    stockfind.reference_at(ser, int((x + 3) * 1000), int(y * 1000),
                           surface_um=int(run.pose.z * 1000))
    assert hole_test(ser, int(x * 1000), int(y * 1000)) is False


def test_a_double_sided_board_is_flipped_found_and_fitted():
    m, ser, board, log, run = _run(True)
    log.flip = lambda: (m.flip(), True)[1]
    assert run.run() is True
    done = [k for k, s in log.steps if s == "done"]
    assert done == ["stock", "level", "align", "drill", "traces", "flip",
                    "stock_top", "fiducials", "level_top", "top_traces",
                    "cutout"]
    # The fit holds the flip: turned by the stock's new angle, mirrored
    # through the board, every fiducial within the gate.
    assert board.fit is not None
    assert abs(math.degrees(board.fit.theta) - run.pose_top.angle) < 0.1


def test_a_board_moved_after_the_flip_is_still_found():
    m, ser, board, log, run = _run(True)
    log.flip = lambda: (m.flip(shift=(2500, -1800), turn_deg=-1.5), True)[1]
    assert run.run() is True


def test_a_flip_that_is_cancelled_stops_the_run():
    from gerber2rml.engine.runner import RunStopped
    m, ser, board, log, run = _run(True)
    log.flip = lambda: False
    with pytest.raises(RunStopped):
        run.run()
    assert ("top_traces", "running") not in log.steps


def test_levelling_can_be_left_out():
    m, ser, board, log, run = _run(False, level=False)
    probed = []
    log.level = lambda *a: probed.append(a)
    assert run.run() is True
    assert not probed
    assert ("level", "done") in log.steps

class WholeStockBoard(Board):
    """A 100 x 80 board used whole: the stock IS the board. Only a 10 x 10
    window in the middle is cut; nothing is cut along the edge."""

    def __init__(self, size=(100.0, 80.0)):
        super().__init__(False)
        self.size = size
        self.margin = None

    def place(self, pose, margin_mm):
        self.margin = margin_mm
        super().place(pose, margin_mm)

    def bottom_steps(self):
        return [("cutout", "Cut-out")]

    def toolpaths(self, key):
        p = self._p
        c = [p(45, 35), p(55, 35), p(55, 45), p(45, 45), p(45, 35)]
        return ([[Move(*c[0], 2, rapid=True)] + [Move(x, y, -1.9) for x, y in c]
                 + [Move(*c[-1], 2, rapid=True)]], 4.0, 1.0)

    def area(self, side):
        return (*self._p(0, 0), *self._p(*self.size))

    extent = area


def _whole(size=(100.0, 80.0), **kw):
    m = sm.machine(speedup=1e6, corner=(41300, 27800), angle_deg=1.2,
                   size=(100000, 80000))
    ser = spi_probe.open_link("SIM")
    board, log = WholeStockBoard(size), Log()
    run = AutoRun(ser, sm.printer(), board, log, click_mm=(90, 70),
                  stock_mm=(100, 80), double=False, runner_opts=FAST, **kw)
    return board, log, run


def test_a_board_the_size_of_the_stock_is_placed_on_it_without_a_margin():
    board, log, run = _whole()
    probed = []
    log.level = lambda *a: probed.append(a)
    assert run.run() is True
    assert run.whole and board.margin == 0.0
    assert [k for k, s in log.steps if s == "done"][-1] == "cutout"
    # what is cut, not the stock's edge, is what gets probed and checked
    x0, y0, x1, y1 = run._work
    assert (x1 - x0, y1 - y0) == pytest.approx((10.8, 10.8))


def test_a_whole_stock_board_cut_away_from_the_tongue_passes_the_keepout():
    # The tongue lies over the stock's front edge: the board covers it, the
    # window in the middle does not.
    board, log, run = _whole(level=False)
    run.keepout = (41.3 + 2.5, 27.8 - 1, 41.3 + 26, 27.8 + 5)
    assert run.run() is True


def test_a_board_a_little_bigger_than_the_stock_still_counts_as_the_stock():
    board, log, run = _whole(size=(100.3, 80.2), level=False)
    assert run.run() is True and run.whole


def test_a_board_clearly_bigger_than_the_stock_is_still_refused():
    board, log, run = _whole(size=(104.0, 80.0), level=False)
    with pytest.raises(RunFailed, match="bigger piece"):
        run.run()
    assert not run.whole


def test_a_dry_run_keeps_the_bit_in_the_air():
    from tests.test_gcode_incremental import _walk
    m, ser, board, log, run = _run(False, level=False, dry_run=True)
    sent = []
    log.job = lambda key, label, prog: sent.append(prog)
    assert run.run() is True
    assert len(sent) == 3
    for prog in sent:
        assert "M3" not in prog.split()
        lowest = min(p[2] for p in _walk(prog)[0])
        # Cut depths are a fraction of a mm: 5 mm up keeps them in the air.
        assert lowest > run.pose.z + 3.0, (lowest, run.pose.z)


def test_the_traces_are_checked_and_cut_again_deeper_before_the_cut_out():
    from tests.test_gcode_incremental import _walk
    m, ser, board, log, run = _run(False, level=False)
    sent, asked = [], []
    log.job = lambda key, label, prog: sent.append((key, prog))
    answers = iter([("again", 0.05), ("again", 0.05), ("go", 0)])

    def check(label, deeper):
        asked.append(deeper)
        return next(answers)
    log.check = check
    assert run.run() is True
    assert asked == [0.0, 0.05, 0.10]
    keys = [k for k, _p in sent]
    assert keys == ["drill", "traces", "traces", "traces", "cutout"]

    def lowest(p):
        return min(z for _x, _y, z in _walk(p)[0])
    t = [lowest(p) for k, p in sent if k == "traces"]
    assert t[0] - t[1] == pytest.approx(0.05, abs=0.002)
    assert t[0] - t[2] == pytest.approx(0.10, abs=0.002)


def test_stop_at_the_check_leaves_the_board_uncut_free():
    from gerber2rml.engine.runner import RunStopped
    m, ser, board, log, run = _run(False, level=False)
    sent = []
    log.job = lambda key, label, prog: sent.append(key)
    log.check = lambda label, deeper: ("stop", 0)
    with pytest.raises(RunStopped):
        run.run()
    assert "cutout" not in sent


def test_a_run_can_start_at_a_later_step():
    m, ser, board, log, run = _run(False, level=False, start_at="traces")
    sent = []
    log.job = lambda key, label, prog: sent.append(key)
    assert run.run() is True
    assert sent == ["traces", "cutout"]
    assert ("drill", "skipped") in log.steps


def test_a_recut_in_boxes_cuts_only_inside_them():
    from tests.test_gcode_incremental import _walk
    m, ser, board, log, run = _run(False, level=False)
    sent = []
    log.job = lambda key, label, prog: sent.append((key, prog))
    first = []

    def check(label, deeper):
        if first:
            return ("go", 0)
        # A box over the middle of where the traces were just cut.
        cuts = [p for p in _walk(sent[-1][1])[0] if p[2] < run.pose.z]
        x0, x1 = min(p[0] for p in cuts), max(p[0] for p in cuts)
        y0, y1 = min(p[1] for p in cuts), max(p[1] for p in cuts)
        q = (x1 - x0) / 4
        box = (x0 + q, y0 - 1, x1 - q, y1 + 1)       # the middle half
        first.append(box)
        return ("again", 0.05, [box])
    log.check = check
    assert run.run() is True
    assert [k for k, _p in sent] == ["drill", "traces", "traces", "cutout"]
    x0, y0, x1, y1 = first[0]
    full = [p for p in _walk(sent[1][1])[0] if p[2] < run.pose.z]
    redo = [p for p in _walk(sent[2][1])[0] if p[2] < run.pose.z]
    span = lambda pts: max(p[0] for p in pts) - min(p[0] for p in pts)
    assert redo and span(redo) < span(full) - 5
    for x, y, _z in redo:
        assert x0 - 0.01 <= x <= x1 + 0.01 and y0 - 0.01 <= y <= y1 + 0.01
    assert min(p[2] for p in redo) < min(p[2] for p in full) - 0.04


def test_a_finished_board_is_brought_to_the_view_position():
    m, ser, board, log, run = _run(False)
    assert run.run() is True
    assert m.pos == [0, 150000, 0]


def test_a_stopped_run_is_not_moved_to_the_view_position(monkeypatch):
    m, ser, board, log, run = _run(False, check_cut=True)
    seen = []
    monkeypatch.setattr(run, "_to_view", lambda: seen.append(1))
    log.check = lambda label, deeper: ("stop", 0.0)
    with pytest.raises(RunStopped):
        run.run()
    assert len(seen) == 1                   # the check's, and no other


def test_the_run_waits_for_the_lid_before_it_cuts_on(monkeypatch):
    m, ser, board, log, run = _run(False)
    lid = [True, True, False]
    monkeypatch.setattr(spi_probe, "machine_status",
                        lambda s: {"cover": lid.pop(0) if lid else False})
    monkeypatch.setattr(time, "sleep", lambda s: None)
    notes = []
    log.note = notes.append
    run._wait_for_lid()
    assert notes == ["Close the lid to carry on."] and not lid


def test_the_board_is_brought_out_to_be_flipped_and_the_lid_waited_for():
    m, ser, board, log, run = _run(True)
    seen = []

    def flip():
        seen.append(tuple(m.pos))
        m.flip()
        return True
    log.flip = flip
    waits = []
    wait = run._wait_for_lid
    run._wait_for_lid = lambda: (waits.append(1), wait())
    assert run.run() is True
    assert seen == [(0, 150000, 0)]
    assert waits


def test_each_fiducial_is_looked_for_where_the_last_one_says(monkeypatch):
    m, ser, board, log, run = _run(True)
    run.pose = run.pose_top = stockfind.StockPose(30.0, 20.0, 0.0, 100.0, 80.0, -40.0)
    run.rotation = None
    run.rotation = about(30.0, 20.0, 0.0)
    bottom = [(40.0, 30.0), (90.0, 30.0), (90.0, 70.0)]
    board.fiducials = lambda: (bottom, [(90.0, 30.0), (40.0, 30.0), (40.0, 70.0)])
    asked = []

    def locate(x, y, search_um=None):
        asked.append((x, y))
        # Off by 2 mm one way, past the old 1.5 mm search if it were not told.
        return (x + 2.0 - (asked[0][0] - asked[0][0] if len(asked) == 1 else 2.0),
                y - 0.5 + (0.0 if len(asked) == 1 else 0.5))
    monkeypatch.setattr(run, "_locate", locate)
    run._fiducials()
    perfect = [(30.0 + 100.0 - (x - 30.0), y) for x, y in bottom]
    assert asked[0] == pytest.approx(perfect[0])
    assert asked[1] == pytest.approx((perfect[1][0] + 2.0, perfect[1][1] - 0.5))
    assert asked[2] == pytest.approx((perfect[2][0] + 2.0, perfect[2][1] - 0.5))


def test_a_flipped_sheet_can_start_at_the_fiducials(monkeypatch):
    m, ser, board, log, run = _run(True, level=False,
                                   start_at=START_AT_FIDUCIALS)
    sent, found = [], []
    log.job = lambda key, label, prog: sent.append(key)
    log.flip = lambda: pytest.fail("it is already flipped")

    def fiducials():
        found.append(1)
        board.set_top_fit(Transform(theta=0.0, scale=1.0, tx=0.0, ty=0.0))
    monkeypatch.setattr(run, "_fiducials", fiducials)
    assert run.run() is True
    assert found == [1]
    assert sent == [k for k, _l in board.top_steps()]
    assert ("drill", "skipped") in log.steps


def _pose_for_fiducials(run):
    run.pose = run.pose_top = stockfind.StockPose(30.0, 20.0, 0.0, 100.0, 80.0, -40.0)
    run.rotation = about(30.0, 20.0, 0.0)


BOTTOM = [(40.0, 30.0), (90.0, 30.0), (90.0, 70.0), (40.0, 70.0)]
TOP = [(90.0, 30.0), (40.0, 30.0), (40.0, 70.0), (90.0, 70.0)]


def test_one_closed_fiducial_is_skipped_and_the_rest_still_fit(monkeypatch):
    m, ser, board, log, run = _run(True)
    _pose_for_fiducials(run)
    board.fiducials = lambda: (BOTTOM, TOP)
    notes = []
    log.note = notes.append
    tries = []

    def locate(x, y, search_um=None):
        tries.append(search_um)
        if len(tries) in (3, 4):             # the third hole, both searches
            raise RunFailed("fiducial not found within 1.0 mm of %.1f, %.1f" % (x, y))
        return x, y
    monkeypatch.setattr(run, "_locate", locate)
    assert run._fiducials() is not None
    assert any("Fiducial 3 skipped" in n for n in notes)
    assert any("3 of 4 found" in n for n in notes)
    # Short, once the fit knows where - and the full search before a hole
    # is given up.
    assert tries[2:] == [1000, 2500, 1000]


def test_a_hole_past_the_short_search_is_found_by_the_full_one(monkeypatch):
    m, ser, board, log, run = _run(True)
    _pose_for_fiducials(run)
    board.fiducials = lambda: (BOTTOM, TOP)
    notes = []
    log.note = notes.append

    def locate(x, y, search_um=None):
        if search_um == 1000:
            raise RunFailed("fiducial not found within 1.0 mm")
        return x, y
    monkeypatch.setattr(run, "_locate", locate)
    assert run._fiducials() is not None
    assert any("4 of 4 found" in n for n in notes)
    assert not any("skipped" in n for n in notes)


def test_a_rim_reading_does_not_give_the_hole_up(monkeypatch):
    """The first spot that reads "no copper" turns out to be the rim: the
    centre finder refuses it, and the search goes on to the hole itself."""
    from gerber2rml.engine import autorun as arun
    from gerber2rml.engine.spi_probe import ProbeError
    m, ser, board, log, run = _run(True)
    _pose_for_fiducials(run)
    monkeypatch.setattr(run, "_hop", lambda *a, **k: None)
    monkeypatch.setattr(stockfind, "reference_at", lambda *a, **k: 0)
    monkeypatch.setattr(arun, "hole_test", lambda ser, x, y, **k: False)
    asked = []

    def centre(ser, x, y, should_abort=None):
        asked.append((x, y))
        if len(asked) == 1:
            raise ProbeError("copper contact at the start point")
        return (x + 0.1, y)
    monkeypatch.setattr(arun, "find_hole_center", centre)
    assert run._locate(50.0, 40.0) == pytest.approx((asked[1][0] + 0.1,
                                                     asked[1][1]))
    assert len(asked) == 2 and asked[0] == (50.0, 40.0)


def test_too_few_fiducials_stop_before_the_top_is_cut(monkeypatch):
    m, ser, board, log, run = _run(True)
    _pose_for_fiducials(run)
    board.fiducials = lambda: (BOTTOM, TOP)
    tries = []

    def locate(x, y, search_um=None):
        tries.append(1)
        if len(tries) > 2:
            raise RunFailed("not found")
        return x, y
    monkeypatch.setattr(run, "_locate", locate)
    with pytest.raises(RunFailed, match="only 2 of the 4"):
        run._fiducials()


def test_starting_at_the_check_cuts_nothing_until_it_is_answered(monkeypatch):
    m, ser, board, log, run = _run(False, level=False, start_at=START_AT_CHECK)
    sent, asked = [], []
    log.job = lambda key, label, prog: sent.append(key)

    def check(label, deeper):
        asked.append((label, list(sent)))
        return ("go", 0.0)
    log.check = check
    assert run.run() is True
    assert asked[0][1] == []                 # nothing cut before the question
    assert sent[-1] == "cutout" and "traces" not in sent


def test_the_check_can_drill_the_holes_again_deeper_and_asks_again(monkeypatch):
    m, ser, board, log, run = _run(False, level=False)
    answers = [("holes", 0.3), ("go", 0.0)]
    log.check = lambda label, deeper: answers.pop(0)
    sent = []
    log.job = lambda key, label, prog: sent.append(key)
    real = board.toolpaths
    monkeypatch.setattr(board, "toolpaths", lambda key: real(
        "drill" if key == HOLES_AGAIN else key))
    assert run.run() is True
    assert not answers
    assert sent[-2:] == [HOLES_AGAIN, "cutout"]



class PouredWholeBoard(WholeStockBoard):
    """The same, with what a ground pour adds: an isolation pass all the
    way round, a tenth of a millimetre inside the board's edge."""

    def bottom_steps(self):
        return [("traces", "Isolation traces")]

    def toolpaths(self, key):
        p = self._p
        w, h = self.size
        c = [p(0.1, 0.1), p(w - 0.1, 0.1), p(w - 0.1, h - 0.1),
             p(0.1, h - 0.1), p(0.1, 0.1)]
        return ([[Move(*c[0], 2, rapid=True)] + [Move(x, y, -0.1) for x, y in c]
                 + [Move(*c[-1], 2, rapid=True)]], 4.0, 1.0)


def test_a_poured_whole_stock_board_is_not_refused_for_its_edge_pass():
    """Its pass runs 0.1 mm inside the stock's edge, so the 0.8 mm cutter's
    edge is 0.3 mm over the side - cutting air. That is not hanging off."""
    m = sm.machine(speedup=1e6, corner=(41300, 27800), angle_deg=0.0,
                   size=(100000, 80000))
    ser = spi_probe.open_link("SIM")
    board, log = PouredWholeBoard(), Log()
    run = AutoRun(ser, sm.printer(), board, log, click_mm=(90, 70),
                  stock_mm=(100, 80), double=False, runner_opts=FAST,
                  level=False)
    assert run.run() is True and run.whole
    assert ("traces", "done") in log.steps


def test_a_whole_stock_board_cutting_past_the_edge_is_still_refused():
    """A pass whose CENTRE is off the stock is another matter."""
    class Over(PouredWholeBoard):
        def toolpaths(self, key):
            paths, xy, plunge = super().toolpaths(key)
            return ([[Move(m.x - 1.0, m.y, m.z, rapid=m.rapid) for m in tp]
                     for tp in paths], xy, plunge)

    m = sm.machine(speedup=1e6, corner=(41300, 27800), angle_deg=0.0,
                   size=(100000, 80000))
    ser = spi_probe.open_link("SIM")
    run = AutoRun(ser, sm.printer(), Over(), Log(), click_mm=(90, 70),
                  stock_mm=(100, 80), double=False, runner_opts=FAST,
                  level=False)
    with pytest.raises(RunFailed, match="hangs off the stock"):
        run.run()


class PutBackBoard(Board):
    """The board of a first run, put back in the mill: the stock's edges
    now put the job a little off where it was cut."""
    OFF = (0.7, -0.5)

    def place(self, pose, margin_mm):
        super().place(pose, margin_mm)
        self.off = (self.off[0] + self.OFF[0], self.off[1] + self.OFF[1])

    def board_holes(self):
        return [(*self._p(15, 15), 1.6), (*self._p(25, 15), 1.6)]

    def set_turn(self, t):
        self.turn = t


def _cut_then_put_back(**kw):
    m, ser, board, log, run = _run(False, level=False)
    log.check = lambda label, deeper: ("stop", 0.0)
    with pytest.raises(RunStopped):
        run.run()                            # drilled and traced, not cut out
    was = run.rotation.apply(*board._p(15, 15))
    again, log2 = PutBackBoard(False), Log()
    log2.check = lambda label, deeper: ("stop", 0.0)
    notes = []
    log2.note = notes.append
    run2 = AutoRun(ser, sm.printer(), again, log2, click_mm=(90, 70),
                   stock_mm=(100, 80), double=False, runner_opts=FAST,
                   level=False, start_at=START_AT_CHECK, **kw)
    return was, again, run2, notes


def test_a_board_put_back_for_rework_is_found_by_its_own_holes():
    was, again, run2, notes = _cut_then_put_back(by_holes=True)
    with pytest.raises(RunStopped):
        run2.run()
    now = run2.rotation.apply(*again._p(15, 15))
    assert math.dist(now, was) < 0.1         # where it was cut, not 0.86 off
    assert again.turn is run2.rotation
    assert any("found by 2 of its holes" in n for n in notes)


def test_without_its_holes_a_board_put_back_goes_by_the_stock():
    was, again, run2, notes = _cut_then_put_back()
    with pytest.raises(RunStopped):
        run2.run()
    now = run2.rotation.apply(*again._p(15, 15))
    assert math.dist(now, was) == pytest.approx(math.hypot(0.7, 0.5), abs=0.1)


def test_a_board_with_no_holes_to_find_it_by_is_not_cut():
    was, again, run2, notes = _cut_then_put_back(by_holes=True)
    again.board_holes = lambda: []
    with pytest.raises(RunFailed, match="no holes it can be found by"):
        run2.run()


class HoleOnATouch(Board):
    """A hole exactly where the surface grid's first touch falls (the grid
    is set 2 mm in from the board's edge)."""
    def toolpaths(self, key):
        if key == "drill":
            return [ring(*self._p(2, 2)), ring(*self._p(25, 15))], 4.0, 1.0
        return super().toolpaths(key)


def test_a_surface_touch_is_moved_off_a_hole(monkeypatch):
    from gerber2rml.engine import autorun as arun
    from shapely.geometry import Point
    m = sm.machine(speedup=1e6, corner=(41300, 27800), angle_deg=1.2,
                   size=(100000, 80000))
    ser = spi_probe.open_link("SIM")
    board, log = HoleOnATouch(False), Log()
    notes, seen = [], []
    log.note = notes.append
    real = arun.level_targets

    def spy(points, zones, reach=None):
        out = real(points, zones, reach)
        seen.append((list(points), out, zones))
        return out
    monkeypatch.setattr(arun, "level_targets", spy)
    run = AutoRun(ser, sm.printer(), board, log, click_mm=(90, 70),
                  stock_mm=(100, 80), double=False, runner_opts=FAST)
    assert run.run() is True
    points, targets, zones = seen[0]
    hole = run.rotation.apply(*board._p(2, 2))
    assert math.dist(points[0], hole) < 0.01          # the grid asked for it
    assert math.dist(targets[0], hole) >= 0.8 + 0.4   # and was moved off it
    assert not any(z.intersects(Point(t)) for z in zones for t in targets)
    # Only those on a hole moved: this one, and the one a millimetre from
    # the other hole.
    other = run.rotation.apply(*board._p(25, 15))
    for p, t in zip(points, targets):
        near = min(math.dist(p, hole), math.dist(p, other)) < 1.2
        assert (t != p) == near
    assert any("moved off a no-go zone or a cut" in n for n in notes)


def test_the_surface_touches_keep_off_holes_and_never_off_traces():
    m, ser, board, log, run = _run(False, level=False)
    run.rotation = about(0.0, 0.0, 0.0)
    from shapely.geometry import Point
    on_trace, on_hole = Point(board._p(20, 10)), Point(board._p(15, 15))
    for start in (None, START_AT_CHECK):
        run.start_at = start
        zone = run._cut_already("bottom", run.rotation)
        assert zone.intersects(on_hole) and not zone.intersects(on_trace)


class HoleAtTheBracket(Board):
    """A hole 15 mm in from the stock's corner: where the bracket's one
    touch for the copper's height comes down."""
    def toolpaths(self, key):
        if key == "drill":
            return [ring(*self._p(15, 15)), ring(*self._p(25, 15))], 4.0, 1.0
        return super().toolpaths(key)

    def place(self, pose, margin_mm):
        self.off = (pose.x, pose.y)          # flush in the corner


def test_the_height_touch_in_the_bracket_is_moved_off_a_hole(monkeypatch):
    sm.machine(speedup=1e6, corner=(30000, 20000), angle_deg=0.0,
               size=(100000, 80000))
    ser = spi_probe.open_link("SIM")
    board, log = HoleAtTheBracket(False), Log()
    notes, touched = [], []
    log.note = notes.append
    real = stockfind.from_datum

    def spy(*a, **k):
        touched.append(k.get("touch"))
        return real(*a, **k)
    monkeypatch.setattr(stockfind, "from_datum", spy)
    run = AutoRun(ser, sm.printer(), board, log, click_mm=(0, 0),
                  stock_mm=(100, 80), double=False, runner_opts=FAST,
                  level=False, datum=(30.0, 20.0, "front-left"))
    run._find_stock("stock")
    assert math.dist(touched[0], (45.0, 35.0)) >= 0.8 + 0.4   # off the hole
    assert math.dist(touched[0], (45.0, 35.0)) <= 2.0         # and near it
    assert any("on one of the board's holes" in n for n in notes)
    # A board with no hole there is touched where it always was.
    board.toolpaths = lambda key: Board.toolpaths(board, "traces")
    run._find_stock("stock")
    assert touched[1] == stockfind.datum_touch((30.0, 20.0), "front-left",
                                               (100, 80))


def test_a_job_reports_how_far_the_head_is_along_its_path():
    m, ser, board, log, run = _run(False, level=False)
    seen = {}
    log.progress = lambda key, f: seen.setdefault(key, []).append(f)
    assert run.run() is True
    for key in ("drill", "traces", "cutout"):
        got = seen.get(key) or []
        assert got, key                           # it was followed at all
        assert got == sorted(got)                 # and never went back
        assert 0.0 <= got[0] and got[-1] <= 1.0
