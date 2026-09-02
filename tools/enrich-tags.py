#!/usr/bin/env python3
"""enrich-tags.py [--apply]   (2026-09-01 metadata enrichment)

1. GENRE NORMALIZATION: map the historical mix of Deezer-Italian/English/junk
   genre strings to one detailed English taxonomy (~40 genres).
2. JUNK/MISSING GENRES: re-derive via (a) majority vote among the same
   artist's other tracks, (b) Deezer public API by ISRC (indexed files),
   else leave untouched.
3. FILL MISSING TAGS (never overwrites): artist/title/album/albumartist/
   date/tracknumber/discnumber/composer from Spotify metadata (indexed
   files, cached in ~/spotiflac/track-meta-cache.json) or from the file's
   _library path (unindexed). albumartist defaults to artist.
4. RELEASETYPE (FLAC only, fill-only): album/single/compilation from
   Spotify, for Symfonium/OpenSubsonic "album type" filtering.

Dry-run by default; --apply writes tags in place with mutagen and logs
every change to ~/spotiflac/enrich-log-<ts>.json.
"""
import asyncio
import json
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import mutagen

import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "bin"))
from _common import LIBRARY_DIR as _LIB, STATE_DIR as _STATE, MUSIC_ROOT as _MUSIC, PLAYLISTS_DIR as _PLAY, VENV as _VENV  # noqa: E402

LIB = _LIB
SPOT = _STATE
CACHE_FILE = SPOT / "track-meta-cache.json"
APPLY = "--apply" in sys.argv
AUDIO = {".mp3", ".m4a", ".flac", ".opus", ".ogg"}

# ---------------------------------------------------------------- taxonomy
# NOTE: this mapping was built from one real library whose historical tags
# mixed Deezer-Italian labels, YouTube category junk and English genres.
# Review/extend it for your own collection before running with --apply.
GENRE_MAP = {
    "Rock": "Rock", "Classic Rock": "Classic Rock",
    "Hard rock": "Hard Rock", "Hard Rock": "Hard Rock",
    "Progressive Rock": "Progressive Rock", "Rock progressivo": "Progressive Rock",
    "Prog-Rock/Art Rock": "Progressive Rock",
    "Alternative": "Alternative", "Post-Grunge": "Post-Grunge",
    "Musica alternativa e indie": "Indie",
    "Pop/Rock": "Pop Rock", "Pop, Rock, Alternative & Indie": "Pop Rock",
    "New Wave": "New Wave",
    "Blues": "Blues", "Rock Blues": "Blues Rock",
    "Metal": "Metal", "Nu Metal": "Nu Metal",
    "Metalcore": "Metalcore", "Progressive Metalcore": "Metalcore",
    "Death Metal/Black Metal": "Death Metal", "Hardcore": "Hardcore",
    "Electro": "Electronic", "ELECTRO": "Electronic", "Elettronica": "Electronic",
    "Electronic": "Electronic", "metal electronic": "Electronic", "Bass": "Electronic",
    "Electronic, Dancehall, Reggae, Raggamuffin, Moombahton, Dubstep, Tropical House, Hip-Hop, Soca, Electropop": "Electronic",
    "Dance": "Dance", "Eurobeat": "Dance", "Disco": "Disco",
    "House": "House", "Techno": "Techno",
    "Dubstep": "Dubstep", "Trapstep": "Dubstep", "Moombahcore": "Dubstep",
    "Real Godzilla Step": "Dubstep", "twerk": "Dubstep",
    "Drum & Bass": "Drum & Bass", "Drum and Bass": "Drum & Bass",
    "Chill-out": "Chill-out", "Lounge": "Chill-out", "Trip Hop": "Trip-Hop",
    "Rap/Hip Hop": "Hip-Hop", "Hip-Hop/Rap": "Hip-Hop", "Hip-Hop": "Hip-Hop",
    "Electronic, Hip Hop": "Hip-Hop", "Trap": "Hip-Hop",
    "R&B": "R&B/Soul", "R&B / Soul": "R&B/Soul", "Soul": "R&B/Soul",
    "Soul / R&B / Pop": "R&B/Soul", "Soul/Funk/R&B": "R&B/Soul",
    "Pop": "Pop", "Singer & Songwriter": "Singer-Songwriter",
    "Jazz": "Jazz", "Vocal jazz": "Jazz",
    "Classica": "Classical", "Classical": "Classical",
    "Contemporary Era": "Classical", "Classical Crossover": "Classical Crossover",
    "Soundtrack": "Soundtrack", "Colonne sonore": "Soundtrack",
    "Film/Videogiochi": "Soundtrack", "Serie TV": "Soundtrack",
    "Film & Animation": "Soundtrack",
    "Video Giochi": "Video Game Music", "Video Game": "Video Game Music",
    "Gaming": "Video Game Music", "side scrolling adventure": "Video Game Music",
    "World music": "World", "Musica Africana": "World", "Musica Asiatica": "World",
    "Musica indiana": "World", "Musica celtica": "World", "Musica francese": "World",
    "Grecia": "World", "Germania": "World", "Worldwide": "World", "Choro": "World",
    "Flamenco": "World",
    "Musica latina": "Latin", "Latina": "Latin", "Reggaeton": "Latin",
    "Reggae": "Reggae", "Folk": "Folk", "Country": "Country",
    "Instrumental": "Instrumental", "New Age": "New Age", "Gospel": "Gospel",
    "Bambini": "Children's", "Canzoni di Natale": "Christmas",
}
# placeholders that mean "no real genre" -> re-derive
JUNK = {"", "Music", "Altri generi", "People & Blogs", "Miscellaneous",
        "Kill The Noise", "Biggie", "Daruma", "FULL FLAVOR", "FESTIVAL",
        "PREVIEW", "Fun", "sample pack", "consumer electronics", "Golf Cart",
        "Entertainment", "trap/city"}

FIELDS = ["artist", "albumartist", "album", "title", "date",
          "tracknumber", "discnumber", "composer"]


def easy(f):
    try:
        return mutagen.File(f, easy=True)
    except Exception:
        return None


def gettag(m, key):
    try:
        v = m.tags.get(key) if m and m.tags else None
        return str(v[0]).strip() if v else ""
    except Exception:
        return ""


def settags(m, changes):
    for k, v in changes.items():
        try:
            m.tags[k] = str(v)
        except Exception as e:
            print(f"    ! cannot set {k}: {e}", file=sys.stderr)
    m.save()


async def fetch_spotify(tids):
    from SpotiFLAC.core.spotify_metadata import SpotifyMetadataClient
    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    todo = [t for t in tids if t not in cache or "cover_url" not in cache.get(t, {})]
    if todo:
        client = SpotifyMetadataClient(timeout_s=15)
        for i, tid in enumerate(todo, 1):
            try:
                t = await client.get_track_async(tid)
                cache[tid] = {"title": t.title, "artists": t.artists, "album": t.album,
                              "album_artist": t.album_artist, "release_date": t.release_date,
                              "track_number": t.track_number, "disc_number": t.disc_number,
                              "composer": t.composer, "isrc": t.isrc,
                              "album_type": getattr(t, "album_type", ""),
                              "cover_url": getattr(t, "cover_url", "")}
            except Exception as e:
                print(f"  spotify fetch failed {tid}: {e}", file=sys.stderr)
            if i % 25 == 0:
                print(f"  spotify meta {i}/{len(todo)}", file=sys.stderr)
                CACHE_FILE.write_text(json.dumps(cache))
            await asyncio.sleep(0.15)
        CACHE_FILE.write_text(json.dumps(cache))
    return cache


def deezer_genre(isrc):
    try:
        with urllib.request.urlopen(f"https://api.deezer.com/track/isrc:{isrc}", timeout=10) as r:
            tr = json.load(r)
        alb = tr.get("album", {}).get("id")
        if not alb:
            return ""
        with urllib.request.urlopen(f"https://api.deezer.com/album/{alb}", timeout=10) as r:
            a = json.load(r)
        g = (a.get("genres", {}).get("data") or [{}])[0].get("name", "")
        return GENRE_MAP.get(g, g)
    except Exception:
        return ""


def main():
    rev = {v: k for k, v in json.load(open(SPOT / "track-id-index.json")).items()}
    files = []
    for f in sorted(LIB.rglob("*")):
        if f.suffix.lower() not in AUDIO or not f.is_file():
            continue
        if any(p.endswith("_quarantine") for p in f.parts):
            continue
        files.append(f)
    print(f"{len(files)} files")

    # pass 1: read all tags
    info = {}
    for f in files:
        m = easy(f)
        rel = str(f.relative_to(LIB))
        info[rel] = {"m": m, "f": f, "tid": rev.get(rel),
                     "tags": {k: gettag(m, k) for k in FIELDS + ["genre"]}}

    # artist -> normalized-genre majority (from non-junk tags)
    artist_genre = defaultdict(Counter)
    for rel, d in info.items():
        g = d["tags"]["genre"]
        ng = GENRE_MAP.get(g, g)
        if g not in JUNK and ng:
            a = (d["tags"]["artist"] or rel.split("/")[0]).lower()
            artist_genre[a][ng] += 1

    # which ids need a spotify fetch? (missing any fill field, or junk genre needing isrc)
    need_ids = set()
    for rel, d in info.items():
        if not d["tid"]:
            continue
        t = d["tags"]
        if any(not t[k] for k in ("artist", "title", "album", "albumartist", "date", "tracknumber", "composer")):
            need_ids.add(d["tid"])
        if t["genre"] in JUNK:
            need_ids.add(d["tid"])
        need_ids.add(d["tid"])  # full coverage: releasetype + cover_url for the art phase
    print(f"spotify lookups needed: {len(need_ids)}")
    cache = asyncio.run(fetch_spotify(need_ids)) if need_ids else {}

    stats = Counter()
    changes_log = {}
    deezer_budget = 400
    for rel, d in info.items():
        m, f, tid, t = d["m"], d["f"], d["tid"], d["tags"]
        if m is None or m.tags is None:
            stats["unreadable"] += 1
            continue
        ch = {}
        sp = cache.get(tid, {}) if tid else {}
        # --- fills (never overwrite) ---
        parts = rel.split("/")
        if not t["artist"]:
            ch["artist"] = sp.get("artists") or parts[0]
        if not t["title"]:
            ch["title"] = sp.get("title") or re.sub(r"\s+-\s+[^-]+$", "", f.stem)
        if not t["album"] and (sp.get("album") or len(parts) >= 2):
            ch["album"] = sp.get("album") or parts[1]
        if not t["albumartist"]:
            ch["albumartist"] = sp.get("album_artist") or ch.get("artist") or t["artist"]
        if not t["date"] and sp.get("release_date"):
            ch["date"] = sp["release_date"][:10]
        if not t["tracknumber"] and sp.get("track_number"):
            ch["tracknumber"] = sp["track_number"]
        if not t["discnumber"] and sp.get("disc_number"):
            ch["discnumber"] = sp["disc_number"]
        if not t["composer"] and sp.get("composer"):
            ch["composer"] = sp["composer"]
        # --- genre ---
        g = t["genre"]
        if g in JUNK:
            a = (t["artist"] or parts[0]).lower()
            top = artist_genre.get(a)
            ng = top.most_common(1)[0][0] if top else ""
            if not ng and sp.get("isrc") and deezer_budget > 0:
                deezer_budget -= 1
                ng = deezer_genre(sp["isrc"])
                time.sleep(0.15)
            if ng:
                ch["genre"] = ng
                stats["genre_derived"] += 1
            else:
                stats["genre_unresolved"] += 1
        elif GENRE_MAP.get(g, g) != g:
            ch["genre"] = GENRE_MAP[g]
            stats["genre_normalized"] += 1
        # --- releasetype (FLAC only, fill-only) ---
        if f.suffix.lower() == ".flac" and sp.get("album_type"):
            if not gettag(m, "releasetype"):
                ch["releasetype"] = sp["album_type"]
        if not ch:
            continue
        stats["files_changed"] += 1
        for k in ch:
            stats[f"set_{k}"] += 1
        changes_log[rel] = {k: str(v) for k, v in ch.items()}
        if APPLY:
            try:
                settags(m, ch)
            except Exception as e:
                stats["write_errors"] += 1
                print(f"  ! write failed {rel}: {e}", file=sys.stderr)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    json.dump(changes_log, open(SPOT / f"enrich-log-{stamp}.json", "w"), indent=1)
    print(f"\nSUMMARY (applied={APPLY}):")
    for k, v in sorted(stats.items()):
        print(f"  {k:20s} {v}")
    print(f"log: enrich-log-{stamp}.json")


main()
