#!/usr/bin/env python3
# Missing-only playlist sync with a permanent-failure quarantine.
#
# The old batch driver re-invoked spotiflac on the whole playlist URL, which
# re-attempted every track on each retry (skip-existing never fires because
# migrate-to-flat.py moves files out of the download dir). This driver:
#
#   1. Enumerates the playlist's FULL track list via SpotiFLAC's own metadata
#      client (works past the 100-track embed cap).
#   2. Treats a track as present when its Spotify ID is in
#      $STATE_DIR/track-id-index.json.
#   3. Downloads ONLY the missing tracks, one spotiflac invocation per track,
#      staged in $OUTPUT_DIR/zz-incoming/ (migrate-to-flat.py ingests any
#      non-underscore top-level dir).
#   4. Counts failures per track in $STATE_DIR/unavailable.txt (TSV: id,
#      fails, last-attempt, name). At SPOTIFLAC_MAX_TRACK_FAILS (default 4)
#      a track is quarantined: skipped on future runs and no longer blocking
#      the playlist from being marked done. Delete its line to retry it.
#      A successful download removes the entry.
#
# stdout: one machine-readable line consumed by run_all.sh —
#   RESULT|total|have|missing|attempted|ok|failed|q_new|q_total|pending|name
# plus QNEW|<track name> lines for newly quarantined tracks.
# pending = missing tracks neither downloaded nor quarantined; run_all.sh
# marks the playlist done when pending == 0.
# Exit codes: 0 ok, 2 enumeration failure.
#
# Usage: fetch-missing.py <playlist_url> [--dry-run]
# Must run with the pipeline venv's python (run_all.sh does).

import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    OUTPUT_DIR, STATE_DIR, VENV, MAX_TRACK_FAILS, TRACK_TIMEOUT_S,
)

STAGING = OUTPUT_DIR / "zz-incoming"
INDEX = STATE_DIR / "track-id-index.json"
QUAR = STATE_DIR / "unavailable.txt"
LOG = STATE_DIR / "spotiflac.log"
SERVICE = __import__("os").environ.get("SPOTIFLAC_SERVICE", "deezer tidal").split()
SPOTIFLAC_BIN = VENV / "bin" / "spotiflac"


def log(msg):
    print(f"[fetch-missing] {msg}", file=sys.stderr, flush=True)


def enumerate_playlist(pid):
    from SpotiFLAC.core.spotify_metadata import SpotifyMetadataClient

    async def go():
        client = SpotifyMetadataClient(timeout_s=15)
        return await client.get_playlist_tracks_async(pid)

    r = asyncio.run(go())
    tracks = next(x for x in r if isinstance(x, list))
    name = ""
    for x in r:
        if isinstance(x, dict) and x.get("name"):
            name = x["name"]
        elif isinstance(x, str) and x and not name:
            name = x
    return name or "Unknown Playlist", tracks


def load_quarantine():
    q = {}
    if QUAR.exists():
        for line in QUAR.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and re.fullmatch(r"[A-Za-z0-9]{22}", parts[0]):
                q[parts[0]] = {
                    "fails": int(parts[1]),
                    "last": parts[2] if len(parts) > 2 else "",
                    "name": parts[3] if len(parts) > 3 else "",
                }
    return q


def save_quarantine(q):
    lines = [
        f"{tid}\t{e['fails']}\t{e['last']}\t{e['name']}"
        for tid, e in sorted(q.items(), key=lambda kv: kv[1]["name"].lower())
    ]
    QUAR.write_text("\n".join(lines) + ("\n" if lines else ""))


def main():
    url = sys.argv[1]
    dry = "--dry-run" in sys.argv
    m = re.search(r"playlist/([A-Za-z0-9]{22})", url)
    if not m:
        log(f"not a playlist url: {url}")
        sys.exit(2)
    pid = m.group(1)

    try:
        name, tracks = enumerate_playlist(pid)
    except Exception as e:
        log(f"enumeration failed for {pid}: {e}")
        sys.exit(2)
    seen, ordered = set(), []
    for t in tracks:
        if t.id and t.id not in seen:
            seen.add(t.id)
            ordered.append(t)
    if not ordered:
        log(f"enumeration returned 0 tracks for {pid} ('{name}') — refusing to proceed")
        sys.exit(2)

    have = set(json.load(open(INDEX)).keys()) if INDEX.exists() else set()
    quar = load_quarantine()
    missing = [t for t in ordered if t.id not in have]
    attempt = [t for t in missing if quar.get(t.id, {}).get("fails", 0) < MAX_TRACK_FAILS]
    pre_quarantined = len(missing) - len(attempt)
    log(f"'{name}': total={len(ordered)} have={len(ordered)-len(missing)} "
        f"missing={len(missing)} quarantined(skip)={pre_quarantined} to_attempt={len(attempt)}")

    if dry:
        for t in attempt:
            log(f"  would fetch: {t.artists} — {t.title} ({t.id})")
        print(f"RESULT|{len(ordered)}|{len(ordered)-len(missing)}|{len(missing)}|0|0|0|0|{pre_quarantined}|{len(attempt)}|{name}")
        return

    STAGING.mkdir(parents=True, exist_ok=True)
    ok = failed = q_new = 0
    qnew_names = []
    logf = open(LOG, "a")
    for i, t in enumerate(attempt, 1):
        disp = f"{t.artists} — {t.title}"
        log(f"[{i}/{len(attempt)}] fetching {disp}")
        cmd = [str(SPOTIFLAC_BIN), f"https://open.spotify.com/track/{t.id}", str(STAGING),
               "--service", *SERVICE, "--retries", "2",
               "--use-artist-subfolders", "--use-album-subfolders", "--quality", "LOSSLESS"]
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=TRACK_TIMEOUT_S)
            out = r.stdout
        except subprocess.TimeoutExpired:
            out = f"[fetch-missing] TIMEOUT after {TRACK_TIMEOUT_S}s: {disp}\n"
        logf.write(out or "")
        logf.flush()
        success = bool(re.search(r"(Successful|Completate)\s*:\s*[1-9]", out or ""))
        if success:
            ok += 1
            if t.id in quar:
                del quar[t.id]
        else:
            failed += 1
            e = quar.setdefault(t.id, {"fails": 0, "last": "", "name": disp})
            e["fails"] += 1
            e["last"] = time.strftime("%Y-%m-%d")
            e["name"] = disp
            if e["fails"] >= MAX_TRACK_FAILS:
                q_new += 1
                qnew_names.append(disp)
                log(f"  QUARANTINED after {e['fails']} fails: {disp}")
        save_quarantine(quar)
    logf.close()

    pending = sum(1 for t in missing
                  if t.id not in have and quar.get(t.id, {}).get("fails", 0) < MAX_TRACK_FAILS
                  ) - ok
    pending = max(pending, 0)
    q_total = pre_quarantined + q_new
    for n in qnew_names:
        print(f"QNEW|{n}")
    print(f"RESULT|{len(ordered)}|{len(ordered)-len(missing)}|{len(missing)}|{len(attempt)}|{ok}|{failed}|{q_new}|{q_total}|{pending}|{name}")


if __name__ == "__main__":
    main()
