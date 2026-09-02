# Changelog

This project was developed iteratively over a real homelab music-library
migration (May 13 → June 6, 2026). The entries below describe milestones
that actually happened during that work; the published repo collapses them
into a clean initial release plus subsequent polish.

The dates are real. The code in this repo is the *result* of that journey —
not a per-day snapshot.

## [Unreleased]

- Discord notifier, web status page.
- `RELEASETYPE` (album/single/EP) tagging — Spotify's metadata path returns
  an empty `album_type`; needs a secondary source (Deezer?).
- Wire `tools/media-enrich.py lyrics` sources beyond LRCLib/Apple once any
  of the other provider proxies come back to life.


## [0.7.0] — 2026-09-02

Library maintenance toolkit, grown while consolidating three generations of
downloads (spotdl MP3s, Lidarr grabs, external yt-dlp syncs) into the one
`_library/<Artist>/<Album>/` structure.

### Added

- **`tools/enrich-tags.py`** — genre normalization to a single English
  taxonomy (with junk-genre re-derivation via artist-majority vote +
  Deezer-by-ISRC), fill-only completion of artist/album/year/track numbers/
  composer from Spotify metadata (cached in `track-meta-cache.json`).
- **`tools/media-enrich.py`** — three subcommands: `art` (replace covers on
  YouTube-sourced rips with the true Spotify album cover, fill missing art
  via Deezer/iTunes search, write per-album `cover.jpg`, everything
  downscaled to 300 px), `lyrics` (LRCLib, synced when available, embedded
  per-format), `country` (MusicBrainz artist origin as the Picard-style
  release-country tag — Navidrome/Symfonium expose it as a browse facet).
- **`tools/backfill-lyrics.py`** — second lyrics pass through spotiflac's own
  multi-provider fetcher with strict pacing and a miss-cache. Hard-won
  lesson encoded in the defaults: the iTunes search API rate-limits around
  20 req/min/IP and 403-blocks offenders for hours.
- **`tools/canonicalize.py`** — make tags AND folder placement match Spotify
  canonical metadata (album-artist's first artist as the folder), updating
  the track index so playlist M3Us survive the moves.
- **`tools/library-import.py`** — fold external download folders (yt-dlp
  SoundCloud syncs, an old Lidarr root) into `_library/`, plus a
  fill-missing-tags-from-path sweep.

### Fixed

- `migrate-to-flat.py` no longer re-ffprobes tagless imports on every run
  (`no-url-cache.json`) — steady-state migrate dropped from ~60 s to ~1.5 s
  on a 4 600-file library.

## [0.6.0] — 2026-09-01

The spotiflac 3.8 migration. Upstream moved from built-in providers (0.5.x)
to a JS-extension runtime — and this release rebuilds the pipeline's
download path around it, fixing the two long-standing efficiency sinks in
the same stroke.

### Added

- **`fetch-missing.py` + missing-only `run_all.sh`.** The batch driver now
  enumerates each playlist's full track list through spotiflac's metadata
  client and downloads only IDs absent from `track-id-index.json`. A
  +5-tracks playlist update costs 5 downloads instead of a full re-run.
- **Permanent-failure quarantine.** Tracks that fail
  `SPOTIFLAC_MAX_TRACK_FAILS` separate batch attempts land in
  `unavailable.txt` and stop blocking their playlist from completing —
  ending the retry-forever loop for tracks no provider can deliver.
  Delete a line to retry; a later success clears the entry automatically.
- **Full-list `spotify-diff.py`.** Playlist diffing now uses the metadata
  client too (embed scrape kept as fallback): the 100-track embed cap is
  gone, so deletions are finally detected on big playlists.
- `SPOTIFLAC_EXTRA_PATH` config key (user-local Node ≥ 20 for the extension
  runtime) and extension-era docs: registry setup, version pinning against
  extension/runtime skew, the Xvfb/Chromium session solver.
- Experimental Docker runtime under `docker/` — Python + Node 22 + Xvfb +
  Chromium + ffmpeg behind a scheduler entrypoint mirroring the crontab.

### Changed

- spotiflac pin **`>=3.8,<4`**. Provider chains default to
  `deezer tidal,tidal deezer,deezer,tidal` — the amazon/ytmusic extensions'
  search endpoints are dead as of late 2026.
- `migrate-to-flat.py` M3U generation is now simply
  `playlist-state ∩ track-id-index` — with full track lists in state, the
  old spotdl-export enrichment and MP3-fallback machinery became dead code.

### Removed

- The spotdl-era side pipeline (`audit-spotdl.py`, `redownload-spotdl.py`,
  `dedup-tracks.py`) and its config keys. It served its one-time purpose:
  the legacy MP3 collection has been merged into `_library/` and the
  missing-only driver + verifier cover everything it did.
- `patches/` — 3.8's resolver made the 0.5.x Odesli `?url=` patch obsolete.

## [0.5.1] — 2026-07-06

### Fixed

- **`migrate-to-flat.py` index increment silently dropped tracks.** Found
  minutes after deploying 0.5.0's cleanup: the index entry for most twins
  pointed at the M4A (indexed later, overwriting the FLAC's entry). When
  dedup quarantined those M4As, `update_index()` pruned the dead entries —
  but its incremental pass only ever indexed freshly-*moved* files, so the
  surviving FLAC keepers were never re-indexed. On the author's library the
  index collapsed from 3,872 to 319 entries and 1,222 tracks vanished from
  the M3Us. `update_index()` is now a true disk↔index reconcile: prune
  dead entries, then index every on-disk file whose path is not an index
  value (collisions resolved via the keeper rule). ffprobe still runs only
  for unindexed files, so steady-state cost is one rglob + set lookups.

### Tests

- `tests/test_migrate_reconcile.py` (5 cases) reproduces the incident with
  a monkeypatched library: orphaned-keeper re-index, twin quarantine at
  reconcile, quarantine-dir skip, dead-entry pruning, untagged files.
  Suite now 45 tests.

## [0.5.0] — 2026-07-06

### Fixed

- **Cross-format duplicate blindness in `dedup-tracks.py`.** The Spotify-ID
  assignment for library files (index reverse-lookup + embedded `TAG:URL`
  fallback) was gated on `ext == "flac"`. M4A and MP3 files in `_library/`
  carry the identical `TAG:URL` written by spotiflac, but never got their
  ID resolved — so a YouTube-fallback M4A twin of an existing FLAC landed
  in the fuzzy-matching pool while its FLAC sat in the by-ID cluster, and
  the two were never compared. On the author's library this had silently
  accumulated **583 side-by-side FLAC + M4A pairs** (same directory, same
  stem, same track ID, ~5 GB of redundant audio) that survived every dedup
  pass. The gate is gone: every audio file under `_library/` resolves its
  ID the same way.

- **Duplicate accumulation at ingest in `migrate-to-flat.py`.** Root cause
  of the twins: re-downloading a playlist (e.g. after the verifier unmarks
  it) re-fetches every track, and the batch may land a different format
  than the prior run (provider chains fall through to YouTube when the
  FLAC providers are down). The file-move pass keys on the full destination
  path — same stem, different extension → no collision → both kept. New
  ingest-time self-heal: when a newly indexed file's track ID already maps
  to a different existing file, `pick_keeper()` applies the format-rank
  rule (FLAC > MP3 > M4A, ties keep the incumbent) and the loser moves to
  `_library/_dedup_quarantine/` immediately. One track ID ↔ one file, by
  construction.

- `migrate-to-flat.py`'s initial full-library scan now skips
  `*_quarantine/` directories (it could previously resurrect quarantined
  files into the index on a fresh-index rebuild).

### Tests

- `tests/test_migrate_ingest.py` (6 cases) pins `pick_keeper()`'s contract,
  including a cross-module check that migrate's suffix ranks agree with
  dedup's `FORMAT_RANK` — two disagreeing keeper rules would fight each
  other across runs. Suite now 40 tests.

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
