#!/usr/bin/env python3
"""media-enrich.py [lyrics|country|art] [--apply]   (2026-09-01/02)

lyrics : embed lyrics from LRCLib (synced when available) into files that
         have none. FLAC → LYRICS comment, MP3 → USLT frame, M4A → ©lyr.
country: artist origin country via MusicBrainz (1 req/s, cached in
         ~/spotiflac/artist-country-cache.json), written fill-only as
         RELEASECOUNTRY (Picard-style per format) so Navidrome/Symfonium
         expose it as the album country facet.
art    : album covers.
         - indexed FLAC (official source): fill-only if art missing
         - indexed MP3/M4A (YouTube/spotdl rips): REPLACE art with the
           Spotify album cover (their embedded art is often a video thumb)
         - unindexed files: fill-only via Deezer search, then iTunes search
         - every album dir also gets a cover.jpg (from the same art, or
           extracted from an existing embedded cover)
         Images are downscaled to max 300px JPEG before embedding.
Dry-run by default; --apply writes. Logs to ~/spotiflac/<mode>-log-<ts>.json.
"""
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, USLT, APIC, TXXX, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover

import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "bin"))
from _common import LIBRARY_DIR as _LIB, STATE_DIR as _STATE, MUSIC_ROOT as _MUSIC, PLAYLISTS_DIR as _PLAY, VENV as _VENV  # noqa: E402

LIB = _LIB
SPOT = _STATE
AUDIO = {".mp3", ".m4a", ".flac"}
MODE = sys.argv[1]
APPLY = "--apply" in sys.argv
UA = {"User-Agent": "spotiflac-pipeline-tools/1.0 (homelab library maintenance)"}


def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def http_bytes(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def walk():
    for f in sorted(LIB.rglob("*")):
        if f.suffix.lower() in AUDIO and f.is_file() and not any(
                p.endswith("_quarantine") for p in f.parts):
            yield f


def easytags(f):
    try:
        m = mutagen.File(f, easy=True)
        return m
    except Exception:
        return None


def get(m, key):
    try:
        v = m.tags.get(key) if m and m.tags else None
        return str(v[0]).strip() if v else ""
    except Exception:
        return ""


# ------------------------------------------------------------------ lyrics
def has_lyrics(f):
    try:
        if f.suffix.lower() == ".flac":
            fl = FLAC(f)
            return any(k.lower() in ("lyrics", "unsyncedlyrics") and fl[k] for k in fl.keys())
        if f.suffix.lower() == ".mp3":
            id3 = ID3(f)
            return bool(id3.getall("USLT")) or bool(id3.getall("SYLT"))
        if f.suffix.lower() == ".m4a":
            mp = MP4(f)
            return bool(mp.tags and mp.tags.get("\xa9lyr"))
    except Exception:
        return True  # unreadable -> don't touch
    return True


def write_lyrics(f, text):
    if f.suffix.lower() == ".flac":
        fl = FLAC(f)
        fl["LYRICS"] = text
        fl.save()
    elif f.suffix.lower() == ".mp3":
        try:
            id3 = ID3(f)
        except ID3NoHeaderError:
            id3 = ID3()
        id3.setall("USLT", [USLT(encoding=3, lang="und", desc="", text=text)])
        id3.save(f)
    elif f.suffix.lower() == ".m4a":
        mp = MP4(f)
        if mp.tags is None:
            mp.add_tags()
        mp.tags["\xa9lyr"] = [text]
        mp.save()


def lrclib(artist, title, album, dur):
    q = urllib.parse.urlencode({"artist_name": artist, "track_name": title,
                                "album_name": album, "duration": int(dur)})
    try:
        d = http_json(f"https://lrclib.net/api/get?{q}")
        return d.get("syncedLyrics") or d.get("plainLyrics") or ""
    except Exception:
        pass
    try:
        q2 = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
        d = http_json(f"https://lrclib.net/api/get?{q2}")
        return d.get("syncedLyrics") or d.get("plainLyrics") or ""
    except Exception:
        return ""


def lyrics():
    stats = Counter()
    log = {}
    for f in walk():
        stats["files"] += 1
        if has_lyrics(f):
            stats["already"] += 1
            continue
        m = easytags(f)
        artist, title, album = get(m, "artist"), get(m, "title"), get(m, "album")
        dur = getattr(getattr(m, "info", None), "length", 0) or 0
        if not artist or not title:
            stats["no_tags"] += 1
            continue
        text = lrclib(artist, title, album, dur)
        time.sleep(0.25)
        if not text:
            stats["not_found"] += 1
            continue
        synced = bool(re.search(r"^\[\d\d:\d\d", text, re.M))
        stats["found_synced" if synced else "found_plain"] += 1
        rel = str(f.relative_to(LIB))
        log[rel] = "synced" if synced else "plain"
        if APPLY:
            try:
                write_lyrics(f, text)
                stats["written"] += 1
            except Exception as e:
                stats["write_errors"] += 1
                print(f"  ! {rel}: {e}", file=sys.stderr)
        if stats["files"] % 200 == 0:
            print(f"  …{stats['files']} files, found {stats['found_synced']+stats['found_plain']}", file=sys.stderr)
    finish("lyrics", stats, log)


# ----------------------------------------------------------------- country
def mb_artist_country(name, cache):
    key = name.lower()
    if key in cache:
        return cache[key]
    q = urllib.parse.quote(f'artist:"{name}"')
    try:
        d = http_json(f"https://musicbrainz.org/ws/2/artist/?query={q}&fmt=json&limit=1")
        arts = d.get("artists") or []
        c = ""
        if arts and int(arts[0].get("score", 0)) >= 90:
            c = arts[0].get("country") or ""
            if not c:
                codes = (arts[0].get("area") or {}).get("iso-3166-1-codes") or []
                c = codes[0] if codes else ""
        cache[key] = c
    except Exception:
        cache[key] = ""
    time.sleep(1.1)
    return cache[key]


def has_country(f):
    try:
        if f.suffix.lower() == ".flac":
            return bool(FLAC(f).get("RELEASECOUNTRY"))
        if f.suffix.lower() == ".mp3":
            id3 = ID3(f)
            return any(x.desc == "MusicBrainz Album Release Country" for x in id3.getall("TXXX"))
        if f.suffix.lower() == ".m4a":
            mp = MP4(f)
            return bool(mp.tags and mp.tags.get("----:com.apple.iTunes:MusicBrainz Album Release Country"))
    except Exception:
        return True
    return True


def write_country(f, code):
    if f.suffix.lower() == ".flac":
        fl = FLAC(f)
        fl["RELEASECOUNTRY"] = code
        fl.save()
    elif f.suffix.lower() == ".mp3":
        try:
            id3 = ID3(f)
        except ID3NoHeaderError:
            id3 = ID3()
        id3.add(TXXX(encoding=3, desc="MusicBrainz Album Release Country", text=[code]))
        id3.save(f)
    elif f.suffix.lower() == ".m4a":
        mp = MP4(f)
        if mp.tags is None:
            mp.add_tags()
        mp.tags["----:com.apple.iTunes:MusicBrainz Album Release Country"] = [code.encode()]
        mp.save()


def country():
    cache_file = SPOT / "artist-country-cache.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    stats = Counter()
    log = {}
    pending = []
    artists_needed = set()
    for f in walk():
        stats["files"] += 1
        if has_country(f):
            stats["already"] += 1
            continue
        m = easytags(f)
        artist = get(m, "albumartist") or get(m, "artist") or f.relative_to(LIB).parts[0]
        artist = artist.split(",")[0].strip()
        if not artist:
            stats["no_artist"] += 1
            continue
        pending.append((f, artist))
        if artist.lower() not in cache:
            artists_needed.add(artist)
    print(f"MusicBrainz lookups needed: {len(artists_needed)} artists "
          f"(~{len(artists_needed)//55+1} min)", file=sys.stderr)
    for i, a in enumerate(sorted(artists_needed), 1):
        mb_artist_country(a, cache)
        if i % 50 == 0:
            print(f"  …{i}/{len(artists_needed)}", file=sys.stderr)
            cache_file.write_text(json.dumps(cache))
    cache_file.write_text(json.dumps(cache))
    for f, artist in pending:
        code = cache.get(artist.lower(), "")
        if not code:
            stats["unknown_country"] += 1
            continue
        rel = str(f.relative_to(LIB))
        log[rel] = code
        stats[f"country_{code}"] += 1
        if APPLY:
            try:
                write_country(f, code)
                stats["written"] += 1
            except Exception as e:
                stats["write_errors"] += 1
                print(f"  ! {rel}: {e}", file=sys.stderr)
    finish("country", stats, log)


# --------------------------------------------------------------------- art
def shrink(data, maxpx=300):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    im.thumbnail((maxpx, maxpx))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


def has_art(f):
    try:
        if f.suffix.lower() == ".flac":
            return bool(FLAC(f).pictures)
        if f.suffix.lower() == ".mp3":
            return bool(ID3(f).getall("APIC"))
        if f.suffix.lower() == ".m4a":
            mp = MP4(f)
            return bool(mp.tags and mp.tags.get("covr"))
    except Exception:
        return True
    return True


def extract_art(f):
    try:
        if f.suffix.lower() == ".flac":
            pics = FLAC(f).pictures
            return pics[0].data if pics else None
        if f.suffix.lower() == ".mp3":
            apics = ID3(f).getall("APIC")
            return apics[0].data if apics else None
        if f.suffix.lower() == ".m4a":
            mp = MP4(f)
            c = mp.tags.get("covr") if mp.tags else None
            return bytes(c[0]) if c else None
    except Exception:
        return None


def write_art(f, jpeg):
    if f.suffix.lower() == ".flac":
        fl = FLAC(f)
        fl.clear_pictures()
        pic = Picture()
        pic.type = 3
        pic.mime = "image/jpeg"
        pic.data = jpeg
        fl.add_picture(pic)
        fl.save()
    elif f.suffix.lower() == ".mp3":
        try:
            id3 = ID3(f)
        except ID3NoHeaderError:
            id3 = ID3()
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=jpeg))
        id3.save(f)
    elif f.suffix.lower() == ".m4a":
        mp = MP4(f)
        if mp.tags is None:
            mp.add_tags()
        mp.tags["covr"] = [MP4Cover(jpeg, imageformat=MP4Cover.FORMAT_JPEG)]
        mp.save()


def deezer_art(artist, title):
    try:
        q = urllib.parse.quote(f'artist:"{artist}" track:"{title}"')
        d = http_json(f"https://api.deezer.com/search?q={q}&limit=1")
        data = d.get("data") or []
        if data:
            return data[0].get("album", {}).get("cover_medium") or ""
    except Exception:
        pass
    return ""


def itunes_art(artist, title):
    try:
        q = urllib.parse.urlencode({"term": f"{artist} {title}", "media": "music", "limit": 1})
        d = http_json(f"https://itunes.apple.com/search?{q}")
        res = d.get("results") or []
        if res:
            return (res[0].get("artworkUrl100") or "").replace("100x100bb", "300x300bb")
    except Exception:
        pass
    return ""


def art():
    idx = json.load(open(SPOT / "track-id-index.json"))
    rev = {v: k for k, v in idx.items()}
    meta = json.loads((SPOT / "track-meta-cache.json").read_text()) if (SPOT / "track-meta-cache.json").exists() else {}
    stats = Counter()
    log = {}
    img_cache = {}   # url -> shrunk jpeg

    def fetch_img(url):
        if not url:
            return None
        if url not in img_cache:
            try:
                img_cache[url] = shrink(http_bytes(url))
            except Exception:
                img_cache[url] = None
        return img_cache[url]

    albumdirs = {}
    for f in walk():
        stats["files"] += 1
        rel = str(f.relative_to(LIB))
        tid = rev.get(rel)
        suffix = f.suffix.lower()
        official = suffix == ".flac"
        want_replace = bool(tid) and not official        # yt/spotdl rip with spotify id
        missing = not has_art(f)
        target = None
        if tid and (want_replace or missing):
            target = (meta.get(tid) or {}).get("cover_url", "")
        elif missing:
            m = easytags(f)
            a, t = get(m, "artist"), get(m, "title")
            if a and t:
                target = deezer_art(a, t) or itunes_art(a, t)
                time.sleep(0.15)
        if target:
            jpeg = fetch_img(target)
            if jpeg:
                stats["replaced" if not missing else "filled"] += 1
                log[rel] = "replaced" if not missing else "filled"
                if APPLY:
                    try:
                        write_art(f, jpeg)
                    except Exception as e:
                        stats["write_errors"] += 1
                        print(f"  ! {rel}: {e}", file=sys.stderr)
                albumdirs.setdefault(f.parent, jpeg)
            elif missing:
                stats["no_source"] += 1
        elif missing:
            stats["no_source"] += 1
        if f.parent not in albumdirs:
            albumdirs[f.parent] = None
        if stats["files"] % 300 == 0:
            print(f"  …{stats['files']} files", file=sys.stderr)

    # cover.jpg per album dir
    for d, jpeg in albumdirs.items():
        if (d / "cover.jpg").exists() or (d / "folder.jpg").exists() or (d / "Cover.jpg").exists():
            continue
        if jpeg is None:
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in AUDIO:
                    raw = extract_art(f)
                    if raw:
                        try:
                            jpeg = shrink(raw)
                        except Exception:
                            jpeg = None
                        break
        if jpeg:
            stats["cover_jpg"] += 1
            if APPLY:
                try:
                    (d / "cover.jpg").write_bytes(jpeg)
                except Exception:
                    stats["cover_jpg_errors"] += 1
    finish("art", stats, log)


def finish(mode, stats, log):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json.dump(log, open(SPOT / f"{mode}-log-{stamp}.json", "w"), indent=0)
    print(f"\nSUMMARY {mode} (applied={APPLY}):")
    for k, v in sorted(stats.items()):
        print(f"  {k:20s} {v}")


{"lyrics": lyrics, "country": country, "art": art}[MODE]()
