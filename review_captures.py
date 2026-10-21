#!/usr/bin/env python3
"""Decode what the vendor app actually wrote, straight out of a capture folder.

`snoop_session.py` stores every captured event as
`captures/<tag>/<tag>_evt<N>_<stage>_<dir>_<size>B.bin`.  The OUT ones are the
app's writes; each write is the same 520-byte feature report our tools send, so
it can be decoded with the shared layer in `k61se_layout.py` and compared with a
dump or with the app's previous write in the same folder.

    python3 review_captures.py                     # summary of every folder
    python3 review_captures.py -d wave_colour      # one folder, event by event
    python3 review_captures.py -d wave_colour -v   # + every byte that moved
    python3 review_captures.py -i                  # list the folders

Read-only: nothing here opens the device.
"""
import argparse
import glob
import os
import re
import sys

import k61se_layout as layout

CAPTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
BLOCK_NAMES = {0x04: "lighting", 0x84: "lighting", 0x0A: "colour", 0x8A: "colour",
               0x08: "music", 0x88: "music",
               0x02: "info", 0x82: "info"}
EVENT_RE = re.compile(r"_evt(\d+)_(submit|callback|error)_(in|out)_(\d+)B\.bin$")


def decode(raw):
    """(op, size, body) for one captured 520-byte report, else None."""
    if len(raw) < 8 or raw[0] != 0x06:
        return None
    op = raw[1]
    size = raw[6] | (raw[7] << 8)
    body = raw[8:8 + size]
    if len(body) != size:
        return None
    return op, size, body


def read_folder(folder):
    """Every decoded event in `folder`, in capture order."""
    events = []
    for path in sorted(glob.glob(os.path.join(folder, "*.bin"))):
        match = EVENT_RE.search(os.path.basename(path))
        if not match:
            continue
        index, stage, direction, _size = match.groups()
        decoded = decode(open(path, "rb").read())
        if decoded is None:
            continue
        op, size, body = decoded
        events.append(
            {
                "evt": int(index),
                "path": path,
                "out": direction == "out",
                "op": op,
                "size": size,
                "body": body,
                "block": BLOCK_NAMES.get(op),
                "write": not op & 0x80,
            }
        )
    events.sort(key=lambda e: (e["evt"], 0 if e["out"] else 1))
    return events


def describe(event):
    """One line describing a decoded event."""
    body, name = event["body"], event["block"]
    if event["out"] and not event["write"]:
        # an OUT report with the 0x80 bit set is the *request*; its payload is
        # just a zero-filled buffer, so do not try to decode it as state
        return f"read request  op=0x{event['op']:02X} size={event['size']}"
    if name == "lighting" and len(body) == layout.LIGHTING_SIZE:
        mode = body[layout.MODE_BODY_OFF]
        slot = layout.effect_slot(mode)
        if slot is None:
            return f"lighting  mode=0x{mode:02X} (no parameter slot)"
        index_of = (slot - layout.PARAMS_OFF) // 2
        return (
            f"lighting  mode=0x{mode:02X} {layout.mode_name(mode):26s} "
            f"slot[{index_of}]={body[slot]:02X} {body[slot + 1]:02X}  "
            f"{layout.slot_summary(body[slot + 1])}"
        )
    if name == "colour" and len(body) == layout.COLOUR_SIZE:
        shown = ", ".join(
            f"m{mode:02X}=#{r:02X}{g:02X}{b:02X}"
            for mode, (r, g, b) in layout.coloured_records(body)
        )
        return (
            f"colour    records {shown or 'none (all black)'}  "
            f"non-zero={sum(1 for b in body if b)}"
        )
    if name == "music" and len(body) == layout.MUSIC_SIZE:
        lit = 0
        peak = 0
        for off in range(0, len(body), layout.MUSIC_CELL):
            cell = body[off:off + layout.MUSIC_CELL]
            if any(cell):
                lit += 1
                peak = max(peak, *cell)
        return f"music     {lit:3d}/{layout.MUSIC_CELLS} cells lit  peak={peak:3d}"
    if name == "info" and len(body) == 6:
        return f"info      {body.hex(' ')}"
    return f"op 0x{event['op']:02X} size={event['size']}"


def diff_against(previous, event, verbose):
    """Bytes that moved since the last write of the same block."""
    if previous is None or len(previous["body"]) != len(event["body"]):
        return []
    moved = [i for i in range(len(event["body"])) if previous["body"][i] != event["body"][i]]
    if not moved:
        return ["(byte-identical to the previous %s write)" % event["block"]]
    if not verbose:
        return ["%d byte(s) moved since the previous %s write" % (len(moved), event["block"])]
    return [
        "   0x%03X  %02X -> %02X  %s"
        % (i, previous["body"][i], event["body"][i], layout.field_name(i, event["block"]))
        for i in moved
    ]


def show_folder(folder, verbose=False, log=print):
    events = read_folder(folder)
    tag = os.path.basename(folder)
    if not events:
        log(f"{tag}: no decoded events")
        return []
    log(f"{tag}  ({len(events)} event(s), {os.path.relpath(folder, CAPTURE_DIR)})")
    previous = {}
    for event in events:
        direction = "OUT" if event["out"] else "IN "
        kind = "write" if event["write"] else "read "
        log(f"   evt{event['evt']:<3d} {direction} {kind} {describe(event)}")
        if event["out"] and event["write"] and event["block"] != "music":
            # music is a stream of full frames; a byte diff every 45 ms is noise
            for line in diff_against(previous.get(event["block"]), event, verbose):
                log(f"        {line}")
            previous[event["block"]] = event
    return events


def folder_list(names):
    if names:
        out = []
        for name in names:
            out.append(name if os.path.isdir(name) else os.path.join(CAPTURE_DIR, name))
        return out
    return sorted(d for d in glob.glob(os.path.join(CAPTURE_DIR, "*")) if os.path.isdir(d))


def summary(folders, log=print):
    """One row per folder: the last write of each block."""
    log(f"{'tag':26s} {'lighting write':58s} colour write")
    for folder in folders:
        last = {}
        for event in read_folder(folder):
            if event["out"] and event["write"]:
                last[event["block"]] = event
        light = describe(last["lighting"]) if "lighting" in last else "-"
        colour = describe(last["colour"]) if "colour" in last else "-"
        log(f"{os.path.basename(folder):26s} {light:58s} {colour}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-d", "--dir", action="append", metavar="TAG",
                        help="capture folder (repeatable); default: every folder")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list every byte that moved between writes")
    parser.add_argument("--summary", action="store_true",
                        help="one line per folder instead of event-by-event")
    parser.add_argument("-i", "--list", action="store_true", help="list folders and exit")
    args = parser.parse_args(argv)

    folders = folder_list(args.dir)
    if args.list:
        for folder in folders:
            log_line = f"{os.path.basename(folder):28s} {len(read_folder(folder))} event(s)"
            print(log_line)
        return 0
    if not folders:
        print(f"no capture folders under {CAPTURE_DIR}", file=sys.stderr)
        return 1
    if args.summary:
        summary(folders)
        return 0
    for folder in folders:
        show_folder(folder, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
