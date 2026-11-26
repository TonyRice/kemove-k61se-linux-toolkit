# KEMOVE K61SE/K87 Linux control

Host-side tools for KEMOVE keyboards on Linux. The K61SE (`258A:010C`) is the
verified target; the K87-1980 (`258A:010C`) and K87SE-1980 (`258A:0164`) are
provisional profiles while their keymap/lighting blocks are measured. The
protocol was reverse-engineered from USB captures of the vendor app; see
`PROTOCOL.md` and `docs/DEVICES.md`.

Close the vendor app before running a lighting or keymap writer. One writer at
a time: the app keeps re-asserting its own state and will fight the host-side
tools.

## Device inventory

```bash
# Read-only: does not open the keyboard
python3 -m kemove.probe
python3 -m kemove.probe --json

# Read-only keymap/lighting/colour probe for a recognised wired G5KB device
python3 -m kemove.probe --read-keymap
python3 -m kemove.probe --read-all --save-dir evidence/captures/k87-probe

# Read-only lighting/colour inspection
python3 -m kemove.lighting_cli read --profile k87-1980
python3 -m kemove.lighting_cli read --profile k87-1980 --save-dir evidence/captures/k87-live

# Lighting change: dry-run first, then an explicit verified write
python3 -m kemove.lighting_cli set --profile k87-1980 --brightness 3 --dry-run
python3 -m kemove.lighting_cli set --profile k87-1980 --brightness 3 --yes --force
python3 -m kemove.lighting_cli set --profile k87-1980 \
  --side-mode 2 --side-colour 1 --dry-run
python3 -m kemove.lighting_cli set --profile k87-1980 \
  --restore evidence/captures/k87-before/lighting.bin --yes --force
```

The probe is the safe first step for a new model: it reports the exact product
string, VID/PID, interface, and usage page. Product-string matching matters
because the K61SE, K87-1980, K68SE, and K98-1980 share `258A:010C`.

`kemove_keymap.py` and `k61se_macro.py` auto-detect one connected keyboard and
also accept `--profile`:

```bash
python3 kemove_keymap.py --read
python3 kemove_keymap.py --profile k87-1980 --read
python3 kemove_keymap.py --profile k87-1980 \
  --base-file evidence/captures/k87-probe/keymap.bin --set A=B --dry-run
```

K87 writes are read-only/dry-run until the hardware round-trip checks are
finished.

## Repository layout

| Directory | Purpose |
|---|---|
| `kemove/` | Shared profiles, discovery, layout/lighting/snapshot codecs, read-only probe |
| `k61se_*.py` | Daily runtime tools and the verified K61SE implementation |
| `research/` | Capture, dump, and reverse-engineering scripts |
| `evidence/` | Local captures and dumps; only canonical fixtures are tracked |
| `docs/` | Protocol/device notes and plans |
| `archive/` | Old one-off experiments, not imported by current tools |
| `app/` | Vendor app binaries (ignored) plus the keyboard XML layout |

## Quick start

```bash
# Read-only key detection: no LED writes
python3 k61se_typing.py --dry-run --echo

# Neon Storm typing feedback (default)
python3 k61se_typing.py

# Timed test
python3 k61se_typing.py --seconds 60

# Read-only keymap/macro probe: no config writes
python3 k61se_macro.py --probe

# Read-only keymap decode
python3 kemove_keymap.py --read

# Read-only full-state snapshot (lighting, colour, keymaps, macros, info)
python3 k61se_snapshot.py --capture evidence/captures/reset-before
# K87:
python3 k61se_snapshot.py --profile k87-1980 --capture evidence/captures/k87-before
# Now hold Fn + Tab for 5 seconds, then:
python3 k61se_snapshot.py --capture evidence/captures/reset-after
python3 k61se_snapshot.py --diff evidence/captures/reset-before evidence/captures/reset-after

# Explicit golden-reset restore: lighting, colour, keymap layer 0
python3 k61se_snapshot.py --restore evidence/captures/golden-reset --yes

# One-shot macro payload validation
python3 k61se_macro_write.py --name test1 --text abc --dry-run
python3 k61se_macro_write.py --name test1 --text abc --yes

# Bind macro index 0 to the Menu key, then restore it
python3 kemove_keymap.py --set 'Menu=macro:0' --yes
python3 kemove_keymap.py --set 'Menu=Menu' --yes

# One-time permission setup for the host-side arrow daemon
./install_udev.sh

# Sticky arrow layer: double-tap CapsLock to toggle
python3 -B k61se_arrows.py

# Offline remap dry-run: no device access with --base-file
python3 kemove_keymap.py --set Space=B --dry-run \
  --base-file evidence/captures/keymap-reset.bin

# Explicit restore of the captured factory keymap (overwrites all remaps)
python3 kemove_keymap.py --restore-stock --yes
```

`Ctrl-C` restores the captured lighting block. The `0x08` lighting frame is a
complete overlay, so keys not painted by the effect are black while the tool
runs.

`k61se_macro_write.py` is an explicit hardware experiment: it saves the old
macro area, writes one F75-style macro, reads it back, and does **not** bind it
to a key. If the readback is wrong, hold Fn+Tab for 5 seconds to recover.

`k61se_arrows.py` is a host-side daemon, not a firmware write. It grabs the
K61SE keyboard event node, creates a uinput virtual keyboard, and toggles the
arrow layer on a CapsLock double-tap. Stop it with `Ctrl-C`.

The existing AULA F75 udev rule covers `hidraw`, not `input` event nodes, so the
arrow daemon needs `packaging/70-kemove-input.rules`. `./install_udev.sh`
installs it; replug the keyboard once afterward. Until then, run the daemon
with `sudo python3 -B k61se_arrows.py`.

## Daily tools

| File | Purpose |
|---|---|
| `k61se_dev.py` | Shared HID/device layer: feature reports, blocks, reconnect handling. |
| `k61se_layout.py` | Lighting-block constants, effect names, state summaries. |
| `k61se_keys.py` | Static `0x08` key art, text, `--fill`/`--all`, and persistent `--solid`. |
| `k61se_music.py` | Send/test a single `0x08` music frame. |
| `k61se_music_live.py` | Live audio capture and animated music rendering. |
| `k61se_typing.py` | Host-side typing feedback: Neon Storm, hold, trail, ripple. |
| `k61se_input.py` | Read-only keyboard report monitor; maps HID usages to `key_index`. |
| `k61se_macro.py` | Read-only keymap/macro probe and offline decoder; no config writes. |
| `k61se_macro_write.py` | One-shot F75-layout macro payload write + readback; no key binding. |
| `k61se_arrows.py` | Host-side sticky arrow layer: double-tap CapsLock to remap the four Fn-arrow keys. |
| `kemove_keymap.py` | Explicit keymap read/remap/restore; pre-write dump and verified readback. |
| `k61se_snapshot.py` | Full-state snapshots/diffs plus explicit verified restore; macros skipped. |
| `k61se_invaders.py` | Invaders-style animation demo. |
| `k61se_power.py` | Mode/power control and recovery helpers. |
| `k61se_gui.py` | Effect/preset test bench. |

## Required data

- `app/K61SE-keyboard.xml` — authoritative key names, HID codes and `row_col`.
- `evidence/captures/keymap-reset.bin` — canonical 504-byte Fn+Tab reset keymap used by
  `kemove_keymap.py --restore-stock`. `evidence/captures/keymap-stock.bin` is the older
  app readback and is kept for comparison.
- `evidence/captures/golden-reset/` — verified Fn+Tab reset snapshot used by
  `k61se_snapshot.py --restore`.
- `designs/` — static key art and music-design JSON.
- `presets/` — saved lighting/colour block presets.
- `.k61se_state.json` — last drawable mode used by `k61se_power.py`.

The public repository tracks the source, docs, `app/K61SE-keyboard.xml`, and the
two small reset fixtures above. The vendor app binaries, the full `evidence/captures/`
archive, `evidence/dumps/`, and `.k61se_state.json` stay local and are Git-ignored. A
fresh clone still runs the runtime tools; only the Wine/vendor-app workflows
and the full raw evidence archive need the local files.

## Reference and evidence

These are useful for protocol work but not required to run the daily tools:

- `PROTOCOL.md` — full reverse-engineered protocol.
- `docs/research/colour_records.md`, `docs/research/colour_mode_audit.md`, `docs/research/app_effect_walk.md` — colour
  and effect analysis.
- `docs/research/KBAccess_notes.txt` — original vendor-access notes.
- `docs/research/WINE.md` — running the Windows vendor app under Wine.
- `evidence/captures/` — local raw USB captures, indexed by `evidence/captures/SESSION_INDEX.md`.
  Only `golden-reset/`, `k87-probe/`, `k87-before/`, `keymap-reset.bin`,
  `keymap-stock.bin`, `k87-side-*` evidence, and the index are tracked.
- `evidence/dumps/` — local 128-byte lighting and 420-byte colour block snapshots; not
  tracked.
- `research/dump_lighting.py`, `research/review_captures.py`,
  `research/review_dumps.py`, `research/sniffer.py`, `research/snoop_session.py`,
  `research/read_desc.py`, `research/payloads.py` — read-only research tools.
- `k61se_music_audio.sh` — PipeWire loopback for the vendor app's music mode.

On this machine `app/` also contains the extracted vendor app (`.exe`, DLLs,
skins, layouts, language files), but those binaries are Git-ignored. The Linux
tools only need `app/K61SE-keyboard.xml`; keep the rest locally if you still
want to run the app under Wine.

`archive/attic/` contains old one-off HID probes from the first reverse-engineering
sessions. They are not imported by the current tools and are safe to delete.

## Housekeeping

Two directories were created by earlier `sudo` runs and are still owned by
root:

```bash
sudo rm -rf __pycache__
sudo chown -R "$USER:$USER" evidence/dumps
```

The first is only a generated bytecode cache. The second lets `research/dump_lighting.py --save` write new evidence files
without `sudo`.
