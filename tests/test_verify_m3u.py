"""Tests for verify-and-cleanup.py's M3U line handling.

The M3U files have two kinds of audio entries:

  ../_library/Artist/Track.flac      ← FLAC/M4A from the spotiflac pipeline
  ../../liked/Track.mp3              ← MP3 fallback from the legacy spotdl tree
  ../../playlists/Foo/Track.mp3      ← MP3 fallback (per-playlist spotdl)

When --clean deletes a bad audio file, it rewrites every M3U with the bad
entry removed but every other line preserved verbatim. An earlier version of
load_m3u_playlists() naively stripped `../_library/` from every line before
storing — which turned MP3-fallback lines into `../../liked/Track.mp3` (no
prefix to strip), and then the rewrite re-added `../_library/` making them
`../_library/../../liked/Track.mp3` — broken. These tests pin the new
behavior so that regression can't sneak back in.
"""


# ─── m3u_line_under_library() ────────────────────────────────────────────────

def test_m3u_line_library_flac(verify):
    line = "../_library/Queen/News of the World/We Will Rock You.flac"
    assert verify.m3u_line_under_library(line) == "Queen/News of the World/We Will Rock You.flac"


def test_m3u_line_library_m4a(verify):
    line = "../_library/Justin Bell/Pillars of Eternity/Track.m4a"
    assert verify.m3u_line_under_library(line) == "Justin Bell/Pillars of Eternity/Track.m4a"


def test_m3u_line_mp3_fallback_liked(verify):
    # MP3 fallback lines from spotdl's liked/ tree shouldn't be touched by
    # --clean's deletion logic; they're not under _library/.
    line = "../../liked/Imagine Dragons - Warriors.mp3"
    assert verify.m3u_line_under_library(line) is None


def test_m3u_line_mp3_fallback_playlist(verify):
    line = "../../playlists/My Playlist/Foo - Bar.mp3"
    assert verify.m3u_line_under_library(line) is None


def test_m3u_line_comment(verify):
    # Comment / directive lines shouldn't be matched either
    assert verify.m3u_line_under_library("#EXTM3U") is None
    assert verify.m3u_line_under_library("") is None


def test_m3u_line_only_prefix_stripped_once(verify):
    # Defense against a regex/sloppy implementation: a path that contains
    # "../_library/" as a substring but doesn't start with it should not match
    line = "../../something/../_library/weird.flac"
    assert verify.m3u_line_under_library(line) is None


# ─── AUDIO_EXTS coverage ─────────────────────────────────────────────────────

def test_audio_exts_contains_flac_and_m4a(verify):
    """v0.4.0 widened verification from FLAC-only to FLAC + M4A.
    Don't let a future refactor accidentally drop M4A."""
    assert ".flac" in verify.AUDIO_EXTS
    assert ".m4a" in verify.AUDIO_EXTS
