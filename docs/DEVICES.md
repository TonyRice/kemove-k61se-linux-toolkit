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

## Read-only verification

The probe does not open the device:

```bash
python3 -m kemove.probe
python3 -m kemove.probe --json
python3 -m kemove.probe --all
```

To verify the wired protocol and measure the keymap block:

```bash
python3 -m kemove.probe --read-keymap
python3 -m kemove.probe --read-keymap --keymap-size 504 --save captures/k87-keymap-probe.bin
```

`--read-keymap` sets the read bit (`0x83`), sends one request, and reads the
reply. It does **not** write a block. Run it with the vendor app closed and only
the keyboard under test connected.

The K61SE reader assumes 504 bytes (126 four-byte cells). The K87 layout has
108 possible LED positions (18 × 6), but the keymap block may still be 504
bytes or may be 432/512 bytes. The probe should settle that before any writer is
pointed at the K87.

## Modularity

The new `kemove/` package is the start of the model-aware layer:

- `kemove/devices.py` is the single registry of VID/PID, product strings,
  transports, keymap sizes, LED counts, and layout XML paths.
- `kemove/probe.py` is a read-only inventory and keymap probe.
- `tests/test_devices.py` verifies profile matching without hardware.

The existing `k61se_*.py` tools are still the verified K61SE implementation.
They should be migrated onto the profile registry rather than copied for every
new model. The next refactor should move the transport and block codecs behind
the profile object, then make the CLI wrappers accept `--profile`/auto-detect.

## Next checks

1. Run `python3 -m kemove.probe --read-keymap --save captures/k87-keymap-probe.bin`
   with only the K87-1980 connected.
2. Compare the returned body with the K61SE reset fixture.
3. Read the K87 lighting block and compare the mode table with the K87 XML.
4. Repeat over the K87 2.4G receiver; if it appears as `3554:FA09`, implement or
   reuse the `CDrv3632` framing.
5. Only after the read-only checks pass, add K87 write support with the same
   pre-write readback and restore guards used by the K61SE tools.
