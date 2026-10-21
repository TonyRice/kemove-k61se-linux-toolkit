#!/usr/bin/env python3
"""Shared device layer for the K61SE tools.

Report 6 is 520 bytes and carries one block at a time:

    read  : SET_FEATURE  06 | op|0x80 | sub | 00 | chunks | index | size_lo | size_hi
            GET_FEATURE  06 | op|0x80 | sub | 00 | chunks | index | size_lo | size_hi | body
    write : SET_FEATURE  06 | op      | sub | 00 | chunks | index | size_lo | size_hi | body

Lighting, colour, and keymap blocks use one chunk (`chunks=01, index=00`).
Large macro blocks use several chunks; `read_body_chunks()` drives that path.

That is not a guess: `captures/color_red/` holds the vendor app's own writes and
its 420-byte colour payload is byte-identical to `dumps/colour_*_red.bin`, and
its 128-byte lighting payload matches ours up to the effect settings we changed
after the fact.

Two rules this module exists to enforce:

  * read-modify-write — never guess a whole block from scratch, because the
    lighting block is full of fields we have not decoded;
  * read back after writing — `send_feature_report()` succeeding only means the
    HID layer accepted the bytes, not that the keyboard did.

Device discovery and the other-writer check are imported from k61se_power.py so
the two tools cannot drift apart.

Only the standard library plus `hid`, so the GUI can import this cheaply.
"""
import glob
import json
import os
import time

import k61se_layout as layout
from k61se_power import (  # re-exported for callers
    PRODUCT_ID,
    REPORT_ID,
    REPORT_LEN,
    VENDOR_ID,
    conflicting_writers,
    get_device,
)

# block name -> (op without the read bit, body size)
BLOCKS = {
    "lighting": (0x04, layout.LIGHTING_SIZE),  # 128 B
    "colour": (0x0A, layout.COLOUR_SIZE),      # 420 B
}
WRITE_ORDER = ("lighting", "colour")  # the order the vendor app uses

COLOUR_OFF = layout.COLOUR_CHANNEL_OFFS  # effect 1's (solid) record: G=0x15 R=0x16 B=0x17


# --------------------------------------------------------------------------- #
# device
# --------------------------------------------------------------------------- #
def open_device():
    """Open management interface 1. Raises RuntimeError if it is not there."""
    return get_device()


def other_writers():
    """[(pid, cmdline)] for processes holding the K61SE open (e.g. the app)."""
    return conflicting_writers()


def _error_text(handle):
    """hidapi's error string is unavailable in some builds."""
    try:
        text = handle.error()
    except Exception:  # noqa: BLE001
        text = ""
    if not text or "not implemented" in str(text).lower():
        return "USB HID operation failed (hidapi supplied no detail)"
    return str(text)


def _frame(op, size, sub=0, chunks=1, index=0):
    return bytes(
        [
            REPORT_ID,
            op & 0xFF,
            sub & 0xFF,
            0x00,
            chunks & 0xFF,
            index & 0xFF,
            size & 0xFF,
            (size >> 8) & 0xFF,
        ]
    )


def _reply_body(raw, opcode):
    """Strip the report-6 header from a GET_FEATURE reply."""
    if len(raw) >= 2 and raw[0] == REPORT_ID:
        return raw[8:]
    if len(raw) >= 1 and raw[0] == (0x80 | opcode):
        return raw[7:]  # hidapi build that strips the report id
    return None


def read_raw(opcode, size, sub=0, dev=None, fill=0x00, attempts=3):
    """Send a read request and return the raw report-6 reply.

    This is a diagnostic for distinguishing a real zero-filled answer from a
    firmware that simply leaves the feature-report buffer untouched.  The
    request payload is filled with ``fill`` instead of zeros; a device that
    echoes the request will return that pattern, while a device that answers
    will replace it with its own data.  It still sends only a read request
    (opcode bit 7 set), never a state-changing write.
    """
    raw = b""
    last_error = None
    for attempt in range(attempts):
        handle = None
        try:
            handle = dev if dev is not None else open_device()
            req = _frame(0x80 | opcode, size, sub)
            payload = req + bytes([fill & 0xFF]) * (REPORT_LEN - len(req))
            if handle.send_feature_report(payload) < 0:
                raise OSError(
                    f"read request failed on op 0x{opcode:02X}: "
                    f"{_error_text(handle)}"
                )
            time.sleep(0.03)
            raw = bytes(handle.get_feature_report(REPORT_ID, REPORT_LEN))
            if len(raw) >= 8:
                return raw
            last_error = OSError(
                f"short raw reply to op 0x{opcode:02X}: "
                f"wanted at least an 8-byte header, got {len(raw)}"
            )
        except OSError as exc:
            last_error = exc
        finally:
            if dev is None and handle is not None:
                handle.close()
        if attempt + 1 < attempts:
            time.sleep(0.05)

    if not raw:
        raise OSError(
            f"empty reply to op 0x{opcode:02X} after {attempts} tries — report 6 "
            "was consumed by another reader, or the keyboard went away"
        )
    raise last_error


def read_body(opcode, size, sub=0, dev=None, attempts=3):
    """Read a block and return just its body (no 8-byte frame header).

    Report 6 holds one report at a time, and a short/empty reply happens when
    something else consumed it (the vendor app polls it) or when the firmware
    was still filling it.  That is transient, so a short reply is retried a
    couple of times before it becomes an error.  With no handle supplied each
    attempt opens the device afresh, so a hidraw node that moved is picked up.
    """
    raw = b""
    last_error = None
    for attempt in range(attempts):
        handle = None
        try:
            handle = dev if dev is not None else open_device()
            req = _frame(0x80 | opcode, size, sub)
            if handle.send_feature_report(req + bytes(REPORT_LEN - len(req))) < 0:
                raise OSError(
                    f"read request failed on op 0x{opcode:02X}: "
                    f"{_error_text(handle)}"
                )
            time.sleep(0.03)  # let the device fill report 6 (captures showed ~29 ms)
            raw = bytes(handle.get_feature_report(REPORT_ID, REPORT_LEN))
            body = _reply_body(raw, opcode)
            if body is not None and len(body) >= size:
                return bytearray(body[:size])
            if body is None:
                last_error = OSError(
                    f"unexpected reply to op 0x{opcode:02X}: {raw[:16].hex(' ')}"
                )
            else:
                last_error = OSError(
                    f"short reply: wanted {size} bytes, got {len(body)}"
                )
        except OSError as exc:
            last_error = exc
        finally:
            if dev is None and handle is not None:
                handle.close()
        if attempt + 1 < attempts:
            time.sleep(0.05)

    if not raw:
        raise OSError(
            f"empty reply to op 0x{opcode:02X} after {attempts} tries — report 6 "
            "was consumed by another reader, or the keyboard went away"
        )
    raise last_error


def read_body_chunks(opcode, size, sub=0, dev=None, attempts=3):
    """Read a block that spans several report-6 chunks.

    The frame's byte 4 is the total chunk count and byte 5 is the chunk index
    (the same layout as the wired F75/SDK transport).  Large macro tables use
    this path; lighting and keymap blocks fit in one 512-byte chunk.

    The caller's handle is left open.  Without one, every retry opens the
    device afresh so a moved hidraw node is picked up.
    """
    if size <= 0:
        return bytearray()
    chunk_max = REPORT_LEN - 8
    total = (size + chunk_max - 1) // chunk_max
    out = bytearray()
    for index in range(total):
        want = min(chunk_max, size - len(out))
        last_error = None
        raw = b""
        for attempt in range(attempts):
            handle = None
            try:
                handle = dev if dev is not None else open_device()
                req = _frame(0x80 | opcode, want, sub, total, index)
                if handle.send_feature_report(req + bytes(REPORT_LEN - len(req))) < 0:
                    raise OSError(
                        f"read request failed on op 0x{opcode:02X} chunk "
                        f"{index + 1}/{total}: {_error_text(handle)}"
                    )
                time.sleep(0.03)
                raw = bytes(handle.get_feature_report(REPORT_ID, REPORT_LEN))
                body = _reply_body(raw, opcode)
                if body is not None and len(body) >= want:
                    out.extend(body[:want])
                    break
                if body is None:
                    last_error = OSError(
                        f"unexpected reply to op 0x{opcode:02X} chunk "
                        f"{index + 1}/{total}: {raw[:16].hex(' ')}"
                    )
                else:
                    last_error = OSError(
                        f"short reply on op 0x{opcode:02X} chunk "
                        f"{index + 1}/{total}: wanted {want} bytes, "
                        f"got {len(body)}"
                    )
            except OSError as exc:
                last_error = exc
            finally:
                if dev is None and handle is not None:
                    handle.close()
            if attempt + 1 < attempts:
                time.sleep(0.05)
        else:
            if not raw:
                raise OSError(
                    f"empty reply to op 0x{opcode:02X} chunk {index + 1}/{total} "
                    f"after {attempts} tries — report 6 was consumed by another "
                    "reader, or the keyboard went away"
                )
            raise last_error
    return out


def write_body(opcode, body, sub=0, dev=None):
    """Write a block body (no frame header)."""
    own = dev is None
    dev = dev or open_device()
    try:
        out = _frame(opcode, len(body), sub) + bytes(body)
        out += bytes(max(0, REPORT_LEN - len(out)))
        written = dev.send_feature_report(out[:REPORT_LEN])
        if written < 0:
            raise OSError(f"write failed: {_error_text(dev)}")
        return written
    finally:
        if own:
            dev.close()


def read_block(name, dev=None):
    opcode, size = BLOCKS[name]
    return read_body(opcode, size, dev=dev)


def write_block(name, body, dev=None):
    opcode, size = BLOCKS[name]
    if len(body) != size:
        raise ValueError(f"{name} body must be {size} bytes, got {len(body)}")
    return write_body(opcode, body, dev=dev)


def read_state(dev=None):
    """{'lighting': body, 'colour': body}.

    With no handle supplied each block opens the device separately, so a node
    that moves between the two reads is picked up again; a caller's handle is
    used as given and left open.
    """
    return {name: read_block(name, dev=dev) for name in WRITE_ORDER}


def describe(state):
    """One-line human summary of a read_state() dict."""
    return "  |  ".join(
        f"{name}: {layout.state_summary(name, body)}" for name, body in state.items()
    )


def colour_of(colour_body, mode=1):
    """(r, g, b) stored for effect `mode` in a colour block body.

    Defaults to effect 1, whose record sits at 0x15..0x17 — the offsets the
    first red/green/blue dumps exposed.  Each effect has its own record; see
    layout.colour_record_off().
    """
    offs = layout.colour_record_offs(mode) or COLOUR_OFF
    return (
        colour_body[offs["red"]],
        colour_body[offs["green"]],
        colour_body[offs["blue"]],
    )


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #
def set_state(
    effect=None,
    brightness=None,
    speed=None,
    colour=None,
    rainbow=None,
    colour_mode=None,
    dev=None,
):
    """Read-modify-write the lighting (and, if asked, colour) block.

    Every argument is optional; None means "leave it alone".  `brightness` and
    `speed` are the 1..5 scales and `rainbow` is the firmware's own rainbow
    (True) versus drawing the colour block (False) — both live in the parameter
    slot of the mode that will be active *after* this call.

    `colour` is written into an effect's **own** 21-byte record in the colour
    block (see layout.colour_record_off).  Which effect defaults to the one
    active after this call; pass `colour_mode` to paint a different one (e.g.
    1 for the old 0x15..0x17 behaviour, which is solid's record).

    Returns a report dict:
        changes  list of human strings, what actually differed
        notes    list of human strings, caveats
        lighting bytes written (or read, if nothing moved)
        colour   bytes written, or None
        verify   {'lighting': bool|None, 'colour': bool|None}
    """
    # Each block op re-opens the device when the caller did not supply one, so a
    # management interface that re-enumerates mid-apply (the hidraw node did move
    # during the 2026-10-19 audit) is picked up again by the next op instead of
    # failing on a stale handle.
    handle = dev
    changes, notes = [], []
    try:
        light = read_block("lighting", dev=handle)
        before_light = bytes(light)

        if effect is not None:
            effect = int(effect) & 0xFF
            was = light[layout.MODE_BODY_OFF]
            if was != effect:
                changes.append(
                    f"effect 0x{was:02X} ({layout.mode_name(was)}) -> "
                    f"0x{effect:02X} ({layout.mode_name(effect)})"
                )
            light[layout.MODE_BODY_OFF] = effect

        mode = light[layout.MODE_BODY_OFF]
        slot = layout.effect_slot(mode)
        if brightness is not None or speed is not None or rainbow is not None:
            if slot is None:
                notes.append(
                    f"mode 0x{mode:02X} has no parameter slot — "
                    "brightness/speed/rainbow skipped"
                )
            else:
                index = (slot - layout.PARAMS_OFF) // 2
                old_b, old_s = light[slot], light[slot + 1]
                if brightness is not None:
                    light[slot] = max(1, min(5, int(brightness)))
                if speed is not None or rainbow is not None:
                    level = (
                        int(speed) if speed is not None else layout.speed_level(old_s)
                    )
                    if rainbow is None:
                        # leave the flag nibble verbatim, since ours is not the
                        # only thing that byte could be carrying
                        light[slot + 1] = layout.speed_byte(level, low=old_s & 0x0F)
                    else:
                        light[slot + 1] = layout.speed_byte(level, rainbow=bool(rainbow))
                if light[slot] != old_b:
                    changes.append(
                        f"brightness {old_b} -> {light[slot]}  (entry[{index}])"
                    )
                if light[slot + 1] != old_s:
                    changes.append(
                        f"{layout.slot_summary(old_s)} -> "
                        f"{layout.slot_summary(light[slot + 1])}  (entry[{index}])"
                    )
                elif rainbow is not None:
                    notes.append(
                        f"rainbow already"
                        f" {'on' if layout.rainbow_flag(old_s) else 'off'}"
                    )
                if rainbow is False and colour is None:
                    if layout.flag_is_ignored(mode):
                        notes.append(
                            f"the flag has no effect on mode 0x{mode:02X} — it draws "
                            "its own colours either way (the write is harmless)"
                        )
                    else:
                        notes.append(
                            f"flag off makes mode 0x{mode:02X} draw its own colour "
                            f"record at 0x{layout.colour_record_off(mode):02X} — write a "
                            "colour too if that record is black, or the panel is dark"
                        )

        if bytes(light) != before_light:
            write_block("lighting", light, dev=handle)
        else:
            notes.append("no lighting change to send")

        colour_body = None
        if colour is not None:
            r, g, b = (max(0, min(255, int(v))) for v in colour)
            target = (
                light[layout.MODE_BODY_OFF] if colour_mode is None else int(colour_mode) & 0xFF
            )
            offs = layout.colour_record_offs(target)
            if offs is None:
                notes.append(
                    f"mode 0x{target:02X} has no colour record — colour skipped"
                )
            else:
                colour_body = read_block("colour", dev=handle)
                was = colour_of(colour_body, target)
                if was != (r, g, b):
                    changes.append(
                        f"colour of 0x{target:02X} ({layout.mode_name(target)}) "
                        f"{was} -> {(r, g, b)}  "
                        f"(record at 0x{layout.colour_record_off(target):02X})"
                    )
                    colour_body[offs["red"]] = r
                    colour_body[offs["green"]] = g
                    colour_body[offs["blue"]] = b
                    write_block("colour", colour_body, dev=handle)
                else:
                    notes.append(
                        f"0x{target:02X} ({layout.mode_name(target)}) already stores "
                        f"colour {(r, g, b)}"
                    )
                if slot is not None:
                    mode_now = light[layout.MODE_BODY_OFF]
                    if mode_now != target:
                        notes.append(
                            f"wrote 0x{target:02X}'s record while 0x{mode_now:02X} is "
                            "the active effect — switching to it later will show this colour"
                        )
                    elif layout.flag_is_ignored(mode_now):
                        notes.append(
                            "mode 0x%02X draws its own colours whatever the flag says, so"
                            " the record is stored but cannot be shown by it" % mode_now
                        )
                    elif layout.rainbow_flag(light[slot + 1]):
                        notes.append(
                            "mode 0x%02X is in rainbow mode, so the firmware ignores its"
                            " colour record — switch rainbow off to see this colour"
                            % mode_now
                        )
                    elif layout.clearing_flag_darkens(mode_now, (r, g, b)):
                        notes.append(
                            "that colour is black, and mode 0x%02X draws it with the flag"
                            " off — the panel will be dark (that is the intended way to"
                            " switch one effect off)" % mode_now
                        )

        verify = {"lighting": None, "colour": None}
        try:
            verify["lighting"] = bytes(read_block("lighting", dev=handle)) == bytes(light)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"lighting read-back failed: {exc}")
        if colour_body is not None:
            try:
                verify["colour"] = bytes(read_block("colour", dev=handle)) == bytes(colour_body)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"colour read-back failed: {exc}")

        return {
            "changes": changes,
            "notes": notes,
            "lighting": bytes(light),
            "colour": bytes(colour_body) if colour_body is not None else None,
            "verify": verify,
        }
    finally:
        if dev is None and handle is not None:  # never close a caller's handle
            handle.close()


# --------------------------------------------------------------------------- #
# configs / presets
# --------------------------------------------------------------------------- #
PRESET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets")


def _as_int(value, what):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{what}: expected a number, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            raise ValueError(f"{what}: not a number: {value!r}") from None
    raise ValueError(f"{what}: expected a number or '0xNN', got {value!r}")


def parse_config(raw):
    """Normalise a preset dict. Raises ValueError on anything unusable."""
    if not isinstance(raw, dict):
        raise ValueError("preset must be a JSON object")

    effect = _as_int(raw.get("effect"), "effect")
    if effect is not None and not 0 <= effect <= 0xFF:
        raise ValueError(f"effect 0x{effect:X} out of range")

    brightness = _as_int(raw.get("brightness"), "brightness")
    if brightness is not None and not 1 <= brightness <= 5:
        raise ValueError(f"brightness {brightness} out of range (1..5)")

    speed = _as_int(raw.get("speed"), "speed")
    if speed is not None and not 1 <= speed <= layout.SPEED_MAX:
        raise ValueError(f"speed {speed} out of range (1..{layout.SPEED_MAX})")

    colour = raw.get("colour")
    if colour is not None:
        if not isinstance(colour, (list, tuple)) or len(colour) != 3:
            raise ValueError("colour must be [r, g, b]")
        colour = tuple(max(0, min(255, _as_int(v, "colour"))) for v in colour)

    rainbow = raw.get("rainbow")
    if rainbow is not None:
        if not isinstance(rainbow, bool):
            raise ValueError(f"rainbow must be true or false, got {rainbow!r}")

    return {
        "name": raw.get("name") or "",
        "effect": effect,
        "brightness": brightness,
        "speed": speed,
        "rainbow": rainbow,
        "colour": colour,
        "notes": raw.get("notes") or "",
    }


def load_config(path):
    with open(path) as f:
        cfg = parse_config(json.load(f))
    cfg["path"] = path
    return cfg


def save_config(path, cfg):
    body = {
        "name": cfg.get("name") or os.path.splitext(os.path.basename(path))[0],
        "effect": cfg.get("effect"),
        "brightness": cfg.get("brightness"),
        "speed": cfg.get("speed"),
        "rainbow": cfg.get("rainbow"),
        "colour": list(cfg["colour"]) if cfg.get("colour") else None,
        "notes": cfg.get("notes") or "",
    }
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w") as f:
        json.dump(body, f, indent=2)
        f.write("\n")
    return path


def slug(text):
    """Filename-safe slug for a preset name."""
    out = "".join(c.lower() if c.isalnum() else "_" for c in text.strip())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "preset"


def list_presets(directory=PRESET_DIR):
    """Sorted [(path, cfg_or_None)] — a broken preset must not break the list."""
    found = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            found.append((path, load_config(path)))
        except Exception:  # noqa: BLE001
            found.append((path, None))
    return found


def apply_config(cfg, dev=None):
    """set_state() driven by a parsed config."""
    return set_state(
        effect=cfg.get("effect"),
        brightness=cfg.get("brightness"),
        speed=cfg.get("speed"),
        rainbow=cfg.get("rainbow"),
        colour=cfg.get("colour"),
        dev=dev,
    )
