#!/usr/bin/env python3
# Find and dedup multiple copies of the same track across the music library.
#
# Sources scanned:
#   - $OUTPUT_DIR/_library/<Artist>/<Album>/*.{flac,m4a,mp3}     (spotiflac)
#   - $SPOTDL_ROOT/liked/<Artist> - <Title>.mp3                  (spotdl Liked)
#   - $SPOTDL_ROOT/playlists/<Playlist>/<Artist> - <Title>.mp3   (spotdl playlists)
#   - $MUSIC_ROOT/<other artist dirs>/<Album>/...                (e.g. Lidarr)
#
# Same-track grouping:
#   1. by spotify_id when both files have one (FLACs via TAG:URL; MP3s via
#      spotdl-audit.json)
#   2. by (artist_norm, title_norm) fuzzy + duration ±TOLERANCE_PAIR s for
#      files without a spotify_id
#   3. PLUS a "misrouted FLAC detector" pass: a FLAC whose duration does NOT
#      match its TAG:URL's Spotify duration, but whose (artist, title) ties
#      to a verified-good MP3 → grouped together with the MP3 as keeper.
#
# Keeper rule within a group:
#   verified-good (file_dur within ±TOLERANCE_VERIFY s of Spotify's) >
#   anything else, then format flac > mp3 > m4a, then higher bitrate, then
#   larger size.
#
# Loser fate: moved to <source_root>/_dedup_quarantine/ with the original
# path encoded into the filename so it can be restored. Reversible.
#
# --do executes the moves; default is dry-run.

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    MUSIC_ROOT, LIBRARY_DIR, SPOTDL_ROOT, STATE_DIR, OLD_PLAYLISTS_JSON,
)

MUSIC = MUSIC_ROOT
SPOTIFLAC_LIB = LIBRARY_DIR
SPOTDL_LIKED = SPOTDL_ROOT / "liked"
SPOTDL_PLAYLISTS = SPOTDL_ROOT / "playlists"
INDEX_FILE = STATE_DIR / "track-id-index.json"
AUDIT_FILE = STATE_DIR / "spotdl-audit.json"
OLD_JSON = Path(OLD_PLAYLISTS_JSON) if OLD_PLAYLISTS_JSON else None
REPORT = STATE_DIR / "dedup-report.json"

AUDIO_EXTS = {".flac", ".m4a", ".mp3", ".opus", ".ogg", ".aac"}
# Top-level dirs under MUSIC_ROOT that the dedup pass treats specially (i.e.
# spotiflac/spotdl roots). Everything else is presumed to be third-party
# managed (e.g. Lidarr) and is scanned read-only for cross-source matches.
FORMAT_RANK = {"flac": 3, "mp3": 2, "m4a": 1, "opus": 1, "ogg": 1, "aac": 1}
TOLERANCE_VERIFY = 5.0
TOLERANCE_PAIR = 3.0
FUZZY_TITLE = 0.85


def keeper_sort_key(f):
    """Sort key for picking the keeper from a cluster of same-track files.

    Lower is better. Order of preference:
      1. verified_good (file duration within ±TOLERANCE_VERIFY of Spotify's)
      2. format rank: flac > mp3 > m4a/aac/ogg/opus
      3. bitrate: higher
      4. size: larger
    """
    return (
        0 if f["verified_good"] else 1,
        -f["format_rank"],
        -f["bitrate"],
        -f["size"],
    )


def norm(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\(feat\.[^)]*\)|\[feat\.[^\]]*\]|\bft\.\s+\S+", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def ffprobe_info(path):
    """Return (duration_seconds, bitrate_bps, url_tag) or (None,...) on error."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,bit_rate:format_tags=URL,Url,url",
             "-of", "default=nokey=0:noprint_wrappers=1", str(path)],
            capture_output=True, text=True, timeout=8,
        )
        out = r.stdout
        dur_m = re.search(r"duration=([\d.]+)", out)
        br_m = re.search(r"bit_rate=(\d+)", out)
        url_m = re.search(r"URL=(\S+)", out, re.I)
        dur = float(dur_m.group(1)) if dur_m else None
        br = int(br_m.group(1)) if br_m else 0
        tid = None
        if url_m:
            m2 = re.search(r"/track/([A-Za-z0-9]+)", url_m.group(1))
            tid = m2.group(1) if m2 else None
        return dur, br, tid
    except Exception:
        return None, 0, None


def parse_artist_title_for_path(p, source):
    """Best-effort extraction of (artist, title) from path + filename."""
    stem = p.stem
    if source == "spotiflac_library":
        # _library/<Artist>/<Album>/<Title> - <Artist>.flac
        parts = p.parts
        # parts[-3] is artist dir if structure is <Artist>/<Album>/<file>
        try:
            idx = parts.index("_library")
            artist = parts[idx + 1] if len(parts) > idx + 1 else ""
        except ValueError:
            artist = parts[0] if parts else ""
        m = re.match(r"(.+?)\s+-\s+", stem)
        title = m.group(1) if m else stem
        return artist, title
    if source == "spotdl_liked":
        m = re.match(r"(.+?)\s+-\s+(.+)", stem)
        return (m.group(1), m.group(2)) if m else ("", stem)
    if source == "spotdl_playlist":
        m = re.match(r"(.+?)\s+-\s+(.+)", stem)
        return (m.group(1), m.group(2)) if m else ("", stem)
    if source == "lidarr":
        # toplevel/<Artist>/<Album>/<Artist> - <Album> - NN - Title.flac OR variants
        parts = p.parts
        artist = parts[0] if parts else ""
        segs = stem.split(" - ")
        # Last segment is usually title; first might be artist
        title = segs[-1] if len(segs) >= 3 else stem
        return artist, title
    return "", stem


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--do", action="store_true", help="actually move files; default is dry-run")
    args = ap.parse_args()

    # Load Spotify track ID → duration_ms from playlists.json (richest source)
    sp_dur = {}
    if OLD_JSON.exists():
        old = json.loads(OLD_JSON.read_text())
        for pl in old.get("playlists", []):
            for t in pl.get("tracks", []):
                tr = t.get("track") or {}
                tid = tr.get("id")
                if tid and "duration_ms" in tr:
                    sp_dur[tid] = tr["duration_ms"] / 1000.0
    print(f"sp_dur lookup: {len(sp_dur)} tracks from playlists.json", file=sys.stderr)

    # Load FLAC id-to-path index (spotiflac)
    flac_index = {}
    if INDEX_FILE.exists():
        flac_index = json.loads(INDEX_FILE.read_text())

    # Load spotdl audit (gives MP3 → spotify_id + spotify_dur_s if matched)
    mp3_id_by_path = {}
    if AUDIT_FILE.exists():
        audit = json.loads(AUDIT_FILE.read_text())
        for bucket in ("good", "bad"):
            for x in audit["buckets"].get(bucket, []):
                if x.get("spotify_id"):
                    mp3_id_by_path[x["path"]] = (x["spotify_id"], x.get("spotify_dur_s"))

    # Walk all sources and build file records
    files = []  # list of dicts

    def add_file(abs_path, source, source_root):
        rel = abs_path.relative_to(source_root)
        ext = abs_path.suffix.lower().lstrip(".")
        dur, br, tag_id = ffprobe_info(abs_path)
        # Look up Spotify ID for this file
        spotify_id = None
        spotify_duration = None
        # FLAC: try URL tag (already loaded into index, but ffprobe-tag is fallback)
        if ext == "flac":
            # Reverse-lookup via flac_index: find id whose path matches
            rel_lib = str(abs_path.relative_to(SPOTIFLAC_LIB)) if SPOTIFLAC_LIB in abs_path.parents else None
            for sid, p_rel in flac_index.items():
                if p_rel == rel_lib:
                    spotify_id = sid
                    break
            if not spotify_id and tag_id:
                spotify_id = tag_id
        # MP3 (spotdl): look in audit
        # Audit-spotdl writes paths relative to SPOTDL_ROOT, so look up
        # this file's path under SPOTDL_ROOT to fetch its spotify_id.
        rel_from_spotdl = abs_path.relative_to(SPOTDL_ROOT) if SPOTDL_ROOT in abs_path.parents else None
        if not spotify_id and rel_from_spotdl:
            pair = mp3_id_by_path.get(str(rel_from_spotdl))
            if pair:
                spotify_id, spotify_duration = pair
        if spotify_id and not spotify_duration:
            spotify_duration = sp_dur.get(spotify_id)

        artist_str, title_str = parse_artist_title_for_path(rel, source)
        verified_good = (
            spotify_duration is not None and dur is not None
            and abs(dur - spotify_duration) <= TOLERANCE_VERIFY
        )
        files.append({
            "abs": str(abs_path),
            "rel": str(rel),
            "source": source,
            "source_root": str(source_root),
            "ext": ext,
            "format_rank": FORMAT_RANK.get(ext, 0),
            "bitrate": br,
            "size": abs_path.stat().st_size,
            "duration": dur,
            "spotify_id": spotify_id,
            "spotify_duration": spotify_duration,
            "verified_good": verified_good,
            "artist_n": norm(artist_str),
            "title_n": norm(title_str),
        })

    def walk(root, source, source_root=None):
        if not root.exists():
            return
        sr = source_root if source_root else root
        n = 0
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in AUDIO_EXTS:
                continue
            # Skip our own quarantine
            if any(part.startswith("_dedup_quarantine") for part in p.parts):
                continue
            if any(part.startswith("_quarantine") for part in p.parts):
                continue
            add_file(p, source, sr)
            n += 1
        print(f"  source {source}: {n} files (root {root})", file=sys.stderr)

    print("Inventory…", file=sys.stderr)
    walk(SPOTIFLAC_LIB, "spotiflac_library", SPOTIFLAC_LIB)
    walk(SPOTDL_LIKED, "spotdl_liked", SPOTDL_LIKED)
    walk(SPOTDL_PLAYLISTS, "spotdl_playlist", SPOTDL_PLAYLISTS)
    # Third-party top-level artist dirs (e.g. Lidarr) under MUSIC_ROOT —
    # excluding anything that contains (or is) our spotiflac/spotdl roots,
    # which we already walked above.
    if MUSIC.exists():
        exclude = set()
        for managed in (LIBRARY_DIR, SPOTDL_LIKED, SPOTDL_PLAYLISTS):
            try:
                first_seg = managed.resolve().relative_to(MUSIC.resolve()).parts
                if first_seg:
                    exclude.add(first_seg[0])
            except (ValueError, OSError):
                pass
        for top in sorted(MUSIC.iterdir()):
            if top.is_dir() and top.name not in exclude and not top.name.startswith("_"):
                walk(top, "third_party", MUSIC)
    print(f"Total inventory: {len(files)} files", file=sys.stderr)

    # Cluster: by spotify_id, otherwise by fuzzy (artist, title) + duration
    clusters_by_id = defaultdict(list)
    no_id_files = []
    for f in files:
        if f["spotify_id"]:
            clusters_by_id[f["spotify_id"]].append(f)
        else:
            no_id_files.append(f)

    # For no_id_files: cluster by artist_n, then within artist by title fuzzy + duration tolerance
    no_id_clusters = []
    by_artist = defaultdict(list)
    for f in no_id_files:
        by_artist[f["artist_n"]].append(f)
    for art_files in by_artist.values():
        # Greedy: each file joins an existing cluster if it matches the first member; else starts new
        for f in art_files:
            placed = False
            for cluster in no_id_clusters:
                rep = cluster[0]
                if rep["artist_n"] != f["artist_n"]:
                    continue
                if f["title_n"] and rep["title_n"]:
                    ratio = SequenceMatcher(None, f["title_n"], rep["title_n"]).ratio()
                    if ratio < FUZZY_TITLE:
                        continue
                # Duration check
                if f["duration"] and rep["duration"]:
                    if abs(f["duration"] - rep["duration"]) > TOLERANCE_PAIR:
                        continue
                cluster.append(f)
                placed = True
                break
            if not placed:
                no_id_clusters.append([f])

    # Misrouted-FLAC detection: for each FLAC whose spotify_id has spotify_duration
    # but its file_duration mismatches > TOLERANCE_VERIFY, look for an MP3 in another
    # cluster with (artist_n, title_n) matching and verified-good; if found, MERGE.
    by_at_verified = defaultdict(list)
    for f in files:
        if f["verified_good"] and f["artist_n"] and f["title_n"]:
            by_at_verified[(f["artist_n"], f["title_n"])].append(f)
    for sid, group in list(clusters_by_id.items()):
        for f in group:
            if f["ext"] != "flac":
                continue
            if not f["spotify_duration"]:
                continue
            if f["duration"] is None:
                continue
            if abs(f["duration"] - f["spotify_duration"]) <= TOLERANCE_VERIFY:
                continue
            # Misrouted candidate. Find best (artist, title) match.
            for (a, t), good_files in by_at_verified.items():
                if a != f["artist_n"]:
                    continue
                if not (t in f["title_n"] or f["title_n"] in t or
                        SequenceMatcher(None, t, f["title_n"]).ratio() >= FUZZY_TITLE):
                    continue
                # Merge this FLAC into the group of good_files[0]
                target_sid = good_files[0]["spotify_id"]
                if target_sid and target_sid != sid:
                    clusters_by_id[target_sid].append(f)
                    clusters_by_id[sid].remove(f)
                else:
                    # MP3 has no spotify_id (shouldn't happen with audit); attach to no_id cluster
                    for nc in no_id_clusters:
                        if good_files[0] in nc:
                            nc.append(f)
                            clusters_by_id[sid].remove(f)
                            break
                break

    # Pick keeper from each cluster (uses module-level keeper_sort_key)
    losers = []
    summary = {
        "total_files": len(files),
        "clusters_by_spotify_id": 0,
        "clusters_by_fuzzy": 0,
        "losers": 0,
        "loser_size": 0,
        "by_format_loser": defaultdict(int),
        "by_format_keeper": defaultdict(int),
    }
    # By spotify_id clusters
    for sid, group in clusters_by_id.items():
        if len(group) <= 1:
            continue
        summary["clusters_by_spotify_id"] += 1
        ranked = sorted(group, key=keeper_sort_key)
        keeper = ranked[0]
        summary["by_format_keeper"][keeper["ext"]] += 1
        for loser in ranked[1:]:
            losers.append({
                "loser": loser,
                "keeper": keeper,
                "reason": "spotify_id_match",
            })
            summary["by_format_loser"][loser["ext"]] += 1
    # By fuzzy clusters
    for cluster in no_id_clusters:
        if len(cluster) <= 1:
            continue
        summary["clusters_by_fuzzy"] += 1
        ranked = sorted(cluster, key=keeper_sort_key)
        keeper = ranked[0]
        summary["by_format_keeper"][keeper["ext"]] += 1
        for loser in ranked[1:]:
            losers.append({
                "loser": loser,
                "keeper": keeper,
                "reason": "fuzzy_match",
            })
            summary["by_format_loser"][loser["ext"]] += 1

    summary["losers"] = len(losers)
    summary["loser_size"] = sum(l["loser"]["size"] for l in losers)
    summary["by_format_loser"] = dict(summary["by_format_loser"])
    summary["by_format_keeper"] = dict(summary["by_format_keeper"])

    print(f"\nSummary:", file=sys.stderr)
    print(f"  total files: {summary['total_files']}", file=sys.stderr)
    print(f"  spotify-id clusters with >1: {summary['clusters_by_spotify_id']}", file=sys.stderr)
    print(f"  fuzzy clusters with >1:      {summary['clusters_by_fuzzy']}", file=sys.stderr)
    print(f"  losers to quarantine:        {summary['losers']} ({summary['loser_size']/(1024**3):.2f} GB)", file=sys.stderr)
    print(f"  losers by format:            {summary['by_format_loser']}", file=sys.stderr)
    print(f"  keepers by format:           {summary['by_format_keeper']}", file=sys.stderr)

    REPORT.write_text(json.dumps({"summary": summary, "losers": losers}, indent=2, ensure_ascii=False, default=str))
    print(f"\nReport: {REPORT}", file=sys.stderr)

    if not args.do:
        print("\nDRY-RUN. Sample of first 10 dedup decisions:", file=sys.stderr)
        for entry in losers[:10]:
            l = entry["loser"]
            k = entry["keeper"]
            print(f"  KEEP  [{k['ext']:4} verified={k['verified_good']}] {k['rel'][:60]}", file=sys.stderr)
            print(f"   DEL  [{l['ext']:4} verified={l['verified_good']}] {l['rel'][:60]}  ({entry['reason']})", file=sys.stderr)
        return

    # Actually quarantine losers
    moved = 0
    by_root = defaultdict(int)
    for entry in losers:
        f = entry["loser"]
        src = Path(f["abs"])
        sr = Path(f["source_root"])
        rel = src.relative_to(sr)
        # encode path into a flat filename for restorability
        flat = str(rel).replace("/", "⁄")
        qdir = sr / "_dedup_quarantine"
        qdir.mkdir(exist_ok=True)
        dest = qdir / flat
        try:
            shutil.move(str(src), str(dest))
            moved += 1
            by_root[str(sr)] += 1
        except Exception as e:
            print(f"  ! move failed {src} → {dest}: {e}", file=sys.stderr)
    print(f"\n✓ moved {moved} files to _dedup_quarantine/ across roots:", file=sys.stderr)
    for r, n in by_root.items():
        print(f"    {n:>5}  {r}", file=sys.stderr)
    print(f"  Run migrate-to-flat.py next to refresh M3Us and the track-id-index.", file=sys.stderr)


if __name__ == "__main__":
    main()
