# Troubleshooting

## "All my downloads are failing — Odesli returns HTTP 400"

You're running upstream `spotiflac` 0.5.1 without the patch.

```bash
# Check whether the patch is applied
grep -q _PLATFORM_URL_TEMPLATES \
  "$SPOTIFLAC_VENV"/lib/python*/site-packages/SpotiFLAC/core/link_resolver.py \
  && echo "patched" || echo "MISSING — re-run install.sh"
```

If you re-installed spotiflac via `pip install --upgrade` after the initial
setup, you may have overwritten the patched file. Re-run `./install.sh` — it's
idempotent and re-applies the patch.

## "FLACs download but the audio is wrong"

Odesli's matching is fuzzy on title + artist + duration. For tracks with
multiple versions (live recordings, regional releases, remasters), it sometimes
matches the wrong one. Two defenses run automatically:

1. The provider chain leads with **Deezer**, which matches via ISRC (correct
   by construction). Tidal and Amazon — the fuzzy-matched providers — are
   fallbacks.
2. `verify-and-cleanup.py` (weekly cron) ffprobes every FLAC, compares to
   Spotify's reported duration, and purges any > 5 s mismatch.

If you're still getting misroutes for specific tracks, force a re-run with
just the no-Tidal/no-Amazon chain:

```bash
echo 'SPOTIFLAC_SERVICE="deezer youtube"' \
    > ~/.local/state/spotiflac-pipeline/service.conf
# Then unmark the affected playlist:
sed -i '/<playlist_id_to_retry>/d' ~/.local/state/spotiflac-pipeline/done.txt
bin/run_all.sh
```

## "The Spotify Web API returns 403 even with a valid OAuth token"

As of mid-2025, Spotify requires the *app owner* (the developer account that
created the API client) to hold a Premium subscription for any authenticated
call — even read-only ones like `GET /playlists/{id}`. This isn't a bug; the
free tier has been gated out of the API entirely.

Workarounds the pipeline already uses:

- `spotify-diff.py` scrapes the **public embed page** (no auth required),
  which serves the playlist's name + total + up to 100 track IDs. The
  pipeline marks playlists with >100 tracks as *incomplete* and falls back
  to count-only sync (additions only via count delta).
- The track-id index from old downloads remains accurate even without API
  access, so once a track is downloaded it stays linked to its M3Us forever.

If you have a >100-track playlist and need exact track-level diffs, the
options are:

1. Sign up for Spotify Premium (you only need it on the *app owner*
   account — your listeners don't need it).
2. Run a one-time export tool like [exportify.dev](https://exportify.dev/)
   and feed the JSON into the pipeline via `SPOTIFLAC_OLD_PLAYLISTS_JSON`.
3. Wait — Spotify has reversed this gate before; it may come back.

## "The watchdog isn't restarting the batch"

Common causes, in order of likelihood:

```bash
tail -50 ~/.local/state/spotiflac-pipeline/watchdog.log
```

| log line | meaning |
|---|---|
| `deferring: backup mountpoint … mounted` | Your `SPOTIFLAC_BACKUP_MOUNTPOINT` is mounted. Unmount or unset the var. |
| `deferring: rsync against output dir in progress` | Active rsync is touching your output. Wait for it. |
| `paused until <timestamp>, skipping` | A full provider rotation made no progress; sleeping 1 h. Force-resume: `rm ~/.local/state/spotiflac-pipeline/watchdog.state` |
| `batch running` repeated indefinitely | spotiflac is wedged. Find and kill: `pgrep -af spotiflac; kill <pid>` |
| (no recent lines at all) | Cron entry missing or removed. Check with `crontab -l`. |

## "Watchdog removed itself from cron and I want it back"

That's by design — once all playlists are complete, the watchdog
self-uninstalls. To resume (e.g. after adding new playlist URLs):

```bash
crontab -e
# Re-add the */15 watchdog line from examples/crontab.example

# Optionally clear done.txt entries for playlists you want to re-run
$EDITOR ~/.local/state/spotiflac-pipeline/done.txt
```

## "yt-dlp can't find a track that I can find manually in 5 seconds"

The first version of the redownloader used `ytsearch1:` and would pick the
top result blindly. The current version uses `ytsearch10:` and ranks
candidates by *duration delta to Spotify*. If a track is still failing:

```bash
# Run the redownloader interactively with --limit 1 --dry-run to see what
# candidates yt-dlp considered and why none matched
bin/redownload-spotdl.py --limit 1 --dry-run
```

The usual culprits:

- The Spotify track's actual length differs from what's on YouTube (live
  recording vs studio, censored version, etc.) — try widening
  `TOLERANCE_SEC` in `bin/redownload-spotdl.py`.
- The title has unusual punctuation that breaks the search query — try
  manually searching with `yt-dlp "ytsearch10:<your query>"` and adjusting.

## "I want a Discord notifier instead of Telegram"

Both `_common.sh:spf_notify` and `_common.py:notify` are single-call
abstractions. Swap their bodies to POST to a Discord webhook — open a PR
once you've done it, this should become a configurable backend.


---

## spotiflac ≥3.8: the extension era

Since 3.x, spotiflac downloads exclusively through JS **extensions** run by
Node.js, installed on demand from a registry. A whole class of new failure
modes comes with that:

### Every extension times out at startup ("did not respond within 30.0s")

Almost always **Node < 20**. The extension sandbox (`_fsguard.js`) wraps
`fs.openSync`, and on Node 18 the CommonJS module loader's own reads go
through that wrapper and get misclassified as writes — the extension process
dies before its ready handshake. Install Node ≥ 20 (a user-local tarball is
fine) and put its `bin/` in `SPOTIFLAC_EXTRA_PATH`.

### "file.downloadSegments is not a function" / "transformPatternedBlocks is not a function"

The registry's newest extensions target a newer app runtime than the PyPI
module ships. Pin older extension builds that only require the features your
bridge implements (check `grep -c downloadSegments …/SpotiFLAC/extensions/_bridge.js`):
download a compatible `.sflx` from the registry repo's git history, unzip it
over `~/.spotiflac/extensions/<name>/`, and **unset `SPOTIFLAC_REGISTRIES`**
afterwards — the startup bootstrap force-updates any version mismatch and
would undo your pin.

### "[Errno 2] No such file or directory: 'Xvfb'"

The deezer/tidal extensions solve a signed-session challenge by driving a
real browser. Install `xvfb` and a Chromium (`apt install xvfb
chromium-browser`); the solver also honors `CHROME_PATH`.

### HTTP 429 from the resolver API on every track

The signed-session bootstrap endpoint rate-limits bursts. It recovers on its
own; avoid concurrent multi-extension retries against it. Sessions are cached
under `~/.spotiflac/` after the first success.

### A whole playlist keeps re-downloading after every retry

You are on the pre-0.6 batch driver. `run_all.sh` now fetches per-track via
`fetch-missing.py` and only downloads IDs absent from `track-id-index.json` —
a playlist retry for 3 missing tracks costs 3 download attempts.
