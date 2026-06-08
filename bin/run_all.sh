#!/bin/bash
# Batch driver for spotiflac.
#
# Reads playlist URLs from $SPOTIFLAC_STATE_DIR/playlists.txt, skips IDs
# already listed in done.txt, downloads each remaining playlist, parses the
# session summary for failures, and triggers the migrate + verify hooks
# after each successful playlist. Aborts after N consecutive all-failed
# playlists so the watchdog can rotate to a different provider chain.

set -u
source "$(dirname "$0")/_common.sh"

PLAYLIST_FILE="$SPOTIFLAC_STATE_DIR/playlists.txt"
FAILED_LOG="$SPOTIFLAC_STATE_DIR/failed.txt"
DONE_LOG="$SPOTIFLAC_STATE_DIR/done.txt"
SERVICE_CONF="$SPOTIFLAC_STATE_DIR/service.conf"
SPOTIFLAC_LOG="$SPOTIFLAC_STATE_DIR/spotiflac.log"
MIGRATE_SCRIPT="$(dirname "$0")/migrate-to-flat.py"
MIGRATE_LOG="$SPOTIFLAC_STATE_DIR/migrate.log"
VERIFY_SCRIPT="$(dirname "$0")/verify-and-cleanup.py"
VERIFY_LOG="$SPOTIFLAC_STATE_DIR/verify.log"
MAX_CONSECUTIVE_FAIL=3

# Active provider chain. The watchdog writes this file when rotating; for a
# standalone run we fall back to the first chain in SPOTIFLAC_PROVIDER_CHAINS.
SPOTIFLAC_SERVICE="${SPOTIFLAC_PROVIDER_CHAINS%%,*}"
[ -f "$SERVICE_CONF" ] && source "$SERVICE_CONF"

if [ ! -f "$PLAYLIST_FILE" ]; then
    echo "Missing $PLAYLIST_FILE — add one Spotify playlist URL per line." >&2
    exit 2
fi

source "$SPOTIFLAC_VENV/bin/activate"

mkdir -p "$SPOTIFLAC_OUTPUT_DIR"
> "$FAILED_LOG"
touch "$DONE_LOG"

ALL_URLS=$(grep -oP 'https://open\.spotify\.com/playlist/[A-Za-z0-9]+' "$PLAYLIST_FILE" | sort -u)
URLS=""
SKIPPED=0
while IFS= read -r url; do
    [ -z "$url" ] && continue
    id="${url##*/}"
    if grep -qFx "$id" "$DONE_LOG"; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi
    URLS+="$url"$'\n'
done <<< "$ALL_URLS"
URLS="${URLS%$'\n'}"

if [ -z "$URLS" ]; then
    spf_notify "🎵 SpotiFlac: nothing to do — all $SKIPPED playlist(s) already processed."
    exit 0
fi

TOTAL=$(echo "$URLS" | wc -l)
COUNT=0
SUCCESS=0
PARTIAL=0
ALL_FAILED=0
CONSECUTIVE_FAIL=0
EARLY_EXIT=0

spf_notify "🎵 SpotiFlac batch started — $TOTAL playlist(s) to process via [$SPOTIFLAC_SERVICE] ($SKIPPED already done)."

while IFS= read -r url; do
    id="${url##*/}"
    COUNT=$((COUNT + 1))

    free_gb=$(df -BG "$SPOTIFLAC_OUTPUT_DIR" | awk 'NR==2 {gsub("G","",$4); print $4}')
    if [ "$free_gb" -lt "$SPOTIFLAC_MIN_FREE_DISK_GB" ]; then
        spf_notify "⚠️ SpotiFlac stopped: only ${free_gb}GB free (limit: ${SPOTIFLAC_MIN_FREE_DISK_GB}GB)."
        break
    fi

    tmp_out=$(mktemp)
    spotiflac "$url" "$SPOTIFLAC_OUTPUT_DIR" \
        --service $SPOTIFLAC_SERVICE \
        --retries 2 \
        --use-artist-subfolders \
        --use-album-subfolders \
        --quality LOSSLESS \
        2>&1 | tee -a "$tmp_out" >> "$SPOTIFLAC_LOG"

    exit_code=${PIPESTATUS[0]}

    failed_tracks=$(grep -oP '(?<=║    ).*(?=: All providers)' "$tmp_out" | sed 's/[[:space:]]*$//')
    if [ -z "$failed_tracks" ]; then
        failed_count=0
    else
        failed_count=$(printf '%s\n' "$failed_tracks" | grep -c .)
    fi
    # spotiflac's session summary box has two label sets: Italian (<=0.5.0)
    # and English (>=0.5.1). Match both.
    completed=$(grep -oP '(Completate|Successful)\s*:\s*\K[0-9]+' "$tmp_out" | tail -1)
    total_tracks=$(grep -oP '(Tracce totali|Total Tracks)\s*:\s*\K[0-9]+' "$tmp_out" | tail -1)
    : "${completed:=0}"
    : "${total_tracks:=0}"
    rm -f "$tmp_out"

    if [ "$exit_code" -eq 0 ] && [ "$failed_count" -eq 0 ] && [ "$completed" -gt 0 ]; then
        echo "$id" >> "$DONE_LOG"
        SUCCESS=$((SUCCESS + 1))
        CONSECUTIVE_FAIL=0
        python3 "$MIGRATE_SCRIPT" >> "$MIGRATE_LOG" 2>&1
        python3 "$VERIFY_SCRIPT" --clean >> "$VERIFY_LOG" 2>&1
        spf_notify "✅ [$COUNT/$TOTAL] Done: $url ($completed/$total_tracks)"
    elif [ "$completed" -gt 0 ] && [ -n "$failed_tracks" ]; then
        echo "$id" >> "$DONE_LOG"
        echo "=== $url ===" >> "$FAILED_LOG"
        echo "$failed_tracks" >> "$FAILED_LOG"
        echo "" >> "$FAILED_LOG"
        PARTIAL=$((PARTIAL + 1))
        CONSECUTIVE_FAIL=0
        python3 "$MIGRATE_SCRIPT" >> "$MIGRATE_LOG" 2>&1
        python3 "$VERIFY_SCRIPT" --clean >> "$VERIFY_LOG" 2>&1
        fail_msg=$(echo "$failed_tracks" | head -10 | sed 's/^/  • /')
        spf_notify "⚠️ [$COUNT/$TOTAL] Partial ($completed/$total_tracks, $failed_count failed): $url
$fail_msg"
    else
        echo "=== $url ===" >> "$FAILED_LOG"
        [ -n "$failed_tracks" ] && echo "$failed_tracks" >> "$FAILED_LOG"
        echo "" >> "$FAILED_LOG"
        ALL_FAILED=$((ALL_FAILED + 1))
        CONSECUTIVE_FAIL=$((CONSECUTIVE_FAIL + 1))
        if [ "$CONSECUTIVE_FAIL" -ge "$MAX_CONSECUTIVE_FAIL" ]; then
            EARLY_EXIT=1
            break
        fi
    fi

done <<< "$URLS"

if [ "$EARLY_EXIT" -eq 1 ]; then
    summary="🛑 Batch aborted after $CONSECUTIVE_FAIL consecutive all-failed playlists via [$SPOTIFLAC_SERVICE]. ✅ $SUCCESS · ⚠️ $PARTIAL · ❌ $ALL_FAILED so far. Watchdog will retry."
else
    summary="🏁 Batch complete via [$SPOTIFLAC_SERVICE]. ✅ $SUCCESS · ⚠️ $PARTIAL partial · ❌ $ALL_FAILED all-failed"
fi
spf_notify "$summary"
