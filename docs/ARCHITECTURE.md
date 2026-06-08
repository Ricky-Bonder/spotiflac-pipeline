# Architecture

This document explains *why* the pipeline looks the way it does. For the
*what*, see the inline docstrings at the top of every script in `bin/`.

## Components

| File | Role | Trigger |
|---|---|---|
| `bin/run_all.sh` | Per-batch driver: iterate playlists, call spotiflac, parse session summary, hook post-success migrate + verify | manual, or kicked by watchdog |
| `bin/spotiflac-watchdog.sh` | Keeps `run_all.sh` alive, rotates provider chains, defers to backups, self-disables when done | cron `*/15 * * * *` |
| `bin/migrate-to-flat.py` | Flatten per-playlist subdirs → `_library/<Artist>/<Album>/`, maintain a persistent Spotify-ID index, regenerate M3Us | post-success hook from `run_all.sh` |
| `bin/verify-and-cleanup.py` | Compare every FLAC's duration to Spotify's reported duration; flag/delete misroutes; regenerate affected M3Us | post-success hook + weekly cron |
| `bin/spotify-diff.py` | Scrape each playlist's embed page; detect adds/removes; unmark playlist from `done.txt` if changed | daily cron |
| `bin/audit-spotdl.py` | Audit a pre-existing spotdl MP3 collection against a JSON export | one-off |
| `bin/redownload-spotdl.py` | Replace BAD spotdl MP3s via yt-dlp using closest-duration matching | one-off (after audit) |
| `bin/dedup-tracks.py` | Cross-source dedup: keep one copy per track by quality priority | one-off / periodic |

## Data flow

```
       Spotify playlist URL
              │
              ▼
       ┌──────────────┐
       │  spotiflac   │── (Odesli → Deezer/Tidal/Amazon/YouTube) ──► FLAC/M4A
       └──────────────┘
              │
              ▼
       run_all.sh parses session summary
              │
       success │ failure
        ▼      │
   migrate-to-flat.py       ── appends to track-id-index.json
        │
        ▼
   verify-and-cleanup.py --clean   (purges duration mismatches,
        │                           re-opens affected playlists)
        ▼
   _playlists/<Name>.m3u8        (FLAC entries first, MP3 fallback)
```

## State files

Everything that needs to survive across runs lives in `$SPOTIFLAC_STATE_DIR`
(default `~/.local/state/spotiflac-pipeline`):

| File | Owner | Purpose |
|---|---|---|
| `playlists.txt` | user | Source list — one Spotify URL per line |
| `done.txt` | `run_all.sh` | Playlist IDs that completed (skip on next run) |
| `failed.txt` | `run_all.sh` | Per-playlist log of failed tracks for the last batch |
| `playlist-state.json` | `spotify-diff.py` | Last-known name / total / track IDs per playlist |
| `track-id-index.json` | `migrate-to-flat.py` | `{spotify_id: relative_path}` for everything in `_library/` |
| `verify-report.json` | `verify-and-cleanup.py` | Latest FLAC verification results |
| `spotify-track-cache.json` | `verify-and-cleanup.py` | Spotify duration cache (avoids re-scraping) |
| `spotdl-audit.json` | `audit-spotdl.py` | MP3 audit buckets (good/bad/unmatched) |
| `spotdl-redownload-state.json` | `redownload-spotdl.py` | Per-spotify-id attempt counter |
| `spotdl-permanent-failures.txt` | `redownload-spotdl.py` | Spotify IDs that exhausted MAX_ATTEMPTS |
| `dedup-report.json` | `dedup-tracks.py` | Last dedup pass — groups, keepers, losers |
| `watchdog.state` | `spotiflac-watchdog.sh` | Sourceable Bash file: prev_done_count, attempts, paused_until, service_idx |
| `service.conf` | watchdog | Currently-active provider chain (also sourceable Bash) |
| `*.log` | various | Rotated at 5 MB by the watchdog |

## Output layout

```
$SPOTIFLAC_OUTPUT_DIR/
├── _library/
│   └── <Artist>/<Album>/Track.flac
└── _playlists/
    ├── All Tracks.m3u8        (union of every playlist's resolved tracks)
    ├── <Playlist 1>.m3u8       (FLAC entries first, then MP3 fallback)
    └── <Playlist 2>.m3u8
```

The flat-by-artist layout is deliberate: Navidrome, Sonos, Plex, and most
other music servers index by artist/album, not by playlist subdirectory. The
M3Us re-create the playlist view *on top of* the flat library, so the same
track in three playlists is one file on disk.

## Why two stages of misroute defense

spotiflac uses [Odesli](https://song.link) (api.song.link) to translate a
Spotify track into its Deezer / Tidal / Amazon Music equivalent, then
downloads from the matched provider. Odesli's matching is *fuzzy* — same
artist + title + duration, not the same recording. For tracks with multiple
versions (live, remaster, deluxe, regional), Odesli sometimes maps to the
wrong one. The downloaded FLAC has correct metadata but wrong audio.

Two defenses:

1. **Provider order.** Deezer is ISRC-based (Spotify→ISRC→Deezer): if a
   Deezer track exists for that ISRC, it's the same recording by definition.
   We try Deezer first, fall back to Tidal/Amazon only when Deezer doesn't
   have the track.
2. **Duration verifier.** Every FLAC gets ffprobe'd and the real duration
   compared to Spotify's. A misroute almost always shows ≥5 s mismatch.
   `verify-and-cleanup.py --clean` deletes mismatches and unmarks the
   affected playlist for retry, ideally via a different provider next time.

## The watchdog state machine (simplified)

```
              ┌─────────────────────────────────────────────────┐
              │ tick: every 15 min from cron                    │
              └─────────────────────────────────────────────────┘
                                  │
       defer? (backup mounted, rsync, configured proc) ── yes ──► exit
                                  │ no
              ┌───────────────────┴───────────────────┐
              │  all playlists done? ── yes ──► notify + remove self from cron
              │                                       │
              │  paused? (pause-until in future) ── yes ──► exit
              │                                       │
              │  resource warnings (OOM/low mem/low disk)? ──► notify
              │                                       │
              │  batch still running? ── yes ──► update state, exit
              │                                       │
              │  attempts > MAX_RETRIES_PER_SERVICE?  │
              │       │ yes                           │
              │       ▼                               │
              │  rotate provider chain (or pause 1h   │
              │  after a full rotation)               │
              │                                       │
              │  restart batch detached, write state  │
              └───────────────────────────────────────┘
```

## Configuration loading

All scripts source a single config file (`~/.config/spotiflac-pipeline/spotiflac.env`).

- Shell scripts: `source bin/_common.sh` — applies defaults, exposes `spf_notify`
- Python scripts: `from _common import ...` — parses the env file, exports
  `Path` constants

This means the config file is sourceable in any shell, *and* parseable by
Python without PyYAML. Adding a new config key takes one line in each
loader plus a comment in `config.example.env`.
