# K61SE Toolkit: Rust Core Evaluation and Migration Plan

**Status:** Proposed / tabled  
**Date:** 2026-11-25  
**Repository:** `TonyRice/kemove-k61se-linux-toolkit`

## 0. Multi-device update (2026-11-25)

The K87-1980 is now part of the target set. It shares `258A:010C` with the
K61SE, so the product string must be part of device selection. A read-only HID
inventory sees the K87-1980 as interface 1 with product string
`KEMOVE K87-1980`; see `docs/DEVICES.md` and `kemove/devices.py`.

This changes the core requirement from “one K61SE device” to a profile-based
device layer:

- VID/PID + product-string matching;
- G5KB wired transport and CDrv3632 2.4G transport;
- per-model keymap length, LED count, and layout XML;
- per-model lighting/effect tables where they differ.

The K87-1980 keymap read is confirmed: 504 bytes, 126 cells, factory XML
matches 86/86. The K87 key indices differ from the K61SE (Esc is cell 0; Fn is
cell 59). The lighting block is also 128 bytes with the same parameter-table
shape. The colour block is the real dialect difference: 420 bytes, but each
21-byte record holds seven RGB triples instead of the K61SE's single RGB triple.
The model layer therefore needs a `palette7` colour codec. Writes remain
unverified; do not enable K87 writes until hardware readback confirms them.

## 1. Context

The current toolkit is a Python project with a flat set of scripts:

- Device transport and protocol: `k61se_dev.py`
- Keymap: `k61se_keymap.py`
- Macros: `k61se_macro.py`, `k61se_macro_write.py`
- Snapshots: `k61se_snapshot.py`
- Sticky arrow daemon: `k61se_arrows.py`
- Lighting/colour/power/typing/music: `k61se_layout.py`, `k61se_keys.py`,
  `k61se_power.py`, `k61se_typing.py`, `k61se_music*.py`
- Tkinter GUI: `k61se_gui.py`
- Research and capture tools: `research/sniffer.py`, `research/snoop_session.py`,
  `research/dump_lighting.py`, `review_*.py`, `research/read_desc.py`, `research/payloads.py`
- Protocol reference: `PROTOCOL.md`

The device-facing surface is relatively small: open the HID device, exchange
fixed-size reports, encode/decode a handful of byte layouts, and run one
evdev/uinput daemon. The main complexity is in the lighting effects, music
modes, and the 1,500-line Tkinter GUI.

The current GUI is not a priority and is considered unsatisfactory. The next
implementation should focus on the core, CLI, and daemon first.

## 2. Goal

Choose and document an implementation path for the **main pieces** of the
toolkit, with a clean architecture that can support a better application later.

The recommended direction is:

> **Rewrite the core, CLI, and arrow daemon in Rust. Keep Python for research
> and reverse engineering. Defer the GUI.**

This is a recommendation, not an immediate rewrite commitment. A short
validation spike should confirm the two native boundaries: HID and evdev/uinput.

## 3. Non-goals

- Do not rewrite the whole repository at once.
- Do not port the GUI in the first phase.
- Do not replace the Python research/capture tools.
- Do not change the device protocol or canonical reset fixtures.
- Do not depend on the vendor app or Wine for normal operation.
- Do not remove working Python tools until the Rust equivalents have reached
  parity and passed hardware tests.

## 4. Options Considered

### Option A: Rust core, CLI, and daemon

**Pros**

- Native single binary; no Python runtime for production tools.
- Direct HID access through `hidapi` or raw hidraw.
- Mature `evdev` and `uinput` crates for the arrow daemon.
- Strong typing for protocol records and readback checks.
- Good fit for a long-running daemon and systemd user service.
- Compile times are manageable for a project of this size.
- Cleaner long-term foundation than a collection of scripts.

**Cons**

- Requires porting the core logic.
- `hidapi` static builds may need a C toolchain and `libudev`.
- Hardware validation still has to be repeated.
- Python and Rust may both exist during migration.
- GUI work is still deferred.

### Option B: GraalPy standalone native binary

GraalPy can produce a standalone native executable that bundles Python code,
the runtime, and the standard library. This is a real option at this point.

**Pros**

- Keeps Python and most of the existing structure.
- Produces a native executable without a separate Python install.
- Could reduce the amount of code that must be rewritten.

**Cons / risks**

- The current `hid` dependency is a CPython C extension; GraalPy's native
  extension support varies by platform and package.
- GraalPy 25.3 targets Python 3.13, while the installed `hid` extension is
  built for CPython 3.14.
- The arrow daemon uses low-level `fcntl`, `ioctl`, and `select`; these are
  platform-specific APIs that need explicit testing.
- Tkinter support is uncertain, though the GUI is deferred.
- Native builds are relatively slow and produce large binaries.
- If the HID binding must be replaced anyway, much of the “no rewrite”
  advantage disappears.

**Decision:** Keep as a timeboxed experiment, not the primary plan.

### Option C: Nuitka or PyInstaller

**Pros**

- Most predictable way to ship the current Python code.
- Handles CPython C extensions such as `hid`.
- Nuitka can compile to C; PyInstaller bundles the interpreter.
- Much less work than a rewrite.

**Cons**

- Not a true native rewrite.
- Binary size and startup are worse than Rust.
- The GUI remains Tkinter.
- Packaging still has to handle native libraries and permissions.

**Decision:** Fallback if the goal becomes “ship the existing Python toolkit”
rather than “build a clean long-term core.”

### Option D: Java + GraalVM Native Image

**Pros**

- Strong typing and good tooling.
- JavaFX is available if a desktop GUI becomes important.
- Native image can produce a single binary.

**Cons**

- HID and evdev/uinput require JNA or FFM bindings and native-library
  packaging.
- JavaFX + native image adds significant build complexity.
- Native-image builds are slow and memory-heavy.
- The main advantage, JavaFX, is explicitly not a priority.

**Decision:** Rejected for now. Revisit only if a JavaFX-based GUI becomes a
real requirement.

## 5. Recommendation

Adopt **Rust for the main pieces**, with the following boundaries:

- Rust owns the HID transport, protocol codecs, CLI, snapshot/keymap/macro
  operations, and the arrow daemon.
- Python remains the research and reverse-engineering environment.
- The existing Tkinter GUI remains in place until a replacement is justified.
- GraalPy and Nuitka remain documented fallbacks, not the default path.
- The protocol remains defined by `PROTOCOL.md` and the golden fixtures.

The core is small enough that this is a realistic port, and Rust's native
binary + direct Linux I/O fit the daemon and CLI better than a Python runtime
or a JVM native image.

## 6. Proposed Architecture

```text
Cargo.toml                    # workspace
crates/
  k61se-core/
    src/
      lib.rs
      error.rs
      device.rs               # HID transport abstraction
      protocol/
        frame.rs
        info.rs
        layout.rs
        keymap.rs
        macro.rs
        lighting.rs
        colour.rs
      snapshot.rs
      linux/
        evdev.rs              # cfg(target_os = "linux")
        uinput.rs
  k61se-cli/
    src/main.rs               # `k61se` binary
  k61se-arrows/
    src/main.rs               # optional separate daemon
  k61se-gui/                  # future
data/
  fixtures/                   # golden reset/keymap/macro bytes
docs/
research/                     # Python capture and RE tools
packaging/
  udev/
  systemd/
```

### Core principles

- Keep protocol/transport logic independent of the CLI and GUI.
- Use a `Transport` abstraction so tests can use a fake device.
- Use typed errors; avoid panics for device/protocol failures.
- Preserve dry-run, readback, and restore-guard behavior from the Python tools.
- Expose JSON output for scripting and human-readable output by default.
- Keep Linux-specific evdev/uinput code behind `cfg(target_os = "linux")`.
- Use `serde` for snapshots and configuration.
- Use golden fixtures for offline tests.

### Proposed CLI shape

```bash
k61se probe
k61se keymap read
k61se keymap set ...
k61se keymap restore-reset
k61se snapshot capture ...
k61se snapshot diff ...
k61se snapshot restore ...
k61se macro write ...
k61se lighting ...
k61se arrows
```

Start with one `k61se` binary and subcommands. Split `k61se-arrows` into a
separate binary later only if systemd/permissions or dependency size make that
useful.

### Rust dependencies to evaluate

- `hidapi` with a Linux hidraw/static build, or a raw `hidraw` implementation
- `evdev`
- `uinput`
- `clap`
- `serde` / `serde_json`
- `thiserror`
- `tracing`

## 7. Porting Map

| Current Python | Rust destination | Priority |
|---|---|---|
| `k61se_dev.py` | `k61se-core::device`, `protocol::frame` | 1 |
| `k61se_layout.py` | `k61se-core::protocol::layout` | 1 |
| `k61se_keymap.py` | `k61se-core::protocol::keymap` | 1 |
| `k61se_macro.py` | `k61se-core::protocol::macro` | 2 |
| `k61se_macro_write.py` | `k61se-core::protocol::macro` | 2 |
| `k61se_snapshot.py` | `k61se-core::snapshot` | 2 |
| `k61se_arrows.py` | `k61se-core::linux`, `k61se-arrows` | 3 |
| `k61se_power.py` | `k61se-core::protocol::lighting` | 4 |
| `k61se_keys.py` | `k61se-core::protocol::lighting` | 4 |
| `k61se_typing.py` | CLI/effects layer | 4 |
| `k61se_music*.py` | later / optional | 5 |
| `k61se_gui.py` | future GUI | deferred |
| `research/sniffer.py`, `research/snoop_session.py`, `review_*.py`, `research/dump_lighting.py`, `research/read_desc.py` | keep Python in `research/` | n/a |
| `PROTOCOL.md` | shared specification | source of truth |
| `evidence/captures/golden-reset/`, `evidence/captures/keymap-reset.bin` | `data/fixtures/` | test fixtures |

## 8. Migration Phases

### Phase 0: Validation spike

Timeboxed experiment before committing to the full port.

Rust spike:

1. Create a small Rust binary using `hidapi`.
2. Enumerate `258A:010C`.
3. Read the keymap feature report.
4. Decode one or more cells.
5. Build a release binary and run it without `sudo` after the udev rule.

GraalPy spike (optional, in parallel):

1. Install GraalPy.
2. Try importing/using `hid`.
3. Run a read-only keymap probe.
4. Build a standalone native executable.
5. Test the `fcntl`/`ioctl` path for evdev.

Proceed with Rust if the HID read and release build are clean. Reconsider if
the native-library/build friction is unexpectedly high.

### Phase 1: Read-only Rust core

- Workspace and crate layout.
- HID transport.
- Frame/report abstraction.
- Info and keymap decoding.
- `k61se probe` and `k61se keymap read`.
- Golden tests against existing fixtures.
- Verify output matches the Python reader.

### Phase 2: Write operations

- Keymap write/restore with pre-write evidence and readback.
- Macro payload write with readback.
- Snapshot capture/diff/restore.
- Preserve restore safety rules:
  - keymap layer 0 only by default
  - layers > 0 require explicit force
  - macros skipped in full snapshot restore unless explicitly supported
- Hardware validation after each write path.

### Phase 3: Arrow daemon

- evdev grab and forwarding.
- uinput virtual keyboard.
- Double-tap CapsLock toggle.
- Preserve single-tap CapsLock behavior.
- Map `/ ?`, Right Alt, Menu/Compose, and Right Ctrl.
- Reconnect/hotplug handling.
- systemd user service and udev rule.
- Test without `sudo`.

### Phase 4: Lighting, colour, and optional effects

- Port the core lighting/colour records.
- Decide whether typing/music/power modes belong in Rust or remain Python.
- Do not port experimental effects unless they are actively used.

### Phase 5: Packaging

- Release binary build.
- udev rules.
- systemd user unit.
- Debian package or AppImage, if desired.
- Install/upgrade documentation.

### Phase 6: GUI (deferred)

Do not start until the CLI and daemon are stable.

Potential options:

- `egui`/`eframe`: simplest single-binary UI.
- `Tauri`: best-looking UI with web frontend, heavier stack.
- `iced` or `Slint`: middle ground.
- `gtk4-rs`: native Linux look, heavier dependencies.

The GUI choice should be made after the core API is stable.

## 9. Testing Strategy

### Offline tests

- Frame encode/decode round-trips.
- Keymap decode/encode against golden reset bytes.
- Macro payload construction against captured payloads.
- Snapshot JSON and raw-block diffing.
- Arrow mapping and toggle state machine, with no real device.
- CLI output and error cases.

### Hardware tests

- Read-only probe and keymap read.
- Keymap write/readback.
- Macro write/readback.
- Snapshot capture/diff/restore.
- Fn+Tab reset and canonical fixture comparison.
- Arrow daemon with all four arrow sources.
- udev permissions without `sudo`.

Hardware tests should be opt-in and never run automatically in CI.

## 10. Risks

| Risk | Mitigation |
|---|---|
| `hidapi` native build/packaging friction | Spike early; use static hidraw build or raw hidraw fallback |
| evdev/uinput ioctl differences | Keep Linux code isolated; test on the actual K61SE |
| Protocol mistakes during port | Golden fixtures and side-by-side Python comparison |
| Python/Rust divergence during migration | Freeze protocol spec; port one operation at a time |
| Rust compile times | `cargo check`, incremental builds, `mold`/`lld`, `sccache` |
| GraalPy C-extension incompatibility | Timeboxed spike; do not make it the critical path |
| GUI becomes a distraction | Explicitly deferred until core parity |
| Losing research tooling | Keep Python research scripts and captures intact |

## 11. Open Questions

1. Is the target Linux-only, or should the core be cross-platform?
2. Which “main pieces” are mandatory for the first Rust release?
   Proposed: probe, keymap, macro, snapshot, arrows.
3. Should lighting/colour/power/typing be in the first release or later?
4. One binary with subcommands, or a separate `k61se-arrows` daemon?
5. Should the Python GUI remain the supported UI during the transition?
6. What distribution format matters: source build, `.deb`, AppImage, or all?
7. Is GraalPy worth a spike, or should the project go straight to Rust?
8. Are there any protocol areas that still need hardware verification before
   they are ported?

## 12. Decision Log

- **2026-11-25:** Java + GraalVM native image rejected as the default path.
  Rust is a better fit for the Linux HID/evdev core and avoids native-image
  complexity.
- **2026-11-25:** GraalPy standalone recognized as a viable experiment, but
  current `hid`/`fcntl`/Tkinter dependencies make it risky as a drop-in.
- **2026-11-25:** GUI is explicitly deferred. Tkinter remains until a
  replacement is justified.
- **2026-11-25:** Python remains the research and reverse-engineering layer.
- **2026-11-25:** Rust is the proposed implementation language for the core,
  CLI, and arrow daemon, subject to the Phase 0 spike.

## 13. Next Action

Run the Phase 0 Rust spike:

- open the K61SE through `hidapi`
- read and decode the keymap
- build a release binary
- verify it runs without `sudo` under the existing udev rule

Do not begin the full port until that spike is complete.
