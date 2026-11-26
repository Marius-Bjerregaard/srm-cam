"""A board put back for rework is cut: its copper is in islands, and the
probe feels only copper the clip still reaches. On the lab mill, 2026-11-26,
the one touch for the copper's height came down on an island, read nothing,
and the head went on down into the board."""
import math

from shapely.geometry import LineString, Point

from gerber2rml.engine import simmachine as sm
from gerber2rml.engine import spi_probe, stockfind
from gerber2rml.engine.autorun import (START_AT_CHECK, TOUCH_CLEAR_MM,
                                       AutoRun, connected_touch)
from gerber2rml.toolpath import Move
from tests.test_autorun import FAST, Board, Log

STOCK = (0.0, 0.0, 100.0, 80.0)


def loop(x0, y0, x1, y1, width=0.2):
    c = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return LineString(c).buffer(width / 2.0)


def test_a_spot_on_copper_that_reaches_the_edge_is_kept():
    cuts = loop(40, 40, 60, 60)
    assert connected_touch(STOCK, cuts, (15.0, 15.0)) == ((15.0, 15.0), True)


def test_a_spot_on_an_island_is_moved_to_copper_that_reaches_the_edge():
    cuts = loop(5, 5, 25, 25)                 # round the usual spot
    (x, y), proven = connected_touch(STOCK, cuts, (15.0, 15.0))
    assert proven
    assert not (5 <= x <= 25 and 5 <= y <= 25)          # out of the island
    assert cuts.distance(Point(x, y)) >= TOUCH_CLEAR_MM - 1e-6
    assert min(x, y, 100 - x, 80 - y) >= TOUCH_CLEAR_MM - 1e-6
    assert math.dist((x, y), (15.0, 15.0)) < 16.0       # and the nearest


def test_a_clamp_is_not_where_the_spot_goes():
    cuts = loop(5, 5, 25, 25)
    (x, y), _ = connected_touch(STOCK, cuts, (15.0, 15.0),
                                free=lambda x, y: x > 50)
    assert x > 50


def test_no_copper_to_the_edge_with_room_is_said_to_be_a_guess():
    """A poured board as big as its stock: the isolation runs all the way
    round, a hair inside the edge."""
    cuts = loop(0.3, 0.3, 99.7, 79.7)
    (x, y), proven = connected_touch(STOCK, cuts, (15.0, 15.0))
    assert not proven and (x, y) == (15.0, 15.0)


class Islanded(Board):
    """The traces cut a closed loop round the bracket's usual touch."""
    def toolpaths(self, key):
        if key == "traces":
            c = [self._p(*q) for q in ((5, 5), (20, 5), (20, 20), (5, 20),
                                       (5, 5))]
            return [[Move(*c[0], 2, rapid=True)]
                    + [Move(x, y, -0.1) for x, y in c]
                    + [Move(*c[-1], 2, rapid=True)]], 4.0, 1.0
        return super().toolpaths(key)


def _bracketed(monkeypatch, **kw):
    sm.machine(speedup=1e6, corner=(30000, 20000), angle_deg=0.0,
               size=(100000, 80000))
    ser = spi_probe.open_link("SIM")
    board, log = Islanded(False), Log()
    notes, touched = [], []
    log.note = notes.append
    real = stockfind.from_datum

    def spy(*a, **k):
        touched.append(k.get("touch"))
        return real(*a, **k)
    monkeypatch.setattr(stockfind, "from_datum", spy)
    run = AutoRun(ser, sm.printer(), board, log, click_mm=(0, 0),
                  stock_mm=(100, 80), double=False, runner_opts=FAST,
                  datum=(30.0, 20.0, "front-left"), **kw)
    return run, board, notes, touched


def test_a_fresh_sheet_is_touched_where_it_always_was(monkeypatch):
    run, _board, _notes, touched = _bracketed(monkeypatch, level=False)
    run._find_stock("stock")
    assert touched == [stockfind.datum_touch((30.0, 20.0), "front-left",
                                             (100, 80))]


def test_a_cut_board_is_touched_on_copper_the_clip_reaches(monkeypatch):
    run, board, notes, touched = _bracketed(monkeypatch, level=False,
                                            start_at=START_AT_CHECK)
    run._find_stock("stock")
    usual = stockfind.datum_touch((30.0, 20.0), "front-left", (100, 80))
    x0, y0 = board._p(5, 5)
    x1, y1 = board._p(20, 20)
    assert x0 < usual[0] < x1 and y0 < usual[1] < y1    # usual: the island
    x, y = touched[0]
    assert not (x0 - 2 < x < x1 + 2 and y0 - 2 < y < y1 + 2)
    assert any("still runs to the stock's edge" in n for n in notes)


def test_rework_on_the_last_runs_measurements_touches_nothing(monkeypatch):
    first, _b, _n, _t = _bracketed(monkeypatch)
    first._find_stock("stock")
    first.src.place(first.pose, first.margin)
    from gerber2rml.engine.autorun import about
    first.rotation = about(first.pose.x, first.pose.y, 0.0)
    first._level("level", "bottom", first.rotation)
    prior = {"pose": first.pose, "z_surf": first.z_surf,
             "level": first.level_rows}
    assert prior["level"] is not None

    run, _board, notes, touched = _bracketed(
        monkeypatch, start_at=START_AT_CHECK, prior=prior)
    sent = []
    monkeypatch.setattr(stockfind, "_cmd",
                        lambda *a, **k: sent.append(a) or None)
    monkeypatch.setattr(stockfind, "reference_at",
                        lambda *a, **k: sent.append(a) or None)
    run._find_stock("stock")
    hmap = run._level("level", "bottom", first.rotation)
    assert touched == [] and sent == []
    assert run.pose is first.pose and run.z_surf == first.z_surf
    x, y, dz = prior["level"][0][-1]
    assert abs(hmap(x, y) - dz) < 1e-6
    assert any("not touched again" in n for n in notes)


def test_a_fresh_start_does_not_use_the_last_runs_measurements(monkeypatch):
    first, _b, _n, _t = _bracketed(monkeypatch, level=False)
    first._find_stock("stock")
    prior = {"pose": first.pose, "z_surf": first.pose.z, "level": None}
    run, _board, _notes, touched = _bracketed(monkeypatch, level=False,
                                              prior=prior)
    run._find_stock("stock")
    assert len(touched) == 1


class Swallowed:
    """A print queue the job leaves without the mill ever running it."""
    purged = 0

    def send(self, data, name=""):
        return 1

    def pending(self):
        return 0

    def purge(self):
        self.purged += 1
        return True


def test_a_job_the_mill_never_ran_is_not_finished():
    """The second recut on the lab mill, 2026-11-26: the job left the queue,
    the head stood still at the view position, and the run said "the mill
    stopped at ..., not at the program's end" and went on as if it had cut."""
    import pytest
    from gerber2rml.engine.runner import JobNotRun, JobRunner
    sm.machine(speedup=1e6)
    ser = spi_probe.open_link("SIM")
    with pytest.raises(JobNotRun, match="Nothing was cut"):
        JobRunner(ser, Swallowed(), lambda: False, still_s=0.05,
                  poll_s=0.01, settled_s=0.2).run(
            "G0 X1\n", "Isolation traces", end_mm=(145.12, 107.12),
            timeout_s=10)


def test_a_recut_that_never_ran_adds_no_depth(monkeypatch):
    from gerber2rml.engine.runner import JobNotRun
    run, _board, notes, _touched = _bracketed(monkeypatch, level=False)
    answers = iter([("again", 0.05), ("again", 0.10), ("go", 0.0)])
    run.ev.check = lambda label, deeper: next(answers)
    run._to_view = run._wait_for_lid = lambda: None
    asked = []

    def job(key, label, t, hmap, deeper=0.0, boxes=None):
        asked.append(round(deeper, 3))
        if len(asked) == 1:
            raise JobNotRun("the mill did not run Traces")
    run._job = job
    run._check_traces(("traces", "Traces"), None, None, "stopped")
    assert asked == [0.05, 0.10]            # not 0.15 on top of nothing
    assert any("as deep as before" in n for n in notes)


def test_a_mill_paused_by_itself_is_told_to_carry_on(monkeypatch):
    """A paused mill takes no data: the job sits in the queue. With the lid
    shut the run does what Continue in VPanel does - once - and the stall
    message says what the mill reports."""
    from gerber2rml.engine import runner
    from gerber2rml.engine.runner import JobRunner
    state = {"paused": True, "jobs": 1}
    resumed, said = [], []

    class Held(Swallowed):
        def pending(self):
            return state["jobs"]

    def status(_ser):
        return {"paused": state["paused"], "system": 0x20 if state["paused"]
                else 0x1}

    def resume(_ser):
        resumed.append(1)
        state.update(paused=False, jobs=0)      # the mill takes the job
        return True
    monkeypatch.setattr(runner.spi_probe, "machine_status", status)
    monkeypatch.setattr(runner.spi_probe, "resume_job", resume)
    monkeypatch.setattr(runner.spi_probe, "query_position",
                        lambda _ser: (10.0, 10.0, 0.0, 0) if state["jobs"]
                        else (40.0, 40.0, 0.0, 0))
    monkeypatch.setattr(JobRunner, "HELD_S", 0.05)
    assert JobRunner(None, Held(), lambda: False, note=said.append,
                     still_s=0.05, poll_s=0.01, stall_s=0.02).run(
        "G0 X1\n", "Isolation traces", end_mm=(40.0, 40.0), timeout_s=10)
    assert resumed == [1]
    assert any("paused (status 0x00000020)" in n for n in said)
    assert any("telling it to carry on" in n for n in said)
