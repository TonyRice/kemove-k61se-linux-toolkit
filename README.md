# K61SE Linux control

Host-side tools for the KEMOVE K61SE keyboard (`258A:010C`). The protocol was
reverse-engineered from USB captures of the vendor app; see `PROTOCOL.md`.

Close the vendor app before running a lighting or keymap writer. One writer at
a time: the app keeps re-asserting its own state and will fight the host-side
tools.

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
python3 k61se_keymap.py --read

# Read-only full-state snapshot (lighting, colour, keymaps, macros, info)
python3 k61se_snapshot.py --capture captures/reset-before
# Now hold Fn + Tab for 5 seconds, then:
python3 k61se_snapshot.py --capture captures/reset-after
python3 k61se_snapshot.py --diff captures/reset-before captures/reset-after

# Explicit golden-reset restore: lighting, colour, keymap layer 0
python3 k61se_snapshot.py --restore captures/golden-reset --yes

# One-shot macro payload validation
python3 k61se_macro_write.py --name test1 --text abc --dry-run
python3 k61se_macro_write.py --name test1 --text abc --yes

# Bind macro index 0 to the Menu key, then restore it
python3 k61se_keymap.py --set 'Menu=macro:0' --yes
python3 k61se_keymap.py --set 'Menu=Menu' --yes

# One-time permission setup for the host-side arrow daemon
./install_udev.sh

# Sticky arrow layer: double-tap CapsLock to toggle
python3 -B k61se_arrows.py

# Offline remap dry-run: no device access with --base-file
python3 k61se_keymap.py --set Space=B --dry-run \
  --base-file captures/keymap-reset.bin

# Explicit restore of the captured factory keymap (overwrites all remaps)
python3 k61se_keymap.py --restore-stock --yes
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
| `k61se_keymap.py` | Explicit keymap read/remap/restore; pre-write dump and verified readback. |
| `k61se_snapshot.py` | Full-state snapshots/diffs plus explicit verified restore; macros skipped. |
| `k61se_invaders.py` | Invaders-style animation demo. |
| `k61se_power.py` | Mode/power control and recovery helpers. |
| `k61se_gui.py` | Effect/preset test bench. |

## Required data

- `app/K61SE-keyboard.xml` — authoritative key names, HID codes and `row_col`.
- `captures/keymap-reset.bin` — canonical 504-byte Fn+Tab reset keymap used by
  `k61se_keymap.py --restore-stock`. `captures/keymap-stock.bin` is the older
  app readback and is kept for comparison.
- `captures/golden-reset/` — verified Fn+Tab reset snapshot used by
  `k61se_snapshot.py --restore`.
- `captures/reset-*` — before/after snapshot evidence (`snapshot.json` plus raw
  block bodies) captured by `k61se_snapshot.py`.
- `designs/` — static key art and music-design JSON.
- `presets/` — saved lighting/colour block presets.
- `.k61se_state.json` — last drawable mode used by `k61se_power.py`.

## Reference and evidence

These are useful for protocol work but not required to run the daily tools:

- `PROTOCOL.md` — full reverse-engineered protocol.
- `colour_records.md`, `colour_mode_audit.md`, `app_effect_walk.md` — colour
  and effect analysis.
- `KBAccess_notes.txt` — original vendor-access notes.
- `WINE.md` — running the Windows vendor app under Wine.
- `captures/` — raw USB captures, indexed by `captures/SESSION_INDEX.md`.
- `dumps/` — 128-byte lighting and 420-byte colour block snapshots.
- `dump_lighting.py`, `review_captures.py`, `review_dumps.py`, `sniffer.py`,
  `snoop_session.py`, `read_desc.py`, `payloads.py` — read-only research tools.
- `k61se_music_audio.sh` — PipeWire loopback for the vendor app's music mode.

`app/` also contains the extracted vendor app (`.exe`, DLLs, skins, layouts,
language files). The Linux tools only need `app/K61SE-keyboard.xml`; keep the
rest only if you still want to run the app under Wine.

`attic/` contains old one-off HID probes from the first reverse-engineering
sessions. They are not imported by the current tools and are safe to delete.

## Housekeeping

Two directories were created by earlier `sudo` runs and are still owned by
root:

```bash
sudo rm -rf __pycache__
sudo chown -R "$USER:$USER" dumps
```

The first is only a generated bytecode cache. The second lets `dump_lighting.py
--save` write new evidence files without `sudo`.
