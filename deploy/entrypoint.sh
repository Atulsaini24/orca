#!/bin/sh
# Container entrypoint. Starts as root only to make the trip-ledger directory writable, then runs the
# server as the unprivileged "orca" user. A fresh volume (Fly.io, Render, a bind mount) is owned by root,
# and the ledger's SQLite files would otherwise fail to open, so every city process would crash at start.
set -e
if [ "$(id -u)" = 0 ]; then
  case "${ORCA_DB:-}" in
    *://*) ;;                                              # PostgreSQL: nothing on disk
    *) dir=$(dirname "${ORCA_DB:-/app/backend/ledger/orca.db}")
       mkdir -p "$dir" && chown orca:orca "$dir" ;;
  esac
  exec setpriv --reuid=orca --regid=orca --init-groups -- "$@"
fi
exec "$@"
