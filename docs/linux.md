# SRM-CAM on Linux, with the machine link

The Linux build does what the Windows and macOS builds do: the Arduino link
(position readout, jog, Probe Z, bed levelling, the automatic run) and sending
jobs straight to the SRM-20. It is the same code as on a Mac - pyserial for
the Arduino, a raw CUPS queue for the mill.

**Not yet run at a mill from Linux.** The Mac and Windows paths cut real
boards; the Linux one is the Mac's code with Linux's password dialog and has
only been through the tests. Keep a hand at STOP the first time, and say what
happens.

## Install

Download `SRM-CAM-x86_64.AppImage` from the latest release, then:

```bash
chmod +x SRM-CAM-x86_64.AppImage
```

It carries its own Qt and libusb and needs no root to run.

## The Arduino (the machine link)

The serial port belongs to a group - `dialout` on Ubuntu and Fedora, `uucp` on
Arch. If Connect is refused, the app names the group the port actually has;
join it and log in again:

```bash
sudo usermod -aG dialout "$USER"
```

## The mill (a print queue named SRM-20)

There is no Roland driver on Linux. The mill is a raw CUPS queue, made once
per computer with the mill switched on and its USB plugged in:

* in the app: **Machine ▸ Set up the mill on this computer…**
* or in a terminal: `python -m gerber2rml.doctor --setup-mill`

An account in the `lpadmin` group makes the queue without a password; any
other is asked for an administrator's. Check it with:

```bash
lpstat -p SRM-20
```

By hand, if both fail (`lpinfo` is in `/usr/sbin` on Debian):

```bash
sudo lpinfo --include-schemes usb -v        # find the usb://Roland... line
sudo lpadmin -p SRM-20 -E -v 'usb://Roland%20DG/SRM-20?serial=...' -m raw
sudo cupsenable SRM-20 && sudo cupsaccept SRM-20
```

## The mill's settings (command set, spindle speed)

**Machine ▸ The mill's settings…** talks to the mill over USB directly, which
needs permission to the device. Put this line in
`/etc/udev/rules.d/60-srm20.rules`:

```
SUBSYSTEM=="usb", ATTR{idVendor}=="0b75", ATTR{idProduct}=="039f", MODE="0666"
```

then run `sudo udevadm control --reload` and plug the mill in again.
