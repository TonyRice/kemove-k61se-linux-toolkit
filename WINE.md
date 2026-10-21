# Wine setup for the KEMOVE app

The vendor app is a 32-bit MFC program (`app/DeviceDriver.exe`, PE32 i386). This
machine runs **Wine 10.0** on Ubuntu 26.04 with a single prefix at `~/.wine`
(`#arch=win64`, new WoW64: `/usr/lib/x86_64-linux-gnu/wine/i386-windows/` exists,
so 32-bit PEs run without a separate `wine32` install).

Everything here is host-side setup. It never writes to the keyboard.

## 1. HiDPI scaling (the UI is tiny)

The panel is **3072×1920 at 300×190 mm** with GNOME fractional scaling and
`text-scaling-factor 1.19`, so the desktop is effectively **2.375×**
(`Xft.dpi: 228`; `scale-monitor-framebuffer` and `xwayland-native-scaling` are
on). XWayland apps get native pixels and must scale themselves; Wine defaults to
96 DPI, hence a 410×446-pixel winecfg.

`DeviceDriver.exe` imports `SetProcessDPIAware`, so it scales from whatever DPI
GDI reports. Tell Wine the truth:

```bash
wine reg add 'HKCU\Control Panel\Desktop' /v LogPixels /t REG_DWORD /d 228 /f
```

Measured effect on winecfg: **410×446 → 946×1001** (≈2.3×). Note that 228 is
`96 × 2 × 1.1875`, i.e. exactly `Xft.dpi`, so Wine matches the rest of the
desktop instead of picking a round 200%/250%.

- Takes effect for **newly started** programs (`wineserver -k` if a stale one
  keeps the old value).
- If 228 makes the app overflow the screen, try `192` (200%) or `216` (225%) —
  same command, different `/d` value.
- Revert with
  `wine reg delete 'HKCU\Control Panel\Desktop' /v LogPixels /f`.
- Font smoothing is already on (`FontSmoothing=2`, grayscale); no change needed.

## 2. Audio (music mode)

Wine's driver is **`winepulse.drv`** and it talks to `pipewire-pulse` through
`$XDG_RUNTIME_DIR/pulse/native`. Verified with

```bash
WINEDEBUG=+pulse winecfg > /tmp/winecfg.log 2>&1
grep pulse_add_device /tmp/winecfg.log
```

which enumerates the generic `PulseAudio Output`/`PulseAudio Input` plus every
real sink and source, including:

- `Tiger Lake-LP Smart Sound Technology Audio Controller Speaker`
- `Monitor of sof-hda-dsp Speaker`
- `Music probe (speaker monitor)` (the loopback from `k61se_music_audio.sh`)
- the USB condenser mic and the onboard mics

So Wine can see the audio; the app only has to be pointed at it. Two cases:

- **The app has a device picker** — choose `Music probe (speaker monitor)` (or
  `Monitor of sof-hda-dsp Speaker`) and play music on the speakers.
- **The app only uses the default input** (it may show just `PulseAudio Input`
  or `Default`) — before launching it:

  ```bash
  ./k61se_music_audio.sh up
  ./k61se_music_audio.sh default-on
  ```

  `default-on` remembers the previous source; `default-off` restores it.

`./k61se_music_audio.sh test` proves the path end-to-end without Wine (it plays
a tone and reports the level captured from `music_probe`; −2.5 dB is correct,
anything near −60 is silence). See `PROTOCOL.md` §10b for why
`stream.capture.sink=true` is required — without it the loopback silently
records the microphone instead of the speakers.

## 3. Running the app and the two-writer rule

```bash
cd /home/tony/src/k61se_linux
wine app/DeviceDriver.exe
```

Only one process may hold the lighting interface. The app and
`k61se_gui.py`/`dump_lighting.py` must not write at the same time, and a
`winedevice.exe` left behind by a crash can still hold the node:

```bash
pgrep -a -f 'DeviceDriver|winedevice'   # should print nothing before --apply
```

Close the app (normally, so it flushes) before driving the keyboard from
Python, and close Python tools before reopening the app.

## 4. Diagnostic recipes

```bash
wine --version
wine reg query 'HKCU\Software\Wine\Drivers'          # winepulse.drv
wine reg query 'HKCU\Control Panel\Desktop' /v LogPixels
WINEDEBUG=+pulse winecfg                             # device enumeration
WINEDEBUG=+pulse wine app/DeviceDriver.exe           # what the app opens
wineserver -k                                        # stop the prefix
```

`WINEDEBUG=+pulse` only logs Wine's own enumeration/stream state; it does not
make the app write anything it would not write anyway.
