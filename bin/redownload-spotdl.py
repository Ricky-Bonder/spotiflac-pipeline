#!/usr/bin/env python3
# Re-download spotdl MP3s flagged BAD by audit-spotdl.py.
#
# For each BAD track (sorted by playlist, then by name):
#   1. Move the existing file to <dir>/_quarantine/<name>.orig.mp3
#   2. Search YouTube via yt-dlp with the title + artist + duration filter
#      and pick the candidate whose duration is closest to Spotify's. Three
#      query variants used in order: "<title> <artist> official audio",
#      "<title> <artist> audio", "<title> <artist>". Each query yields up
#      to SEARCH_LIMIT candidates; live streams and >30 min videos are
#      excluded via yt-dlp's --match-filter.
#   3. After downloading the picked candidate, ffprobe its duration. If
#      within TOLERANCE_SEC of Spotify's → success, the file lands at the
#      original path. Else move to <dir>/_quarantine/<name>.tried_aN.mp3
#      and (if more attempts remain) try the next query.
#   4. After MAX_ATTEMPTS exhausted: log spotify_id+name to the permanent
#      failures file so the next run skips it.
#
# Skipped: tracks whose Spotify ID is already in the FLAC index ($STATE_DIR/
# track-id-index.json) — they have a verified-good FLAC counterpart so the
# MP3 is redundant.
#
# State: $STATE_DIR/spotdl-redownload-state.json
#   {spotify_id: {attempts: N, succeeded: bool, last_path: "..."}}
#
# Usage: redownload-spotdl.py [--limit N] [--dry-run]

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import SPOTDL_ROOT, STATE_DIR, VENV

MUSIC = SPOTDL_ROOT
AUDIT = STATE_DIR / "spotdl-audit.json"
STATE_FILE = STATE_DIR / "spotdl-redownload-state.json"
PERM_FAIL = STATE_DIR / "spotdl-permanent-failures.txt"
FLAC_INDEX_FILE = STATE_DIR / "track-id-index.json"

YT_DLP = VENV / "bin" / "yt-dlp"
MAX_ATTEMPTS = 3
TOLERANCE_SEC = 5.0
PER_TRACK_TIMEOUT = 180


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


def search_query_templates(title, artists):
    """Return progressively-broader search query strings (no ytsearch prefix)."""
    artist = artists[0] if artists else ""
    return [
        f"{title} {artist} official audio",
        f"{title} {artist} audio",
        f"{title} {artist}",
    ]


SEARCH_LIMIT = 10  # how many YouTube results to consider per query


def yt_dlp_candidates(query):
    """Metadata-only search. Returns list of dicts: {id, duration, title, uploader}."""
    cmd = [
        str(YT_DLP),
        "--no-warnings", "--quiet",
        "--flat-playlist",
        "--print", "%(id)s|%(duration)s|%(title)s|%(uploader)s",
        "--match-filter", "duration<1800 & live_status!=is_live",
        f"ytsearch{SEARCH_LIMIT}:{query}",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("|", 3)
        if len(parts) < 2:
            continue
        try:
            dur = float(parts[1]) if parts[1] not in ("NA", "None", "") else None
        except ValueError:
            dur = None
        if dur is None:
            continue
        out.append({
            "id": parts[0],
            "duration": dur,
            "title": parts[2] if len(parts) > 2 else "",
            "uploader": parts[3] if len(parts) > 3 else "",
        })
    return out


def yt_dlp_download_id(video_id, tmp_out):
    """Download a specific YouTube video to tmp_out as mp3 320k."""
    cmd = [
        str(YT_DLP),
        "-x", "--audio-format", "mp3", "--audio-quality", "320K",
        "--no-playlist", "--no-warnings", "--quiet",
        "-o", str(tmp_out.with_suffix("")) + ".%(ext)s",
        f"https://youtu.be/{video_id}",
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=PER_TRACK_TIMEOUT, check=False)
        return tmp_out.exists()
    except subprocess.TimeoutExpired:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="process only the first N bad tracks (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would happen, don't write")
    args = ap.parse_args()

    if not AUDIT.exists():
        sys.exit(f"missing {AUDIT} — run audit-spotdl.py first")
    audit = json.loads(AUDIT.read_text())
    bad = audit["buckets"]["bad"]
    print(f"audit reports {len(bad)} BAD tracks", file=sys.stderr)

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    # Load FLAC index so we can detect "this track has a FLAC counterpart and
    # was correctly dedup'd away — skip" vs "this track has no FLAC and the
    # original is just sitting in our own _quarantine/.orig — retry".
    flac_index = {}
    flac_index_file = FLAC_INDEX_FILE
    if flac_index_file.exists():
        flac_index = json.loads(flac_index_file.read_text())

    todo = []
    skipped_dedup = 0
    skipped_done = 0
    for t in bad:
        sid = t.get("spotify_id")
        if not sid:
            continue
        s = state.get(sid, {})
        if s.get("succeeded") or s.get("attempts", 0) >= MAX_ATTEMPTS:
            skipped_done += 1
            continue
        # If a FLAC counterpart exists in the spotiflac library, the MP3 is
        # redundant — no need to redownload.
        if sid in flac_index:
            skipped_dedup += 1
            continue
        # Otherwise: queue for redownload, regardless of whether the original
        # MP3 still exists at its path. The redownload writes a fresh file at
        # the original path; the quarantined-on-first-attempt original stays
        # in _quarantine/.orig.mp3 as evidence of the original bad audio.
        todo.append(t)
    if skipped_dedup:
        print(f"skipped {skipped_dedup} bad MP3s with a FLAC counterpart (dedup'd)", file=sys.stderr)
    if skipped_done:
        print(f"skipped {skipped_done} already succeeded/exhausted", file=sys.stderr)
    if args.limit:
        todo = todo[:args.limit]
    print(f"to attempt: {len(todo)}", file=sys.stderr)

    if args.dry_run:
        for t in todo[:15]:
            print(f"  would attempt: {t['path']}", file=sys.stderr)
            print(f"    spotify: \"{t['spotify_name']}\" by {', '.join(t['spotify_artists'])} ({t['spotify_dur_s']}s)", file=sys.stderr)
        return

    succ_n = 0
    fail_n = 0
    for i, t in enumerate(todo, 1):
        sid = t["spotify_id"]
        original = MUSIC / t["path"]
        target_dir = original.parent
        quarantine_dir = target_dir / "_quarantine"
        quarantine_dir.mkdir(exist_ok=True)
        title = t["spotify_name"]
        artists = t["spotify_artists"]
        sp_dur = t["spotify_dur_s"]

        print(f"\n[{i}/{len(todo)}] {original.name}", file=sys.stderr)
        print(f"   spotify: {title} — {', '.join(artists)} ({sp_dur}s)", file=sys.stderr)

        # Quarantine original (only on first attempt)
        s = state.setdefault(sid, {"attempts": 0, "succeeded": False})
        if original.exists() and s["attempts"] == 0:
            qpath = quarantine_dir / f"{original.stem}.orig{original.suffix}"
            shutil.move(str(original), str(qpath))

        # Two-phase per attempt: (1) fetch top-N candidates metadata, (2) pick
        # the FIRST one within ±TOLERANCE_SEC of Spotify's duration, download
        # only that one. Avoids wasted downloads on wrong YouTube results.
        # Track which candidate ids we've already tried across attempts so we
        # don't re-pick the same wrong one if a later query surfaces it again.
        tried_ids = set(s.get("tried_ids", []))
        queries = search_query_templates(title, artists)
        success = False
        for attempt_idx in range(s["attempts"], MAX_ATTEMPTS):
            q = queries[min(attempt_idx, len(queries) - 1)]
            print(f"   attempt {attempt_idx + 1}: searching '{q[:80]}'", file=sys.stderr)

            candidates = yt_dlp_candidates(q)
            # Pick the untried candidate whose duration is *closest* to Spotify's
            # (within TOLERANCE_SEC). Closeness matters because two YouTube
            # uploads with the same title can have different durations (a 80s vs
            # 77s "Menu Theme" by the same composer for two different games);
            # the one matching Spotify's exact duration is the right one.
            within = sorted(
                (c for c in candidates
                 if c["id"] not in tried_ids
                 and abs(c["duration"] - sp_dur) <= TOLERANCE_SEC),
                key=lambda c: abs(c["duration"] - sp_dur),
            )
            best = within[0] if within else None
            if not best:
                near = sorted(
                    (c for c in candidates if c["id"] not in tried_ids),
                    key=lambda c: abs(c["duration"] - sp_dur),
                )[:3]
                print(f"     no match (need ≈{sp_dur}s); top 3 closest were:", file=sys.stderr)
                for c in near:
                    print(f"       {c['duration']:>6.1f}s  {c['title'][:50]}  ({c['uploader'][:25]})", file=sys.stderr)
                s["attempts"] = attempt_idx + 1
                continue

            tried_ids.add(best["id"])
            s["tried_ids"] = sorted(tried_ids)
            print(f"     candidate {best['id']}: {best['duration']:.1f}s — '{best['title'][:50]}' by {best['uploader'][:25]}", file=sys.stderr)

            tmp_out = quarantine_dir / f"_tmp_a{attempt_idx + 1}.mp3"
            if tmp_out.exists():
                tmp_out.unlink()
            ok = yt_dlp_download_id(best["id"], tmp_out)
            if not ok:
                print(f"     download failed", file=sys.stderr)
                s["attempts"] = attempt_idx + 1
                continue
            dur = file_duration(tmp_out)
            if dur is None:
                tmp_out.unlink()
                s["attempts"] = attempt_idx + 1
                continue
            diff = abs(dur - sp_dur)
            if diff <= TOLERANCE_SEC:
                shutil.move(str(tmp_out), str(original))
                s["succeeded"] = True
                s["attempts"] = attempt_idx + 1
                s["last_path"] = str(original.relative_to(MUSIC))
                print(f"     ✓ {dur:.1f}s (Δ {diff:.1f}s) → {original.name}", file=sys.stderr)
                succ_n += 1
                success = True
                break
            wrong = quarantine_dir / f"{original.stem}.tried_a{attempt_idx + 1}.mp3"
            shutil.move(str(tmp_out), str(wrong))
            print(f"     ✗ post-DL {dur:.1f}s (Δ {diff:.1f}s), kept as {wrong.name}", file=sys.stderr)
            s["attempts"] = attempt_idx + 1

        if not success and s["attempts"] >= MAX_ATTEMPTS:
            fail_n += 1
            print(f"   PERMANENT FAILURE", file=sys.stderr)
            with open(PERM_FAIL, "a", encoding="utf-8") as f:
                f.write(f"{sid}\t{title} — {', '.join(artists)}\t{t['path']}\n")

        # persist state after each track
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        time.sleep(2)

    print(f"\nDone this run: succeeded={succ_n}, permanent_failures={fail_n}", file=sys.stderr)


if __name__ == "__main__":
    main()
