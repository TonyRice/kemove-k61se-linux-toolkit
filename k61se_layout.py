#!/usr/bin/env python3
"""Shared constants + helpers for the KEMOVE K61SE vendor blocks.

Frame (report 6, 520-byte feature report):
  06 | op | sub | 00 | chunks=01 | index=00 | size_lo | size_hi | <size bytes>
  0x80 in the op byte means READ.

Blocks:
  * lighting block  op 0x84  128 bytes
  * colour block    op 0x8A  420 bytes
  * music frame     op 0x88  378 bytes  (streamed by the app, write op 0x08)

Unless a name says "report", every offset here is a BODY offset, i.e. measured
after the 8-byte frame has been stripped.

The full write-up — frame format, blocks, the panel gate model, gotchas and
open questions — is in PROTOCOL.md next to this file.  Keep the two in sync:
this module is the machine-readable half, PROTOCOL.md is the readable half.
"""

# --------------------------------------------------------------------------- #
# lighting block (128 bytes)
# --------------------------------------------------------------------------- #
LIGHTING_SIZE = 128
COLOUR_SIZE = 420

# Music mode is streamed as a raw LED matrix, op 0x08 (write) / 0x88 (read).
# 21 columns x 6 rows x (G,R,B); only columns 0-13 and rows 1-5 carry keys.
MUSIC_SIZE = 378
MUSIC_COLS = 21
MUSIC_ROWS = 6
MUSIC_CELL = 3
MUSIC_CELLS = MUSIC_SIZE // MUSIC_CELL       # 126
# A key's cell is its `key_index` from the layout XML; byte offset = 3*key_index.
# The first cell of each column (0, 6, 12, ...) is padding and always zero.

POWER_REPORT_OFF = 0x12                      # offset in the full 136-byte response
POWER_BODY_OFF = POWER_REPORT_OFF - 8        # 0x0A in the stripped body

# Body 0x0A is a MODE enum, not a boolean power flag: 0 means "backlight off",
# every other value selects an effect. Derived from paired per-mode dumps.
MODE_BODY_OFF = POWER_BODY_OFF
MODE_REPORT_OFF = POWER_REPORT_OFF
MODES = {
    0x00: "off",
    0x01: "solid",
    0x02: "pulsing",
    0x03: "rotating",
    0x04: "typing feedback",
    # Named from the user's own dump labels during the 2026-10-19 sweep.
    # 0x06 and 0x0E were never observed and stay unknown.
    0x05: "rainbow pixel changing",
    0x07: "typing feedback, whole keyboard",
    0x08: "rainbow pulsing + rotating",
    0x09: "rainbow solid candy",
    0x0A: "rainbow snake",
    0x0B: "rainbow wave",
    0x0C: "typing feedback, single key",
    0x0D: "wave pulse",                     # fast/slow are the same mode
    0x0F: "spinning",
    0x10: "wave 2",
    0x11: "pulse 2",
}


def mode_name(value):
    return MODES.get(value, f"unknown (0x{value:02X})")

PALETTE_SEP_OFF = 0x38                       # FF FF separator
PALETTE_OFF = 0x3A                           # first RGB565 little-endian word
TRAILER_OFF = 0x7E                           # 5A A5 trailer
PALETTE_WORDS = (TRAILER_OFF - PALETTE_OFF) // 2   # 34

# (start, end_exclusive, label) — tentative, to be confirmed by dumps.
LIGHTING_FIELDS = (
    (0x00, 0x08, "block header"),
    (0x08, 0x0A, "marker (07 00)"),
    (0x0A, 0x0B, "MODE (00=off 01=solid 02=pulsing 03=rotating 04=typing)"),
    (0x0B, 0x0C, "flags? (20)"),
    (0x0C, 0x14, "unmapped"),
    (0x14, 0x16, "unmapped (04 04)"),
    (0x16, 0x18, "0x16/0x17 (7F 00 normal, 00 FF preview)"),
    (0x18, 0x19, "unmapped (0C)"),
    (0x19, 0x1E, "unmapped"),
    (0x1E, 0x1F, "unmapped (04)"),
    (0x1F, 0x38, "unmapped"),
    (0x38, 0x3A, "entry-0 separator (FF FF)"),
    (0x3A, 0x7E, "entry table (34 x 2 bytes)"),
    (0x7E, 0x80, "trailer (5A A5)"),
)

# colour block (op 0x8A, 420 bytes) — **twenty 21-byte records, one per effect**
#
#     record `mode` starts at 21 * mode; record[mode][0..2] = (G, R, B)
#
# Proven 2026-10-19 by pairing the vendor app's own "colourful" and "single
# colour" dumps for every effect.  Toggling effect m to single colour wrote
# R=FF at exactly 21*m+1 — 0x16 for solid, 0x2B for pulsing, 0x40 for rotating,
# 0x55 for typing feedback, 0x6A for 0x05, 0xE8 for 0x0B, 0xFD for 0x0C,
# 0x112 for 0x0D, 0x13C for 0x0F, 0x151 for 0x10, 0x166 for 0x11 — because the
# selected colour was red and only the red byte changed.
#
# Two long-standing mysteries dissolve in this layout:
#   * 0x15/0x16/0x17 are not "the" colour, they are **effect 1's** colour
#     (record 1 at 21*1), which is why writing them only ever coloured solid;
#   * 0x2B and 0x55 are not sticky flags, they are the red values of effect 2
#     (pulsing) and effect 4 (typing feedback) — the two animated effects the
#     app had already been given a colour in the 2026-10-18 session.
#
# The remaining 18 bytes of each record have never been seen non-zero; unknown.
COLOUR_RECORD_STRIDE = 21
COLOUR_RECORDS = COLOUR_SIZE // COLOUR_RECORD_STRIDE   # 20 records, modes 0..19


def colour_record_off(mode):
    """Byte offset of effect `mode`'s 21-byte colour record, or None."""
    if mode is None or not 0 <= mode < COLOUR_RECORDS:
        return None
    return mode * COLOUR_RECORD_STRIDE


def colour_record_offs(mode):
    """{'green','red','blue'} offsets inside effect `mode`'s colour record."""
    base = colour_record_off(mode)
    if base is None:
        return None
    return {"green": base, "red": base + 1, "blue": base + 2}


def colour_record_colour(data, mode):
    """(r, g, b) stored for effect `mode`, or None if there is no record."""
    offs = colour_record_offs(mode)
    if offs is None or offs["blue"] >= len(data):
        return None
    return (data[offs["red"]], data[offs["green"]], data[offs["blue"]])


def coloured_records(data):
    """Yield (mode, (r,g,b)) for every effect record that holds a non-black colour."""
    for mode in range(1, COLOUR_RECORDS):
        rgb = colour_record_colour(data, mode)
        if rgb and any(rgb):
            yield mode, rgb


# Kept for older callers: these are effect 1's own channels, not a global colour.
COLOUR_CHANNEL_OFFS = colour_record_offs(1)  # {"green": 0x15, "red": 0x16, "blue": 0x17}

# lighting-block candidates that are still unconfirmed
BRIGHTNESS_OFF = 0x0B           # 0x20 in every sample — NOT brightness, still unmapped
ENTRY3_HI_OFF = 0x41            # high byte of entry[3] — part of the per-effect table

# --- per-effect parameter table -------------------------------------------- #
# The 34 words at 0x3A are indexed by effect: entry[mode - 1].  Brightness is
# the low byte, speed the high byte.  Proven 2026-10-19: the only slots in
# 0..16 that never changed were 5 and 13, i.e. modes 0x06 and 0x0E, the only
# two never selected; and the brightness/speed sweeps on mode 0x11 moved
# exactly entry[16] (byte 0x5A = 1..5, byte 0x5B = 0x17/0x37/0x57).
PARAMS_OFF = PALETTE_OFF

# The parameter byte at entry[mode-1]+1 holds TWO things, one per nibble:
#
#   high nibble : speed level, a 5-level scale
#                 0x17 level 1, 0x27 level 2, 0x37 level 3, 0x47 level 4,
#                 0x57 level 5.  Only levels 1/3/5 were ever named by the user
#                 (slow/middle/fast).
#   low nibble  : RAINBOW FLAG
#                 0x7 = the firmware draws its own rainbow and the colour
#                       block's channels are IGNORED;
#                 0x0 = draw the colour block's channels.
#
# Proven 2026-10-19 with controlled pairs that differ in exactly this one byte:
#   mode 0x04  typing feedback rainbow (0x57) vs red (0x50)  -> only 0x41
#   mode 0x01  solid rainbow        (0x57) vs red (0x50)    -> only 0x3B
# and cross-checked against the vendor app's own writes: its "solid red" write
# carries 0x50, and the user's 2026-10-18 `colorful_solid` capture carries 0x57.
SPEED_MAX = 5
# All five rungs were confirmed on the panel 2026-10-19 (the bench wrote each
# level and the animation visibly changed step by step).
SPEED_LEVEL_NAMES = {1: "slowest", 2: "slow", 3: "middle", 4: "fast", 5: "fastest"}
RAINBOW_ON = 0x7
RAINBOW_OFF = 0x0

# --- what the rainbow flag does, per effect (confirmed 2026-10-19) ---------- #
#
# The first audit (see colour_mode_audit.md) concluded that only three effects
# could draw a colour, because it wrote 0x15/0x16/0x17 for every effect — and
# those turned out to be *effect 1's* record (see colour_record_off).  The app
# walk then showed each effect has its own record, and the user confirmed the
# result on the panel with `k61se_gui.py --effect-colour MODE red` on 2026-10-19:
# **every effect drew the colour except 0x03, 0x0F and 0x10.**
#
# So there are two classes, not three:
#
#   * flag-off draws that effect's own colour record — every effect that has a
#     parameter slot and is not in FLAG_IGNORED_MODES.  If the record is black
#     (0,0,0) the panel is simply black: the fourth dark state in §6.
#   * the flag is ignored entirely — 0x03 rotating, 0x0F spinning, 0x10 wave 2
#     render identically either way (confirmed twice: the bench audit and the
#     user watching the vendor app's own "colourful" toggle).  These three have
#     no single-colour mode at all, so a colour record for them is inert.
FLAG_IGNORED_MODES = frozenset({0x03, 0x0F, 0x10})


def supports_single_colour(mode):
    """True when the flag-off rendering draws this effect's own colour record."""
    return (
        mode in MODES
        and mode != 0x00
        and effect_slot(mode) is not None
        and mode not in FLAG_IGNORED_MODES
    )


def flag_is_ignored(mode):
    """True when the firmware renders this effect the same either way."""
    return mode in FLAG_IGNORED_MODES


# Kept as a name for callers: every effect confirmed to draw a colour.
SINGLE_COLOUR_MODES = frozenset(
    m for m in MODES if m != 0x00 and m not in FLAG_IGNORED_MODES
)


def clearing_flag_darkens(mode, colour=None):
    """True when clearing the flag would leave the panel with nothing to draw.

    Only one situation does that now: an effect that *does* draw its record, at
    a moment when that record is black.  Pass the effect's stored (r, g, b) to
    get an answer; without it the flag-ignored and unknown modes are the only
    ones we can rule out, and everything else is reported as safe (the caller
    that matters, `set_state()`, always knows the colour it is writing).
    """
    if not supports_single_colour(mode):
        return False
    if colour is None:
        return False
    return not any(colour)


def speed_level(value):
    """Speed level 1..5 from a parameter byte (the high nibble)."""
    return value >> 4


def rainbow_flag(value):
    """True when the parameter byte asks the firmware for its own rainbow."""
    return (value & 0x0F) == RAINBOW_ON


def set_rainbow(value, on):
    """The same byte with the rainbow flag set or cleared."""
    return (value & 0xF0) | (RAINBOW_ON if on else RAINBOW_OFF)


def speed_byte(level, rainbow=False, low=None):
    """Encode speed level 1..5.

    `rainbow` picks the flag nibble; pass `low` instead to echo back a nibble
    verbatim (e.g. one we have not decoded rather than one of ours).
    """
    level = max(1, min(SPEED_MAX, int(level)))
    if low is None:
        low = RAINBOW_ON if rainbow else RAINBOW_OFF
    return (level << 4) | (low & 0x0F)


def effect_slot(mode):
    """Body offset of the 2-byte parameter word for effect `mode`, else None."""
    if not 1 <= mode <= PALETTE_WORDS:
        return None
    return PARAMS_OFF + 2 * (mode - 1)


def effect_params(data):
    """(brightness, speed_byte) for the block's current mode, or (None, None)."""
    off = effect_slot(data[MODE_BODY_OFF])
    if off is None or off + 1 >= len(data):
        return None, None
    return data[off], data[off + 1]


def speed_name(value):
    """Speed label from the high nibble (levels 1..5).

    All five levels are reachable and were confirmed on the panel on
    2026-10-19.  The rainbow flag lives in the same byte and is reported
    separately — see `rainbow_flag()`.
    """
    level = speed_level(value)
    if not 1 <= level <= SPEED_MAX:
        return f"0x{value:02X}"
    named = SPEED_LEVEL_NAMES.get(level)
    if named:
        return f"{named} ({level}/{SPEED_MAX})"
    return f"level {level}/{SPEED_MAX}"


def slot_summary(value):
    """One-line description of a parameter byte: speed level + rainbow flag."""
    return (
        f"speed {speed_name(value)}, "
        f"{'rainbow' if rainbow_flag(value) else 'single colour'}"
    )

FIELD_SETS = {"lighting": LIGHTING_FIELDS, "colour": ()}

# What we have actually observed move (facts, not guesses):
#   0x0A : MODE enum — 00 off, 01 solid, 02 pulsing, 03 rotating, 04 typing
#          feedback; 0B is rainbow wave, not a preview (early guess, corrected)
#          The panel is lit only when the mode is non-zero AND the firmware's
#          enable flag is set.  The hotkey toggles that flag and leaves the whole
#          block untouched, so 0x0A says nothing about whether the panel is lit,
#          and nothing we (or the vendor app) can write clears that flag.
#   entry[mode-1]+1 : speed level (high nibble) + rainbow flag (low nibble, 7=on)
#          e.g. 0x57 = fast + rainbow, 0x50 = fast + single colour.  Mode 0x04's
#          byte lives at 0x41 and was the first place we saw both variants.
#   colour 21*mode .. +2 : that effect's own (G,R,B) in the colour block.  Only
#          the active effect's record is drawn; record 1 (0x15..0x17) is solid's,
#          not a global colour.  Verified 2026-10-19 by pairing the app's
#          colourful/single-colour dumps for all 15 effects.


def field_name(off, block="lighting"):
    if block == "colour":
        return colour_field_name(off)
    for start, end, name in FIELD_SETS.get(block, LIGHTING_FIELDS):
        if start <= off < end:
            return name
    return "?"


def colour_field_name(off):
    """Name a colour-block byte using the 21-byte per-effect records."""
    mode, index = divmod(off, COLOUR_RECORD_STRIDE)
    channel = ("GREEN", "RED", "BLUE")[index] if index < 3 else None
    if channel:
        return f"record {mode} (mode 0x{mode:02X}) {channel}"
    return f"record {mode} (mode 0x{mode:02X}) +0x{index:02X}"


# --------------------------------------------------------------------------- #
# colour helpers
# --------------------------------------------------------------------------- #
def _scale(value, bits):
    return value * 255 // ((1 << bits) - 1)


def rgb565(word):
    """Decode a little-endian RGB565 word into an 8-bit (r, g, b) tuple."""
    r = _scale((word >> 11) & 0x1F, 5)
    g = _scale((word >> 5) & 0x3F, 6)
    b = _scale(word & 0x1F, 5)
    return r, g, b


def hexcolour(rgb):
    return "#%02X%02X%02X" % rgb


NAMED_COLOURS = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "off": (0, 0, 0),
}


def parse_colour(text):
    """Parse 'red' | '#RRGGBB' | 'RRGGBB' | 'r,g,b' into an (r, g, b) tuple."""
    value = str(text).strip().lower()
    if value in NAMED_COLOURS:
        return NAMED_COLOURS[value]
    if "," in value:
        parts = [p.strip() for p in value.split(",")]
        if len(parts) != 3:
            raise ValueError(f"colour {text!r}: expected three comma-separated numbers")
        try:
            rgb = tuple(int(p, 0) for p in parts)
        except ValueError:
            raise ValueError(f"colour {text!r}: not three numbers") from None
    else:
        hexpart = value[1:] if value.startswith("#") else value
        if len(hexpart) != 6:
            raise ValueError(
                f"colour {text!r}: use a name, #RRGGBB, RRGGBB or r,g,b"
            )
        try:
            rgb = tuple(int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            raise ValueError(f"colour {text!r}: bad hex") from None
    if not all(0 <= v <= 255 for v in rgb):
        raise ValueError(f"colour {text!r}: channels must be 0..255")
    return rgb


def word_at(data, off):
    return data[off] | (data[off + 1] << 8)


def in_palette(off):
    return PALETTE_OFF <= off < TRAILER_OFF


def palette_word_offset(off):
    """Return the offset of the RGB565 word containing body byte `off`."""
    return PALETTE_OFF + ((off - PALETTE_OFF) // 2) * 2


def palette(data):
    """Yield (index, offset, word, rgb) for each palette word in `data`."""
    for i in range(PALETTE_WORDS):
        off = PALETTE_OFF + i * 2
        if off + 1 >= len(data):
            return
        word = word_at(data, off)
        yield i, off, word, rgb565(word)


def palette_runs(data):
    """Merge consecutive equal palette words -> list of (count, offset, word, rgb)."""
    runs = []
    for _, off, word, rgb in palette(data):
        if runs and runs[-1][2] == word:
            runs[-1][0] += 1
        else:
            runs.append([1, off, word, rgb])
    return [tuple(r) for r in runs]


# --------------------------------------------------------------------------- #
# decoded state summaries (shared by dump_lighting --watch and review_dumps -v)
# --------------------------------------------------------------------------- #
MODE_SLUGS = {
    0x00: "off",
    0x01: "solid",
    0x02: "pulse",
    0x03: "rotate",
    0x04: "typing",
    0x05: "rainbow_pixel",
    0x07: "typing_whole",
    0x08: "rainbow_pulse_rot",
    0x09: "rainbow_candy",
    0x0A: "rainbow_snake",
    0x0B: "rainbow_wave",
    0x0C: "typing_single",
    0x0D: "wave_pulse",
    0x0F: "spinning",
    0x10: "wave2",
    0x11: "pulse2",
}

CHANNEL_ORDER = (("red", "r"), ("green", "g"), ("blue", "b"))


def channel_letters(colour, mode=1):
    """'r', 'g', 'b' for each channel of effect `mode`'s record that is on (FF)."""
    offs = colour_record_offs(mode)
    if offs is None or offs["blue"] >= len(colour):
        return ""
    return "".join(
        letter for name, letter in CHANNEL_ORDER if colour[offs[name]] == 0xFF
    )


def auto_label(lighting=None, colour=None):
    """Self-describing label for a captured state, built from decoded fields.

    e.g. 'm02pulse_rgb', 'm01solid_r', 'm04typing_rgb'.  The letters come from
    the record belonging to the mode in the lighting block, because each effect
    has its own colour (see colour_record_off).
    """
    parts = []
    mode = None
    if lighting is not None:
        mode = lighting[MODE_BODY_OFF]
        parts.append(f"m{mode:02X}{MODE_SLUGS.get(mode, 'unk')}")
    if colour is not None:
        letters = channel_letters(colour, mode if mode is not None else 1) or "none"
        # With no lighting block to anchor it, say "col_" so a bare "g" cannot be
        # mistaken for a lighting state.
        parts.append(letters if lighting is not None else f"col_{letters}")
    return "_".join(parts) or "state"


def state_summary(block, data):
    """One line describing the decoded state of `block`, for reports."""
    if block == "lighting":
        mode = data[MODE_BODY_OFF]
        bright, speed = effect_params(data)
        slot = effect_slot(mode)
        if bright is None:
            params = "  (no parameter slot)"
        else:
            params = (
                f"  brightness={bright}"
                f"  speed={speed_name(speed)}"
                f"  colour={'rainbow' if rainbow_flag(speed) else 'single'}"
                f"  slot[{(slot - PARAMS_OFF) // 2}]={bright:02X} {speed:02X}"
            )
        return f"mode=0x{mode:02X} ({mode_name(mode)})" + params
    if block == "colour":
        nz = sum(1 for b in data if b)
        shown = ", ".join(
            f"m{mode:02X}={r:02X}{g:02X}{b:02X}" for mode, (r, g, b) in coloured_records(data)
        )
        return f"records (R/G/B)={shown or 'none'}  non-zero={nz}"
    return f"{len(data)} bytes"


# --------------------------------------------------------------------------- #
# shared formatting
# --------------------------------------------------------------------------- #
def hexdump(data, base=0, width=16, indent="  "):
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hexs = " ".join(f"{b:02X}" for b in chunk)
        asci = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{indent}{base + i:04X}  {hexs:<{width * 3}} |{asci}|")
    return "\n".join(lines)


def format_palette(data, indent="  "):
    """Human-readable entry table, runs merged.

    Entries are shown as little-endian 16-bit words decoded as RGB565 — that
    decode is a GUESS and unconfirmed; treat the raw bytes as authoritative.
    """
    runs = palette_runs(data)
    if not runs:
        return f"{indent}entries: (block too short to decode)"
    total = sum(c for c, _, _, _ in runs)
    lines = [
        f"{indent}entries: {total} x 2 bytes @ body 0x{PALETTE_OFF:02X}"
        f"  (bytes shown are authoritative; RGB565 decode is a guess)"
    ]
    for count, off, word, rgb in runs:
        lines.append(
            f"{indent}  0x{off:02X}  x{count:<3} {word & 0xFF:02X} {word >> 8:02X}"
            f"   0x{word:04X}  {hexcolour(rgb)}"
        )
    return "\n".join(lines)
