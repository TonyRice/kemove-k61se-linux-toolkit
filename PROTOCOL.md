# KEMOVE K61SE — vendor HID protocol & lighting control on Linux

Reverse-engineered from USB captures of the Windows vendor app running under
Wine. Everything below is either **verified from a capture** or explicitly
marked **guess / unknown**. The byte dumps are authoritative; decodes are not.

- Device: `258A:010C` (KEMOVE), management **interface 1**
- HID **report id 6**, **520-byte** feature report
- Nodes seen on this machine: `/dev/hidraw2`, `/dev/hidraw5` —
  **they move on replug**; always enumerate by VID/PID + interface number.
- Panic button if a bad state is ever written: hold **Fn + Tab for 5 s**
  (factory reset).

Repository note: the public Git repository tracks the source, docs, the vendor
keyboard XML, and the small canonical reset fixtures. The full `captures/`
archive, `dumps/`, and vendor app binaries are local evidence and are ignored.

---

## 1. Frame format

Every exchange is a HID feature report, report id `0x06`:

```
06 | op | sub | 00 | chunks=01 | index=00 | size_lo | size_hi | <size bytes of payload>
 │    │
 │    └─ 0x80 bit set => READ,  clear => WRITE
 └─ report id
```

- `chunks` is the total number of 512-byte chunks; `index` is the zero-based
  chunk number. Lighting, colour, and keymap blocks fit in one chunk
  (`01`/`00`). Large macro areas use the same header with `chunks > 1`; each
  request/response carries one chunk. The vendor SDK's wired read method and
  the F75 wired transport both build the frame this way.
- `size` is little-endian and says how many payload bytes follow the 8-byte
  frame **for this chunk** (at most 512).

### Reading a block

Send the frame with `op | 0x80` and the wanted size, then `get_feature_report(6, 520)`:

```python
dev.send_feature_report(bytes([0x06, 0x80|op, sub, 0, 0x01, 0x00,
                               size & 0xFF, size >> 8] + b"\x00"*512))
resp = dev.get_feature_report(0x06, 520)   # -> 8 + size bytes
body = resp[8:]                            # the actual block
```

### The reply length tells you nothing

**The device always returns exactly `8 + declared_size` bytes, for every op.**
Asking `0x84` for 512 bytes returns 520 bytes, exactly like an unimplemented op.
So length is *not* a block discriminator — judge a read by its **content**.
Unknown ops return leftover memory (we have seen coherent `8051` firmware), and
that is harmless.

---

## 2. Blocks

Only the first four blocks were observed from the app; the last three are
recovered statically from `KBAccess.dll` and are not yet exercised on K61SE
hardware (§5b):

| purpose | read op | write op | sub | size | notes |
|---|---|---|---|---|---|
| device info | `0x82` | — | `1` | 6 B | reply `03 00 00 00 01 71` |
| lighting | `0x84` | `0x04` | `0` | 128 B | mode + brightness + 34 entry words |
| colour | `0x8A` | `0x0A` | `0` | 420 B | twenty 21-byte per-effect colour records (§5) |
| music frame | `0x88`* | `0x08` | `0` | 378 B | 21×6 RGB matrix, 14×5 used (§5a) |
| keymap / layers | `0x83` | `0x03` | layer | 504 B | 126 × 4-byte actions (§5b) |
| macros | `0x85` | `0x05` | `0` | ≤ 4096 B | table + blobs (§5b) |
| per-key colour/game | `0x86` | `0x06` | profile | 378 B | vendor `SetKeyColor`; not used yet |

\* inferred from the write op (`| 0x80`); the read side has not been tried yet.

Writes use the *same* frame with the `0x80` bit **clear** and the body attached.

⚠️ **There is no "apply" byte.** The `FF` at colour body `0x16` in the `color_red`
capture is simply *the red channel being turned on*, not a commit marker.

---

## 3. Lighting block (`op 0x84`, 128 bytes)

Offsets are **body** offsets (after the 8-byte frame).

| offset | width | observed value(s) | meaning |
|---|---|---|---|
| `0x00` | 8 | `00 03 03 02 00 00 04 04` | block header, never seen change |
| `0x08` | 2 | `07 00` | marker, never seen change |
| **`0x0A`** | 1 | see §4 | **MODE enum** |
| `0x0B` | 1 | `20` | **not brightness** — never varies, still unmapped |
| `0x0C`–`0x13` | | zeros | unmapped |
| `0x14` | 2 | `04 04` | unmapped |
| `0x16` | 2 | `7F 00` normal / `00 FF` preview | unmapped flag pair |
| `0x18` | 1 | `0C` | unmapped |
| `0x1E` | 1 | `04` | unmapped |
| `0x38` | 2 | `FF FF` | separator before the parameter table |
| `0x3A`–`0x7D` | 68 | 34 × 2-byte slots | **per-effect brightness + speed** — see below |
| `0x7E` | 2 | `5A A5` | trailer |

### The parameter table (`0x3A`–`0x7D`) — one slot per effect

34 words, **indexed by effect: `entry[mode - 1]`.**

| slot byte | meaning |
|---|---|
| `+0` (low) | **brightness**, `1`–`5` |
| `+1` (high) | **speed** in the high nibble (level 1–5) **and the rainbow flag in the low nibble** (`7` = rainbow, `0` = single colour) |

So `0x57` is "level 5, rainbow" and `0x50` is "level 5, single colour" — two
different renderings of the same effect. (The firmware only has a single-colour
renderer for three of the fifteen effects; on the rest `0x50` means *nothing
drawn*. See §3 and `colour_mode_audit.md`.)

Proven on 2026-10-19, from two experiments run on mode `0x11`:

- **Brightness**, five captures with only the slider moving: `entry[16]` went
  `5701 → 5702 → 5703 → 5704 → 5705`. Exactly one byte changed, monotonically.
- **Speed**, fast → middle → slow → fast: `entry[16]` went `5705 → 3705 → 1705
  → 5705`, and the last capture is **byte-identical** to the first, so it is a
  setting and not a latch.

The sweep of all 18 effects then confirms the indexing. Each dump's own slot
`entry[mode-1]` was always a touched slot, and across the whole set of 0..16 the
**only slots that never changed are 5 and 13** — precisely modes `0x06` and
`0x0E`, the only two never selected:

```
slot 0  -> mode 0x01      slot 12 -> mode 0x0D
slot 1  -> mode 0x02      slot 13 -> mode 0x0E   <- never touched
slot 5  -> mode 0x06     <- never touched
slot 16 -> mode 0x11      slot 20+ -> modes 0x15+ (no effect, left at default)
```

Slots holding no selected effect keep the default `3703` (brightness 3, speed
middle, single colour). That is what the earlier "one slot appears per click"
pattern was: each effect's slot gets written the first time you select it, not an
accumulating counter.

This also revises an earlier guess: `0x53` **is** the speed — of mode `0x0D`.
It is `entry[12]`'s high byte, so the "fast wave pulse" / "slow wave pulse" pair
differing by that one byte (`57` vs `17`) was the speed all along. It stayed
`17` during the later speed test because that test was on mode `0x11`, whose
speed lives at `0x5B`. Speed is stored **per effect**, at `entry[mode-1]+1`,
and there is no single global speed byte.

### The low nibble is a rainbow flag

Settled 2026-10-19, once the user dumped the vendor app's own "solid rainbow"
state. The byte is not just a speed: its **low nibble asks the firmware to draw
its own rainbow**, in which case that effect's colour record is ignored.

Two controlled dumps differ in **exactly this one byte**, nothing else:

| pair | only difference |
|---|---|
| mode `0x04` typing feedback, rainbow vs red, 11 s apart | `0x41`: `57` → `50` |
| mode `0x01` solid, rainbow vs red, 12 s apart | `0x3B`: `57` → `50` |

and it cross-checks against the vendor app's own captured writes: its "solid
red" write carries `0x50`, while the user's 2026-10-18 `colorful_solid` capture
and the 2026-10-19 `solid_rainbow_app` dump both carry `0x57`.

Consequences:

- `0x01` + `05 57` = the **solid rainbow** the user was looking for. Solid's
  colour record in that state still reads stale red (`0x16=FF`) precisely because
  it is ignored.
- The palette is 34 slots, each carrying three things per effect:
  **brightness, speed level, rainbow-or-single**.
- `k61se_layout.py` exposes this as `rainbow_flag()`, `set_rainbow()` and
  `speed_byte(level, rainbow=…)`; `k61se_dev.set_state()` takes `rainbow=`;
  `state_summary()` prints `colour=rainbow` / `colour=single`.

#### …and the flag-off rendering draws each effect's own colour record

**CONFIRMED 2026-10-19 (evening).** The first audit (§ below) concluded that nine
effects have no colour renderer. The follow-up app walk (§5) showed the colour
block holds **one colour record per effect**, and the audit only ever wrote
*solid's* record (`0x15/0x16/0x17`). The targeted re-test with the vendor app
closed settled it: `k61se_gui.py --effect-colour MODE red` turned **all twelve
remaining effects red**, while `0x03` (the control) stayed colourful. The
corrected bulk `--audit` then reproduced that exactly — **12 of 15 both
renderings, 3 flag-ignored, 0 dark** (`colour_mode_audit.md`). So:

| flag off | effects | what the panel does |
|---|---|---|
| draws its own record | every effect with a slot except the three below (`0x01`, `0x02`, `0x04`–`0x05`, `0x07`–`0x0D`, `0x11`) | draws that effect's own 21-byte colour record; a black record means a **black panel**, not a missing renderer |
| flag ignored | `0x03` rotating, `0x0F` spinning, `0x10` wave 2 | identical to the rainbow rendering, byte for byte; their record is stored but never drawn |

`k61se_layout` expresses this as `supports_single_colour(mode)` (the effect has a
drawable record), `flag_is_ignored(mode)` and
`clearing_flag_darkens(mode, colour)` — the last one is True only for a
colour-capable effect whose record is **black**, which is the "fourth dark state"
in §6 and the intended way to switch a single effect off. `SINGLE_COLOUR_MODES`
is kept as a name and now means "everything but the flag-ignored three".

The GUI, the `theme_*` presets and `--matrix` all cover every colour-capable
effect: the matrix is 16 rainbow writes + 12 colours × 12 effects = 160 writes.

History — the original audit's row, kept so the retraction is auditable:

| flag off | effects | what the panel did |
|---|---|---|
| colour renderer exists | `0x01` solid, `0x02` pulsing, `0x04` typing feedback | drew the colour block's channels (they were *its* records) |
| no colour renderer | `0x05`, `0x07`, `0x08`, `0x09`, `0x0A`, `0x0B`, `0x0C`, `0x0D`, `0x11` | **nothing at all** — now read as "their own records were black" |
| flag ignored | `0x03` rotating, `0x0F` spinning, `0x10` wave 2 | identical to the rainbow rendering, byte for byte |

The three ignored ones were confirmed by eye twice: `05 50` and `05 57` look
the same, and the user saw the same thing in the vendor app (toggling
"colourful" on rotating changed nothing).

Notes and limits:

- Brightness is the low byte (`1`–`5`). `0x5002` in an early dump (brightness 2)
  and the `01…05` series both fit.
- The speed values step by `0x20` (`17 / 37 / 57`) within a fixed flag nibble,
  i.e. **the level is the high nibble**: `0x17`=1, `0x27`=2, `0x37`=3, `0x47`=4,
  `0x57`=5 (all with rainbow on; `0x10`–`0x50` with it off). All five rungs are
  real: the bench wrote each level in turn on 2026-10-19 and the animation
  stepped through them, so the earlier "levels 2 and 4 untested" caveat is gone.
- Only `0` and `7` have been observed in the flag nibble. Nothing says a value
  in between is illegal, so a writer that does not care about the flag should
  echo the existing nibble verbatim rather than forcing `7` or `0`.
- ~~"For mode `0x01` the high byte is `0x50` rather than `0x5?`, so it carries
  more than speed."~~ It carries the rainbow flag, as above.
- `format_palette()` decodes these words as little-endian RGB565. That guess is
  now firmly dead: the bytes are brightness, speed and the rainbow flag.
- `0x41` is just `entry[3]`'s parameter byte, so it is mode `0x04`'s
  speed **and** rainbow flag, not an independent field.

---

## 4. Mode enum (lighting body `0x0A`)

| value | effect | how established |
|---|---|---|
| `0x00` | off | labelled dump |
| `0x01` | solid | labelled dumps |
| `0x02` | pulsing | labelled dumps |
| `0x03` | rotating | labelled dumps |
| `0x04` | typing feedback | labelled dumps |
| `0x05` | rainbow pixel changing | user label, 2026-10-19 |
| `0x06` | **draws nothing — panel goes dark** | direct write, 2026-10-19; not in the app's list |
| `0x07` | typing feedback, whole keyboard | user label, 2026-10-19 |
| `0x08` | rainbow pulsing + rotating | user label, 2026-10-19 |
| `0x09` | rainbow solid candy | user label, 2026-10-19 |
| `0x0A` | rainbow snake | user label, 2026-10-19 |
| `0x0B` | rainbow wave | user label, 2026-10-19 |
| `0x0C` | typing feedback, single key | user label, 2026-10-19 |
| `0x0D` | wave pulse (**fast and slow are the same mode**) | user labels, 2026-10-19 |
| `0x0E` | — **never observed** | gap in the app's list |
| `0x0F` | spinning | user label, 2026-10-19 |
| `0x10` | wave 2 | user label, 2026-10-19 |
| `0x11` | pulse 2 | user label, 2026-10-19 |

All 15 observations are single-label provenance, so a name is only as good as the
app's own effect name. `mode_name()`/`MODE_SLUGS` in `k61se_layout.py` carry the
same table; `0x06` and `0x0E` print as `unknown (0xNN)`.

The app has **no scrolling list** — 15 effects is the whole set, so `0x06` and
`0x0E` are not reachable from the app at all. `0x06` renders nothing (see §6).
`0x0E` is untested and should be assumed equally blank until proven otherwise.

**Fast vs slow wave pulse** differ in exactly one byte: body `0x53`
(`0x57` fast, `0x17` slow) — that is speed, stored per effect at
`entry[mode-1]+1` (§3), and `0x53` is mode `0x0D`'s slot. It did not change
during the later speed sweep because that sweep used mode `0x11`, whose speed
byte is `0x5B`.

---

## 5. Colour block (`op 0x8A`, 420 bytes) — one 21-byte record per effect

**The block is twenty records of 21 bytes, one per effect:**

```
record `mode` starts at 21 * mode       record[mode][0..2] = (G, R, B)
```

| offset within a record | meaning |
|---|---|
| `+0` | **GREEN** level for that effect (0–255) |
| `+1` | **RED** level |
| `+2` | **BLUE** level |
| `+3 … +20` | never seen non-zero in any sample; unknown |

So effect 1's record is `0x15/0x16/0x17` — the offsets every earlier red/green/
blue dump pointed at — but they are **solid's colour, not a global one**. That
single mistake explains a lot of history, see below.

### How it was proven (2026-10-19 app walk)

The user stepped through all 15 effects in the vendor app and saved a dump with
"colourful" **on** and **off** for each (`dump_lighting.py --save`). Pairing the
two dumps per effect shows what the app actually wrote, and the changed byte in
the colour block is always `21*mode + 1`:

| effect | changed byte | offset the record model predicts |
|---|---|---|
| `0x05` rainbow pixel changing | `0x6A` | 21*5+1 = 0x6A |
| `0x0B` rainbow wave | `0xE8` | 21*11+1 = 0xE8 |
| `0x0C` typing feedback, single key | `0xFD` | 21*12+1 = 0xFD |
| `0x0D` wave pulse | `0x112` | 21*13+1 = 0x112 |
| `0x0F` spinning | `0x13C` | 21*15+1 = 0x13C |
| `0x10` wave 2 | `0x151` | 21*16+1 = 0x151 |
| `0x11` pulse 2 | `0x166` | 21*17+1 = 0x166 |

Every one of them went `00 → FF`, and only the red byte moved, because the colour
selected in the app was red. The gap between `0x112` (mode `0x0D`) and `0x13C`
(mode `0x0F`) is 42 = 2 × 21 — exactly one skipped record, for mode `0x0E`, which
has never been observed. The lattice fits to the byte.

The same walk retires two old mysteries:

- **`0x2B` and `0x55` are not sticky flags.** `21*2+1 = 0x2B` is *pulsing's* red
  level and `21*4+1 = 0x55` is *typing feedback's* red level. They went `00 → FF`
  on 2026-10-18 when those two effects were first given red, and stayed `FF`
  because their records kept that colour. Nothing "latches".
- `0x16` (= `21*1+1`) is solid's red level, which is why an app dump can show
  `0x16=0x80` (half red, from the colour wheel) while "red" in the table below it
  reads `FF`.

### Consequences

- **Each effect remembers its own colour.** Switching effects no longer disturbs
  the colour another effect draws; `k61se_dev.set_state(colour=…)` writes the
  record of the effect that is active after the call (pass `colour_mode=` to
  paint a different record, e.g. `1` for the old behaviour).
- **Every effect that is not one of `0x03`/`0x0F`/`0x10` is colourable**: its own
  record is what the flag-off rendering draws, and `--effect-colour MODE red`
  confirmed it on the panel for all twelve on 2026-10-19. The one caveat is that
  a **black** record now really does mean a black panel (the "fourth dark state"
  in §6) — that is also the intended way to switch a single effect off. The
  three flag-ignored effects stay rainbow whatever their record says (confirmed
  in the app and again in the targeted run); the user's own sweep marks them
  "no" because they **cannot be set to a single colour at all** — there is no
  flag-off rendering to paint, not merely a flag the firmware overlooks.
- **These are 8-bit levels, not on/off flags** (§5 old evidence stands, and the
  app's colour wheel only makes sense this way). The half-red test wrote solid's
  record while solid was live, which is exactly the record solid reads — so the
  "visibly dimmer red" result is still valid for solid.
- **Rainbow is not a colour.** While the flag nibble is `7` the firmware draws its
  own rainbow and ignores the record; the record keeps its value either way.
- **The app's own colour write is captured**, and it settles the write format:

  ```
  captures/color_red/color_red_evt8_submit_out_520B.bin
    06 0a 00 00 01 00 a4 01 | <420-byte body>
  ```
  That 420-byte body is **byte-identical** to `dumps/colour_*_red.bin`, so the
  colour write path is a copy of the read path with the `0x80` bit cleared —
  no separate "apply" sequencing (the app writes lighting first, then colour).
- That also explains the app's "rainbow" colour swatch, a red herring: its body is
  `G=FF R=FF B=FF` in **solid's** record, identical to its "white" swatch. What
  actually changed the rendering was the flag nibble in the *lighting* block,
  which those colour captures did not record. `solid_rainbow_app` shows a solid
  rainbow while solid's record still read stale red — proof the record was unused.
- `presets/colour_rainbow.json` (255,255,255) has been **removed**, since picking
  all channels is not how a rainbow is selected. The rainbow is now
  `"rainbow": true` in a preset, or the `Rainbow` checkbox in the test bench.
- **Confirmed** (`k61se_gui.py --effect-colour MODE COLOUR`, vendor app closed,
  2026-10-19): painting each effect's own record red turned every effect red
  except the three that ignore the flag — the record model and the two-class
  rendering model above are the protocol's current answer. The corrected bulk
  `--audit` gave the same answer (12/15 both, 3 flag-ignored, 0 dark).

---

## 5a. Music frame block (`op 0x08`, 378 bytes)

Captured 2026-10-19 with `snoop_session.py` while the vendor app was in music
mode and playing music (`captures/music_session/`, `music_session_2/`).

The app **streams** this block at about **22 frames/s** (149 frames in 6.76 s;
typical spacing 16–30 ms, occasional 100–220 ms timer hiccups). It sends
**nothing else** during music mode — no `0x84` lighting and no `0x0A` colour
writes. The frame is the complete picture:

```
06 08 00 00 01 00 7A 01 | <378-byte body>
                            ^^^^^ = 0x017A = 378
```

Body layout: **126 cells × 3 bytes**, each `(G,R,B)` in the same channel order as
the colour records (§5). It is a **column-major LED matrix with six rows**:

```text
cell = 6*column + row + 1        (row 0-4, column 0-13; cells 1-83)
byte = 3 * cell                  (cell 6*column is padding; 84-125 unused)
```

The vendor layout file `app/K61SE-keyboard.xml` gives every key exactly this
`key_index` (e.g. Esc = 1, Tab = 2, `1` = 7, A = 9), so the mapping is not
guesswork: `key_index` *is* the 1-based cell number in this frame. K61SE uses
14 columns × 5 rows = 61 keys; the first cell of each column (0, 6, 12 … 78)
and all cells from 84 up are always zero — the firmware sizes the frame for the
largest keyboard on the platform, not for the K61SE. Decoding a captured frame
through the XML produces the expected left-to-right rainbow.

- The 21-column stride is why the frame is `21*18 = 378` bytes, not `61*3`.
- A frame of all zeros is silence — the app still sends it (36 zero frames
  before the music started in `music_session`).
- There are **no parameter bytes** in the frame: no brightness, speed or mode
  field, just per-key colour. The colours are the final values handed to the
  LEDs (up to 255); the music layer's own settings are `fore_amplitude` and
  `backb_right` in `t_musiclayer_data`.
- `music_session_2` ("turned music mode back on and increased the amplitude")
  uses the identical format and header; only the values differ, max still 255.

### Entering and leaving music mode

Captured separately as `music_entry` (52 frames, 2.4 s) and `music_exit`
(37 zero frames, then a lighting read/write):

- **Entry is just the first `0x08` frame.** `music_entry` starts with the
  stream; there is **no `0x04` write, no read and no special opcode** before
  it. The app never switches the keyboard into a "music mode" — the stream
  takes over the LED output while the selected effect stays configured
  underneath.
- **Exit is stopping the stream and re-sending the lighting block.**
  `music_exit` ends with 37 zero frames, then an `0x84` read request/reply,
  then a `0x04` write that is **byte-identical** to the reply (mode `0x11`,
  pulse 2). The mode had not changed during music; the app simply re-asserts
  the current effect once the overlay ends.
- Consequence for a driver: stream `0x08` frames to take over, stop and write a
  normal lighting block to hand control back. There is no enable/disable
  command and no music-mode enum value.
- A single `0x08` frame does **not** latch. When the stream stops, the firmware
  resumes the configured effect (hence the app's zero-frame tail and its
  lighting re-write). For static art, keep streaming the same frame; a
  one-shot write is only visible briefly.

### What the visualiser actually is

The frame is a **14-bar spectrum analyser**. Each matrix column is one frequency
band (column 0 is the left edge — Esc/Tab/CapsLock/Shift/Ctrl — and behaved as
the bass band), and each band is up to five LEDs tall, drawn bottom-up (matrix
row 4 is the bottom, row 0 the top). At low gain only column 0's bottom cell
lights; at full gain all 14 bars are present.

The two settings captured on 2026-10-19 are **not in the protocol**:

- `music_amplitude` — 362 frames over 18.6 s; DB `fore_amplitude` went
  180 → 420. Moving the slider changes *how many bars cross the threshold*
  (mean lit cells: 0.7/s at the bottom, ~39/s at the top), but never the
  per-cell level — every lit cell still peaks at 255. It is a gain/threshold
  inside the app.
- `music_brightness` — 255 frames over 11.9 s, moving the **Background
  Brightness** slider: no non-`0x08` writes at all. With Background Mode off
  (`backmode = 0`) the stream is pure foreground and does not change. The
  setting only matters once a background is composited, which has not been
  captured.

### Colours are per cell, not a mode

`0x08` has no palette: every cell carries its own RGB triple. The vendor app
paints a gradient across the bars, but that is not a firmware "rainbow mode":

- The app's captured palette moves purple/magenta (bass, column 0) through red
  and orange to yellow/green (treble, column 13). The base triples are in
  `k61se_music_live.py` (`APP_PALETTE`). The optional `rainbow` palette is the
  classic cyan → green → yellow → orange gradient.
- Within a bar the top cell is brightest and the bottom is about half as
  bright. That is the app's vertical gradient, not a per-cell level from the
  firmware.
- Any cell can be any colour, and any key can be lit independently. The
  `test` pattern (Esc/Tab/Caps/Space) is a live demonstration;
  `k61se_music_live.py --colour RRGGBB --flat` paints every lit cell one
  colour. `k61se_keys.py` uses the same frame for static per-key art, with no
  audio involved at all.
- `k61se_music_live.py` drives this end to end from the Linux speaker or
  Bluetooth monitor: real audio → FFT → frames, with the vendor app closed.
  Confirmed on the panel 2026-10-19. Designs (`bars`, `mirror`, `peak`,
  `pulse`) and custom palettes are chosen entirely in the host; the wire format
  stays the same.
- What remains app-side is the **choice** of palette and the FFT, not a hidden
  protocol feature.

Still untested: the **foreground** Brightness control (a different slider) and
whether `op 0x88` reads the frame back. The channel order is **`(G,R,B)`**,
confirmed on the panel 2026-10-19: `k61se_music_live.py --colour FF0000 --flat`
lit red, not green.

---

## 5b. Keymap and macro blocks (`op 0x03`/`0x83`, `0x05`/`0x85`)

**Status 2026-10-20: keymap write/read confirmed on K61SE hardware.** The
vendor app's own `0x03` writes and `0x83` readbacks were captured in
`captures/keymap_write/` and `captures/keymap_switch_back_to_default/`; the
readback is the same shape as the write. The macro block is still statically
recovered only. The wired `CDevG5KB` vtable in `app/KBAccess.dll` uses the same
command family as the wired AULA F75, whose protocol is already documented and
tested in `/home/tony/src/aula-f75-linux`.

| block | write | read | sub | body |
|---|---|---|---|---|
| key matrix / layers | `0x03` | `0x83` | layer | `0x1F8` (126 × 4 bytes) |
| on-keyboard macros | `0x05` | `0x85` | `0` | variable, ≤ `0x1000` |
| per-key colour/game matrix | `0x06` | `0x86` | profile | `0x17A` (378 bytes) |

The first two are the F75-compatible remap/macro commands. `0x06` is the
vendor's `SetKeyColor`/`ReadKeyColor` path: 378 bytes, the same size as one
music frame, but selected by a profile byte rather than streamed like `0x08`.
It is listed for completeness; it is not used by the current lighting tools.

The command evidence is in the wired `CDevG5KB` vtable in `KBAccess.dll`:
`+0x1C`/`+0x20` keymap write/read, `+0x24`/`+0x28` macro write/read,
`+0x2C`/`+0x30` 128-byte LED block, `+0x34`/`+0x38` 378-byte key colour,
`+0x3C`/`+0x40` 420-byte colour table, and `+0x44` realtime `0x08`. The
3632/wireless vtable is a different transport (`0x13` packets) and is not used
by these K61SE tools.

The captured app sequence is **read, then write**:

```text
06 83 00 00 01 00 F8 01   read request, 504-byte keymap
06 83 00 00 01 00 F8 01   reply, 504-byte body (512-byte USB transfer)
06 03 00 00 01 00 F8 01   write, 504-byte body
```

`captures/keymap_write/` changed only key_index 35 (Space):
`00 00 00 2C` -> `00 00 00 08` (HID E). The prior readback was the 60/60
factory XML map and is saved as `captures/keymap-stock.bin` (504-byte body).
`captures/keymap_switch_back_to_default/` then changed key_index 17 (Left Alt):
`00 04 00 00` -> `00 00 00 08` (HID E), and re-sent the same body; it did not
restore Left Alt. These are useful protocol evidence, not a recommended remap.
The later Fn+Tab snapshot in `captures/golden-reset/` supersedes the app
readback as the canonical reset baseline: it has Fn at cell 83 and the
duplicate `\` at reserved cell 75. Its raw layer-0 body is saved as
`captures/keymap-reset.bin`. Use
`python3 k61se_keymap.py --restore-stock --yes` to put that body back, with
the pre/post evidence described in the `k61se_keymap.py` tool section (§7).

### Keymap (`0x03` / `0x83`)

`KBAccess.dll`'s `SetKey` and the vtable read method pass `0x1F8` bytes with
`sub = layer`. The body is one 4-byte action per matrix cell:

```
offset 4 * key_index
```

The XML's `key_index` is **1-based and is the keymap cell number directly**.
Captured app writes use `matrix[4 * key_index]`; slot 0 is reserved and is not
a physical key. The older app readback had a duplicate Esc in slot 0; the
Fn+Tab reset snapshot leaves it zero. Esc is 1, Tab is 2, Space is 35, and Fn
is 83 on this board. Only cells for physical keys are used; the other cells in the 126-cell
table stay zero. The earlier `key_index - 1` assumption was off by one and was
corrected against the 2026-10-20 app captures.

Action encodings (same as F75):

| bytes | meaning |
|---|---|
| `00 00 00 00` | disabled / no action |
| `00 mods 00 usage` | keyboard key; `mods` is the HID modifier bitmap (bit 0 = Left Ctrl … bit 7 = Right Win) |
| `02 00 usage_hi usage_lo` | consumer/media key (16-bit usage) |
| `03 mode count macro_index` | macro key; `macro_index` is 0-based |
| `0D 00 00 00` | Fn (vendor special; XML code `0xFA`) |
| `0D 01 00 00` | Fn2 (vendor special; XML code `0xFB`) |

`SetKey` also accepts a profile index below 4 and a layer below the SDK's layer
count (1–8). The K61SE XML advertises `macro="1"` and `dks="1"`, and its
`fn_layer` is 0. The layer count is host-side state in `KBAccess`; it is not
part of this packet.

### Macros (`0x05` / `0x85`)

The whole macro area is one block, not one command per macro. Layout:

```
N × [ addr u16, size u16 ]   table at the start of the body
blob 0                       at addr 0, normally 4 * N
blob 1                       immediately after blob 0
...
```

`addr` is relative to the start of the body. `SetMacro` relocates every table
entry by `4 * N`, concatenates the table and the macro data, and sends the
result with `0x05`. It refuses an empty table or a total above `0x1000`.
`ClearMacro` zeroes a 400-byte table (up to 100 entries) and a 4096-byte data
buffer. A body over 512 bytes is split into chunks just like a read; byte 4 is
the total chunk count and byte 5 is the chunk index.

Each blob is:

```
name_len u8 | name (UTF-16LE) | event 0 | event 1 | ...
```

The F75 format stores `name_len` as the **byte** length of the UTF-16LE name.
`KBAccess`'s `AddMacro` looks inconsistent here: it calls `wcsnlen` (which
returns characters), stores the low byte, and then copies only that many bytes.
The probe therefore tries both interpretations and picks whichever makes the
event stream line up; its output says `bytes`, `chars`, or `truncated`.

Each event is four bytes:

```
[(type << 4) | up << 7 | delay[19:16], delay[15:8], delay[7:0], code]
```

* type 0 = keyboard key, `code` is a HID usage (`0x04` = A, `0x29` = Esc)
* type 1 = modifier, `code` is `0xE0`–`0xE7`
* type 2 = mouse button
* type 3/4 = mouse X/Y
* type 5 = wheel
* `up` means key/button release; `delay` is milliseconds to wait after the
  event

A macro key in the keymap uses `03 01 count macro_index` for “play macro
`macro_index` `count` times”; other mode values are not yet confirmed on the
K61SE.

### Order of writes

The F75 applies the keymap first and the macro table second. `KBAccess` exposes
the same two operations as separate calls (`SetKey`, then `SetMacro`), but the
K61SE call order has not been observed. The keymap write/read pair is confirmed
by the 2026-10-20 captures. The macro payload write/readback matched on
2026-10-20 for one F75-style macro; macro playback and key binding are still
being tested.

### Read-only probe

`k61se_macro.py` is the hardware check. It has no write path:

```bash
python3 k61se_macro.py --probe
python3 k61se_macro.py --probe --layers 0,1,2,3 --macro-size 4096 --save
python3 k61se_macro.py --echo-test
```

If a probe returns all zeros, run `--echo-test`: it fills the read request
payload with `A5` instead of zeros, so an echoed/ignored request (body still
`A5`, or only an 8-byte header) is distinguishable from a real zero-filled
reply. It still only sends read requests.

It reads `0x83` for each requested layer and `0x85` for the macro area, prints
the decoded keymap and macro events, and saves raw bodies plus `probe.json`
under `captures/macro-probe/` when `--save` is used. `--macro-size` can go up to
4096; the device layer splits that into 512-byte chunks using the frame's
chunk-count/chunk-index bytes. Close the vendor app first — the probe only
reads, but the app competes for report 6.

Offline decoding of an existing dump needs no keyboard:

```bash
python3 k61se_macro.py --decode-keymap captures/macro-probe/keymap-layer0.bin
python3 k61se_macro.py --decode-macros captures/macro-probe/macros-512.bin
```

Expected signs that `0x83` really is the keymap: cell 1 is
`00 00 00 29` (Esc), and the XML's letters/numbers match their declared cells.
Slot 0 may be zero in a Fn+Tab reset snapshot or a duplicate Esc in the older
app readback. The probe prints the Esc check, the number of XML matches, and a
plausibility count instead of assuming success.

### One-shot macro write experiment

`k61se_macro_write.py` is separate from the read-only probe. It builds one
macro using the F75/KBAccess layout, writes it with `0x05`, reads the area back
with `0x85`, and compares the bodies. It does **not** bind the macro to a key
and does not touch the keymap.

```bash
python3 k61se_macro_write.py --name test1 --text abc --dry-run
python3 k61se_macro_write.py --name test1 --text abc --yes
```

The body layout is:

```text
N x [addr u16, size u16]
name_len u8, UTF-16LE name, events...
event = [(type<<4)|(up<<7)|delay[19:16], delay[15:8], delay[7:0], code]
```

`type` 0 is a keyboard key, 1 a modifier (`0xE0..0xE7`), and 2/3/4/5 are mouse
button/X/Y/wheel. The first blob starts at `4 * N`; the table addresses are
absolute from the start of the body. Name length is the F75 byte count by
default; `--name-length chars` tries the KBAccess character-count form.

The first hardware run on 2026-10-20 used `--name test1 --text abc --yes`
(write body 39 bytes, table `04 00 23 00`, name length 10). The `0x85` readback
prefix matched exactly, so the F75-style payload layout is now write/readback
verified for this case. Playback and key binding still need a separate test.
The tool saves `pre-*.bin`, `write-*.bin`, `post-*.bin`, and `result-*.json`
under `captures/macro-write/`, and leaves the macro unbound. If a future
readback differs, hold Fn+Tab for 5 seconds.

The next test binds macro index 0 to a safe key. `Menu` is key 59 and is a good
choice; `macro:0` is the first macro even though the decoder labels it
“Macro 1”:

```bash
python3 k61se_keymap.py --set 'Menu=macro:0' --yes
# press Menu in a text editor: it should type "abc"
python3 k61se_keymap.py --set 'Menu=Menu' --yes
```

The keymap cell becomes `03 01 01 00` (type 3, mode 1, count 1, index 0).
Avoid binding Fn: it is the vendor-special cell 83 and its Fn-layer behavior is
not captured. There is no separate F1–F12 row on this 61-key board.

---

## 6. The panel gate model — why the backlight can go dark and stay dark

```
panel lit  ==  (mode 0x0A != 0)
             AND (firmware enable flag == 1)
             AND (the firmware knows how to draw that mode)
                      │                          │
                host-controlled            hotkey only
                (op 0x04 write)          (no software path)
```

**Three independent gates, and three different "offs" that are not interchangeable:**

| | written by | reversible from software? | keeps the effect? | visible in the block? |
|---|---|---|---|---|
| **software off** (`0x0A = 00`) | app / `k61se_power.py off` | ✅ `on` restores it | ❌ overwrites the mode | ✅ reads `00` |
| **hotkey off** | the keyboard itself | ❌ **no** | ✅ remembers exactly | ❌ block stays `04` |
| **unknown mode** (`0x0A = 06`) | `k61se_power.py mode 0x06` | ✅ write a real mode | ❌ overwrites the mode | ✅ reads `06` |
| **black colour record** (`entry+1` low nibble `0`, record `00 00 00`) | app / our tools | ✅ write a colour or the nibble back to `7` | ✅ | ✅ reads `05 50`, e.g. `mode=0x11 slot[16]=05 50` |

The last row used to be documented as "no colour renderer" (see §3 and
`colour_mode_audit.md`), on the strength of an audit that wrote *solid's* colour
record while other effects were live. Since the walk showed each effect has its
own record (§5), the honest reading is that those effects had a **black** record.
That is now confirmed: `--effect-colour` reddened all twelve colour-capable
effects, so the gate is

```
mode != 0  AND  enable flag  AND
    (the effect ignores the flag  OR  rainbow flag set  OR  its own record is not black)
```

— i.e. clearing the flag is a fourth way to a dark panel exactly when the record
is unset, and it is a deliberate way to switch one effect off. Three effects
(`0x03`, `0x0F`, `0x10`) ignore the flag entirely, so for them there is no dark
state to reach this way.

Evidence for the hotkey row:

- Five dumps taken before / during / after a hotkey off–on cycle are
  **byte-identical** (sha256 `a7684a74f58d`), all reading mode `0x04`, while the
  panel was dark.
- A **real** mode transition (`0x04 → 0x00 → 0x04`, block verified at each step)
  left the hotkey-darkened panel **dark**.
- The **vendor app cannot relight it either** — it can still change colours while
  the panel is dark, it just cannot re-enable it.

So the hotkey's enable flag is private to the firmware and simply is not exposed
to the host. **Only the hotkey turns the panel back on.**

Evidence for the unknown-mode row (2026-10-19):

- `mode 0x06` wrote cleanly and read back as `0x06`, and the panel went **dark**.
- The lighting hotkey **appeared dead**: it only toggles the enable flag, and
  with `0x06` selected there is nothing to re-enable. Two dark causes, one
  visible symptom.
- Writing a real mode restores the panel, so it is not a bricked state.

Practical consequences:

- If you want a light you can toggle from software, use the tool's `off`/`on`,
  not the hotkey.
- **Never write a mode value the app does not use.** `0x06` and `0x0E` are the
  only gaps in `0x01`–`0x11`; `0x06` draws nothing and `0x0E` is untested.
- `k61se_power.py` now refuses to *remember* an unknown mode, because storing
  one made `off` followed by `on` restore a dark keyboard.

---

## 7. Tooling

The read-only tools need `sudo` only because `/dev/hidraw*` is normally
root-owned. On this machine a uaccess ACL already grants the desktop user
read-write on the K61SE nodes (`getfacl /dev/hidraw4` → `user:tony:rw-`), so the
test bench runs unprivileged. `dump_lighting.py` still wants `sudo` for its
*saves*, because `dumps/` was created by earlier root runs
(`drwxr-xr-x root root`) — or `chown` it once.

### `dump_lighting.py` — universal read-only reader

```bash
sudo python3 dump_lighting.py --manual             # RECOMMENDED: you name each theme
sudo python3 dump_lighting.py                      # lighting + colour
sudo python3 dump_lighting.py lighting             # just the 128-byte block
sudo python3 dump_lighting.py --save green         # label a one-off save
sudo python3 dump_lighting.py --op 0x02 --sub 1 --size 6
sudo python3 dump_lighting.py --sweep              # compact content browser 0x00..0x1F
sudo python3 dump_lighting.py --passive            # does a bare GET see the app's writes?
sudo python3 dump_lighting.py --watch              # passive auto-capture (never sends)
sudo python3 dump_lighting.py --watch --active     # request-polling: app must be CLOSED
sudo python3 dump_lighting.py --app-walk           # guided: app colours each effect, we read
```

- Sends **only** read requests; never writes.
- `--save [LABEL]` writes `dumps/<block>_<timestamp>[_<label>].bin` and
  auto-diffs against the previous dump of that block. Saves never collide
  (a same-second second save gets a `_2` suffix).
- **`--manual` is the recommended way to sweep the app's themes.** It reads
  nothing until you press Enter, and opens/closes the device around each
  capture, so between themes the tool holds nothing and sends nothing. Each
  capture is the same single read `--save` always did — safe with the app open.
  You type the label; Enter alone accepts the auto label; a duplicate is flagged.
- **`--app-walk` is the guided version for the colour question.** It walks the
  fifteen effects in order, naming each one (`[4/15] effect 0x04 typing
  feedback`). For every effect you select its colourful version in the app and
  press Enter; the tool reads lighting + colour and saves a labelled pair; you
  then flip the app's "colourful" toggle off and press Enter again. It diffs the
  two snapshots byte by byte, prints what moved, and writes
  `app_effect_walk.md` with a verdict per effect:
  `has a colour renderer`, `the app kept the flag on`, `the firmware ignores
  it`, `the firmware draws nothing`, or — the interesting one — `the app
  changed the MODE 0x08 -> 0x0A`. `s` skips a step, `q` finishes early,
  `--start 0x0B` resumes. Read-only; the app stays open. `dumps/` grows by up to 60
  files (15 effects × 2 renderings × 2 blocks).
- `--watch` (passive) auto-captures instead. It only ever GETs report 6 and
  never sends a request, so it cannot disturb the app — but it only sees the
  block the app *last wrote*, and since report 6 holds one report at a time it
  can miss one of the two blocks a theme change writes.
- ⚠️ `--watch --active` request-polls both blocks, and that **does** break the
  app: sustained polling of shared report 6 stops the vendor app from finding
  the keyboard. Use it only with the app closed.

### `review_dumps.py` — diff the dumps

```bash
python3 review_dumps.py                    # list saves
python3 review_dumps.py -l green           # list label "green"
python3 review_dumps.py -d                 # diff the two most recent
python3 review_dumps.py -d red green       # by label
python3 review_dumps.py -d lighting:red lighting:green
python3 review_dumps.py -d 3 5             # by list index
python3 review_dumps.py -s green           # annotated hex dump
python3 review_dumps.py -v                  # what VARIES across all dumps
python3 review_dumps.py -v lighting         # ... just the lighting block
python3 review_dumps.py -v colour theme_*   # ... just matching labels
```

- Diffs name known offsets and show entry-table changes as raw byte pairs with a
  speculative RGB565 read-out.
- **`-v` is the one to reach for when scanning a batch of themes/colours.** It
  prints a state summary per dump, then only the offsets that actually differ,
  with their distinct values and example labels. A byte that never varies is not
  something a theme controls. (It also proves things stay constant: `-v lighting
  red green` reports the two lighting blocks are byte-identical, so colour lives
  purely in the colour block.)

### `snoop_session.py` — raw USB capture (interactive)

```bash
sudo python3 snoop_session.py
# prompts: "Enter action tag (e.g. 'off', 'static_white') [q to quit]:"
```

- Captures the **whole bus**, one action per tag, into `captures/<tag>/`.
- Writes a full `.raw` usbmon record per event plus a Markdown report, and
  appends to `captures/SESSION_INDEX.md`.
- Enlarges the usbmon ring to 64 MiB (override with `USB_MON_RING_SIZE`) because
  the ~300 KB default silently drops events during app startup.
- **Filters out**: ISO and Bulk transfers, and **all INTR IN** (keyboard/mouse
  polling). INTR **OUT** is kept.

### `review_captures.py` — decode what the app actually wrote

```bash
python3 review_captures.py              # one row per capture folder
python3 review_captures.py -i           # list the folders and their event counts
python3 review_captures.py -d wave_colour        # one folder, event by event
python3 review_captures.py -d wave_colour -v     # + every byte that moved
```

- Reads the `.bin` files `snoop_session.py` saved and decodes each 520-byte
  report with the shared layer, so a capture is read the same way a dump is:
  `evt4 OUT write lighting mode=0x0B rainbow wave slot[10]=05 57 speed fast (5/5), rainbow`,
  `evt8 OUT write colour G/R/B=00/FF/00 …`.
- Read *requests* (`op 0x84`/`0x8A`, the zero-filled OUT report) are labelled as
  such rather than decoded as state, and IN replies are shown too, so the
  request/reply pairs stay legible.
- `-v` diffs each write against the previous write of the same block in that
  folder: that is how you see exactly which byte the app moved. A
  `(byte-identical to the previous … write)` line means the app re-sent the same
  state.
- Music captures are a stream of `op 0x08` frames (§5a). They are shown as
  `music  31/126 cells lit  peak=255`; no per-byte diff is printed because every
  frame changes. Zero-frame runs are silence.
- **Read-only and device-free** — it never opens the keyboard, so it is safe to
  run while the app is still up.
- This is the tool for the open question in §11.4: capture the app while it puts
  each pattern into its colour mode and the diff shows whether it clears the flag
  (and also blanks the panel), or writes a mode/byte we have never seen.

### `k61se_power.py` — power / mode control

```bash
sudo python3 k61se_power.py status
sudo python3 k61se_power.py off        # write mode 0x00  (reversible)
sudo python3 k61se_power.py on         # restore the remembered mode
sudo python3 k61se_power.py toggle
sudo python3 k61se_power.py mode 0x02  # explicit mode
sudo python3 k61se_power.py reset      # emergency software reset
```

- Read-modify-write of the whole 128-byte block, so colours/brightness survive.
- Caches the last non-zero mode in `.k61se_state.json` (created root-owned by
  `sudo` runs) so `on` can restore it after a software off.
- Verifies every write with a **read-back**, and warns if another process holds
  the device.
- If the block already reads the target mode it does **not** write, and says so
  (that is the hotkey-dark case, which it cannot fix).
- `--help` is inert and prints usage; unknown actions are rejected. The old
  off/dark/on smoke test now requires the explicit `test` action.

### `k61se_dev.py` — shared device layer (library, no CLI)

Generic report-6 read/write for any block, plus `set_state()` /
`read_state()`. Every write is a read-modify-write and every apply is read back.

```python
import k61se_dev as dev
dev.read_state()                                    # {'lighting': …, 'colour': …}
dev.set_state(effect=0x0B, brightness=5, speed=5, colour=(255, 0, 0))
```

- Frame construction is byte-for-byte what the vendor app sends (verified against
  `captures/color_red/`), for both blocks and both directions.
- A short/empty feature report is retried (3 attempts) because report 6 is
  single-slot and both the app and a re-enumerating node can steal it. When the
  caller does not pass a handle, each block op opens afresh, so a hidraw node that
  moved is picked up rather than returning `short reply … got 0` forever (§9).
- `read_body_chunks()` reads blocks larger than 512 bytes (macro tables) by
  setting the frame's chunk-count and chunk-index bytes; `read_body()` remains
  the one-chunk path used by lighting, colour, and keymap.
- Device discovery and the other-writer check are **imported from
  `k61se_power.py`**, not copied, so the two cannot drift.
- This is where a new tool should start instead of re-deriving the frame.

### `k61se_gui.py` — test bench (GUI + headless)

```bash
python3 k61se_gui.py                    # the GUI
python3 k61se_gui.py --read             # print the current state
python3 k61se_gui.py --list             # list the presets
python3 k61se_gui.py --apply presets/0b_rainbow_wave.json
python3 k61se_gui.py --matrix --delay 1 # every effect rainbow + every colour of every colour-capable effect
python3 k61se_gui.py --audit            # both renderings of every effect, asks what you saw
python3 k61se_gui.py --audit --audit-start 0x04  # resume an interrupted audit at 0x04
python3 k61se_gui.py --probe-nibbles 0x0B  # one effect, parameter byte 0x50..0x57
python3 k61se_gui.py --effect-colour 0x0A red  # paint that effect's OWN record, then watch the panel
python3 k61se_gui.py --make-presets     # (re)write the preset files
python3 k61se_gui.py --selftest         # device-free checks, no keyboard needed
```

- No `sudo` needed here (see the ACL note above).
- Effect dropdown covers `0x00`–`0x11` with the names from the sweep; `0x06` and
  `0x0E` are marked *draws nothing*.
- Brightness and speed are 1–5, colour is R/G/B 0–255 with a hex box and the
  desktop colour wheel, and a **`Rainbow` checkbox** selects the firmware's own
  rainbow (the flag nibble in §3) — while it is ticked the colour widgets are
  greyed out, because the firmware ignores them. `Apply` writes lighting then
  colour, then reads back.
- **The tick is per effect, not global**, because the flag lives in that effect's
  own slot. Next to the dropdown the bench prints the slot the selected effect
  currently stores (`stored 05 57 · fast (5/5) · rainbow`), and picking a
  different effect in the list re-reads that readout and the tick from the last
  block fetched — no extra device traffic.
- **The tick is enabled for every effect that draws its own record** — all of
  them except the three that ignore the flag (`0x03`, `0x0F`, `0x10`, which say
  `always colourful — the flag has no effect here` and keep whatever nibble is
  stored). While the tick is off the colour widgets are live. The readout warns
  when the selected effect is in single-colour mode but its record is **black**,
  because that draws nothing; `do_apply()` refuses exactly that combination
  (flag off with no colour), so a click cannot blank the panel by accident, but
  deliberately applying `colour_off_black` to an effect still can — that is the
  documented way to switch one effect off.
- `Sweep all` walks the preset folder at 1.5 s a step so you can watch the panel;
  `Stop` halts. `--matrix` is the same idea headless: every effect once with
  `rainbow: true`, then every effect that draws its record once per colour with
  the flag off (16 + 12×12 = 160 writes, ~3 min at the default 1 s). The three
  flag-ignored effects are skipped for the colour half — they would render
  identically each time.
- Colour is written as levels based on the 8-bit hypothesis in §5 — this is the
  tool that can confirm or kill it (`colour_half_red` = (128,0,0), with the
  `Rainbow` box **unticked**, since a rainbow hides the channels entirely).
- `--selftest` re-checks the frame bytes against the vendor app's captured
  writes, the speed scale, the rainbow nibble against three pairs of real dumps,
  `set_state()` against an in-memory device, the hex helpers and every preset
  file. It never opens the device, so it is the thing to run after editing any of
  this.
- `--audit` is the one test that needs eyes: it writes every effect twice (flag
  on, then flag off plus red `(255,0,0)` written into **that effect's own**
  record) and asks what the panel did — `y`, `n` or `d` for dark — then writes
  `colour_mode_audit.md` and **restores the last effect's rainbow rendering**, so
  a run cannot leave a blank keyboard behind. The block is identical either way,
  so the panel is the only instrument. Its first run is retracted (§3/§5) because
  it wrote solid's record every time; the targeted `--effect-colour` run has
  since confirmed what a corrected run should show, and the **corrected run on
  2026-10-19 15:12 reproduced it exactly** (12 both, 3 flag-ignored, 0 dark), so
  this is now the bulk cross-check rather than the decisive test. A device error
  no longer aborts it:
  each write is retried once, and if it still fails the run stops cleanly,
  restores the last effect's rainbow state, writes a partial report, and exits 1.
  `--audit-start 0x04` resumes at a given effect, so a run that dies mid-way does
  not cost the answers already given.
- `--effect-colour MODE COLOUR` is the per-record test that settled §5. It sets
  the effect, clears the flag and writes **that effect's own 21-byte record**
  (colour accepts `red`, `#00FF80`, `00FF80` or `255,128,0`), then prints the
  offsets it moved. Run with the vendor app closed, one effect at a time. ✅ done
  2026-10-19: all twelve colour-capable effects drew the colour; `0x03` stayed
  colourful as the control.
- `--probe-nibbles MODE` is a separate line of enquiry: it holds one effect and
  writes its parameter byte as `0x50`, `0x51`, … `0x57` (speed fixed at 5, only
  the low nibble moves), asking after each what the panel did, then restores
  `0x?7`. Every effect we have ever captured used only `0` and `7`; if `1`–`6`
  turn out to be other built-in schemes that is another route to colour. Writes
  `nibble_probe.md`.

### `k61se_music_audio.sh` — speakers into the app's music mode

```bash
./k61se_music_audio.sh up           # create the "music_probe" capture source
./k61se_music_audio.sh test         # beep + record; prints the captured level
./k61se_music_audio.sh status       # source, default input, Wine's source list
./k61se_music_audio.sh default-on   # make it the system default input (reversible)
./k61se_music_audio.sh down         # remove it again
```

- Audio only: it never touches `/dev/hidraw*`.
- Creates a `pw-loopback` whose capture side is the speaker's monitor and whose
  playback side is an `Audio/Source`, so the music playing on the speakers
  becomes an input. See §10b for why `stream.capture.sink=true` matters.
- `default-on` saves the previous default source and `default-off` restores it;
  the state file lives in `$XDG_RUNTIME_DIR`, so a logout forgets it (safe).
- `status` lists the sources as `pipewire-pulse` presents them — the same list
  `winepulse` enumerates for the app.

### `k61se_input.py` — read key presses (read-only)

```bash
python3 k61se_input.py                 # press keys; Ctrl-C to stop
python3 k61se_input.py --raw           # also print the 8-byte HID report
python3 k61se_input.py --seconds 30
python3 k61se_input.py --list-keys     # HID code -> key_index mapping
python3 k61se_input.py --device /dev/hidraw4
```

- Reads the ordinary **boot-keyboard interface 0**, not the vendor report-6
  interface. It auto-detects the hidraw node by VID/PID and the standard
  keyboard report descriptor, so replugging the keyboard is fine.
- Report layout: one modifier bitmap, one reserved byte, then six HID keyboard
  usage IDs. The XML's `code` field is already that HID usage, so key events map
  straight to `key_index` (e.g. `0x04` -> A -> index 9, `0xE1` -> left Shift ->
  index 4).
- **Read-only:** it never opens the vendor interface, never writes lighting,
  and does not consume evdev events — normal typing still works. It can run
  while the vendor app is open.
- `Fn` is handled inside the keyboard firmware and may not appear in the
  standard report. Ordinary keys and all modifiers do.
- This is the input side of a custom typing-feedback effect; `k61se_keys.py`
  can paint the same `key_index` values in a transient `0x08` frame.

### `k61se_arrows.py` — host-side sticky arrow layer

```bash
./install_udev.sh                         # one-time; replug afterward
python3 k61se_arrows.py --print-mapping
python3 -B k61se_arrows.py
python3 -B k61se_arrows.py --window-ms 350
python3 -B k61se_arrows.py --single-tap none
# Before installing the rule: sudo python3 -B k61se_arrows.py
```

- Grabs the K61SE evdev keyboard node and creates a uinput virtual keyboard. It
  never writes firmware or the vendor interface.
- **Double-tap CapsLock** toggles the sticky layer. The default four Fn-arrow
  keys become:
  - `/ ?` -> Up
  - Right Alt -> Left
  - Menu/Compose -> Down (the K61SE Menu key is Linux `KEY_COMPOSE` 127 on
    this kernel, not `KEY_MENU` 139)
  - Right Ctrl -> Right
- A **single** CapsLock tap still emits CapsLock after the double-tap window
  (default 300 ms). `--single-tap none` makes a single tap do nothing.
- Normal keys and modifiers are forwarded. The daemon tracks the code emitted
  on key-down, so toggling the layer while a key is held still releases the
  correct key.
- `--device` overrides the event node; the default is
  `/dev/input/by-id/usb-BY_Tech_KEMOVE_K61-event-kbd`.
- It needs read access to the event node and write access to `/dev/uinput`.
  The AULA F75 udev rule only tags `hidraw`, not `input` event nodes, so this
  repo adds `packaging/70-kemove-input.rules`. Run `./install_udev.sh` once and
  replug the keyboard. `/dev/uinput` already has a user ACL on this machine.
  Before installing the rule, run the daemon with `sudo` for a test.
- `Ctrl-C` releases the grab and destroys the virtual keyboard. Only the K61SE
  is affected; another keyboard remains normal.
- It only works while the daemon runs, and not in the BIOS. The physical
  CapsLock LED is controlled by the host and may not mirror the virtual layer.

### `k61se_typing.py` — custom host-side typing feedback

```bash
python3 k61se_typing.py --dry-run --echo          # read keys; no LED writes
python3 k61se_typing.py                           # neon storm (default)
python3 k61se_typing.py --mode storm --rainbow
python3 k61se_typing.py --mode hold --colour 00FFFF
python3 k61se_typing.py --mode trail --rainbow
python3 k61se_typing.py --mode ripple --colour FF00FF --ripple-life 1.2
python3 k61se_typing.py --seconds 60 --echo
```

- Reads interface 0 with `k61se_input.py` and writes the transient `0x08`
  overlay to interface 1. The vendor app must be closed; it is a lighting
  writer. It restores the captured lighting block on exit.
- `storm` (default) is the showcase: every press makes a coloured core bloom,
  a diamond shockwave, and three sparks; fast typing builds energy, keeps a dim
  moving nebula alive, and every fifth press in a 1.5 s window fires a white
  wave across the whole board. `hold` lights a key only while held. `trail`
  fades it after release. `ripple` keeps the held key lit and spawns an
  expanding diamond ring.
- `--rainbow` colours by `key_index`; `--random` gives each press a new colour;
  `--colour` fixes one colour (storm is dynamically coloured by default).
- `--fade`, `--ripple-life`, and `--ripple-speed` tune the animation.
- `--echo` prints key names and `key_index` values, but writes nothing to disk.
- `--dry-run` still reads the keyboard, so it exercises detection without
  opening the vendor interface or sending LED frames.
- The `0x08` frame is a complete 14 x 5 overlay, not a layer: unlit keys are
  black while the tool runs. It replaces the firmware effect for the duration
  and the effect returns when the stream stops.
- If a USB write fails mid-stream, it tries to reopen the lighting interface and
  continue. The previous `hid_error is not implemented yet` message means the
  binding could not supply a USB error string; it is not a frame-format error.
- `--input-device /dev/hidrawN` overrides auto-detection. `--no-restore` leaves
  the effect to return naturally when the process exits.
- Fn may not appear because the keyboard firmware handles it internally.

### `k61se_macro.py` — read-only keymap/macro probe

```bash
python3 k61se_macro.py --probe
python3 k61se_macro.py --probe --layers 0,1,2,3 --macro-size 4096 --save
python3 k61se_macro.py --echo-test
python3 k61se_macro.py --decode-keymap captures/macro-probe/keymap-layer0.bin
python3 k61se_macro.py --decode-macros captures/macro-probe/macros-4096.bin
```

- **Read-only:** it only sends `0x83`/`0x85` read requests. It cannot write a
  keymap, macro, lighting, or colour block.
- `--echo-test` fills the request payload with `A5` and reports whether the
  reply is the request echoed back, an 8-byte header only, a real zero-filled
  block, or actual data. Use it when a normal probe returns all zeros.
- Reads the XML key layout from `app/K61SE-keyboard.xml` and labels matrix cells
  with the physical key names. The XML `key_index` is the cell number directly
  (`offset = 4 * key_index`); slot 0 is reserved and Esc is cell 1.
- Decodes keyboard, consumer, macro, Fn/Fn2, and disabled actions, plus macro
  names and 4-byte macro events.
- `--layers` defaults to `0,1`; `--layers all` reads 0–7. Unknown layers may
  return empty data, so start with the defaults.
- `--macro-size` defaults to 512. Use `4096` for a complete macro area; the
  shared device layer splits it into 512-byte chunks.
- `--save` writes raw `keymap-layerN.bin`, `macros-N.bin`, and `probe.json`
  under `captures/macro-probe/` (not the root-owned `dumps/` directory).
- `--decode-keymap` and `--decode-macros` work on saved bodies and never open
  the keyboard, so they are safe to run while the app is open.
- The vendor app must be closed for a clean `--probe`: the probe only reads,
  but report 6 holds one reply at a time and the app consumes them.
- The protocol is recovered statically (§5b). If the probe shows no keymap,
  do not assume the command is wrong first: check that another program is not
  consuming report 6, then retry. The confirmed `0x03` write path lives in
  `k61se_keymap.py`; this probe stays read-only.

### `k61se_keymap.py` — explicit keymap read/remap/restore

```bash
# Read-only decode of the live keymap
python3 k61se_keymap.py --read

# Offline: decode a saved body, or show a remap without touching the device
python3 k61se_keymap.py --read --base-file captures/keymap-reset.bin
python3 k61se_keymap.py --set Space=B --dry-run \
  --base-file captures/keymap-reset.bin

# Explicit writes: require --yes and read back
python3 k61se_keymap.py --set Space=B --yes
python3 k61se_keymap.py --set Alt@17="Left Alt" --yes
python3 k61se_keymap.py --restore-stock --yes
```

- Separate from `k61se_macro.py` on purpose. This is the only tool with a
  keymap write path.
- Uses the confirmed pair from §5b: read `0x83`, write `0x03`, both with a
  504-byte body and `sub=0`. The write is always the full map, not a diff.
- `--read` only sends the read request. `--read --base-file` and
  `--set ... --dry-run --base-file` are fully offline.
- `--set` builds the map from `--base auto|stock|device` or `--base-file`.
  `auto` uses the live map when it looks populated, otherwise
  `captures/keymap-reset.bin`; a factory-empty all-zero map cannot be used as
  a base. A heavily customised map that fails the factory-layout check needs
  an explicit `--base device --force` (or `--base-file FILE --force`).
- Key names come from `app/K61SE-keyboard.xml`. Duplicate names such as Alt
  need a cell, e.g. `Alt@17` and `Alt@53`. Space is 35, Left Alt is 17, Fn is
  83, and slot 0 is reserved.
- Actions: `B`, `Space`, `F1`, `Left Shift`, `key:0x05`, `consumer:Mute`,
  `macro:INDEX[,MODE[,COUNT]]` (index 0-based; payload written/read back by
  `k61se_macro_write.py`), `fn`, `fn2`, `raw:00 00 00 05`, and `none`.
- `--restore-stock` refuses a stock file that does not match the XML factory
  map (60/60 for the captured baseline). It overwrites the whole map, so use
  it deliberately; that is the recovery path for the accidental Left Alt=E
  remap.
- Every write saves `pre-*.bin`, `write-*.bin`, `post-*.bin`, and
  `result-*.json` under `captures/keymap-write/`, then compares the readback
  with the body written. A mismatch exits non-zero and keeps both files.
- Close the vendor app first. If another process has the keyboard open, the
  tool refuses to write unless `--force` is passed.
- The read/write protocol is hardware-confirmed; the end-to-end write flow
  remains a user-run check with the pre/post evidence above.

### `k61se_snapshot.py` — state snapshots, diffs, and explicit restore

```bash
# Vendor app closed
python3 k61se_snapshot.py --capture captures/reset-before

# Hold Fn + Tab for 5 seconds, then capture again
python3 k61se_snapshot.py --capture captures/reset-after

# Offline: show exactly what the factory reset changed
python3 k61se_snapshot.py --diff captures/reset-before captures/reset-after

# Show the restore plan without touching the device
python3 k61se_snapshot.py --restore captures/golden-reset --dry-run

# Explicitly restore lighting, colour, and keymap layer 0
python3 k61se_snapshot.py --restore captures/golden-reset --yes
```

- **Capture and diff are read-only.** They send `0x82`/`0x84`/`0x8A`/`0x83`/
  `0x85` reads and never call a write function.
- Captures `info` (6 B, sub 1), lighting (128 B), colour (420 B), keymap
  layers 0–7 (504 B each), and the macro area (4096 B by default). A snapshot
  directory contains `snapshot.json` plus raw `*.bin` bodies.
- The manifest records device VID/PID, creation time, capture arguments,
  block operations, sizes, SHA-256 hashes, keymap reports, the lighting mode,
  and any per-block errors. `--macro-size 0` skips macros.
- It refuses to capture while another process has the keyboard open unless
  `--force` is passed; a forced snapshot is marked `"clean": false`.
- `--diff A B` works entirely offline. It reports added/removed/changed blocks,
  differing byte offsets, the first changed keymap cell, and a lighting mode
  change. It exits `0` when identical, `1` when different, and `2` on errors.
- `--restore DIR` is an explicit writer. It refuses without `--yes`, saves a
  pre-restore snapshot (`restore-pre-*` or `--pre-dir`), writes lighting
  (`0x04`), colour (`0x0A`), and keymap layer 0 (`0x03`, sub 0), then reads
  every block back. It stops at the first mismatch and records
  `restore.json`, `post-*.bin`, and the pre-restore bodies.
- Restore skips macros because `0x05` is still unverified. Keymap layers
  above 0 are also unverified and require `--layers 0-7 --force`; the default
  restore is layer 0 only. Info is read-only.
- The Fn+Tab snapshot in `captures/golden-reset/` is the canonical reset
  image. Its keymap has Fn at cell 83 and a duplicate `\` at reserved cell 75;
  the older `captures/keymap-stock.bin` app readback does not.

### `k61se_keys.py` — static per-key painter

```bash
python3 k61se_keys.py --list-keys
python3 k61se_keys.py --key W=00FF00 --key A=0000FF --key S=FFFFFF --key D=FF0000 --dry-run
python3 k61se_keys.py --key W=00FF00 --key A=0000FF --key S=FFFFFF --key D=FF0000
python3 k61se_keys.py --file designs/keys/wasd.json
python3 k61se_keys.py --file designs/keys/lol.json
python3 k61se_keys.py --text LOL --rainbow
python3 k61se_keys.py --all 0000FF --key Esc=FF0000
python3 k61se_keys.py --file designs/keys/wasd.json --hold 30 --restore
python3 k61se_keys.py --file designs/keys/wasd.json --once
python3 k61se_keys.py --solid FF0000      # persistent whole-board solid, no stream
```

- **Separate from the music tool:** no audio, no FFT. But `0x08` is a
  **transient overlay**, not a latch: when frames stop arriving, the firmware
  resumes the configured lighting effect. The tool therefore streams the
  frame continuously by default, exactly like the music overlay.
- `--hold N` streams for N seconds; `--hold 0` streams until Ctrl-C (the
  default). `--once` sends one frame and exits, which will revert shortly.
  `--restore` writes the lighting block back when the stream ends.
- `--fps` is the overlay keepalive rate. 20 fps is only ~10 kB/s (the app uses
  ~22 fps), so this is not a USB or CPU bottleneck. The default is **1 fps**;
  a 10-second WASD test at 1 fps was steady on the actual keyboard. If a frame
  shows the effect between updates, raise it to 5/10/20.
- `--solid RRGGBB` is different: it writes the firmware's own **effect 0x01
  (solid)** with `rainbow=False` and exits. That is a persistent lighting-block
  write, so one uniform colour needs no stream and no keepalive. It is not a
  factory reset: it replaces the active effect with solid and leaves the other
  state (brightness/speed) alone.
- Keep the process running to keep the art. For a design that survives the
  terminal, run it in the background, e.g.
  `nohup python3 k61se_keys.py --file designs/keys/wasd.json >/tmp/k61se_keys.log 2>&1 &`.
- Key names come from `app/K61SE-keyboard.xml`. A duplicate name (`Shift`,
  `Ctrl`, `Alt`) paints both; use `--cell` for one. `--cell` takes the raw
  `key_index` (1–83 used; 84–125 ignored).
- `--all` paints the 61 key positions; `--fill` paints all 14 x 5 matrix cells,
  including the gaps.
- `--text TEXT` draws a short string with a variable-width 3x4 block font (A–Z,
  0–9, space and a little punctuation) in the top four matrix rows;
  `--text-colour` defaults to white. This is a demo font, not a layout engine;
  use `--cell`/JSON `cells` for longer words or precise art. `--rainbow` colours
  `--fill`, `--all` or `--text` by physical column; explicit `--key`/`--cell`
  colours still override. Design files accept `text`, `text_colour` and
  `rainbow` too.
- `--dry-run` prints the cells without opening the device; `--list-keys` prints
  names and indices.
- Designs live in `designs/keys/*.json` (see `designs/README.md`).

### `k61se_invaders.py` — animated Space Invaders demo

```bash
python3 k61se_invaders.py --dry-run          # ASCII preview, no device
python3 k61se_invaders.py --seconds 30       # play for 30 seconds
python3 k61se_invaders.py --layout tall      # bigger, ship on row 2
python3 k61se_invaders.py                    # until Ctrl-C
```

- `top` (default) has three 3 x 1 invaders on row 0, a three-LED ship on row 1,
  a short shot, and a six-frame explosion. Nothing below row 1 is lit.
- `tall` gives the invaders a second row (rows 0-1) and puts the ship on row 2,
  so the scene reads larger but uses one middle row. In both layouts the middle
  invader disappears under the blast and reappears on the next loop.
- It uses the same transient `0x08` frame as `k61se_keys.py`, but changes it
  every tick. The default is 5 fps; the full loop is 15 frames (3.0 s).
- `--dry-run` prints the full 15-frame ASCII loop and never opens the device.
  `--seconds 0` (the default) runs until Ctrl-C; `--no-restore` skips the
  lighting-block restore.
- The vendor app must be closed, as with every other writer.

### `k61se_music.py` — stream live LED frames (music-style)

```bash
python3 k61se_music.py --dry-run                       # build the frame, no device
python3 k61se_music.py --pattern test --seconds 5      # channel-order test + restore
python3 k61se_music.py --pattern solid --colour FF0000 # whole board one colour
python3 k61se_music.py --pattern bars --seconds 10     # 14 rainbow bars
python3 k61se_music.py --pattern off --no-restore      # zero frames, no restore
```

- This is the **write** side of §5a: it sends `op 0x08` frames at 20 fps, then
  writes the lighting block back. No mode write and no handshake, exactly like
  the app (`music_entry` / `music_exit`).
- Refuses to run if another process holds a K61SE node, so close the vendor app
  under Wine first.
- `test` lights Esc, Tab, Caps and Space. Esc is green and Tab red, which
  matches the confirmed `(G,R,B)` wire order; `--order rgb` swaps the channels
  if you ever need the other order.
- `solid` lights all 14 x 5 cells in one colour (`--colour RRGGBB`). This is
  the clearest channel-order check, and it also shows that music frames are not
  restricted to the app's gradient. `--order rgb` swaps the wire channels.
- `--seconds 0` streams until Ctrl-C; the lighting block is still restored.
  `--no-restore` skips that write, but the configured effect returns as soon as
  the frames stop — `0x08` is an overlay, not a stored frame.
- `--dry-run` is safe with the app open: it prints the body and exits.

### `k61se_music_live.py` — speaker-driven visualiser

```bash
python3 k61se_music_live.py --dry-run                     # terminal bars, no device
python3 k61se_music_live.py --seconds 30                  # drive the keyboard
python3 k61se_music_live.py --seconds 30 --keep-open      # continue live
python3 k61se_music_live.py --colour FF0000 --flat --seconds 30
python3 k61se_music_live.py --palette rainbow --seconds 30
python3 k61se_music_live.py --design peak --palette fire --seconds 30
python3 k61se_music_live.py --design psychedelic --palette rainbow
python3 k61se_music_live.py --design psychedelic --max-brightness
python3 k61se_music_live.py --design-file designs/music/psychedelic.json
```

- Captures the **default sink's monitor**, not a microphone. `pw-record
  --target` must be given the sink's **serial** (`object.serial` from
  `wpctl inspect @DEFAULT_AUDIO_SINK@`); a sink *node name* silently falls back
  to the default source. `--device` accepts a source name (e.g. `music_probe`)
  or a sink serial.
- It follows whatever the default output is — speakers or Bluetooth — so play
  something and the keyboard follows it. Silence is black frames; after 3 s
  without signal it warns `no audio on ...; is anything playing?`. If audio is
  routed to a non-default sink, pass that sink's serial with `--device`.
- 2048-sample FFT at 48 kHz, 14 log-spaced bands 50 Hz–16 kHz, five rows per
  bar, bottom-up, 25 fps. `--gain`, `--floor`, `--span` and `--gamma` tune the
  mapping; a 440 Hz tone should sit in column 5 and a sweep should walk left to
  right.
- **Designs:** `--design bars` is the default; `mirror` puts the bass in the
  centre and treble at the edges; `peak` adds falling peak dots; `pulse` fills
  the whole board with a bass-driven flash; `psychedelic` morphs through six
  patterns (plasma, tunnel, spiral, vortex, ripple, kaleidoscope), cross-fading
  every seven seconds and reacting to bass, level and treble. `--palette`
  selects `app` (captured purple → red → orange → green), `rainbow`, `fire` or
  `ice`. `--mode height` colours by bar height instead of by column.
- **Custom palettes:** `--palette-file PATH` takes a JSON list of `RRGGBB`
  stops, or `{"stops": [...], "mode": "column"|"height"}`. `--design-file
  PATH` bundles `design`, `palette`, `mode`, `flat`, `max_brightness` and
  `colour`; see `designs/README.md` and `designs/music/`.
- The default palette is the app's captured gradient (`APP_PALETTE`, §5a).
  `--colour` paints one fixed colour and `--flat` removes the vertical
  gradient; `--colour FF0000 --flat` was confirmed red on the panel, so the
  wire order is `(G,R,B)` and `--order` normally stays at `grb`.
- `--max-brightness` drives every lit cell at its full palette colour: no
  audio-level or vertical dimming. Bar heights still follow the music; this
  only changes how bright the lit pixels are. `designs/music/psychedelic.json`
  turns it on for the psychedelic theme.
- `--dry-run` prints terminal bars and never opens the device; `--show` prints
  them while driving. Restores the lighting block like `k61se_music.py`, and
  refuses to run while the vendor app holds the keyboard.
- `--keep-open` keeps the **live** visualizer running after `--seconds` until
  Ctrl-C; it does not freeze or re-send the last frame. Use it when a timed run
  is just a minimum and you want the audio-reactive display to continue.
  `--seconds 0` already runs until Ctrl-C, so the flag only matters with a
  finite timer; Ctrl-C then restores the lighting block as usual.
- If a USB feature-report write fails, it now closes the handle and tries up to
  five times to reopen interface 1 before giving up. The old
  `hid_error is not implemented yet` text was just the Python binding failing
  to supply a USB error string, not a bad frame.

### `designs/` — JSON design files

- `designs/keys/*.json` for `k61se_keys.py`: `keys` (XML names), `cells` (raw
  indices), `all` (61 keys), `fill` (70 matrix cells), and the text shortcuts
  `text`, `text_colour` and `rainbow`.
- `designs/music/*.json` for `k61se_music_live.py --design-file`: `design`,
  `palette` (a built-in name or a stop list), `mode`, `flat`,
  `max_brightness` and `colour`.
- `designs/music/*_palette.json` for `--palette-file`: a stop list or
  `{"stops": [...], "mode": "column"|"height"}`.
- See `designs/README.md` for the formats and the example files.

### `presets/` — one JSON file per state

```json
{
  "name": "rainbow wave",
  "effect": "0x0B",
  "brightness": 5,
  "speed": 5,
  "rainbow": true,
  "colour": [255, 0, 0],
  "notes": "effect + rainbow; colour is ignored by the firmware while rainbow is on"
}
```

- `effect` accepts `0x0B` or `11`; `null` means *leave the effect alone*. Same
  for `brightness`, `speed`, `rainbow` (`true`/`false`) and `colour` (`[r, g, b]`),
  so a preset can change just one thing.
- `--make-presets` regenerates 16 effect presets (brightness 5, speed 5,
  `"rainbow": true` — the form every effect was actually captured in, since the
  sweep ran with the flag on), 12 colour presets (`red`, `half_red`,
  `quarter_white`, `off_black`, …) which set **`"rainbow": null`** — they write
  the channels and leave the flag alone, because a colour preset does not know
  which effect is live and clearing the flag on a black record blanks it — and
  13 combined `theme_*` presets (effect **and** colour/rainbow):
  `theme_solid_rainbow`, plus `theme_<effect>_red` for every one of the twelve
  colour-capable effects (`theme_solid_red`, `theme_pulse_red`,
  `theme_rainbow_wave_red`, `theme_pulse2_red`, …). 41 files in total.
  `--selftest` enforces all of it: every effect preset must state its rendering,
  no colour preset may touch the flag, no preset may clear the flag without also
  supplying a non-black colour, and the colour-capable set must be exactly
  "everything but `0x03`/`0x0F`/`0x10`". The matrix runner uses only the
  effect-only × colour-only pair, so combined themes are cycled by `Sweep all`
  instead.
- A preset's `colour` goes into **the active effect's own record** (§5), so
  `theme_solid_red` writes record 1 (`0x15/0x16/0x17`) only because its effect is
  `0x01`, and `theme_rainbow_wave_red` writes record 0x0B at `0xE7`. Every
  colour-capable effect now has one, which is what "all themes should be
  colourable" asked for.
- Hand-edited files live alongside them; a preset that fails to parse is listed
  as `unusable` and skipped rather than breaking the list.

**First tests worth running** (each one settles an open question in §11):

1. Close the vendor app. `python3 k61se_gui.py --read` — does it work without
   `sudo`? (It should, via the ACL.) ✅ done
2. In the GUI press `Read keyboard`, then `Apply` with no changes — it should
   report *nothing changed* and skip both writes. ✅ done
3. `Apply` speed **2**, then **4** — both rungs are real. ✅ done 2026-10-19
4. `colour half_red` with a **solid** effect and the tick off → dimmer red, so
   the record's bytes are levels, not flags. ✅ done 2026-10-19
5. **`--effect-colour MODE COLOUR` for each of the twelve "colourless" effects**
   (vendor app closed). ✅ done 2026-10-19 — every one drew the colour and the
   control (`0x03`) stayed rainbow, so the record model in §5 is confirmed.
6. `--probe-nibbles 0x0B` — five minutes. If any of `0x51`–`0x56` lights up, the
   effects have built-in schemes too, a second route to colour (and maybe how
   `0x03`/`0x0F`/`0x10` would take one).
7. `--matrix` is widened to every effect × every colour (160 writes) — the bench
   can now cycle the lot unattended.
8. `Sweep all` and watch the panel.

---

## 8. Working method (capture → label → diff)

1. Close the vendor app if you want the Python tools to win (see §9).
2. `sudo python3 dump_lighting.py --save <label>` before the change.
3. Make the change **in the vendor app** (user action).
4. `sudo python3 dump_lighting.py --save <label>` after the change.
5. `python3 review_dumps.py -d <label> <label>` to see exactly which bytes moved.
6. For write-level detail (opcode, order, timing) capture the app with
   `snoop_session.py` under a descriptive tag and read the Markdown report.

Paired `lighting` + `colour` dumps per state is what cracked the mode enum, so
always save **both blocks** unless you have a reason not to.

---

## 9. Operational gotchas

- **Two writers.** The vendor app keeps re-asserting its own state, so a block
  write it disagrees with snaps back within a moment. The symptom is exactly
  "`off` does nothing but `on` works". Close the app (check for
  `winedevice.exe` under Wine) before driving the keyboard from Python.
- **`dumps/` is root-owned** (created by `sudo` runs), so non-root saves fail.
- Two root-owned `__pycache__` files break plain `python3 -m py_compile`; use
  `PYTHONDONTWRITEBYTECODE=1`.
- **usbmon `read(2)` returns the 48-byte API0 header** — payload starts at
  offset **48**, not 64. Assuming 64 eats the first 16 payload bytes.
- Device node names move on replug; re-detect rather than hardcoding.
- **The management interface can re-enumerate mid-run** and the node number
  changes with it: on 2026-10-19 14:49 interface 1 of the K61SE was re-registered
  (`3-1.2:1.1` → hidraw5; sysfs instance `0003:258A:010C.00CE`, while interface 0
  kept `.0005`) while `--audit` was running, and the open handle started returning
  an **empty** feature report
  ("short reply: wanted 420 bytes, got 0"). The tools now (a) retry a short/empty
  read up to 3 times with a fresh handle each time, and (b) open the device per
  block instead of holding one handle across an apply, so the new node is picked
  up automatically. The audit also retries a failed write once, stops cleanly
  instead of raising a traceback, and writes a partial report saying which effect
  failed. If `--read` works, the node is fine; if not, check
  `/sys/class/hidraw/hidraw*/device/uevent` for which node currently carries
  `BY Tech KEMOVE K61`.

---

## 10. Roadmap

### 10a. Remaining colour / effect functionality

**Status 2026-10-19: the colour block is decoded and confirmed (§5).** Every
colour-capable effect draws its own record, the bench covers all of them, and the
remaining work is the app's *other* colour functionalities (the colour wheel,
per-key colour, music mode) rather than the record layout.

```bash
python3 k61se_gui.py --effect-colour 0x0A red   # one effect, app closed — watch the panel
python3 k61se_gui.py --read                     # the byte that moved
sudo python3 dump_lighting.py --save <label>    # or capture the app the slow way
python3 review_dumps.py -v                      # every offset that varied
```

One capture per theme, named by you, one read each — the app can stay open. If
you would rather not type, `sudo python3 dump_lighting.py --watch` auto-captures
passively instead, at the cost of sometimes missing one of the two blocks a
theme change writes.

- [x] ~~**Mode values `0x05`–`0x0A` and above**~~ Done 2026-10-19: 15 values
      labelled, `MODES`/`MODE_SLUGS` filled in. Still missing: `0x06`, `0x0E`.
- [x] ~~**`0x06` and `0x0E`**~~ `0x06` tested 2026-10-19: writes cleanly, reads
      back, **panel goes dark and the hotkey cannot relight it** — the firmware
      draws nothing for it. Recovered by writing a real mode. `0x0E` untested;
      assume the same.
      Remaining question: is `0x06` merely unused, or a mode that waits for host
      data (music/rhythm, per-key custom)? Cheap test — open the vendor app,
      click its music-mode control, then dump the lighting block and see which
      mode byte it writes.
- [x] ~~**Themes / presets**~~ Test bench built 2026-10-19: `k61se_gui.py` plus
      41 presets (16 effects, 12 colours, 13 combined themes) to walk every
      effect and colour by hand, or `--matrix` for the whole cross-product. Still
      the big prize: watch the colour block's non-zero count during a theme change
      — if it jumps from a handful to hundreds of non-zero bytes, it is carrying
      per-key colour.
- [x] ~~**Brightness**~~ Found 2026-10-19: `entry[mode-1]` low byte, `1`–`5`.
      **`0x0B` is not brightness** — it never varies.
- [x] ~~**Speed / direction**~~ Found 2026-10-19: `entry[mode-1]` high byte,
      `0x17` slow / `0x37` middle / `0x57` fast. Per effect, no global byte.
      Now modelled as a 5-level scale (`level = high nibble`). ~~what extra bits
      the high byte carries for non-animated effects (`0x50` in solid)~~ —
      answered 2026-10-19: the **low nibble is a rainbow flag**, `7` = the
      firmware draws its own rainbow and ignores the colour block, `0` = draw the
      channels. Open: whether `0x27` / `0x47` are reachable (user believes 5
      positions).
- [x] ~~**Solid + rainbow**~~ Answered 2026-10-19 from the vendor app's own
      state: it is effect `0x01` with the rainbow flag set (`slot[0] = 05 57`),
      and the colour block is ignored (it still read stale red).
      `presets/theme_solid_rainbow.json` now encodes exactly that.
- [x] ~~**Is colour 8-bit per channel?**~~ **Yes.** Answered 2026-10-19: writing
      `(128,0,0)` into a live solid effect showed visibly dimmer red. The bytes
      are levels in each effect's own record (§5).
- [x] ~~**Do all 15 effects have a single-colour and a rainbow rendering?**~~
      **Answered 2026-10-19.** The 2026-10-19 audit said only
      `0x01`/`0x02`/`0x04` could draw a colour and nine went dark — but it wrote
      *solid's* record every time (§5). The app walk showed the colour block holds
      **one colour record per effect**, and the targeted `--effect-colour` run
      confirmed it on the panel: the twelve colour-capable effects all drew the
      requested red, `0x03`/`0x0F`/`0x10` ignored the flag and stayed colourful.
      The corrected bulk `--audit` (2026-10-19 15:12) matched: **12 of 15 both
      renderings, 3 flag-ignored, 0 dark**. So every effect has both renderings
      except those three.
- [x] ~~**Can the other twelve be coloured at all?**~~ Yes — confirmed
      2026-10-19 (§3/§5). `--matrix`, the GUI tick and the `theme_*` presets are
      all widened to the twelve.
- [ ] **What are the other 18 bytes of each colour record?** They have never been
      seen non-zero, so per-key colour — if the app offers it — is the thing to go
      and look for. 420 = 20 × 21, and `21 = 3 + 18`; 18 is not a multiple of 61
      keys, so this block is not a plain per-key map.
- [x] ~~**Meaning of `0x2B` / `0x55`** sticky flags.~~ **Answered 2026-10-19:**
      they are not flags. `0x2B` = `21*2+1` is pulsing's red level, `0x55` =
      `21*4+1` is typing feedback's red level (§5). Their disappearance from the
      open list is what let the record layout fall out.
- [ ] **`0x02` changed `03` → `83`** between the 10-18 and 10-19 sessions and
      stayed there. `0x80` is the READ bit in the frame, so this may mean the
      block is not a pure copy of what the app wrote. Re-read now and check.
- [ ] **`0x16`/`0x17` flag pair** (`7F 00` vs `00 FF`).

### 10b. Music mode (WASAPI loopback, solved)

The app does **not** open a microphone. It calls
`GetDefaultAudioEndpoint(eRender, eConsole)`, registers an
`IMMNotificationClient`, and opens that **render** endpoint with
`AUDCLNT_STREAMFLAGS_LOOPBACK` (`0x20000`) — it records what the speakers are
playing. Wine 10.0's `winepulse.drv` implements this correctly: a
`WINEDEBUG=+mmdevapi,+pulse` run showed the loopback flag and 845 captured
buffers, and the app's own level meter moves.

So: play music on the default PipeWire sink, leave the app on its default
device, and music mode works. Nothing has to be routed to `music_probe`.

Verified 2026-10-19 with Wine 10.0 / PipeWire 1.6.2:

- driver `winepulse.drv`; socket `$XDG_RUNTIME_DIR/pulse/native`
- `HKCU\Software\Wine\Drivers\winepulse.drv\DefaultOutput` =
  `{0.0.0.00000000}.{0FB07C09-…}` (Speaker)
- the Music tab's device dropdown stays **empty** under Wine because mmdevapi
  never sends `OnDeviceAdded` (only `OnDefaultDeviceChanged`). The app never
  calls `EnumAudioEndpoints`, so this is cosmetic — audio still flows.

`k61se_music_audio.sh` (`music_probe`) is now only a **diagnostic** for the
speaker-monitor path; it is not what the app selects. `up` / `test` / `down`
remain useful to prove that the speaker mix is audible to a PulseAudio client.

If the level meter is dead:

1. `wine reg query 'HKCU\Software\Wine\Drivers'` — `winepulse.drv` must be
   the driver.
2. Check that `DefaultOutput` names a real sink and that sink is the default
   (`wpctl status`).
3. Re-run with `WINEDEBUG=+mmdevapi,+pulse` and look for
   `AUDCLNT_STREAMFLAGS_LOOPBACK` and `IAudioCaptureClient::GetBuffer`.

Protocol: once music plays the app streams `op 0x08` frames — see §5a. A
6.8 s capture holds ~150 frames at ~22 fps and no other writes.

### 10c. Keymap and macro next steps

The keymap command pair is now confirmed by the 2026-10-20 captures. Remaining
steps:

1. ~~Keep the captured stock body from `captures/keymap_write/` as the
   canonical baseline.~~ Done: `captures/golden-reset/` and
   `captures/keymap-reset.bin` are the canonical Fn+Tab reset state. It has Fn
   at cell 83, the duplicate `\` at reserved cell 75, and slot 0 zero. The
   older `captures/keymap-stock.bin` app readback remains for comparison.
2. ~~Probe all layers after a factory reset and compare them.~~ Done
   2026-10-20: layers 0–7 all read back identical (SHA-256
   `39d19f848c013ffd9c65ddbd16c1a013136fcc8fed9175e244ca9025d968cc1a`).
   `KBAccess` validates 1–8 but the packet has no layer-count field.
3. ~~Read the macro area with `--macro-size 4096`.~~ Done: the reset snapshot
   reads 4096 zero bytes, so the reset state has no macro table. The first
   non-empty table should still start with `4 * N`.
4. ~~Implement a separate explicit keymap write.~~ Done in
   `k61se_keymap.py` (`0x03`, 0x1F8, sub=0): read-modify-write, `--yes`,
   pre-write dump, post-write readback, and a JSON diff. It is intentionally
   separate from the lighting tools and `k61se_macro.py`.
5. ~~Build and validate the first macro writer.~~ The 2026-10-20
   `k61se_macro_write.py --name test1 --text abc --yes` run wrote a 39-byte
   F75-style body (`04 00 23 00 ...`, table at 4, name length 10 bytes) and the
   `0x85` readback prefix matched exactly. Macro keymap binding is now enabled
   as `macro:0`; playback/binding remains the next hardware test.
6. ~~Capture before/after Fn+Tab and use the post-reset snapshot as a golden
   image.~~ Done: `captures/golden-reset/` and `captures/keymap-reset.bin` are
   the canonical reset state. `k61se_snapshot.py --restore` writes lighting,
   colour, and keymap layer 0 with a pre-restore snapshot and per-block
   readback. Macros and unverified keymap layers remain skipped by default.
7. Decide the GUI path once remap and macros are known: a K61SE Studio MVP can
   treat lighting (§3–§5), keymap (§5b), and macros as separate panes sharing
   `k61se_dev.py`.

The keymap pair is confirmed; the macro pair is not. Keep
`k61se_macro.py` read-only until a macro write/readback is captured, and keep
keymap writes in a separate explicit tool with a pre-write dump and readback.

---

## 11. Open questions

1. Does anything other than the hotkey set the firmware enable flag? (All
   evidence says no — the vendor app can't either.)
2. Is `0x06` (and `0x0E`) just unused, or a mode that waits for host data
   (music/rhythm, per-key custom)? `KBAccess` now shows `0x06` is a 378-byte
   per-key colour/game matrix written with a profile byte (`SetKeyColor`), but
   the K61SE's use of it (and the `0x0E` lighting mode) is still unobserved.
3. What is body `0x0B` (`20` in every dump ever), if not brightness? And
   `0x0C`–`0x0F`, which are likewise constant?
4. ~~What else does the parameter table's high byte carry?~~ Answered
   2026-10-19: the **low nibble is a rainbow flag** (`7` on / `0` off). The
   untouched slots holding `3703` are just the default (brightness 3, speed 3,
   rainbow on), and non-animated effects storing `0x50` simply have the flag off.
   Follow-up answered with the app walk (2026-10-19, §5), and it overturns the
   audit: the colour block is **twenty 21-byte records, one per effect**, and the
   audit wrote solid's record every time. **Confirmed on the panel 2026-10-19**:
   every effect except `0x03`/`0x0F`/`0x10` drew the colour written to its own
   record, and those three ignore the flag and stay colourful. Open: are `1`–`6`
   built-in colour schemes too (`--probe-nibbles 0x0B`)?
5. Are the speed levels `0x27` and `0x47` reachable, or is the scale really
   just slow / middle / fast? (The user believes five positions; the test bench
   writes any of the five levels, so this can be settled by eye.)
6. Are the colour channels 8-bit levels (colour wheel) or on/off flags? **Yes,
   levels** — a live solid effect with `(128,0,0)` in its record showed visibly
   dimmer red (2026-10-19). Each effect reads its own record (§5).
7. ~~**Does solid (`0x01`) + all-channels-full render a static rainbow or
   white?**~~ Answered 2026-10-19: neither — that guess had the mechanism wrong.
   The vendor app's own solid-rainbow state is effect `0x01` with the rainbow
   flag set (`slot[0] = 05 57`) and a stale red record, so all-channels-full
   was never involved. `theme_solid_rainbow` reproduces it.
   The `0x16`/`0x17` pair (`7F 00` vs `00 FF`) is still open — both sit inside
   solid's record (`0x16` = its red, `0x17` = its green), so this may just be
   "which record was written last" rather than a separate flag pair.
8. Why did body `0x02` go `0x03` → `0x83` between the two sessions?
9. How is per-key colour represented? (420 = 20 × 21, so the block is not a
   plain 61-key map; the 18 bytes after each record's colour have never been
   seen non-zero.)
10. ~~Do `0x2B` / `0x55` gate anything?~~ No — they are pulsing's and typing
    feedback's red levels (§5).
11. ~~Does music mode need a mode write before `0x08` frames are accepted?~~
    Answered 2026-10-19 (`music_entry`, `music_exit`): no. Entry is just the
    first `0x08` frame; exit is stopping the stream and re-writing the lighting
    block. There is no music mode enum value (§5a).
12. ~~Is the music block's channel order `(G,R,B)`?~~ Answered 2026-10-19:
    `--colour FF0000 --flat` lit red, so the wire order is `(G,R,B)`, the same
    as the colour records and the LED chain.
13. Does `op 0x88` (378 bytes) read the last music frame back? Untested; not
    needed to drive it.
14. ~~Does layer 0 always contain the XML factory layout, and what is slot 0?~~
    Answered 2026-10-20: the body uses `4 * key_index` directly. Layers 0–7
    read back identical after Fn+Tab. Slot 0 is reserved and zero in the reset
    snapshot; the older app readback had a duplicate Esc there. The reset
    snapshot is saved as `captures/golden-reset/`.
15. How many remap layers does the K61SE expose, and does `SetLayerCount` live
    only in the host SDK? `KBAccess` accepts 1–8 but the packet has no layer
    count field.
16. Does the vendor app build a macro blob the same way as `KBAccess`'s
    `AddMacro`, including the apparent `wcsnlen` byte/character mismatch? The
    probe reports which name-length interpretation fits the event stream.
17. Does `0x06`/`0x86` (378 bytes) write/read the per-key colour matrix on the
    K61SE, and which `sub` byte selects the profile? Not used by the current
    lighting tools.

---

## 12. Corrections log

Things we believed and then disproved — recorded so nobody re-derives them:

- ~~"Only three ops return real blocks."~~ The device always returns
  `8 + size`; op existence comes from captures, not from read length.
- ~~"The K61SE keymap/macro protocol is probably a different command family."~~
  The wired `KBAccess` vtable uses `0x03`/`0x83` for the 0x1F8 keymap and
  `0x05`/`0x85` for the variable macro block, the same as the wired F75.
  Static only so far — hardware read-back is the next check (§5b).
- ~~"The firmware ignores a no-change write."~~ The vendor app re-asserting
  `0x04` explained the failures. It was never the firmware.
- ~~"A two-step `off` then target will defeat a hotkey-off."~~ Tested against a
  hotkey-darkened panel: it stayed dark. Removed from the tool.
- ~~"Polling report 6 in a watch loop is safe."~~ It is not. Sustained polling
  (~2 blocks every 0.35 s) stops the vendor app finding the keyboard. Watch mode
  is passive by default now, and `--manual` (one on-demand read per theme) is
  the recommended sweep. Related fact: report 6 holds **one** report at a time,
  so a read returns only the newest block and the other is gone.
- ~~"Colour body `0x16 = FF` is an apply/commit marker."~~ It is the red channel.
- ~~"Mode `0x0B` is a click-light preview mode."~~ One observation, wrong. `0x0B`
  is the **rainbow wave** effect (user label, 2026-10-19).
- ~~"The `0x3A` table is a palette / the static-colour store."~~ It is neither.
  It is a **per-effect parameter table**: `entry[mode-1]` holds that effect's
  brightness (low byte) and speed (high byte). The RGB565 decode is dead, and the
  effect is selected by `0x0A` alone.
- ~~"The `0x3A` table accumulates one slot per effect you click."~~ Half right,
  wrong cause. Each slot is filled when its effect is *first selected*, so
  clicking the list in order looks like accumulation. The slots that never
  changed (5 and 13) are exactly modes `0x06` and `0x0E`, never selected.
- ~~"`0x53` may be a latch rather than the speed."~~ It is speed — mode `0x0D`'s
  parameter high byte. It only looked like a latch because the later speed test
  was on mode `0x11`, whose speed byte is `0x5B`.
- ~~"`0x0B` is the brightness byte."~~ Never varied, including across a deliberate
  "max brightness" capture. Brightness is in the parameter table instead.
- ~~"`0x16 = FF` in the colour block is a red on/off flag."~~ More likely the red
  **level** (`0x15`/`0x16`/`0x17` = G/R/B, 0–255) — the app has a colour wheel and
  nothing else in the 420 bytes has ever moved. The app's own colour write was
  captured and its payload is byte-identical to our dumps, so from here it is a
  question of testing values, not of decoding a frame.
- ~~"A non-zero mode means the panel is lit."~~ Only for modes the firmware can
  draw. `0x0A = 06` reads back non-zero and renders nothing, and because the
  hotkey only toggles a separate enable flag, the panel then looks unfixable by
  the hotkey. The gate has three terms, not two (§6).
- ~~"Music mode is a firmware mode with its own enum value."~~ It is not. The
  app never writes a lighting mode on entry; it takes over the LEDs with `0x08`
  frames while the selected effect stays configured, and on exit it re-sends
  that effect (`music_entry` / `music_exit`, 2026-10-19, §5a).
- ~~"usbmon payload starts at offset 64."~~ It is **48** (`read(2)` uses the
  API0 header).
- ~~"The parameter table's low nibble is part of the speed / is an undecoded
  flag to preserve."~~ It is a **rainbow flag**: `7` = the firmware draws its own
  rainbow and ignores the colour block, `0` = draw the colour channels. Settled
  2026-10-19 from three pairs that differ in exactly that byte (mode `0x04`
  typing feedback rainbow vs red; mode `0x01` solid rainbow vs red; the app's own
  captured "solid red" write vs the `solid_rainbow_app` dump). The earlier claim
  that solid mode's `0x50` "carries more than speed" was right, but the extra
  thing is this flag, not an unexplained bit.
- ~~"The app's `rainbow` colour swatch (`FF FF FF`) selects the rainbow; solid +
  all-channels-full is the test."~~ Wrong mechanism. The swatch writes the same
  three colour bytes as "white"; what selects a rainbow is the flag nibble in the
  *lighting* block, which the colour-only captures did not record. The
  `solid_rainbow_app` dump proves it: a visible solid rainbow with a stale red
  colour block. `presets/colour_rainbow.json` was removed as misleading.
- ~~"The 33 presets needed `theme_solid_rainbow` to write `255,255,255`."~~ It
  now writes effect `0x01` + `"rainbow": true`, which is what the vendor app
  actually sends, and the preset count was 33 at that point from a different
  split (16 effects, 12 colours, 5 themes). It is 41 now (13 themes) after the
  per-record test passed.
- ~~"Every effect has both a colourful and a single-colour rendering, so `--audit`
  will find 15 of them."~~ Disproved by the *first* audit run (2026-10-19) — but
  that disproof is itself retracted below: the audit wrote solid's record for
  every effect. **Confirmed 2026-10-19:** 12 of 15 effects draw their own colour
  and 3 (`0x03` rotating, `0x0F` spinning, `0x10` wave 2) ignore the flag and
  render identically to their rainbow version (`colour_mode_audit.md`).
- ~~"A `no` to the audit's second question means the effect ignored the flag and
  stayed colourful."~~ On the first (yes/no) run it usually meant the panel went
  **blank** — the script offered no way to say that, so it wrote a misleading
  verdict column. It now takes `d` for dark and restores a lit state when it
  finishes. The corrected re-run (each effect's own record) gives **12 coloured,
  3 flag-ignored**. The colour presets — which used to force `"rainbow": false` —
  still leave the flag alone, not because clearing it is unsafe, but because a
  colour-only preset cannot know whether the live effect's record is black.
- ~~"`theme_snake_green` and `theme_wave_blue` are valid themes."~~ They were
  retired while the audit said `0x0A`/`0x0B` had no colour renderer. That verdict
  is retracted: single-colour themes now exist for every colour-capable effect,
  including `theme_rainbow_snake_red` and `theme_rainbow_wave_red`, and
  `--selftest` fails any preset that clears the flag without supplying a
  non-black colour.
- ~~"3 of 15 effects can draw a colour; nine go dark at the flag-off position."~~
  **Retracted later the same day (2026-10-19).** The audit wrote the colour into
  `0x15/0x16/0x17` for every effect, but the app walk proved the colour block is
  **twenty 21-byte records, one per effect**, with that effect's red at
  `21*mode + 1` — so those three offsets are *solid's* record, and the audit was
  colouring solid twelve times over. "Dark" almost certainly meant "its own record
  was black". The `0x03`/`0x0F`/`0x10` row survives: the user saw in the app itself
  that toggling "colourful" on rotating changes nothing. `k61se_dev.set_state()`
  now writes the active effect's own record, `--effect-colour` tests it, and the
  GUI gate was widened once that test passed. **Confirmed 2026-10-19:** every
  colour-capable effect drew the colour, so "dark" really did mean a black record.
  The corrected bulk `--audit` the same afternoon gave 12/15 both renderings, 3
  flag-ignored, 0 dark.
- ~~"`0x2B` / `0x55` are sticky flags whose meaning is unknown."~~ They are not
  flags: `0x2B = 21*2+1` is **pulsing's red level** and `0x55 = 21*4+1` is
  **typing feedback's red level** (§5). Nothing latches; those two records simply
  keep the colour they were first given, and the "sticky" reading came from
  comparing dumps taken before and after the app first coloured them.
- ~~"`0x15`/`0x16`/`0x17` are the colour block's channels."~~ They are **effect
  1's (solid's)** channels. Every effect has its own record; the offsets were
  never global, they just happened to belong to the first effect we tested.
- ~~"The music block's channel order is assumed `(G,R,B)`, not tested."~~
  Confirmed 2026-10-19: `k61se_music_live.py --colour FF0000 --flat` lit red,
  not green. Static art (`k61se_keys.py`) uses the same order.
- ~~"One `0x08` frame latches and the panel keeps it until a lighting write."~~
  It does not. `0x08` is a transient foreground overlay; when the stream stops,
  the firmware resumes the configured effect. The vendor app streams at ~22 fps
  and re-writes the lighting block on exit. `k61se_keys.py` therefore streams
  continuously by default (1 fps, confirmed steady; `--fps` raises it);
  `--once` reproduces the brief one-shot.
