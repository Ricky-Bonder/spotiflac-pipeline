#!/usr/bin/env python3
# Pass 1 (MIGRATE): move audio files from per-playlist subdirs (e.g.
#   <OUTPUT_DIR>/<PlaylistName>/<Artist>/<Album>/track.flac) into the flat
#   _library/<Artist>/<Album>/track.flac, deduplicating by destination path.
#
# Pass 2 (INDEX): maintain a persistent {spotify_id: rel_path} index at
#   $STATE_DIR/track-id-index.json. Newly-moved files get their URL tag
#   read via ffprobe. Stale entries (file gone) are pruned. First run does
#   a full library scan.
#
# Pass 3 (M3U REGEN): for every playlist in playlist-state.json, write
#   _playlists/<Name>.m3u8 as the intersection of (track IDs in this
#   playlist's state) and (IDs in the index). spotify-diff.py now stores
#   FULL track lists for every playlist (metadata-client enumeration), so
#   the old spotdl-export enrichment and MP3-fallback machinery are gone.
#
# Idempotent. Hook it after each successful spotiflac playlist completion
# (run_all.sh already does this). The first invocation may take ~1-2 min
# due to the initial full ffprobe scan; subsequent runs only scan new files.

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUTPUT_DIR, LIBRARY_DIR, PLAYLISTS_DIR, STATE_DIR

ROOT = OUTPUT_DIR
LIB = LIBRARY_DIR
PLAY = PLAYLISTS_DIR
STATE_FILE = STATE_DIR / "playlist-state.json"
INDEX_FILE = STATE_DIR / "track-id-index.json"
NOURL_CACHE = STATE_DIR / "no-url-cache.json"   # files known to carry no URL tag

AUDIO_EXTS = {".flac", ".m4a", ".mp3", ".ogg", ".opus", ".aac"}


def url_tag_id(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format_tags=URL,Url,url", "-of", "default=nokey=0:noprint_wrappers=1",
             str(path)],
            capture_output=True, text=True, timeout=10,
        )
        m = re.search(r"URL=(\S+)", r.stdout, re.I)
        if not m:
            return None
        m2 = re.search(r"/track/([A-Za-z0-9]+)", m.group(1))
        return m2.group(1) if m2 else None
    except Exception:
        return None


def migrate_files():
    """Move per-playlist subdir contents into _library. Returns list of (dest_path, was_new)."""
    LIB.mkdir(exist_ok=True)
    PLAY.mkdir(exist_ok=True)
    moved_new = []
    dup_removed = 0
    for playlist_dir in sorted(ROOT.iterdir()):
        if not playlist_dir.is_dir() or playlist_dir.name.startswith("_"):
            continue
        for src in list(playlist_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(playlist_dir)
            dest = LIB / rel
            if dest.exists():
                try:
                    src.unlink()
                    dup_removed += 1
                except OSError as e:
                    print(f"  ! could not remove dup {src}: {e}", file=sys.stderr)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                moved_new.append(dest)
        # cleanup empty dirs bottom-up
        for d in sorted(
            (p for p in playlist_dir.rglob("*") if p.is_dir()),
            key=lambda p: -len(p.parts),
        ):
            try: d.rmdir()
            except OSError: pass
        try: playlist_dir.rmdir()
        except OSError: pass

    # Sweep legacy top-level .m3u/.m3u8 files (those used to live here)
    for legacy in list(ROOT.glob("*.m3u")) + list(ROOT.glob("*.m3u8")):
        try: legacy.unlink()
        except OSError: pass

    return moved_new, dup_removed


def load_index():
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text())
        except Exception:
            pass
    return {}


def save_index(index):
    INDEX_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False))


# Rank for the ingest-time keeper rule. Mirrors dedup-tracks.py's
# FORMAT_RANK but keyed by suffix.
_SUFFIX_RANK = {".flac": 3, ".mp3": 2, ".m4a": 1, ".opus": 1, ".ogg": 1, ".aac": 1}


def pick_keeper(old_rel, new_rel):
    """Given two library-relative paths claiming the same Spotify track ID,
    return (keeper_rel, loser_rel) by format rank; ties keep the incumbent.

    This is the ingest-time self-heal: re-downloading a playlist can produce
    the same track in a different format (e.g. a YouTube-fallback M4A next
    to an existing verified FLAC). One track ID must map to exactly one
    file — the lower-ranked twin gets quarantined at ingest instead of
    accumulating.
    """
    old_rank = _SUFFIX_RANK.get(Path(old_rel).suffix.lower(), 0)
    new_rank = _SUFFIX_RANK.get(Path(new_rel).suffix.lower(), 0)
    if new_rank > old_rank:
        return new_rel, old_rel
    return old_rel, new_rel


def _quarantine(rel):
    """Move a library file into LIB/_dedup_quarantine/ (reversible), encoding
    the original path into the filename the same way dedup-tracks.py does."""
    src = LIB / rel
    if not src.exists():
        return
    qdir = LIB / "_dedup_quarantine"
    qdir.mkdir(exist_ok=True)
    dest = qdir / str(rel).replace("/", "⁄")
    try:
        shutil.move(str(src), str(dest))
        print(f"  quarantined twin: {rel}", file=sys.stderr)
    except OSError as e:
        print(f"  ! could not quarantine {rel}: {e}", file=sys.stderr)


def _index_file(index, f):
    """Index one audio file, resolving track-ID collisions via pick_keeper."""
    tid = url_tag_id(f)
    if not tid:
        return False
    new_rel = str(f.relative_to(LIB))
    old_rel = index.get(tid)
    if old_rel and old_rel != new_rel and (LIB / old_rel).exists():
        keeper, loser = pick_keeper(old_rel, new_rel)
        _quarantine(loser)
        index[tid] = keeper
    else:
        index[tid] = new_rel
    return True


def update_index(index, moved_new):
    """Reconcile the index against the library.

    1. Prune entries whose file is gone.
    2. Index every on-disk audio file whose path is not an index value —
       not just freshly-moved files. This matters when an index entry dies
       while its track survives under another path: e.g. the index pointed
       at an M4A twin that dedup quarantined, while the keeper FLAC (same
       track ID, indexed earlier, later overwritten) is still on disk.
       An increment that only looked at moved_new left such tracks
       unindexed forever, silently dropping them from every M3U.

    ffprobe runs only for unindexed files, so steady-state cost is one
    rglob + set lookups.
    """
    # Prune first so a keeper can reclaim its ID cleanly below.
    stale = [tid for tid, rel in index.items() if not (LIB / rel).exists()]
    for tid in stale:
        del index[tid]
    if stale:
        print(f"  pruned {len(stale)} dead entries", file=sys.stderr)

    try:
        nourl = json.loads(NOURL_CACHE.read_text()) if NOURL_CACHE.exists() else {}
    except Exception:
        nourl = {}
    known_paths = set(index.values())
    added = 0
    for f in sorted(LIB.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXTS:
            continue
        if any(part.endswith("_quarantine") for part in f.parts):
            continue
        rel = str(f.relative_to(LIB))
        if rel in known_paths:
            continue
        mt = f.stat().st_mtime
        if nourl.get(rel) == mt:
            continue  # known tagless import, unchanged since last look
        if _index_file(index, f):
            added += 1
        else:
            nourl[rel] = mt
    nourl = {r: t for r, t in nourl.items() if (LIB / r).exists()}
    NOURL_CACHE.write_text(json.dumps(nourl))
    if added:
        print(f"  index +{added} files (reconcile)", file=sys.stderr)


def regenerate_m3us(index):
    """One M3U per playlist: (state track_ids) ∩ (index), plus All Tracks."""
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    PLAY.mkdir(exist_ok=True)
    all_paths = set()
    resolved = 0
    for pid, p in state.items():
        name = p.get("name", "?")
        m3u_name = name.replace("/", "_")
        ordered, seen = [], set()
        for tid in p.get("track_ids", []):
            rel = index.get(tid)
            if rel:
                pth = f"../_library/{rel}"
                if pth not in seen:
                    seen.add(pth)
                    ordered.append(pth)
                    resolved += 1
        with open(PLAY / f"{m3u_name}.m3u8", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for pth in ordered:
                f.write(f"{pth}\n")
        all_paths.update(ordered)

    with open(PLAY / "All Tracks.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for pth in sorted(all_paths):
            f.write(f"{pth}\n")
    print(f"  resolved {resolved} playlist entries", file=sys.stderr)
    print(f"  regenerated {len(state)} M3Us, All Tracks={len(all_paths)} paths", file=sys.stderr)
    return len(state), len(all_paths)


def main():
    print(f"migrate v2 starting…", file=sys.stderr)
    moved_new, dup = migrate_files()
    print(f"  moved {len(moved_new)} files, dedup removed {dup}", file=sys.stderr)

    index = load_index()
    update_index(index, moved_new)
    save_index(index)

    regenerate_m3us(index)

    # Total audio files in library (sanity)
    total = sum(1 for f in LIB.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXTS)
    print(f"  _library now has {total} audio files; {len(index)} ID-indexed", file=sys.stderr)


if __name__ == "__main__":
    main()
