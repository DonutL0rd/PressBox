#!/bin/bash
set -e

echo "╔══════════════════════════════════════════╗"
echo "║         📺 PressBox starting...          ║"
echo "╚══════════════════════════════════════════╝"

mkdir -p /data/cookies /data/config /data/logs

# Strip proxy env injected by OrbStack/Docker Desktop — httpx chokes on
# IPv6 CIDR entries in NO_PROXY, and this app makes direct outbound calls.
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY \
      http_proxy https_proxy all_proxy no_proxy

echo "[*] PressBox listening on container port 5000 (default host mapping: http://<server-ip>:5050/)"
exec python -m tv_automator.main
