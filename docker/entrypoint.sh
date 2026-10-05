#!/bin/sh
# Starter serveren som PUID/PGID (Unraid: 99/100) i stedet for root.
set -e

PUID="${PUID:-99}"
PGID="${PGID:-100}"
umask "${UMASK:-000}"
CONFIG_DIR="${CONFIG_DIR:-/config}"

# Blender lagrer innstillinger og kompilerte GPU-kjerner i $HOME. Legges i /config,
# så OPTIX-kjernene ikke må kompileres på nytt etter hver omstart.
export HOME="${CONFIG_DIR}/home"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$CONFIG_DIR" "$HOME"
    chown "$PUID:$PGID" "$CONFIG_DIR" "$HOME"
    find "$CONFIG_DIR" -maxdepth 1 -name 'ko.db*' -exec chown "$PUID:$PGID" {} +
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups /opt/venv/bin/python -m app.server
fi

mkdir -p "$HOME"
exec /opt/venv/bin/python -m app.server
