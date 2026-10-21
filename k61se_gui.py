#!/usr/bin/env python3
"""K61SE test bench: a small GUI for poking effects, brightness, speed and
colour at the keyboard, plus a headless mode so the same presets can be driven
from a script.

    python3 k61se_gui.py                     # the GUI
    python3 k61se_gui.py --read              # print the current state
    python3 k61se_gui.py --list              # list presets
    python3 k61se_gui.py --apply presets/*.json
    python3 k61se_gui.py --matrix            # every effect x every colour
    python3 k61se_gui.py --effect-colour 0x0A red   # one effect's own record
    python3 k61se_gui.py --audit             # both renderings of every effect
    python3 k61se_gui.py --make-presets      # (re)write the preset files
    python3 k61se_gui.py --selftest          # device-free checks

No sudo needed: a uaccess ACL already gives the desktop user read-write on the
K61SE hidraw nodes.  Close the vendor app before applying anything, or the two
writers will fight over report 6.

Read-modify-write only: the lighting block is 128 bytes and mostly undecoded, so
we always read it, change one or two bytes, and write it back.  Every apply is
read back afterwards.
"""
import argparse
import os
import sys
import time

import k61se_dev as dev
import k61se_layout as layout

HOTKEY_HINT = (
    "If an apply lands but the panel stays dark, the keyboard's own on/off "
    "hotkey has the backlight disabled — that flag lives in firmware and no "
    "software write can clear it.  Tap the hotkey once, then apply again."
)


def hex_of(r, g, b):
    """(r, g, b) -> '#RRGGBB'."""
    return "#%02X%02X%02X" % (r & 0xFF, g & 0xFF, b & 0xFF)


def parse_hex(text):
    """'#RRGGBB', 'RRGGBB' or CSS shorthand 'RGB' -> (r, g, b); None if invalid."""
    text = (text or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        return None
    try:
        value = int(text, 16)
    except ValueError:
        return None
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def effect_choices():
    """[(label, mode)] for the combobox, in mode order."""
    choices = []
    for mode in sorted(layout.MODES):
        name = layout.mode_name(mode)
        label = f"0x{mode:02X}  {name}"
        if mode in (0x06, 0x0E):
            label += "   (draws nothing)"
        choices.append((label, mode))
    return choices


def log_report(log, report, label=""):
    """Print a set_state() report through `log(str)`."""
    if label:
        log(f"--- {label}")
    for line in report["changes"] or ["nothing changed"]:
        log(f"  {line}")
    for line in report["notes"]:
        log(f"  note: {line}")
    verify = report["verify"]
    bits = [f"{k}={'ok' if v else 'NO'}" for k, v in verify.items() if v is not None]
    if bits:
        log(f"  read-back: {', '.join(bits)}")
    if report["lighting"] is not None:
        log(f"  lighting: {layout.state_summary('lighting', report['lighting'])}")
    if report["colour"] is not None:
        log(f"  colour:   {layout.state_summary('colour', report['colour'])}")


def warn_other_writers(log):
    others = dev.other_writers()
    if others:
        pids = ", ".join(f"{name} (pid {pid})" for pid, name in others)
        log(f"WARNING: another process has the keyboard open: {pids}")
        log("         the vendor app must be closed for writes to stick.")
    return others


def current_state():
    """read_state() with a friendly error instead of a traceback."""
    try:
        return dev.read_state(), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


# --------------------------------------------------------------------------- #
# presets
# --------------------------------------------------------------------------- #
def make_presets(directory=dev.PRESET_DIR, log=print):
    """Write one preset per effect plus a set of colour presets."""
    written = []
    for mode in sorted(layout.MODES):
        slug = layout.MODE_SLUGS.get(mode, f"{mode:02x}unk")
        cfg = {
            "name": f"effect {layout.mode_name(mode)}",
            "effect": mode,
            "brightness": 5,
            "speed": 5,
            # Every effect was captured with the rainbow flag ON, so that is the
            # form a bare effect preset reproduces.  Colour presets switch it off
            # again, and the test bench's Rainbow tick flips it by hand.
            "rainbow": True,
            "colour": None,
            "notes": "effect 0x%02X, brightness 5, speed 5, rainbow on (the form "
            "the sweep captured); leaves the colour block alone" % mode,
        }
        path = os.path.join(directory, f"{mode:02x}_{slug}.json")
        written.append(dev.save_config(path, cfg))
        log(f"wrote {path}")

    colours = {
        "red": (255, 0, 0),
        "green": (0, 255, 0),
        "blue": (0, 0, 255),
        "white": (255, 255, 255),
        "amber": (255, 128, 0),
        "cyan": (0, 255, 255),
        "magenta": (255, 0, 255),
        "half_red": (128, 0, 0),
        "half_green": (0, 128, 0),
        "half_blue": (0, 0, 128),
        "quarter_white": (64, 64, 64),
        "off_black": (0, 0, 0),
    }
    for name, rgb in colours.items():
        cfg = {
            "name": f"colour {name}",
            "effect": None,
            "brightness": None,
            "speed": None,
            # Leave the flag alone.  Every effect draws its own record with the
            # flag off now, but a colour preset has no effect to pair with, and
            # clearing the flag on an effect whose record is still black would
            # blank the panel — so a colour preset must not do it.
            "rainbow": None,
            "colour": list(rgb),
            "notes": "colour only; written into the active effect's own record, so"
            " it shows immediately if that effect is not in rainbow mode",
        }
        path = os.path.join(directory, f"colour_{name}.json")
        written.append(dev.save_config(path, cfg))
        log(f"wrote {path}")

    # Combined presets: effect *and* colour, i.e. a saved "theme".  Every effect
    # draws its own record with the flag off (confirmed 2026-10-19), except the
    # three that ignore the flag — so a red theme exists for each of the rest.
    # The matrix runner ignores these (it wants effect-only and colour-only
    # presets).
    themes = {"solid_rainbow": (0x01, True, None)}
    for mode in sorted(layout.SINGLE_COLOUR_MODES):
        slug = layout.MODE_SLUGS.get(mode, f"{mode:02x}unk")
        themes[f"{slug}_red"] = (mode, False, (255, 0, 0))
    for name, (mode, rainbow, rgb) in sorted(themes.items()):
        cfg = {
            "name": f"theme {name}",
            "effect": mode,
            "brightness": 5,
            "speed": 5,
            "rainbow": rainbow,
            "colour": list(rgb) if rgb else None,
            "notes": (
                "effect 0x%02X + %s" % (
                    mode,
                    "the firmware's own rainbow (its colour record is ignored)"
                    if rainbow else f"single colour {rgb}, written into that effect's own "
                    "record (0x%02X)" % layout.colour_record_off(mode),
                )
            ),
        }
        path = os.path.join(directory, f"theme_{name}.json")
        written.append(dev.save_config(path, cfg))
        log(f"wrote {path}")

    log(f"\n{len(written)} presets in {directory}")
    return written


def apply_paths(paths, log=print):
    """Apply preset files in order. Returns the number that failed."""
    failures = 0
    for path in paths:
        try:
            cfg = dev.load_config(path)
        except Exception as exc:  # noqa: BLE001
            log(f"{os.path.basename(path)}: unusable preset: {exc}")
            failures += 1
            continue
        try:
            report = dev.apply_config(cfg)
        except Exception as exc:  # noqa: BLE001
            log(f"{os.path.basename(path)}: {exc}")
            failures += 1
            continue
        log_report(log, report, label=cfg["name"] or os.path.basename(path))
    return failures


def apply_matrix(directory, delay, log=print, effects=None, colours=None):
    """Every effect in its rainbow rendering, plus every colour for every effect
    that draws its own colour record.  Ctrl-C stops it."""
    all_presets = dev.list_presets(directory)
    if effects is None:
        # Effect-only presets; a combined `theme_*` with rainbow on is not a
        # separate effect (theme_solid_rainbow duplicates 01_solid).
        effects = [
            cfg for path, cfg in all_presets
            if cfg and cfg["effect"] is not None and cfg["colour"] is None
            and not os.path.basename(path).startswith("theme_")
        ]
    if colours is None:
        colours = [
            cfg for _, cfg in all_presets if cfg and cfg["colour"] is not None and cfg["effect"] is None
        ]
    # Every effect except the three that ignore the flag draws its own colour
    # record now (confirmed 2026-10-19), so each of them gets the full colour set.
    work = []
    for cfg in effects:
        work.append((cfg, None, True))
        if layout.supports_single_colour(cfg["effect"]):
            for colour_cfg in colours:
                work.append((cfg, colour_cfg, False))
    total = len(work)
    colours_per_effect = len(colours)
    log(f"matrix: {len(effects)} effects in rainbow + "
        f"{sum(1 for _, c, _ in work if c)} colour writes = {total} writes, {delay}s apart"
        f"  ({colours_per_effect} colours each, skipping 0x03/0x0F/0x10)")
    log("Ctrl-C to stop")
    done = 0
    try:
        for cfg, colour_cfg, rainbow in work:
            done += 1
            log(
                f"[{done}/{total}] effect 0x{cfg['effect']:02X} "
                f"({layout.mode_name(cfg['effect'])}) + "
                + ("rainbow" if rainbow else f"colour {colour_cfg['colour']}")
            )
            dev.set_state(
                effect=cfg["effect"],
                brightness=cfg["brightness"],
                speed=cfg["speed"],
                rainbow=rainbow,
                colour=colour_cfg["colour"] if colour_cfg else None,
            )
            time.sleep(delay)
    except KeyboardInterrupt:
        log(f"stopped after {done} of {total}")
        return 130
    log("matrix finished")
    return 0


AUDIT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colour_mode_audit.md")
NIBBLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nibble_probe.md")


def set_effect_colour(mode_text, colour_text, log=print):
    """Paint one effect's **own** colour record and switch it to single colour.

    This is the test that confirmed the layout in PROTOCOL.md §5 on 2026-10-19:
    the colour block holds one 21-byte record per effect, so 0x15/0x16/0x17 (what
    every earlier test wrote) is solid's record only.  Run against the twelve
    colour-capable effects it turned each of them the requested colour; the three
    that ignore the flag stayed colourful, which is the control case.
    """
    try:
        mode = int(str(mode_text), 0) & 0xFF
    except ValueError:
        log(f"not a mode: {mode_text!r}")
        return 2
    try:
        rgb = layout.parse_colour(colour_text)
    except ValueError as exc:
        log(str(exc))
        return 2
    slot = layout.effect_slot(mode)
    if slot is None:
        log(f"mode 0x{mode:02X} ({layout.mode_name(mode)}) has no parameter slot")
        return 2
    warn_other_writers(log)
    record = layout.colour_record_off(mode)
    log(
        f"setting 0x{mode:02X} ({layout.mode_name(mode)}) to single colour "
        f"rgb{rgb}  — its record lives at colour-block 0x{record:02X}"
    )
    try:
        report = dev.set_state(effect=mode, rainbow=False, colour=rgb)
    except Exception as exc:  # noqa: BLE001
        log(f"failed: {exc}")
        return 1
    log_report(log, report, f"effect 0x{mode:02X}")
    if layout.flag_is_ignored(mode):
        log(
            f"note: 0x{mode:02X} is known to ignore the flag — expect no change "
            "(that is the control case)"
        )
    else:
        log("watch the panel: colour = the record is drawn, dark = not")
    return 0


def ask_panel_state(ask, log, question):
    """Ask what the panel is doing: y -> True, n -> False, d -> 'dark'."""
    while True:
        answer = ask(f"    {question} [y/n/d] ").strip().lower()
        first = answer[:1]
        if first == "y":
            return True
        if first == "n" or first == "":
            return False
        if first == "d":
            return "dark"
        log("    (please answer y, n or d)")


def probe_nibbles(mode, log=print, ask=input, out_path=NIBBLE_PATH):
    """Write one effect's parameter byte with every low nibble `0`–`7`.

    Every effect we have decoded uses only `0` (draw the colour block) and `7`
    (the firmware's own rainbow).  If `1`–`6` turn out to be other built-in
    schemes, that is where the 3 effects that ignore the flag (0x03/0x0F/0x10)
    would hide the colour support — and it may also explain the two never-seen
    modes 0x06/0x0E.
    """
    mode = int(mode) & 0xFF
    slot = layout.effect_slot(mode)
    if slot is None:
        log(f"mode 0x{mode:02X} has no parameter slot — nothing to probe")
        return 2
    index_of = (slot - layout.PARAMS_OFF) // 2
    log(f"probing effect 0x{mode:02X} ({layout.mode_name(mode)}), slot[{index_of}]")
    log("answering for each nibble: y = lit/coloured, n = lit but wrong,"
        " d = dark/blank\n")
    rows = []
    light = None
    try:
        light = dev.read_block("lighting")
        light[layout.MODE_BODY_OFF] = mode
        for nibble in range(8):
            light[slot + 1] = layout.speed_byte(5, low=nibble)
            try:
                dev.write_block("lighting", light)
                back = dev.read_block("lighting")
            except OSError as exc:
                log(f"  ! device error at 0x5{nibble:X}: {exc} — stopping")
                break
            stored = back[slot + 1]
            note = "" if stored == light[slot + 1] else f"  (read back 0x{stored:02X})"
            log(f"  0x{nibble:X} -> wrote slot[{index_of}]="
                f"{back[slot]:02X} {light[slot + 1]:02X}{note}")
            state = ask_panel_state(ask, log, "what did the panel do?")
            rows.append((nibble, state))
    finally:
        try:  # always leave the rainbow rendering behind
            if light is not None:
                light[slot + 1] = layout.speed_byte(5, rainbow=True)
                dev.write_block("lighting", light)
                log(f"restored slot[{index_of}]="
                    f"{light[slot]:02X} {light[slot + 1]:02X} (rainbow)")
        except Exception as exc:  # noqa: BLE001
            log(f"could not restore: {exc}")

    if not rows:
        log("no answers recorded — nothing written")
        return 1

    def word(value):
        return "dark" if value == "dark" else ("yes" if value else "no")

    lines = [
        "# K61SE parameter-nibble probe",
        "",
        f"Run {time.strftime('%Y-%m-%d %H:%M')} via "
        f"`python3 k61se_gui.py --probe-nibbles 0x{mode:02X}`.",
        "",
        f"Effect `0x{mode:02X}` ({layout.mode_name(mode)}), parameter slot "
        f"`entry[{mode - 1}]`. Speed left at 5; only the low nibble moved.",
        "",
        "| high byte | low nibble | panel |",
        "|---|---|---|",
    ]
    for nibble, state in rows:
        lines.append(f"| `0x5{nibble:X}` | {nibble} | {word(state)} |")
    lines += [
        "",
        "`0` = draw the colour block, `7` = the firmware's own rainbow. Anything",
        "interesting in `1`–`6` would be a built-in scheme we have not seen.",
        "",
    ]
    try:
        with open(out_path, "w") as handle_out:
            handle_out.write("\n".join(lines))
        log(f"wrote {out_path}")
    except OSError as exc:  # noqa: BLE001
        log(f"could not write {out_path}: {exc}")
    lit = [n for n, s in rows if s is True]
    log(f"summary: lit/coloured at nibbles {lit if lit else 'none'}")
    return 0


def audit_colour_modes(log=print, ask=input, out_path=AUDIT_PATH, start=None):
    """Walk every effect in both renderings and record what the panel did.

    Only the panel can answer this — the block looks the same either way — so
    this writes a state, asks you to look, and records the answer.  Keep the
    vendor app closed while it runs.

    Answers are `y` (yes), `n` (no) or `d` (dark/blank), because a flag-off write
    can *extinguish* an effect rather than colour it.

    **Corrected 2026-10-19:** the first run of this audit wrote `0x15/0x16/0x17`
    for every effect, which turned out to be *solid's* record, so its "dark"
    verdicts are retracted (see `colour_records.md`).  `set_state()` now writes
    the active effect's own record.  The targeted `--effect-colour MODE red` run
    confirmed the model on the panel, and the corrected **bulk run the same day
    reproduced it exactly: 12 of 15 effects rendered both ways, 3 were
    flag-ignored, 0 went dark.**
    """
    def ask_state(question):
        return ask_panel_state(ask, log, question)

    def word(value):
        if value is None:
            return "—"
        return "dark" if value == "dark" else ("yes" if value else "no")

    def writers_hint():
        try:
            others = dev.other_writers()
        except Exception:  # noqa: BLE001
            others = []
        if others:
            for pid, cmd in others:
                log(f"      another process holds the K61SE: pid {pid} {cmd}")
            log("      close the vendor app, then re-run this audit")
        else:
            log("      nothing else holds the device; if it repeats, replug the")
            log("      keyboard (its hidraw node may move) and run --read")

    def write_state(**kwargs):
        """set_state with one retry; returns (report, error)."""
        error = None
        for attempt in (1, 2):
            try:
                return dev.set_state(**kwargs), None
            except OSError as exc:
                error = exc
                log(f"    ! device error: {exc}")
                if attempt == 1:
                    log("      retrying once …")
                    time.sleep(0.4)
        return None, error

    modes = [m for m in sorted(layout.MODES) if layout.effect_slot(m) is not None]
    if start is not None:
        modes = [m for m in modes if m >= start]
        log(f"resuming at effect 0x{start:02X} ({len(modes)} left)")
    log("colour-mode audit: every effect, first colourful then single-colour red.")
    log("Each effect gets its OWN colour record now.  Led by the 2026-10-19")
    log("--effect-colour run, every effect should turn red except 0x03, 0x0F and")
    log("0x10 (which ignore the flag) — answer honestly either way.")
    log("Watch the keyboard and answer y (yes), n (no) or d (dark/blank).")
    log("Ctrl-C to stop early.\n")
    rows = []
    last = None
    try:
        for index, mode in enumerate(modes, 1):
            slot = layout.effect_slot(mode)
            name = layout.mode_name(mode)
            index_of = (slot - layout.PARAMS_OFF) // 2
            last = mode
            log(f"{index}/{len(modes)}  effect 0x{mode:02X}  {name}")

            report, error = write_state(
                effect=mode, brightness=5, speed=5, rainbow=True
            )
            if error is not None:
                log("    ! could not write the rainbow state — stopping here")
                writers_hint()
                rows.append((mode, name, None, None, "write failed (device)"))
                break
            written = report.get("lighting")
            if written is not None:
                log("    wrote slot[%d]=%02X %02X (rainbow)"
                    % (index_of, written[slot], written[slot + 1]))
            colourful = ask_state("colourful — several colours at once?")

            report, error = write_state(
                effect=mode, brightness=5, speed=5, rainbow=False, colour=(255, 0, 0)
            )
            if error is not None:
                log("    ! could not write the single-colour state — stopping here")
                writers_hint()
                rows.append((mode, name, colourful, None, "write failed (device)"))
                break
            written = report.get("lighting")
            record = layout.colour_record_off(mode)
            if written is not None:
                log("    wrote slot[%d]=%02X %02X + red into its own record at 0x%02X"
                    % (index_of, written[slot], written[slot + 1], record))
            single = ask_state("single colour — every key the same red?")

            if colourful is True and single is True:
                verdict = "both renderings"
            elif colourful == "dark":
                verdict = "rainbow draws nothing — mode has no renderer?"
            elif single == "dark":
                verdict = "flag off draws NOTHING even with its own record set"
            elif single is False:
                verdict = "flag ignored — still colourful"
            elif colourful is False:
                verdict = "flag ignored — always single colour"
            else:
                verdict = "unclear — note it by hand"
            rows.append((mode, name, colourful, single, verdict))
            log(f"    -> {verdict}\n")
    except KeyboardInterrupt:
        log("\nstopped early\n")
    finally:
        if last is not None:
            # never leave the panel dark: reinstate the rainbow rendering
            _, error = write_state(effect=last, brightness=5, speed=5, rainbow=True)
            if error is None:
                log(f"restored effect 0x{last:02X} in rainbow mode")
            else:
                log("could not restore the panel — re-apply a preset by hand "
                    "(e.g. presets/theme_solid_rainbow.json)")

    both = [r for r in rows if r[2] is True and r[3] is True]
    dark = [r for r in rows if r[3] == "dark"]
    failed = [r for r in rows if r[4].startswith("write failed")]
    lines = [
        "# K61SE colour-mode audit",
        "",
        f"Run {time.strftime('%Y-%m-%d %H:%M')} via `python3 k61se_gui.py --audit"
        + (f" --audit-start 0x{start:02X}" if start is not None else "")
        + "`.",
        "",
        "Each effect was written twice: rainbow flag on (`entry[mode-1]+1` low",
        "nibble `7`), then flag off plus colour red `(255,0,0)` (`0x50`). The",
        "answers below are what the panel actually did; `dark` means the panel",
        "went blank, i.e. the firmware drew nothing at all for that write.",
        "",
        "Supersedes the first run of 2026-10-19, which wrote solid's record for",
        "every effect; that run's nine \"dark\" verdicts are retracted (PROTOCOL.md",
        "§3, `colour_records.md`). The panel is the only instrument here.",
        "",
    ]
    if start is not None:
        lines += [
            f"Started at `0x{start:02X}` (`--audit-start`), so effects before it "
            "are not in this report.",
            "",
        ]
    lines += [
        "| mode | effect | colourful | single colour | verdict |",
        "|---|---|---|---|---|",
    ]
    for mode, name, colourful, single, verdict in rows:
        lines.append(
            f"| `0x{mode:02X}` | {name} | {word(colourful)} "
            f"| {word(single)} | {verdict} |"
        )
    lines += [
        "",
        f"**{len(both)} of {len(rows)} effects rendered both ways**; "
        f"{len(dark)} went dark when the flag was cleared.",
        "",
    ]
    if failed:
        lines += [
            f"**Stopped early:** {len(failed)} effect(s) could not be written "
            f"(`0x{failed[0][0]:02X}`). The device read back an empty/short "
            "report — usually the vendor app holding report 6, or a hidraw node "
            "that moved. Re-run after `--read` succeeds.",
            "",
        ]
    lines += [
        "A dark result at `0x50` would mean the firmware had nothing to draw: the",
        "mode byte stays valid and reads back fine, the panel simply had no lit",
        "pixel (§6).  With each effect's own record holding red, `dark` should not",
        "appear at all now; `no` is the flag-ignored case (§3).",
        "",
    ]
    try:
        with open(out_path, "w") as handle:
            handle.write("\n".join(lines))
        log(f"wrote {out_path}")
    except OSError as exc:  # noqa: BLE001
        log(f"could not write {out_path}: {exc}")
    log(f"summary: {len(both)} of {len(rows)} rendered both ways, {len(dark)} dark"
        + (f", {len(failed)} write failure(s)" if failed else ""))
    return 1 if failed else 0


def selftest(directory=dev.PRESET_DIR):
    """Device-free sanity checks. Returns 0 if everything passed, else 1."""
    failures = []

    def check(name, ok, detail=""):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail and not ok else ''}")
        if not ok:
            failures.append(name)

    print("frames (expected values are the vendor app's own captured writes):")
    expected = {
        (0x84, 128): "0684000001008000",
        (0x04, 128): "0604000001008000",
        (0x8A, 420): "068a00000100a401",
        (0x0A, 420): "060a00000100a401",
    }
    for (op, size), want in expected.items():
        got = dev._frame(op, size).hex()
        check(f"op 0x{op:02X} size {size}", got == want, f"got {got}")
    check("block table", dev.BLOCKS["lighting"] == (0x04, 128) and dev.BLOCKS["colour"] == (0x0A, 420))

    print("speed scale:")
    for level in range(1, layout.SPEED_MAX + 1):
        byte = layout.speed_byte(level)
        check(f"level {level} <-> 0x{byte:02X}", layout.speed_level(byte) == level)
    check("clamps above", layout.speed_byte(99) == (layout.SPEED_MAX << 4))
    check("clamps below", layout.speed_byte(-1) == (1 << 4))
    check("echoes an unknown nibble", layout.speed_byte(3, low=0x3) == 0x33)
    check("observed names", layout.speed_name(0x17).startswith("slow")
          and layout.speed_name(0x37).startswith("middle")
          and layout.speed_name(0x57).startswith("fast"))
    check("all five rungs are named and distinct",
          len({layout.speed_name(0x10 * n) for n in range(1, 6)}) == 5)
    check("level 2 and 4 read as slow/fast",
          layout.speed_name(0x27).startswith("slow")
          and layout.speed_name(0x47).startswith("fast"))

    print("rainbow flag (the other nibble of the same byte):")
    check("rainbow encodes as 7", layout.speed_byte(5, rainbow=True) == 0x57)
    check("single encodes as 0", layout.speed_byte(5, rainbow=False) == 0x50)
    check("rainbow_flag", layout.rainbow_flag(0x57) and not layout.rainbow_flag(0x50))
    check("set_rainbow on", layout.set_rainbow(0x50, True) == 0x57)
    check("set_rainbow off", layout.set_rainbow(0x57, False) == 0x50)
    check("keeps the speed level", layout.set_rainbow(0x17, False) == 0x10)

    print("decode checks against the dumps (the evidence for the flag):")
    pairs = [
        ("typing feedback 0x04", "dumps/lighting_20261018_193755_typing_feedback_rainbow.bin",
         "dumps/lighting_20261018_193806_typing_feedback_red.bin"),
        ("solid 0x01", "dumps/lighting_20261019_113902_default_solid_rainbow.bin",
         "dumps/lighting_20261019_113914_solid_red_max_bright.bin"),
        ("solid 0x01 app dump", "dumps/lighting_20261019_124606_solid_rainbow_app.bin",
         "dumps/lighting_20261019_113914_solid_red_max_bright.bin"),
    ]
    if not os.path.exists(pairs[0][1]):
        print("  SKIP  dumps/ not present")
    for name, rainbow_path, single_path in pairs:
        if not (os.path.exists(rainbow_path) and os.path.exists(single_path)):
            print(f"  SKIP  {name} (dump missing)")
            continue
        a = open(rainbow_path, "rb").read()
        b = open(single_path, "rb").read()
        slot = layout.effect_slot(a[layout.MODE_BODY_OFF])
        check(f"{name}: rainbow nibble", layout.rainbow_flag(a[slot + 1]))
        check(f"{name}: single nibble", not layout.rainbow_flag(b[slot + 1]))
        if name.endswith("app dump"):
            same = [i for i in range(128)
                    if a[i] != b[i] and i not in (0x02,) and not 0x3A <= i <= 0x7D]
            check(f"{name}: differs only by settings+flags", not same, str(same[:6]))
        else:
            diff = [i for i in range(128) if a[i] != b[i]]
            check(f"{name}: one byte differs", diff == [slot + 1], str(diff))

    print("read_body retries a transient short feature report:")

    class FlakyHid:
        def __init__(self, replies):
            self.replies = list(replies)
            self.sent = 0

        def send_feature_report(self, data):
            self.sent += 1
            return len(data)

        def get_feature_report(self, report_id, length):
            return self.replies.pop(0) if self.replies else b""

        def close(self):
            pass

    raw = (
        bytes([dev.REPORT_ID, 0x84, 0x00, 0x00, 0x01, 0x00, 0x80, 0x00])
        + bytes([0x42]) * layout.LIGHTING_SIZE
    )
    flaky = FlakyHid([b"", raw])
    got = dev.read_body(0x04, layout.LIGHTING_SIZE, dev=flaky)
    check("empty report then a good one", len(got) == layout.LIGHTING_SIZE
          and got[0] == 0x42 and flaky.sent == 2, f"{len(got)} bytes, {flaky.sent} tries")

    flaky = FlakyHid([b"", raw[:40], raw])
    got = dev.read_body(0x04, layout.LIGHTING_SIZE, dev=flaky)
    check("short reports are retried too", len(got) == layout.LIGHTING_SIZE
          and flaky.sent == 3, f"{flaky.sent} tries")

    try:
        dev.read_body(0x04, layout.LIGHTING_SIZE, dev=FlakyHid([b""]), attempts=2)
        ok, detail = False, "no error raised"
    except OSError as exc:
        ok, detail = "empty reply" in str(exc), str(exc)
    check("a persistent empty reply names the cause", ok, detail)

    class DeadHid:
        def send_feature_report(self, data):
            return -1

        def error(self):
            return "stale handle"

        def get_feature_report(self, *a):
            return b""

        def close(self):
            pass

    good = FlakyHid([raw])
    handles = [DeadHid(), good]
    real_open = dev.open_device
    dev.open_device = lambda: handles.pop(0)
    try:
        got = dev.read_body(0x04, layout.LIGHTING_SIZE)
    finally:
        dev.open_device = real_open
    check("a moved hidraw node is reopened and retried",
          len(got) == layout.LIGHTING_SIZE and good.sent == 1,
          f"{len(got)} bytes, {good.sent} good tries")

    print("set_state against an in-memory device (no hardware):")

    class FakeDevice:
        def __init__(self, lighting, colour):
            self.blocks = {"lighting": bytearray(lighting), "colour": bytearray(colour)}
            self.writes = []

        def close(self):
            pass

    real_read, real_write = dev.read_block, dev.write_block
    dev.read_block = lambda name, dev=None: bytearray(dev.blocks[name])

    def fake_write(name, body, dev=None):
        dev.writes.append(name)
        dev.blocks[name] = bytearray(body)
        return len(body)

    dev.write_block = fake_write
    try:
        lighting = bytearray(layout.LIGHTING_SIZE)
        lighting[layout.MODE_BODY_OFF] = 0x01                    # solid
        lighting[layout.PARAMS_OFF] = 5                          # brightness 5
        lighting[layout.PARAMS_OFF + 1] = layout.speed_byte(5, rainbow=False)
        colour = bytearray(layout.COLOUR_SIZE)
        colour[dev.COLOUR_OFF["red"]] = 255
        fake = FakeDevice(lighting, colour)

        report = dev.set_state(effect=0x01, rainbow=True, dev=fake)
        check("rainbow on sets the nibble",
              fake.blocks["lighting"][layout.PARAMS_OFF + 1] == 0x57,
              f"got {fake.blocks['lighting'][layout.PARAMS_OFF + 1]:02X}")
        check("rainbow on reports the change", bool(report["changes"]))
        check("rainbow read-back ok", report["verify"]["lighting"] is True)

        fake.writes.clear()
        report = dev.set_state(effect=0x01, rainbow=True, dev=fake)
        check("re-asserting rainbow writes nothing", not fake.writes)
        check("re-asserting rainbow says so", any("already" in n for n in report["notes"]))

        dev.set_state(effect=0x01, speed=3, dev=fake)
        check("speed keeps the rainbow nibble",
              fake.blocks["lighting"][layout.PARAMS_OFF + 1] == 0x37,
              f"got {fake.blocks['lighting'][layout.PARAMS_OFF + 1]:02X}")

        dev.set_state(effect=0x01, rainbow=False, dev=fake)
        check("rainbow off clears the nibble",
              fake.blocks["lighting"][layout.PARAMS_OFF + 1] == 0x30,
              f"got {fake.blocks['lighting'][layout.PARAMS_OFF + 1]:02X}")

        dev.set_state(effect=0x01, rainbow=True, dev=fake)
        report = dev.set_state(effect=0x01, colour=(0, 0, 255), dev=fake)
        check("colour written", fake.blocks["colour"][dev.COLOUR_OFF["blue"]] == 255)
        check("warns that rainbow ignores the colour",
              any("rainbow mode" in n for n in report["notes"]))

        dev.set_state(effect=0x01, rainbow=False, dev=fake)
        report = dev.set_state(effect=0x01, colour=(0, 255, 0), dev=fake)
        check("no ignore-warning once rainbow is off",
              not any("rainbow mode" in n for n in report["notes"]))

        report = dev.set_state(effect=0x00, brightness=5, speed=5, rainbow=True, dev=fake)
        check("mode 0x00 has no slot, so it is skipped",
              any("no parameter slot" in n for n in report["notes"]))

        report = dev.set_state(effect=0x0F, rainbow=False, dev=fake)
        check("flag-ignored mode says the flag does nothing",
              any("no effect on mode 0x0F" in n for n in report["notes"]),
              str(report["notes"]))
        report = dev.set_state(effect=0x0B, rainbow=False, dev=fake)
        check("a colour-capable effect says the flag draws its own record",
              any("draw its own colour" in n for n in report["notes"]),
              str(report["notes"]))
        report = dev.set_state(effect=0x0B, colour=(255, 0, 0), dev=fake)
        check("and that record is what the flag-off rendering uses",
              dev.colour_of(fake.blocks["colour"], 0x0B) == (255, 0, 0),
              str(dev.colour_of(fake.blocks["colour"], 0x0B)))
        report = dev.set_state(effect=0x0B, colour=(0, 0, 0), dev=fake)
        check("a black record on the active effect warns that the panel is dark",
              any("will be dark" in n for n in report["notes"]),
              str(report["notes"]))

        # per-effect colour records: the heart of the 2026-10-19 finding
        offs = layout.colour_record_offs(0x0A)
        check("record offset matches the app's dump", offs["red"] == 0xD3,
              f"0x{offs['red']:02X}")
        check("effect 1 record is the old 0x15/0x16/0x17",
              layout.colour_record_offs(1) == {"green": 0x15, "red": 0x16, "blue": 0x17})
        check("0x2B/0x55 are records 2 and 4, not flags",
              layout.colour_record_offs(2)["red"] == 0x2B
              and layout.colour_record_offs(4)["red"] == 0x55)
        solid_before = bytes(fake.blocks["colour"])
        dev.set_state(effect=0x0A, colour=(255, 0, 0), dev=fake)
        check("colour lands in the active effect's own record",
              fake.blocks["colour"][offs["red"]] == 255
              and fake.blocks["colour"][offs["green"]] == 0
              and fake.blocks["colour"][offs["blue"]] == 0,
              f"got {dev.colour_of(fake.blocks['colour'], 0x0A)}")
        check("and leaves every other effect's record alone",
              fake.blocks["colour"][:offs["green"]] == solid_before[:offs["green"]]
              and fake.blocks["colour"][offs["blue"] + 1:] == solid_before[offs["blue"] + 1:])
        dev.set_state(effect=0x0A, colour=(0, 255, 0), colour_mode=1, dev=fake)
        check("colour_mode overrides which record is painted",
              dev.colour_of(fake.blocks["colour"], 1) == (0, 255, 0))
        check("coloured_records lists the non-black ones",
              sorted(m for m, _ in layout.coloured_records(fake.blocks["colour"]))
              == [1, 0x0A])
    finally:
        dev.read_block, dev.write_block = real_read, real_write

    print("colour hex helpers:")
    check("round trip", hex_of(*parse_hex("#00FF80")) == "#00FF80")
    check("shorthand", parse_hex("0f8") == (0, 255, 136))
    check("rejects junk", parse_hex("zzz") is None and parse_hex("#12345") is None)
    check("channel order", parse_hex("#FF8000") == (255, 128, 0))

    print(f"presets in {directory}:")
    entries = dev.list_presets(directory)
    check("found some", bool(entries), "no preset files")
    for path, cfg in entries:
        check(os.path.basename(path), cfg is not None, "unusable")
    bad = [cfg for _, cfg in entries if cfg and cfg["effect"] is not None
           and not 0 <= cfg["effect"] <= 0xFF]
    check("effect values in range", not bad)
    vague = [os.path.basename(p) for p, c in entries
             if c and c["effect"] is not None and c["rainbow"] is None]
    check("every effect preset states the rendering", not vague, str(vague))
    # Colour-only presets must not touch the flag: clearing it is safe on every
    # effect now, but a colour preset does not know which effect is live, and a
    # live effect whose record is black would be blanked.
    forced = [os.path.basename(p) for p, c in entries
              if c and c["colour"] is not None and c["effect"] is None
              and c["rainbow"] is not None]
    check("colour presets leave the flag alone", not forced, str(forced))
    # ...and nothing may ask for a blank panel: a preset that clears the flag on
    # an effect which draws its record must bring a non-black colour with it.
    blanks = [os.path.basename(p) for p, c in entries
              if c and c["effect"] is not None and c["rainbow"] is False
              and (c["colour"] is None
                   or layout.clearing_flag_darkens(c["effect"], c["colour"]))]
    check("no preset clears the flag without a colour to draw", not blanks, str(blanks))
    check("colour-capable set is everything but the flag-ignored three",
          layout.SINGLE_COLOUR_MODES
          == frozenset(layout.MODES) - layout.FLAG_IGNORED_MODES - {0x00},
          str(sorted(hex(m) for m in layout.SINGLE_COLOUR_MODES)))
    check("flag-ignored set matches the confirmed three",
          layout.FLAG_IGNORED_MODES == frozenset({0x03, 0x0F, 0x10}))
    check("the two sets are disjoint",
          not (layout.SINGLE_COLOUR_MODES & layout.FLAG_IGNORED_MODES))
    check("colour-capable effects draw any colour, even a black one",
          all(layout.clearing_flag_darkens(m, (0, 0, 0))
              for m in layout.SINGLE_COLOUR_MODES
              if layout.supports_single_colour(m)))
    check("a stored colour never darkens",
          not any(layout.clearing_flag_darkens(m, (255, 0, 0))
                  for m in layout.SINGLE_COLOUR_MODES))
    check("flag-ignored modes never darken, black or not",
          not any(layout.clearing_flag_darkens(m, (0, 0, 0))
                  for m in layout.FLAG_IGNORED_MODES))
    check("unknown colour is reported as safe to clear",
          not layout.clearing_flag_darkens(0x0A))
    check("off is not a darkening case", not layout.clearing_flag_darkens(0x00))

    print(f"\n{'all checks passed' if not failures else str(len(failures)) + ' check(s) failed'}")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
def build_ui(root):
    """Build the widgets and return an app object (with .log())."""
    import tkinter as tk
    from tkinter import colorchooser, messagebox, simpledialog, ttk

    root.title("K61SE test bench")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)

    top = ttk.Frame(root, padding=(10, 8, 10, 0))
    top.grid(row=0, column=0, sticky="ew")
    top.columnconfigure(1, weight=1)

    # ---- controls ---------------------------------------------------------- #
    effect_var = tk.StringVar()
    choices = effect_choices()
    labels = [label for label, _ in choices]
    label_to_mode = {label: mode for label, mode in choices}

    brightness_var = tk.IntVar(value=5)
    speed_var = tk.IntVar(value=5)
    r_var, g_var, b_var = (tk.IntVar(value=v) for v in (255, 0, 0))
    hex_var = tk.StringVar(value="#FF0000")
    status_var = tk.StringVar(value="starting…")

    effect_info_var = tk.StringVar(value="no read yet — press Read keyboard")
    ttk.Label(top, text="Effect").grid(row=1, column=0, sticky="w", pady=2)
    effect_row = ttk.Frame(top)
    effect_row.grid(row=1, column=1, sticky="w", pady=2)
    effect_box = ttk.Combobox(
        effect_row, textvariable=effect_var, values=labels, state="readonly", width=32
    )
    effect_box.grid(row=0, column=0, sticky="w")
    ttk.Label(
        effect_row, textvariable=effect_info_var, foreground="#555"
    ).grid(row=0, column=1, padx=(10, 0), sticky="w")

    def show_mode(mode):
        """Select `mode` in the combobox, adding it if it is not a known effect."""
        label = next((lb for lb, m in label_to_mode.items() if m == mode), None)
        if label is None:
            label = f"0x{mode:02X}  {layout.mode_name(mode)}   (not in effect list)"
            labels.append(label)
            label_to_mode[label] = mode
            effect_box.configure(values=labels)
        effect_var.set(label)

    action = ttk.Frame(top)
    action.grid(row=1, column=2, rowspan=2, sticky="e", padx=(12, 0))
    read_btn = ttk.Button(action, text="Read keyboard")
    read_btn.grid(row=0, column=0, padx=2)
    apply_btn = ttk.Button(action, text="Apply to keyboard")
    apply_btn.grid(row=1, column=0, sticky="ew", padx=2, pady=(4, 0))

    def add_scale(row_, label, var, frm, to, width=200):
        ttk.Label(top, text=label).grid(row=row_, column=0, sticky="w", pady=1)
        s = tk.Scale(
            top,
            variable=var,
            from_=frm,
            to=to,
            orient="horizontal",
            resolution=1,
            length=width,
            showvalue=True,
        )
        s.grid(row=row_, column=1, sticky="w", pady=1)
        return s

    add_scale(2, "Brightness", brightness_var, 1, 5)
    add_scale(3, "Speed", speed_var, 1, 5)
    r_scale = add_scale(4, "Red", r_var, 0, 255)
    g_scale = add_scale(5, "Green", g_var, 0, 255)
    b_scale = add_scale(6, "Blue", b_var, 0, 255)

    # ---- colour ------------------------------------------------------------ #
    colour_row = ttk.Frame(top)
    colour_row.grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))
    swatch = tk.Label(colour_row, width=6, height=1, bg="#FF0000", relief="groove")
    swatch.grid(row=0, column=0, padx=(0, 8))
    ttk.Label(colour_row, text="hex").grid(row=0, column=1)
    hex_entry = ttk.Entry(colour_row, textvariable=hex_var, width=9)
    hex_entry.grid(row=0, column=2, padx=(4, 8))

    def sync_hex(*_):
        try:
            text = hex_of(r_var.get(), g_var.get(), b_var.get())
        except tk.TclError:
            return
        hex_var.set(text)
        swatch.configure(bg=text)

    for var in (r_var, g_var, b_var):
        var.trace_add("write", sync_hex)

    def hex_to_vars(*_):
        rgb = parse_hex(hex_var.get())
        if rgb is None:
            sync_hex()  # unparseable: put the real value back
            return
        r_var.set(rgb[0])
        g_var.set(rgb[1])
        b_var.set(rgb[2])

    hex_entry.bind("<Return>", hex_to_vars)
    hex_entry.bind("<FocusOut>", hex_to_vars)

    def pick_colour():
        result = colorchooser.askcolor(
            color=hex_of(r_var.get(), g_var.get(), b_var.get()),
            title="K61SE colour",
        )
        if result and result[0]:
            r_var.set(int(result[0][0]))
            g_var.set(int(result[0][1]))
            b_var.set(int(result[0][2]))

    wheel_btn = ttk.Button(colour_row, text="Wheel…", command=pick_colour)
    wheel_btn.grid(row=0, column=3)

    # ---- rainbow ----------------------------------------------------------- #
    # The per-effect parameter byte carries a rainbow flag (see k61se_layout):
    # with it set, the firmware draws its own rainbow and ignores the colour
    # block, so the colour widgets are greyed out while it is on.
    rainbow_var = tk.BooleanVar(value=False)
    colour_widgets = [hex_entry, wheel_btn, r_scale, g_scale, b_scale]

    def sync_rainbow(*_):
        """Grey out what the firmware will not use — two cases since 2026-10-19.

        - rotating/spinning/wave 2 (0x03/0x0F/0x10): the flag changes nothing,
          because they always draw their own colours — the tick is not offered.
        - every other effect: flag off draws *that effect's own* colour record
          (confirmed on the panel with --effect-colour), so the tick and the
          colour widgets are both live.  The record is what gets drawn, so a
          black record means a dark panel rather than "no renderer" — see the
          warning in show_stored().
        """
        mode = label_to_mode.get(effect_var.get())
        capable = mode is not None and layout.supports_single_colour(mode)
        rainbow_check.configure(state="normal" if capable else "disabled")
        live = capable and not rainbow_var.get()
        for widget in colour_widgets:
            widget.configure(state="normal" if live else "disabled")

    rainbow_check = ttk.Checkbutton(
        colour_row,
        text="Rainbow (firmware draws it — colour ignored)",
        variable=rainbow_var,
        command=sync_rainbow,
    )
    rainbow_check.grid(row=0, column=4, padx=(12, 0))

    # ---- per-effect settings ------------------------------------------------ #
    # Each effect has its OWN parameter slot (entry[mode-1]), so the rainbow tick
    # and the stored brightness/speed belong to the selected effect, not to the
    # keyboard as a whole.  After a Read we can show them straight from the
    # cached block — no extra device traffic.
    state_cache = {"lighting": None, "colour": None}

    def show_stored(*_):
        mode = label_to_mode.get(effect_var.get())
        light = state_cache["lighting"]
        if mode is None:
            return
        if light is None:
            effect_info_var.set("no read yet — press Read keyboard")
            return
        slot = layout.effect_slot(mode)
        if slot is None:
            effect_info_var.set("no parameter slot (mode 0x00)")
            sync_rainbow()
            return
        bright, speed = light[slot], light[slot + 1]
        if layout.flag_is_ignored(mode):
            rendering = "always colourful — the flag has no effect here"
        elif layout.rainbow_flag(speed):
            rendering = "rainbow (the firmware's own colours)"
        else:
            stored = None
            block = state_cache["colour"]
            if block:
                stored = layout.colour_record_colour(block, mode)
                if stored is not None:
                    stored = tuple(stored)
            if stored is None:
                rendering = "single colour (press Read to see its record)"
            elif layout.clearing_flag_darkens(mode, stored):
                rendering = (
                    "single colour, but its record is BLACK "
                    f"(0x{layout.colour_record_off(mode):02X}) — the panel is dark"
                )
            else:
                rendering = f"single colour {stored}"
        effect_info_var.set(
            f"stored {bright:02X} {speed:02X} · {layout.speed_name(speed)} · {rendering}"
        )
        # The stored nibble is what the effect is doing: on the three that ignore
        # the flag the tick still follows it (clearing it is harmless), and
        # everywhere else it is the real rainbow/colour switch.
        rainbow_var.set(layout.rainbow_flag(speed))
        sync_rainbow()

    effect_box.bind("<<ComboboxSelected>>", show_stored)

    # ---- presets ----------------------------------------------------------- #
    ttk.Separator(top, orient="horizontal").grid(
        row=8, column=0, columnspan=3, sticky="ew", pady=6
    )

    preset_var = tk.StringVar()
    preset_box = ttk.Combobox(
        top, textvariable=preset_var, values=[], state="readonly", width=32
    )
    preset_box.grid(row=9, column=0, columnspan=2, sticky="w")

    preset_btns = ttk.Frame(top)
    preset_btns.grid(row=10, column=0, columnspan=3, sticky="w", pady=(4, 0))
    for i, text in enumerate(("Load", "Save as…", "Delete", "Reload list")):
        ttk.Button(preset_btns, text=text).grid(row=0, column=i, padx=(0, 4))
    sweep_btn = ttk.Button(preset_btns, text="Sweep all")
    sweep_btn.grid(row=0, column=4, padx=(12, 4))
    stop_btn = ttk.Button(preset_btns, text="Stop", state="disabled")
    stop_btn.grid(row=0, column=5)

    # ---- log --------------------------------------------------------------- #
    log_frame = ttk.Frame(root, padding=(10, 6, 10, 6))
    log_frame.grid(row=1, column=0, sticky="nsew")
    log_frame.columnconfigure(0, weight=1)
    log_frame.rowconfigure(0, weight=1)
    log_text = tk.Text(log_frame, height=14, wrap="none", state="disabled")
    log_text.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(log_frame, orient="vertical", command=log_text.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    log_text.configure(yscrollcommand=scroll.set)

    footer = ttk.Label(root, textvariable=status_var, padding=(10, 0, 10, 8), foreground="#555")
    footer.grid(row=2, column=0, sticky="w")

    # ---- app --------------------------------------------------------------- #
    app = type("App", (), {})()
    app.root = root
    app.preset_box = preset_box
    app.presets = []
    app.names = []
    app.sweep_state = None

    def log(message):
        log_text.configure(state="normal")
        log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
        log_text.see("end")
        log_text.configure(state="disabled")

    app.log = log

    def refresh_presets():
        app.presets = dev.list_presets()
        names = []
        for path, cfg in app.presets:
            if cfg is None:
                names.append(f"! {os.path.basename(path)}  (unusable)")
            else:
                names.append(cfg["name"] or os.path.basename(path))
        app.names = names
        preset_box.configure(values=names)
        if names and preset_var.get() not in names:
            preset_var.set(names[0])
        log(f"presets: {len(names)} file(s) in {dev.PRESET_DIR}")

    def selected_preset():
        names = list(preset_box.cget("values"))
        if not names:
            return None, None
        current = preset_var.get()
        if current not in names:
            return None, None
        return app.presets[names.index(current)]

    def set_controls(cfg):
        if cfg.get("effect") is not None:
            show_mode(cfg["effect"])
            show_stored()  # tick + readout follow that effect's own slot …
        if cfg.get("brightness") is not None:
            brightness_var.set(cfg["brightness"])
        if cfg.get("speed") is not None:
            speed_var.set(cfg["speed"])
        if cfg.get("rainbow") is not None:
            # … unless the preset states the rendering itself.  Clearing the flag
            # is safe on every effect except the three that ignore it, but it
            # only *shows* something when that effect's record holds a colour —
            # so refuse just the blank combination (flag off, no colour given).
            mode = label_to_mode.get(effect_var.get())
            rgb = cfg.get("colour")
            if (cfg["rainbow"] is False and mode is not None
                    and layout.clearing_flag_darkens(mode, tuple(rgb) if rgb else (0, 0, 0))):
                log(
                    f"preset wants single colour on 0x{mode:02X} but brings no colour "
                    "to draw — leaving rainbow on (add a non-black colour to blank it "
                    "deliberately)"
                )
            else:
                rainbow_var.set(bool(cfg["rainbow"]))
                sync_rainbow()
        if cfg.get("colour") is not None:
            r_var.set(cfg["colour"][0])
            g_var.set(cfg["colour"][1])
            b_var.set(cfg["colour"][2])

    def gather():
        return {
            "effect": label_to_mode.get(effect_var.get()),
            "brightness": int(brightness_var.get()),
            "speed": int(speed_var.get()),
            "rainbow": bool(rainbow_var.get()),
            "colour": (int(r_var.get()), int(g_var.get()), int(b_var.get())),
        }

    def do_read(*_):
        state, error = current_state()
        if error:
            log(f"read failed: {error}")
            status_var.set("no keyboard")
            return
        light, colour = state["lighting"], state["colour"]
        state_cache["lighting"] = light
        state_cache["colour"] = colour
        mode = light[layout.MODE_BODY_OFF]
        show_mode(mode)
        bright, speed = layout.effect_params(light)
        if bright:
            brightness_var.set(bright)
        if speed:
            level = layout.speed_level(speed)
            speed_var.set(level if 1 <= level <= layout.SPEED_MAX else layout.SPEED_MAX)
        show_stored()
        r, g, b = dev.colour_of(colour, mode)
        r_var.set(r)
        g_var.set(g)
        b_var.set(b)
        log("--- read from keyboard")
        log(f"  lighting: {layout.state_summary('lighting', light)}")
        log(f"  colour:   {layout.state_summary('colour', colour)}")
        status_var.set(f"read ok — mode 0x{mode:02X} ({layout.mode_name(mode)})")

    def do_apply(values=None):
        values = values or gather()
        # Clearing the flag is safe on every effect except the three that ignore
        # it — confirmed on the panel 2026-10-19 with --effect-colour.  What
        # *would* blank the panel is the flag off on an effect whose own record
        # is black (the fourth dark state, PROTOCOL.md §6), so refuse exactly
        # that combination: rainbow off with no colour to draw.
        effect = values.get("effect")
        if values.get("rainbow") is False:
            cached = state_cache["lighting"]
            mode = effect if effect is not None else (
                cached[layout.MODE_BODY_OFF] if cached else None
            )
            rgb = values.get("colour")
            target = tuple(rgb) if rgb else None
            if target is None:
                block = state_cache["colour"]
                if block and mode is not None:
                    got = layout.colour_record_colour(block, mode)
                    target = tuple(got) if got is not None else None
            if (mode is not None and not layout.flag_is_ignored(mode)
                    and not any(target or (0, 0, 0))):
                log(
                    f"effect 0x{mode:02X} would be asked to draw a black/missing record "
                    "— keeping rainbow on rather than blanking the panel"
                )
                values = dict(values, rainbow=True)
        warn_other_writers(log)
        try:
            report = dev.set_state(**values)
        except Exception as exc:  # noqa: BLE001
            log(f"apply failed: {exc}")
            status_var.set("apply failed")
            return False
        log_report(log, report, label="apply")
        if report.get("lighting") is not None:
            state_cache["lighting"] = report["lighting"]
        if report.get("colour") is not None:
            state_cache["colour"] = report["colour"]
        show_stored()
        ok = all(v for v in report["verify"].values() if v is not None)
        status_var.set("apply ok" if ok else "applied, but read-back differed")
        return ok

    def do_save():
        current = preset_var.get()
        name = simpledialog.askstring(
            "Save preset", "Name:", initialvalue=current or "my preset", parent=root
        )
        if not name:
            return
        values = gather()
        cfg = dict(values, name=name, colour=values["colour"])
        path = os.path.join(dev.PRESET_DIR, dev.slug(name) + ".json")
        if os.path.exists(path) and not messagebox.askyesno(
            "Overwrite?", f"{os.path.basename(path)} exists. Overwrite it?"
        ):
            return
        dev.save_config(path, cfg)
        log(f"saved {path}")
        refresh_presets()
        preset_var.set(name)

    def do_load(*_):
        _, cfg = selected_preset()
        if cfg is None:
            log("load: no usable preset selected")
            return
        set_controls(cfg)
        log(f"loaded {os.path.basename(cfg['path'])} into the controls (not applied yet)")

    def do_delete():
        _, cfg = selected_preset()
        if cfg is None:
            return
        path = cfg["path"]
        if not messagebox.askyesno("Delete?", f"Delete {os.path.basename(path)}?"):
            return
        os.remove(path)
        log(f"deleted {os.path.basename(path)}")
        refresh_presets()

    def sweep_next():
        if app.sweep_state is None:
            return
        index = app.sweep_state
        if index >= len(app.presets):
            log("sweep finished")
            app.sweep_state = None
            sweep_btn.configure(state="normal")
            stop_btn.configure(state="disabled")
            status_var.set("sweep finished")
            return
        app.sweep_state = index + 1
        path, cfg = app.presets[index]
        if cfg is None:
            log(f"sweep: skipping unusable {os.path.basename(path)}")
        else:
            set_controls(cfg)
            preset_var.set(app.names[index])
            do_apply(
                {
                    "effect": cfg["effect"],
                    "brightness": cfg["brightness"],
                    "speed": cfg["speed"],
                    "rainbow": cfg["rainbow"],
                    "colour": cfg["colour"],
                }
            )
        status_var.set(f"sweeping {index + 1}/{len(app.presets)}")
        root.after(1500, sweep_next)

    def do_sweep_start():
        writable = [p for p, c in app.presets if c is not None]
        if not writable:
            log("sweep: no usable presets")
            return
        log(f"sweep: {len(writable)} preset(s), 1.5 s apart — press Stop to halt")
        app.sweep_state = 0
        sweep_btn.configure(state="disabled")
        stop_btn.configure(state="normal")
        sweep_next()

    def do_sweep_stop():
        if app.sweep_state is not None:
            app.sweep_state = None
            log("sweep stopped")
        sweep_btn.configure(state="normal")
        stop_btn.configure(state="disabled")

    read_btn.configure(command=do_read)
    apply_btn.configure(command=do_apply)
    preset_btns.winfo_children()[0].configure(command=do_load)
    preset_btns.winfo_children()[1].configure(command=do_save)
    preset_btns.winfo_children()[2].configure(command=do_delete)
    preset_btns.winfo_children()[3].configure(command=refresh_presets)
    sweep_btn.configure(command=do_sweep_start)
    stop_btn.configure(command=do_sweep_stop)

    root.bind("<Control-Return>", lambda _e: do_apply())
    root.bind("<F5>", do_read)

    if labels:
        effect_var.set(labels[0])
    refresh_presets()
    log("K61SE test bench — close the vendor app before applying")
    log(HOTKEY_HINT)
    warn_other_writers(log)
    do_read()
    return app


def main_gui():
    import tkinter as tk

    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass
    build_ui(root)
    root.mainloop()
    return 0


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="K61SE test bench (GUI, or headless preset runner).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--read", action="store_true", help="print the current state and exit")
    parser.add_argument("--list", action="store_true", help="list presets and exit")
    parser.add_argument(
        "--apply", nargs="+", metavar="PRESET", help="apply preset file(s) and exit"
    )
    parser.add_argument(
        "--make-presets", action="store_true", help="(re)write the preset files and exit"
    )
    parser.add_argument(
        "--selftest", action="store_true", help="device-free checks of frames/scale/presets"
    )
    parser.add_argument("--dir", default=dev.PRESET_DIR, help="preset directory")
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="apply every effect preset with every colour preset and exit",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="seconds between matrix steps (default 1.0)"
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="walk every effect in both renderings, ask what the panel did, write a report",
    )
    parser.add_argument(
        "--audit-start",
        metavar="MODE",
        type=lambda text: int(text, 0),
        help="with --audit, start at this effect (resume an interrupted run)",
    )
    parser.add_argument(
        "--probe-nibbles",
        metavar="MODE",
        help="write one effect's parameter byte with every low nibble 0-7 and ask what it did",
    )
    parser.add_argument(
        "--effect-colour",
        nargs=2,
        metavar=("MODE", "COLOUR"),
        help="paint one effect's own colour record (e.g. '0x0A red') and switch it to single colour",
    )
    args = parser.parse_args(argv)

    if args.make_presets:
        make_presets(args.dir)
        return 0

    if args.selftest:
        return selftest(args.dir)

    if args.probe_nibbles is not None:
        warn_other_writers(print)
        return probe_nibbles(args.probe_nibbles)

    if args.effect_colour:
        return set_effect_colour(args.effect_colour[0], args.effect_colour[1])

    if args.audit:
        warn_other_writers(print)
        return audit_colour_modes(start=args.audit_start)

    if args.matrix:
        warn_other_writers(print)
        return apply_matrix(args.dir, args.delay)

    if args.list:
        for path, cfg in dev.list_presets(args.dir):
            if cfg is None:
                print(f"{os.path.basename(path):32s}  UNUSABLE")
            else:
                effect = (
                    "-" if cfg["effect"] is None else f"0x{cfg['effect']:02X} {layout.mode_name(cfg['effect'])}"
                )
                colour = "-" if cfg["colour"] is None else "rgb(%d,%d,%d)" % cfg["colour"]
                rainbow = "-" if cfg.get("rainbow") is None else ("on" if cfg["rainbow"] else "off")
                print(
                    f"{os.path.basename(path):32s}  {cfg['name'][:28]:28s}  "
                    f"effect={effect:28s} bright={cfg['brightness']} speed={cfg['speed']} "
                    f"rainbow={rainbow:3s} colour={colour}"
                )
        return 0

    if args.apply:
        warn_other_writers(print)
        return 1 if apply_paths(args.apply) else 0

    if args.read:
        state, error = current_state()
        if error:
            print(f"read failed: {error}", file=sys.stderr)
            return 1
        for name, body in state.items():
            print(f"{name}: {layout.state_summary(name, body)}")
        return 0

    return main_gui()


if __name__ == "__main__":
    sys.exit(main())
