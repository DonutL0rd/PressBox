#!/bin/bash
# Grant Docker containers access to the local X display.
# Run as the nrm user (not root).  Safe to re-run.
#
# What this does:
#   1. Finds the XAUTHORITY file for the running X session
#   2. Exports DISPLAY=:0 and XAUTHORITY so xhost can talk to the server
#   3. Runs: xhost +local:  (allows any local Unix-socket connection)
#
# To make this permanent across reboots, install the systemd service:
#   sudo cp ../systemd/tv-automator-xhost.service /etc/systemd/system/
#   sudo systemctl enable --now tv-automator-xhost.service

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
warn() { echo -e "${YELLOW}[??]${NC}  $*"; }
die()  { echo -e "${RED}[!!]${NC}  $*"; exit 1; }

DISPLAY_NUM="${1:-:0}"
export DISPLAY="$DISPLAY_NUM"

echo "Setting up X11 access for Docker (DISPLAY=$DISPLAY) ..."
echo ""

# ── Find XAUTHORITY ─────────────────────────────────────────────
# On Wayland+Xwayland (Ubuntu/GNOME default), the auth file is a
# randomly-named file like /run/user/1000/.mutter-XwaylandauthXXXX.
# We merge it into ~/.Xauthority so Docker has a stable path to mount.
find_and_merge_xauth() {
    local uid
    uid="${HOST_UID:-$(id -u)}"
    local run_dir="/run/user/${uid}"

    # 1. Wayland/Xwayland: mutter auth file (GNOME)
    local mutter_auth
    mutter_auth=$(sudo bash -c "ls ${run_dir}/.mutter-Xwaylandauth.* 2>/dev/null" | head -1)
    if [ -n "$mutter_auth" ]; then
        ok "Wayland/Xwayland session detected (UID $uid) — merging mutter auth: $mutter_auth"
        sudo xauth -f "$mutter_auth" extract - "$DISPLAY" | xauth merge -
        echo "$HOME/.Xauthority"
        return 0
    fi

    # 2. Standard candidates (X11 session, LightDM, GDM X11 mode)
    local candidates=(
        "${run_dir}/gdm/Xauthority"
        "${run_dir}/Xauthority"
        "/var/run/lightdm/root/:0"
        "/var/run/lightdm/${USER}/xauthority"
        "/var/lib/gdm3/.Xauthority"
        "/var/lib/gdm/.Xauthority"
        "/tmp/.Xauthority"
        "$HOME/.Xauthority"
    )
    for f in "${candidates[@]}"; do
        [ -f "$f" ] && echo "$f" && return 0
    done

    # 3. Broader search
    local found
    found=$(find /run /var/run -name '*[Aa]uthority*' 2>/dev/null | head -1)
    [ -n "$found" ] && echo "$found" && return 0

    return 1
}

if [ -n "${XAUTHORITY:-}" ] && [ -f "$XAUTHORITY" ]; then
    ok "XAUTHORITY already set: $XAUTHORITY"
else
    XAUTHORITY=$(find_and_merge_xauth) || die "Could not find an Xauthority file. Is a display manager running?"
    export XAUTHORITY
    ok "Using XAUTHORITY: $XAUTHORITY"
fi

# ── Verify we can reach the display ─────────────────────────────
if ! command -v xdpyinfo &>/dev/null; then
    warn "xdpyinfo not installed; skipping connectivity check."
    warn "Install with: sudo apt install x11-utils"
elif xdpyinfo -display "$DISPLAY" &>/dev/null; then
    ok "X server at $DISPLAY is reachable"
else
    die "Cannot reach X server at $DISPLAY with XAUTHORITY=$XAUTHORITY

  Possible fixes:
    • Run 'loginctl show-session' to confirm an active graphical session
    • Try DISPLAY=:1 if the monitor is on a different display number
    • Run this script from a terminal *inside* the graphical session"
fi

# ── Grant access ─────────────────────────────────────────────────
echo ""
echo "Running: xhost +local:"
xhost +local:
ok "Docker containers (and any local process) can now draw on $DISPLAY"

# ── Print export block for convenience ──────────────────────────
echo ""
echo "─────────────────────────────────────────────────────"
echo "Add these to your shell or docker compose invocation:"
echo ""
echo "  export DISPLAY=$DISPLAY"
echo "  export XAUTHORITY=$XAUTHORITY"
echo ""
echo "Start the container:"
echo "  cd $(dirname "$0")/../docker"
echo "  DISPLAY=$DISPLAY docker compose up -d"
echo ""
echo "Verify Chrome can render:"
echo "  docker exec tv-automator google-chrome --no-sandbox --disable-gpu https://google.com &"
echo "─────────────────────────────────────────────────────"
