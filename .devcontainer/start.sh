#!/usr/bin/env bash
# Runs every time the codespace starts: the live server on port 8000, in the background (log: /tmp/orca.log).
#   ORCA_CITIES=manhattan   Manhattan runs on the server with its real trips; Chicago and Cambridge, chosen in the
#                           control room, run in the visitor's browser (their trip data is not prepared here).
#   ORCA_TRUST_PROXY=1      Codespaces' port forwarding is a proxy: rate limits see each visitor's own address.
#   ORCA_OPEN_CONTROLS=1    anyone with the link may use the operator controls (speed, demand, incidents, what-ifs);
#                           remove it to keep them for yourself (then set ORCA_ADMIN_TOKEN and open /app?token=...).
set -euo pipefail
cd "$(dirname "$0")/../backend"
pkill -f "orca.router" 2>/dev/null || true
export ORCA_CITIES=manhattan ORCA_TRUST_PROXY=1 ORCA_OPEN_CONTROLS=1
nohup setsid python -m orca.router --host 0.0.0.0 --port 8000 > /tmp/orca.log 2>&1 < /dev/null &
echo "ORCA live server starting on port 8000 (about a minute; log: /tmp/orca.log)"
