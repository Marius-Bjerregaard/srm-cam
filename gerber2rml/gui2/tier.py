"""Two tiers over one code path, Novice and Pro - and Bot, which asks.

Most people who sit down at this mill have never used CAM and will use it
twice; a few run double-sided boards over a probed height map and need
everything. Since Automatic mode cut real boards (2026-11-02), the line falls
between **running the job** and **running each step of it**:

``ESSENTIAL`` (shown as Novice) is built around Automatic mode. Load the board,
say whether it is double-sided, put the stock in the bracket, press Start. It
levels, cuts, asks for the flip and stops before the cut-out by itself. The rail
has three rows and there are no files to export.

``FULL`` (shown as Pro) has everything, with Automatic mode as one way among
several.

Three properties are kept on purpose:

* **Hidden, not disabled.** A greyed-out control still invites a click and
  still has to be explained. A hidden one costs a beginner nothing.
* **One plan.** Novice shows three rows of the plan Pro shows in full; the run
  needs all of it. Same ``ProjectState``, same ``build_jobs`` call, and
  ``tests/test_gui2_tier.py`` asserts the exported bytes are identical.
* **STOP is never in a mode.** The machine bar is a structural part of the
  window, in both.

``ESSENTIAL`` is the default on a fresh install, because the person who has
never opened the app is exactly who a default is for.

``BOT`` is not a third tier. It is Novice with someone asking the questions:
the same three rows, the same controls, the same Start, and a run of pop-ups
(``gui2/bot.py``) that fills those controls in from the answers. So it is a
*profile* that can be chosen and pinned like the other two, while everything
that decides what is on screen or what is cut still asks :func:`is_full` and
gets Novice's answer. That is what keeps the byte-for-byte promise above true
for it without a test of its own.

``SRM_CAM_MODE`` pins the profile machine-wide - the same variable the first
interface reads, and the same spellings, so a lab that has already pinned its
seats does not have to do it twice.
"""
import os

from PySide6.QtCore import QSettings

ESSENTIAL = "essential"
FULL = "full"
BOT = "bot"

_VALID = (ESSENTIAL, FULL, BOT)
_KEY = "ui/tier"

# The environment variable is shared with the first interface, so its
# vocabulary has to be accepted as well as ours.
_ALIASES = {"novice": ESSENTIAL, "essential": ESSENTIAL,
            "pro": FULL, "professional": FULL, "full": FULL,
            "bot": BOT, "guided": BOT}


def _settings():
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope,
                     "SRM-CAM", "SRM-CAM-2")


def _tier_of(profile):
    """The tier a profile runs on. Bot is Novice, asked."""
    return ESSENTIAL if profile == BOT else profile


def pinned_profile():
    """The profile fixed by ``SRM_CAM_MODE``, or None if the user may choose."""
    return _ALIASES.get(os.environ.get("SRM_CAM_MODE", "").strip().lower())


def pinned_tier():
    """The tier fixed by ``SRM_CAM_MODE``, or None if the user may choose."""
    pin = pinned_profile()
    return _tier_of(pin) if pin else None


def current_profile():
    """What the Interface menu has ticked: the environment pin, else the
    stored choice, else ESSENTIAL. One of ESSENTIAL, FULL and BOT."""
    pin = pinned_profile()
    if pin:
        return pin
    stored = str(_settings().value(_KEY, ESSENTIAL) or ESSENTIAL).lower()
    return stored if stored in _VALID else ESSENTIAL


def current_tier():
    """Active tier, ESSENTIAL or FULL. Never BOT: see the note at the top."""
    return _tier_of(current_profile())


def set_tier(tier):
    """Store the chosen profile. Ignored while ``SRM_CAM_MODE`` pins it."""
    if tier not in _VALID:
        raise ValueError(f"unknown tier {tier!r}; expected one of {_VALID}")
    if pinned_profile():
        return False
    _settings().setValue(_KEY, tier)
    return True


def is_full():
    return current_tier() == FULL


def is_bot():
    """Is the app asking the questions itself? Novice's controls either way."""
    return current_profile() == BOT


# What FULL adds, in the words a person would use rather than the words the
# code uses. The Interface menu shows this verbatim, so that nobody has to
# guess whether the thing they remember is gone or merely put away.
ADDED_BY_FULL = [
    "Running the job by hand: every step of the plan, one at a time, and "
    "exporting the files for VPanel. Novice runs them itself.",
    "Per-operation cutting parameters - feeds, depths, offsets, stepover, "
    "V-bit geometry. In Novice the tool profile sets these, apart from "
    "the trace cut depth and the sheet size, which are on Your board.",
    "How a double-sided board is registered: dowels, and choosing the "
    "fiducials. Novice flips and finds its own fiducials.",
    "Stock that is not in the bracket, found by its edges or clamped by hand, "
    "and hold-down screws.",
    "Rework of a finished board, levelling on its own page, and drilling a "
    "board made elsewhere.",
    "Output format and mirroring. The defaults are the ones this lab runs.",
    "Streaming a job over the link instead of through VPanel "
    "(experimental - see the warning it carries).",
    "The machine test panel, the simulated mill, and saving your own tool "
    "profiles.",
]

# What NOVICE does for you that a beginner still gets, and why. Shown beside
# the list above so the split reads as a decision rather than a missing feature.
KEPT_IN_ESSENTIAL = [
    ("Bed levelling", "Part of every run. Probing makes the cut depth follow "
                      "the real surface, and that is what decides whether "
                      "isolation actually separates two tracks."),
    ("Double-sided boards", "One tick. The run drills its own fiducials, asks "
                            "you to turn the board over, finds them again and "
                            "cuts the top to match."),
    ("A look before the cut-out", "The run stops before it cuts the board "
                                  "free. Box what came out shallow and it cuts "
                                  "just those spots again, deeper."),
    ("Pre-flight checks", "A beginner needs the checks more than an expert "
                          "does, not less."),
    ("Moving the machine by hand", "The jog pad, the arrow keys and Click to "
                                   "jog, with STOP always beside them. STOP "
                                   "is part of the window, not of a mode."),
]
