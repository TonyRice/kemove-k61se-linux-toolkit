# Device support and protocol families

This note records what is known about the KEMOVE K87 and how it relates to the
K61SE and the AULA F75. It is deliberately separate from `PROTOCOL.md`:
`PROTOCOL.md` describes the K61SE bytes that have been verified; this file
tracks the wider device family and the questions that still need a hardware
probe.

## Current finding

The K87-1980 and the K61SE share the same USB identity and wired management
interface. Both are `258A:010C`, management interface 1, and the read-only HID
inventory sees the K87-1980 as:

```text
VID:PID    MI  product            profile
258A:010C  0   KEMOVE K87-1980    -
258A:010C  1   KEMOVE K87-1980    k87-1980 (provisional)
```

The product string is the discriminator, because several models share the same
VID/PID. The current K61SE scripts only check VID/PID and interface, so on a
machine with more than one of these keyboards they can open the wrong one.

The K87SE-1980 uses a different PID, `258A:0164`, according to the vendor app's
model table.

## What the vendor app says

The K87 driver download is the same KEMOVE application already present in
`app/`:

| File | SHA-256 |
|---|---|
| `app/DeviceDriver.exe` | `d306a0771252be07a4cec55441d244d50533b3b48fbdbfc3ef9bae25cdde3262` |
| `app/KBAccess.dll` | `2e28fd56699cc5587aba02ce3d1001e609a604e4f3a669eea02a644e7f1aee4d` |

The downloaded K87 package contains the same binaries and the same XML
layouts, including `K87-1980-keyboard.xml` and `K87SE-1980-keyboard.xml`.
`DeviceDriver.exe` contains these model records:

| Model | USB identity | Notes |
|---|---|---|
| KEMOVE K61SE | `VID_258A&PID_010C&MI_01` | verified target |
| KEMOVE K87-1980 | `VID_258A&PID_010C&MI_01` | same VID/PID as K61SE |
| KEMOVE K87SE-1980 | `VID_258A&PID_0164&MI_01` | different PID |
| KEMOVE K68SE | `VID_258A&PID_010C&MI_01` | same VID/PID |
| KEMOVE K98-1980 | `VID_258A&PID_010C&MI_01` | same VID/PID |
| KEMOVE K98SE-1980 | `VID_258A&PID_0164&MI_01` | same PID family as K87SE |
| KEMOVE DK61SE | `VID_258A&PID_0155&MI_01` | different PID |

`KBAccess.dll` contains both transport implementations:

- `CDrvG5KB` — the 520-byte feature-report transport used by the K61SE.
- `CDrv3632` — the 20-byte `0x13` report transport used by the AULA F75
  receiver/Bluetooth path.

That is the same split documented in `../aula-f75-linux/docs/PROTOCOL.md`.

## Protocol relationship

The AULA F75 has two transports:

| Link | HID identity | Framing |
|---|---|---|
| USB-C | `258A:010C`, interface 1 | 520-byte feature report `0x06` |
| 2.4G receiver | `3554:FA09`, usage page `0xFF02` | 20-byte report `0x13` |
| Bluetooth | `3554:FA08`, usage page `0xFF02` | same 20-byte report; configuration is unreliable |

The K61SE is already using the **wired G5KB framing**:

```text
06 | op | sub | 00 | chunks | index | size_lo | size_hi | payload
```

The wired command mapping in the AULA F75 notes is the same mapping the K61SE
tools use:

| Function | Wired command |
|---|---|
| Key matrix | `0x03` write / `0x83` read |
| LED settings | `0x04` / `0x84` |
| Macro buffer | `0x05` / `0x85` |
| Per-key LED matrix | `0x06` / `0x86` |
| Real-time frame | `0x08` |
| RGB table | `0x0A` / `0x8A` |
| Reset | `0x11` |

So the K61SE protocol is best understood as the **wired subset** of the F75
protocol family, not a separate language. The differences for the K87 are
likely to be:

- product-string matching instead of a unique VID/PID;
- a larger key layout (87 keys, 18 columns × 6 rows);
- a potentially different keymap length or layer count;
- model-specific lighting effect names and mode values;
- a 2.4G receiver path that needs the `CDrv3632` framing if the K87 is used
  wirelessly.

The first three are enough to make the current scripts behave incorrectly even
if the frame format is identical.

### K87 keymap is confirmed

The read-only probe succeeded against the connected K87-1980:

- read command `0x83`, declared size `504`;
- reply body is exactly **504 bytes / 126 four-byte cells**;
- the K87 XML matches **86/86** non-Fn factory cells;
- cells 102-125 are zero;
- Fn is at cell **59** (`0d 00 00 00`);
- Menu is at cell **65** (`00 00 00 65`);
- Right Ctrl is at cell **83** (`00 10 00 00`);
- Esc is at cell **0**, unlike the K61SE where Esc is cell 1 and slot 0 is
  reserved.

So the K87 uses the same G5KB keymap command and size as the K61SE. The
difference is the layout/key-index map, which is exactly what model profiles
are for. Writes are still unverified.

### Lighting block

The K87 lighting block is also **128 bytes** and keeps the same shape:

- `0x38..0x39` = `ff ff`
- 34 two-byte effect slots at `0x3A..0x7D`
- `0x7E..0x7F` = `5a a5`
- the current mode is at `0x0A`

Compared with the K61SE reset snapshot, only six bytes differ, and they are
consistent with the K87's current mode and brightness rather than a new block
layout:

| Offset | K87 | K61SE reset | Meaning |
|---:|---|---|---|
| `0x00` | `80` | `00` | model/state header byte; still unnamed |
| `0x0A` | `08` | `0b` | current effect |
| `0x0B` | `21` | `20` | unnamed state byte |
| `0x0C` | `01` | `00` | unnamed state byte |
| `0x0E` | `01` | `00` | unnamed state byte |
| `0x48` | `04` | `03` | brightness of effect slot 8 |

The lighting block is therefore very likely the same structure. Do not write
it until a K87-specific round-trip test is done.

### Colour block: this is the real difference

The colour block is still **420 bytes / 20 records × 21 bytes**, but the
record semantics are different:

- K61SE record: one RGB triple at `+0..+2`, then 18 untouched bytes.
- K87 record: **seven RGB triples**, a seven-colour palette.

The K87 snapshot has this 21-byte palette in every record:

```text
ff 00 00  00 ff 00  00 00 ff  ff ff 00  ff 00 ff  00 ff ff  ff ff ff
 red       green     blue      yellow     magenta    cyan      white
```

All 20 records are identical in the captured state. The K61SE reset block has
only a single red/green/blue triple per record.

So the keymap and lighting are the same protocol family; the **colour encoder
and decoder are model-specific**. Writing a K61SE colour block to a K87 would
produce a different palette, not necessarily the intended colours.

## Read-only verification

The probe does not open the device:

```bash
python3 -m kemove.probe
python3 -m kemove.probe --json
python3 -m kemove.probe --all
```

The keymap and macro tools now auto-detect one connected keyboard, and accept
an explicit profile when more than one is attached:

```bash
python3 k61se_keymap.py --read
python3 k61se_keymap.py --profile k87-1980 --read
python3 k61se_macro.py --probe --profile k87-1980
python3 k61se_keymap.py --profile k87-1980 \
  --base-file evidence/captures/k87-probe/keymap.bin --set A=B --dry-run
```

To verify the wired protocol and measure the blocks:

```bash
python3 -m kemove.probe --read-keymap
python3 -m kemove.probe --read-all --save-dir evidence/captures/k87-probe
```

`--read-keymap` sets the read bit (`0x83`); `--read-lighting` uses `0x84` and
`--read-colour` uses `0x8A`. `--read-all` requests all three. They only send
read requests and never write a block. Run them with the vendor app closed and
only the keyboard under test connected.

The K61SE reader assumes 504 bytes (126 four-byte cells). The K87 uses the
same 504-byte block; the unused cells 102-125 are zero. The K87 layout has 108
possible LED positions (18 × 6), but the keymap block is 126 cells. Do not
assume the K87 lighting/colour blocks have the same layout until they are read
and compared.

## Modularity

The new `kemove/` package is the start of the model-aware layer:

- `kemove/devices.py` is the single registry of VID/PID, product strings,
  transports, keymap sizes, LED counts, colour formats, and layout XML paths.
- `kemove/layout.py` is the tolerant vendor XML loader shared by the tools.
- `kemove/colours.py` has the K87 `palette7` codec, kept separate from the
  K61SE single-colour records.
- `kemove/lighting.py` validates lighting blocks and decodes single/palette7
  colour records.
- `kemove/snapshot.py` loads snapshot manifests and diffs blocks offline.
- `kemove/probe.py` is a read-only inventory and keymap/lighting/colour probe.
- `tests/test_devices.py`, `tests/test_layout.py`, `tests/test_lighting.py`,
  `tests/test_snapshot.py`, and `tests/test_k87_blocks.py` verify profile and
  block behaviour without hardware.

`k61se_keymap.py` and `k61se_macro.py` are now profile-aware: they auto-detect
a single connected keyboard, accept `--profile k61se|k87-1980`, and load the
profile's layout XML. K87 writes are gated behind `--force` until hardware
round-trip tests are complete; dry-run keymap edits are allowed.

`k61se_snapshot.py` now selects a profile for capture/restore and records the
profile in new manifests. Restore of provisional profiles is gated behind
`--force`. `python3 -m kemove.lighting_cli` provides profile-aware read-only
lighting/colour inspection and offline decoding; the old lighting/power writers
still use the K61SE path until their write guards are wired.

## Next checks

1. ~~Read the K87 keymap and confirm the 504-byte block.~~ Done on 2026-11-25.
2. ~~Read the K87 lighting and colour blocks.~~ Done on 2026-11-25.
3. ~~Add a K87 palette codec (`palette7`) and tests for round-tripping one record.~~ Done in `kemove/colours.py`.
4. ~~Make the keymap/macro CLI profile-aware so it loads the K87 XML and
   key-index map automatically.~~ Done.
5. ~~Add profile-aware snapshot capture/restore and read-only lighting/colour
   inspection.~~ Done; K87 writes are still gated.
6. Add a K87 keymap write test on a non-arrow key with pre-write readback and
   restore.
7. Add a K87 lighting/colour read-modify-write test using the new palette codec.
8. Repeat over the K87 2.4G receiver; if it appears as `3554:FA09`, implement or
   reuse the `CDrv3632` framing.
9. Only after those pass, enable K87 write support with the same pre-write
   readback and restore guards used by the K61SE tools.
