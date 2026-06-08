# spotiflac-pipeline

An opinionated, self-healing pipeline for building a high-quality local music
library from Spotify playlists — wraps [spotiflac](https://pypi.org/project/spotiflac/)
and [yt-dlp](https://github.com/yt-dlp/yt-dlp) with a batch driver, a
provider-rotation watchdog, audio/metadata verification, cross-source
deduplication, and unified M3U playlist generation.

Built and battle-tested against a real 43-playlist library (~4 600 tracks,
~250 GB FLAC + MP3) on a homelab Navidrome server. The [`CHANGELOG`](CHANGELOG.md)
documents the ~25 days of iteration that produced this.

> **Personal-use quality, v0.1.** This is shared more as "show your work" than
> as a polished tool. PRs welcome — see [CONTRIBUTING](docs/DEVELOPMENT.md).

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
                ┌────────────────────────┐
                │  run_all.sh            │  (per-playlist batch)
                │  ─ spotiflac → FLAC    │
                │  ─ on success: hook    │
                └───────────┬────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
       migrate-to-flat  verify-and-     spotify-diff
       (flatten +       cleanup         (daily — detect
        unified M3Us)   (duration-      adds/removes on
                        mismatch        Spotify side)
                        purge)
                            │
                            ▼
                ┌────────────────────────┐
                │  audit-spotdl.py       │  (one-off — audit an
                │  redownload-spotdl.py  │   existing spotdl MP3
                │  dedup-tracks.py       │   collection, redownload
                └────────────────────────┘   misrouted, then dedup
                                             against the FLAC pass)
```

### The core problems it solves

| Problem | Fix |
|---|---|
| spotiflac 0.5.1's Odesli resolver returns HTTP 400 (`?id=&platform=` deprecated) | A bundled patch ([`patches/`](patches/)) rebuilds the request as `?url=…` |
| ~64 % of Tidal/Amazon FLACs are *misrouted* (right metadata, wrong audio) — Odesli's fuzzy mapping | Deezer-first provider chain (ISRC-based, correct by construction) + a duration-mismatch verifier that purges misroutes |
| Some tracks (rare scores, regional releases) are only on YouTube | Two-phase yt-dlp fallback: `ytsearch10:` for metadata, pick the candidate whose duration is *closest* to Spotify's |
| Multiple copies of the same track across spotdl + spotiflac + Lidarr | Cross-source dedup with `verified-good > FLAC > MP3 > M4A > bitrate > size` priority |
| Spotify's web API now requires the app owner to be a Premium subscriber (2025+) | Auth-free embed scraping for playlist sync (capped at 100 tracks/playlist) |

---

## Install

### Prerequisites

- Linux (tested on Ubuntu 24.04). Likely works on macOS.
- Python ≥ 3.10
- `ffmpeg`, `patch`, `curl`
- A spotiflac-compatible network — see [TROUBLESHOOTING](docs/TROUBLESHOOTING.md)
  for the Spotify Premium gate

```bash
git clone https://github.com/Ricky-Bonder/spotiflac-pipeline
cd spotiflac-pipeline
./install.sh
```

`install.sh` is idempotent. It:

1. Verifies host deps
2. Creates a Python venv at `~/.local/share/spotiflac-pipeline/venv`
3. Installs `spotiflac` and `yt-dlp`
4. Applies the `link_resolver.py` patch (skips if already patched)
5. Seeds `~/.config/spotiflac-pipeline/spotiflac.env` and a `playlists.txt`

### Configure

Edit `~/.config/spotiflac-pipeline/spotiflac.env`. Every key is optional
(defaults shown commented-out). Most users only set:

```bash
SPOTIFLAC_MUSIC_ROOT="$HOME/Music"        # where downloads live
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

---

## Documentation

| | |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Data flow, state files, M3U strategy, what each script does and why |
| [`docs/INSTALL.md`](docs/INSTALL.md) | Long-form first-run walkthrough with sample output |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Spotify Premium gate, provider outages, common failure modes |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Every config key with intent and edge cases |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | How to test changes locally, the contributing flow |

---

## Known limitations

- **Spotify Premium gate.** The Spotify Web API requires the *app owner* (not
  just the end-user) to hold a Premium subscription for any authenticated
  call. Without Premium, the pipeline falls back to scraping public embed
  pages, which serve at most 100 tracks per playlist. Playlists larger than
  that need a one-time import from another source — see
  [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).
- **No Windows support.** The shell scripts assume POSIX (`bash`, `pgrep`,
  `crontab`, `journalctl`).
- **No Sonarr/Beets integration.** Yet.

---

## Project status

| | |
|---|---|
| Stability | Personal-use — works for the author's 43-playlist library |
| Maintained | Best-effort; the author runs this on their own server |
| Open to PRs | Yes — see [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) |
| Roadmap | [`CHANGELOG.md`](CHANGELOG.md) under *Unreleased* |

## License

MIT — see [`LICENSE`](LICENSE).
