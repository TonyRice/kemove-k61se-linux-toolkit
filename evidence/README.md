# Evidence

Local reverse-engineering evidence, kept out of the runtime package.

- `captures/` — raw USB captures, one folder per action/session
- `dumps/` — 128-byte lighting and 420-byte colour block snapshots

Only small canonical fixtures are tracked in Git:

```text
captures/keymap-reset.bin
captures/keymap-stock.bin
captures/golden-reset/
captures/k87-probe/
captures/k87-before/
captures/SESSION_INDEX.md
```

Everything else is local-only and ignored. See `../research/README.md` for the
tools that generate this data.
