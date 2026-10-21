#!/usr/bin/env bash
# Feed the K61SE vendor app's music mode from whatever is playing on the
# speakers, under Wine (winepulse -> pipewire-pulse).
#
#   ./k61se_music_audio.sh up          # create the "music_probe" capture source
#   ./k61se_music_audio.sh status      # what is up, what the default source is
#   ./k61se_music_audio.sh sources     # what a PulseAudio client (i.e. Wine) sees
#   ./k61se_music_audio.sh default-on  # make it the system default input
#   ./k61se_music_audio.sh default-off # put the previous default back
#   ./k61se_music_audio.sh test        # beep + record, print the captured level
#   ./k61se_music_audio.sh down        # remove the source again
#
# Nothing here touches the keyboard; it only creates an audio node.
#
# Why the loopback: in PipeWire a sink's monitor is not a node of its own, and
# pw-loopback's capture stream autoconnects to the *default source* (your mic)
# unless `stream.capture.sink=true` is set.  Without that flag the app records
# the room instead of the music, which is quiet enough to look like a Wine bug.
set -u

SINK="${K61SE_SINK:-alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Speaker__sink}"
NAME="${K61SE_SOURCE:-music_probe}"
LOOP="${NAME//[^A-Za-z0-9_.-]/_}_loop"
RUNDIR="${XDG_RUNTIME_DIR:-/tmp}"
PIDFILE="$RUNDIR/k61se_music_audio.pid"
STATEFILE="$RUNDIR/k61se_music_audio.prev-source"
LOG="$RUNDIR/k61se_music_audio.log"

die() { echo "error: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

have pw-loopback || die "pw-loopback not found (install pipewire-bin)"
have wpctl || die "wpctl not found (install wireplumber)"
have python3 || die "python3 not found (needed to read pw-dump)"

# node name -> PipeWire global id, empty when absent
find_id() {
    pw-dump 2>/dev/null | python3 -c '
import json, sys
want = sys.argv[1]
for obj in json.load(sys.stdin):
    props = (obj.get("info") or {}).get("props") or {}
    if props.get("node.name") == want:
        print(obj["id"])
        break
' "$1"
}

default_source() {
    wpctl inspect @DEFAULT_AUDIO_SOURCE@ 2>/dev/null \
        | sed -n 's/^ *\* *node\.name = "\(.*\)"$/\1/p' | head -1
}

is_up() { [ -n "$(find_id "$NAME")" ]; }

up() {
    if is_up; then
        echo "$NAME is already up (node $(find_id "$NAME"))"
        return 0
    fi
    [ -n "$(find_id "$SINK")" ] \
        || die "sink not found: $SINK (set K61SE_SINK=... to override)"
    pw-loopback -n "$LOOP" -m '[FL FR]' -C "$SINK" \
        --capture-props='stream.capture.sink=true' \
        --playback-props="media.class=Audio/Source node.name=$NAME node.description=\"Music probe (speaker monitor)\"" \
        >"$LOG" 2>&1 &
    echo $! >"$PIDFILE"
    for _ in $(seq 1 30); do
        is_up && break
        sleep 0.1
    done
    is_up || die "loopback did not appear; see $LOG"
    echo "$NAME is up (node $(find_id "$NAME")), capturing $SINK"
}

down() {
    local pid
    if [ -f "$PIDFILE" ]; then
        pid=$(cat "$PIDFILE" 2>/dev/null)
        [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null
        rm -f "$PIDFILE"
    fi
    # also catch one started by hand (no pid file)
    for pid in $(pgrep -x pw-loopback 2>/dev/null); do
        if tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q -- "-n $LOOP"; then
            kill "$pid" 2>/dev/null
        fi
    done
    for _ in $(seq 1 20); do
        is_up || break
        sleep 0.1
    done
    if is_up; then
        echo "$NAME is still up (node $(find_id "$NAME"))" >&2
        return 1
    fi
    echo "$NAME is down"
}

status() {
    local def id
    def=$(default_source)
    id=$(find_id "$NAME")
    echo "speaker sink : $SINK"
    if [ -n "$id" ]; then
        echo "music source : $NAME (node $id)"
    else
        echo "music source : not running (run: $0 up)"
    fi
    echo "default input: ${def:-unknown}"
    if [ "$def" = "$NAME" ]; then
        echo "               ^ music mode gets the speakers"
    else
        echo "               ^ tell the app to use '$NAME', or run: $0 default-on"
    fi
    echo
    echo "pulse sources (what winepulse enumerates):"
    if have ffmpeg; then
        ffmpeg -hide_banner -sources pulse 2>&1 | sed -n '/Auto-detected sources/,$p'
    else
        echo "  (ffmpeg not installed)"
    fi
}

default_on() {
    local def id
    is_up || die "$NAME is not up (run: $0 up)"
    def=$(default_source)
    id=$(find_id "$NAME")
    [ -n "$id" ] || die "could not find $NAME's node id"
    if [ "$def" = "$NAME" ]; then
        echo "already the default input"
        return 0
    fi
    printf '%s\n' "$def" >"$STATEFILE"
    wpctl set-default "$id"
    echo "default input -> $NAME (was: ${def:-unknown}; undo with: $0 default-off)"
}

default_off() {
    local prev id
    [ -f "$STATEFILE" ] || die "no saved default (was it set by hand?)"
    prev=$(cat "$STATEFILE")
    if [ -z "$prev" ] || [ "$prev" = "$NAME" ]; then
        rm -f "$STATEFILE"
        echo "nothing to restore"
        return 0
    fi
    id=$(find_id "$prev")
    [ -n "$id" ] || die "previous source '$prev' is gone; pick one with wpctl set-default"
    wpctl set-default "$id"
    rm -f "$STATEFILE"
    echo "default input -> $prev"
}

test_capture() {
    local tone max
    is_up || die "$NAME is not up (run: $0 up)"
    have ffmpeg || die "ffmpeg needed for the test"
    tone=$(mktemp --suffix=.wav)
    ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=3" \
        -af volume=6 -y "$tone"
    ( pw-play "$tone" >/dev/null 2>&1 & )
    sleep 1
    ffmpeg -hide_banner -loglevel error -f pulse -i "$NAME" -t 1.5 -ac 2 \
        -y "$RUNDIR/k61se_music_probe_test.wav"
    max=$(ffmpeg -hide_banner -i "$RUNDIR/k61se_music_probe_test.wav" -af volumedetect \
        -f null - 2>&1 | sed -n 's/.*max_volume: \(.*\) dB/\1/p')
    rm -f "$tone"
    echo "captured max level: ${max:-unknown} dB  (a real signal is well above -50)"
    [ -n "${max:-}" ] || return 1
    awk -v m="$max" 'BEGIN { exit !(m > -50) }' || {
        echo "that is silence — check the sink name and that something was playing" >&2
        return 1
    }
}

case "${1:-status}" in
    up) up ;;
    down) down ;;
    status) status ;;
    sources) ffmpeg -hide_banner -sources pulse 2>&1 | sed -n '/Auto-detected sources/,$p' ;;
    default-on) default_on ;;
    default-off) default_off ;;
    test) test_capture ;;
    -h|--help|help)
        sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
        ;;
    *) die "unknown command: $1 (try: $0 help)" ;;
esac
