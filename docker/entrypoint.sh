#!/bin/bash
# Scheduler loop for the containerized pipeline. Mirrors examples/crontab.example:
#   watchdog     every 15 min  (launches/monitors the batch, rotates chains)
#   spotify-diff daily         (detect playlist additions/removals)
#   verify       weekly        (full duration sweep + cleanup)
set -u

# Optionally drop privileges to the uid/gid of the mounted music volume.
if [ -n "${PUID:-}" ] && [ "$(id -u)" = "0" ]; then
    groupadd -f -g "${PGID:-$PUID}" spotiflac 2>/dev/null || true
    id spotiflac >/dev/null 2>&1 || useradd -u "$PUID" -g "${PGID:-$PUID}" -M spotiflac
    exec gosu spotiflac "$0" "$@"
fi

echo "[entrypoint] spotiflac-pipeline container starting"
mkdir -p "$SPOTIFLAC_STATE_DIR"

if [ ! -f "$SPOTIFLAC_STATE_DIR/playlists.txt" ]; then
    echo "[entrypoint] NOTE: no playlists.txt in the state volume yet —"
    echo "             add one Spotify playlist URL per line to $SPOTIFLAC_STATE_DIR/playlists.txt"
fi

last_diff=0
last_verify=0
while :; do
    now=$(date +%s)
    /app/bin/spotiflac-watchdog.sh || true
    if [ $((now - last_diff)) -ge 86400 ]; then
        "$SPOTIFLAC_VENV/bin/python3" /app/bin/spotify-diff.py || true
        last_diff=$now
    fi
    if [ $((now - last_verify)) -ge 604800 ]; then
        "$SPOTIFLAC_VENV/bin/python3" /app/bin/verify-and-cleanup.py --clean || true
        last_verify=$now
    fi
    sleep 900
done
