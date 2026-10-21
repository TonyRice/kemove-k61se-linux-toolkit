# K61SE colour-mode audit

Run 2026-10-19 15:12 via `python3 k61se_gui.py --audit`.

Each effect was written twice: rainbow flag on (`entry[mode-1]+1` low
nibble `7`), then flag off plus colour red `(255,0,0)` (`0x50`). The
answers below are what the panel actually did; `dark` means the panel
went blank, i.e. the firmware drew nothing at all for that write.

Supersedes the first run of 2026-10-19, which wrote solid's record for
every effect; that run's nine "dark" verdicts are retracted (PROTOCOL.md
§3, `colour_records.md`). The panel is the only instrument here.

| mode | effect | colourful | single colour | verdict |
|---|---|---|---|---|
| `0x01` | solid | yes | yes | both renderings |
| `0x02` | pulsing | yes | yes | both renderings |
| `0x03` | rotating | yes | no | flag ignored — still colourful |
| `0x04` | typing feedback | yes | yes | both renderings |
| `0x05` | rainbow pixel changing | yes | yes | both renderings |
| `0x07` | typing feedback, whole keyboard | yes | yes | both renderings |
| `0x08` | rainbow pulsing + rotating | yes | yes | both renderings |
| `0x09` | rainbow solid candy | yes | yes | both renderings |
| `0x0A` | rainbow snake | yes | yes | both renderings |
| `0x0B` | rainbow wave | yes | yes | both renderings |
| `0x0C` | typing feedback, single key | yes | yes | both renderings |
| `0x0D` | wave pulse | yes | yes | both renderings |
| `0x0F` | spinning | yes | no | flag ignored — still colourful |
| `0x10` | wave 2 | yes | no | flag ignored — still colourful |
| `0x11` | pulse 2 | yes | yes | both renderings |

**12 of 15 effects rendered both ways**; 0 went dark when the flag was cleared.

A dark result at `0x50` would mean the firmware had nothing to draw: the
mode byte stays valid and reads back fine, the panel simply had no lit
pixel (§6).  With each effect's own record holding red, `dark` should not
appear at all now; `no` is the flag-ignored case (§3).
