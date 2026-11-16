# Handoff: find out what VPanel sends to the SRM-20, so SRM-CAM can send it too

## Goal

SRM-CAM (https://github.com/MadsRudolph/srm-cam, Python/PySide6) now runs on the
user's Mac with the machine link. One Windows dependency is left: a few machine
settings that only Roland's **VPanel** (Windows-only) can change. Work out what
VPanel sends to the Roland SRM-20 when those settings are changed, and build
the same into SRM-CAM so a Mac user never needs a Windows PC.

The user is Danish. Write all communication with the user in Danish. Keep
code, comments, docs and commits in English, in upstream's style (long
explanatory docstrings that say *why*).

## Where things are

- Repo: `/Users/marius/Desktop/Apps/Apps/SR-CAM-No-SWIFT`, branch
  `macos-machine-link`, remote `upstream` = MadsRudolph/srm-cam. No fork, no
  `origin`.
- **Nothing is committed.** The working tree holds a large uncommitted macOS
  patch (about 25 modified files and 25 new ones). Do not reset, stash, check
  out or overwrite it. Suggest to the user that it gets committed before you
  add to it; commit only when the user says so; never push or open a PR
  without asking.
- Python 3.12 venv in `.venv`. Tests:
  `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -p no:cacheprovider -W ignore -n auto --dist worksteal`
  (about 70 s). Five tests fail on macOS with or without any patch - the
  list is in `docs/macos.md`, "Known limitations". Anything else failing is
  yours.
- `docs/macos.md` describes everything the macOS patch does. Read it first.
- Build the app: `packaging/build-macos.sh` (about 20 s). It replaces
  `dist/SRM-CAM.app`, so never run it while SRM-CAM is open
  (`pgrep -f "MacOS/SRM-CAM"`).

## What SRM-CAM needs from VPanel today

1. **Command Set = NC Code.** Automatic mode sends NC code, not RML
   (`gerber2rml/engine/autorun.py`, module docstring;
   `backends/gcode.render_incremental`). A mill set to RML-1 refuses the job:
   it holds in pause with its error bit up, which `engine/runner.py` detects
   and reports ("In VPanel, Setup > Command Set must be NC Code").
2. **G54 X/Y at machine 0, 0.** The run moves to its first point through G54,
   reads the head back, and stops if it is not where it expected
   (`AutoRun._check_frame`). After that every move is incremental.
3. **Spindle speed.** Over the Arduino SPI link the spindle can only be turned
   on and off; the speed is whatever VPanel's slider was last set to
   (`docs/archive/2026-08-21-spi-command-audit.md`, the "RPM" section, where an
   earlier claim that SPI sets the speed is retracted).

Not known: whether the mill keeps 1 and 2 across a power cycle. The user's
lab mill is very likely already set up (upstream was developed on the same
DTU fixture), so this is about removing the dependency, not unblocking a job.

## What is already known about how VPanel talks

- From `gerber2rml/engine/rmlspool.py` (docstring): VPanel is a front end;
  its `SRM20ControlAPI.dll` imports the Windows spooler
  (`OpenPrinterA` / `StartDocPrinterA` / `WritePrinter`), and the Roland
  driver installs the mill as an ordinary printer, "Roland SRM-20" on
  `USB001`. A job is bytes written RAW to that printer. So settings *may*
  travel the same way - that is the hopeful case - or VPanel may use
  bidirectional reads or USB control transfers the spooler does not carry.
- On the Mac the same channel is a raw CUPS queue named `SRM-20`
  (`rmlspool.CupsPrinter`: `lp -d SRM-20 -o raw`, `lpstat -o`, `cancel -a`).
  **It has never been tried against the real mill.** Roland DG's USB vendor
  id is 0x0B75.
- The Arduino side: Roland's own SPI remote protocol, vendored in
  `hardware/SRM20SPIRemote/`, firmware in `hardware/srm20_spi_probe/`, what
  each opcode was proven to do in the audit document above, and
  `hardware/README.md` (note there: `setOrigin` writes the User coordinate
  system, and G54 was not observed to follow it).
- `scripts/autorun_lab.py` is upstream's harness for experiments at the
  machine (E1-E5); `cmd_nc` shows how a few NC lines are sent and traced.

## The plan

### 1. Get captures (the user does this; you cannot)

It needs a real x64 Windows PC with Roland's driver, VPanel and the mill.
The user has been given these instructions and will put the files in the
project folder, with notes of exactly what was clicked and the values before
and after:

- Wireshark with USBPcap, one capture per action, each a few seconds long:
  - `tomgang.pcapng` - 10 s with VPanel open and nothing touched (VPanel
    polls the mill constantly; this is the noise to subtract)
  - Command Set NC Code -> RML-1, and RML-1 -> NC Code
  - G54 origin set, with the head at a noted position
  - spindle speed set to a noted value
  - ideally each one twice
- Only the mill's packets should be in the files (USBPcap records the whole
  hub, keyboards included). If a capture holds other devices, say so to the
  user and work only on the mill's device address.

If the files are not there yet, ask the user for them. Do not guess the
protocol and do not send experimental bytes to the mill to find out.

### 2. Analyse

- `tshark` (`brew install wireshark` - ask first) or a small parser.
- Separate bulk OUT (host to mill), bulk IN and control transfers. Subtract
  the idle polling. Compare the two captures of each action: what is
  identical is the command, what differs is a counter or a value.
- For each setting, write down the exact bytes, which endpoint and transfer
  type carried them, whether the mill answered, and whether the value is
  encoded readably (RML-1 is ASCII; expect mode commands such as `^IN;`,
  `!MC`, `!RC`).
- Decide which case it is:
  - **plain bytes on the printer's bulk OUT** - SRM-CAM can send them with
    the API it already has (`SpoolPrinter.send` on Windows, `CupsPrinter.send`
    on macOS);
  - **needs reads or control transfers** - CUPS cannot do it; that takes
    `pyusb` + libusb talking to the printer-class interface directly, which
    is a new dependency. Stop and ask the user before building that.

Prefer observing the user's own device's traffic. If you think disassembling
Roland's binaries is needed, say why and ask first - do not copy, quote or
redistribute Roland's code, and keep the write-up to what goes over the wire.

### 3. Build it into SRM-CAM

- A small engine module (suggested: `gerber2rml/engine/millsetup.py`), stdlib
  only, taking a "printer" the way `engine/runner.py` does (anything with
  `send(data, name)`), so tests use a fake and Windows and macOS share it.
  Functions along the lines of `set_command_set(printer, nc=True)`,
  `set_g54_here(...)`, `set_spindle_speed(printer, rpm)` - only for what the
  captures actually explain.
- In the GUI (`gerber2rml/gui2/`): a Machine menu entry, and where the run
  already reports "Command Set must be NC Code", an offer to set it. Follow
  `gui2/macmill.py` for the pattern (a sheet that explains, then acts).
- Tests with a fake printer for every byte sequence, taken from the captures.
- `docs/vpanel-settings.md`: what each setting is, the bytes, how it was
  found (file names of the captures), what is still unknown.
- Update the "Before the first job" paragraph and the limitations in
  `docs/macos.md`, and the README section "Required before first use" only
  with the user's agreement (it is upstream's text).

## Safety (a real mill with a spinning tool, shared by a lab)

- Nothing is sent to the real mill unless the user is standing at it and
  says go. First trial of each new command: spindle off, bit clear of
  everything.
- These settings are shared: other people's jobs depend on Command Set = NC
  Code and G54 at 0, 0. Note the values before any trial and put them back.
  Ask the user to check with whoever is responsible for the mill.
- Do not change upstream's motion or safety logic, feeds, depths or
  defaults. STOP must keep working exactly as it does
  (`engine/runner.py`, `JobRunner.stop`).
- Do not make the run change a machine setting silently. Setting the
  command set or the origin is always something the operator asked for and
  sees happen.

## Done when

- Each setting is either documented with its bytes and implemented with
  tests, or documented as "not possible over this channel, because ..." with
  the evidence.
- The test suite passes apart from the five known macOS failures.
- The user has a short checklist for trying each new command at the mill.
- Nothing is committed, pushed or sent to the mill without the user's yes.

## Worth suggesting to the user

Upstream's author (Mads Rudolph) is working towards retiring VPanel himself
and may already know some of this, or want the result as a PR. Asking him
first can save the captures entirely.
