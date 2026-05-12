#!/bin/bash
set -e

echo "╔══════════════════════════════════════════╗"
echo "║      📺 TV-Automator Starting...         ║"
echo "╚══════════════════════════════════════════╝"

# ── Start D-Bus ──────────────────────────────────────────────────
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
    eval $(dbus-launch --sh-syntax)
    export DBUS_SESSION_BUS_ADDRESS
    echo "[*] D-Bus started"
fi

# ── Ensure DISPLAY is set ────────────────────────────────────────
if [ "$ENABLE_LOCAL_BROWSER" = "false" ]; then
    echo "[*] Local browser disabled via ENABLE_LOCAL_BROWSER=false"
    mkdir -p /data/cookies /data/config /data/logs
    echo "[*] Starting TV-Automator on port 5000..."
    exec python -m tv_automator.main
fi

if [ -z "$DISPLAY" ]; then
    export DISPLAY=:0
fi
echo "[*] DISPLAY=$DISPLAY"

# ── Find X11 auth cookie (try common locations) ──────────────────
for xauth_candidate in \
        "$XAUTHORITY" \
        "/run/user/${HOST_UID:-1000}/gdm/Xauthority" \
        "/run/user/${HOST_UID:-1000}/.mutter-Xwaylandauth."* \
        "$HOME/.Xauthority" \
        "/tmp/.Xauthority"; do
    [ -z "$xauth_candidate" ] && continue
    # Expand glob — skip if no match
    [ -e "$xauth_candidate" ] || continue
    export XAUTHORITY="$xauth_candidate"
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        echo "[*] X auth: $XAUTHORITY"
        break
    fi
done

# ── Check for a working X display; fall back to Xvfb ────────────
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "[!] No X server at $DISPLAY — starting Xvfb on :99"
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
    Xvfb :99 -screen 0 1920x1080x24 -ac &
    XVFB_PID=$!
    # Wait until Xvfb is actually accepting connections
    for i in $(seq 1 10); do
        if xdpyinfo -display :99 >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
    export DISPLAY=:99
    echo "[*] Xvfb running on $DISPLAY (pid $XVFB_PID)"
fi

# ── Start Openbox window manager ─────────────────────────────────
echo "[*] Starting Openbox on $DISPLAY..."
DISPLAY="$DISPLAY" openbox --sm-disable &
sleep 1

# ── Create data directories ──────────────────────────────────────
mkdir -p /data/cookies /data/config /data/logs

# ── Start PulseAudio ─────────────────────────────────────────────
echo "[*] Starting PulseAudio..."
pulseaudio --start --exit-idle-time=-1 2>/dev/null || true

# ── Start TV-Automator web server ────────────────────────────────
echo "[*] Starting TV-Automator on port 5000..."
echo "[*] Dashboard available at http://<server-ip>:5000/"
exec python -m tv_automator.main
