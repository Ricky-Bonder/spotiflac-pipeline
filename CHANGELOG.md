# Changelog

This project was developed iteratively over a real homelab music-library
migration (May 13 → June 6, 2026). The entries below describe milestones
that actually happened during that work; the published repo collapses them
into a clean initial release plus subsequent polish.

The dates are real. The code in this repo is the *result* of that journey —
not a per-day snapshot.

## [Unreleased]

- Docker example, Discord notifier, web status page.
- Re-validate spotiflac 0.9.x to consider bumping the upper pin to `<1.0`.
- Make `spotify-diff.py` re-install the watchdog cron when it unmarks a
  playlist — currently a steady-state gap (diff detects additions and
  unmarks, but nothing automatically picks the work back up).

## [0.4.0] — 2026-06-19

### Added

- **`verify-and-cleanup.py` now covers M4A files** in addition to FLAC.
  Until now the duration verifier only checked the FLAC outputs from the
  Deezer/Tidal/Amazon providers — M4As produced by the YouTube fallback
  chain were never duration-checked against Spotify, even though they're
  served from a different recording-match path entirely (yt-dlp closest-
  duration heuristic vs Odesli ISRC mapping). A parallel audit of a
  100-track random sample revealed a ~9% misroute rate in the M4A
  population — extrapolated to the author's library, ~355 wrong tracks
  silently passed every prior sweep. Widening the scan glob to
  `*.flac` + `*.m4a` (one constant, one for-loop change) puts them under
  the same Sunday-04:00 sweep as the FLACs.

### Fixed

- **M3U-rebuild corruption in `verify-and-cleanup.py --clean`.** The
  previous load/rebuild round-trip naively stripped a `../_library/`
  prefix from every playlist line, then re-added it during the rebuild.
  This worked for FLAC/M4A entries that genuinely lived under
  `_library/`, but turned MP3-fallback lines (e.g.
  `../../liked/Foo.mp3`) into broken `../_library/../../liked/Foo.mp3`
  paths on disk. In practice the bug was masked by `migrate-to-flat.py`
  running shortly after every `--clean` and regenerating the M3Us from
  scratch — but in steady state (watchdog gone) the corruption could
  linger. New `m3u_line_under_library()` helper preserves non-library
  lines verbatim through the rebuild.

### Refactored

- `verify-and-cleanup.py`: renamed internal `flacs` → `audio_files`,
  `flac_dur` → `file_dur` in bad-record dicts. The report JSON gets the
  new key — any tooling consuming the old `flac_dur` key needs to update.

### Tests

- `tests/test_verify_m3u.py` (7 cases) pins `m3u_line_under_library()`'s
  contract — FLAC and M4A lines under `_library/` produce a rel path,
  MP3 fallback lines produce `None`. Total suite now 34 tests.

## [0.3.0] — 2026-06-19

### Added

- **CI on every push + PR**. `.github/workflows/ci.yml` runs `bash -n` on
  every shell script, `python3 -m py_compile` on every Python script, and
  the full pytest suite under Python 3.10 + 3.12 in matrix.
- **`tests/`** with 27 pytest cases:
  - `test_audit_match.py` — `norm()` edge cases, spotdl filename parsing,
    and the artist-anywhere + 0.80-fuzzy-title rule
  - `test_dedup_priority.py` — pins the keeper-selection ordering
    (verified-good > FLAC > MP3 > M4A > bitrate > size) against
    refactor regressions
- README CI badge.

### Changed

- `dedup-tracks.py`: extracted `keeper_sort_key` from inside `main()` to
  module level so it's unit-testable. Pure refactor, no behavior change.
- `docs/DEVELOPMENT.md`: rewrote the *Tests* section now that there are
  actually tests.

## [0.2.0] — 2026-06-08

### Changed

- **Default spotiflac pin moved to `>=0.6.9,<0.9`** (was `>=0.5.1,<0.6`).
  0.6.9 is the first upstream release with the rewritten `link_resolver.py`
  that doesn't need our patch. Validated end-to-end against 0.8.4 on a real
  Spotify track.
- `install.sh` simplified: dropped the patch-application step and the `patch`
  host-dep check. One fewer thing to go wrong, ~25 fewer lines of bash.

### Documented

- `patches/README.md`: marked as legacy / historical. Includes manual-apply
  instructions for anyone deliberately pinning to the 0.5.x line.
- Findings on intermediate upstream versions: 0.5.x has the Odesli `?id=&
  platform=` bug; 0.6.0 still broken; 0.6.9+ fixed; 0.8.9+ restructured to
  `backend.launcher`; **1.0.0's PyPI wheel is broken** (empty
  `top_level.txt`, missing `backend/` module — published the same day this
  validation ran). Reported upstream nowhere yet — feels like a slipped
  packaging step that the author will likely fix soon.

### Why now

The original CHANGELOG flagged this as *Unreleased* validation work. The
upstream landscape is moving fast (35+ releases between 0.5.1 and 1.0.0
across ~3 weeks), and a one-line `install.sh` pin bump is materially
simpler than maintaining our own patch indefinitely.

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

> Upstream has since released spotiflac 1.0.0 with a completely rewritten
> resolver — this patch is therefore only relevant for the 0.5.x line that
> this project is currently pinned to. See `patches/README.md` and the
> *Unreleased* section above.

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
