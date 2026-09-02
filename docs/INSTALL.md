# Installation

The quick path is `./install.sh` from the repo root. This page is the
long-form version: what gets installed, the spotiflac ≥3.8 extension setup
that `install.sh` can't do for you, and how to verify each piece.

## Host dependencies

| Dependency | Used by | Check |
|---|---|---|
| `python3 ≥ 3.10` | spotiflac + all pipeline scripts | `python3 -c "import sys; assert sys.version_info >= (3,10)"` |
| `ffmpeg` (`ffprobe`) | verifier, taggers, migrate | `ffprobe -version` |
| `curl` | notifications, watchdog checks | `curl --version` |
| **Node ≥ 20** | spotiflac's JS extension runtime | `node -v` |
| **Xvfb + Chromium** | the deezer/tidal extensions' browser-based session solver | `which Xvfb chromium-browser` |

```bash
sudo apt install ffmpeg curl xvfb chromium-browser
```

**About Node:** Debian/Ubuntu LTS ship Node 18, which breaks the extension
sandbox (module loads are misclassified as writes; every extension dies with
a 30 s startup timeout). If you can't install Node ≥ 20 system-wide, a
user-local tarball works fine:

```bash
curl -fsSL https://nodejs.org/dist/latest-v22.x/ | grep -oP 'node-v22[^"]+linux-x64\.tar\.xz' | head -1
# download + extract that under ~/.local/opt, then:
ln -s ~/.local/opt/node-v22.*/bin/node ~/.local/bin/node
```

and set `SPOTIFLAC_EXTRA_PATH="$HOME/.local/bin"` in your config — every
pipeline script prepends it to `PATH`.

## Run the installer

```bash
git clone https://github.com/Ricky-Bonder/spotiflac-pipeline
cd spotiflac-pipeline
./install.sh
```

It is idempotent and:

1. Checks host dependencies (warns on old Node / missing Xvfb).
2. Creates a venv at `$SPOTIFLAC_VENV` (default
   `~/.local/share/spotiflac-pipeline/venv`).
3. Installs `spotiflac>=3.8,<4`, `yt-dlp`, `mutagen`.
4. Seeds `~/.config/spotiflac-pipeline/spotiflac.env` from
   `config.example.env` and an empty `playlists.txt` in the state dir.

## Extensions

spotiflac ≥3.8 ships **no download providers**. Downloads run through JS
extensions installed from a registry — and no registry is configured out of
the box. Two things to set up once:

### 1. Point spotiflac at a registry

spotiflac reads `SPOTIFLAC_REGISTRIES` from its own env file
(`~/.spotiflac_env`), *not* from this pipeline's config:

```bash
echo 'SPOTIFLAC_REGISTRIES=https://raw.githubusercontent.com/spotiflacapp/SpotiFLAC-Extension/main/registry.json' \
  > ~/.spotiflac_env
```

On the next invocation spotiflac bootstraps the extensions it needs into
`~/.spotiflac/extensions/`.

### 2. Mind extension/runtime version skew

The registry serves extensions built for the *mobile app's* runtime, which
moves faster than the PyPI module. If downloads fail with
`… is not a function` errors, the extension calls bridge features your
module doesn't implement — pin older builds:

```bash
# does your bridge support the feature the extension wants?
grep -c downloadSegments "$SPOTIFLAC_VENV"/lib/python3*/site-packages/SpotiFLAC/extensions/_bridge.js

# pick an older .sflx from the registry repo's git history, then:
unzip -o deezer-<sha>.sflx -d ~/.spotiflac/extensions/deezer/

# IMPORTANT: comment SPOTIFLAC_REGISTRIES back out of ~/.spotiflac_env —
# the startup bootstrap force-updates any version mismatch and would undo
# the pin on the next run.
```

See [TROUBLESHOOTING](TROUBLESHOOTING.md#spotiflac-38-the-extension-era) for
the full failure-mode catalogue.

## Smoke test

```bash
V=~/.local/share/spotiflac-pipeline/venv
rm -rf /tmp/sftest && mkdir -p /tmp/sftest
"$V/bin/spotiflac" "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b" /tmp/sftest \
    --service deezer tidal --retries 1 --quality LOSSLESS \
    --use-artist-subfolders --use-album-subfolders
find /tmp/sftest -name "*.flac"    # expect one FLAC with Artist/Album dirs
```

If the session summary says `Successful : 1`, the whole chain — Node,
extensions, browser solver, registry — works.

## First playlist

```bash
echo "https://open.spotify.com/playlist/<id>" >> ~/.local/state/spotiflac-pipeline/playlists.txt
bin/run_all.sh
```

Watch the RESULT lines land in `~/.local/state/spotiflac-pipeline/spotiflac.log`;
the library materializes under `$SPOTIFLAC_OUTPUT_DIR/_library/` with M3Us in
`_playlists/`.

## Continuous operation

```bash
crontab -l | cat - examples/crontab.example | crontab -
```

The watchdog takes it from there. It removes its own cron entry when every
playlist is done, and `spotify-diff.py` re-queues playlists whenever they
change on Spotify.
