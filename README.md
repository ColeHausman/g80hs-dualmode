# g80hs-dualmode

Switch a Samsung Odyssey G80HS between **6K** and **Dual Mode** from the keyboard,
over DDC/CI. No OSD joystick, no Samsung Display Manager, no factory reset.

One file, Python 3.8+ standard library only.

| Platform | Status |
|---|---|
| Windows 10/11 | Tested (RTX 4090, HDMI, firmware 1000.2) |
| macOS, Apple Silicon | Tested (macOS 26.7.2, M5 Pro, DisplayPort): reads, writes, `dual` and `6k` work. See [macOS notes](#macos-notes) |
| macOS, Intel | Not supported |
| Linux | Not included; the commands below work with `ddcutil` |

> **Use at your own risk.** This writes undocumented vendor commands to your monitor.
> It was worked out on one unit. Read [If it gets stuck](#if-it-gets-stuck) first.

## Usage

```
python g80hs.py status            show the current mode
python g80hs.py toggle            6K <-> Dual Mode
python g80hs.py dual              6K -> Dual Mode, 24" window
python g80hs.py dual full         6K -> Dual Mode, full screen
python g80hs.py 6k                Dual Mode -> 6K
python g80hs.py input dp          switch input: dp | hdmi1 | hdmi2
python g80hs.py get F0 10         read VCP codes (hex)
python g80hs.py set 10 30         write a VCP code (hex code, decimal or 0x value)
python g80hs.py snapshot          show the picture settings it can save and restore
```

A switch takes about 6 seconds. The monitor disconnects and reconnects as a different
display each time (that is how its firmware changes mode), so other monitors may blink.
Going dual -> 6K blinks once more than the OSD does, because of the 60 Hz step below.

**Close Samsung Display Manager first.** It polls the monitor every 2 seconds, and its
own 6K button is the thing that stalls.

### Bind it to a key

- **Windows (AutoHotkey v2):** `F15::Run('pythonw "C:\path\to\g80hs.py" toggle',, "Hide")`
- **macOS:** a Shortcuts "Run Shell Script" action with `python3 /path/to/g80hs.py toggle`,
  or Hammerspoon `hs.hotkey.bind({}, "f15", function() hs.execute("python3 /path/to/g80hs.py toggle") end)`

## How it works

The monitor has a vendor VCP code, `F0`, that takes a 16-bit command and reads back the
current state.

| Write to F0 | Effect | F0 reads back |
|---|---|---|
| `0x202` | Dual Mode, 24" window | 8 |
| `0x201` | Dual Mode, full screen | 4 |
| `0x200` | Back to 6K (**only safe at 60 Hz**, see below) | 0 |
| anything else | ignored | |

While a switch is in progress F0 reads 12 and the picture controls read 253 ("busy").

**6K -> dual** is one write: `F0 = 0x202`.

**dual -> 6K** is where Samsung's own software fails. `F0 = 0x200` sent at 330 Hz is
accepted, the monitor enters the "switching" state, and then it never swaps its EDID or
drops the link. It sits at F0 = 12 showing "Not Optimum" until it is reset. The same write
sent while the computer is outputting **60 Hz** completes normally. So the script:

1. sets the monitor to its current resolution at 60 Hz (a temporary mode, not saved),
2. waits until F0 has settled back to 4/8 (it reads 12 for almost a second after any
   refresh-rate change),
3. writes `F0 = 0x200` exactly once,
4. waits for the monitor to come back as the 6K display with F0 = 0.

If it stalls anyway, the script sends `VCP 04 = 1` (factory reset; 6K is the factory
default), waits, and writes back the picture settings it saved beforehand.

Only 60 Hz was tested. Where the limit sits between 60 and 330 Hz is unknown.

## Other commands we found

All values are for firmware 1000.2. "Standard" means it is a normal MCCS code.

| VCP | Meaning | Notes |
|---|---|---|
| `F0` | Dual Mode command / state | See above |
| `F2` | Mode status | low byte 1 or 0x11 in 6K, 253 in Dual Mode. Read-only |
| `60` | Input source (standard) | `0x0F` DisplayPort, `0x11` HDMI 1, `0x12` HDMI 2. Works from the computer on the inactive input too. Reads 0 right after a factory reset |
| `04` | Factory reset (standard) | Write 1. Lands in 6K. Wipes picture settings |
| `10` / `12` | Brightness / contrast (standard) | 0-50 on this monitor |
| `14` | Colour preset (standard) | |
| `16` `18` `1A` | Red / green / blue gain (standard) | |
| `87` | Sharpness (standard) | 0-20 |
| `62` / `8D` | Volume / mute (standard) | |
| `0A` | A picture-mode setting | Writing 1 locked brightness (it read 253) until set back to 0 |
| `EB` | SDM session register | SDM writes A1/B1/C2 to read the serial number via E0-E4. Not needed for switching |
| `E7` | Listed, write-only | 23 values tried, no visible effect |
| `05`, `08` | Reset luminance / colour (standard) | No effect on Dual Mode |

The capabilities string lists `cmds(01 02 03 07 0C E3 F3)`, which are all standard DDC/CI
commands. There is no hidden vendor command, and Samsung Display Manager 1.3.11 sends only
`F0 = 0x200` for its 6K button (verified by hooking its DDC calls).

The hardware ID changes with the mode: `SAM7C5F` in 6K, `SAM7C5E` in Dual Mode. Always find
the monitor by that ID rather than "the primary display".

## If it gets stuck

Symptoms: "Not Optimum" or a black screen, `status` shows `switching` (F0 = 12) for more
than a few seconds.

1. Run `python g80hs.py set 04 1` (factory reset). This has always recovered it. If the
   monitor comes back on another input, run `python g80hs.py input hdmi1` (or yours).
2. If the picture is still wrong, restart the computer.
3. Do **not** keep sending `F0 = 0x200` and do not use SDM's 6K button to "retry". Repeated
   attempts in a stall once made it survive a power-cycle (flashing bars even with the
   cable unplugged), and only step 1 plus step 2 fixed that.

Rules the script follows, and that you should keep if you port it:

- `F0 = 0x200` is written once per switch, never in a loop, and only at 60 Hz.
- It reads F0 first and refuses to act unless the monitor is in a clean state.
- `set F0 0x200` by hand is refused.

## macOS notes

Tested on macOS 26.7.2, Apple M5 Pro, monitor connected over DisplayPort (input `dp`).
`status`, `get`, `snapshot`, `set` (brightness round trip), `dual` and `6k` all worked.
`toggle` was not run separately; it uses the same two paths.

It uses the private `IOAVService` I2C calls (the same approach as m1ddc and MonitorControl)
and CoreGraphics for the 60 Hz step. The Mac needs a USB-C/DisplayPort connection, and the
monitor must be on the Mac's input for the Mac to see it.

- **EDID IDs differ over DisplayPort.** macOS sees the 6K display as model `0x7C5C` and Dual
  Mode as `0x7C5A` (Windows over HDMI: `0x7C5F` / `0x7C5E`). The script accepts all of them.
- **If the monitor is the Mac's only display, macOS signs you out on every switch.** The
  monitor disconnects and reconnects as a different display, which tears down the session.
  The switch itself completes. With a second display attached this should not happen, but
  that was not tested. Run `dual` and `6k` from a context that survives it (for example
  `nohup python3 g80hs.py 6k &`), because the script's own "finished?" checks can be cut off
  by the sign-out.
- In Dual Mode macOS offers 60, 120, 240 and 330 Hz, up to 3072x1728.
- The 60 Hz step before `6k` worked: the monitor reached 6K with F0 = 0 on the first write.

## License

MIT. See [LICENSE](LICENSE).
