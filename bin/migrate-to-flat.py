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
#   playlist's state) and (IDs in the index). For >100-track playlists
#   that the embed scrape didn't fully cover, enrich the ID set from an
#   optional old spotdl playlists.json export (matched by name; entries in
#   SPOTIFLAC_LIKES_ALIASES are merged together as one "Likes" proxy).
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
from _common import (
    OUTPUT_DIR, LIBRARY_DIR, PLAYLISTS_DIR, STATE_DIR, SPOTDL_ROOT,
    OLD_PLAYLISTS_JSON, LIKES_ALIASES,
)

ROOT = OUTPUT_DIR
LIB = LIBRARY_DIR
PLAY = PLAYLISTS_DIR
STATE_FILE = STATE_DIR / "playlist-state.json"
INDEX_FILE = STATE_DIR / "track-id-index.json"
AUDIT_FILE = STATE_DIR / "spotdl-audit.json"
OLD_JSON = Path(OLD_PLAYLISTS_JSON) if OLD_PLAYLISTS_JSON else None

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


def update_index(index, moved_new):
    """Add new files to index. Also prune entries pointing to dead paths. First
    run (empty index) does a full library scan."""
    if not index:
        # Initial scan
        print("Initial library scan (first run)…", file=sys.stderr)
        for f in LIB.rglob("*"):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                tid = url_tag_id(f)
                if tid:
                    index[tid] = str(f.relative_to(LIB))
        print(f"  indexed {len(index)} files", file=sys.stderr)
        return

    # Incremental: add new files
    added = 0
    for f in moved_new:
        if f.suffix.lower() not in AUDIO_EXTS:
            continue
        tid = url_tag_id(f)
        if tid:
            index[tid] = str(f.relative_to(LIB))
            added += 1
    if added:
        print(f"  index +{added} new files", file=sys.stderr)

    # Prune stale entries
    stale = [tid for tid, rel in index.items() if not (LIB / rel).exists()]
    for tid in stale:
        del index[tid]
    if stale:
        print(f"  pruned {len(stale)} dead entries", file=sys.stderr)


def build_mp3_index():
    """Build {spotify_id: abs_mp3_path} from the spotdl audit's 'good' bucket,
    filtering to files that still exist (not quarantined)."""
    if not AUDIT_FILE.exists():
        return {}
    audit = json.loads(AUDIT_FILE.read_text())
    music_root = SPOTDL_ROOT
    out = {}
    for x in audit["buckets"].get("good", []):
        sid = x.get("spotify_id")
        if not sid:
            continue
        p = music_root / x["path"]
        # Skip any *_quarantine/ subdir (dedup or redownload-evidence files
        # shouldn't be referenced by playlist M3Us)
        if any(part.endswith("_quarantine") for part in p.parts):
            continue
        if p.exists():
            out[sid] = p
    return out


def regenerate_m3us(index):
    """Write one M3U per playlist in state, plus All Tracks union.
    Each track is resolved: FLAC (from `index`) preferred, fallback to verified-good MP3."""
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    mp3_index = build_mp3_index()
    if mp3_index:
        print(f"  mp3 fallback index: {len(mp3_index)} verified-good MP3s", file=sys.stderr)

    # Optional old playlists.json: enrichment for incomplete (>100-track) playlists
    old_name_to_ids = {}
    if OLD_JSON and OLD_JSON.exists():
        try:
            old = json.loads(OLD_JSON.read_text())
            for pl in old.get("playlists", []):
                key = "LIKES" if pl["name"] in LIKES_ALIASES else pl["name"]
                ids = [
                    (t.get("track") or {}).get("id")
                    for t in pl.get("tracks", [])
                ]
                old_name_to_ids.setdefault(key, set()).update(i for i in ids if i)
        except Exception as e:
            print(f"  ! could not parse old json: {e}", file=sys.stderr)

    PLAY.mkdir(exist_ok=True)
    all_paths = set()
    mp3_used = 0
    flac_used = 0
    for pid, p in state.items():
        name = p.get("name", "?")
        m3u_name = name.replace("/", "_")
        ids = set(p.get("track_ids", []))
        # Enrich incomplete playlists from old data
        if not p.get("complete"):
            key = "LIKES" if name in LIKES_ALIASES else name
            ids |= old_name_to_ids.get(key, set())
        # Map to library paths: prefer FLAC, fallback to verified MP3
        paths = []
        for tid in ids:
            rel = index.get(tid)
            if rel:
                paths.append(("flac", f"../_library/{rel}"))
                flac_used += 1
            else:
                mp3_abs = mp3_index.get(tid)
                if mp3_abs:
                    # M3U is at PLAY/<name>.m3u8; compute relative path to MP3.
                    rel_path = os.path.relpath(mp3_abs, PLAY)
                    paths.append(("mp3", rel_path))
                    mp3_used += 1
        # Write M3U (dedup paths; preserve order: FLAC entries first, then MP3)
        seen = set()
        ordered = []
        for kind in ("flac", "mp3"):
            for k, pth in paths:
                if k == kind and pth not in seen:
                    seen.add(pth)
                    ordered.append(pth)
        m3u = PLAY / f"{m3u_name}.m3u8"
        with open(m3u, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for rel in ordered:
                f.write(f"{rel}\n")
        all_paths.update(ordered)

    # "All Tracks" union — only paths actually referenced by some playlist M3U.
    all_m3u = PLAY / "All Tracks.m3u8"
    with open(all_m3u, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for rel in sorted(all_paths):
            f.write(f"{rel}\n")
    print(f"  resolved tracks: {flac_used} via FLAC + {mp3_used} via MP3 fallback", file=sys.stderr)

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
