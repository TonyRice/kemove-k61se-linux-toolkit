# K61SE colour block — one 21-byte record per effect

Analysis of the user's 2026-10-19 vendor-app walk (15 effects, each saved twice
with `dump_lighting.py --save app_<effect>_rainbow` / `app_<effect>_colour`).
Device-free; re-run any time with

```bash
python3 review_dumps.py --records          # pairs app_*_rainbow / app_*_colour
```

## What the walk shows

Pairing the two dumps per effect, the byte the app moved in the 420-byte colour
block is always `21*mode + 1` — that effect's **red** level — and it always goes
`00 → FF`, because the colour selected in the app was red:

| effect | mode | changed byte | `21*mode + 1` | |
|---|---|---|---|---|
| rainbow pixel changing | `0x05` | `0x6A` | 106 | ✅ |
| rainbow wave | `0x0B` | `0xE8` | 232 | ✅ |
| typing feedback, single key | `0x0C` | `0xFD` | 253 | ✅ |
| wave pulse | `0x0D` | `0x112` | 274 | ✅ |
| spinning | `0x0F` | `0x13C` | 316 | ✅ |
| wave 2 | `0x10` | `0x151` | 337 | ✅ |
| pulse 2 | `0x11` | `0x166` | 358 | ✅ |

`0x112 → 0x13C` is a 42-byte step for a two-mode gap: mode `0x0E` is the only one
never observed, and it has no record written, so the 21-byte stride is confirmed
by the gap as well as by the hits.

## The layout

```
colour block (420 B) = 20 records × 21 B, record `mode` at 21 * mode

  +0  GREEN   0..255
  +1  RED     0..255        <- the byte the app writes when colouring an effect
  +2  BLUE    0..255
  +3  .. +20   never seen non-zero; unknown
```

Effect 1's record is therefore `0x15/0x16/0x17`, the offsets every earlier
red/green/blue dump pointed at. They are **solid's** colour, not a global one —
which is the whole reason the earlier audit went wrong.

Two other things fall out of this:

- `0x2B` and `0x55`, long documented as "sticky flags", are `21*2+1` and
  `21*4+1`: **pulsing's red** and **typing feedback's red**. They were set on
  2026-10-18 when those effects were first coloured and never changed again —
  nothing latches, those records just keep their colour.
- `0x16 = 0x80` in one app dump is solid at **half red** from the colour wheel,
  i.e. the bytes are levels, not on/off flags.

## Rows with no colour written

Nine of the fifteen pairs show the flag cleared (`05 57 → 05 50`) but no colour
byte moved. Those are effects whose record the app had **already** filled in an
earlier session (`0x02`, `0x03`, `0x04`, `0x07`, `0x08`, `0x09`, `0x0A`), plus
`0x04` where the toggle did not register at all (its snapshot is unchanged).
That is consistent: the app only writes a record when its colour differs from the
one stored.

## What this retracts

`colour_mode_audit.md` concluded that nine effects have no colour renderer. That
audit wrote `0x15/0x16/0x17` — solid's record — while each effect was live, so it
coloured solid every time and left the effect under test black.

**Confirmed on the panel 2026-10-19 (evening).** The user ran
`python3 k61se_gui.py --effect-colour MODE red` with the vendor app closed:
`0x03` stayed rainbow (the control), and **every other effect drew the red**.
The corrected bulk `--audit` the same afternoon reproduced it exactly — **12 of
15 both renderings, 3 flag-ignored, 0 dark** (`colour_mode_audit.md`). The
record model below is therefore the protocol's current answer, not a
hypothesis, and the bench has been widened to match (`--matrix`, the GUI tick,
one `theme_<effect>_red` preset per colour-capable effect).

The three flag-ignored effects (`0x03`, `0x0F`, `0x10`) are unaffected: the app
writes their records too (`0x40`, `0x13C`, `0x151`) but the panel still ignores
the flag, which the user confirmed in the app itself and again in the targeted
run.

## Artefacts

- Raw dumps: `dumps/*_20261019_13*_app_*.bin` (the `--save` walk) and
  `dumps/*_20261019_14*_app_*.bin` (the full 15-effect sweep).
- `app_effect_walk.md` is the earlier guided run (13:52); its verdict column uses
  the old "colour renderer" wording and predates this analysis.
- `review_dumps.py --records` regenerates the pairing table above.

## Next

1. ~~`--effect-colour` per effect~~ ✅ done 2026-10-19: twelve coloured, three
   controls stayed rainbow. The corrected bulk `--audit` matched (12/15, 0 dark).
2. ~~Widen `--matrix`, the GUI gate and the `theme_*` presets~~ ✅ done. The
   matrix is 160 writes (16 rainbow + 12 colours × 12 effects) and there are 41
   presets (16 effects, 12 colours, 13 themes).
3. Still open: the 18 unused bytes per record (per-key colour?), the two unseen
   modes `0x06`/`0x0E`, and the app's *other* colour functionalities (its colour
   wheel, music mode).
