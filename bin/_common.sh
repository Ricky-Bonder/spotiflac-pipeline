#!/bin/bash
# Shared config loader for all spotiflac-pipeline shell scripts.
# Sourced at the top of each bin/*.sh script. Sets SPOTIFLAC_* variables
# from ~/.config/spotiflac-pipeline/spotiflac.env (if present) and fills in
# defaults for anything left unset.

_SPF_CONFIG_FILE="${SPOTIFLAC_CONFIG_FILE:-$HOME/.config/spotiflac-pipeline/spotiflac.env}"
[ -f "$_SPF_CONFIG_FILE" ] && source "$_SPF_CONFIG_FILE"

SPOTIFLAC_MUSIC_ROOT="${SPOTIFLAC_MUSIC_ROOT:-$HOME/Music}"
SPOTIFLAC_OUTPUT_DIR="${SPOTIFLAC_OUTPUT_DIR:-$SPOTIFLAC_MUSIC_ROOT/spotiflac}"
SPOTIFLAC_SPOTDL_ROOT="${SPOTIFLAC_SPOTDL_ROOT:-$SPOTIFLAC_MUSIC_ROOT/spotdl}"
SPOTIFLAC_STATE_DIR="${SPOTIFLAC_STATE_DIR:-$HOME/.local/state/spotiflac-pipeline}"
SPOTIFLAC_VENV="${SPOTIFLAC_VENV:-$HOME/.local/share/spotiflac-pipeline/venv}"

SPOTIFLAC_TELEGRAM_BOT_TOKEN="${SPOTIFLAC_TELEGRAM_BOT_TOKEN:-}"
SPOTIFLAC_TELEGRAM_CHAT_ID="${SPOTIFLAC_TELEGRAM_CHAT_ID:-}"

SPOTIFLAC_BACKUP_MOUNTPOINT="${SPOTIFLAC_BACKUP_MOUNTPOINT:-}"
SPOTIFLAC_BACKUP_PROCNAMES="${SPOTIFLAC_BACKUP_PROCNAMES:-}"

SPOTIFLAC_MIN_FREE_DISK_GB="${SPOTIFLAC_MIN_FREE_DISK_GB:-50}"
SPOTIFLAC_MIN_FREE_MEM_MB="${SPOTIFLAC_MIN_FREE_MEM_MB:-200}"

# spotiflac ≥3.8 era: providers are JS extensions; as of late 2026 only the
# deezer and tidal-web extensions reliably work, so the default rotation is
# limited to combinations of those two.
SPOTIFLAC_PROVIDER_CHAINS="${SPOTIFLAC_PROVIDER_CHAINS:-deezer tidal,tidal deezer,deezer,tidal}"

# Optional directory prepended to PATH (e.g. a user-local Node ≥20 install —
# the JS extension runtime breaks on distro Node 18).
SPOTIFLAC_EXTRA_PATH="${SPOTIFLAC_EXTRA_PATH:-}"
[ -n "$SPOTIFLAC_EXTRA_PATH" ] && export PATH="$SPOTIFLAC_EXTRA_PATH:$PATH"

SPOTIFLAC_MAX_TRACK_FAILS="${SPOTIFLAC_MAX_TRACK_FAILS:-4}"

mkdir -p "$SPOTIFLAC_STATE_DIR"

spf_notify() {
    local msg="$1"
    if [ -n "$SPOTIFLAC_TELEGRAM_BOT_TOKEN" ] && [ -n "$SPOTIFLAC_TELEGRAM_CHAT_ID" ]; then
        curl -s --max-time 10 -X POST \
            "https://api.telegram.org/bot${SPOTIFLAC_TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${SPOTIFLAC_TELEGRAM_CHAT_ID}" \
            -d text="$msg" > /dev/null
    fi
    echo "[notify] $msg" >> "$SPOTIFLAC_STATE_DIR/notify.log"
}
