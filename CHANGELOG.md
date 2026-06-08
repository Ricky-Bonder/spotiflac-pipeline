# Changelog

This project was developed iteratively over a real homelab music-library
migration (May 13 → June 6, 2026). The entries below describe milestones
that actually happened during that work; the published repo collapses them
into a clean initial release plus subsequent polish.

The dates are real. The code in this repo is the *result* of that journey —
not a per-day snapshot.

## [Unreleased]

— Ongoing polish: tests, Docker example, Discord notifier, web status page.

## [0.1.0] — 2026-06-08

First public release. Bundles the full pipeline as developed against the
author's homelab. Generalizes hard-coded paths into a single config file,
strips personal identifiers, documents the Spotify Premium gate, and
packages the spotiflac `link_resolver.py` patch as a standalone diff.

### Development history

#### 2026-05-13 — broken-batch diagnosis
Inherited a batch driver that crashed silently after three playlists. Root
causes: `failed_count=$(grep -c . || echo 0)` produced literal `"0\n0"`
and broke arithmetic; `ls $OUTPUT_DIR | grep -q $id` could never match a
Spotify ID against an artist-named folder, so the "skip already done"
guard never fired. Replaced both with explicit per-playlist tracking in
`done.txt`.

#### 2026-05-15 — first verifier pass
Built `verify-and-cleanup.py`: walk every FLAC, ffprobe its duration,
scrape the Spotify embed page for the real track duration, flag any
mismatch greater than 5 s. First run on the existing library found 419/658
FLACs misrouted by Odesli's Tidal/Amazon resolver, deleted them all, and
re-enabled the affected playlists for retry.

#### 2026-05-16 — spotiflac 0.5.1 + Odesli patch
Upstream spotiflac 0.5.0 → 0.5.1 carried an unrelated rename of the
session-summary box from Italian to English (`Tracce totali` →
`Total Tracks`); the batch driver's grep patterns were updated to match
either. The same release continued to call Odesli with the deprecated
`?id=&platform=` form, which returns `HTTP 400 invalid_entity_type`.
Patched `link_resolver.py` to build a canonical track URL per platform
and submit it via `?url=` instead — see `patches/spotiflac-0.5.1-
link-resolver.patch`.

#### 2026-05-17–05-25 — watchdog + provider rotation
Authored `spotiflac-watchdog.sh`: a 15 min cron that keeps the batch
alive, monitors disk / memory / OOM events, rotates provider chains when
no playlist progress is observed across N watchdog ticks, defers when an
external backup is running, and removes itself from crontab once every
playlist is done. Refactored the chains into `SPOTIFLAC_PROVIDER_CHAINS`
to be config-driven.

#### 2026-06-02 — migrate v2 with persistent ID index
The first migrate pass would wipe M3Us on retry because it rebuilt them
from scratch each time. Rewrote to v2 with a persistent
`track-id-index.json` keyed on Spotify track ID, scanned via ffprobe's
`TAG:URL` field. ~497 previously orphaned FLACs were re-linked into
their playlists by this run.

#### 2026-06-03 — Deezer-first chain + spotdl audit pipeline
Library re-verify (with a fix to the `--clean` mode that had been
acting on an 18-day-stale report) removed 1113 misrouted FLACs in one
sweep — 64 % of FLACs sourced from Tidal/Amazon were wrong. Reordered
every provider chain to lead with Deezer (which maps Spotify→ISRC→
Deezer, correct by construction). For the existing spotdl MP3 collection,
built `audit-spotdl.py` (filename vs. spotdl JSON, fuzzy artist + title,
duration ± 5 s): 1391 of 4886 MP3s were similarly misrouted.

#### 2026-06-03–06-04 — dedup + redownload pipeline
`dedup-tracks.py`: cross-source dedup (spotiflac FLAC, spotdl MP3,
third-party). Same-track grouping by spotify_id or fuzzy + duration ± 3 s.
Keeper rule: verified-good > FLAC > MP3 > M4A > bitrate > size. Losers
moved to a `_dedup_quarantine/` directory (reversible). 1287 redundant
files quarantined, ~15 GB freed. `redownload-spotdl.py`: re-download
spotdl-flagged BAD tracks via yt-dlp. First version used `ytsearch1:`
and missed obvious candidates that a human could find in seconds. Rewrote
to a two-phase approach: metadata-only `ytsearch10:` with a duration
filter, then pick the candidate whose duration is *closest* to Spotify's
(not just within tolerance). Success rate rose from 32 % to 89 %.

#### 2026-06-04–06-06 — convergence
Unified the M3U format so each playlist references FLAC entries first
(from `_library/`) and falls back to verified-good MP3s for tracks the
FLAC pipeline couldn't resolve. Folded the 186 newly redownloaded MP3s
into the unified M3Us. Watchdog completed all 43 playlists on 2026-06-06
and self-removed from cron — the steady-state daily diff + weekly verify
jobs remain.

#### 2026-06-08 — public release
Sanitized paths into a single env-file config. Stripped personal
identifiers (Telegram tokens, homelab-specific paths, the `~/backup/
backup.conf` reference, the private `playlists.json` export). Documented
the Spotify Premium gate (the OAuth path can't work for non-Premium app
owners as of 2025) and the embed-scrape 100-track cap. Bundled the
spotiflac patch as a proper unified diff. Published the result.

[0.1.0]: https://github.com/Ricky-Bonder/spotiflac-pipeline/releases/tag/v0.1.0
