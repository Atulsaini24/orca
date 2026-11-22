#!/bin/sh
# Container entrypoint: keep ORCA's live server running on port 8000 (log: /tmp/orca.log).
# It waits for setup.sh to finish (it writes .devcontainer/.ready), then runs the server and restarts it if it exits.
#   ORCA_CITIES=manhattan   Manhattan runs on the server with its real trips; Chicago and Cambridge, picked in the
#                           control room, run in the visitor's browser (their trip data is not prepared here).
#   ORCA_TRUST_PROXY=1      Codespaces' port forwarding is a proxy: rate limits see each visitor's own address.
#   ORCA_OPEN_CONTROLS=1    anyone with the link may use the operator controls (speed, demand, incidents, what-ifs);
#                           remove it to keep them for yourself (then set ORCA_ADMIN_TOKEN and open /app?token=...).
(
  while true; do
    for ws in /workspaces/*/; do
      if [ -f "$ws.devcontainer/.ready" ] && [ -f "${ws}backend/orca/router.py" ]; then
        cmd="cd ${ws}backend && ORCA_CITIES=manhattan ORCA_TRUST_PROXY=1 ORCA_OPEN_CONTROLS=1 exec python -m orca.router --host 0.0.0.0 --port 8000"
        echo "$(date -u +%FT%TZ) starting the ORCA live server" >> /tmp/orca.log
        if [ "$(id -u)" = 0 ] && id vscode > /dev/null 2>&1; then
          su vscode -s /bin/sh -c "$cmd" >> /tmp/orca.log 2>&1
        else
          sh -c "$cmd" >> /tmp/orca.log 2>&1
        fi
        echo "$(date -u +%FT%TZ) the server exited (code $?); restarting in 5 s" >> /tmp/orca.log
      fi
    done
    sleep 5
  done
) > /dev/null 2>&1 &
exec "$@"
