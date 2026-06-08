#!/bin/bash
# Runs every 15 min via cron. Keeps run_all.sh alive until every playlist is
# done, rotates provider chain when no progress, alerts on resource pressure,
# defers to backups, and self-disables when the batch is complete.

set -u
source "$(dirname "$0")/_common.sh"

SCRIPT="$(dirname "$0")/run_all.sh"
PLAYLIST_FILE="$SPOTIFLAC_STATE_DIR/playlists.txt"
DONE_LOG="$SPOTIFLAC_STATE_DIR/done.txt"
STATE_FILE="$SPOTIFLAC_STATE_DIR/watchdog.state"
SERVICE_CONF="$SPOTIFLAC_STATE_DIR/service.conf"
LOG="$SPOTIFLAC_STATE_DIR/watchdog.log"
NOHUP_LOG="$SPOTIFLAC_STATE_DIR/nohup.log"

MAX_RETRIES_PER_SERVICE=3
PAUSE_AFTER_FULL_ROTATION_SECONDS=3600
MAX_LOG_BYTES=5000000   # 5 MB

# Split SPOTIFLAC_PROVIDER_CHAINS (comma-separated chains) into an array.
IFS=',' read -ra SERVICE_OPTIONS <<< "$SPOTIFLAC_PROVIDER_CHAINS"
# Trim leading/trailing whitespace from each chain
for i in "${!SERVICE_OPTIONS[@]}"; do
    SERVICE_OPTIONS[$i]="$(echo "${SERVICE_OPTIONS[$i]}" | sed 's/^ *//; s/ *$//')"
done

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

rotate_logs() {
    for f in "$SPOTIFLAC_STATE_DIR"/*.log; do
        [ -f "$f" ] || continue
        sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
        if [ "$sz" -ge "$MAX_LOG_BYTES" ]; then
            mv -f "$f" "$f.old"
            : > "$f"
        fi
    done
}

# Return 0 (defer) when external maintenance is detected. Designed to play
# nicely with backups and other heavy-I/O jobs the watchdog shouldn't fight.
maintenance_active() {
    if [ -n "$SPOTIFLAC_BACKUP_MOUNTPOINT" ] && mountpoint -q "$SPOTIFLAC_BACKUP_MOUNTPOINT" 2>/dev/null; then
        echo "backup mountpoint $SPOTIFLAC_BACKUP_MOUNTPOINT mounted"
        return 0
    fi
    if pgrep -af "rsync.*$SPOTIFLAC_OUTPUT_DIR" >/dev/null 2>&1; then
        echo "rsync against output dir in progress"
        return 0
    fi
    if [ -n "$SPOTIFLAC_BACKUP_PROCNAMES" ]; then
        IFS=',' read -ra PROC_NAMES <<< "$SPOTIFLAC_BACKUP_PROCNAMES"
        for name in "${PROC_NAMES[@]}"; do
            name="$(echo "$name" | sed 's/^ *//; s/ *$//')"
            [ -z "$name" ] && continue
            if pgrep -f "$name" >/dev/null 2>&1; then
                echo "$name running"
                return 0
            fi
        done
    fi
    return 1
}

prev_done_count=0
prev_attempts=0
paused_until=0
service_idx=0
[ -f "$STATE_FILE" ] && source "$STATE_FILE"

write_state() {
    cat > "$STATE_FILE" <<EOF
prev_done_count=$1
prev_attempts=$2
paused_until=$3
service_idx=$4
EOF
}

write_service_conf() {
    echo "SPOTIFLAC_SERVICE=\"${SERVICE_OPTIONS[$1]}\"" > "$SERVICE_CONF"
}

rotate_logs

if [ ! -f "$PLAYLIST_FILE" ]; then
    log "no playlists.txt at $PLAYLIST_FILE — nothing to do"
    exit 0
fi

all_ids=$(grep -oP 'https://open\.spotify\.com/playlist/\K[A-Za-z0-9]+' "$PLAYLIST_FILE" 2>/dev/null | sort -u)
done_ids=$(sort -u "$DONE_LOG" 2>/dev/null)
remaining=$(comm -23 <(echo "$all_ids") <(echo "$done_ids") | grep -c .)
done_count=$(grep -c . "$DONE_LOG" 2>/dev/null || echo 0)

log "tick: remaining=$remaining done=$done_count prev_attempts=$prev_attempts service_idx=$service_idx"

if reason=$(maintenance_active); then
    log "deferring: $reason"
    [ "$done_count" -gt "$prev_done_count" ] && prev_attempts=0
    write_state "$done_count" "$prev_attempts" 0 "$service_idx"
    exit 0
fi

if [ "$remaining" -eq 0 ]; then
    spf_notify "🎉 spotiflac-pipeline: all $done_count playlists complete. Watchdog removing itself from cron."
    log "complete; removing cron entry"
    (crontab -l 2>/dev/null | grep -v 'spotiflac-watchdog.sh') | crontab -
    rm -f "$STATE_FILE" "$SERVICE_CONF"
    exit 0
fi

now=$(date +%s)
if [ "$now" -lt "$paused_until" ]; then
    log "paused until $paused_until, skipping"
    exit 0
fi

oom_recent=$(journalctl --since "20 minutes ago" -k 2>/dev/null | grep -iE "out of memory|killed process" | head -1)
if [ -n "$oom_recent" ]; then
    spf_notify "⚠️ spotiflac-pipeline watchdog: kernel OOM detected — $oom_recent"
    log "OOM: $oom_recent"
fi

mem_avail=$(free -m | awk '/^Mem:/ {print $7}')
if [ -n "$mem_avail" ] && [ "$mem_avail" -lt "$SPOTIFLAC_MIN_FREE_MEM_MB" ]; then
    spf_notify "⚠️ spotiflac-pipeline watchdog: low RAM (${mem_avail}MB available, threshold ${SPOTIFLAC_MIN_FREE_MEM_MB}MB)"
    log "low mem: ${mem_avail}MB"
fi

free_gb=$(df -BG "$SPOTIFLAC_OUTPUT_DIR" 2>/dev/null | awk 'NR==2 {gsub("G","",$4); print $4}')
if [ -n "$free_gb" ] && [ "$free_gb" -lt "$SPOTIFLAC_MIN_FREE_DISK_GB" ]; then
    spf_notify "⚠️ spotiflac-pipeline watchdog: low disk (${free_gb}GB free, threshold ${SPOTIFLAC_MIN_FREE_DISK_GB}GB)"
    log "low disk: ${free_gb}GB"
fi

if pgrep -f "$SCRIPT" >/dev/null || pgrep -f "$SPOTIFLAC_VENV/bin/spotiflac http" >/dev/null; then
    log "batch running"
    attempts=$prev_attempts
    [ "$done_count" -gt "$prev_done_count" ] && attempts=0
    write_state "$done_count" "$attempts" 0 "$service_idx"
    exit 0
fi

attempts=$((prev_attempts + 1))
log "batch not running; remaining=$remaining attempt=$attempts service_idx=$service_idx"

if [ "$attempts" -gt "$MAX_RETRIES_PER_SERVICE" ] && [ "$done_count" -le "$prev_done_count" ]; then
    next_idx=$((service_idx + 1))
    if [ "$next_idx" -ge "${#SERVICE_OPTIONS[@]}" ]; then
        spf_notify "🚨 spotiflac-pipeline: tried all ${#SERVICE_OPTIONS[@]} provider chains with no progress. Pausing 1 h. $remaining playlist(s) still pending."
        log "full rotation exhausted; pausing 1 h"
        write_state "$done_count" 0 $((now + PAUSE_AFTER_FULL_ROTATION_SECONDS)) 0
        write_service_conf 0
        exit 0
    fi
    service_idx=$next_idx
    attempts=1
    write_service_conf "$service_idx"
    spf_notify "🔄 spotiflac-pipeline: no progress on previous chain — rotating to [${SERVICE_OPTIONS[$service_idx]}]"
    log "rotated to service_idx=$service_idx (${SERVICE_OPTIONS[$service_idx]})"
fi

[ ! -f "$SERVICE_CONF" ] && write_service_conf "$service_idx"

nohup "$SCRIPT" >> "$NOHUP_LOG" 2>&1 &
disown
log "restarted batch silently, pid=$!"

write_state "$done_count" "$attempts" 0 "$service_idx"
