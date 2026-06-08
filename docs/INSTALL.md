# Installation — long form

A guided walk-through. If you just want the quick-start, see the
[README](../README.md). This page covers what `install.sh` does, what
manual install looks like, and how to verify the setup.

## What `install.sh` does

```text
[1/5] Checking host dependencies…
  python 3.12, ffprobe, patch, curl — ok

[2/5] Creating venv at /home/you/.local/share/spotiflac-pipeline/venv…

[3/5] Installing spotiflac + yt-dlp into the venv…

[4/5] Applying spotiflac link_resolver patch (if needed)…
  Patched /home/you/.local/share/spotiflac-pipeline/venv/lib/python3.12/
          site-packages/SpotiFLAC/core/link_resolver.py

[5/5] Preparing state directory at /home/you/.local/state/spotiflac-pipeline…
  Seeded /home/you/.local/state/spotiflac-pipeline/playlists.txt — add your
  playlist URLs there.

Done. Next steps:
  1. Edit /home/you/.config/spotiflac-pipeline/spotiflac.env
  2. Add your Spotify playlist URLs to .../playlists.txt
  3. Run a single playlist manually: bin/run_all.sh
  4. Or install the watchdog into cron: see examples/crontab.example
```

It's idempotent — re-running upgrades spotiflac and re-checks the patch
without disturbing your state directory or downloaded music.

## Host dependencies

| Package | Why | Test |
|---|---|---|
| `python3 ≥ 3.10` | spotiflac + all the pipeline scripts | `python3 -c "import sys; assert sys.version_info >= (3,10)"` |
| `ffmpeg` (specifically `ffprobe`) | Read audio duration + tags from FLAC/MP3 | `ffprobe -version` |
| `patch` | Apply the link_resolver fix | `which patch` |
| `curl` | Telegram notifications + Spotify embed scraping | `which curl` |

On Debian/Ubuntu: `sudo apt install python3 python3-venv ffmpeg patch curl`

`yt-dlp` is installed *into the venv* by `install.sh`; you don't need it as a
system package.

## Manual install (if `install.sh` doesn't fit your setup)

```bash
# 1. Make a venv
python3 -m venv ~/.local/share/spotiflac-pipeline/venv
source ~/.local/share/spotiflac-pipeline/venv/bin/activate
pip install -U pip
pip install "spotiflac>=0.5.1,<0.6" "yt-dlp>=2024.0"

# 2. Apply the patch
SITE=$(python -c "import SpotiFLAC, os; print(os.path.dirname(os.path.dirname(SpotiFLAC.__file__)))")
patch -p1 -d "$SITE" < /path/to/spotiflac-pipeline/patches/spotiflac-0.5.1-link-resolver.patch

# 3. Seed config
mkdir -p ~/.config/spotiflac-pipeline ~/.local/state/spotiflac-pipeline
cp /path/to/spotiflac-pipeline/config.example.env ~/.config/spotiflac-pipeline/spotiflac.env
$EDITOR ~/.config/spotiflac-pipeline/spotiflac.env
```

## Verify the install

```bash
# 1. spotiflac entrypoint should resolve in the venv
~/.local/share/spotiflac-pipeline/venv/bin/spotiflac --help | head

# 2. Patch is applied
grep -q "_PLATFORM_URL_TEMPLATES" \
  ~/.local/share/spotiflac-pipeline/venv/lib/python*/site-packages/SpotiFLAC/core/link_resolver.py \
  && echo "patch ok" || echo "PATCH MISSING"

# 3. Config loads without complaint
~/.local/share/spotiflac-pipeline/venv/bin/python3 -c "
import sys; sys.path.insert(0, 'bin')
from _common import OUTPUT_DIR, STATE_DIR, VENV
print(f'OUTPUT_DIR = {OUTPUT_DIR}')
print(f'STATE_DIR  = {STATE_DIR}')
print(f'VENV       = {VENV}')
"

# 4. Dry-run a single playlist (cancel after a few seconds with Ctrl-C)
echo "https://open.spotify.com/playlist/<some-public-playlist-id>" \
    > ~/.local/state/spotiflac-pipeline/playlists.txt
bin/run_all.sh
```

## Install the cron entries

```bash
crontab -e
# Paste the contents of examples/crontab.example, substituting ${SPF} and ${ENV}
```

After saving:

```bash
crontab -l           # confirm entries are present
# Wait up to 15 min, then:
tail -f ~/.local/state/spotiflac-pipeline/watchdog.log
```

## Uninstall

```bash
# 1. Remove cron entries
crontab -e   # delete the spotiflac-pipeline lines

# 2. Remove state + venv (preserves your music!)
rm -rf ~/.local/share/spotiflac-pipeline
rm -rf ~/.local/state/spotiflac-pipeline
rm -rf ~/.config/spotiflac-pipeline

# 3. Remove the repo clone
rm -rf /path/to/spotiflac-pipeline
```

Your music in `$SPOTIFLAC_OUTPUT_DIR` is never touched by uninstall.
