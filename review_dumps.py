#!/usr/bin/env python3
"""Review the .bin dumps written by `dump_lighting.py --save [LABEL]`.

Dumps are named  <block>_<timestamp>[_<label>][_<n>].bin  so a labelled save
reads back as e.g. `lighting_20261018_193001_green.bin`.

Usage:
  python3 review_dumps.py                     # list saved dumps
  python3 review_dumps.py -l green            # list only dumps labelled "green"
  python3 review_dumps.py -d                  # diff the two most recent dumps
  python3 review_dumps.py -d red green        # diff by label (like with like)
  python3 review_dumps.py -d lighting:red lighting:green
  python3 review_dumps.py -d 3 5              # diff by list index
  python3 review_dumps.py -d A.bin B.bin      # diff two specific files
  python3 review_dumps.py -s green            # annotated hex dump (label/index/file)
  python3 review_dumps.py -v                  # what VARIES across all dumps
  python3 review_dumps.py -v lighting         # ... just the lighting block
  python3 review_dumps.py -v colour theme_*   # ... just matching labels

`-v` prints a one-line state summary per dump and then lists only the offsets
that actually differ, with their distinct values and example labels. That is the
fast way to scan a batch of theme / colour dumps: if a byte never varies it is
not what a theme controls.

Byte changes are named where the offset is known, and anything inside the
2-byte entry table is shown as a raw byte pair (plus a speculative RGB565
read-out), so a diff reads like:

  A = lighting_20261018_191727.bin  (128 B, lighting, power=ON)
  B = lighting_20261018_191845.bin  (128 B, lighting, power=ON)
  2 changed byte(s):
    entry[ 0] @0x3A  05 57 -> 02 50   (rgb565? #52E229 -> #520010)
"""
import fnmatch
import glob
import os
import re
import sys
import time

import k61se_layout as layout

DUMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dumps")
NAME_RE = re.compile(r"^(?P<block>[A-Za-z0-9]+)_(?P<ts>\d{8}_\d{6})(?P<rest>.*)\.bin$")


def parse_name(path):
    """-> (block, timestamp, label) parsed from the filename."""
    base = os.path.basename(path)
    m = NAME_RE.match(base)
    if not m:
        return os.path.splitext(base)[0], "", ""
    return m.group("block"), m.group("ts"), m.group("rest").lstrip("_")


def find_dumps():
    files = glob.glob(os.path.join(DUMP_DIR, "*.bin"))
    files.sort(key=lambda p: (os.path.getmtime(p), os.path.basename(p)))
    return files


def load(path):
    with open(path, "rb") as f:
        return f.read()


def describe_size(data):
    if len(data) == layout.LIGHTING_SIZE:
        return f"lighting, mode={layout.mode_name(data[layout.MODE_BODY_OFF])}"
    if len(data) == layout.COLOUR_SIZE:
        shown = ", ".join(
            f"m{mode:02X}=#{r:02X}{g:02X}{b:02X}"
            for mode, (r, g, b) in layout.coloured_records(data)
        )
        return f"colour, {shown or 'no coloured records'}"
    return f"{len(data)} bytes"


def resolve_spec(spec):
    """Accept a real path, [block:]index, [block:]label, or a block name."""
    block = None
    if ":" in spec:
        block, spec = spec.split(":", 1)
    if os.path.exists(spec):
        return spec
    files = find_dumps()
    if block:
        files = [f for f in files if parse_name(f)[0] == block]
    if spec.isdigit() and int(spec) < len(files):
        return files[int(spec)]
    by_label = [f for f in files if parse_name(f)[2] == spec]
    if by_label:
        return by_label[-1]
    if not block:
        by_block = [f for f in files if parse_name(f)[0] == spec]
        if by_block:
            return by_block[-1]
    return None


def cmd_list(label_filter=None):
    files = find_dumps()
    if label_filter is not None:
        files = [f for f in files if parse_name(f)[2] == label_filter]
    if not files:
        extra = f" labelled '{label_filter}'" if label_filter else ""
        print(f"no dumps{extra} in {DUMP_DIR}")
        print("capture one with:  sudo python3 dump_lighting.py --save <label>")
        return

    print(f"{'#':>3}  {'file':<40} {'size':>5}  {'label':<10} {'modified':<19}  note")
    for i, path in enumerate(files):
        data = load(path)
        _, _, label = parse_name(path)
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
        print(
            f"{i:>3}  {os.path.basename(path):<40} {len(data):>5}  "
            f"{(label or '-'):<10} {mtime:<19}  {describe_size(data)}"
        )


def describe_change(old, new, off, block="lighting"):
    """One-line description of a byte change, entry-table aware."""
    label = layout.field_name(off, block)
    if layout.in_palette(off):
        woff = layout.palette_word_offset(off)
        ow, nw = layout.word_at(old, woff), layout.word_at(new, woff)
        index = (woff - layout.PALETTE_OFF) // 2
        return (
            f"    entry[{index:>2}] @0x{woff:02X}  "
            f"{ow & 0xFF:02X} {ow >> 8:02X} -> {nw & 0xFF:02X} {nw >> 8:02X}"
            f"   (rgb565? {layout.hexcolour(layout.rgb565(ow))} -> "
            f"{layout.hexcolour(layout.rgb565(nw))})"
        )
    return f"    0x{off:02X}  {label:<32} {old[off]:02X} -> {new[off]:02X}"


def cmd_diff(specs):
    if not specs:
        files = find_dumps()
        if len(files) < 2:
            print("need at least two dumps to diff (save some first)")
            return
        specs = files[-2:]

    paths = []
    for spec in specs:
        path = resolve_spec(spec)
        if path is None:
            print(f"[-] could not resolve '{spec}' (not a file, index, label or block name)")
            return
        paths.append(path)
    if len(paths) != 2:
        print("diff needs exactly two dumps")
        return

    # A label can exist for more than one block (e.g. --save red writes both a
    # lighting and a colour dump). If the two sides resolve to different
    # blocks, prefer the same-block counterpart so we diff like with like.
    if parse_name(paths[0])[0] != parse_name(paths[1])[0]:
        block0, block1 = parse_name(paths[0])[0], parse_name(paths[1])[0]
        same = resolve_spec(f"{block0}:{specs[1]}")
        if same is not None:
            paths[1] = same
        else:
            same = resolve_spec(f"{block1}:{specs[0]}")
            if same is not None:
                paths[0] = same
            else:
                print(
                    f"  [!] {block0} vs {block1} — different blocks;"
                    f" use e.g. -d {block0}:A {block0}:B to compare like with like"
                )

    a_path, b_path = paths
    a, b = load(a_path), load(b_path)
    block = parse_name(a_path)[0]
    print(f"  A = {os.path.basename(a_path)}  ({len(a)} B, {describe_size(a)})")
    print(f"  B = {os.path.basename(b_path)}  ({len(b)} B, {describe_size(b)})")
    if len(a) != len(b):
        print(f"  sizes differ — comparing the first {min(len(a), len(b))} bytes")

    n = min(len(a), len(b))
    changed = [i for i in range(n) if a[i] != b[i]]
    if not changed:
        print("  identical")
        return

    print(f"  {len(changed)} changed byte(s):")
    is_pal = lambda o: block == "lighting" and layout.in_palette(o)
    i = 0
    while i < len(changed):
        off = changed[i]
        if is_pal(off):
            print(describe_change(a, b, off, block))
            woff = layout.palette_word_offset(off)
            i += 1
            while i < len(changed) and changed[i] in (woff, woff + 1):
                i += 1
            continue

        run = [off]
        k = i + 1
        while k < len(changed) and changed[k] == run[-1] + 1 and not is_pal(changed[k]):
            run.append(changed[k])
            k += 1
        old = " ".join(f"{a[o]:02X}" for o in run)
        new = " ".join(f"{b[o]:02X}" for o in run)
        print(f"    0x{off:02X}  {layout.field_name(off, block):<32} {old} -> {new}")
        i = k


def cmd_show(spec):
    path = resolve_spec(spec)
    if path is None:
        print(f"[-] could not resolve '{spec}'")
        return
    data = load(path)
    block, ts, label = parse_name(path)
    print(f"  file  : {os.path.basename(path)}")
    print(f"  block : {block}   label: {label or '-'}   saved: {ts}")
    print(f"  size  : {len(data)} bytes ({describe_size(data)})")
    print(layout.hexdump(data))
    if len(data) == layout.LIGHTING_SIZE:
        p = data[layout.POWER_BODY_OFF]
        print(
            f"  power : 0x{p:02X} ({'ON' if p else 'OFF'})"
            f" at body offset 0x{layout.POWER_BODY_OFF:02X}"
        )
        print(layout.format_palette(data))


# --------------------------------------------------------------------------- #
# -v : what varies across a batch of dumps
# --------------------------------------------------------------------------- #
def label_of(path):
    """Label if the dump has one, else the HHMMSS part of its timestamp."""
    label = parse_name(path)[2]
    if label:
        return label
    m = re.search(r"_(\d{8}_\d{6})", os.path.basename(path))
    return m.group(1)[9:] if m else os.path.basename(path)


def fmt_values(pairs):
    """(value, label) pairs -> 'most common value first' summary string."""
    counts = {}
    for value, label in pairs:
        counts.setdefault(value, []).append(label)
    items = sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    parts = []
    for value, labels in items[:5]:
        ex = ",".join(labels[:2]) + (f",+{len(labels) - 2}" if len(labels) > 2 else "")
        parts.append(f"{value} x{len(labels)} [{ex}]")
    if len(items) > 5:
        parts.append(f"(+{len(items) - 5} more values)")
    return "   ".join(parts)


def cmd_vary(specs):
    want_block, globs = None, []
    for spec in specs:
        if spec in layout.FIELD_SETS:
            want_block = spec
        else:
            globs.append(spec)

    groups = {}
    for path in find_dumps():
        block = parse_name(path)[0]
        if want_block and block != want_block:
            continue
        label = parse_name(path)[2]
        if globs and not any(
            fnmatch.fnmatch(os.path.basename(path), g)
            or fnmatch.fnmatch(label, g)
            for g in globs
        ):
            continue
        groups.setdefault(block, []).append(path)

    if not groups:
        print("no dumps matched")
        return

    for block in sorted(groups):
        paths = groups[block]
        datas = [load(p) for p in paths]
        n = min(len(d) for d in datas)
        print(f"\n======== {block}: {len(paths)} dump(s), {n} bytes compared ========")
        for path, data in zip(paths, datas):
            print(f"  {label_of(path):<28} {layout.state_summary(block, data)}")

        if len(paths) < 2:
            print("  (only one dump of this block — save another to compare)")
            continue

        varied_bytes, varied_words = [], []
        for off in range(n):
            if block == "lighting" and layout.in_palette(off):
                continue
            pairs = [(f"{d[off]:02X}", label_of(p)) for d, p in zip(datas, paths)]
            if len({v for v, _ in pairs}) > 1:
                varied_bytes.append((off, pairs))

        if block == "lighting":
            for i in range(layout.PALETTE_WORDS):
                off = layout.PALETTE_OFF + 2 * i
                if off + 1 >= n:
                    break
                pairs = [
                    (f"{d[off]:02X} {d[off + 1]:02X}", label_of(p))
                    for d, p in zip(datas, paths)
                ]
                if len({v for v, _ in pairs}) > 1:
                    varied_words.append((i, off, pairs))

        if not varied_bytes and not varied_words:
            print(f"\n  compared {n} bytes — every dump is byte-identical.")
            continue

        print(
            f"\n  compared {n} bytes: {len(varied_bytes)} vary outside the entry"
            f" table, {len(varied_words)} entry word(s) vary."
        )
        if varied_bytes:
            print("  varying bytes:")
            for off, pairs in varied_bytes[:40]:
                print(
                    f"    0x{off:02X}  {layout.field_name(off, block):<34} {fmt_values(pairs)}"
                )
            if len(varied_bytes) > 40:
                print(f"    ... and {len(varied_bytes) - 40} more varying byte(s)")
        if varied_words:
            print("  varying entry words (bytes as stored):")
            for i, off, pairs in varied_words[:20]:
                print(f"    entry[{i:>2}] @0x{off:02X}   {fmt_values(pairs)}")
            if len(varied_words) > 20:
                print(f"    ... and {len(varied_words) - 20} more varying word(s)")


def cmd_records(pattern="app_"):
    """Pair up `<label>_rainbow` / `<label>_colour` dumps and decode the records.

    Device-free.  This is the check that produced the per-effect record model in
    PROTOCOL.md §5, and it re-runs on any future app walk saved with --save.
    """
    pairs = {}
    for path in find_dumps():
        block, _ts, label = parse_name(path)
        for suffix in ("rainbow", "colour"):
            if label.endswith("_" + suffix):
                base = label[: -(len(suffix) + 1)]
                if pattern and pattern not in base:
                    continue
                pairs.setdefault(base, {}).setdefault(suffix, {})[block] = path
    if not pairs:
        print(f"no <label>_rainbow / <label>_colour dump pairs matching {pattern!r}")
        return
    print(f"{'effect':34} {'mode':9} {'slot':9} 'written record'  other moved")
    for base in sorted(pairs):
        got = pairs[base]
        if not all(s in got for s in ("rainbow", "colour")):
            print(f"{base:34} incomplete pair")
            continue
        for block in ("lighting", "colour"):
            if block not in got["rainbow"] or block not in got["colour"]:
                print(f"{base:34} missing {block} block")
                break
        else:
            light_a = load(got["rainbow"]["lighting"])
            light_b = load(got["colour"]["lighting"])
            col_a = load(got["rainbow"]["colour"])
            col_b = load(got["colour"]["colour"])
            mode_a = light_a[layout.MODE_BODY_OFF]
            mode_b = light_b[layout.MODE_BODY_OFF]
            slot = layout.effect_slot(mode_b)
            word = (
                "%02X %02X" % (light_b[slot], light_b[slot + 1])
                if slot is not None
                else "-"
            )
            moved = [
                i for i in range(min(len(col_a), len(col_b))) if col_a[i] != col_b[i]
            ]
            record = layout.colour_record_off(mode_b)
            record_hit = record is not None and any(
                record <= i <= record + 2 for i in moved
            )
            where = f"0x{record:02X}" if record_hit else "-"
            if record_hit:
                rgb = layout.colour_record_colour(col_b, mode_b)
                where += " = #%02X%02X%02X" % rgb
                status = "record written"
            elif mode_a != mode_b:
                status = "mode changed"
            else:
                status = "no colour written"
            others = [
                "0x%02X" % i
                for i in moved
                if record is None or not record <= i <= record + 2
            ]
            print(
                f"{base:34} {mode_a:02X}->{mode_b:02X} {word:9} "
                f"{where:16} {', '.join(others) or '-':12} {status}"
            )
    print(
        "\nA 'record written' row is confirmed when the offset is 21*mode (the "
        "effect's\nown green byte, red at +1, blue at +2). Rows with no colour "
        "written are effects\nwhose record the app had already filled, or that "
        "could not be shown."
    )


def main():
    args = sys.argv[1:]
    if not args:
        cmd_list()
    elif args[0] in ("-h", "--help"):
        print(__doc__)
    elif args[0] in ("-l", "--label"):
        cmd_list(args[1] if len(args) > 1 else None)
    elif args[0] in ("-d", "--diff"):
        cmd_diff(args[1:])
    elif args[0] in ("-s", "--show"):
        if len(args) < 2:
            print("usage: review_dumps.py -s FILE|INDEX|LABEL")
            return
        cmd_show(args[1])
    elif args[0] in ("-v", "--vary"):
        cmd_vary(args[1:])
    elif args[0] in ("-r", "--records"):
        cmd_records(args[1] if len(args) > 1 else "app_")
    else:
        cmd_diff(args)


if __name__ == "__main__":
    main()
