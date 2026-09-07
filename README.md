# spotiflac-pipeline

[![CI](https://github.com/Ricky-Bonder/spotiflac-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/Ricky-Bonder/spotiflac-pipeline/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An opinionated, self-healing pipeline for building a high-quality local music
library from Spotify playlists — wraps [spotiflac](https://pypi.org/project/spotiflac/)
with a missing-only batch driver, a permanent-failure quarantine, a
provider-rotation watchdog, audio/metadata verification, per-playlist M3U
generation, and a metadata enrichment toolkit (genres, covers, synced lyrics,
artist countries).

Built and battle-tested against a real 43-playlist library (~4 600 tracks,
~250 GB) serving Navidrome/Symfonium on a homelab box. The
[`CHANGELOG`](CHANGELOG.md) documents the months of iteration that produced
this — including the May→September 2026 migration from spotiflac 0.5 to the
3.8 extension runtime.

> **Personal-use quality.** This is shared more as "show your work" than as a
> polished tool. PRs welcome — see [CONTRIBUTING](docs/DEVELOPMENT.md).

---

## What it does

```
                ┌────────────────────────┐
   playlists.txt│  spotiflac-watchdog.sh │  (cron, every 15 min)
   ────────────►│  ─ rotates providers   │
                │  ─ defers to backups   │
                │  ─ self-disables done  │
                └───────────┬────────────┘
                            │ kicks off
                            ▼
                ┌────────────────────────┐     ┌──────────────────────────┐
                │  run_all.sh            │────►│  fetch-missing.py        │
                │  (per-playlist batch)  │     │  ─ full track list via   │
                │                        │     │    spotiflac's metadata  │
                │  done when everything  │     │    client (no 100-cap)   │
                │  is present *or*       │     │  ─ downloads ONLY tracks │
                │  quarantined           │     │    absent from the index │
                └───────────┬────────────┘     │  ─ unavailable.txt       │
                            │                  │    quarantine after N    │
              ┌─────────────┼─────────────┐    │    failed attempts       │
              ▼             ▼             ▼    └──────────────────────────┘
       migrate-to-flat  verify-and-     spotify-diff
       (flatten into    cleanup         (daily — full-list diff,
        _library/ +     (duration-      detects adds AND removes
        M3U regen)      mismatch        on any playlist size)
                        purge)
```

Plus a `tools/` directory of library-maintenance utilities: tag enrichment
and genre normalization, cover replacement for YouTube-sourced rips, synced
lyrics backfill, MusicBrainz artist countries, and importers that fold
external download folders (yt-dlp SoundCloud syncs, old Lidarr roots) into
the same `_library/<Artist>/<Album>/` structure.

### The core problems it solves

| Problem | Fix |
|---|---|
| A playlist retry re-downloads **all** its tracks (migrate moves files away, so spotiflac's skip-existing never fires) | `fetch-missing.py` diffs the live track list against `track-id-index.json` and fetches only the gap — a +5-tracks update costs 5 downloads |
| Tracks no provider can deliver keep a playlist retrying forever | Per-track failure counter → `unavailable.txt` quarantine; the playlist completes with its obtainable set |
| Fuzzy provider matching returns *misrouted* audio (right metadata, wrong recording) | Deezer-first chains (ISRC-based, correct by construction) + a duration-mismatch verifier that purges misroutes per batch and weekly |
| Spotify's embed pages cap at 100 tracks, hiding deletions on big playlists | `spotify-diff.py` enumerates full track lists through spotiflac's metadata client (embed scrape kept as fallback) |
| spotiflac ≥3.8 ships **no** download providers and its extension runtime has sharp edges (Node ≥ 20, browser-based session solver, extension/runtime version skew) | Documented setup + pinning strategy in [INSTALL](docs/INSTALL.md) and [TROUBLESHOOTING](docs/TROUBLESHOOTING.md); the pipeline scripts honor `SPOTIFLAC_EXTRA_PATH` for a user-local Node |

---

## Install

### Prerequisites

- Linux (tested on Ubuntu 24.04). Likely works on macOS.
- Python ≥ 3.10, `ffmpeg`, `curl`
- **Node ≥ 20** (spotiflac's JS extension runtime breaks on distro Node 18)
- **Xvfb + Chromium** for the deezer/tidal extensions' session solver
- An extension registry for spotiflac ≥3.8 — see [INSTALL](docs/INSTALL.md#extensions)

```bash
git clone https://github.com/Ricky-Bonder/spotiflac-pipeline
cd spotiflac-pipeline
./install.sh
```

`install.sh` is idempotent. It verifies host deps (warns on old Node /
missing Xvfb), creates the venv, installs `spotiflac>=3.8` + `yt-dlp` +
`mutagen`, and seeds the config + `playlists.txt`.

### Configure

Edit `~/.config/spotiflac-pipeline/spotiflac.env`. Every key is optional
(defaults shown commented-out). Most users only set:

```bash
SPOTIFLAC_MUSIC_ROOT="$HOME/Music"        # where downloads live
SPOTIFLAC_EXTRA_PATH="$HOME/.local/bin"   # if your Node ≥20 lives there
SPOTIFLAC_TELEGRAM_BOT_TOKEN="123:abc…"   # optional, for notifications
SPOTIFLAC_TELEGRAM_CHAT_ID="987654321"
```

See [`config.example.env`](config.example.env) for the full reference.

### Run one playlist

Add a Spotify playlist URL to `~/.local/state/spotiflac-pipeline/playlists.txt`:

```
https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
```

Then:

```bash
bin/run_all.sh
```

### Run continuously

Install the cron entries from [`examples/crontab.example`](examples/crontab.example).
The watchdog handles everything from there — provider rotation, resource
monitoring, self-shutdown on completion.

### Run in Docker (experimental)

`docker/` bundles the whole runtime — Python, Node 22, Xvfb, Chromium,
ffmpeg, and **pre-pinned compatible extensions** (deezer + tidal-web) —
behind a scheduler entrypoint that mirrors the crontab cadence. Downloads
work out of the box, no registry setup needed:

```bash
docker pull ghcr.io/ricky-bonder/spotiflac-pipeline:latest
# adapt docker/docker-compose.example.yml (music/state/config volumes),
# drop playlist URLs into state/playlists.txt — done.
```

Or build locally: `docker build -f docker/Dockerfile -t spotiflac-pipeline .`

---

## Documentation

| | |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Data flow, state files, M3U strategy, what each script does and why |
| [`docs/INSTALL.md`](docs/INSTALL.md) | Long-form first-run walkthrough, spotiflac ≥3.8 extension setup |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Extension-runtime failure modes, provider outages, misroute defense |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Every config key with intent and edge cases |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | How to test changes locally, the contributing flow |

---

## Known limitations

- **spotiflac ≥3.8 provider health is external.** Extensions resolve and
  download through community endpoints that come and go; as of late 2026 only
  the deezer and tidal-web extensions reliably work. The quarantine keeps the
  pipeline converging regardless.
- **No Windows support.** The shell scripts assume POSIX (`bash`, `pgrep`,
  `crontab`).
- **Docker runtime is experimental** — the bare-metal cron path is what the
  author actually runs.

---

## Project status

| | |
|---|---|
| Stability | Personal-use — runs the author's 43-playlist library daily |
| Maintained | Best-effort; the author runs this on their own server |
| Open to PRs | Yes — see [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) |
| Roadmap | [`CHANGELOG.md`](CHANGELOG.md) under *Unreleased* |

## License

MIT — see [`LICENSE`](LICENSE).
