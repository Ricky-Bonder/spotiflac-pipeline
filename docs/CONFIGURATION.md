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
| `SPOTIFLAC_STATE_DIR` | `$HOME/.local/state/spotiflac-pipeline` | Tiny JSON/txt state files. ~50 MB max even on a big library. |
| `SPOTIFLAC_VENV` | `$HOME/.local/share/spotiflac-pipeline/venv` | The Python venv `install.sh` builds |

The pipeline never writes outside `SPOTIFLAC_OUTPUT_DIR` and
`SPOTIFLAC_STATE_DIR`. The maintenance tools in `tools/` can also read
`SPOTIFLAC_MUSIC_ROOT` (e.g. `library-import.py` consolidating external
download folders into the library).

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
| `SPOTIFLAC_PROVIDER_CHAINS` | `deezer tidal,tidal deezer,deezer,tidal` | Comma-separated chains; each chain is a space-separated provider order |

When the active chain produces no progress over `MAX_RETRIES_PER_SERVICE` (3)
watchdog ticks, the watchdog rotates to the next chain. After exhausting all
chains, it backs off exponentially (1 h → 2 h → 4 h … capped at 24 h),
sending a single 🚨 per failure streak; the backoff resets as soon as a
batch makes progress.

With spotiflac ≥3.8 these aliases resolve to installed JS extensions
(`deezer` → `ext:deezer`, `tidal` → `ext:tidal-web`, …). As of late 2026
only the deezer and tidal-web extensions reliably deliver audio — the
amazon and ytmusic extensions' search endpoints are dead — hence the
trimmed default. The leftmost provider in a chain is tried first per track.

**Why Deezer first?** Deezer maps Spotify → ISRC → Deezer, so the matched
track is the same recording by definition. Tidal and Amazon match fuzzily on
title + artist + duration and ~64 % of those FLACs were misrouted in the
author's library before the verifier was added. See [ARCHITECTURE.md](ARCHITECTURE.md#why-two-stages-of-misroute-defense).

## spotiflac ≥3.8 runtime

| Key | Default | Notes |
|---|---|---|
| `SPOTIFLAC_EXTRA_PATH` | (empty) | Directory prepended to `PATH` by every pipeline script. Point it at a user-local Node ≥ 20 `bin/` if your distro ships Node 18 (which breaks spotiflac's extension sandbox). |

spotiflac ≥3.8 ships **no download providers**: you must configure an
extension registry (`SPOTIFLAC_REGISTRIES` in `~/.spotiflac_env`, read by
spotiflac itself, not by this pipeline) and mind extension/runtime version
compatibility — see [INSTALL.md](INSTALL.md#extensions) and
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Missing-only batch driver

| Key | Default | Notes |
|---|---|---|
| `SPOTIFLAC_MAX_TRACK_FAILS` | `4` | Failed batch attempts before a track is quarantined in `unavailable.txt` (skipped thereafter; delete its line to retry) |
| `SPOTIFLAC_TRACK_TIMEOUT_S` | `420` | Hard timeout for a single-track spotiflac invocation |

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
