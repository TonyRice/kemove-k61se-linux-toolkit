# Vendor app effect walk

Run 2026-10-19 13:52 via `sudo python3 dump_lighting.py --app-walk`.

The vendor app was the only writer. Each snapshot is one read of the
lighting and colour blocks taken right after the click, saved in `dumps/`
as `<block>_<timestamp>_app_<mode>_<effect>_colourful.bin` and `..._single.bin`.

| mode | effect | colourful slot | colour off slot | colour G/R/B | verdict |
|---|---|---|---|---|---|
| `0x01` | solid | `05 57` | `05 50` | `00/FF/00` | flag cleared and this effect has a colour renderer — real colour |
| `0x02` | pulsing | `05 57` | `05 50` | `00/FF/00` | flag cleared and this effect has a colour renderer — real colour |

Bytes the app moved between the two renderings:

- `0x01` solid: 0x3B entry table (34 x 2 bytes): 57 -> 50; colour 0x16 RED channel (FF = on): 80 -> FF
- `0x02` pulsing: 0x3D entry table (34 x 2 bytes): 57 -> 50

A single changed byte inside the effect's own parameter word is the
expected answer (brightness low, speed high, flag nibble low). Anything
else — a moved mode byte, a new offset, an unchanged flag — is the
interesting result: it means the app colours these effects by some path
we have not decoded yet.
