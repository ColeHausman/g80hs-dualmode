#!/usr/bin/env python3
"""g80hs.py - switch a Samsung Odyssey G80HS between 6K and Dual Mode over DDC/CI.

No OSD, no Samsung Display Manager, no dependencies (Python 3.8+ standard library).

    python g80hs.py status            show the current mode
    python g80hs.py toggle            6K <-> Dual Mode
    python g80hs.py dual [24|full]    6K -> Dual Mode (24" window, or full screen)
    python g80hs.py 6k                Dual Mode -> 6K
    python g80hs.py input dp|hdmi1|hdmi2
    python g80hs.py get F0 10 ...     read VCP codes (hex)
    python g80hs.py set 10 30         write one VCP code (hex code, decimal or 0x value)
    python g80hs.py snapshot          print the picture settings that would be restored

How the switch works (found by experiment, firmware 1000.2):
    6K   -> dual : write VCP F0 = 0x202 (24") or 0x201 (full screen).
    dual -> 6K   : set the display to 60 Hz, wait for the monitor to settle, then write
                   VCP F0 = 0x200 once.  At 330 Hz the monitor accepts the same write
                   but never finishes (F0 stays 12).  If it stalls anyway, the script
                   sends VCP 04 = 1 (factory reset, 6K is the default) and writes the
                   picture settings back.

Windows is tested.  macOS (Apple Silicon only) is UNTESTED.  See README.md.
"""
import ctypes
import sys
import time

VENDOR = 0x4C2D                       # "SAM" in EDID
MODEL_6K, MODEL_DUAL = 0x7C5F, 0x7C5E  # the monitor shows a different EDID per mode
DUAL_CMD = {"24": 0x202, "full": 0x201}
INPUTS = {"dp": 0x0F, "hdmi1": 0x11, "hdmi2": 0x12}
RESTORE_CODES = [0x0A, 0x14, 0x16, 0x18, 0x1A, 0x10, 0x12, 0x87, 0x62, 0x8D, 0x2D, 0xCC]
UNAVAILABLE = 253                     # value the G80 returns for a busy/locked control
SAFE_HZ = 60


class NotFound(RuntimeError):
    pass


# ============================================================================ Windows
if sys.platform == "win32":
    import ctypes.wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dxva2 = ctypes.WinDLL("dxva2")

    class _MONITORINFOEX(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                    ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]

    class _DISPLAY_DEVICE(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32), ("DeviceString", wt.WCHAR * 128),
                    ("StateFlags", wt.DWORD), ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]

    class _PHYSICAL_MONITOR(ctypes.Structure):
        _fields_ = [("h", wt.HANDLE), ("desc", wt.WCHAR * 128)]

    class _DEVMODE(ctypes.Structure):
        _fields_ = [("dmDeviceName", wt.WCHAR * 32), ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
                    ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD), ("dmFields", wt.DWORD),
                    ("dmPositionX", wt.LONG), ("dmPositionY", wt.LONG), ("dmDisplayOrientation", wt.DWORD),
                    ("dmDisplayFixedOutput", wt.DWORD), ("dmColor", wt.SHORT), ("dmDuplex", wt.SHORT),
                    ("dmYResolution", wt.SHORT), ("dmTTOption", wt.SHORT), ("dmCollate", wt.SHORT),
                    ("dmFormName", wt.WCHAR * 32), ("dmLogPixels", wt.WORD), ("dmBitsPerPel", wt.DWORD),
                    ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD), ("dmDisplayFlags", wt.DWORD),
                    ("dmDisplayFrequency", wt.DWORD), ("_pad", wt.DWORD * 8)]

    _ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
    user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, _ENUMPROC, wt.LPARAM]
    user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]
    user32.EnumDisplayDevicesW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.c_void_p, wt.DWORD]
    user32.EnumDisplaySettingsW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.c_void_p]
    user32.ChangeDisplaySettingsExW.argtypes = [wt.LPCWSTR, ctypes.c_void_p, wt.HWND, wt.DWORD, ctypes.c_void_p]
    dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wt.HMONITOR, ctypes.POINTER(wt.DWORD)]
    dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [wt.HMONITOR, wt.DWORD, ctypes.c_void_p]
    dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [wt.HANDLE, ctypes.c_ubyte, ctypes.c_void_p,
                                                      ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD)]
    dxva2.SetVCPFeature.argtypes = [wt.HANDLE, ctypes.c_ubyte, wt.DWORD]
    dxva2.DestroyPhysicalMonitor.argtypes = [wt.HANDLE]

    class G80:
        """The G80HS, found by hardware ID (never "the primary monitor")."""

        def __init__(self):
            self.arr = None
            found = []

            def cb(hmon, hdc, rect, lp):
                mi = _MONITORINFOEX()
                mi.cbSize = ctypes.sizeof(mi)
                user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
                dd = _DISPLAY_DEVICE()
                dd.cb = ctypes.sizeof(dd)
                if user32.EnumDisplayDevicesW(mi.szDevice, 0, ctypes.byref(dd), 0) and "SAM7C5" in dd.DeviceID:
                    found.append((hmon, dd.DeviceID.split("\\")[1], mi.szDevice))
                return True

            user32.EnumDisplayMonitors(None, None, _ENUMPROC(cb), 0)
            if not found:
                raise NotFound("G80HS not found (is it connected to this computer and on the desktop?)")
            hmon, hwid, self.device = found[0]
            self.model = MODEL_6K if hwid.endswith("7C5F") else MODEL_DUAL
            n = wt.DWORD()
            if not (dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)) and n.value):
                raise NotFound("G80HS has no DDC/CI handle")
            self.arr = (_PHYSICAL_MONITOR * n.value)()
            if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, n.value, self.arr):
                raise NotFound("GetPhysicalMonitorsFromHMONITOR failed")
            self.h = self.arr[0].h

        def close(self):
            for p in self.arr or []:
                dxva2.DestroyPhysicalMonitor(p.h)

        def get_raw(self, code):
            cur, mx = wt.DWORD(), wt.DWORD()
            if dxva2.GetVCPFeatureAndVCPFeatureReply(self.h, code, None, ctypes.byref(cur), ctypes.byref(mx)):
                return cur.value, mx.value
            return None

        def set_raw(self, code, value):
            return bool(dxva2.SetVCPFeature(self.h, code, value))

        def mode(self):
            dm = _DEVMODE()
            dm.dmSize = ctypes.sizeof(_DEVMODE)
            if not user32.EnumDisplaySettingsW(self.device, 0xFFFFFFFF, ctypes.byref(dm)):
                return None
            return dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency

        def set_60hz(self):
            """Same resolution at 60 Hz, as a temporary mode (not saved)."""
            cur = self.mode()
            if cur is None:
                return False
            if cur[2] == SAFE_HZ:
                return True
            dm, pick, i = _DEVMODE(), None, 0
            dm.dmSize = ctypes.sizeof(_DEVMODE)
            while user32.EnumDisplaySettingsW(self.device, i, ctypes.byref(dm)):
                if (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency) == (cur[0], cur[1], SAFE_HZ):
                    pick = _DEVMODE.from_buffer_copy(dm)
                i += 1
            if pick is None:
                return False
            pick.dmFields = 0x80000 | 0x100000 | 0x400000          # width | height | frequency
            return user32.ChangeDisplaySettingsExW(self.device, ctypes.byref(pick), None, 4, None) == 0

# ============================================================================== macOS
elif sys.platform == "darwin":
    # UNTESTED.  Apple Silicon only: DDC goes through the private IOAVService API, which
    # works on USB-C / DisplayPort outputs (usually not on a Mac's built-in HDMI port).
    from ctypes import c_void_p, c_uint32, c_int32, c_double, c_size_t, c_char_p, c_long, POINTER, byref

    _iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    _cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    _cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

    _iokit.IOServiceMatching.restype = c_void_p
    _iokit.IOServiceMatching.argtypes = [c_char_p]
    _iokit.IOServiceGetMatchingServices.argtypes = [c_uint32, c_void_p, POINTER(c_uint32)]
    _iokit.IOIteratorNext.restype = c_uint32
    _iokit.IOIteratorNext.argtypes = [c_uint32]
    _iokit.IOObjectRelease.argtypes = [c_uint32]
    _iokit.IOAVServiceCreateWithService.restype = c_void_p
    _iokit.IOAVServiceCreateWithService.argtypes = [c_void_p, c_uint32]
    _iokit.IOAVServiceReadI2C.argtypes = [c_void_p, c_uint32, c_uint32, c_void_p, c_uint32]
    _iokit.IOAVServiceWriteI2C.argtypes = [c_void_p, c_uint32, c_uint32, c_void_p, c_uint32]
    _cf.CFRelease.argtypes = [c_void_p]
    _cf.CFArrayGetCount.restype = c_long
    _cf.CFArrayGetCount.argtypes = [c_void_p]
    _cf.CFArrayGetValueAtIndex.restype = c_void_p
    _cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
    _cg.CGGetOnlineDisplayList.argtypes = [c_uint32, POINTER(c_uint32), POINTER(c_uint32)]
    _cg.CGDisplayVendorNumber.restype = c_uint32
    _cg.CGDisplayVendorNumber.argtypes = [c_uint32]
    _cg.CGDisplayModelNumber.restype = c_uint32
    _cg.CGDisplayModelNumber.argtypes = [c_uint32]
    _cg.CGDisplayCopyDisplayMode.restype = c_void_p
    _cg.CGDisplayCopyDisplayMode.argtypes = [c_uint32]
    _cg.CGDisplayCopyAllDisplayModes.restype = c_void_p
    _cg.CGDisplayCopyAllDisplayModes.argtypes = [c_uint32, c_void_p]
    _cg.CGDisplaySetDisplayMode.restype = c_int32
    _cg.CGDisplaySetDisplayMode.argtypes = [c_uint32, c_void_p, c_void_p]
    for _f in ("CGDisplayModeGetWidth", "CGDisplayModeGetHeight",
               "CGDisplayModeGetPixelWidth", "CGDisplayModeGetPixelHeight"):
        getattr(_cg, _f).restype = c_size_t
        getattr(_cg, _f).argtypes = [c_void_p]
    _cg.CGDisplayModeGetRefreshRate.restype = c_double
    _cg.CGDisplayModeGetRefreshRate.argtypes = [c_void_p]

    def _mode_tuple(m):
        return (_cg.CGDisplayModeGetWidth(m), _cg.CGDisplayModeGetHeight(m),
                _cg.CGDisplayModeGetPixelWidth(m), _cg.CGDisplayModeGetPixelHeight(m),
                round(_cg.CGDisplayModeGetRefreshRate(m)))

    class G80:
        def __init__(self):
            # 1. CoreGraphics display, by EDID vendor/model.
            ids, n = (c_uint32 * 16)(), c_uint32()
            _cg.CGGetOnlineDisplayList(16, ids, byref(n))
            self.display = None
            for d in ids[:n.value]:
                if _cg.CGDisplayVendorNumber(d) == VENDOR and _cg.CGDisplayModelNumber(d) in (MODEL_6K, MODEL_DUAL):
                    self.display, self.model = d, _cg.CGDisplayModelNumber(d)
            if self.display is None:
                raise NotFound("G80HS not found (is it connected to this Mac and showing its picture?)")
            # 2. Matching IOAVService: read the first EDID bytes over I2C (address 0x50).
            self.av, candidates = None, []
            it = c_uint32()
            _iokit.IOServiceGetMatchingServices(0, _iokit.IOServiceMatching(b"DCPAVServiceProxy"), byref(it))
            while True:
                svc = _iokit.IOIteratorNext(it.value)
                if not svc:
                    break
                av = _iokit.IOAVServiceCreateWithService(None, svc)
                _iokit.IOObjectRelease(svc)
                if not av:
                    continue
                buf = (ctypes.c_ubyte * 16)()
                if _iokit.IOAVServiceReadI2C(av, 0x50, 0x00, buf, 16) == 0 and bytes(buf[8:10]) == b"\x4c\x2d" \
                        and bytes(buf[10:12]) in (b"\x5e\x7c", b"\x5f\x7c"):
                    candidates.append(av)
                else:
                    _cf.CFRelease(av)
            _iokit.IOObjectRelease(it.value)
            if not candidates:
                raise NotFound("G80HS found, but no DDC channel to it (Apple Silicon + USB-C/DisplayPort needed)")
            self.av = candidates[0]
            for extra in candidates[1:]:
                _cf.CFRelease(extra)

        def close(self):
            if self.av:
                _cf.CFRelease(self.av)
                self.av = None

        def get_raw(self, code):
            req = (ctypes.c_ubyte * 4)(0x82, 0x01, code, 0x6E ^ 0x51 ^ 0x82 ^ 0x01 ^ code)
            if _iokit.IOAVServiceWriteI2C(self.av, 0x37, 0x51, req, 4) != 0:
                return None
            time.sleep(0.05)
            r = (ctypes.c_ubyte * 12)()
            if _iokit.IOAVServiceReadI2C(self.av, 0x37, 0x51, r, 12) != 0:
                return None
            if r[2] != 0x02 or r[3] != 0 or r[4] != code:
                return None
            return (r[8] << 8) | r[9], (r[6] << 8) | r[7]

        def set_raw(self, code, value):
            d = [0x84, 0x03, code, (value >> 8) & 0xFF, value & 0xFF]
            chk = 0x6E ^ 0x51
            for b in d:
                chk ^= b
            req = (ctypes.c_ubyte * 6)(*d, chk)
            return _iokit.IOAVServiceWriteI2C(self.av, 0x37, 0x51, req, 6) == 0

        def mode(self):
            m = _cg.CGDisplayCopyDisplayMode(self.display)
            if not m:
                return None
            t = _mode_tuple(m)
            _cf.CFRelease(m)
            return t[2], t[3], t[4]

        def set_60hz(self):
            """Same mode at 60 Hz for this login session only."""
            m = _cg.CGDisplayCopyDisplayMode(self.display)
            if not m:
                return False
            cur = _mode_tuple(m)
            _cf.CFRelease(m)
            if cur[4] == SAFE_HZ:
                return True
            modes = _cg.CGDisplayCopyAllDisplayModes(self.display, None)
            ok = False
            for i in range(_cf.CFArrayGetCount(modes)):
                cand = _cf.CFArrayGetValueAtIndex(modes, i)
                if _mode_tuple(cand) == cur[:4] + (SAFE_HZ,):
                    ok = _cg.CGDisplaySetDisplayMode(self.display, cand, None) == 0
                    break
            _cf.CFRelease(modes)
            return ok

else:
    sys.exit("g80hs.py supports Windows and macOS only (on Linux, use ddcutil with the codes in README.md)")


# ======================================================================= shared logic
def get(m, code, tries=3):
    for _ in range(tries):
        r = m.get_raw(code)
        if r is not None:
            return r[0]
        time.sleep(0.06)
    return None


def put(m, code, value):
    ok = m.set_raw(code, value)
    time.sleep(0.06)                    # DDC/CI needs a gap between messages
    return ok


class open_g80:
    def __enter__(self):
        self.m = G80()
        return self.m

    def __exit__(self, *exc):
        self.m.close()


def mode_of(f0):
    return {0: "6k", 4: "dual (full screen)", 8: 'dual (24")', 12: "switching"}.get(f0, f"unknown (F0={f0})")


def status():
    with open_g80() as m:
        f0 = get(m, 0xF0)
        return {"edid": "6K" if m.model == MODEL_6K else "dual", "F0": f0, "mode": mode_of(f0),
                "signal": "%dx%d@%d" % m.mode() if m.mode() else None}


def snapshot(m):
    s = {}
    for c in RESTORE_CODES:
        v = get(m, c)
        if v is not None and v != UNAVAILABLE:
            s[c] = v
    return s


def wait_for(model, f0_values, timeout):
    """Wait until the monitor is back with the given EDID and has finished switching."""
    t0 = time.monotonic()
    time.sleep(1.5)                     # let the old link drop first
    while time.monotonic() - t0 < timeout:
        try:
            with open_g80() as m:
                if m.model == model and get(m, 0xF0) in f0_values:
                    time.sleep(0.5)
                    return time.monotonic() - t0
        except NotFound:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"monitor did not finish switching within {timeout}s")


def to_dual(size="24"):
    with open_g80() as m:
        f0 = get(m, 0xF0)
        if f0 != 0:
            raise RuntimeError(f"not in 6K (F0={f0}); nothing written")
        put(m, 0xF0, DUAL_CMD[size])
    took = wait_for(MODEL_DUAL, (4, 8), 20)
    print(f"6K -> dual in {took:.1f}s")


def to_6k():
    with open_g80() as m:
        f0 = get(m, 0xF0)
        if f0 not in (4, 8):
            raise RuntimeError(f"not in Dual Mode (F0={f0}); nothing written")
        saved = snapshot(m)
        if not m.set_60hz():
            raise RuntimeError("could not switch the display to 60 Hz; nothing written "
                               "(set 60 Hz by hand in your display settings and run this again)")
        # F0 reads 12 for up to a second after any refresh-rate change.  Wait until the
        # monitor has settled on the 60 Hz signal before asking for 6K.
        t0 = time.monotonic()
        seen_60hz = None
        while True:
            mode = m.mode()
            if mode and mode[2] == SAFE_HZ and seen_60hz is None:
                seen_60hz = time.monotonic()
            f0 = get(m, 0xF0, tries=1)
            if (seen_60hz and time.monotonic() - seen_60hz >= 0.3      # give the monitor time to notice
                    and f0 in (4, 8) and get(m, 0x10, tries=1) not in (None, UNAVAILABLE)):
                break
            if time.monotonic() - t0 > 8:
                raise RuntimeError(f"monitor did not settle at 60 Hz (F0={f0}); nothing written")
            time.sleep(0.15)
        put(m, 0xF0, 0x200)             # ONCE.  Never retry this write.
    try:
        took = wait_for(MODEL_6K, (0,), 20)
        print(f"dual -> 6K in {took:.1f}s")
        return
    except RuntimeError:
        print("the switch stalled; recovering with a factory reset (VCP 04=1)")
    with open_g80() as m:
        put(m, 0x04, 1)
    took = wait_for(MODEL_6K, (0,), 25)
    with open_g80() as m:
        restored = []
        for c, v in saved.items():
            if get(m, c) != v:
                put(m, c, v)
                restored.append("%02X" % c)
    print(f"dual -> 6K in {took:.1f}s via factory reset; restored {restored or 'nothing'}")


def toggle():
    f0 = status()["F0"]
    if f0 == 0:
        to_dual()
    elif f0 in (4, 8):
        to_6k()
    else:
        raise RuntimeError(f"monitor is {mode_of(f0)}; not touching it (see README: recovery)")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "status":
        print(status())
    elif cmd == "toggle":
        toggle()
    elif cmd == "dual":
        size = argv[2] if len(argv) > 2 else "24"
        if size not in DUAL_CMD:
            sys.exit('dual takes "24" or "full"')
        to_dual(size)
    elif cmd == "6k":
        to_6k()
    elif cmd == "input" and len(argv) > 2 and argv[2] in INPUTS:
        with open_g80() as m:
            print("ok" if put(m, 0x60, INPUTS[argv[2]]) else "DDC write failed")
    elif cmd == "get" and len(argv) > 2:
        with open_g80() as m:
            for a in argv[2:]:
                r = m.get_raw(int(a, 16))
                print(a.upper(), "unreadable" if r is None else "current=%d (0x%X) max=%d" % (r[0], r[0], r[1]))
    elif cmd == "set" and len(argv) > 3:
        code, value = int(argv[2], 16), int(argv[3], 0)
        if code == 0xF0 and value == 0x200:
            sys.exit('refusing a bare F0=0x200: it stalls the monitor above 60 Hz. Use "6k".')
        with open_g80() as m:
            print("ok" if put(m, code, value) else "DDC write failed")
    elif cmd == "snapshot":
        with open_g80() as m:
            print({"%02X" % c: v for c, v in snapshot(m).items()})
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except (RuntimeError, NotFound) as e:
        sys.exit(f"error: {e}")
