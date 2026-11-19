"""Bot: the app asks, the operator answers, and then it runs.

Novice already needs very little: a board, how it is made, one side or two,
the thickness, the size of the copper, Start. But it needs the operator to know
that list, and to find each thing on two pages. Someone at the mill for the
first time knows neither. Bot turns the list round: one question a pop-up, in
the order the job is set up, each with a sentence on why it is asked and the
answer already filled in where the app can guess it.

It is a layer over Novice, not a third way to make a board:

* **The answers land in Novice's own controls**, through the handlers a hand
  would have triggered - the Setup page's tick and spin boxes, the Run page's
  size and choices, and :meth:`AutoRunPage.start`. Nothing here cuts,
  places or decides a depth. What Bot starts is exactly what Novice would
  have started with the same things set, which is why ``tier.is_full()`` is
  all the rest of the app ever asks.
* **Nothing is touched while the questions are being asked.** The answers are
  kept here until the last one is in; only then are they applied, so the
  pre-flight checks can be run on the real job and the summary shows it on
  the bed behind it. Cancel before that point has nothing to undo. Cancel
  after it puts back :meth:`MainWindow.job_snapshot`.
* **Back works everywhere**, and never loses an answer already given.
* **No pop-up while the mill is moving.** A modal sheet would cover STOP.
  The run's own prompts - turn the board over, look at the traces - stay on
  the Run page, where they are in Novice. The one sheet after Start comes
  when a run has stopped without finishing, and the machine with it: the
  run has refusals of its own, made once it has found the copper, that no
  check before Start can make for it.

One thing Cancel leaves: a machine that was connected from the question that
offers it stays connected. That was a button pressed, not an answer given.
"""
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                               QDoubleSpinBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QRadioButton,
                               QScrollArea, QVBoxLayout, QWidget)

from gerber2rml.engine import method as fab
from gerber2rml.gui2 import dialogs, theme, widgets, workspace

NEXT, BACK, CANCEL, JUMP = "next", "back", "cancel", "jump"


# ---------------------------------------------------------------------------
# The parts a question is built from
# ---------------------------------------------------------------------------

class _Row(QWidget):
    """One choice: clicking anywhere on it picks it."""

    def __init__(self, radio):
        super().__init__()
        self.radio = radio

    def mousePressEvent(self, e):
        self.radio.setChecked(True)
        super().mousePressEvent(e)


class Choice(QWidget):
    """A short list to pick one from. Each entry says what it is and, in
    smaller type, why you would pick it - and both lines wrap, which a radio
    button's own text does not."""

    def __init__(self):
        super().__init__()
        self._group = QButtonGroup(self)
        self._radios = {}
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(theme.GAP_S)

    def add(self, key, text, why=""):
        radio = QRadioButton()
        radio.setAccessibleName(text)
        row = _Row(radio)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(theme.GAP_S)
        h.addWidget(radio, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        t = QLabel(text)
        t.setFont(theme.font("body"))
        t.setWordWrap(True)
        t.setStyleSheet(f"color: {theme.TEXT};")
        col.addWidget(t)
        if why:
            col.addWidget(widgets.hint(why))
        h.addLayout(col, 1)
        self._group.addButton(radio)
        self._radios[key] = radio
        self._v.addWidget(row)
        return radio

    def keys(self):
        return list(self._radios)

    def set(self, key):
        if key in self._radios:
            self._radios[key].setChecked(True)

    def value(self):
        for key, radio in self._radios.items():
            if radio.isChecked():
                return key
        return None


def spin_like(real, value):
    """A number box with the range, step and unit of the control the answer
    will land in, so that nothing can be answered that the control refuses."""
    s = QDoubleSpinBox()
    s.setRange(real.minimum(), real.maximum())
    s.setDecimals(real.decimals())
    s.setSingleStep(real.singleStep())
    s.setSuffix(real.suffix() or " mm")
    s.setValue(float(value))
    s.setKeyboardTracking(False)
    s.setMinimumWidth(110)
    return s


def scrolling(inner, parent, *, room=260, least=150, width=500):
    """``inner`` in a scroll area as tall as it needs and no taller than the
    screen has room for.

    A lab PC at 1920 x 1080 and 150 % scaling has 680 px of height. A sheet
    that lists ten answers is taller than that, and its Start would be off
    the bottom of the screen; ``room`` is what the rest of the sheet and the
    window's own frame need. On a screen with the room, nothing scrolls.
    """
    screen = parent.screen() if parent is not None else None
    screen = screen or QApplication.primaryScreen()
    free = screen.availableGeometry().height() if screen is not None else 768
    most = max(least, free - room)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(inner)
    # A scroll area asks for very little by itself, whatever is in it: say
    # what the content needs at the width the sheet gives it.
    lay = inner.layout()
    need = (lay.heightForWidth(width) if lay is not None
            and lay.hasHeightForWidth() else inner.sizeHint().height())
    sa.setMinimumHeight(min(max(need, 0) + 4, most))
    sa.setMaximumHeight(most)
    return sa


class Ask(dialogs.Sheet):
    """One question, as a sheet: where you are, the question, why it is
    asked, the answer, and Cancel / Back / Next.

    ``outcome`` is how it was left. Closing it any other way than by its
    buttons is Cancel.
    """

    def __init__(self, parent, headline, *, where="", level="info"):
        super().__init__(parent, headline, level=level, width=560)
        self.outcome = CANCEL
        self.jump = None
        self.problem = None             # where a refusal is said: buttons()
        self._check = None
        self._keep = None
        self.where = self.say(where, small=True) if where else None

    def buttons(self, *, back=True, forward="Next", check=None, keep=None,
                safe=False):
        """The action row, added last. ``check`` is run by Next and returns
        why the answer will not do, or nothing; ``keep`` stores an answer on
        the way Back. ``safe``: Enter must not be able to press ``forward``."""
        self._check, self._keep = check, keep
        self.problem = self.say("", colour=theme.CAUTION)
        self.problem.setVisible(False)
        self.cancel_btn = self.act("Cancel", on=self.reject)
        self.back_btn = self.act("Back", on=self._back)
        self.back_btn.setVisible(back)
        self.next_btn = None
        if forward:
            self.next_btn = self.act(forward, kind="primary", on=self._next,
                                     default=not safe)
        if safe or not forward:
            for b in (self.cancel_btn, self.back_btn, self.next_btn):
                if b is not None:
                    b.setAutoDefault(False)
            self.back_btn.setDefault(True)

    def complain(self, why):
        """Say why the answer will not do, on the sheet it was given on."""
        if self.problem is None:
            return                      # still being built: nothing to refuse
        self.problem.setText(why or "")
        self.problem.setVisible(bool(why))
        self.settle()

    def _next(self):
        why = self._check() if self._check is not None else None
        if why:
            self.complain(why)
            return
        self.outcome = NEXT
        self.accept()

    def _back(self):
        if self._keep is not None:
            self._keep()
        self.outcome = BACK
        self.accept()

    def go_to(self, key):
        """Leave for another question, to come back here afterwards."""
        self.outcome, self.jump = JUMP, key
        self.accept()


# ---------------------------------------------------------------------------
# The questions
# ---------------------------------------------------------------------------

class Question:
    """One thing Bot asks. ``key`` names its answer; ``name`` is what the
    summary calls it."""
    key = ""
    name = ""

    def asked(self, bot):
        """Is it asked at all, given the answers before it?"""
        return True

    def default(self, bot):
        """The answer when none has been given: what the control says now."""
        return None

    def headline(self, bot):
        return self.name

    def build(self, bot, sheet):
        """Put the question on the sheet, showing ``bot.answer(self.key)``."""

    def read(self, bot, sheet):
        """``(answer, None)``, or ``(None, why it will not do)``."""
        return None, None

    def told(self, bot):
        """The answer in words, for the summary."""
        return str(bot.answer(self.key))

    def apply(self, bot):
        """Put the answer into the control a hand would have used."""


class Board(Question):
    key, name = "board", "Board"

    def default(self, bot):
        return None                     # the one that is open

    def headline(self, bot):
        return "Which board are we making?"

    def build(self, bot, sheet):
        win = bot.win
        sheet.say("The app needs the folder KiCad plotted the board into: the "
                  "copper layers, the Edge.Cuts outline and the drill file, "
                  "all in one folder.")
        sheet.folder = bot.answer(self.key)
        sheet.found = sheet.say("", colour=theme.TEXT)

        def show():
            facts = bot.facts(sheet.folder)
            if facts is None:
                sheet.found.setText("No board is open yet.")
            else:
                sheet.found.setText(
                    "%s: %s, %.1f × %.1f mm, %d hole%s, copper on %s."
                    % ("Chosen" if sheet.folder else "Open now",
                       facts["name"], facts["w"], facts["h"], facts["holes"],
                       "" if facts["holes"] == 1 else "s",
                       "both layers" if facts["both"] else "one layer"))

        def take(folder):
            """A folder was picked: read it, and say what it is or why not."""
            if not folder:
                return
            if bot.facts(folder) is None:
                from gerber2rml.gui2.window import NOT_A_BOARD
                sheet.complain("That folder could not be read as a board. "
                               + NOT_A_BOARD)
                return
            sheet.folder = str(folder)
            sheet.complain("")
            show()
            sheet.settle()

        def choose():
            take(QFileDialog.getExistingDirectory(
                sheet, "Choose the folder KiCad plotted into",
                workspace.remembered_dir("gerber")))

        sheet.take = take
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(theme.GAP_S)
        sheet.choose_btn = widgets.button(
            "Choose the Gerber folder…", on=choose,
            tip="The folder KiCad's Plot wrote: the copper layers, Edge.Cuts "
                "and the .drl drill file.")
        sheet.demo_btn = widgets.button(
            "Use the demo board", on=lambda: take(win.demo_folder()),
            tip="A small board that comes with the app, to try the whole "
                "thing without plotting anything from KiCad.")
        h.addWidget(sheet.choose_btn)
        h.addWidget(sheet.demo_btn)
        h.addStretch(1)
        sheet.add(row)
        if win.state.board is not None:
            sheet.say("Next keeps the board that is open, unless you choose "
                      "another.", small=True)
        show()

    def read(self, bot, sheet):
        if sheet.folder is None and bot.win.state.board is None:
            return None, ("Choose the folder your board is in first - or use "
                          "the demo board to try the machine.")
        return sheet.folder, None

    def told(self, bot):
        facts = bot.facts(bot.answer(self.key))
        return "%s, %.1f × %.1f mm" % (facts["name"], facts["w"], facts["h"])

    def apply(self, bot):
        win, folder = bot.win, bot.answer(self.key)
        if folder and folder != bot.opened:
            win.open_board(folder)
            got = win.state.gerber_dir
            if got is None or Path(got) != Path(folder):
                bot.trouble.append(Finding(
                    "fail", "The board could not be opened",
                    "The folder read a moment ago will not read now. Go back "
                    "and choose it again.", self.key))
                return
            bot.opened = folder
        # The Run page behind the summary: the copper, the bracket and the
        # board on it are what the answers are about.
        win.select_step("autorun")


class Method(Question):
    """How the board's copper is made, which is also which bit is in the
    collet: the same choice as the chip at the top and Your board."""
    key, name = "method", "How it is made"

    _WHY = {"kicad": "KiCad says this board is made on the %s.",
            "chosen": "You chose the %s for this board before.",
            "guessed": "The app's guess for this board is the %s",
            "profile": "The tool profile in use is the %s's."}

    def default(self, bot):
        folder = bot.answer("board")
        if folder is None:
            return bot.win.method_id()
        return bot.facts(folder)["method"][0]

    def headline(self, bot):
        return "How is the board made?"

    def build(self, bot, sheet):
        win, folder = bot.win, bot.answer("board")
        sheet.say("This decides which bit goes in the machine, and how "
                  "close together two tracks may be. One bit cuts the whole "
                  "board: the tracks, the holes and the cut-out.")
        sheet.choice = Choice()
        for mid in fab.IDS:
            sheet.choice.add(mid, fab.name(mid), fab.METHODS[mid].about)
        sheet.choice.set(bot.answer(self.key))
        sheet.add(sheet.choice)
        if folder is None:
            mid, src, gap = (win.method_id(), win._method_source,
                             win._method_gap)
        else:
            mid, src, gap = bot.facts(folder)["method"]
        why = self._WHY.get(src, self._WHY["profile"]) \
            % fab.METHODS[mid].short.lower()
        if src == "guessed":
            why += (": its narrowest gap between two tracks is %.2f mm."
                    % gap if gap else
                    ": no gap between two tracks is narrower than the flat "
                    "bit.")
        sheet.say(why, small=True)
        sheet.say("Look at the bit that is in the collet. If you are not "
                  "sure it is the one your choice names, ask before you "
                  "start: the wrong bit breaks, or leaves tracks joined.",
                  small=True)

    def read(self, bot, sheet):
        v = sheet.choice.value()
        if v is None:
            return None, "Choose how the board is made."
        return v, None

    def told(self, bot):
        return fab.name(bot.answer(self.key))

    def apply(self, bot):
        win, mid = bot.win, bot.answer(self.key)
        if win.method_id() == mid:
            return                      # as the board, KiCad or the app said
        combo = win.inspector.setup.method
        combo.setCurrentIndex(combo.findData(mid))
        if win.method_id() != mid:
            bot.trouble.append(Finding(
                "fail", "The board could not be set up that way",
                "Go back and choose another way of making it.", self.key))


class Sides(Question):
    key, name = "sides", "Sides"

    def asked(self, bot):
        # A laser board is milled as a one-sided job whatever its copper:
        # the holes go through, and both faces are the laser's.
        return bot.answer("method") != fab.LASER

    def default(self, bot):
        folder = bot.answer("board")
        if folder is None:
            return "both" if bot.win._double else "one"
        facts = bot.facts(folder)
        return "both" if facts and facts["both"] else "one"

    def headline(self, bot):
        return "Copper on one side, or on both?"

    def build(self, bot, sheet):
        facts = bot.facts(bot.answer("board"))
        sheet.say("Most first boards have all their tracks on one side. A "
                  "board with tracks on both is milled on one face, turned "
                  "over, and milled on the other.")
        sheet.choice = Choice()
        sheet.choice.add("one", "One side",
                         "Only the bottom copper layer is milled.")
        sheet.choice.add("both", "Both sides",
                         "The run drills its own reference holes, asks you "
                         "to turn the board over, finds the holes again and "
                         "cuts the top to match.")
        sheet.choice.set(bot.answer(self.key))
        sheet.add(sheet.choice)
        sheet.say("This board has copper on both layers in KiCad."
                  if facts["both"] else
                  "This board has copper on one layer only in KiCad.",
                  small=True)

    def read(self, bot, sheet):
        v = sheet.choice.value()
        if v == "both":
            if not bot.facts(bot.answer("board"))["both"]:
                return None, ("This folder has copper on one layer only, so "
                              "there is nothing to mill on a second side. "
                              "Choose one side - or plot F.Cu from KiCad as "
                              "well and open the folder again.")
            st = bot.win.state
            if bot.answer("board") is None and st.is_panel:
                return None, ("Both sides is for one board at a time, and "
                              "this sheet has %d." % len(st.boards))
        return v, None

    def told(self, bot):
        return ("Both sides - you will be asked to turn it over"
                if bot.answer(self.key) == "both" else "One side")

    def apply(self, bot):
        win, both = bot.win, bot.answer(self.key) == "both"
        win.inspector.setup.double.setChecked(both)
        if bool(win._double) != both:
            bot.trouble.append(Finding(
                "fail", "This board cannot be set up as double-sided",
                "Go back and choose one side.", self.key))


class Thickness(Question):
    key, name = "thickness", "Board thickness"

    def default(self, bot):
        return bot.win.inspector.setup.thickness.value()

    def headline(self, bot):
        return "How thick is the board?"

    def build(self, bot, sheet):
        sheet.say("Measure the blank with calipers, copper and all, rather "
                  "than trusting the label. The holes and the cut-out go "
                  "this deep and a little more, so that they come through "
                  "cleanly without digging into the bed.")
        sheet.spin = spin_like(bot.win.inspector.setup.thickness,
                               bot.answer(self.key))
        sheet.add(widgets.Field("Thickness", sheet.spin))
        sheet.say("The usual blank is 1.6 mm.", small=True)

    def read(self, bot, sheet):
        return sheet.spin.value(), None

    def told(self, bot):
        return "%.2f mm" % bot.answer(self.key)

    def apply(self, bot):
        setup, v = bot.win.inspector.setup, bot.answer(self.key)
        setup.thickness.setValue(v)
        # Said again even when the number has not changed: the bit's profile
        # has just been applied, and its depths are for a board of its own.
        setup._on_thickness(v)


class SheetSize(Question):
    key, name = "sheet", "Piece of copper"

    def default(self, bot):
        setup = bot.win.inspector.setup
        return (setup.stock_w.value(), setup.stock_h.value())

    def headline(self, bot):
        return "How big is the piece of copper?"

    def build(self, bot, sheet):
        # The run measures from the Run page's two boxes, which is where
        # Your board's send the number: theirs is the range that counts.
        page = bot.win.autorun_page
        w, h = bot.answer(self.key)
        facts = bot.facts(bot.answer("board"))
        sheet.say("The whole blank that goes in the machine, not the board "
                  "drawn in KiCad. Measure both edges with a ruler. The run "
                  "uses this to know where the copper ends.")
        sheet.w_spin = spin_like(page.w_spin, w)
        sheet.h_spin = spin_like(page.h_spin, h)
        sheet.add(widgets.Field(
            "Width", sheet.w_spin,
            help="Left to right, as it lies in the machine."))
        sheet.add(widgets.Field(
            "Height", sheet.h_spin,
            help="Front to back, as it lies in the machine."))
        sheet.say("Your board is %.1f × %.1f mm, and needs a few millimetres "
                  "of copper round it." % (facts["w"], facts["h"]),
                  small=True)

    def read(self, bot, sheet):
        w, h = sheet.w_spin.value(), sheet.h_spin.value()
        facts = bot.facts(bot.answer("board"))
        if facts["w"] > w + 1e-6 or facts["h"] > h + 1e-6:
            return None, ("The board is %.1f × %.1f mm, which does not fit "
                          "on a piece %.1f × %.1f mm. Measure again - or "
                          "take a bigger piece of copper."
                          % (facts["w"], facts["h"], w, h))
        return (w, h), None

    def told(self, bot):
        return "%.1f × %.1f mm" % bot.answer(self.key)

    def apply(self, bot):
        # Sheet width and Sheet height on Your board. In Novice their
        # handler passes the size on to the Run page, which draws the sheet
        # from the bracket and puts the board on it.
        setup = bot.win.inspector.setup
        w, h = bot.answer(self.key)
        setup.stock_w.setValue(w)
        setup.stock_h.setValue(h)


class InTheMachine(Question):
    key, name = "bracket", "In the machine"

    def default(self, bot):
        return False                    # looked at afresh every time

    def headline(self, bot):
        return "Is the copper in the machine?"

    @staticmethod
    def _tongue(bot):
        """Is the bracket on the bed the printed one with the probe tongue?
        A plain L has nothing over the stock, and the wire is clipped on."""
        return bool(bot.win.autorun_page._bracket.tongue)

    def build(self, bot, sheet):
        corner = bot.win.autorun_page.datum()[2].replace("-", " ")
        tongue = self._tongue(bot)
        sheet.say("Stick the copper down flat with double-sided tape, with "
                  "its %s corner pushed into the corner bracket: tight "
                  "against both sides%s. The run measures everything from "
                  "that corner."
                  % (corner, " and under the bracket's tongue" if tongue
                     else ""))
        sheet.say("The machine finds the surface by touch: when the bit "
                  "meets the copper a circuit closes through the probe "
                  "wire. Without the wire on the copper the bit cannot tell "
                  "when it has arrived.")
        done = bool(bot.answer(self.key))
        sheet.in_chk = QCheckBox("The copper is taped down, in the bracket's "
                                 "corner")
        sheet.clip_chk = QCheckBox(
            "The bracket's tongue, with the probe wire, is down on bare "
            "copper" if tongue else
            "The probe wire is clipped to bare copper")
        for box in (sheet.in_chk, sheet.clip_chk):
            box.setChecked(done)
            sheet.add(box)
        if not tongue:
            sheet.say("Clip it at the edge of the copper, away from where "
                      "the board will be cut.", small=True)

    def read(self, bot, sheet):
        if not (sheet.in_chk.isChecked() and sheet.clip_chk.isChecked()):
            return None, ("Both have to be true before the machine can "
                          "start. Tick each one when it is done.")
        return True, None

    def told(self, bot):
        return ("In the corner bracket, under its probe tongue"
                if self._tongue(bot) else
                "In the corner bracket, probe wire clipped to the copper")

    def apply(self, bot):
        # Novice's stock is always in the bracket; said for whoever reads on.
        where = bot.win.autorun_page.where
        where.setCurrentIndex(where.findData("bracket"))


class Where(Question):
    key, name = "where", "Where on the copper"

    def default(self, bot):
        return ("centre" if bot.win.autorun_page.centre_chk.isChecked()
                else "corner")

    def headline(self, bot):
        return "Where on the copper should the board be cut?"

    def build(self, bot, sheet):
        sheet.say("The piece of copper is usually bigger than the board. "
                  "This decides which part of it is used.")
        sheet.choice = Choice()
        sheet.choice.add("corner", "In the corner, next to the bracket",
                         "The rest of the piece stays whole for the next "
                         "board. The usual choice.")
        sheet.choice.add("centre", "In the middle of the piece",
                         "The spare copper is shared equally on every side.")
        sheet.choice.set(bot.answer(self.key))
        sheet.add(sheet.choice)

    def read(self, bot, sheet):
        return sheet.choice.value(), None

    def told(self, bot):
        return ("In the middle of the piece"
                if bot.answer(self.key) == "centre"
                else "In the corner, next to the bracket")

    def apply(self, bot):
        page = bot.win.autorun_page
        if bot.answer(self.key) == "centre":
            page.centre_chk.setChecked(True)
        else:
            # The page's own Reset position: centring off, and the board
            # back in the corner even if it was dragged away from it.
            page.reset_position()


class CutOut(Question):
    key, name = "cutout", "Cut-out"

    def default(self, bot):
        return "leave" if bot.win.autorun_page.no_cutout() else "cut"

    def headline(self, bot):
        return "Should the board be cut free of the copper?"

    def build(self, bot, sheet):
        sheet.say("The last thing the run does is cut round the board's "
                  "outline, so that it comes away from the rest of the "
                  "piece.")
        sheet.choice = Choice()
        sheet.choice.add("cut", "Yes, cut it out",
                         "The board is cut round its outline and left held "
                         "by a few small tabs%s. The usual choice."
                         % (", so the whole piece can go to the laser"
                            if bot.answer("method") == fab.LASER else ""))
        sheet.choice.add("leave", "No, leave it in the piece",
                         "For a board about as big as the piece of copper. "
                         "Nothing is cut round it, so it needs no room for "
                         "that.")
        sheet.choice.set(bot.answer(self.key))
        sheet.add(sheet.choice)

    def read(self, bot, sheet):
        return sheet.choice.value(), None

    def told(self, bot):
        return ("Left in the piece, not cut out"
                if bot.answer(self.key) == "leave" else "Cut out")

    def apply(self, bot):
        page, leave = bot.win.autorun_page, bot.answer(self.key) == "leave"
        page.nocut_chk.setChecked(leave)
        if page.no_cutout() != leave:
            # The tick follows the job, not the other way round, when the
            # two have drifted apart: say it to the job as well.
            bot.win.set_no_cutout(leave)
            page._sync_nocut()


class LookFirst(Question):
    key, name = "check", "A look at the tracks"

    def asked(self, bot):
        # A laser board has no tracks from the mill to look at.
        return bot.answer("method") != fab.LASER

    def default(self, bot):
        return bot.win.autorun_page.check_chk.isChecked()

    @staticmethod
    def _left(bot):
        return bot.answer("cutout") == "leave"

    def headline(self, bot):
        return ("Stop after the tracks, so you can look at them?"
                if self._left(bot) else
                "Stop before the cut-out, so you can look at the tracks?")

    def build(self, bot, sheet):
        sheet.say("After the tracks and the holes the bit can park and "
                  "wait. If a track is not cut right through the copper, it "
                  "can then be cut again a little deeper, in exactly the "
                  "same place%s."
                  % ("" if self._left(bot) else
                     ". Once the board is cut free that is no longer "
                     "possible"))
        sheet.choice = Choice()
        sheet.choice.add(True, "Yes, stop and let me look",
                         "The run waits until you say the tracks are good. "
                         "The usual choice.")
        sheet.choice.add(False, "No, run straight through",
                         "The board is finished without a stop.")
        sheet.choice.set(bool(bot.answer(self.key)))
        sheet.add(sheet.choice)

    def read(self, bot, sheet):
        return sheet.choice.value(), None

    def told(self, bot):
        return ("The run stops for you to look at the tracks"
                if bot.answer(self.key) else "No stop")

    def apply(self, bot):
        bot.win.autorun_page.check_chk.setChecked(bool(bot.answer(self.key)))


class _RunWatch(QObject):
    """The end of the run Bot started, brought back to it - once.

    A QObject for the reason :class:`_LinkWatch` is one: the run says it is
    finished from its own thread.
    """

    def __init__(self, bot, page):
        super().__init__(page)
        self._bot, self._page = bot, page
        page.finished.connect(self.ended)

    def ended(self, ok, msg):
        try:
            self._page.finished.disconnect(self.ended)
        except (RuntimeError, TypeError):
            pass
        bot, self._bot = self._bot, None
        if bot is not None and not ok:
            # After the page has dealt with it, and off this signal's back.
            QTimer.singleShot(0, lambda: bot.run_stopped(msg))
        self.deleteLater()


class _LinkWatch(QObject):
    """The machine link's news, brought to a sheet.

    The link emits from its worker thread. A bound method of an object that
    lives on this thread is queued onto it; a plain function would be run on
    the worker and touch the widgets from there.
    """

    def __init__(self, sheet, show, failed):
        super().__init__(sheet)
        self._show, self._failed = show, failed

    def changed(self, *_):
        self._show()

    def failed(self, name, _msg):
        if name == "connect":
            self._failed()


class Machine(Question):
    key, name = "machine", "Machine"

    def default(self, bot):
        return bot.win.link.is_connected()

    def headline(self, bot):
        return "Is the machine connected?"

    def build(self, bot, sheet):
        win = bot.win
        link, bar, page = win.link, win.bar, win.autorun_page
        sheet.say("The mill has to be switched on, with its lid closed and "
                  "its USB lead in this computer.")
        sheet.status = sheet.say("", colour=theme.TEXT)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(theme.GAP_S)
        # The Run page's own Connect button is what is pressed.
        sheet.connect_btn = widgets.button("Connect", kind="primary",
                                           on=page.connect_btn.click)
        sheet.rescan_btn = widgets.button(
            "Look for it again", on=lambda: (bar.refresh_ports(), show()),
            tip="After switching the mill on or plugging the lead in.")
        h.addWidget(sheet.connect_btn)
        h.addWidget(sheet.rescan_btn)
        h.addStretch(1)
        sheet.add(row)

        def show(*_):
            on = link.is_connected()
            port = (link.firmware or {}).get("port") if on else None
            if on:
                sheet.status.setText("Connected, on %s." % port)
            elif bar.current_port():
                sheet.status.setText("Not connected yet. Found: %s."
                                     % bar.port_combo.currentText())
            else:
                sheet.status.setText(
                    "Not connected, and no machine was found. Check that it "
                    "is switched on and the lead is in, then look again.")
            sheet.connect_btn.setVisible(not on)
            sheet.rescan_btn.setVisible(not on)
            sheet.connect_btn.setEnabled(bool(bar.current_port()))
            if on:
                sheet.complain("")
            sheet.settle()

        def failed():
            show()
            sheet.complain(
                "The machine did not answer. Check that it is switched on, "
                "that the USB lead is in, and that no other program (VPanel, "
                "a serial monitor) is using it - then press Connect again.")

        sheet.watch = watch = _LinkWatch(sheet, show, failed)
        link.linked.connect(watch.changed)
        link.unlinked.connect(watch.changed)
        link.op_failed.connect(watch.failed)
        sheet.finished.connect(lambda _r: self._let_go(link, watch))
        show()

    @staticmethod
    def _let_go(link, watch):
        """The sheet is closed: nothing of it is told about the link again."""
        for sig, slot in ((link.linked, watch.changed),
                          (link.unlinked, watch.changed),
                          (link.op_failed, watch.failed)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def read(self, bot, sheet):
        if not bot.win.link.is_connected():
            return None, ("The machine is not connected yet. Press Connect, "
                          "and wait for it to answer.")
        return True, None

    def told(self, bot):
        link = bot.win.link
        if not link.is_connected():
            return "Not connected"
        return "Connected, on %s" % (link.firmware or {}).get("port")


QUESTIONS = (Board, Method, Sides, Thickness, SheetSize, InTheMachine, Where,
             CutOut, LookFirst, Machine)


# ---------------------------------------------------------------------------
# What the checks found, in the words of someone who cannot change a depth
# ---------------------------------------------------------------------------

class Finding:
    """Something in the way of the run (``fail``) or worth knowing before it
    (``warn``): what it is, what to do, and the question that changes it."""

    def __init__(self, level, title, detail, question=None):
        self.level, self.title, self.detail = level, title, detail
        self.question = question


# A pre-flight check is written for the Check it page, where the reader can
# reach every control. Here they can reach ten answers, so each failure a
# Bot job can run into is said again in terms of those: (part of the check's
# title, what to do about it, the question that changes it).
_WHAT_TO_DO = (
    ("The job runs off the copper",
     "The board does not fit on the piece of copper as it was measured. "
     "Measure the copper again; or, if the board is about as big as the "
     "piece, choose to leave it in the piece instead of cutting it out.",
     "sheet"),
    ("Off the bed",
     "Part of the board is outside what the machine can reach. Check the "
     "size of the copper. If that is right, the bracket is not where the "
     "app thinks it is: ask whoever looks after the mill.", "sheet"),
    ("Nets closer than the bit",
     "Two tracks that must stay apart are closer together than this bit is "
     "wide, so they would be joined on the finished board. A finer way of "
     "making it may fit between them - the ball mill, or the laser; "
     "otherwise the tracks have to be moved apart in KiCad.", "method"),
    ("Nets closer than the laser can separate",
     "Two tracks that must stay apart are closer together than even the "
     "laser can separate. They have to be moved apart in KiCad, and the "
     "board sent again.", None),
    ("depth far below the stock",
     "The holes or the cut-out would go much deeper than the board is "
     "thick, into the bed. Check the thickness you measured.", "thickness"),
    ("The two sides would meet in the middle",
     "The cuts from the two sides are deep enough to meet inside a board "
     "this thin. Check the thickness you measured.", "thickness"),
    ("Deepest cut is out of Z range",
     "The bit cannot reach deep enough from where the copper sits. Check "
     "the thickness you measured. If that is right, the bit has to stick "
     "out further from the collet: ask whoever looks after the mill.",
     "thickness"),
    ("Work lies in a no-go zone",
     "A clamp has been marked on the Run it page where this board is to be "
     "cut. Cancel, clear the zone there (Clear zones), and ask again.",
     None),
    # A warning every job carries until the copper has been touched: said
    # here as what it means for someone who is about to press Start.
    ("Z reach unknown",
     "Whether the bit reaches deep enough to cut right through depends on "
     "how far it sticks out of the collet, which the app cannot see. If "
     "the holes or the cut-out do not come through, ask whoever looks "
     "after the mill.", None),
)
_NOT_BOTS = ("This is not something the questions can change. Cancel, and "
             "ask whoever looks after the mill - the Check it page shows it "
             "next to the board.")


def in_plain_words(check):
    """A pre-flight check as a :class:`Finding`."""
    for part, what, question in _WHAT_TO_DO:
        if part in check.title:
            return Finding(check.level, check.title, what, question)
    detail = check.detail[:1].upper() + check.detail[1:]
    if check.level == "fail":
        detail += " " + _NOT_BOTS
    return Finding(check.level, check.title, detail)


# ---------------------------------------------------------------------------
# The run of questions
# ---------------------------------------------------------------------------

class Bot:
    """One run of the questions over one window."""

    def __init__(self, win):
        self.win = win
        self.questions = [q() for q in QUESTIONS]
        self.answers = {}
        self.before = None          # the job as it was, once it was touched
        self.opened = None          # the folder these questions opened
        self.trouble = []           # what applying the answers ran into
        self._facts = {}
        # How a sheet is put in front of the operator. The tests put their
        # own hands here; the sheets are the same ones.
        self.ask = self._exec

    # -- answers ---------------------------------------------------------
    def question(self, key):
        return next(q for q in self.questions if q.key == key)

    def answer(self, key):
        """The answer given, else what the app would have had anyway."""
        if key in self.answers:
            return self.answers[key]
        return self.question(key).default(self)

    def facts(self, folder):
        """What a board is: its name, size, holes, and whether both layers
        carry copper. ``folder`` None is the board that is open; any other
        is read without being opened, so that asking changes nothing."""
        from gerber2rml.gui2.window import copper_on_both_sides
        st = self.win.state
        if folder is None:
            board, name = st.board, st.name
        else:
            key = str(folder)
            if key not in self._facts:
                from gerber2rml.app import panel as panel_mod
                try:
                    m = panel_mod.read_board(folder, mirror=st.mirror)
                    self._facts[key] = (m.board(), m.name)
                except Exception:           # noqa: BLE001 - said on the sheet
                    self._facts[key] = (None, "")
            board, name = self._facts[key]
        if board is None:
            return None
        geom = (board.outline if board.outline is not None
                and not board.outline.is_empty else board.copper)
        if geom is None or geom.is_empty:
            return None
        x0, y0, x1, y1 = geom.bounds
        return {"name": name, "w": x1 - x0, "h": y1 - y0,
                "holes": len(board.holes),
                "both": copper_on_both_sides(board),
                # (id, where it comes from, the narrowest gap if guessed)
                "method": (None if folder is None
                           else self.win.method_of(board, folder))}

    def asked(self):
        """The questions this job is asked, in order."""
        return [q for q in self.questions if q.asked(self)]

    # -- sheets ----------------------------------------------------------
    @staticmethod
    def _exec(sheet):
        sheet.exec()
        return sheet.outcome

    def sheet_for(self, q):
        """The sheet for one question, ready to be shown."""
        asked = self.asked()
        n = asked.index(q) + 1
        sheet = Ask(self.win, q.headline(self),
                    where="Question %d of %d" % (n, len(asked)))
        sheet.question = q
        q.build(self, sheet)

        def check():
            value, why = q.read(self, sheet)
            if why:
                return why
            self._store(q, value)
            return None

        def keep():
            value, why = q.read(self, sheet)
            if not why:
                self._store(q, value)

        sheet.buttons(back=n > 1, check=check, keep=keep)
        return sheet

    def _store(self, q, value):
        if q.key == "board" and value != self.answers.get("board"):
            # Another board: how it is made, and one side or two, are asked
            # of that one afresh.
            self.answers.pop("method", None)
            self.answers.pop("sides", None)
        self.answers[q.key] = value

    def findings_sheet(self, findings):
        """What is in the way, and the way to each answer that clears it."""
        fails = [f for f in findings if f.level == "fail"]
        sheet = Ask(self.win,
                    "One thing has to be put right first" if len(fails) == 1
                    else "%d things have to be put right first" % len(fails),
                    level="fail")
        sheet.findings = fails
        sheet.say("The app checked the board against your answers, and it "
                  "cannot be milled like this. Nothing has been cut.")
        sheet.fix_btns = {}
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, theme.GAP_S, 0)
        v.setSpacing(theme.GAP_S)
        for f in fails:
            card = widgets.Card(quiet=True, pad=theme.GAP_M, gap=5)
            t = QLabel(f.title)
            t.setFont(theme.font("sub"))
            t.setWordWrap(True)
            t.setStyleSheet(f"color: {theme.DANGER};")
            card.box.addWidget(t)
            d = QLabel(f.detail)
            d.setFont(theme.font("small"))
            d.setWordWrap(True)
            d.setStyleSheet(f"color: {theme.TEXT_2};")
            card.box.addWidget(d)
            if f.question and self.question(f.question).asked(self) \
                    and f.question not in sheet.fix_btns:
                b = widgets.button(
                    "Change: %s" % self.question(f.question).name.lower(),
                    on=lambda _c=False, k=f.question: sheet.go_to(k))
                sheet.fix_btns[f.question] = b
                row = QHBoxLayout()
                row.addWidget(b)
                row.addStretch(1)
                card.box.addLayout(row)
            v.addWidget(card)
        v.addStretch(1)
        sheet.add(scrolling(inner, self.win), grow=True)
        sheet.buttons(forward="")
        return sheet

    def summary_sheet(self, findings):
        """Every answer, the job on the bed behind it, and Start."""
        win, page = self.win, self.win.autorun_page
        laser = self.answer("method") == fab.LASER
        sheet = Ask(win, "Ready to start?")
        sheet.say("This is what you answered. Start finds the copper and "
                  "drills and cuts out the board; its copper is the laser's."
                  if laser else
                  "This is what you answered. Start finds the copper, "
                  "measures its surface and cuts the board.")
        # Everything between the first line and the last scrolls, so that
        # Start is on the screen however small the screen is.
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, theme.GAP_S, 0)
        v.setSpacing(theme.GAP_S)
        grid = QWidget()
        g = QGridLayout(grid)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(theme.GAP_M)
        g.setVerticalSpacing(theme.GAP_XS)
        g.setColumnStretch(1, 1)
        sheet.rows, sheet.change_btns = {}, {}
        for r, q in enumerate(self.asked()):
            name = widgets.hint(q.name, wrap=False)
            told = QLabel(q.told(self))
            told.setFont(theme.font("body"))
            told.setWordWrap(True)
            told.setStyleSheet(f"color: {theme.TEXT};")
            change = widgets.button(
                "Change", on=lambda _c=False, k=q.key: sheet.go_to(k))
            g.addWidget(name, r, 0, Qt.AlignVCenter)
            g.addWidget(told, r, 1, Qt.AlignVCenter)
            g.addWidget(change, r, 2, Qt.AlignVCenter)
            sheet.rows[q.key] = told
            sheet.change_btns[q.key] = change
        v.addWidget(grid)
        # What follows from the answers without having been one of them.
        sheet.facts = facts = []
        plan, st = win.plan, win.state
        if plan is not None and plan.single_tool:
            facts.append("The bit in the collet has to be the %s."
                         % plan.tool_label)
        if not laser and getattr(st.trace, "tool_type", "flat") != "vbit":
            facts.append("The tracks are cut %.2f mm deep (Cut depth, on "
                         "Your board)." % st.trace.cut_depth)
        if page.level_chk.isChecked():
            facts.append("The run touches the copper in a grid of points "
                         "first, so that every cut follows the real "
                         "surface.")
        if self.question("sides").asked(self) \
                and self.answer("sides") == "both":
            facts.append("It stops and asks you to turn the board over.")
        if self.question("check").asked(self) and self.answer("check"):
            facts.append("It stops for you to look at the tracks.")
        if laser:
            facts.append("When the mill is done the whole piece goes to the "
                         "laser, which etches the copper lined up on the "
                         "holes: View \u25b8 The xTool laser.")
        for line in facts:
            v.addWidget(widgets.hint("\u00b7 " + line))
        sheet.warnings = [f for f in findings if f.level == "warn"]
        if sheet.warnings:
            sec = widgets.Section("Worth knowing")
            for f in sheet.warnings:
                sec.add(widgets.hint("\u00b7 %s: %s" % (f.title, f.detail)))
            v.addWidget(sec)
        v.addStretch(1)
        sheet.add(scrolling(inner, win), grow=True)
        sheet.say("Close the lid before you start. STOP, on the bar along "
                  "the bottom of the window, stops the machine at any "
                  "moment and lifts the bit; so does the Esc key.")
        # Start moves a machine: a stray Enter must not be able to press it.
        sheet.buttons(forward="Start", safe=True)
        return sheet

    # -- applying --------------------------------------------------------
    def apply(self):
        """The answers into the app, through its own controls. The first
        time, what was there is kept for Cancel."""
        win = self.win
        if self.before is None:
            self.before = win.job_snapshot()
        self.trouble = []
        for q in self.asked():
            q.apply(self)
            if self.trouble:
                return
        # The copper drawn and the board put on it with every answer in:
        # the same call a board opened by hand ends with.
        win.autorun_page.board_opened()

    def findings(self):
        """What stands in the way of the run, and what is worth knowing:
        the pre-flight checks, and whether the copper is big enough for the
        board to sit in the bracket's corner."""
        win, page = self.win, self.win.autorun_page
        if self.trouble:
            return list(self.trouble)
        out, small = [], False
        if not page.centre_chk.isChecked():
            need = page.sheet_needed()
            sw, sh = win.stock[2:4]
            if need and (need[0] > sw + 1e-6 or need[1] > sh + 1e-6):
                small = True
                more = ("" if page.no_cutout() else
                        " Or, if the board is about as big as the piece, "
                        "choose to leave it in the piece: it then needs no "
                        "room round it.")
                out.append(Finding(
                    "fail", "The piece of copper is too small for this board",
                    "The board needs a piece at least %.1f × %.1f mm, and "
                    "you measured %.1f × %.1f mm. The extra is the room the "
                    "cut-out and the bracket's tongue take. Measure the "
                    "copper again, or take a bigger piece.%s"
                    % (need[0], need[1], sw, sh, more), "sheet"))
        if win.plan is not None and len(win.plan.tools) > 1:
            # Start refuses this itself: the run never stops for a bit
            # change. Said here, where the answer can still be changed.
            out.append(Finding(
                "fail", "The board would need more than one bit",
                "The tool profile for this way of making the board uses %d "
                "different bits, and the run cuts everything with the one "
                "in the collet. Choose another way of making it - or ask "
                "whoever looks after the mill for a one-bit profile."
                % len(win.plan.tools), "method"))
        win.refresh_checks()
        for c in win.checks():
            if c.level == "ok":
                continue
            if small and "off the copper" in c.title:
                continue                    # the same thing, said above
            out.append(in_plain_words(c))
        return out

    # -- the run of questions ---------------------------------------------
    def run(self):
        """Ask, apply, check, show, start. True if the run was started."""
        win = self.win
        if win.autorun_page._thread is not None:
            win.say("warn", "A run is in progress. The questions can be "
                            "asked again when it is done.")
            return False
        n = len(self.questions)
        i, step, returning = 0, +1, False
        while True:
            if i < n:
                q = self.questions[i]
                if not q.asked(self):
                    i += step
                    continue
                out = self.ask(self.sheet_for(q))
                if out == CANCEL:
                    return self._cancel()
                if out == BACK:
                    i, step, returning = i - 1, -1, False
                    continue
                # Next. An answer changed from the summary goes straight
                # back to it, through the checks.
                i, step, returning = (n if returning else i + 1), +1, False
                continue
            self.apply()
            findings = self.findings()
            blocked = any(f.level == "fail" for f in findings)
            sheet = (self.findings_sheet(findings) if blocked
                     else self.summary_sheet(findings))
            out = self.ask(sheet)
            if out == CANCEL:
                return self._cancel()
            if out == BACK:
                i, step = n - 1, -1
            elif out == JUMP:
                i = self.questions.index(self.question(sheet.jump))
                step, returning = +1, True
            else:
                return self._start()

    def _cancel(self):
        """Leave the app as it was found."""
        if self.before is not None:
            self.win.job_restore(self.before)
            self.before = None
        return False

    def _start(self):
        """Novice's own Start, on Novice's own page."""
        win, page = self.win, self.win.autorun_page
        self.before = None                  # these are the job's settings now
        win.select_step("autorun")
        page.start()
        started = page._thread is not None
        if started:
            self.watch = _RunWatch(self, page)
        return started

    def stopped_sheet(self, msg):
        """A run that ended without finishing the board, said where it will
        be read. The machine has stopped by now, so a sheet covers nothing."""
        sheet = Ask(self.win, "The run stopped", level="warn")
        sheet.stopped = msg
        said = (msg or "It did not say why.").strip()
        sheet.say(said[:1].upper() + said[1:], colour=theme.TEXT)
        sheet.say("The machine has stopped and the bit is up. If it stopped "
                  "before it began to cut - the board does not fit where it "
                  "was put, say - change that answer and start again.")
        sheet.say("If it stopped part-way through a board, do not move the "
                  "copper, and ask whoever looks after the mill: a run can "
                  "be picked up from the step it stopped at.", small=True)
        sheet.problem = sheet.say("", colour=theme.CAUTION)
        sheet.problem.setVisible(False)
        sheet.cancel_btn = sheet.act("Close", on=sheet.reject)
        sheet.back_btn = None
        sheet.next_btn = sheet.act("Ask me again", kind="primary",
                                   on=sheet._next)
        for b in (sheet.cancel_btn, sheet.next_btn):
            b.setAutoDefault(False)
        return sheet

    def run_stopped(self, msg):
        """The run Bot started has stopped short. Say so, and offer the
        questions again - with the answers still in them, because they are
        in the controls now."""
        if self.ask(self.stopped_sheet(msg)) == NEXT:
            self.win.action_bot()


def run(win):
    """Ask for every setting in turn, then run. True if the run started."""
    return Bot(win).run()
