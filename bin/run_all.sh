#!/bin/bash
# Batch driver for spotiflac — missing-only mode.
#
# For each playlist in $SPOTIFLAC_STATE_DIR/playlists.txt not yet in done.txt,
# fetch-missing.py enumerates the full track list, downloads ONLY tracks
# absent from track-id-index.json, and maintains the permanent-failure
# quarantine in unavailable.txt. A playlist is marked done when pending == 0
# (everything present or quarantined) — so playlists with a few unobtainable
# tracks no longer retry forever, and a +N-tracks playlist update costs N
# downloads instead of a full re-run.

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
FETCH_MISSING="$(dirname "$0")/fetch-missing.py"
MAX_CONSECUTIVE_FAIL=3

# Active provider chain. The watchdog writes this file when rotating; for a
# standalone run we fall back to the first chain in SPOTIFLAC_PROVIDER_CHAINS.
SPOTIFLAC_SERVICE="${SPOTIFLAC_PROVIDER_CHAINS%%,*}"
[ -f "$SERVICE_CONF" ] && source "$SERVICE_CONF"
export SPOTIFLAC_SERVICE

if [ ! -f "$PLAYLIST_FILE" ]; then
    echo "Missing $PLAYLIST_FILE — add one Spotify playlist URL per line." >&2
    exit 2
fi

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

spf_notify "🎵 SpotiFlac batch started — $TOTAL playlist(s) to check via [$SPOTIFLAC_SERVICE] ($SKIPPED already done). Missing-only mode."

while IFS= read -r url; do
    id="${url##*/}"
    COUNT=$((COUNT + 1))

    free_gb=$(df -BG "$SPOTIFLAC_OUTPUT_DIR" | awk 'NR==2 {gsub("G","",$4); print $4}')
    if [ "$free_gb" -lt "$SPOTIFLAC_MIN_FREE_DISK_GB" ]; then
        spf_notify "⚠️ SpotiFlac stopped: only ${free_gb}GB free (limit: ${SPOTIFLAC_MIN_FREE_DISK_GB}GB)."
        break
    fi

    tmp_res=$(mktemp)
    "$SPOTIFLAC_VENV/bin/python3" "$FETCH_MISSING" "$url" > "$tmp_res" 2>> "$SPOTIFLAC_LOG"
    fm_exit=$?
    result_line=$(grep '^RESULT|' "$tmp_res" | tail -1)
    qnew_names=$(grep '^QNEW|' "$tmp_res" | cut -d'|' -f2- | head -5 | sed 's/^/  ⛔ /')
    rm -f "$tmp_res"

    if [ "$fm_exit" -ne 0 ] || [ -z "$result_line" ]; then
        echo "=== $url === (enumeration/driver error, exit $fm_exit)" >> "$FAILED_LOG"
        ALL_FAILED=$((ALL_FAILED + 1))
        CONSECUTIVE_FAIL=$((CONSECUTIVE_FAIL + 1))
        if [ "$CONSECUTIVE_FAIL" -ge "$MAX_CONSECUTIVE_FAIL" ]; then
            EARLY_EXIT=1
            break
        fi
        continue
    fi

    IFS='|' read -r _ total have missing attempted ok failed q_new q_total pending name <<< "$result_line"

    if [ "$ok" -gt 0 ]; then
        python3 "$MIGRATE_SCRIPT" >> "$MIGRATE_LOG" 2>&1
        python3 "$VERIFY_SCRIPT" --clean >> "$VERIFY_LOG" 2>&1
    fi

    if [ "$pending" -eq 0 ]; then
        # everything present or quarantined -> done
        grep -qFx "$id" "$DONE_LOG" || echo "$id" >> "$DONE_LOG"
        CONSECUTIVE_FAIL=0
        if [ "$q_total" -eq 0 ]; then
            SUCCESS=$((SUCCESS + 1))
            if [ "$attempted" -gt 0 ]; then
                spf_notify "✅ [$COUNT/$TOTAL] $name: fetched $ok missing track(s) ($have/$total were already present)."
            else
                spf_notify "✅ [$COUNT/$TOTAL] $name: already complete ($total tracks, nothing missing)."
            fi
        else
            PARTIAL=$((PARTIAL + 1))
            echo "=== $url === quarantined: $q_total" >> "$FAILED_LOG"
            spf_notify "⚠️ [$COUNT/$TOTAL] $name: done with $q_total unobtainable track(s) quarantined (fetched $ok new).
$qnew_names
(see unavailable.txt in the state dir — delete a line to retry)"
        fi
    else
        echo "=== $url === pending: $pending after $attempted attempt(s)" >> "$FAILED_LOG"
        if [ "$ok" -gt 0 ]; then
            PARTIAL=$((PARTIAL + 1))
            CONSECUTIVE_FAIL=0
            spf_notify "⚠️ [$COUNT/$TOTAL] $name: fetched $ok, but $pending track(s) still failing (will retry next cycle)."
        else
            ALL_FAILED=$((ALL_FAILED + 1))
            CONSECUTIVE_FAIL=$((CONSECUTIVE_FAIL + 1))
            if [ "$CONSECUTIVE_FAIL" -ge "$MAX_CONSECUTIVE_FAIL" ]; then
                EARLY_EXIT=1
                break
            fi
        fi
    fi

done <<< "$URLS"

if [ "$EARLY_EXIT" -eq 1 ]; then
    summary="🛑 Batch aborted after $CONSECUTIVE_FAIL consecutive all-failed playlists via [$SPOTIFLAC_SERVICE]. ✅ $SUCCESS · ⚠️ $PARTIAL · ❌ $ALL_FAILED so far. Watchdog will retry."
else
    summary="🏁 Batch complete via [$SPOTIFLAC_SERVICE]. ✅ $SUCCESS · ⚠️ $PARTIAL partial · ❌ $ALL_FAILED failed"
fi
spf_notify "$summary"
