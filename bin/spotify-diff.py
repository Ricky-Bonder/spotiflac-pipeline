#!/usr/bin/env python3
# Daily check: for each Spotify playlist URL, scrape the public embed page
# (no auth required) to get name + total + up to 100 track IDs. Compare to
# last-known state.
#
# Additions  -> remove playlist from done.txt so spotiflac re-runs it
#               (skip-existing means only new tracks actually download).
# Deletions  -> remove the track lines from that playlist's M3U; if the FLAC
#               isn't referenced by any other M3U, delete it too.
#
# Notes:
# - Embed caps at 100 tracks. For larger playlists we fall back to count-only
#   sync (additions only via count delta).

import html
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
from _common import STATE_DIR, LIBRARY_DIR, PLAYLISTS_DIR as _PLAYLISTS_DIR, notify

PLAYLIST_FILE = STATE_DIR / "playlists.txt"
DONE_LOG = STATE_DIR / "done.txt"
STATE_FILE = STATE_DIR / "playlist-state.json"

LIBRARY = LIBRARY_DIR
PLAYLISTS_DIR = _PLAYLISTS_DIR

UA = "Mozilla/5.0"
EMBED_CAP = 100

TRACK_URI_RE = re.compile(r"spotify:track:([A-Za-z0-9]{22})")
TITLE_RE = re.compile(r'og:title"[^>]+content="([^"]+)"')
DESC_RE = re.compile(r'og:description"[^>]+content="[^"]*·\s*(\d+)\s+items?\b')


def fetch_main(playlist_id):
    """Main playlist page: name + total from og: meta tags."""
    req = urllib.request.Request(
        f"https://open.spotify.com/playlist/{playlist_id}",
        headers={"User-Agent": UA},
    )
    body = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
    name_m = TITLE_RE.search(body)
    desc_m = DESC_RE.search(body)
    return (html.unescape(name_m.group(1)) if name_m else "?",
            int(desc_m.group(1)) if desc_m else None)


def fetch_embed_tracks(playlist_id):
    """Embed page: returns (raw_ids_with_dupes, unique_ids). Caps at ~100 entries."""
    req = urllib.request.Request(
        f"https://open.spotify.com/embed/playlist/{playlist_id}",
        headers={"User-Agent": UA},
    )
    body = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
    m = re.search(r"<script[^>]*__NEXT_DATA__[^>]*>(.+?)</script>", body, re.S)
    if not m:
        return [], []
    try:
        data = json.loads(m.group(1))
        tl = data["props"]["pageProps"]["state"]["data"]["entity"]["trackList"]
    except (KeyError, ValueError):
        return [], []
    raw_ids = [t["uri"].split(":")[-1] for t in tl if t.get("uri", "").startswith("spotify:track:")]
    return raw_ids, list(dict.fromkeys(raw_ids))


def fetch_playlist(playlist_id):
    """Combine: main URL → name+total, embed URL → track IDs (if total <= cap)."""
    name, total = fetch_main(playlist_id)
    # Embed caps around 100; we get all *available* tracks below that. Spotify's
    # reported total may include unavailable/region-locked tracks, so we don't
    # require an exact match — just that we didn't hit the cap.
    if total is not None and total > EMBED_CAP:
        return {"name": name, "total": total, "track_ids": [], "complete": False}
    raw_ids, unique_ids = fetch_embed_tracks(playlist_id)
    complete = len(raw_ids) < EMBED_CAP  # we got everything embed serves
    return {"name": name, "total": total, "track_ids": unique_ids, "complete": complete}


def send_telegram(text):
    # Delegates to the shared notifier in _common.py.
    notify(text)


def ffprobe_url_tag(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=URL", "-of", "default=nokey=0:noprint_wrappers=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        m = re.search(r"URL=(\S+)", r.stdout)
        if m:
            tid = re.search(r"/track/([A-Za-z0-9]+)", m.group(1))
            if tid:
                return tid.group(1)
    except Exception:
        pass
    return None


def build_indexes():
    """Return (id_to_path, path_to_playlists)."""
    id_to_path = {}
    for flac in LIBRARY.rglob("*.flac"):
        tid = ffprobe_url_tag(flac)
        if tid:
            id_to_path[tid] = flac
    path_to_playlists = defaultdict(set)
    for m3u in PLAYLISTS_DIR.glob("*.m3u8"):
        for line in m3u.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            # paths look like "../_library/Artist/Album/Title.flac"
            rel = line.replace("../_library/", "", 1)
            path_to_playlists[rel].add(m3u.stem)
    return id_to_path, path_to_playlists


def rewrite_m3u(m3u_path, rel_paths_to_remove):
    if not m3u_path.exists():
        return 0
    removed = 0
    kept = []
    for line in m3u_path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            kept.append(line)
            continue
        rel = s.replace("../_library/", "", 1)
        if rel in rel_paths_to_remove:
            removed += 1
            continue
        kept.append(line)
    m3u_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed


def regenerate_all_tracks(path_to_playlists):
    """Rebuild All Tracks.m3u8 as the union of every other M3U."""
    union = set()
    for path, pls in path_to_playlists.items():
        if any(p != "All Tracks" for p in pls):
            union.add(path)
    m3u = PLAYLISTS_DIR / "All Tracks.m3u8"
    with open(m3u, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for rel in sorted(union):
            f.write(f"../_library/{rel}\n")


def main():
    if not PLAYLIST_FILE.exists():
        sys.exit(f"missing: {PLAYLIST_FILE}")

    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            state = {}

    done_ids = set()
    if DONE_LOG.exists():
        done_ids = {ln.strip() for ln in DONE_LOG.read_text().splitlines() if ln.strip()}

    pids = []
    for line in PLAYLIST_FILE.read_text().splitlines():
        m = re.search(r"https://open\.spotify\.com/playlist/([A-Za-z0-9]+)", line)
        if m:
            pids.append(m.group(1))

    # Per-playlist diff
    additions = []     # (pid, name, count_added)
    deletions = []     # (pid, name, removed_ids)
    errors = []
    new_state = {}
    over_cap = []      # playlists too big for embed
    for pid in pids:
        try:
            info = fetch_playlist(pid)
        except Exception as e:
            errors.append(f"{pid}: {e}")
            new_state[pid] = state.get(pid, {})
            continue
        prev = state.get(pid, {})
        prev_ids = set(prev.get("track_ids", []))
        cur_ids = set(info["track_ids"])

        if info["complete"]:
            added = cur_ids - prev_ids
            removed = prev_ids - cur_ids
            if removed and prev.get("complete"):
                deletions.append((pid, info["name"], removed))
            if added and prev_ids and prev.get("complete"):
                additions.append((pid, info["name"], len(added)))
            elif not prev_ids:
                pass  # first run, nothing to compare
        else:
            over_cap.append(pid)
            # fallback to count-only
            prev_total = prev.get("total")
            if prev_total is not None and info["total"] is not None and info["total"] > prev_total:
                additions.append((pid, info["name"], info["total"] - prev_total))

        new_state[pid] = {
            "name": info["name"],
            "total": info["total"],
            "track_ids": info["track_ids"],
            "complete": info["complete"],
        }
        time.sleep(0.3)

    STATE_FILE.write_text(json.dumps(new_state, indent=2, ensure_ascii=False))

    # Apply additions: unmark from done.txt
    if additions:
        for pid, _, _ in additions:
            done_ids.discard(pid)
        DONE_LOG.write_text("\n".join(sorted(done_ids)) + ("\n" if done_ids else ""))

    # Apply deletions: rewrite M3Us, possibly delete files
    deleted_files = 0
    deleted_m3u_lines = 0
    if deletions:
        id_to_path, path_to_playlists = build_indexes()
        for pid, name, removed_ids in deletions:
            m3u_name = name.replace("/", "_")
            m3u_path = PLAYLISTS_DIR / f"{m3u_name}.m3u8"
            rel_paths = set()
            for rid in removed_ids:
                fp = id_to_path.get(rid)
                if fp:
                    rel = str(fp.relative_to(LIBRARY))
                    rel_paths.add(rel)
            deleted_m3u_lines += rewrite_m3u(m3u_path, rel_paths)
            # After removing from this M3U, update path_to_playlists
            for rel in rel_paths:
                path_to_playlists.get(rel, set()).discard(m3u_name)

        # Now delete files no longer referenced by any non-"All Tracks" M3U
        for rel, pls in list(path_to_playlists.items()):
            real_pls = pls - {"All Tracks"}
            if not real_pls:
                full = LIBRARY / rel
                if full.exists():
                    full.unlink()
                    deleted_files += 1
                path_to_playlists.pop(rel, None)
        regenerate_all_tracks(path_to_playlists)

    # Report
    lines = []
    if additions:
        lines.append(f"🆕 {len(additions)} playlist(s) grew:")
        for _, name, n in additions[:8]:
            lines.append(f"  + {name}: +{n}")
        if len(additions) > 8:
            lines.append(f"  …and {len(additions)-8} more")
    if deletions:
        total_removed = sum(len(r) for _, _, r in deletions)
        lines.append(f"➖ {len(deletions)} playlist(s) lost {total_removed} track(s):")
        for _, name, removed in deletions[:8]:
            lines.append(f"  − {name}: -{len(removed)}")
        if deletions and (deleted_files or deleted_m3u_lines):
            lines.append(f"  cleaned: {deleted_m3u_lines} M3U lines, {deleted_files} orphaned FLAC(s)")
    if errors:
        lines.append(f"⚠️ {len(errors)} scrape error(s)")
    if over_cap:
        lines.append(f"ℹ️  {len(over_cap)} playlist(s) > {EMBED_CAP} tracks → additions-only (count delta)")
    if not lines:
        msg = f"SpotiFLAC diff: no changes ({len(pids)} playlists, {sum(p.get('total') or 0 for p in new_state.values())} tracks total)"
    else:
        msg = "SpotiFLAC diff:\n" + "\n".join(lines)
    print(msg)
    if additions or deletions:
        send_telegram(msg)

    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  ! {e}")


if __name__ == "__main__":
    main()
