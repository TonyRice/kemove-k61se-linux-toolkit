#!/usr/bin/env python3
"""Universal read-only reader for the KEMOVE K61SE vendor feature-report blocks.

Frame (report 6, 520-byte feature report):
  06 | op | sub | 00 | chunks=01 | index=00 | size_lo | size_hi | <size bytes>
  bit 0x80 in `op` means READ.

This tool only ever sends READ requests — it never writes to the device.

IMPORTANT about the reply length
  The device always returns exactly 8 + declared_size bytes, for every op. So
  the length tells you nothing about whether an op is a real block: asking
  op 0x84 for 512 bytes returns 520 bytes, just like an unimplemented op. Judge
  a read by its CONTENT, not its length. Which ops actually exist comes from
  captures of the vendor app, not from sweeping.

  Known from captures, the app only ever uses:
    op 0x82  sub 1  info        6 bytes  reply "03 00 00 00 01 71"
    op 0x84  sub 0  lighting  128 bytes  mode + brightness + 34 x 2-byte entries
    op 0x8A  sub 0  colour    420 bytes  RGB-channel mask + effect flags
  Unknown ops return memory contents (we have seen coherent 8051 firmware).

Usage
  sudo python3 dump_lighting.py                         # lighting + colour
  sudo python3 dump_lighting.py --manual                # label each theme by hand
  sudo python3 dump_lighting.py lighting                # just the 128-byte block
  sudo python3 dump_lighting.py --save green            # save both, label "green"
  sudo python3 dump_lighting.py lighting --save green   # label only this block
  sudo python3 dump_lighting.py --op 0x02 --sub 1 --size 6
  sudo python3 dump_lighting.py --op 0x04 --sizes 64,128,256,512
  sudo python3 dump_lighting.py --sweep                 # content browser, 0x00..0x1F
  sudo python3 dump_lighting.py --sweep --full          # + full hexdump per op
  sudo python3 dump_lighting.py --sweep 0x00-0x3F --sub 1

  --save [LABEL]   write each block body to dumps/<block>_<timestamp>[_<label>].bin
                   and diff it against the previous dump of that block. A token
                   right after --save is the label unless it is a block name.
                   Saves never overwrite: a second save in the same second gets
                   a _2 suffix.
  --app-walk       walk the vendor app's effect list: it names the effect, you
                   select its colourful version, Enter; you switch "colourful"
                   off, Enter. Two labelled snapshots per effect (lighting and
                   colour), then a diff of exactly what the app changed and a
                   verdict in app_effect_walk.md.  Read-only; the app stays open.
  --start 0x0B     with --app-walk, resume from this effect.
  --sweep [A-B]    dump every op in the range as one compact content row.
  --sizes A,B,C    with --op, read the same op at several declared sizes so you
                   can see whether the size changes the content or just pads it.
  --sub N          sub byte for the sweep / arbitrary --op (default 0).

  --manual         RECOMMENDED for theme sweeps: one on-demand read per Enter,
                   named by you. Click a theme in the vendor app, then type a
                   name here. Enter alone = auto label; q or Ctrl-D = finish.
                   Nothing is read or sent until you press Enter, and the device
                   is opened and closed per capture, so the app can stay open.
  --watch          auto-save a dump each time the state changes, labelled from
                   the decoded content (e.g. m02pulse_rgb, m01solid_r). Start it,
                   click through every theme/colour in the vendor app, then
                   Ctrl-C for a summary.
                   PASSIVE by default: it only ever GETs report 6 and never
                   sends a request, so it cannot disturb the vendor app — it
                   sees the blocks the app itself wrote.
  --active         make --watch request-poll each block instead, so it can see
                   the current state rather than only the app's writes. It
                   shares report 6 with the app and CAN stop the app from
                   finding the keyboard — use it with the app closed.
  --passive        one-shot diagnostic: GET report 6 without sending anything
                   and show what came back. Run it, change a theme in the app,
                   run it again — if the bytes changed, passive watch will work.
  --interval SEC   watch poll interval (default 0.35).
  --label PREFIX   in watch mode, prefix each auto label (e.g. --label theme
                   gives theme_m02pulse_rgb).
"""
import glob
import hashlib
import os
import re
import sys
import time

import k61se_layout as layout

VENDOR_ID = 0x258A
PRODUCT_ID = 0x010C
REPORT_ID = 0x06
REPORT_LEN = 520

DUMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dumps")

# opcode (without the 0x80 read bit) -> (name, size, sub)
KNOWN_BLOCKS = {
    0x02: ("info", 6, 1),
    0x04: ("lighting", layout.LIGHTING_SIZE, 0),
    0x0A: ("colour", layout.COLOUR_SIZE, 0),
}
BLOCK_NAMES = {"info", "lighting", "colour", "all"}
DEFAULT_TARGETS = ("lighting", "colour")
SWEEP_DEFAULT = (0x00, 0x1F)
SWEEP_SIZE_DEFAULT = REPORT_LEN - 8  # 512


# --------------------------------------------------------------------------- #
# device I/O
# --------------------------------------------------------------------------- #
def get_device():
    import hid  # lazy: keeps this module importable without hid/sudo

    for d in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        if d.get("interface_number") == 1:
            dev = hid.device()
            dev.open_path(d["path"])
            return dev
    raise RuntimeError("K61SE interface 1 not detected.")


def read_block(dev, opcode, size, sub=0):
    """Read a vendor block: SET_FEATURE(op|0x80) then GET_FEATURE report 6."""
    hdr = bytes(
        [
            REPORT_ID,
            0x80 | opcode,
            sub & 0xFF,
            0x00,
            0x01,
            0x00,
            size & 0xFF,
            (size >> 8) & 0xFF,
        ]
    )
    if dev.send_feature_report(hdr + bytes(REPORT_LEN - len(hdr))) < 0:
        raise OSError(f"read request failed: {dev.error()}")
    time.sleep(0.03)  # let the device fill report 6 (capture showed ~29 ms)
    raw = bytes(dev.get_feature_report(REPORT_ID, REPORT_LEN))
    # Normalise a hidapi build that strips the leading report id.
    if len(raw) >= 2 and raw[0] == REPORT_ID:
        return raw
    if len(raw) >= 1 and raw[0] == (0x80 | opcode):
        return bytes([REPORT_ID]) + raw
    return raw


def split_report(raw):
    """Parse a raw report-6 buffer -> (op, sub, size, body), or None.

    Accepts both a report that still carries the leading report id and a raw
    buffer from which hidapi stripped it.
    """
    raw = bytes(raw)
    if len(raw) >= 8 and raw[0] == REPORT_ID:
        frame, body = raw[:8], raw[8:]
    elif len(raw) >= 7:
        frame, body = bytes([REPORT_ID]) + raw[:7], raw[7:]
    else:
        return None
    return frame[1], frame[2], frame[6] | (frame[7] << 8), body


def read_passive(dev):
    """GET_FEATURE report 6 WITHOUT sending a read request first.

    Nothing is ever written to the device, so this cannot disturb the vendor
    app. What comes back is whatever the device's report-6 buffer currently
    holds — i.e. the last block the app (or anyone else) wrote. Returns
    (op, sub, size, body) or None.
    """
    return split_report(bytes(dev.get_feature_report(REPORT_ID, REPORT_LEN)))


def block_name(op):
    """Name a block from an op byte, ignoring the 0x80 read bit."""
    return KNOWN_BLOCKS.get(op & 0x7F, (None, None, None))[0] or f"op{op & 0x7F:02X}"


# --------------------------------------------------------------------------- #
# dumps
# --------------------------------------------------------------------------- #
def dump_path(name, label=None):
    """dumps/<name>_<timestamp>[_<label>][_<n>].bin — never overwrites."""
    stem = f"{name}_{time.strftime('%Y%m%d_%H%M%S')}" + (f"_{label}" if label else "")
    path = os.path.join(DUMP_DIR, stem + ".bin")
    n = 2
    while os.path.exists(path):
        path = os.path.join(DUMP_DIR, f"{stem}_{n}.bin")
        n += 1
    return path


def previous_dump(name):
    files = glob.glob(os.path.join(DUMP_DIR, f"{name}_*.bin"))
    if not files:
        return None, None
    files.sort(key=lambda p: (os.path.getmtime(p), p))
    return files[-1], open(files[-1], "rb").read()


def save_block(name, body, label=None, log=print, diff=True):
    prev_path, prev = previous_dump(name)
    if diff and prev is not None:
        if len(prev) == len(body):
            diffs = [i for i in range(len(body)) if prev[i] != body[i]]
            if diffs:
                changed = ", ".join(
                    f"0x{i:02X}:{prev[i]:02X}->{body[i]:02X}" for i in diffs
                )
                log(f"  diff vs {os.path.basename(prev_path)}: {changed}")
            else:
                log(f"  no change vs {os.path.basename(prev_path)}")
        else:
            log(
                f"  (previous dump {os.path.basename(prev_path)} is "
                f"{len(prev)} bytes, skipped diff)"
            )

    os.makedirs(DUMP_DIR, exist_ok=True)
    path = dump_path(name, label)
    with open(path, "wb") as f:
        f.write(body)
    log(f"  saved: {os.path.relpath(path)}")


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #
def read_and_show(dev, opcode, size, sub=0, save=False, label=None):
    name = KNOWN_BLOCKS.get(opcode, (None, None, None))[0] or f"op{opcode:02X}"
    print(
        f"\n=== {name} :: op 0x{0x80 | opcode:02X} sub {sub} "
        f"(write 0x{opcode:02X}) :: {size} bytes ==="
    )
    try:
        resp = read_block(dev, opcode, size, sub=sub)
    except Exception as e:
        print(f"  read failed: {e}")
        return
    if len(resp) < 8:
        print(f"  short response ({len(resp)} bytes): {resp.hex(' ')}")
        return

    frame, body = resp[:8], resp[8:]
    print(
        f"  frame : {' '.join(f'{b:02X}' for b in frame)}"
        f"  (op=0x{frame[1]:02X} size={frame[6] | frame[7] << 8})"
    )
    print(f"  body  : {len(body)} bytes")
    if opcode not in KNOWN_BLOCKS:
        print("  note  : unknown op — this is memory content, not configuration")
    print(layout.hexdump(body))

    if opcode == 0x04 and len(resp) > layout.MODE_REPORT_OFF:
        p = resp[layout.MODE_REPORT_OFF]
        print(
            f"  mode  : 0x{p:02X} ({layout.mode_name(p)})"
            f" at report offset 0x{layout.MODE_REPORT_OFF:02X}"
            f" / body offset 0x{layout.MODE_BODY_OFF:02X}"
        )
        print(layout.format_palette(body))

    if save:
        save_block(name, body, label)


def sweep_ops(dev, lo, hi, size, sub=0, full=False, save=False, label=None):
    """Browse every op in the range as a content row (length proves nothing)."""
    print(
        f"\n=== opcode sweep 0x{lo:02X}..0x{hi:02X}  "
        f"(declared size {size}, sub {sub}) ==="
    )
    print("    every reply is 8 + declared size; judge by CONTENT, not length")
    print("    'nonzero' = payload bytes that are not 0x00")

    hits = []
    for op in range(lo, hi + 1):
        known = KNOWN_BLOCKS.get(op)
        use_sub = known[2] if known else sub
        name = known[0] if known else "?"
        try:
            resp = read_block(dev, op, size, sub=use_sub)
        except Exception as e:
            print(f"  op 0x{0x80 | op:02X} (0x{op:02X})  read failed: {e}")
            continue

        body = resp[8:] if len(resp) > 8 else b""
        nonzero = sum(1 for b in body if b)
        head = " ".join(f"{b:02X}" for b in body[:16])
        print(
            f"  op 0x{0x80 | op:02X} (0x{op:02X})  {name:<9} sub {use_sub}"
            f"  got {len(body):>4}B  nonzero {nonzero:>4}  {head}"
        )
        if full:
            print(layout.hexdump(body))
        if nonzero:
            hits.append((op, name, nonzero))
        if save and nonzero:
            save_block(known[0] if known else f"op{op:02X}", body, label)

    print("\n  known blocks: 0x82 info(sub 1), 0x84 lighting, 0x8A colour")
    if hits:
        print(
            "  non-zero ops: "
            + ", ".join(f"0x{0x80 | op:02X}({name}, {nz}B)" for op, name, nz in hits)
        )
        print("  note: ops outside the known set return memory/ROM content")
    else:
        print("  no non-zero payloads in range")


# --------------------------------------------------------------------------- #
# watch mode
# --------------------------------------------------------------------------- #
def show_passive(dev):
    """One-shot diagnostic: can a bare GET_FEATURE see the app's writes?"""
    print("\n=== passive read :: GET_FEATURE report 6, nothing sent ===")
    try:
        got = read_passive(dev)
    except Exception as e:
        print(f"  read failed: {e}")
        return
    if got is None:
        print("  short/empty report — the device returned nothing usable.")
        return
    op, sub, size, body = got
    kind = "read-response" if op & 0x80 else "write"
    print(f"  frame : 06 {op:02X} {sub:02X} ... declared size {size}")
    print(f"  block : {block_name(op)} ({kind} frame), body {len(body)}B")
    if not any(body):
        print("  body is all zeros:")
        print("    either nothing has been written since power-up, or this device")
        print("    does not keep its write buffer readable. See the test below.")
    else:
        print(layout.hexdump(body[:64]))
        print("  (first 64 bytes)")
    print("\n  is this usable?")
    print("    1. run this, note the frame/bytes above")
    print("    2. change a theme in the vendor app")
    print("    3. run this again — changed bytes => passive watch will work")


def commit_state(state, meta, prefix=None):
    """Save the blocks in `state` that changed and have not been saved yet.

    `state` is {block name: body}. `meta` carries the session bookkeeping:
    saved_last, seen, counts, count. Returns the list of (name, body) saved.
    """
    pending = [
        (name, body)
        for name, body in sorted(state.items())
        if body != meta["saved_last"].get(name)
        and hashlib.sha1(body).hexdigest() not in meta["seen"].get(name, set())
    ]
    for name, body in sorted(state.items()):
        meta["saved_last"][name] = body
    if not pending:
        return []

    base = layout.auto_label(
        lighting=state.get("lighting"), colour=state.get("colour")
    )
    if prefix:
        base = f"{prefix}_{base}"
    # Two states can decode to the same label (same mode + channels but a
    # different entry table, say). Keep labels unique so `-d <label>` always
    # means one specific state.
    meta["counts"][base] = meta["counts"].get(base, 0) + 1
    label = base if meta["counts"][base] == 1 else f"{base}_{meta['counts'][base]}"
    meta["count"] += 1

    print(f"\n[state {meta['count']}]  label = {label}")
    for name, body in pending:
        print(f"    {name:<8} {layout.state_summary(name, body)}")
    for name, body in pending:
        meta["seen"].setdefault(name, set()).add(hashlib.sha1(body).hexdigest())
        save_block(name, body, label)
    return pending


def watch_active(dev, jobs, interval=0.35, prefix=None):
    """Request-polling watch (sends read requests — needs the app closed).

    A state is only committed once it has SETTLED (two consecutive reads agree),
    so a burst of app writes is captured as one state instead of several
    half-written ones.
    """
    print(f"\n=== watch (active) :: {len(jobs)} block(s), poll every {interval:.2f}s ===")
    print("    [!] this SENDS read requests. Report 6 is shared with the vendor")
    print("    app, so polling can stop the app finding the keyboard. Use it with")
    print("    the app CLOSED, or use the default passive watch instead.")
    print("    Ctrl-C to stop.\n")

    def read_state():
        state = {}
        for opcode, size, sub, name in jobs:
            resp = read_block(dev, opcode, size, sub=sub)
            state[name] = resp[8:]
        return state

    def safe_state():
        try:
            return read_state()
        except Exception as e:
            print(f"  read failed: {e}")
            return None

    def key_of(state):
        return tuple(state[n] for n in sorted(state))

    meta = {"saved_last": {}, "seen": {}, "counts": {}, "count": 0}
    last_key = None

    try:
        while True:
            state = safe_state()
            if state is None:
                time.sleep(1.0)
                continue
            key = key_of(state)

            if key == last_key:
                time.sleep(interval)
                continue

            # Let the app finish writing every block before we commit anything.
            for _ in range(6):
                time.sleep(0.12)
                again = safe_state()
                if again is None:
                    continue
                if key_of(again) == key:
                    break
                state, key = again, key_of(again)

            last_key = key
            if not commit_state(state, meta, prefix):
                print("  (back to an already-captured state)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n\n=== watch stopped: {meta['count']} distinct state(s) captured ===")
        if meta["count"]:
            print("  next:  python3 review_dumps.py -v     # what varies across them")
            print("         python3 review_dumps.py        # list them")


def watch_passive(dev, jobs, interval=0.35, prefix=None, settle=0.35):
    """Watch by READING ONLY — nothing is ever sent to the device.

    The vendor app's writes land in report 6, so a bare GET_FEATURE shows the
    last block it wrote. We remember the newest lighting and colour bodies and,
    once the traffic settles, save them together under one decoded label. Since
    no request is ever sent, the app cannot be disturbed.
    """
    wanted = {name: size for _, size, _, name in jobs}
    print(
        f"\n=== watch (passive) :: {len(wanted)} block(s), read every {interval:.2f}s ==="
    )
    print("    NOT sending anything to the device, so the vendor app is safe.")
    print("    waiting for the app's own writes to appear in report 6...")
    print("    if nothing is ever captured, stop and check with:")
    print("      sudo python3 dump_lighting.py --passive")
    print("    click through the themes / colours now — Ctrl-C to stop.\n")

    meta = {"saved_last": {}, "seen": {}, "counts": {}, "count": 0}
    latest = {}
    dirty, last_change, announced = False, 0.0, False

    try:
        while True:
            try:
                got = read_passive(dev)
            except Exception as e:
                print(f"  read failed: {e}")
                time.sleep(1.0)
                continue

            if got is not None:
                op, _sub, _size, body = got
                name = block_name(op)
                want = wanted.get(name)
                if want and len(body) >= want and any(body[:want]):
                    body = body[:want]
                    if latest.get(name) != body:
                        latest[name] = body
                        dirty, last_change = True, time.monotonic()
                        if not announced:
                            print("  (report 6 traffic seen — watching)")
                            announced = True

            if dirty and time.monotonic() - last_change >= settle:
                dirty = False
                commit_state(latest, meta, prefix)

            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n\n=== watch stopped: {meta['count']} distinct state(s) captured ===")
        if meta["count"]:
            print("  next:  python3 review_dumps.py -v     # what varies across them")
            print("         python3 review_dumps.py        # list them")
        else:
            print("  nothing was captured — the app may not have written anything,")
            print("  or report 6 may not stay readable. Try: dump_lighting.py --passive")


def watch(dev, jobs, interval=0.35, prefix=None, active=False):
    """Dispatch to the passive (default) or request-polling watcher."""
    if active:
        return watch_active(dev, jobs, interval=interval, prefix=prefix)
    return watch_passive(dev, jobs, interval=interval, prefix=prefix)


def watch_manual(jobs, prefix=None):
    """Manual labelling: one on-demand read per capture, named by you.

    Nothing is read until you press Enter, and the device is opened and closed
    around each capture — so between themes the tool holds nothing and sends
    nothing. Each capture is the same single read `--save` has always done,
    which is the safe way to work with the vendor app open.
    """
    print("\n=== manual labelling :: one read per Enter ===")
    print("    click a theme in the vendor app, then type a name here.")
    print("      Enter on its own  -> label it automatically from the contents")
    print("      q or Ctrl-D       -> finish")
    print("    nothing is read or sent until you press Enter, and the device is")
    print("    opened and closed per capture, so the app can stay open.\n")

    meta = {"saved_last": {}, "count": 0}
    try:
        while True:
            try:
                answer = input(
                    f"capture #{meta['count'] + 1}  label (Enter=auto, q=quit): "
                ).strip()
            except EOFError:
                break
            if answer.lower() in ("q", "quit", "exit"):
                break

            try:
                dev = get_device()
            except Exception as e:
                print(f"    could not open the device: {e}")
                continue
            try:
                state = {}
                for opcode, size, sub, name in jobs:
                    state[name] = read_block(dev, opcode, size, sub=sub)[8:]
            except Exception as e:
                print(f"    read failed: {e}")
                continue
            finally:
                try:
                    dev.close()
                except Exception:
                    pass

            label = answer or layout.auto_label(
                lighting=state.get("lighting"), colour=state.get("colour")
            )
            if not answer and prefix:
                label = f"{prefix}_{label}"

            meta["count"] += 1
            print(f"\n[capture {meta['count']}]  label = {label}")
            for name, body in sorted(state.items()):
                print(f"    {name:<8} {layout.state_summary(name, body)}")
            if meta["saved_last"] and all(
                body == meta["saved_last"].get(name) for name, body in state.items()
            ):
                print("    [!] byte-identical to the previous capture — did the app")
                print("        register the change? Saving it anyway.")
            for name, body in sorted(state.items()):
                meta["saved_last"][name] = body
                save_block(name, body, label)
    except KeyboardInterrupt:
        pass

    print(f"\n=== manual session ended: {meta['count']} capture(s) ===")
    if meta["count"]:
        print("  next:  python3 review_dumps.py         # list them")
        print("         python3 review_dumps.py -v      # what varies across them")


# --------------------------------------------------------------------------- #
# app walk: one read per app click, both renderings of every effect
# --------------------------------------------------------------------------- #
WALK_BLOCKS = ((0x84, 128, 0, "lighting"), (0x8A, 420, 0, "colour"))
WALK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_effect_walk.md")


def _walk_snapshot():
    """The lighting and colour bodies, with the device opened just for this.

    One snapshot is two explicit read requests and a close — the same footprint
    as `--manual`, which is known to be safe with the vendor app running.
    """
    dev = get_device()
    try:
        return {
            name: read_block(dev, opcode, size, sub=sub)[8:]
            for opcode, size, sub, name in WALK_BLOCKS
        }
    finally:
        try:
            dev.close()
        except Exception:
            pass


def _walk_ask_snapshot(ask, log):
    """Enter -> a snapshot, `s` -> None (skip), `q`/Ctrl-D -> KeyboardInterrupt."""
    while True:
        try:
            answer = ask("    capture (Enter / s = skip / q = finish): ").strip().lower()
        except EOFError:
            raise KeyboardInterrupt
        if answer in ("q", "quit", "exit"):
            raise KeyboardInterrupt
        if answer in ("s", "skip"):
            return None
        if answer:
            log("    (Enter to capture, s to skip, q to finish)")
            continue
        try:
            state = _walk_snapshot()
        except Exception as exc:  # noqa: BLE001
            log(f"    read failed: {exc}")
            continue
        for block in ("lighting", "colour"):
            body = state.get(block)
            if body is not None:
                log(f"      {block:<8} {layout.state_summary(block, body)}")
        return state


def _walk_verdict(colourful, single):
    """(verdict, detail lines) from the app's two snapshots of one effect."""
    light_a, light_b = colourful["lighting"], single["lighting"]
    colour_a, colour_b = colourful["colour"], single["colour"]
    mode_a = light_a[layout.MODE_BODY_OFF]
    mode_b = light_b[layout.MODE_BODY_OFF]
    moved_light = [
        i for i in range(min(len(light_a), len(light_b))) if light_a[i] != light_b[i]
    ]
    moved_colour = [
        i for i in range(min(len(colour_a), len(colour_b)))
        if colour_a[i] != colour_b[i]
    ]
    details = [
        "0x%02X %s: %02X -> %02X" % (i, layout.field_name(i), light_a[i], light_b[i])
        for i in moved_light
    ]
    details += [
        "colour 0x%02X %s: %02X -> %02X"
        % (i, layout.field_name(i, "colour"), colour_a[i], colour_b[i])
        for i in moved_colour
    ]
    if not moved_light and not moved_colour:
        return (
            "byte-identical snapshots — the app's toggle wrote nothing (did the "
            "click register?)",
            details,
        )
    if not moved_light:
        return (
            "only the colour block moved — the app did not touch the lighting "
            "block, so this effect must read the channels as it is",
            details,
        )
    if mode_b != mode_a:
        return (
            "the app changed the MODE 0x%02X -> 0x%02X — a colour variant we had "
            "not seen" % (mode_a, mode_b),
            details,
        )
    slot = layout.effect_slot(mode_b)
    if slot is None:
        return "no parameter slot in the second snapshot", details
    if layout.rainbow_flag(light_b[slot + 1]):
        return (
            "the app kept the flag on (nibble still 7) — its colour toggle does "
            "not move this byte, so the colour must come from somewhere else",
            details,
        )
    if layout.flag_is_ignored(mode_b):
        if moved_colour:
            return (
                "flag cleared and its record written, but this effect ignores the "
                "flag — expect no visible change (control case)",
                details,
            )
        return (
            "flag cleared, but this effect renders identically either way "
            "(the firmware ignores it)",
            details,
        )
    record = layout.colour_record_off(mode_b)
    wrote_record = record is not None and any(
        record <= i <= record + 2 for i in moved_colour
    )
    rgb = layout.colour_record_colour(colour_b, mode_b) or (0, 0, 0)
    if wrote_record:
        return (
            "flag cleared AND the app wrote this effect's own colour record "
            "(0x%02X = #%02X%02X%02X) — real colour (confirmed on the panel "
            "2026-10-19 with --effect-colour)" % (record, rgb[0], rgb[1], rgb[2]),
            details,
        )
    if any(rgb):
        return (
            "flag cleared; this effect's own record (0x%02X = #%02X%02X%02X) was "
            "already set, so the app had nothing to write — expect a real colour"
            % (record, rgb[0], rgb[1], rgb[2]),
            details,
        )
    return (
        "flag cleared, and this effect's own record (0x%02X) is black — expect a "
        "dark panel here (the app has the same gap)" % record,
        details,
    )


def _walk_report(rows, path=None, log=print):
    path = path or WALK_PATH
    lines = [
        "# Vendor app effect walk",
        "",
        f"Run {time.strftime('%Y-%m-%d %H:%M')} via "
        "`sudo python3 dump_lighting.py --app-walk`.",
        "",
        "The vendor app was the only writer. Each snapshot is one read of the",
        "lighting and colour blocks taken right after the click, saved in `dumps/`",
        "as `<block>_<timestamp>_app_<mode>_<effect>_colourful.bin` and `..._single.bin`.",
        "",
        "| mode | effect | colourful slot | colour off slot | colour record | verdict |",
        "|---|---|---|---|---|---|",
    ]
    for mode, name, colourful, single, verdict, details in rows:
        def slot_of(state):
            if state is None:
                return "-"
            body = state["lighting"]
            slot = layout.effect_slot(body[layout.MODE_BODY_OFF])
            if slot is None:
                return "-"
            return f"`{body[slot]:02X} {body[slot + 1]:02X}`"

        def rgb(state):
            if state is None:
                return "-"
            body = state["colour"]
            mode = state["lighting"][layout.MODE_BODY_OFF]
            r, g, b = layout.colour_record_colour(body, mode) or (0, 0, 0)
            return f"`#{r:02X}{g:02X}{b:02X}` (record 0x{layout.colour_record_off(mode):02X})"

        lines.append(
            f"| `0x{mode:02X}` | {name} | {slot_of(colourful)} | {slot_of(single)} "
            f"| {rgb(single)} | {verdict} |"
        )
    moved = [
        (mode, name, details)
        for mode, name, _c, _s, _v, details in rows
        if details
    ]
    if moved:
        lines += ["", "Bytes the app moved between the two renderings:", ""]
        for mode, name, details in moved:
            lines.append(f"- `0x{mode:02X}` {name}: " + "; ".join(details))
    lines += [
        "",
        "A single changed byte inside the effect's own parameter word is the",
        "expected answer (brightness low, speed high, flag nibble low). Anything",
        "else — a moved mode byte, a new offset, an unchanged flag — is the",
        "interesting result: it means the app colours these effects by some path",
        "we have not decoded yet.",
        "",
    ]
    try:
        with open(path, "w") as handle:
            handle.write("\n".join(lines))
        log(f"wrote {path}")
    except OSError as exc:  # noqa: BLE001
        log(f"could not write {path}: {exc}")


def walk_app_effects(start=None, prefix="app", log=print, ask=input):
    """Walk the vendor app's effect list, capturing both renderings of each.

    The app is the only writer. For each effect the tool names it, you select
    its colourful version there, press Enter, then switch its "colourful" toggle
    off and press Enter again. Both snapshots (lighting *and* colour) are saved
    and diffed, which is the one thing a plain dump cannot answer: what the app
    writes for the twelve effects the firmware renders nothing for.
    """
    modes = [
        m for m in sorted(layout.MODES)
        if m != 0x00 and layout.effect_slot(m) is not None
    ]
    if start is not None:
        modes = [m for m in modes if m >= start]
    if not modes:
        log("nothing to walk")
        return 1

    log("\n=== app walk :: two snapshots per effect ===")
    log("    The vendor app does the writing; this tool only reads, opening the")
    log("    device for one snapshot at a time, so the app can stay open.")
    log("    Enter = capture, s = skip this step, q = finish.\n")

    rows = []
    try:
        for index, mode in enumerate(modes, 1):
            name = layout.mode_name(mode)
            slug = "%02x_%s" % (mode, layout.MODE_SLUGS.get(mode, "mode"))
            log(f"[{index}/{len(modes)}] effect 0x{mode:02X}  {name}")

            def save(state, suffix):
                for block, body in sorted(state.items()):
                    try:
                        save_block(
                            block, body, f"{prefix}_{slug}_{suffix}", log=log, diff=False
                        )
                    except OSError as exc:  # noqa: BLE001
                        log(
                            f"    (could not save {block}: {exc} — rerun with sudo to "
                            "keep the dumps)"
                        )

            log('    1) In the app: select this effect with "colourful" ON (rainbow).')
            colourful = _walk_ask_snapshot(ask, log)
            if colourful is None:
                log("    skipped\n")
                continue
            save(colourful, "colourful")

            log('    2) In the app: turn "colourful" OFF, so it should show one colour.')
            single = _walk_ask_snapshot(ask, log)
            if single is None:
                log("    (no colour-mode snapshot for this effect)\n")
                rows.append(
                    (mode, name, colourful, None,
                     "only the colourful state captured", [])
                )
                continue
            save(single, "single")

            verdict, details = _walk_verdict(colourful, single)
            log(f"    -> {verdict}")
            for line in details:
                log(f"       moved: {line}")
            log("")
            rows.append((mode, name, colourful, single, verdict, details))
    except KeyboardInterrupt:
        log("\nstopped early")

    if rows:
        _walk_report(rows, log=log)
        both = sum(1 for r in rows if r[3] is not None)
        log(f"walk: {len(rows)} effect(s), {both} with both snapshots")
        log("  next:  python3 review_dumps.py -v app_     # every offset the app moved")
    else:
        log("nothing captured")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def to_int(text):
    text = text.strip()
    try:
        return int(text, 0)
    except ValueError:
        return int(text, 16)


def parse_range(spec):
    if not spec:
        return SWEEP_DEFAULT
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return to_int(lo), to_int(hi)
    v = to_int(spec)
    return v, v


RANGE_RE = re.compile(r"^(0x[0-9a-fA-F]+|[0-9a-fA-F]+)(-(0x)?[0-9a-fA-F]+)?$")


def parse_args(argv):
    targets, label, save = [], None, False
    sweep_spec, single_op, single_size, sub, full = None, None, None, 0, False
    sizes = None
    watch_mode, interval = False, 0.35
    passive, active, manual = False, False, False
    app_walk, start = False, None

    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        elif a == "--watch":
            watch_mode = True
        elif a == "--passive":
            passive = True
        elif a == "--active":
            active = True
        elif a == "--manual":
            manual, watch_mode = True, True        # manual implies watch semantics
        elif a == "--app-walk":
            app_walk = True
        elif a == "--start":
            start = to_int(argv[i + 1])
            i += 1
        elif a.startswith("--start="):
            start = to_int(a.split("=", 1)[1])
        elif a == "--interval":
            interval = float(argv[i + 1])
            i += 1
        elif a.startswith("--interval="):
            interval = float(a.split("=", 1)[1])
        elif a == "--save":
            save = True
            if (
                i + 1 < len(argv)
                and not argv[i + 1].startswith("-")
                and argv[i + 1].lower() not in BLOCK_NAMES
            ):
                label = argv[i + 1]
                i += 1
        elif a.startswith("--save="):
            save, label = True, a.split("=", 1)[1]
        elif a == "--label":
            label = argv[i + 1]
            i += 1
        elif a == "--sweep":
            sweep_spec = ""
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                if RANGE_RE.match(argv[i + 1]):
                    sweep_spec = argv[i + 1]
                    i += 1
        elif a.startswith("--sweep="):
            sweep_spec = a.split("=", 1)[1]
        elif a == "--op":
            single_op = to_int(argv[i + 1])
            i += 1
        elif a == "--size":
            single_size = to_int(argv[i + 1])
            i += 1
        elif a == "--sizes":
            sizes = [to_int(x) for x in argv[i + 1].split(",")]
            i += 1
        elif a == "--sub":
            sub = to_int(argv[i + 1])
            i += 1
        elif a == "--full":
            full = True
        elif a.startswith("-"):
            print(f"unknown option: {a}")
            sys.exit(2)
        else:
            targets.append(a.lower())
        i += 1

    return {
        "targets": targets,
        "save": save,
        "label": label,
        "sweep_spec": sweep_spec,
        "single_op": single_op,
        "single_size": single_size,
        "sub": sub,
        "full": full,
        "sizes": sizes,
        "watch": watch_mode,
        "interval": interval,
        "passive": passive,
        "active": active,
        "manual": manual,
        "app_walk": app_walk,
        "start": start,
    }


def main():
    opts = parse_args(sys.argv[1:])

    jobs = []          # (opcode, size, sub)
    watch_jobs = []    # (opcode, size, sub, name)
    by_name = {nm: (op, sz, s) for op, (nm, sz, s) in KNOWN_BLOCKS.items()}

    if opts["watch"]:
        names = opts["targets"] or list(DEFAULT_TARGETS)
        if "all" in names:
            names = list(DEFAULT_TARGETS)
        for n in names:
            if n in by_name:
                watch_jobs.append((*by_name[n], n))
            else:
                print(f"[-] unknown block '{n}' (known: {', '.join(sorted(BLOCK_NAMES))})")
        if not watch_jobs:
            print("[-] nothing to watch")
            sys.exit(2)
    elif opts["single_op"] is not None:
        known = KNOWN_BLOCKS.get(opts["single_op"])
        base_size = opts["single_size"] or (known[1] if known else SWEEP_SIZE_DEFAULT)
        use_sub = opts["sub"] if opts["sub"] else (known[2] if known else 0)
        for size in opts["sizes"] or [base_size]:
            jobs.append((opts["single_op"], size, use_sub))
    elif opts["sweep_spec"] is None:
        names = opts["targets"] or list(DEFAULT_TARGETS)
        if "all" in names:
            names = list(DEFAULT_TARGETS)
        for n in names:
            if n in by_name:
                jobs.append(by_name[n])
            else:
                print(f"[-] unknown block '{n}' (known: {', '.join(sorted(BLOCK_NAMES))})")

    # Manual mode opens the device per capture, so it must not hold one open here.
    if opts["app_walk"]:
        walk_app_effects(start=opts["start"], prefix=opts["label"] or "app")
        return

    if opts["manual"]:
        watch_manual(watch_jobs, prefix=opts["label"])
        return

    try:
        dev = get_device()
    except Exception as e:
        print(f"[-] Could not open K61SE interface 1: {e}")
        sys.exit(1)

    try:
        if opts["watch"]:
            watch(
                dev,
                watch_jobs,
                interval=opts["interval"],
                prefix=opts["label"],
                active=opts["active"],
            )
        elif opts["passive"]:
            show_passive(dev)
        else:
            for opcode, size, use_sub in jobs:
                read_and_show(
                    dev, opcode, size, sub=use_sub,
                    save=opts["save"], label=opts["label"],
                )
            if opts["sweep_spec"] is not None:
                lo, hi = parse_range(opts["sweep_spec"])
                sweep_ops(
                    dev,
                    lo,
                    hi,
                    opts["single_size"] or SWEEP_SIZE_DEFAULT,
                    sub=opts["sub"],
                    full=opts["full"],
                    save=opts["save"],
                    label=opts["label"],
                )
    finally:
        try:
            dev.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
