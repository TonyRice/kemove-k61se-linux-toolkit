# Research tools

These are the reverse-engineering and capture utilities. They are not part of
the runtime CLI and are not imported by the daily tools.

Run them from the repository root so the shared modules are importable:

```bash
python3 research/dump_lighting.py --help
python3 research/review_dumps.py
python3 research/review_captures.py --summary
sudo python3 research/snoop_session.py
```

The scripts add the repository root to `sys.path` themselves, so the direct
`python3 research/...` form works without installing the package.

Evidence written by these tools goes under `evidence/`:

- `evidence/captures/` — raw USB captures from `snoop_session.py` / `sniffer.py`
- `evidence/dumps/` — lighting and colour block dumps from `dump_lighting.py`

The small canonical fixtures are tracked in `evidence/captures/`:
`keymap-reset.bin`, `keymap-stock.bin`, `golden-reset/`, and `k87-probe/`.
The bulk capture/dump archive is local-only and Git-ignored.
