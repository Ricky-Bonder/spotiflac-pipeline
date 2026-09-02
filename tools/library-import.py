#!/usr/bin/env python3
"""library-import.py [soundcloud|lidarr|tagfill] [--apply]  (2026-09-01 consolidation)

soundcloud: move import-soundcloud/<Playlist>/* into _library/<Artist>/<Playlist>/
            (artist from tag, else filename prefix), write _playlists/<Playlist>.m3u8.
            Reusable: the external sync cron calls this after each download run.
lidarr:     merge every artist dir at the music root (except import-*) into _library.
tagfill:    fill missing artist/album/title tags in _library from the file's path.
Dry-run by default; --apply executes."""
import json, re, shutil, subprocess, sys, time
from pathlib import Path

import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "bin"))
from _common import LIBRARY_DIR as _LIB, STATE_DIR as _STATE, MUSIC_ROOT as _MUSIC, PLAYLISTS_DIR as _PLAY, VENV as _VENV  # noqa: E402

MUSIC = _MUSIC
LIB = _LIB
PLAY = _PLAY
AUDIO = {".mp3",".m4a",".flac",".opus",".ogg",".webm",".wav",".aac"}
APPLY = "--apply" in sys.argv
MODE = sys.argv[1]

def san(s):
    return re.sub(r"\s+"," ",(s or "").replace("/","_").replace("\0","")).strip().rstrip(".") or "Unknown"

def tag(path, name):
    r = subprocess.run(["ffprobe","-v","quiet","-show_entries",f"format_tags={name}",
                        "-of","csv=p=0",str(path)], capture_output=True, text=True)
    return r.stdout.strip()

def soundcloud():
    moved = 0
    for root in (MUSIC/"import-soundcloud", MUSIC/"import-ytmusic"):
        if not root.exists(): continue
        for pldir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
            plname = pldir.name
            m3u = PLAY / f"{plname}.m3u8"
            entries = []
            if m3u.exists():
                entries = [l for l in m3u.read_text().splitlines() if l and not l.startswith("#")]
            for f in sorted(pldir.iterdir()):
                if f.suffix.lower() not in AUDIO or not f.is_file(): continue
                artist = san(tag(f,"artist") or tag(f,"uploader") or f.name.split(" - ")[0])
                dest = LIB / artist / san(plname) / f.name
                relref = f"../_library/{artist}/{san(plname)}/{f.name}"
                print(f"  {'MOVE' if APPLY else 'would move'}: {f.relative_to(MUSIC)} -> {dest.relative_to(LIB)}")
                if APPLY:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists(): shutil.move(str(f), str(dest))
                    else: f.unlink()
                if relref not in entries: entries.append(relref)
                moved += 1
            if APPLY and entries:
                m3u.write_text("#EXTM3U\n" + "\n".join(entries) + "\n")
                # remove the old folder-local m3u + empty dir
                old = root / f"{plname}.m3u8"
                if old.exists(): old.unlink()
                try: pldir.rmdir()
                except OSError: pass
    print(f"soundcloud/ytmusic: {moved} files (applied={APPLY})")

def lidarr():
    moved = files = 0
    for d in sorted(p for p in MUSIC.iterdir() if p.is_dir() and not p.name.startswith(("import-","@","."))
                    and p.name != "lost+found"):
        dest_artist = LIB / d.name
        n = sum(1 for f in d.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO)
        print(f"  {'MERGE' if APPLY else 'would merge'}: {d.name}/ ({n} audio files) -> _library/{d.name}/")
        files += n
        if APPLY:
            for src in list(d.rglob("*")):
                if not src.is_file(): continue
                dest = dest_artist / src.relative_to(d)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    if dest.stat().st_size == src.stat().st_size: src.unlink()
                    else:
                        alt = dest.with_name(dest.stem + " (root)" + dest.suffix)
                        shutil.move(str(src), str(alt))
                else:
                    shutil.move(str(src), str(dest))
            shutil.rmtree(d)
        moved += 1
    print(f"lidarr: {moved} artist dirs, {files} audio files (applied={APPLY})")

def tagfill():
    fixed = checked = 0
    for f in sorted(LIB.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in AUDIO: continue
        if any(part.endswith("_quarantine") for part in f.parts): continue
        checked += 1
        need = {}
        cur_artist, cur_title, cur_album = tag(f,"artist"), tag(f,"title"), tag(f,"album")
        rel = f.relative_to(LIB)
        parts = rel.parts   # Artist/Album/file
        if not cur_artist and len(parts) >= 1: need["artist"] = parts[0]
        if not cur_album and len(parts) >= 2: need["album"] = parts[1]
        if not cur_title:
            t = f.stem
            t = re.sub(r"\s+-\s+[^-]+$", "", t) if " - " in t else t
            need["title"] = t
        if not need: continue
        print(f"  {'FIX' if APPLY else 'would fix'} {rel}: {need}")
        if APPLY:
            tmp = f.with_suffix(f.suffix + ".tagtmp")
            cmd = ["ffmpeg","-v","error","-y","-i",str(f),"-c","copy"]
            for k,v in need.items(): cmd += ["-metadata", f"{k}={v}"]
            cmd += [str(tmp)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                tmp.replace(f)
                fixed += 1
            else:
                tmp.unlink(missing_ok=True)
                print(f"    ! ffmpeg failed: {r.stderr[:120]}")
    print(f"tagfill: checked={checked} fixed={fixed} (applied={APPLY})")

{"soundcloud": soundcloud, "lidarr": lidarr, "tagfill": tagfill}[MODE]()
