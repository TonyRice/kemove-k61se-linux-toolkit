#!/usr/bin/env python3
"""KEMOVE K61SE backlight power + effect mode over USB HID (Linux).

Reverse-engineered from captures (see captures/lighting_off_on/ and the
per-mode dumps in dumps/):

    report 6, 520-byte feature report
    frame:  06 | op | sub | 00 | chunks=01 | index=00 | size_lo | size_hi | <size>
      * op 0x84 = READ  lighting block (128 bytes)
      * op 0x04 = WRITE lighting block

Body byte 0x0A (report byte 0x12) is a MODE enum, not a boolean:

    0x00 off      0x01 solid     0x02 pulsing
    0x03 rotating 0x04 typing feedback
    ...0x05..0x11 are the app's remaining effects (see layout.MODES); 0x06 and
    0x0E were never observed.  "0x0B = preview" was a one-observation guess and
    is wrong: 0x0B is the "rainbow wave" effect.

The panel is lit only when ALL of these hold:

    mode (0x0A) != 0             <- host-controlled, via an op 0x04 block write
    the firmware's enable flag   <- toggled ONLY by the keyboard hotkey
    the firmware can draw that mode  <- unknown modes draw nothing (see below)

So "off" is really mode 0, and there are THREE offs that are not interchangeable:

  * writing mode 0x00 — what the vendor app does when you click its light icon,
    and what this tool does.  Fully reversible: `on` writes the remembered mode
    back and the panel lights.  It does overwrite the stored mode, so the effect
    is gone unless somebody remembers it -- hence the state file below.
  * the keyboard's own hotkey.  It flips the firmware enable flag and leaves this
    block completely untouched (verified: byte-identical dumps before / while /
    after a hotkey off-on cycle, all reading mode 0x04 while the panel was dark),
    so it keeps the effect.  There is NO software path out of it: a real mode
    transition (0x04 -> 0x00 -> 0x04, block verified at each step) left the panel
    dark, and the vendor app cannot relight it either -- it can still change
    colours while the panel is dark, it just cannot re-enable it.  Only the
    hotkey turns the panel back on.
  * an unused mode value.  `mode 0x06` writes and reads back fine, but the panel
    goes dark and the hotkey appears dead, because the hotkey only flips the
    enable flag and there is nothing to draw.  Unlike the hotkey case this IS
    reversible in software: write a mode the app actually uses.  Only 0x06 and
    0x0E are missing from 0x01..0x11; treat any unknown value as blank.

Because the block cannot express the hotkey's off, this tool caches the last
drawable mode it saw in a small state file next to the script
(.k61se_state.json) and `on` restores that.  Unknown modes are never cached: one
of them made `off` followed by `on` restore a dark panel.

Every write is a read-modify-write of the whole 128-byte block, so whatever
colour, brightness and per-key settings you have are preserved.

CLOSE THE VENDOR APP FIRST
  Two writers fight over this device.  The vendor app keeps re-asserting its own
  idea of the state, so while it is running your `off` gets reverted to whatever
  the app still believes (it looked like "the firmware refuses mode 0", but the
  app was simply writing 0x04 back).  Writes that happen to agree with the app
  survive, which makes the failure look bizarrely one-sided.
  This tool now warns when it can see another process holding the device.

Usage:
    sudo python3 k61se_power.py status        # current mode + remembered mode
    sudo python3 k61se_power.py off
    sudo python3 k61se_power.py on            # restores the remembered mode
    sudo python3 k61se_power.py toggle
    sudo python3 k61se_power.py mode 0x0A     # set an effect explicitly (0x00..0x11)
    sudo python3 k61se_power.py reset         # emergency USB port reset
    ... append "force" to resend even when nothing would change
"""
import fcntl
import glob
import json
import os
import sys
import time

import hid

import k61se_layout as layout

VENDOR_ID = 0x258A
PRODUCT_ID = 0x010C
USBDEVFS_RESET = ord("U") << 8 | 20

REPORT_ID = 0x06
REPORT_LEN = 520          # 8-byte frame header + 512 bytes of room
LIGHT_SIZE = layout.LIGHTING_SIZE          # size field of the lighting block
MODE_OFFSET = layout.MODE_REPORT_OFF       # 0x12 in the 136-byte response

HDR_READ_LIGHT = bytes([0x06, 0x84, 0x00, 0x00, 0x01, 0x00, 0x80, 0x00])
OP_WRITE_LIGHT = 0x04

MODE_OFF = 0x00
DEFAULT_ON_MODE = 0x01                     # fall back to "solid"

STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".k61se_state.json"
)


# --------------------------------------------------------------------------- #
# remembered mode
# --------------------------------------------------------------------------- #
def load_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        return True
    except Exception as e:
        print(f"[!] Could not write {STATE_FILE}: {e}")
        return False


def mode_renders_something(mode):
    """True for modes we have actually seen draw an effect.

    Anything outside `layout.MODES` is unknown, and unknown modes draw nothing:
    writing 0x06 leaves the panel completely dark, and the hotkey cannot bring
    it back because the firmware has nothing to re-enable. Such a mode must
    never be remembered as the mode that `on` restores.
    """
    return mode != MODE_OFF and mode in layout.MODES


def remembered_mode():
    """Last mode we know renders, defaulting to 'solid'."""
    value = load_state().get("last_mode", DEFAULT_ON_MODE)
    if not isinstance(value, int) or not mode_renders_something(value):
        return DEFAULT_ON_MODE
    return value


def remember(mode):
    """Record a mode that actually renders, so `on` can restore it later.

    Unknown modes are skipped deliberately: 0x06 darkens the panel, so storing
    it would make `off` followed by `on` restore a dark keyboard.
    """
    if not mode_renders_something(mode):
        return
    state = load_state()
    if state.get("last_mode") != mode:
        state["last_mode"] = mode
        state["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)


# --------------------------------------------------------------------------- #
# other writers
# --------------------------------------------------------------------------- #
def k61se_nodes():
    """Device nodes belonging to the K61SE (hidraw nodes + the raw USB node)."""
    nodes = set()

    for uevent in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        try:
            with open(uevent) as f:
                text = f.read()
        except OSError:
            continue
        # e.g. "HID_ID=0003:0000258A:0000010C"
        if "258A" in text.upper() and "010C" in text.upper():
            node = os.path.basename(os.path.dirname(os.path.dirname(uevent)))
            nodes.add(f"/dev/{node}")

    for dev_path in glob.glob("/sys/bus/usb/devices/*"):
        try:
            with open(os.path.join(dev_path, "idVendor")) as f:
                vid = f.read().strip().lower()
            with open(os.path.join(dev_path, "idProduct")) as f:
                pid = f.read().strip().lower()
            if vid == "258a" and pid == "010c":
                with open(os.path.join(dev_path, "busnum")) as f:
                    bus = int(f.read().strip())
                with open(os.path.join(dev_path, "devnum")) as f:
                    dev = int(f.read().strip())
                nodes.add(f"/dev/bus/usb/{bus:03d}/{dev:03d}")
        except OSError:
            continue

    return nodes


def conflicting_writers():
    """[(pid, command)] for other processes holding a K61SE node open."""
    nodes = k61se_nodes()
    if not nodes:
        return []

    me = os.getpid()
    found = {}
    for pid_text in os.listdir("/proc"):
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == me:
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if target in nodes:
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmd = f.read().replace(b"\x00", b" ").strip().decode(
                            "utf-8", "replace"
                        )
                except OSError:
                    cmd = "?"
                found[pid] = cmd[:70] or "?"
                break

    return sorted(found.items())


def warn_about_other_writers():
    others = conflicting_writers()
    if not others:
        return False
    print("[!] Another process has the K61SE open:")
    for pid, cmd in others:
        print(f"      pid {pid:<7} {cmd}")
    print(
        "    A vendor app re-asserts its own settings, so it will undo writes it"
        " disagrees with\n    (your `off` snaps back while `on` appears to work)."
        " Close it before trusting this tool."
    )
    return True


# --------------------------------------------------------------------------- #
# device I/O
# --------------------------------------------------------------------------- #
def emergency_software_reset():
    """Failsafe: reboots the USB port directly if the keyboard ever hangs."""
    print("\n[!] Triggering emergency software USB reset...")
    for dev_path in glob.glob("/sys/bus/usb/devices/*"):
        v_file = os.path.join(dev_path, "idVendor")
        p_file = os.path.join(dev_path, "idProduct")
        if os.path.isfile(v_file) and os.path.isfile(p_file):
            try:
                with open(v_file) as f:
                    vid = f.read().strip().lower()
                with open(p_file) as f:
                    pid = f.read().strip().lower()
                if vid == "258a" and pid == "010c":
                    with open(os.path.join(dev_path, "busnum")) as f:
                        bus = int(f.read().strip())
                    with open(os.path.join(dev_path, "devnum")) as f:
                        dev = int(f.read().strip())
                    node = f"/dev/bus/usb/{bus:03d}/{dev:03d}"
                    fd = os.open(node, os.O_WRONLY)
                    fcntl.ioctl(fd, USBDEVFS_RESET, 0)
                    os.close(fd)
                    print("[+] USB bus reset complete. Device operational.")
                    return
            except Exception:
                continue


def get_device():
    target_path = None
    for d in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        if d.get("interface_number") == 1:
            target_path = d["path"]
            break
    if not target_path:
        raise RuntimeError("K61SE Interface 1 not detected.")
    dev = hid.device()
    dev.open_path(target_path)
    return dev


def read_light_block(dev):
    """Read the 128-byte lighting block (op 0x84).

    Returns the full 136-byte response: 8-byte frame header then the block, so
    the mode byte sits at MODE_OFFSET.
    """
    req = HDR_READ_LIGHT + bytes(REPORT_LEN - len(HDR_READ_LIGHT))
    if dev.send_feature_report(req) < 0:
        raise OSError(f"lighting read request failed: {dev.error()}")
    time.sleep(0.03)  # give the device time to fill report 6 (capture ~29 ms)

    raw = bytes(dev.get_feature_report(REPORT_ID, REPORT_LEN))
    # hidraw returns the report id as byte 0; tolerate a build that strips it.
    if len(raw) >= 2 and raw[0] == REPORT_ID and raw[1] == 0x84:
        return bytearray(raw)
    if len(raw) >= 1 and raw[0] == 0x84:
        return bytearray(bytes([REPORT_ID]) + raw)
    raise OSError(
        f"unexpected lighting read-back ({len(raw)} bytes): {raw[:16].hex(' ')}"
    )


def write_mode_byte(dev, block, mode):
    """Write the whole block back with only the mode byte changed."""
    out = bytearray(block)
    out[0] = REPORT_ID
    out[1] = OP_WRITE_LIGHT          # 0x84 read -> 0x04 write
    out[MODE_OFFSET] = mode & 0xFF
    if len(out) < REPORT_LEN:
        out += bytes(REPORT_LEN - len(out))
    out = out[:REPORT_LEN]

    written = dev.send_feature_report(out)
    if written < 0:
        raise OSError(dev.error())
    return written


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #
def choose_target(action, current, explicit=None):
    """Resolve the mode byte for an action against the current mode."""
    if action == "off":
        return MODE_OFF
    if action == "on":
        return remembered_mode()
    if action == "toggle":
        return MODE_OFF if current != MODE_OFF else remembered_mode()
    if action == "mode":
        return explicit
    raise ValueError(action)


def apply_mode(action, explicit=None, force=False):
    if action == "off":
        print("[*] Setting backlight: OFF...")
    elif action == "on":
        print("[*] Setting backlight: ON...")
    elif action == "toggle":
        print("[*] Toggling backlight...")
    else:
        print(f"[*] Setting mode 0x{explicit:02X} ({layout.mode_name(explicit)})...")
        if explicit and explicit not in layout.MODES:
            print(
                f"[!] 0x{explicit:02X} is not one of the app's effects. It draws"
                " nothing, so the panel will go dark\n"
                "    and the hotkey will not relight it. Recover with"
                f" `mode 0x{DEFAULT_ON_MODE:02X}`."
            )

    warn_about_other_writers()

    try:
        dev = get_device()
    except Exception as e:
        print(f"[-] Could not open K61SE interface 1: {e}")
        return

    try:
        try:
            block = read_light_block(dev)
        except Exception as e:
            print(f"[-] Read failed, not writing anything: {e}")
            return

        current = block[MODE_OFFSET]
        remember(current)
        print(
            f"    current mode = 0x{current:02X} ({layout.mode_name(current)})"
        )

        target = choose_target(action, current, explicit)

        # The hotkey's off state is INVISIBLE in this block: byte-identical dumps
        # taken before / while / after a hotkey off-on cycle all read mode 0x04
        # while the panel was dark.
        #
        # It is also UNREACHABLE from here.  A real mode transition was tested
        # against a hotkey-darkened panel (0x04 -> 0x00 -> 0x04, two genuine
        # changes, block verified at each step) and the panel stayed dark, so a
        # non-zero mode is no proof the panel is lit and no write sequence we
        # have found can undo a hotkey-off.  Only the hotkey can.
        if current == target and not force:
            if target == MODE_OFF:
                print("[=] Already off; no write needed (use 'force' to resend).")
            else:
                print(
                    f"[=] Block already reads 0x{target:02X}"
                    f" ({layout.mode_name(target)}); nothing to change."
                )
                print(
                    "    if the panel is dark, the keyboard hotkey darkened it and"
                    " only the hotkey can undo that"
                )
            return

        try:
            written = write_mode_byte(dev, block, target)
        except Exception as e:
            print(f"[-] Write failed: {e}")
            emergency_software_reset()
            return

        # Verify at the block level.  send_feature_report succeeding only means
        # the HID layer took the bytes; it says nothing about whether the device
        # applied them.  So read back instead of claiming success.
        now = None
        try:
            now = read_light_block(dev)[MODE_OFFSET]
        except Exception as e:
            print(f"[!] Could not read back to verify: {e}")

        note = ""
        if action == "on" and target != DEFAULT_ON_MODE:
            note = " (restored remembered mode)"
        remember(target)   # keep the memory in step with what we just set
        print(
            f"[+] Wrote {written}-byte lighting block,"
            f" mode 0x{target:02X} ({layout.mode_name(target)}){note}."
        )
        if now is None:
            pass
        elif now == target:
            print(f"    verify: block now reads 0x{now:02X} ({layout.mode_name(now)})")
        else:
            print(
                f"[!] verify: block still reads 0x{now:02X} ({layout.mode_name(now)}),"
                f" expected 0x{target:02X} — the device did not apply the write"
            )
        print(
            "    (this is what the block says; the panel's lit/dark state cannot be"
            " read from here)"
        )
    finally:
        try:
            dev.close()
        except Exception:
            pass


def print_status():
    warn_about_other_writers()
    try:
        dev = get_device()
    except Exception as e:
        print(f"[-] Could not open K61SE interface 1: {e}")
        return
    try:
        block = read_light_block(dev)
    except Exception as e:
        print(f"[-] Read failed: {e}")
        return
    finally:
        try:
            dev.close()
        except Exception:
            pass

    mode = block[MODE_OFFSET]
    remember(mode)

    print(f"[*] Lighting block ({len(block)} bytes):")
    print(f"    frame   : {' '.join(f'{b:02X}' for b in block[:8])}")
    print(
        f"    mode    : 0x{mode:02X} ({layout.mode_name(mode)})"
        f" at report offset 0x{MODE_OFFSET:02X}"
        f" / body offset 0x{layout.MODE_BODY_OFF:02X}"
    )
    if mode == MODE_OFF:
        lit = "OFF (mode 0x00)"
    elif not mode_renders_something(mode):
        lit = "DARK — unknown mode, nothing to draw"
    else:
        lit = "ON per block"
    print(
        f"    backlight: {lit}"
        f"   remembered mode: 0x{remembered_mode():02X}"
        f" ({layout.mode_name(remembered_mode())})"
    )
    print(
        "    (the keyboard hotkey can darken the panel without changing this"
        " block, so a non-zero mode is not proof the backlight is lit)"
    )
    print(f"    block[8:24]: {' '.join(f'{b:02X}' for b in block[8:24])}")


def parse_mode(text):
    try:
        value = int(text, 0)
    except ValueError:
        print(f"[-] Not a mode: {text!r} (use 0..{max(layout.MODES)})")
        sys.exit(2)
    if not 0 <= value <= 0xFF:
        print(f"[-] Mode out of range: {value} (use 0..{max(layout.MODES)})")
        sys.exit(2)
    return value


def usage():
    print(
        "usage: k61se_power.py [off|on|toggle|status|reset|test]\n"
        "       k61se_power.py mode <0..255> [force]\n"
        "\n"
        "  off       set the lighting mode byte to 0x00\n"
        "  on        restore the remembered mode\n"
        "  toggle    off if currently on, otherwise restore\n"
        "  status    read and print the lighting block (no write)\n"
        "  mode N    select effect mode N\n"
        "  reset     software recovery reset\n"
        "  test      explicitly run the old off/dark/on smoke test\n"
        "\n"
        "Unknown actions do nothing. The device performs a write for\n"
        "off/on/toggle/mode/reset/test; status is read-only."
    )


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    force = any(a.lower() == "force" for a in args)
    tokens = [a for a in args if a.lower() != "force"]
    action = tokens[0].lower() if tokens else "toggle"

    if action in ("-h", "--help", "help"):
        usage()
    elif action == "off":
        apply_mode("off", force=force)
    elif action == "on":
        apply_mode("on", force=force)
    elif action == "status":
        print_status()
    elif action == "mode":
        if len(tokens) < 2:
            print("usage: k61se_power.py mode <0..255> [force]")
            sys.exit(2)
        apply_mode("mode", explicit=parse_mode(tokens[1]), force=force)
    elif action == "reset":
        emergency_software_reset()
    elif action == "toggle":
        apply_mode("toggle", force=force)
    elif action == "test":
        apply_mode("toggle", force=force)
        print("[*] Holding dark for 2 seconds...")
        time.sleep(2.0)
        apply_mode("on", force=force)
    else:
        print(f"[-] Unknown action: {action!r}", file=sys.stderr)
        usage()
        sys.exit(2)


if __name__ == "__main__":
    main()
