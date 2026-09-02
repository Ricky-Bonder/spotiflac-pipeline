#!/usr/bin/env python3
"""canonicalize.py [--apply]   (2026-09-02)

Make artist/album tags AND folder placement of every Spotify-indexed file
match Spotify's canonical metadata (track-meta-cache.json):
  - tags: artist, albumartist, album  (overwritten only when loosely different)
  - path: _library/<AlbumArtist>/<Album>/<existing filename>
  - track-id-index.json updated for moved files
Playlist membership is untouched: M3Us regenerate from playlist-state ∩ index,
so tracks stay in their Spotify playlists regardless of folder/tag changes.
Unindexed files (SoundCloud bootlegs, kept Lidarr artists) are not touched.
Dry-run by default. Log: ~/spotiflac/canonicalize-log-<ts>.json
"""
import json
import re
import shutil
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

import mutagen

import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "bin"))
from _common import LIBRARY_DIR as _LIB, STATE_DIR as _STATE, MUSIC_ROOT as _MUSIC, PLAYLISTS_DIR as _PLAY, VENV as _VENV  # noqa: E402

LIB = _LIB
SPOT = _STATE
APPLY = "--apply" in sys.argv


def loose(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def san(s):
    return re.sub(r"\s+", " ", (s or "").replace("/", "_").replace("\0", "")).strip().rstrip(".") or "Unknown"


def get(m, key):
    try:
        v = m.tags.get(key) if m and m.tags else None
        return str(v[0]).strip() if v else ""
    except Exception:
        return ""


def main():
    idx = json.load(open(SPOT / "track-id-index.json"))
    meta = json.loads((SPOT / "track-meta-cache.json").read_text())
    stats = Counter()
    log = {}
    new_index = dict(idx)

    for tid, rel in sorted(idx.items()):
        f = LIB / rel
        sp = meta.get(tid)
        if not sp or not f.exists():
            stats["no_meta_or_file"] += 1
            continue
        want_artist = sp.get("artists") or ""
        want_aartist = sp.get("album_artist") or (want_artist.split(",")[0].strip() if want_artist else "")
        want_album = sp.get("album") or ""
        if not want_artist or not want_album:
            stats["meta_incomplete"] += 1
            continue

        m = mutagen.File(f, easy=True)
        if m is None or m.tags is None:
            stats["unreadable"] += 1
            continue
        cur_artist, cur_aartist, cur_album = get(m, "artist"), get(m, "albumartist"), get(m, "album")

        tag_ch = {}
        if loose(cur_artist) != loose(want_artist):
            tag_ch["artist"] = want_artist
        if loose(cur_aartist) != loose(want_aartist):
            tag_ch["albumartist"] = want_aartist
        if loose(cur_album) != loose(want_album):
            tag_ch["album"] = want_album

        # canonical folder: first artist of the album artist (library convention),
        # full joined strings stay in the tags only
        dir_artist = want_aartist.split(",")[0].strip()
        want_dir = f"{san(dir_artist)}/{san(want_album)}"
        cur_dir = "/".join(Path(rel).parts[:-1])
        move_to = None
        if loose(cur_dir.replace("/", " ")) != loose(want_dir.replace("/", " ")):
            new_rel = f"{want_dir}/{f.name}"
            dest = LIB / new_rel
            if dest.exists() and dest != f:
                stats["move_collision"] += 1
            else:
                move_to = new_rel

        if not tag_ch and not move_to:
            stats["ok"] += 1
            continue
        stats["files_changed"] += 1
        for k in tag_ch:
            stats[f"tag_{k}"] += 1
        if move_to:
            stats["moved"] += 1
        log[rel] = {"tags": tag_ch, "moved_to": move_to,
                    "was": {"artist": cur_artist, "albumartist": cur_aartist, "album": cur_album}}
        if APPLY:
            try:
                for k, v in tag_ch.items():
                    m.tags[k] = v
                if tag_ch:
                    m.save()
                if move_to:
                    dest = LIB / move_to
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f), str(dest))
                    new_index[tid] = move_to
            except Exception as e:
                stats["errors"] += 1
                print(f"  ! {rel}: {e}", file=sys.stderr)

    if APPLY:
        json.dump(new_index, open(SPOT / "track-id-index.json", "w"), indent=0)
        # prune empty dirs
        for d in sorted((p for p in LIB.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
            try:
                d.rmdir()
            except OSError:
                pass
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json.dump(log, open(SPOT / f"canonicalize-log-{stamp}.json", "w"), indent=1)
    print(f"SUMMARY canonicalize (applied={APPLY}):")
    for k, v in sorted(stats.items()):
        print(f"  {k:18s} {v}")
    ex = [(k, v) for k, v in list(log.items())[:6]]
    for k, v in ex:
        print(f"  ex: {k}\n      -> {v['moved_to'] or '(tags only)'} {v['tags']}")


main()
