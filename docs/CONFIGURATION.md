# Configuration reference

All keys live in `~/.config/spotiflac-pipeline/spotiflac.env`. The file is
sourced by shell scripts and parsed by Python scripts at startup; values are
plain `KEY=value` with optional double quotes around values that contain
spaces.

Every key is optional — defaults work for a single-user homelab setup that
keeps music in `~/Music`. Override only what you need to.

## Paths

| Key | Default | Notes |
|---|---|---|
| `SPOTIFLAC_MUSIC_ROOT` | `$HOME/Music` | Root under which all derived paths live |
| `SPOTIFLAC_OUTPUT_DIR` | `$SPOTIFLAC_MUSIC_ROOT/spotiflac` | Where the FLAC library + M3Us are built. Must be writable. |
| `SPOTIFLAC_SPOTDL_ROOT` | `$SPOTIFLAC_MUSIC_ROOT/spotdl` | Optional existing spotdl downloads. Leave unset if you don't have any. |
| `SPOTIFLAC_STATE_DIR` | `$HOME/.local/state/spotiflac-pipeline` | Tiny JSON/txt state files. ~50 MB max even on a big library. |
| `SPOTIFLAC_VENV` | `$HOME/.local/share/spotiflac-pipeline/venv` | The Python venv `install.sh` builds |

The pipeline never writes outside `SPOTIFLAC_OUTPUT_DIR` and
`SPOTIFLAC_STATE_DIR`. The `dedup-tracks.py` script *reads* arbitrary
subdirectories of `SPOTIFLAC_MUSIC_ROOT` but only moves files into a
`_dedup_quarantine/` subdir of the same source root (reversible).

## Notifications

| Key | Default | Notes |
|---|---|---|
| `SPOTIFLAC_TELEGRAM_BOT_TOKEN` | (empty — notifications disabled) | Get one from [@BotFather](https://t.me/botfather) |
| `SPOTIFLAC_TELEGRAM_CHAT_ID` | (empty) | Your numeric Telegram user ID or a group/channel ID |

If both are set, the watchdog and batch driver send Telegram messages on
start, completion, errors, and resource warnings. The Python notifier
(`_common.py:notify`) and shell notifier (`_common.sh:spf_notify`) share
the same env vars and behavior.

## Watchdog

| Key | Default | Notes |
|---|---|---|
| `SPOTIFLAC_BACKUP_MOUNTPOINT` | (empty) | Path that, when mounted, makes the watchdog defer (e.g. `/mnt/backup-disk`) |
| `SPOTIFLAC_BACKUP_PROCNAMES` | (empty) | Comma-separated list of process names that, when running, make the watchdog defer (e.g. `restic,duplicacy,backup.sh`) |
| `SPOTIFLAC_MIN_FREE_DISK_GB` | `50` | Warn + skip batch if `SPOTIFLAC_OUTPUT_DIR` has less free space |
| `SPOTIFLAC_MIN_FREE_MEM_MB` | `200` | Warn (don't skip) if available RAM falls below this |

The watchdog also auto-defers when an `rsync` process with the output dir in
its argv is detected — no config needed.

## Provider chains

| Key | Default | Notes |
|---|---|---|
| `SPOTIFLAC_PROVIDER_CHAINS` | `deezer tidal amazon youtube,deezer amazon tidal youtube,deezer tidal amazon,deezer youtube` | Comma-separated chains; each chain is a space-separated provider order |

When the active chain produces no progress over `MAX_RETRIES_PER_SERVICE` (3)
watchdog ticks, the watchdog rotates to the next chain. After exhausting all
chains, it pauses for 1 h and starts over.

Available providers (per spotiflac): `deezer`, `tidal`, `amazon`, `qobuz`,
`soundcloud`, `youtube`, `spoti`, `apple`. The leftmost in a chain is tried
first per track.

**Why Deezer first?** Deezer maps Spotify → ISRC → Deezer, so the matched
track is the same recording by definition. Tidal and Amazon match fuzzily on
title + artist + duration and ~64 % of those FLACs were misrouted in the
author's library before the verifier was added. See [ARCHITECTURE.md](ARCHITECTURE.md#why-two-stages-of-misroute-defense).

## Optional spotdl integration

| Key | Default | Notes |
|---|---|---|
| `SPOTIFLAC_OLD_PLAYLISTS_JSON` | (empty) | Path to an old spotdl playlist export. Used by `migrate-to-flat.py` (enrich M3Us for playlists >100 tracks) and `audit-spotdl.py` (filename matching base). Leave unset if you've never used spotdl. |
| `SPOTIFLAC_LIKES_ALIASES` | `Liked Songs` | Comma-separated playlist names treated as one logical "Likes" proxy when merging IDs from the spotdl JSON |

## Per-invocation overrides

Any key can be overridden for a single invocation via environment:

```bash
SPOTIFLAC_MIN_FREE_DISK_GB=10 bin/run_all.sh   # temporarily lower the threshold
SPOTIFLAC_CONFIG_FILE=/tmp/test.env bin/spotiflac-watchdog.sh
```

## Validating your config

```bash
~/.local/share/spotiflac-pipeline/venv/bin/python3 -c "
import sys; sys.path.insert(0, 'bin')
import _common
for k, v in vars(_common).items():
    if k.isupper() and not k.startswith('_'):
        print(f'{k:<25} {v}')
"
```
