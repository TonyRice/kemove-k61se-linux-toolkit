# Designs

Two kinds, both plain JSON.

## Static key designs — `k61se_keys.py --file ...`

```json
{
  "name": "wasd",
  "keys": {"W": "00FF00", "A": "0000FF", "S": "FFFFFF", "D": "FF0000"},
  "cells": {"1": "FF0000"},
  "all": "000000",
  "fill": "101010"
}
```

- `keys` are names from `app/K61SE-keyboard.xml`; `--list-keys` prints them.
  A duplicated name (`Shift`, `Ctrl`, `Alt`) paints every copy.
- `cells` are raw `key_index` values (`1..83` are used).
- `all` paints the 61 key positions; `fill` paints all 14 x 5 matrix cells,
  including the gaps.
- `text` draws a short string with a 3x4 block font (see below). `text_colour`
  sets its colour (default `FFFFFF`). `rainbow: true` colours `fill`, `all` and
  `text` by physical column; explicit `keys`/`cells` still win.
- Painting order: CLI `--fill`/`--all` background, then the design file
  (`fill`, `all`, `text`, `keys`, `cells`), then CLI `--text`, then CLI
  `--key`/`--cell`. Later entries override earlier ones.
- Colours are `RRGGBB`, wire order `(G,R,B)`.

Run:

```bash
python3 k61se_keys.py --file designs/keys/wasd.json --dry-run
python3 k61se_keys.py --file designs/keys/wasd.json
python3 k61se_keys.py --file designs/keys/lol.json
python3 k61se_keys.py --file designs/keys/lol_cells.json
python3 k61se_keys.py --file designs/keys/lol_rainbow.json
python3 k61se_keys.py --file designs/keys/rainbow_all.json
python3 k61se_keys.py --all FFFFFF --rainbow
python3 k61se_keys.py --text LOL --rainbow
python3 k61se_keys.py --text HI --text-colour 00FFFF
python3 k61se_keys.py --solid FF0000        # persistent, no stream
```

### Text and custom key art

`--text` renders a short string across the 14-column board, using the key LEDs
as pixels. It draws into the top four matrix rows (the bottom row has large gaps
in the physical layout), and centres the glyphs horizontally. `LOL` fits
comfortably; lower-case input is uppercased. The font covers `A-Z`, `0-9`,
space, `-`, `.`, `!` and `?`.

This is deliberately a tiny demo font, not a text layout engine. At 14 x 5,
four-letter words are tight and can collide with the bottom-row gaps; `HELLO`
is too wide. For anything beyond a short word, use explicit `cells` — or a
future canvas/editor that lets you draw the pixels directly.

```bash
python3 k61se_keys.py --text LOL --dry-run
python3 k61se_keys.py --text LOL --rainbow
python3 k61se_keys.py --text A --text-colour FF00FF
```

For a design file, the same fields are available:

```json
{
  "name": "LOL rainbow",
  "text": "LOL",
  "rainbow": true
}
```

Anything more precise can use `cells`: every lit `key_index` is one pixel.
For example, `designs/keys/lol.json` uses `text`, while the WASD design uses
named `keys`. `designs/keys/lol_cells.json` draws the same word with explicit
cells and a different colour for each letter, which is the pattern to copy for
custom palettes or shapes. There is no need to stick to key labels — any used
index paints the corresponding LED, so you can draw a word, a shape or a
mini-palette.

`0x08` frames are transient: the firmware returns to its configured effect
when the stream stops. The tool streams continuously by default (until
Ctrl-C); `--hold 30` streams for 30 seconds, `--once` sends one frame, and
`--restore` writes the lighting block back when the stream ends. Keep the
process running to keep the art; for a background design, use `nohup` or a
small user service.

For one **uniform** colour, don't use `0x08` at all: `--solid RRGGBB` writes
the firmware's own solid effect (`0x01`) once, so it persists with no stream.

`--fps` is the keepalive rate, not a bandwidth problem: 20 fps is only about
10 kB/s and matches the app's ~22 fps. The default is now **1 fps**, which was
steady on the actual keyboard for a 10-second WASD test. If a particular frame
shows the old effect between updates, raise it (`--fps 5`, `10`, `20`); there is
no need to stream faster than the overlay timeout.

## Music designs — `k61se_music_live.py --design-file ...`

```json
{
  "name": "fire bars",
  "design": "bars",
  "palette": ["FF0000", "FF8000", "FFFF00"],
  "mode": "height",
  "flat": false
}
```

- `design`: `bars`, `mirror` (folds the 14 bands into seven pairs, then mirrors
  them: bass in the centre, treble at both edges), `peak` (falling peak dots),
  `pulse` (whole-board bass flash) or `psychedelic` (six morphing patterns:
  plasma, tunnel, spiral, vortex, ripple and kaleidoscope, cross-fading every
  seven seconds).
- `palette`: a built-in name (`app`, `rainbow`, `fire`, `ice`) or a list of
  `RRGGBB` stops.
- `mode`: `column` (colour by frequency) or `height` (colour by bar height).
- `flat`: `true` removes the vertical brightness gradient.
- `max_brightness`: `true` drives every lit cell at its full palette colour,
  with no level or vertical dimming.
- `colour`: optional fixed colour, overriding the palette.

Run:

```bash
python3 k61se_music_live.py --design-file designs/music/fire_bars.json
python3 k61se_music_live.py --design-file designs/music/psychedelic.json
python3 k61se_music_live.py --design psychedelic --palette rainbow
python3 k61se_music_live.py --design psychedelic --max-brightness
python3 k61se_music_live.py --palette rainbow --design peak
python3 k61se_music_live.py --palette-file designs/music/sunset_palette.json
```

A palette file is either a plain list or an object with `stops` and `mode`:

```json
{"stops": ["FF0040", "FF8000", "FFD000"], "mode": "column"}
```
