"""Bot: Novice, asked.

Three things are held here. That Bot is a profile over the Novice tier and
not a third program - chosen, pinned and remembered like the other two, and
exporting Novice's bytes. That its questions come in the order the job is
set up, with Back at every one and a Cancel that leaves nothing behind. And
that an answer ends up in the control a hand would have used, so that what
Start runs is what Novice would have run.

The sheets are the real ones. ``Bot.ask`` is the one seam: by default it
shows a sheet modally and waits; here a function plays the operator on the
same sheet and presses its buttons.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from dataclasses import asdict
from pathlib import Path

import pytest

from gerber2rml.engine import bracket as br
from gerber2rml.engine import diagnostics as diag
from gerber2rml.engine import method as fab
from gerber2rml.gui2 import bot as botmod
from gerber2rml.gui2 import tier
from gerber2rml.gui2.window import MainWindow

# The board as the app loads it. With the suite's boxed pads the fixture has
# thirteen shorts, and a Bot job with a short does not reach its summary -
# which is one test here (below), not every one.
pytestmark = pytest.mark.exact_pads

FIXT = Path(__file__).parent / "fixtures" / "mosfet_test"   # 104 mm square

ORDER = ["board", "method", "sides", "thickness", "sheet", "bracket", "where",
         "cutout", "check", "machine"]


class _Settings:
    """A QSettings stand-in for the tier, so choosing one here is not a
    choice on the developer's machine."""

    def __init__(self):
        self.kept = {}

    def value(self, key, default=None):
        return self.kept.get(key, default)

    def setValue(self, key, value):
        self.kept[key] = value


@pytest.fixture
def coupon(tmp_path):
    """A second board, small and single-sided enough to tell from the first:
    the demo coupon, written where the test can throw it away."""
    from gerber2rml.examples.calibration import write_coupon
    d = tmp_path / "coupon"
    d.mkdir()
    write_coupon(d)
    return d


def _window(monkeypatch, mode="bot", folder=FIXT):
    monkeypatch.setenv("SRM_CAM_MODE", mode)
    w = MainWindow()
    w.resize(1400, 900)
    # The bracket where the simulated stock's corner is (see _sim).
    w.autorun_page.set_bracket(br.Bracket(3, 12, "back-left"))
    if folder is not None:
        w.load_folder(str(folder))
    return w


def _close(w):
    # Kept with the workspace, which the whole suite shares: off again.
    w.autorun_page.centre_chk.setChecked(False)
    w.link.disconnect_from("test")
    if w.link._thread is not None:
        w.link._q.put(None)
        w.link._thread.join(2)
    w.close()


def _wait(qt_app, cond, secs=120):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < secs:
        qt_app.processEvents()
        time.sleep(0.01)
    return cond()


def _sim(qt_app, w):
    """Connect the window to the simulated mill, a 150 x 124 mm sheet."""
    from gerber2rml.engine import simmachine as sm
    sm.machine(speedup=1e6, corner=(42490, 5470), angle_deg=0.0,
               size=(150000, 124000))
    w.link.connect_to("SIM")
    assert _wait(qt_app, w.link.is_connected, 10)
    return sm


def _key(sheet):
    q = getattr(sheet, "question", None)
    if q is not None:
        return q.key
    for kind in ("findings", "stopped"):
        if hasattr(sheet, kind):
            return kind
    return "summary"


class Hands:
    """The operator. ``answers`` maps a question to a function that fills its
    sheet in; ``then`` maps a sheet to what to press on it when it comes up -
    a list, one entry per visit, the last one repeated. Everything not
    mentioned is answered as it stands with Next, and the two things the app
    cannot guess (the ticks on the bracket sheet) are ticked."""

    def __init__(self, answers=None, then=None):
        self.answers = dict(answers or {})
        self.then = {k: list(v) for k, v in (then or {}).items()}
        self.seen = []                  # every sheet, in the order shown
        self.sheets = {}                # the last sheet of each kind

    def __call__(self, sheet):
        key = _key(sheet)
        self.seen.append(key)
        self.sheets[key] = sheet
        if key == "bracket":
            sheet.in_chk.setChecked(True)
            sheet.clip_chk.setChecked(True)
        if key in self.answers:
            self.answers[key](sheet)
        todo = self.then.get(key)
        press = "next"
        if todo:
            press = todo.pop(0) if len(todo) > 1 else todo[0]
        if callable(press):
            press = press(sheet)
        if press == "next":
            sheet.next_btn.click()
        elif press == "back":
            sheet.back_btn.click()
        elif press == "cancel":
            sheet.cancel_btn.click()
        elif press.startswith("go:"):
            (getattr(sheet, "change_btns", None)
             or sheet.fix_btns)[press[3:]].click()
        return sheet.outcome


def _size(w, h):
    def fill(sheet):
        sheet.w_spin.setValue(w)
        sheet.h_spin.setValue(h)
    return fill


def _pick(key):
    return lambda sheet: sheet.choice.set(key)


def _fingerprint(w):
    """Everything about a job a set of answers could have changed."""
    st, setup, page = w.state, w.inspector.setup, w.autorun_page
    from gerber2rml.gui2 import workspace
    ws = workspace._settings()
    return {
        "boards": [id(m) for m in st.boards],
        "placed": [(m.place_x, m.place_y, m.rotate) for m in st.boards],
        "folder": str(st.gerber_dir), "name": st.name,
        "jobs": (asdict(st.trace), asdict(st.drill), asdict(st.cutout)),
        "double": (w._double, setup.double.isChecked()),
        "thickness": setup.thickness.value(),
        "preset": setup.preset.currentText(),
        "method": (w._method, w._method_source, w._method_gap,
                   w.method_id(), setup.method.currentData()),
        "stock": tuple(w.stock), "stock_set": w._stock_set,
        "size": (page.w_spin.value(), page.h_spin.value(),
                 setup.stock_w.value(), setup.stock_h.value()),
        "centre": page.centre_chk.isChecked(),
        "nocut": (w._no_cutout, page.nocut_chk.isChecked()),
        "check": page.check_chk.isChecked(),
        "level": page.level_chk.isChecked(),
        "undo": (list(w.placement_undo._undo), list(w.placement_undo._redo)),
        "step": w._current_step.key if w._current_step else None,
        "plan": [(s_.key, s_.file) for s_ in w.plan],
        "settings": {k: ws.value(k) for k in ws.allKeys()
                     if k.startswith(("dirs/gerber", "autorun/centre",
                                      "sides/", "nocut/", "refs/",
                                      "method/"))},
    }


# -- a profile over Novice ----------------------------------------------------

def test_bot_is_novice_as_far_as_the_tiers_go(monkeypatch):
    for word in ("bot", "Bot", "guided"):
        monkeypatch.setenv("SRM_CAM_MODE", word)
        assert tier.current_profile() == tier.BOT
        assert tier.is_bot()
        # Everything that decides what is on screen and what is cut asks
        # this, and gets Novice's answer.
        assert tier.current_tier() == tier.ESSENTIAL and not tier.is_full()
        assert tier.pinned_tier() == tier.ESSENTIAL
    assert tier.set_tier(tier.FULL) is False            # pinned: menu is inert
    for word in ("novice", "pro"):
        monkeypatch.setenv("SRM_CAM_MODE", word)
        assert not tier.is_bot()


def test_bot_is_remembered_like_the_other_tiers(monkeypatch):
    monkeypatch.delenv("SRM_CAM_MODE", raising=False)
    kept = _Settings()
    monkeypatch.setattr(tier, "_settings", lambda: kept)
    assert tier.set_tier(tier.BOT) is True
    assert tier.current_profile() == tier.BOT and tier.is_bot()
    assert tier.current_tier() == tier.ESSENTIAL
    assert tier.set_tier(tier.ESSENTIAL) is True
    assert not tier.is_bot()
    with pytest.raises(ValueError):
        tier.set_tier("robot")


def test_the_window_in_bot_is_novices_window_with_a_way_to_be_asked(
        qt_app, monkeypatch):
    w = _window(monkeypatch)
    try:
        assert w.bot_act.isChecked() and not w.essential_act.isChecked()
        assert not w.full_act.isChecked()
        assert w.tier_chip.label.text().lower() == "bot"
        rows = list(w.traveller._rows)                  # Novice's rail
        assert rows[:3] == ["setup", "checks", "autorun"]
        assert all(k.startswith("laser_") for k in rows[3:])
        assert not w.inspector.setup.bot_btn.isHidden()
        assert w.bot_again_act.isVisible()
        assert not w.export_act.isVisible()             # Novice's rules
        assert w.autorun_page.where.isHidden()
    finally:
        _close(w)
    w = _window(monkeypatch, "novice")
    try:
        assert w.essential_act.isChecked() and not w.bot_act.isChecked()
        assert w.inspector.setup.bot_btn.isHidden()
        assert not w.bot_again_act.isVisible()
    finally:
        _close(w)


def test_choosing_bot_from_the_menu_starts_the_questions(qt_app, monkeypatch):
    monkeypatch.delenv("SRM_CAM_MODE", raising=False)
    kept = _Settings()
    monkeypatch.setattr(tier, "_settings", lambda: kept)
    w = MainWindow()
    try:
        asked = []
        monkeypatch.setattr(w, "action_bot", lambda: asked.append(True))
        w.bot_act.trigger()
        assert tier.is_bot() and asked == [True]
        assert w.bot_act.isChecked()
        assert kept.kept["ui/tier"] == tier.BOT         # there next launch
        w.essential_act.trigger()
        assert not tier.is_bot() and asked == [True]
    finally:
        _close(w)


def test_bot_exports_the_bytes_novice_exports(qt_app, monkeypatch, tmp_path):
    """Bot is not in the tier test's pair because it is not a tier. This is
    the same promise, said for it once."""
    out = {}
    for mode in ("novice", "bot"):
        w = _window(monkeypatch, mode)
        try:
            out[mode] = tmp_path / mode
            w.export_to(out[mode])
        finally:
            _close(w)
    names = sorted(p.name for p in out["novice"].iterdir())
    assert names == sorted(p.name for p in out["bot"].iterdir())
    for name in names:
        if name.endswith("_runplan.txt"):
            continue
        assert ((out["novice"] / name).read_bytes()
                == (out["bot"] / name).read_bytes()), name


# -- the questions, in order --------------------------------------------------

def test_the_questions_come_in_the_order_the_job_is_set_up(qt_app,
                                                           monkeypatch):
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        hands = Hands(answers={"sheet": _size(150, 124)},
                      then={"summary": ["cancel"]})
        b = botmod.Bot(w)
        b.ask = hands
        assert b.run() is False
        assert hands.seen == ORDER + ["summary"]
        # Each says where it is in the run of them.
        assert hands.sheets["board"].where.text() == "Question 1 of 10"
        assert hands.sheets["machine"].where.text() == "Question 10 of 10"
        assert not hands.sheets["board"].back_btn.isVisibleTo(
            hands.sheets["board"])                      # nowhere to go back to
        assert hands.sheets["sides"].back_btn.isVisibleTo(
            hands.sheets["sides"])
    finally:
        _close(w)


def test_a_laser_board_is_not_asked_what_the_mill_does_not_do(qt_app,
                                                             monkeypatch):
    """On a laser board the mill drills and cuts out: one side whatever the
    copper, and no tracks of its own to stop and look at."""
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        hands = Hands(answers={"method": _pick(fab.LASER),
                               "sheet": _size(150, 124)},
                      then={"summary": ["cancel"]})
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        assert hands.seen == [k for k in ORDER
                              if k not in ("sides", "check")] + ["summary"]
        summary = hands.sheets["summary"]
        assert "sides" not in summary.rows and "check" not in summary.rows
        assert summary.rows["method"].text() == fab.name(fab.LASER)
        assert any("laser" in line for line in summary.facts)
        assert hands.sheets["machine"].where.text() == "Question 8 of 8"
    finally:
        _close(w)


def test_the_look_at_the_tracks_is_asked_with_or_without_a_cut_out(
        qt_app, monkeypatch):
    """Left in the sheet there is no cut-out to stop before, and the stop is
    wanted all the same: it comes after the tracks, and the question says
    so."""
    w = _window(monkeypatch)
    try:
        b = botmod.Bot(w)
        q = b.question("check")
        assert q.asked(b)
        assert "before the cut-out" in q.headline(b)
        b.answers["cutout"] = "leave"
        assert q.asked(b)
        assert q.headline(b) == ("Stop after the tracks, so you can look at "
                                 "them?")
    finally:
        _close(w)


def test_back_works_at_every_question_and_keeps_what_was_answered(
        qt_app, monkeypatch):
    """Back the first time each question comes up, then on. Every question
    is therefore reached from both sides, with its answer still in it."""
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)

        def thick(sheet):
            if len([k for k in hands.seen if k == "thickness"]) == 1:
                sheet.spin.setValue(1.5)                # typed once...
            else:
                assert sheet.spin.value() == 1.5        # ...and still there

        then = {k: ["back", "next"] for k in ORDER[1:]}
        then["summary"] = ["back", "cancel"]
        hands = Hands(answers={"sheet": _size(150, 124), "thickness": thick},
                      then=then)
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        expect = ["board"]
        for prev, key in zip(ORDER, ORDER[1:]):
            expect += [key, prev, key]
        expect += ["summary", "machine", "summary"]
        assert hands.seen == expect
        assert b.answers["thickness"] == 1.5
    finally:
        _close(w)


# -- Cancel -------------------------------------------------------------------

def test_cancel_while_being_asked_has_touched_nothing(qt_app, monkeypatch,
                                                      coupon):
    w = _window(monkeypatch)
    try:
        before = _fingerprint(w)
        hands = Hands(
            answers={"board": lambda s: s.take(str(coupon)),
                     "method": _pick(fab.LASER),
                     "thickness": lambda s: s.spin.setValue(0.8)},
            then={"sheet": ["cancel"]})
        b = botmod.Bot(w)
        b.ask = hands
        assert b.run() is False
        assert hands.seen == ["board", "method", "thickness", "sheet"]
        # Not put back: never changed. No snapshot was needed.
        assert b.before is None
        assert _fingerprint(w) == before
    finally:
        _close(w)


def _everything_else(coupon=None):
    """Answers that differ from the defaults at every question. (How it is
    made is answered "flat mill": the tests that use this start the window
    on the ball mill, so that it is a change.)"""
    out = {"sides": _pick("one"), "method": _pick(fab.FLAT),
           "thickness": lambda s: s.spin.setValue(1.5),
           "sheet": _size(150, 124), "where": _pick("centre"),
           "cutout": _pick("cut"), "check": _pick(False)}
    if coupon is not None:
        out["board"] = lambda s: s.take(str(coupon))
    return out


def test_cancel_on_the_summary_puts_everything_back(qt_app, monkeypatch,
                                                    coupon):
    """The answers are in the app by then - another board, another bit,
    another sheet - and Cancel has to take all of it out again."""
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        w.action_method(fab.BALL)
        w.select_step("checks")
        first = w.state.boards[0]
        before = _fingerprint(w)
        assert before["method"][3] == fab.BALL
        assert before["jobs"][0]["bit_diameter"] == 0.44

        def at_summary(sheet):
            assert w.state.boards[0] is not first       # it really was applied
            assert _fingerprint(w) != before
            return "cancel"
        hands = Hands(answers=_everything_else(coupon),
                      then={"summary": [at_summary]})
        b = botmod.Bot(w)
        b.ask = hands
        assert b.run() is False
        assert w.state.boards[0] is first
        assert _fingerprint(w) == before
        assert w.traveller.current() == "checks"
        # The link is the one thing left: a button pressed, not an answer.
        assert w.link.is_connected()
    finally:
        _close(w)


def test_cancel_with_no_board_open_before_leaves_none_open(qt_app,
                                                           monkeypatch):
    w = _window(monkeypatch, folder=None)
    try:
        _sim(qt_app, w)
        before = _fingerprint(w)
        hands = Hands(answers=dict(_everything_else(FIXT),
                                   sides=_pick("both")),
                      then={"summary": ["cancel"]})
        b = botmod.Bot(w)
        b.ask = hands
        assert b.run() is False
        assert hands.seen[-1] == "summary"
        assert w.state.board is None and not w.state.boards
        assert _fingerprint(w) == before
    finally:
        _close(w)


def test_the_real_sheets_are_modal_and_closing_one_is_cancel(qt_app,
                                                             monkeypatch):
    """Without a stand-in: the sheet is shown with exec(), and dismissed the
    way the window's close box or Esc would."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    w = _window(monkeypatch)
    try:
        before = _fingerprint(w)
        shown = []

        def dismiss():
            sheet = QApplication.activeModalWidget()
            shown.append(sheet.windowTitle() if sheet else None)
            if sheet is not None:
                sheet.reject()
        QTimer.singleShot(50, dismiss)
        assert w.action_bot() is False
        assert shown == ["Which board are we making?"]
        assert w._bot is None                           # and it can be asked again
        assert _fingerprint(w) == before
    finally:
        _close(w)


# -- each answer, in the control a hand would have used -----------------------

def test_every_answer_lands_in_its_control(qt_app, monkeypatch, coupon):
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        w.select_step("setup")
        setup, page = w.inspector.setup, w.autorun_page

        def at_summary(sheet):
            st = w.state
            assert Path(st.gerber_dir) == coupon        # the board
            assert not setup.double.isChecked() and not w._double
            assert w.method_id() == fab.FLAT            # how it is made...
            assert setup.method.currentData() == fab.FLAT
            assert st.trace.bit_diameter == 0.8         # ...and its numbers
            assert setup.thickness.value() == 1.5
            # The thickness is what the through-cuts are derived from.
            deep = 1.5 + setup.overshoot.value()
            assert st.drill.total_depth == pytest.approx(deep)
            assert st.cutout.total_depth == pytest.approx(deep)
            # Typed on Your board's own fields, and passed on to the run.
            assert (setup.stock_w.value(), setup.stock_h.value()) \
                == (150, 124)
            assert (page.w_spin.value(), page.h_spin.value()) == (150, 124)
            assert tuple(w.stock[2:4]) == (150, 124) and w._stock_set
            assert page.bracket()
            assert page.centre_chk.isChecked()
            assert not page.nocut_chk.isChecked() and not w._no_cutout
            assert not page.check_chk.isChecked()
            # Centred means centred: the board in the middle of the sheet.
            x0, y0, x1, y1 = w.work_bounds()
            sx, sy, sw, sh = w.stock
            assert x0 - sx == pytest.approx(sx + sw - x1, abs=1e-6)
            assert y0 - sy == pytest.approx(sy + sh - y1, abs=1e-6)
            # ...and the Run page is what is behind the summary.
            assert w.inspector.stack.currentWidget() is page
            rows = {k: lb.text() for k, lb in sheet.rows.items()}
            assert rows["thickness"] == "1.50 mm"
            assert rows["sheet"] == "150.0 × 124.0 mm"
            assert rows["method"] == fab.name(fab.FLAT)
            assert any("0.80 mm flat endmill" in line for line in sheet.facts)
            assert rows["machine"] == "Connected, on SIM"
            return "cancel"
        hands = Hands(answers=_everything_else(coupon),
                      then={"summary": [at_summary]})
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        assert hands.seen[-1] == "summary"
    finally:
        _close(w)


def test_the_other_answers_land_too(qt_app, monkeypatch):
    """The opposite choice at every either-or, on the two-sided board."""
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        setup, page = w.inspector.setup, w.autorun_page
        page.centre_chk.setChecked(True)
        setup.double.setChecked(False)

        def at_summary(sheet):
            assert setup.double.isChecked() and w._double
            assert w.plan.by_key("flip") is not None
            assert not page.centre_chk.isChecked()
            assert page.nocut_chk.isChecked() and w._no_cutout
            assert page.no_cutout()
            return "cancel"
        hands = Hands(answers={"sides": _pick("both"),
                               "sheet": _size(150, 124),
                               "where": _pick("corner"),
                               "cutout": _pick("leave")},
                      then={"summary": [at_summary]})
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        assert hands.seen[-1] == "summary"
    finally:
        _close(w)


def test_how_it_is_made_lands_and_brings_its_tool_with_it(qt_app,
                                                         monkeypatch):
    """The board is open on the ball mill; the answer is the flat mill. It
    goes in through Your board's own How it is made, which brings the flat
    mill's profile with it - and Cancel brings the ball mill's back."""
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        setup = w.inspector.setup
        w.action_method(fab.BALL, "kicad")
        before = _fingerprint(w)
        assert w.state.trace.bit_diameter == 0.44

        def at_summary(sheet):
            assert w.method_id() == fab.FLAT
            assert setup.method.currentData() == fab.FLAT
            assert w._method_source == "chosen"         # by the operator, now
            assert w.state.trace.bit_diameter == 0.8
            assert "flat" in setup.preset.currentText()
            return "cancel"
        hands = Hands(answers={"method": _pick(fab.FLAT),
                               "sheet": _size(150, 124)},
                      then={"summary": [at_summary]})
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        # The question said where the choice it showed came from.
        assert hands.sheets["method"].choice.keys() == list(fab.IDS)
        assert hands.seen[-1] == "summary"
        assert _fingerprint(w) == before
        assert w._method_source == "kicad"
    finally:
        _close(w)


def test_a_laser_board_is_set_up_as_the_mill_runs_it_and_put_back(
        qt_app, monkeypatch):
    """Laser copper turns a two-sided board into a one-sided job for the
    mill and takes the levelling off. All of that is the app's own doing,
    from the one answer; and all of it is undone by Cancel."""
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        setup, page = w.inspector.setup, w.autorun_page
        setup.double.setChecked(True)
        before = _fingerprint(w)

        def at_summary(sheet):
            assert w.method_id() == fab.LASER and w.laser_method()
            assert not w._double                        # one side, on the mill
            assert not page.level_chk.isChecked()       # nothing to level for
            assert not any("touches the copper" in f for f in sheet.facts)
            return "cancel"
        hands = Hands(answers={"method": _pick(fab.LASER),
                               "sheet": _size(150, 124)},
                      then={"summary": [at_summary]})
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        assert hands.seen[-1] == "summary"
        assert _fingerprint(w) == before
        assert w._double and setup.double.isChecked()
        assert page.level_chk.isChecked()
    finally:
        _close(w)


def test_a_job_that_needs_two_bits_is_stopped_before_start(qt_app,
                                                           monkeypatch):
    """Start refuses a plan with more than one bit: the run never stops for
    a bit change. Bot says so while the answer can still be changed."""
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        w.state.drill.bit_diameter = 1.0                # a second bit
        w.action_params_changed()
        assert len(w.plan.tools) == 2

        def blocked(sheet):
            (f,) = [f for f in sheet.findings if "more than one bit" in f.title]
            assert f.question == "method" and "2 different bits" in f.detail
            return "cancel"
        hands = Hands(answers={"sides": _pick("one"),
                               "sheet": _size(150, 124)},
                      then={"findings": [blocked]})
        b = botmod.Bot(w)
        b.ask = hands
        assert b.run() is False
        assert hands.seen[-1] == "findings"
    finally:
        _close(w)


def test_what_is_open_is_the_answer_already_filled_in(qt_app, monkeypatch):
    """A question shows what the control says now, so Next all the way
    through changes nothing that was set by hand."""
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        setup, page = w.inspector.setup, w.autorun_page
        setup.double.setChecked(True)
        setup.thickness.setValue(1.2)
        page.w_spin.setValue(140)
        page.h_spin.setValue(124)
        page.check_chk.setChecked(False)
        hands = Hands(then={"summary": ["cancel"]})
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        s = hands.sheets
        assert s["board"].folder is None                # the one that is open
        assert "buck" in s["board"].found.text()
        assert s["method"].choice.value() == fab.FLAT   # what the tool says
        assert s["sides"].choice.value() == "both"      # as it was ticked
        assert s["thickness"].spin.value() == 1.2
        assert (s["sheet"].w_spin.value(), s["sheet"].h_spin.value()) \
            == (140, 124)
        assert s["where"].choice.value() == "corner"
        assert s["cutout"].choice.value() == "cut"
        assert s["check"].choice.value() is False
    finally:
        _close(w)


# -- answers that will not do -------------------------------------------------

def test_an_answer_that_cannot_be_right_is_refused_on_its_own_sheet(
        qt_app, monkeypatch, coupon, tmp_path):
    w = _window(monkeypatch, folder=None)
    try:
        b = botmod.Bot(w)

        def sheet_for(key):
            return b.sheet_for(b.question(key))

        # No board, and a folder that is not one.
        s = sheet_for("board")
        s.next_btn.click()
        assert s.outcome == botmod.CANCEL and "Choose the folder" \
            in s.problem.text()
        (tmp_path / "empty").mkdir()
        s.take(str(tmp_path / "empty"))
        assert s.folder is None and "could not be read" in s.problem.text()
        s.take(str(coupon))
        assert s.folder == str(coupon) and not s.problem.text()
        s.next_btn.click()
        assert s.outcome == botmod.NEXT

        # A sheet smaller than the board.
        s = sheet_for("sheet")
        s.w_spin.setValue(20)
        s.h_spin.setValue(20)
        s.next_btn.click()
        assert s.outcome == botmod.CANCEL and "does not fit" in s.problem.text()

        # Both ticks, not one.
        s = sheet_for("bracket")
        s.in_chk.setChecked(True)
        s.next_btn.click()
        assert s.outcome == botmod.CANCEL and s.problem.text()
        s.clip_chk.setChecked(True)
        s.next_btn.click()
        assert s.outcome == botmod.NEXT

        # No machine, no further.
        s = sheet_for("machine")
        assert "Not connected" in s.status.text()
        s.next_btn.click()
        assert s.outcome == botmod.CANCEL and "not connected" \
            in s.problem.text()
        s.reject()
    finally:
        _close(w)


def test_both_sides_needs_copper_on_both_layers(qt_app, monkeypatch,
                                                tmp_path):
    """A folder with one copper layer cannot be milled on two sides."""
    import shutil
    one = tmp_path / "one_sided"
    shutil.copytree(FIXT, one)
    for p in one.glob("*F_Cu*"):
        p.unlink()
    w = _window(monkeypatch, folder=one)
    try:
        b = botmod.Bot(w)
        s = b.sheet_for(b.question("sides"))
        assert s.choice.value() == "one"
        s.choice.set("both")
        s.next_btn.click()
        assert s.outcome == botmod.CANCEL
        assert "one layer only" in s.problem.text()
        s.choice.set("one")
        s.next_btn.click()
        assert s.outcome == botmod.NEXT and b.answers["sides"] == "one"
    finally:
        _close(w)


def test_the_connect_question_follows_the_link(qt_app, monkeypatch):
    w = _window(monkeypatch)
    try:
        b = botmod.Bot(w)
        s = b.sheet_for(b.question("machine"))
        assert not s.connect_btn.isHidden()
        _sim(qt_app, w)
        assert _wait(qt_app, lambda: "Connected" in s.status.text(), 5)
        assert s.connect_btn.isHidden()
        s.next_btn.click()
        assert s.outcome == botmod.NEXT
        # Closed, it is told nothing more.
        w.link.disconnect_from("test")
        qt_app.processEvents()
        assert "Connected" in s.status.text()
    finally:
        _close(w)


# -- the checks ---------------------------------------------------------------

def test_a_failed_check_is_said_in_plain_words_with_the_way_to_fix_it(
        qt_app, monkeypatch):
    """A sheet the board fits on, but not with the room the cut-out and the
    bracket's tongue need: said before the summary, with a button to the
    question that changes it, and from there straight back."""
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        sizes = [_size(105, 105), _size(150, 124)]

        def sheet_size(sheet):
            sizes.pop(0)(sheet)

        def blocked(sheet):
            assert sheet.next_btn is None               # no way on from here
            f = sheet.findings[0]
            assert f.level == "fail" and f.question == "sheet"
            assert "too small" in f.title
            assert "105.0 × 105.0 mm" in f.detail
            assert "sheet" in sheet.fix_btns
            # Said once, not twice in two vocabularies.
            assert len(sheet.findings) == 1
            return "go:sheet"
        hands = Hands(answers={"sides": _pick("one"), "sheet": sheet_size},
                      then={"findings": [blocked], "summary": ["cancel"]})
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        # Fixed, it goes back to the checks and on - not through every
        # question after it again.
        assert hands.seen == ORDER + ["findings", "sheet", "summary"]
    finally:
        _close(w)


def test_a_board_with_shorted_nets_does_not_reach_start(qt_app, monkeypatch):
    """The fixture as the rest of the suite loads it: pads as their bounding
    boxes, and thirteen places where two nets are closer than the bit."""
    from gerber2rml import loader
    monkeypatch.setattr(loader, "PAD_SHAPES", "boxes")
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)

        def blocked(sheet):
            assert sheet.next_btn is None
            (f,) = [f for f in sheet.findings if "Nets" in f.title]
            assert f.question == "method" and "KiCad" in f.detail
            assert "method" in sheet.fix_btns
            return "cancel"
        hands = Hands(answers={"sheet": _size(150, 124)},
                      then={"findings": [blocked]})
        before = _fingerprint(w)
        b = botmod.Bot(w)
        b.ask = hands
        assert b.run() is False
        assert hands.seen == ORDER + ["findings"]
        assert _fingerprint(w) == before                # Cancel, from here too
    finally:
        _close(w)


def test_one_answer_changed_from_the_summary_comes_straight_back(qt_app,
                                                                 monkeypatch):
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        hands = Hands(answers={"sheet": _size(150, 124)},
                      then={"summary": ["go:thickness", "cancel"]})
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        assert hands.seen == ORDER + ["summary", "thickness", "summary"]
    finally:
        _close(w)


def test_checks_are_put_in_the_words_of_the_answers():
    f = botmod.in_plain_words(diag.Check(
        "fail", "Nets closer than the bit", "3 spot(s) where two SEPARATE "
        "nets sit closer than 0.80 mm."))
    assert f.level == "fail" and f.question == "method"
    assert "KiCad" in f.detail and "SEPARATE" not in f.detail
    f = botmod.in_plain_words(diag.Check(
        "fail", "Drill depth far below the stock", "x"))
    assert f.question == "thickness"
    # One nobody wrote advice for keeps its own words, and says who to ask.
    f = botmod.in_plain_words(diag.Check(
        "fail", "A rapid would hit a screw head", "the lift is 2 mm."))
    assert f.question is None
    assert f.detail.startswith("The lift is 2 mm.")
    assert "whoever looks after the mill" in f.detail
    # Every question a piece of advice points at exists.
    keys = {q.key for q in botmod.QUESTIONS}
    assert all(q is None or q in keys for _p, _w, q in botmod._WHAT_TO_DO)


def test_the_checks_page_was_run_on_the_job_the_summary_shows(qt_app,
                                                              monkeypatch):
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        ran = []
        real = w.refresh_checks
        monkeypatch.setattr(w, "refresh_checks",
                            lambda: (ran.append(tuple(w.stock[2:4])), real()))

        def at_summary(sheet):
            # After the answers were in, on the sheet that was answered.
            assert ran and ran[-1] == (150, 124)
            assert all(f.level == "warn" for f in sheet.warnings)
            assert not any(c.level == "fail" for c in w.checks())
            return "cancel"
        hands = Hands(answers={"sheet": _size(150, 124)},
                      then={"summary": [at_summary]})
        b = botmod.Bot(w)
        b.ask = hands
        b.run()
        assert hands.seen[-1] == "summary"
    finally:
        _close(w)


# -- not while something else is going on ------------------------------------

def test_no_questions_while_a_run_is_going(qt_app, monkeypatch):
    w = _window(monkeypatch)
    try:
        w.autorun_page._thread = object()
        b = botmod.Bot(w)
        b.ask = lambda sheet: pytest.fail("asked during a run")
        assert b.run() is False
    finally:
        w.autorun_page._thread = None
        _close(w)


def test_a_board_from_kicad_waits_for_the_questions_to_end(qt_app,
                                                           monkeypatch,
                                                           coupon):
    w = _window(monkeypatch)
    try:
        w._bot = object()
        assert w.open_from_outside(str(coupon)) is False
        assert Path(w.state.gerber_dir) == FIXT
        assert w.action_bot() is False                  # nor a second set
    finally:
        w._bot = None
        _close(w)


# -- start to finish ----------------------------------------------------------

def test_bot_runs_a_board_from_open_a_board_to_the_board_is_done(
        qt_app, monkeypatch, tmp_path):
    """Nothing loaded, the mill switched off. Every question answered, Start,
    and a double-sided board comes off the simulated mill - with the run's
    own prompts, the flip and the look before the cut-out, where Novice has
    them."""
    from gerber2rml import platform as plat
    monkeypatch.setattr(plat, "documents_dir", lambda *a, **k: tmp_path)
    w = _window(monkeypatch, folder=None)
    try:
        page = w.autorun_page
        assert w.state.board is None

        def connect(sheet):
            assert not sheet.connect_btn.isHidden()
            sm = _sim(qt_app, w)                        # the mill, switched on
            assert _wait(qt_app, sheet.connect_btn.isHidden, 5)
            return sm
        hands = Hands(answers={"board": lambda s: s.take(str(FIXT)),
                               "sides": _pick("both"),
                               "sheet": _size(150, 124),
                               "where": _pick("corner"),
                               "machine": connect})
        done = []
        page.finished.connect(lambda ok, msg: done.append((ok, msg)))
        b = botmod.Bot(w)
        b.ask = hands
        assert b.run() is True
        assert hands.seen == ORDER + ["summary"]
        assert b.before is None                         # nothing to take back
        seen = {"flip": False, "check": False}

        def step():
            if not page.flip_btn.isHidden():
                seen["flip"] = True
                page.flip_btn.click()
            if not page.check_box.isHidden():
                seen["check"] = True
                page.cut_btn.click()
            return bool(done)
        assert _wait(qt_app, step), "the run never finished"
        assert done[0] == (True, "The board is done.")
        assert seen == {"flip": True, "check": True}
        from gerber2rml.engine import simmachine as sm
        assert sm.machine().lines, "nothing was cut"
        assert _wait(qt_app, w.link.is_connected, 15)   # the link is back
    finally:
        _close(w)


def test_a_run_that_stops_short_says_so_in_a_sheet_and_offers_the_questions(
        qt_app, monkeypatch, tmp_path):
    """The run has refusals of its own, made once it has found the copper.
    Here the two-sided board, centred, would come back from the flip with
    work under the bracket: nothing is cut, and Bot says what the run said."""
    from gerber2rml import platform as plat
    monkeypatch.setattr(plat, "documents_dir", lambda *a, **k: tmp_path)
    w = _window(monkeypatch)
    try:
        _sim(qt_app, w)
        page = w.autorun_page
        hands = Hands(answers={"sides": _pick("both"),
                               "sheet": _size(150, 124),
                               "where": _pick("centre")},
                      then={"stopped": ["cancel"]})
        done = []
        page.finished.connect(lambda ok, msg: done.append((ok, msg)))
        b = botmod.Bot(w)
        b.ask = hands
        assert b.run() is True
        assert _wait(qt_app, lambda: "stopped" in hands.seen, 60)
        assert done and done[0][0] is False
        sheet = hands.sheets["stopped"]
        assert sheet.stopped == done[0][1] and "bracket" in sheet.stopped
        assert sheet.next_btn.text() == "Ask me again"
        assert page._thread is None                     # the machine is idle
        from gerber2rml.engine import simmachine as sm
        assert not sm.machine().lines                   # and nothing was cut
        # Asked again, the answers are still there: they are in the controls.
        again = botmod.Bot(w)
        assert again.answer("where") == "centre"
        assert again.answer("sheet") == (150, 124)
        assert _wait(qt_app, w.link.is_connected, 15)
    finally:
        _close(w)


def test_a_run_that_finishes_raises_no_sheet(qt_app, monkeypatch):
    w = _window(monkeypatch)
    try:
        b = botmod.Bot(w)
        b.ask = lambda sheet: pytest.fail("a sheet after a good run")
        watch = botmod._RunWatch(b, w.autorun_page)
        watch.ended(True, "The board is done.")
        qt_app.processEvents()
    finally:
        _close(w)
