#!/bin/bash
# Bootstrap a spotiflac-pipeline install.
#
#   1. Check host dependencies (python3, ffmpeg).
#   2. Create a Python venv at $SPOTIFLAC_VENV.
#   3. pip install spotiflac (the upstream FLAC downloader) + yt-dlp.
#   4. Create the state directory and seed a config file from
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

echo "[1/4] Checking host dependencies…"
need python3 "python3"
need ffprobe "ffmpeg"
need curl    "curl"

# spotiflac ≥3.8 runs its download providers as JS extensions under Node.
# Node ≥ 20 is required: on Node 18 the extension sandbox misclassifies
# module loads as writes and every extension dies with a startup timeout.
if command -v node >/dev/null 2>&1; then
    NODE_MAJOR=$(node -v | sed 's/^v//' | cut -d. -f1)
    if [ "$NODE_MAJOR" -lt 20 ]; then
        echo "  WARNING: node $(node -v) found — spotiflac's extension runtime needs ≥ 20." >&2
        echo "  Install a user-local Node (e.g. official tarball into ~/.local) and set" >&2
        echo "  SPOTIFLAC_EXTRA_PATH to its bin dir in your config." >&2
    else
        echo "  node $(node -v) — ok"
    fi
else
    echo "  WARNING: node not found — spotiflac ≥3.8 needs Node ≥ 20 for its extensions." >&2
fi

# The deezer/tidal extensions solve a signed-session challenge with a real
# browser: Xvfb + a Chromium are needed on headless boxes.
for opt in Xvfb chromium-browser chromium google-chrome; do
    command -v "$opt" >/dev/null 2>&1 && FOUND_BROWSER_DEPS="${FOUND_BROWSER_DEPS:-}$opt "
done
case "${FOUND_BROWSER_DEPS:-}" in
    *Xvfb*chromium*|*Xvfb*chrome*) echo "  Xvfb + chromium — ok" ;;
    *) echo "  NOTE: install xvfb + chromium for the deezer/tidal extensions" >&2
       echo "        (apt install xvfb chromium-browser)" >&2 ;;
esac

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "  Need Python ≥ 3.10 (found $PY_VER)." >&2
    exit 2
fi
echo "  python $PY_VER, ffprobe, curl — ok"

echo "[2/4] Creating venv at $SPOTIFLAC_VENV…"
if [ ! -d "$SPOTIFLAC_VENV" ]; then
    mkdir -p "$(dirname "$SPOTIFLAC_VENV")"
    python3 -m venv "$SPOTIFLAC_VENV"
fi
"$SPOTIFLAC_VENV/bin/pip" install --quiet --upgrade pip

echo "[3/4] Installing spotiflac + yt-dlp into the venv…"
# Pin range: 0.6.9 is the first release with the rewritten link_resolver.py
# (no more deprecated Odesli ?id=&platform= form). 0.9.x onwards restructured
# the package layout to backend.launcher — handled but more brittle. 1.0.0's
# published wheel is broken (empty top_level.txt, missing backend/). We pin
# to the validated 0.8.x range; raise upper bound after the next round of
# upstream stabilises.
"$SPOTIFLAC_VENV/bin/pip" install --quiet "spotiflac>=3.8,<4" "yt-dlp>=2024.0" "mutagen>=1.47"

echo "[4/4] Preparing state directory at $SPOTIFLAC_STATE_DIR…"
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
