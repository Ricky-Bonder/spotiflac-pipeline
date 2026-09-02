#!/usr/bin/env python3
"""backfill-lyrics.py [--apply]   (2026-09-02)

Second lyrics pass over files that still have none, using SpotiFLAC's own
multi-provider fetcher (spotify/musixmatch/apple/genius/deezer/amazon/netease
— far wider coverage than the LRCLib-only first pass). Uses each file's
Spotify track id + ISRC from ~/spotiflac/track-meta-cache.json for precise
matches. Misses are recorded in ~/spotiflac/lyrics-miss-cache.json and
skipped on later runs (delete an entry to retry). Embeds like the first
pass: FLAC LYRICS, MP3 USLT, M4A ©lyr. Run with the venv python.
"""
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

import logging
import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, USLT, ID3NoHeaderError
from mutagen.mp4 import MP4

from SpotiFLAC.core.lyrics import fetch_lyrics_async

import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "bin"))
from _common import LIBRARY_DIR as _LIB, STATE_DIR as _STATE, MUSIC_ROOT as _MUSIC, PLAYLISTS_DIR as _PLAY, VENV as _VENV  # noqa: E402

LIB = _LIB
SPOT = _STATE
MISS_FILE = SPOT / "lyrics-miss-cache.json"
AUDIO = {".mp3", ".m4a", ".flac"}
APPLY = "--apply" in sys.argv
import os
PROVIDERS = os.environ.get("LYR_PROVIDERS", "apple").split(",")   # only apple+lrclib actually work (2026-09); lrclib exhausted in pass 1
CHUNK = int(os.environ.get("LYR_CHUNK", "400"))          # extra cool-down every N lookups
CHUNK_SLEEP = int(os.environ.get("LYR_CHUNK_SLEEP", "600"))
PACE_S = float(os.environ.get("LYR_PACE_S", "5.0"))   # iTunes search: ~20 req/min/IP; stay well under
if "--debug" in sys.argv:
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("SpotiFLAC.core.lyrics").setLevel(logging.DEBUG)
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def has_lyrics(f):
    try:
        s = f.suffix.lower()
        if s == ".flac":
            fl = FLAC(f)
            return any(k.lower() in ("lyrics", "unsyncedlyrics") and fl[k] for k in fl.keys())
        if s == ".mp3":
            id3 = ID3(f)
            return bool(id3.getall("USLT")) or bool(id3.getall("SYLT"))
        if s == ".m4a":
            mp = MP4(f)
            return bool(mp.tags and mp.tags.get("\xa9lyr"))
    except Exception:
        return True
    return True


def write_lyrics(f, text):
    s = f.suffix.lower()
    if s == ".flac":
        fl = FLAC(f)
        fl["LYRICS"] = text
        fl.save()
    elif s == ".mp3":
        try:
            id3 = ID3(f)
        except ID3NoHeaderError:
            id3 = ID3()
        id3.setall("USLT", [USLT(encoding=3, lang="und", desc="", text=text)])
        id3.save(f)
    elif s == ".m4a":
        mp = MP4(f)
        if mp.tags is None:
            mp.add_tags()
        mp.tags["\xa9lyr"] = [text]
        mp.save()


def get(m, key):
    try:
        v = m.tags.get(key) if m and m.tags else None
        return str(v[0]).strip() if v else ""
    except Exception:
        return ""


async def main():
    rev = {v: k for k, v in json.load(open(SPOT / "track-id-index.json")).items()}
    meta = json.loads((SPOT / "track-meta-cache.json").read_text()) if (SPOT / "track-meta-cache.json").exists() else {}
    miss = json.loads(MISS_FILE.read_text()) if MISS_FILE.exists() else {}
    stats = Counter()
    log = {}

    targets = []
    for f in sorted(LIB.rglob("*")):
        if f.suffix.lower() not in AUDIO or not f.is_file():
            continue
        if any(p.endswith("_quarantine") for p in f.parts):
            continue
        stats["files"] += 1
        rel = str(f.relative_to(LIB))
        if "--only" in sys.argv:
            sub = sys.argv[sys.argv.index("--only") + 1]
            if sub.lower() not in rel.lower():
                continue
        if rel in miss:
            stats["skipped_known_miss"] += 1
            continue
        if has_lyrics(f):
            stats["already"] += 1
            continue
        targets.append((f, rel))
    print(f"targets without lyrics: {len(targets)}", flush=True)

    sem = asyncio.Semaphore(1)   # strictly sequential: provider APIs rate-limit hard
    done = 0

    async def handle(f, rel):
        nonlocal done
        async with sem:
            m = mutagen.File(f, easy=True)
            artist, title, album = get(m, "artist"), get(m, "title"), get(m, "album")
            dur = int(getattr(getattr(m, "info", None), "length", 0) or 0)
            if not artist or not title:
                stats["no_tags"] += 1
                return
            tid = rev.get(rel, "")
            isrc = (meta.get(tid) or {}).get("isrc", "") if tid else ""
            await asyncio.sleep(PACE_S)
            try:
                text, provider = await fetch_lyrics_async(
                    title, artist, album, dur, track_id=tid, isrc=isrc,
                    providers=PROVIDERS)
            except Exception as e:
                stats["fetch_errors"] += 1
                print(f"  ! fetch {rel}: {e}", file=sys.stderr)
                return
            done += 1
            if done % CHUNK == 0:
                print(f"  chunk pause {CHUNK_SLEEP}s at {done}", flush=True)
                MISS_FILE.write_text(json.dumps(miss))
                await asyncio.sleep(CHUNK_SLEEP)
            if done % 100 == 0:
                print(f"  …{done}/{len(targets)} found={stats['found']}", flush=True)
                MISS_FILE.write_text(json.dumps(miss))
            if not text or not text.strip():
                stats["not_found"] += 1
                miss[rel] = time.strftime("%Y-%m-%d")
                return
            stats["found"] += 1
            stats[f"via_{provider}"] += 1
            log[rel] = provider
            if APPLY:
                try:
                    write_lyrics(f, text)
                    stats["written"] += 1
                except Exception as e:
                    stats["write_errors"] += 1
                    print(f"  ! write {rel}: {e}", file=sys.stderr)

    await asyncio.gather(*(handle(f, rel) for f, rel in targets))
    MISS_FILE.write_text(json.dumps(miss))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json.dump(log, open(SPOT / f"lyrics2-log-{stamp}.json", "w"), indent=0)
    print(f"\nSUMMARY lyrics-backfill (applied={APPLY}):")
    for k, v in sorted(stats.items()):
        print(f"  {k:22s} {v}")


asyncio.run(main())
