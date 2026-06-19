#!/usr/bin/env python3
# Walk every FLAC + M4A in _library/, extract its Spotify URL tag, fetch the
# real Spotify duration, compare to the file's actual audio duration. If they
# differ by more than TOLERANCE_SEC seconds, flag the file as bad
# (metadata/audio mismatch — typically a misrouted download from spotiflac's
# Odesli-based resolver for FLAC, or yt-dlp picking the wrong YouTube video
# for M4A fallback).
#
# Modes:
#   default (no args)     verify only; write JSON report
#   --clean               re-verify, delete bad files, regenerate affected
#                         M3Us, unmark affected playlists from done.txt so
#                         the next batch picks them up

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import LIBRARY_DIR, PLAYLISTS_DIR, STATE_DIR

LIB = LIBRARY_DIR
PLAY = PLAYLISTS_DIR
DONE_LOG = STATE_DIR / "done.txt"
REPORT_FILE = STATE_DIR / "verify-report.json"
CACHE_FILE = STATE_DIR / "spotify-track-cache.json"

TOLERANCE_SEC = 5.0
UA = "Mozilla/5.0"
AUDIO_EXTS = (".flac", ".m4a")
M3U_LIBRARY_PREFIX = "../_library/"


def ffprobe_info(path: Path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        out = r.stdout
    except Exception:
        return None
    dur = None
    for m in re.finditer(r"^duration=([\d.]+)", out, re.M):
        dur = float(m.group(1))
        break
    url_m = re.search(r"TAG:URL=(\S+)", out)
    isrc_m = re.search(r"TAG:ISRC=(\S+)", out)
    return {
        "duration": dur,
        "url": url_m.group(1) if url_m else None,
        "isrc": isrc_m.group(1) if isrc_m else None,
    }


def spotify_duration(track_id, cache):
    if track_id in cache:
        return cache[track_id]
    try:
        req = urllib.request.Request(
            f"https://open.spotify.com/track/{track_id}",
            headers={"User-Agent": UA},
        )
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
        m = re.search(r'music:duration"[^>]+content="(\d+)"', html)
        if m:
            cache[track_id] = int(m.group(1))
        else:
            cache[track_id] = None
    except Exception:
        cache[track_id] = None
    time.sleep(0.3)  # be polite
    return cache[track_id]


def load_m3u_playlists():
    """Return {m3u_stem: [verbatim non-comment lines]}.

    Stored verbatim (NOT prefix-stripped) so non-`_library/` paths — e.g.
    MP3 fallback entries like `../../liked/Foo.mp3` — survive a round-trip
    through clean()'s rebuild without being mangled.
    """
    out = {}
    for m3u in PLAY.glob("*.m3u8"):
        lines = []
        for line in m3u.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            lines.append(line)
        out[m3u.stem] = lines
    return out


def m3u_line_under_library(line):
    """If this M3U line points at a file under _library/, return the rel path; else None.

    Lines starting with `../_library/` map to files in LIB. Anything else
    (MP3 fallback entries, `#` directives) returns None.
    """
    if line.startswith(M3U_LIBRARY_PREFIX):
        return line[len(M3U_LIBRARY_PREFIX):]
    return None


def verify():
    cache = {}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            cache = {}

    audio_files = sorted(
        f for ext in AUDIO_EXTS for f in LIB.rglob(f"*{ext}")
    )
    print(f"Scanning {len(audio_files)} audio files (FLAC + M4A)…", file=sys.stderr)

    bad = []
    unknown = []
    good = 0

    for i, audio_file in enumerate(audio_files, 1):
        rel = str(audio_file.relative_to(LIB))
        info = ffprobe_info(audio_file)
        if not info or info["duration"] is None:
            unknown.append({"file": rel, "reason": "ffprobe failed"})
            continue
        url = info["url"]
        if not url:
            unknown.append({"file": rel, "reason": "no URL tag"})
            continue
        m = re.search(r"/track/([A-Za-z0-9]+)", url)
        if not m:
            unknown.append({"file": rel, "reason": "url not a /track/"})
            continue
        spotify_id = m.group(1)
        spot_dur = spotify_duration(spotify_id, cache)
        if spot_dur is None:
            unknown.append({"file": rel, "reason": "spotify lookup failed", "id": spotify_id})
            continue
        diff = abs(info["duration"] - spot_dur)
        if diff > TOLERANCE_SEC:
            bad.append({
                "file": rel,
                "spotify_id": spotify_id,
                "file_dur": round(info["duration"], 2),
                "spotify_dur": spot_dur,
                "diff": round(diff, 2),
                "isrc": info["isrc"],
            })
        else:
            good += 1

        if i % 25 == 0:
            print(f"  {i}/{len(audio_files)}  good={good} bad={len(bad)} unknown={len(unknown)}", file=sys.stderr)
            CACHE_FILE.write_text(json.dumps(cache, indent=2))

    CACHE_FILE.write_text(json.dumps(cache, indent=2))

    # Map bad files back to their playlists (by matching against m3u lines'
    # `../_library/...` references; MP3-fallback lines never match).
    playlists = load_m3u_playlists()
    bad_rel_set = {b["file"] for b in bad}
    affected_playlists = defaultdict(list)
    for pname, lines in playlists.items():
        for line in lines:
            rel = m3u_line_under_library(line)
            if rel and rel in bad_rel_set:
                affected_playlists[pname].append(rel)

    report = {
        "total": len(audio_files),
        "good": good,
        "bad": bad,
        "unknown": unknown,
        "affected_playlists": dict(sorted(
            ((k, len(v)) for k, v in affected_playlists.items()),
            key=lambda kv: -kv[1],
        )),
    }
    REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nDone: total={report['total']}  good={report['good']}  bad={len(report['bad'])}  unknown={len(report['unknown'])}")
    print(f"Affected playlists ({len(report['affected_playlists'])}):")
    for name, n in list(report["affected_playlists"].items())[:15]:
        print(f"  {n:4d} bad files  ·  {name}")
    print(f"\nFull report at {REPORT_FILE}")


def clean():
    if not REPORT_FILE.exists():
        sys.exit("No report — run verify first (no flags)")
    report = json.loads(REPORT_FILE.read_text())
    bad = report.get("bad", [])
    if not bad:
        print("Nothing to clean.")
        return

    # Map each bad file → playlists it appears in
    playlists = load_m3u_playlists()
    bad_files = {b["file"] for b in bad}

    def line_is_bad(line):
        rel = m3u_line_under_library(line)
        return rel is not None and rel in bad_files

    # 1. Delete bad files
    deleted = 0
    for rel in bad_files:
        p = LIB / rel
        try:
            p.unlink()
            deleted += 1
        except FileNotFoundError:
            pass

    # 2. Rewrite each M3U keeping every line whose target file wasn't deleted.
    # MP3-fallback lines (e.g. `../../liked/Foo.mp3`) pass through verbatim
    # because they never start with `../_library/`.
    for pname, lines in playlists.items():
        kept = [line for line in lines if not line_is_bad(line)]
        m3u = PLAY / f"{pname}.m3u8"
        with open(m3u, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for line in kept:
                f.write(f"{line}\n")

    # 3. Rebuild "All Tracks.m3u8" as union of all surviving lines (dedup'd).
    all_lines = set()
    for pname, lines in playlists.items():
        if pname == "All Tracks":
            continue
        for line in lines:
            if not line_is_bad(line):
                all_lines.add(line)
    with open(PLAY / "All Tracks.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for line in sorted(all_lines):
            f.write(f"{line}\n")

    # 4. Unmark affected playlists from done.txt
    affected_names = set(report.get("affected_playlists", {}).keys())
    # map name → playlist id via Spotify scrape result file
    # We need the inverse map: from M3U name -> playlist id.
    # spotiflac names M3U after playlist name (with / → _). We have
    # ~/spotiflac/playlist-state.json {id: {name, count}}
    state_file = STATE_DIR / "playlist-state.json"
    if state_file.exists():
        st = json.loads(state_file.read_text())
        # sanitize fn — spotiflac replaces / with _ in folder names; do the same here
        def sanitize(n):
            return n.replace("/", "_")
        name_to_id = {sanitize(meta["name"]): pid for pid, meta in st.items()}
        if DONE_LOG.exists():
            done_ids = [line.strip() for line in DONE_LOG.read_text().splitlines() if line.strip()]
        else:
            done_ids = []
        unmark = set()
        for name in affected_names:
            pid = name_to_id.get(name)
            if pid and pid in done_ids:
                unmark.add(pid)
        if unmark:
            kept = [d for d in done_ids if d not in unmark]
            DONE_LOG.write_text("\n".join(kept) + ("\n" if kept else ""))
            print(f"Unmarked {len(unmark)} playlist(s) from done.txt for retry.")

    print(f"Deleted {deleted} bad audio file(s), regenerated {len(playlists)} M3U(s) + All Tracks union.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="Re-verify + delete bad files + update done.txt + M3Us")
    ap.add_argument("--no-reverify", action="store_true", help="Skip the re-verify step in --clean (acts on existing report). For debugging only.")
    args = ap.parse_args()
    if args.clean:
        if not args.no_reverify:
            verify()  # refresh the report before cleaning
        clean()
    else:
        verify()
