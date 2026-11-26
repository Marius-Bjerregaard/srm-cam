"""Run one RML job on the mill and know when it has finished.

The job goes to the printer (:mod:`gerber2rml.engine.rmlspool`, or the
simulator's); the Arduino link watches it. Neither one alone can say "done":
the spooler empties as soon as the driver has the bytes, long before the
last move, and the machine stands still between moves of a slow plunge. So
the end is all three at once - nothing queued, the motors stopped for
``still_s``, and the head where the program ends.

A head that has not moved at all for ``settled_s``, with nothing queued, has
finished too, wherever it is: waiting on would hang the run for hours with the
mill standing idle (seen 2026-11-03, the check before the cut-out never came).
The run goes on, and says how far off the head stopped, because that means the
job did not end where it was meant to.

STOP is the reverse: purge what the spooler still holds, tell the machine to
stop moving and drop its job, spindle off, lift. Lifting needs firmware v4
(the abort lift used to cancel itself; hardware/srm20_spi_probe).
"""
import math
import time

from gerber2rml.engine import spi_probe


class RunStopped(RuntimeError):
    """The run was stopped - by the operator, or by the machine."""


class JobNotRun(RunStopped):
    """The job left the print queue and the mill never moved: it was not
    run, and nothing was cut by it.

    This used to count as finished - "the mill stopped at ..., not at the
    program's end" - and the run went on. On the lab mill, 2026-11-26, a
    second recut of the traces went that way: the board was not cut, the
    run said it had been, and the next recut would have been asked for on
    top of a depth that was never there."""


def program_end(rml_text):
    """The last commanded (x, y) of an RML program, in RML units (0.01 mm),
    or None."""
    last = None
    for cmd in rml_text.replace("\n", "").split(";"):
        cmd = cmd.strip()
        try:
            if cmd.startswith("Z"):
                x, y, _z = (int(v) for v in cmd[1:].split(","))
                last = (x, y)
            elif cmd.startswith("PU"):
                x, y = (int(v) for v in cmd[2:].split(","))
                last = (x, y)
        except ValueError:
            continue
    return last


def status_words(st):
    """What the mill says of itself, for a message: "paused, lid open
    (status 0x00020020)" - or "nothing unusual (status 0x...)"."""
    if not st:
        return "no status read"
    said = [text for key, text in (("paused", "paused"), ("cover", "lid open"),
                                   ("error", "error"), ("fatal", "fatal error"),
                                   ("cmderr", "last command refused"))
            if st.get(key)]
    return "%s (status 0x%08X)" % (", ".join(said) or "nothing unusual",
                                   st.get("system", 0) & 0xFFFFFFFF)


class JobRunner:
    """Send jobs and supervise them over the link ``ser``."""

    def __init__(self, ser, printer, should_abort, should_pause=None,
                 note=None, still_s=2.0, poll_s=0.3, origin_mm=(0.0, 0.0),
                 settled_s=20.0, stall_s=30.0):
        self.ser = ser
        self.printer = printer
        self.should_abort = should_abort
        self.should_pause = should_pause or (lambda: False)
        self.note = note or (lambda _m: None)
        self.still_s = still_s
        self.poll_s = poll_s
        self.settled_s = settled_s
        # A job the queue holds while the mill never moves: switched off,
        # unplugged, or a print queue that stopped itself. Said once.
        self.stall_s = stall_s
        self.origin = origin_mm

    def run(self, program, name, timeout_s=6 * 3600, progress=None,
            end_mm=None):
        """Run ``program`` to the end. Raises :class:`RunStopped`.

        ``end_mm`` is where the head stops, machine XY; without it the end is
        read from the program as RML."""
        if end_mm is None:
            end = program_end(program)
            end_mm = (None if end is None else
                      (self.origin[0] + end[0] / 100.0,
                       self.origin[1] + end[1] / 100.0))
        self.printer.send(program, name)
        try:
            return self._watch(name, end_mm, timeout_s, progress)
        except RunStopped:
            raise
        except BaseException:
            # Anything else - the Arduino's USB dropping mid-job (a
            # SerialException from a status read), the spooler failing to
            # answer pending() - used to leave run() with the job still
            # spooling: the run was over on the screen while the mill went on
            # cutting from its queue, and the bar's STOP had no printer to
            # purge. The job is stopped first, whatever happened.
            self.stop()
            raise

    # No status and no position for this long: the link is gone, and a job
    # nobody can see is not one that is "done".
    BLIND_S = 30.0
    # A job that ends away from its program's end without the head ever
    # having been this far from where it started was not run (JobNotRun).
    NOT_RUN_MM = 3.0
    # Paused by itself for this long before the run tells it to carry on.
    HELD_S = 3.0

    def _watch(self, name, end_mm, timeout_s, progress):
        t0 = time.monotonic()
        still_since = None
        paused = False
        last_pos, settled_since = None, time.monotonic()
        first_pos, moved = None, False      # did the mill ever start?
        held_since, resumed = None, False   # paused, and not by this run
        stalled_since, stall_told = None, False
        blind_since = None
        while True:
            if self.should_abort():
                self.stop()
                raise RunStopped("stopped")
            if self.should_pause() != paused:
                paused = not paused
                (spi_probe.suspend_job if paused else spi_probe.resume_job)(
                    self.ser)
            st = spi_probe.machine_status(self.ser) or {}
            if st.get("cover"):
                self.stop()
                raise RunStopped("the lid was opened")
            if st.get("error") or st.get("fatal"):
                # What a refused program looks like: the mill holds in pause
                # with its error bit up (RML sent to a mill set to NC Code).
                self.stop()
                raise RunStopped("the mill refused the job. In VPanel, "
                                 "Setup ▸ Command Set must be NC Code; press "
                                 "Cancel there to clear it. Without VPanel, "
                                 "Machine ▸ The mill's settings… reads and "
                                 "sets the command set")
            # Paused, and this run did not ask for it: what the mill does by
            # itself after its lid was opened, and where VPanel would show
            # Continue. A paused mill takes no data - the job sits in the
            # print queue and is never cut (a second recut after a look at
            # the board, twice in the lab, on a Mac and on Windows). With
            # the lid shut again the run presses Continue, once.
            if st.get("paused") and not paused and not st.get("cover"):
                held_since = held_since or time.monotonic()
                if (not resumed
                        and time.monotonic() - held_since >= self.HELD_S):
                    resumed = True
                    self.note("%s: the mill is paused, as after its lid was "
                              "opened - telling it to carry on." % name)
                    spi_probe.resume_job(self.ser)
            else:
                held_since = None
            pos = spi_probe.query_position(self.ser)
            if progress is not None and pos:
                progress(pos)
            # A silent Arduino gives no status and no position, and both read
            # as "finished": not moving, and no position to be away from the
            # end at. Two seconds of a dead link ended the job, and the run
            # went on to its next step with the mill still cutting.
            blind = not st or pos is None
            if blind:
                blind_since = blind_since or time.monotonic()
                if time.monotonic() - blind_since >= self.BLIND_S:
                    self.stop()
                    raise RunStopped(
                        "the Arduino stopped answering, so the job could not "
                        "be watched - it was stopped. Check its USB lead")
            else:
                blind_since = None
            moving = bool(st.get("moving"))
            if pos is not None:
                # Away from where it stood by more than NOT_RUN_MM. Not the
                # "moving" bit, and not any movement at all: that day the
                # head went from the run's view position (Y 150) to the
                # mill's own (Y 152.4) by itself while the job was lost.
                first_pos = first_pos or pos[:3]
                moved = moved or math.dist(pos[:3], first_pos) > self.NOT_RUN_MM
            queued = self.printer.pending()
            if queued and not moving and not paused:
                stalled_since = stalled_since or time.monotonic()
                if (not stall_told
                        and time.monotonic() - stalled_since >= self.stall_s):
                    stall_told = True
                    self.note(
                        "%s: the job has waited in the print queue for %d s "
                        "and the mill has not moved. Is it switched on, its "
                        "USB cable in, and the queue not paused? The mill "
                        "reports: %s."
                        % (name, self.stall_s, status_words(st)))
            else:
                stalled_since = None
            at_end = (end_mm is None or pos is None or
                      math.dist(pos[:2], end_mm) < 0.3)
            if (not blind and not paused and not moving and not queued
                    and at_end):
                still_since = still_since or time.monotonic()
                if time.monotonic() - still_since >= self.still_s:
                    return True
            else:
                still_since = None
            if (pos is None or paused or queued or last_pos is None
                    or math.dist(pos[:3], last_pos) > 0.02):
                settled_since = time.monotonic()
                last_pos = pos[:3] if pos else last_pos
            elif time.monotonic() - settled_since >= self.settled_s:
                if not moved:
                    # Nothing to stop: the queue is empty and the mill is
                    # standing where it stood. Purged all the same, in case
                    # the job is only late.
                    self.stop()
                    raise JobNotRun(
                        "the mill did not run %s: the job left the print "
                        "queue and the head never moved (it stands at X "
                        "%.2f, Y %.2f, Z %.2f; the mill reports: %s). "
                        "Nothing was cut by it"
                        % ((name,) + tuple(pos[:3]) + (status_words(st),)))
                self.note(
                    "%s: the mill stopped at X %.2f, Y %.2f, Z %.2f, not at the "
                    "program's end (X %.2f, Y %.2f). Check the job finished."
                    % ((name,) + tuple(pos[:3]) + tuple(end_mm or pos[:2])))
                return True
            if time.monotonic() - t0 > timeout_s:
                self.stop()
                raise RunStopped("the job ran past its time limit")
            time.sleep(self.poll_s)

    def stop(self):
        """Everything that stops the machine, each failure swallowed."""
        for step in (lambda: self.printer.purge(),
                     lambda: spi_probe.stop_moving_now(self.ser),
                     lambda: spi_probe.cancel_job(self.ser),
                     lambda: spi_probe.spindle_off(self.ser),
                     lambda: spi_probe.send_abort(self.ser)):
            try:
                step()
            except Exception:
                pass
