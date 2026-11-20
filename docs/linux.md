# SRM-CAM on Linux, with the machine link

The Linux build does what the Windows and macOS builds do: the Arduino link
(position readout, jog, Probe Z, bed levelling, the automatic run), sending
jobs straight to the SRM-20, and the mill's own settings. Proven on the lab
mill since 2026-11-19.

## Install

Download `SRM-CAM-x86_64.AppImage` from the latest release, then:

```bash
chmod +x SRM-CAM-x86_64.AppImage
```

## Set up USB access (once)

The kernel's own printer driver, `usblp`, makes the mill `/dev/usb/lp0` when
its USB cable is in and it is switched on - the same port the Windows spooler
calls `USB001`. No CUPS queue and no driver: jobs are written to that node
(`engine/rmlspool.py`, `UsbLpPrinter`).

The node, the Arduino's serial port and the mill's settings channel belong to
root. **Machine ▸ Set up USB access…** installs a udev rule
(`/etc/udev/rules.d/60-srm-cam.rules`) that hands them to whoever is logged in
at the screen, and keeps ModemManager off the Arduino. It asks for your
password once; nothing has to be unplugged. From a terminal the same is:

```bash
python -m gerber2rml.doctor --setup-mill
```
