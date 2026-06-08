#!/usr/bin/env python3
# Audit MP3s under the spotdl root ($SPOTDL_ROOT/liked/ and $SPOTDL_ROOT/
# playlists/<name>/) against an old spotdl playlists.json export.
#
# Match: filename → (file_artist_str, file_title_str). A file matches a track
# in playlists.json when:
#   (a) any track artist name appears (normalized) in EITHER the file artist
#       field OR the file title field, AND
#   (b) the track title has ≥ FUZZY_TITLE normalized fuzzy overlap with the
#       file title.
#
# For each matched MP3, ffprobe its local duration and compare to the
# track's duration_ms from playlists.json. Mark BAD if the gap > TOLERANCE_SEC.
#
# Output: $STATE_DIR/spotdl-audit.json + a printed summary.

import json
import re
import subprocess
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import SPOTDL_ROOT, STATE_DIR, OLD_PLAYLISTS_JSON

MUSIC = SPOTDL_ROOT
LIKED = MUSIC / "liked"
PLAYLISTS_DIR = MUSIC / "playlists"
OLD_JSON = Path(OLD_PLAYLISTS_JSON) if OLD_PLAYLISTS_JSON else None
REPORT = STATE_DIR / "spotdl-audit.json"

TOLERANCE_SEC = 5.0
FUZZY_TITLE = 0.80


def norm(s):
    if not s:
        return ""
    s = s.lower()
    # collapse punctuation/whitespace, drop common parenthetical noise
    s = re.sub(r"\(feat\.[^)]*\)|\[feat\.[^\]]*\]|\bft\.\s+\S+", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fname_parse(stem):
    # spotdl convention: "<Artist> - <Title>"
    m = re.match(r"(.+?)\s+-\s+(.+)", stem)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", stem.strip()


def file_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def build_track_lookup(old):
    """Yield (track_dict, playlist_name) pairs from the old json."""
    for pl in old.get("playlists", []):
        for t in pl.get("tracks", []):
            tr = t.get("track")
            if tr:
                yield tr, pl["name"]


def find_match(file_artist, file_title, track_lookup, indexed):
    """Find best matching playlists.json track for this MP3 file.
    Returns (track_dict, [playlist_names]) or (None, [])."""
    fa_n = norm(file_artist)
    ft_n = norm(file_title)
    if not (fa_n or ft_n):
        return None, []

    best = None
    best_score = 0.0
    for tr in indexed:
        ta_names = [norm(a.get("name", "")) for a in tr.get("artists", [])]
        tt_n = norm(tr.get("name", ""))
        # artist condition: any track-artist (normalized) appears in either
        # the file's artist field OR the file's title field
        artist_ok = any(
            tan and (tan in fa_n or tan in ft_n)
            for tan in ta_names
        )
        if not artist_ok:
            continue
        # title fuzzy match
        score = SequenceMatcher(None, ft_n, tt_n).ratio()
        # Substring boost: if normalised track title is a substring of file
        # title (e.g. "Tidecaller" in "Tidecaller [official audio]"), accept
        if tt_n and tt_n in ft_n:
            score = max(score, 0.95)
        if score > best_score:
            best_score = score
            best = tr
    if best and best_score >= FUZZY_TITLE:
        # find all playlists this track appears in (re-scan)
        playlists = [pl for tr, pl in track_lookup if tr.get("id") == best.get("id")]
        return best, sorted(set(playlists))
    return None, []


def main():
    if not OLD_JSON or not OLD_JSON.exists():
        sys.exit(
            "audit-spotdl requires SPOTIFLAC_OLD_PLAYLISTS_JSON to point at an "
            "old spotdl playlists.json export. Set it in your env file."
        )
    old = json.loads(OLD_JSON.read_text())

    # Build a dedup'd track list and a (track, playlist_name) lookup
    seen_ids = set()
    indexed = []
    track_lookup = []
    for pl in old["playlists"]:
        for t in pl.get("tracks", []):
            tr = t.get("track") or {}
            tid = tr.get("id")
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                indexed.append(tr)
            if tr:
                track_lookup.append((tr, pl["name"]))
    print(f"playlists.json: {len(seen_ids)} unique tracks across {len(old['playlists'])} playlists", file=sys.stderr)

    # Walk MP3s — skip files under any *_quarantine/ subdir (those are our
    # own evidence files from dedup or from the redownload retry mechanism,
    # not user-facing audio).
    files = []
    for root in (LIKED, PLAYLISTS_DIR):
        if not root.exists():
            continue
        for p in root.rglob("*.mp3"):
            if any(part.endswith("_quarantine") for part in p.parts):
                continue
            files.append(p)
    print(f"\nMP3 files to audit: {len(files)} (after excluding *_quarantine/ paths)", file=sys.stderr)

    buckets = {"good": [], "bad": [], "unmatched": [], "no_duration": []}
    for i, f in enumerate(files, 1):
        rel = str(f.relative_to(MUSIC))
        artist, title = fname_parse(f.stem)
        match, pl_names = find_match(artist, title, track_lookup, indexed)
        if not match:
            buckets["unmatched"].append({"path": rel, "size": f.stat().st_size,
                                          "file_artist": artist, "file_title": title})
            continue
        file_dur = file_duration(f)
        if file_dur is None:
            buckets["no_duration"].append({"path": rel, "size": f.stat().st_size})
            continue
        spotify_dur_s = match.get("duration_ms", 0) / 1000.0
        diff = abs(file_dur - spotify_dur_s)
        entry = {
            "path": rel,
            "spotify_id": match.get("id"),
            "spotify_url": match.get("external_urls", {}).get("spotify"),
            "spotify_name": match.get("name"),
            "spotify_artists": [a.get("name") for a in match.get("artists", [])],
            "spotify_dur_s": round(spotify_dur_s, 2),
            "file_dur_s": round(file_dur, 2),
            "diff_s": round(diff, 2),
            "playlists": pl_names,
            "size": f.stat().st_size,
        }
        if diff > TOLERANCE_SEC:
            buckets["bad"].append(entry)
        else:
            buckets["good"].append(entry)

        if i % 200 == 0:
            print(f"  {i}/{len(files)}  good={len(buckets['good'])} bad={len(buckets['bad'])} unmatched={len(buckets['unmatched'])}", file=sys.stderr)

    REPORT.write_text(json.dumps({
        "summary": {b: len(v) for b, v in buckets.items()},
        "total": len(files),
        "buckets": buckets,
    }, indent=2, ensure_ascii=False))

    print(f"\nDone. total={len(files)}")
    for b in ("good", "bad", "unmatched", "no_duration"):
        sz = sum(x.get("size", 0) for x in buckets[b])
        print(f"  {b:>11}: {len(buckets[b]):>5} ({sz/(1024**3):.2f} GB)")
    print(f"\nReport: {REPORT}")
    if buckets["bad"]:
        print(f"\nSample bad (first 10):")
        for x in buckets["bad"][:10]:
            print(f"  {x['file_dur_s']:>6.1f}s file vs {x['spotify_dur_s']:>6.1f}s spotify (Δ {x['diff_s']:>5.1f}s)  {x['path'][:60]}")


if __name__ == "__main__":
    main()
