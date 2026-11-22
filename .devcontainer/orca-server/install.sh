#!/bin/sh
# Installs the entrypoint that keeps ORCA's live server running (see devcontainer-feature.json).
set -e
cp "$(dirname "$0")/orca-server-init.sh" /usr/local/share/orca-server-init.sh
chmod 755 /usr/local/share/orca-server-init.sh
