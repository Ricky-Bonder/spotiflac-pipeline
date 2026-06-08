#!/bin/bash
# Bootstrap a spotiflac-pipeline install.
#
#   1. Check host dependencies (python3, ffmpeg, patch).
#   2. Create a Python venv at $SPOTIFLAC_VENV.
#   3. pip install spotiflac (the upstream FLAC downloader) + yt-dlp.
#   4. Apply patches/spotiflac-0.5.1-link-resolver.patch to the freshly
#      installed spotiflac package, unless the fix is already present.
#   5. Create the state directory and seed a config file from
#      config.example.env if the user doesn't have one yet.
#
# Idempotent. Safe to re-run after upgrading. Does NOT touch your music root.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPF_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/spotiflac-pipeline"
SPF_CONFIG_FILE="$SPF_CONFIG_DIR/spotiflac.env"

if [ ! -f "$SPF_CONFIG_FILE" ]; then
    mkdir -p "$SPF_CONFIG_DIR"
    cp "$REPO_DIR/config.example.env" "$SPF_CONFIG_FILE"
    echo "Seeded a default config at $SPF_CONFIG_FILE — review it before running anything."
fi
# shellcheck disable=SC1090
source "$SPF_CONFIG_FILE"

SPOTIFLAC_VENV="${SPOTIFLAC_VENV:-$HOME/.local/share/spotiflac-pipeline/venv}"
SPOTIFLAC_STATE_DIR="${SPOTIFLAC_STATE_DIR:-$HOME/.local/state/spotiflac-pipeline}"

need() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "  Missing required binary: $1" >&2
        echo "    Install via your distro's package manager (e.g. apt install $2)." >&2
        exit 2
    }
}

echo "[1/5] Checking host dependencies…"
need python3 "python3"
need ffprobe "ffmpeg"
need patch  "patch"
need curl   "curl"

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "  Need Python ≥ 3.10 (found $PY_VER)." >&2
    exit 2
fi
echo "  python $PY_VER, ffprobe, patch, curl — ok"

echo "[2/5] Creating venv at $SPOTIFLAC_VENV…"
if [ ! -d "$SPOTIFLAC_VENV" ]; then
    mkdir -p "$(dirname "$SPOTIFLAC_VENV")"
    python3 -m venv "$SPOTIFLAC_VENV"
fi
"$SPOTIFLAC_VENV/bin/pip" install --quiet --upgrade pip

echo "[3/5] Installing spotiflac + yt-dlp into the venv…"
"$SPOTIFLAC_VENV/bin/pip" install --quiet "spotiflac>=0.5.1,<0.6" "yt-dlp>=2024.0"

echo "[4/5] Applying spotiflac link_resolver patch (if needed)…"
SITE_DIR=$("$SPOTIFLAC_VENV/bin/python3" -c \
    "import SpotiFLAC, os; print(os.path.dirname(os.path.dirname(SpotiFLAC.__file__)))")
TARGET="$SITE_DIR/SpotiFLAC/core/link_resolver.py"
if grep -q "_PLATFORM_URL_TEMPLATES" "$TARGET" 2>/dev/null; then
    echo "  Already patched — skipping."
else
    if patch --dry-run -p1 -d "$SITE_DIR" < "$REPO_DIR/patches/spotiflac-0.5.1-link-resolver.patch" >/dev/null 2>&1; then
        patch -p1 -d "$SITE_DIR" < "$REPO_DIR/patches/spotiflac-0.5.1-link-resolver.patch"
        echo "  Patched $TARGET"
    else
        echo "  Patch did not apply cleanly — your spotiflac version may differ from 0.5.1." >&2
        echo "  Inspect $REPO_DIR/patches/spotiflac-0.5.1-link-resolver.patch manually." >&2
    fi
fi

echo "[5/5] Preparing state directory at $SPOTIFLAC_STATE_DIR…"
mkdir -p "$SPOTIFLAC_STATE_DIR"
if [ ! -f "$SPOTIFLAC_STATE_DIR/playlists.txt" ]; then
    cp "$REPO_DIR/examples/playlists.txt.example" "$SPOTIFLAC_STATE_DIR/playlists.txt"
    echo "  Seeded $SPOTIFLAC_STATE_DIR/playlists.txt — add your playlist URLs there."
fi

echo
echo "Done. Next steps:"
echo "  1. Edit $SPF_CONFIG_FILE (output dir, Telegram bot if you want notifications)"
echo "  2. Add your Spotify playlist URLs to $SPOTIFLAC_STATE_DIR/playlists.txt"
echo "  3. Run a single playlist manually:    $REPO_DIR/bin/run_all.sh"
echo "  4. Or install the watchdog into cron: see examples/crontab.example"
