"""The SRM-20's settings channel on Windows, through the system's own USB
printer driver - so the Roland driver stays installed and printing is
untouched.

On Windows the mill is a USB printer: the inbox class driver ``usbprint.sys``
owns it, and libusb cannot open it without replacing that driver, which would
take the printer queue the jobs go through with it. ``usbprint.sys`` has a
door for exactly this, though: two device I/O controls that put a
*vendor-specific* control transfer on endpoint 0, meant for the printer's own
helper software (Microsoft's ``IOCTL_USBPRINT_VENDOR_SET_COMMAND`` and
``IOCTL_USBPRINT_VENDOR_GET_COMMAND``, usbprint.h). The transfers they make
are vendor requests to the printer interface - ``bmRequestType`` 0x41 out,
0xC1 in, ``wIndex`` the interface number, 0 - which is byte for byte what
VPanel was captured sending (docs/vpanel-settings.md). Their buffers:

    SET  in:  bRequest, wValue high, wValue low, data...
    GET  in:  bRequest, wValue high, wValue low
         out: the wLength bytes asked for (wLength = the out buffer's size)

:class:`UsbPrintDevice` wraps that in pyusb's ``ctrl_transfer`` so
:mod:`gerber2rml.engine.millsetup` uses it unchanged. Only the two vendor
directions are accepted: that is all the channel needs, and all usbprint can
send.

ctypes only, and only loaded on Windows.
"""
import ctypes
import uuid

# usbprint.h: CTL_CODE(FILE_DEVICE_UNKNOWN, USBPRINT_IOCTL_INDEX + 14/15,
# METHOD_BUFFERED, FILE_ANY_ACCESS), USBPRINT_IOCTL_INDEX = 0.
IOCTL_USBPRINT_VENDOR_SET_COMMAND = 0x00220038
IOCTL_USBPRINT_VENDOR_GET_COMMAND = 0x0022003C

# GUID_DEVINTERFACE_USBPRINT: every device usbprint.sys serves.
GUID_DEVINTERFACE_USBPRINT = "{28d78fad-5a12-11d1-ae5b-0000f803a8c2}"

_VENDOR_OUT = 0x41
_VENDOR_IN = 0xC1

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
ERROR_NO_MORE_ITEMS = 259
ERROR_INSUFFICIENT_BUFFER = 122


def set_buffer(request, value, data):
    """The input buffer of a vendor SET: request, value MSB first, data."""
    return bytes([request & 0xFF, (value >> 8) & 0xFF, value & 0xFF]) \
        + bytes(data)


def get_buffer(request, value):
    """The input buffer of a vendor GET: request, value MSB first."""
    return bytes([request & 0xFF, (value >> 8) & 0xFF, value & 0xFF])


def matches(path, vid, pid):
    """Is this usbprint device path the device ``vid``:``pid``? The path
    carries ``vid_XXXX&pid_XXXX`` in the parent's hardware id."""
    return ("vid_%04x&pid_%04x" % (vid, pid)) in path.lower()


class UsbPrintDevice:
    """One usbprint device, opened, with pyusb's ``ctrl_transfer``.

    ``ioctl(code, inbuf, outlen)`` -> bytes is the only thing that touches
    the system; tests give a fake one."""

    def __init__(self, ioctl, close=None, path=""):
        self._ioctl = ioctl
        self._close = close
        self.path = path

    def ctrl_transfer(self, bmRequestType, bRequest, wValue=0, wIndex=0,
                      data_or_wLength=None, timeout=None):
        if wIndex != 0:
            raise OSError("usbprint sends vendor requests to the printer "
                          "interface only (wIndex 0), not %d" % wIndex)
        if bmRequestType == _VENDOR_IN:
            n = int(data_or_wLength or 0)
            return self._ioctl(IOCTL_USBPRINT_VENDOR_GET_COMMAND,
                               get_buffer(bRequest, wValue), n)
        if bmRequestType == _VENDOR_OUT:
            data = bytes(data_or_wLength or b"")
            self._ioctl(IOCTL_USBPRINT_VENDOR_SET_COMMAND,
                        set_buffer(bRequest, wValue, data), 0)
            return len(data)
        raise OSError("usbprint carries vendor requests to the interface "
                      "only (0x41, 0xC1), not 0x%02x" % bmRequestType)

    def close(self):
        if self._close is not None:
            self._close()
            self._close = None

    def __del__(self):
        try:
            self.close()
        except Exception:                       # noqa: BLE001
            pass


# --- the real thing -----------------------------------------------------------

def _api():
    from ctypes import wintypes as wt

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                    ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                    ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
    setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
        wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wt.BOOL

    kernel32.CreateFileW.argtypes = [
        wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD,
        wt.HANDLE]
    kernel32.CreateFileW.restype = wt.HANDLE
    kernel32.DeviceIoControl.argtypes = [
        wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
        wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    kernel32.DeviceIoControl.restype = wt.BOOL
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL

    u = uuid.UUID(GUID_DEVINTERFACE_USBPRINT)
    guid = GUID(u.fields[0], u.fields[1], u.fields[2],
                (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    return wt, setupapi, kernel32, guid, SP_DEVICE_INTERFACE_DATA


def _invalid(h):
    return h is None or h == ctypes.c_void_p(-1).value


def device_paths():
    """The paths of every usbprint device present."""
    wt, setupapi, _k, guid, SPDID = _api()
    info = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if _invalid(info):
        raise OSError("the USB printers could not be listed (error %d)"
                      % ctypes.get_last_error())
    paths = []
    try:
        i = 0
        while True:
            data = SPDID()
            data.cbSize = ctypes.sizeof(SPDID)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    info, None, ctypes.byref(guid), i, ctypes.byref(data)):
                if ctypes.get_last_error() == ERROR_NO_MORE_ITEMS:
                    break
                raise OSError("the USB printers could not be listed (error "
                              "%d)" % ctypes.get_last_error())
            i += 1
            need = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                info, ctypes.byref(data), None, 0, ctypes.byref(need), None)
            if need.value == 0:
                continue
            buf = ctypes.create_string_buffer(need.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize: a DWORD and one WCHAR,
            # padded - 8 on 64-bit Python, 6 on 32-bit.
            ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    info, ctypes.byref(data), buf, need, None, None):
                continue
            paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info)
    return paths


def open_device(vid, pid):
    """The usbprint device ``vid``:``pid`` as a :class:`UsbPrintDevice`, or
    None when no USB printer with that id is present."""
    wt, _s, kernel32, _g, _d = _api()
    path = next((p for p in device_paths() if matches(p, vid, pid)), None)
    if path is None:
        return None
    h = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                             FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, 0, None)
    if _invalid(h):
        raise OSError("the mill's USB printer port could not be opened "
                      "(error %d) - another program may hold it"
                      % ctypes.get_last_error())

    def ioctl(code, inbuf, outlen):
        inb = ctypes.create_string_buffer(bytes(inbuf), len(inbuf))
        outb = ctypes.create_string_buffer(max(outlen, 1))
        got = wt.DWORD(0)
        if not kernel32.DeviceIoControl(
                h, code, inb, len(inbuf), outb if outlen else None, outlen,
                ctypes.byref(got), None):
            raise OSError("the USB printer driver refused the request "
                          "(error %d)" % ctypes.get_last_error())
        return outb.raw[:got.value]

    return UsbPrintDevice(ioctl, close=lambda: kernel32.CloseHandle(h),
                          path=path)
