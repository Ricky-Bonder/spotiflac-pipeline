"""Tests for audit-spotdl.py's fuzzy matching logic.

The audit pass takes spotdl MP3 filenames ("Artist - Title.mp3") and tries
to match them against a known-good track list. The match rule is:

  (a) any track-artist name appears (normalized) in EITHER the file artist
      field OR the file title field
  (b) the track title has >= FUZZY_TITLE normalized fuzzy overlap with the
      file title

This file pins those rules so that future refactors don't silently change
them.
"""


# ─── norm() ─────────────────────────────────────────────────────────────────

def test_norm_lowercases(audit):
    assert audit.norm("Hello World") == "hello world"


def test_norm_strips_feat_parens(audit):
    # spotdl-style "(feat. X)" should be removed
    assert audit.norm("Track Name (feat. Some Artist)") == "track name"


def test_norm_strips_feat_brackets(audit):
    assert audit.norm("Track Name [feat. Some Artist]") == "track name"


def test_norm_strips_ft_dot(audit):
    assert audit.norm("Track Name ft. Other") == "track name"


def test_norm_collapses_whitespace(audit):
    assert audit.norm("a   b\t\tc") == "a b c"


def test_norm_strips_punctuation(audit):
    assert audit.norm("Don't Stop Me Now!") == "don t stop me now"


def test_norm_empty_input(audit):
    assert audit.norm("") == ""
    assert audit.norm(None) == ""


# ─── fname_parse() ───────────────────────────────────────────────────────────

def test_fname_parse_basic_spotdl(audit):
    # spotdl filename convention: "Artist - Title"
    assert audit.fname_parse("Queen - Bohemian Rhapsody") == ("Queen", "Bohemian Rhapsody")


def test_fname_parse_multiple_dashes(audit):
    # Dashes in the title shouldn't confuse us — split on the FIRST " - "
    artist, title = audit.fname_parse("Artist - Title - With - Dashes")
    assert artist == "Artist"
    assert title == "Title - With - Dashes"


def test_fname_parse_no_dash(audit):
    # No dash → all of it is treated as the title, artist empty
    assert audit.fname_parse("JustATrackName") == ("", "JustATrackName")


def test_fname_parse_dash_inside_word(audit):
    # "x-y" without surrounding spaces isn't a separator
    assert audit.fname_parse("Mr.Self-Destruct") == ("", "Mr.Self-Destruct")


# ─── find_match() ────────────────────────────────────────────────────────────

def _indexed_track(track_id, name, artist_names):
    """Build a minimal track dict shaped like the spotdl JSON export."""
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": a} for a in artist_names],
        "duration_ms": 180_000,
        "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
    }


def test_find_match_artist_in_artist_field(audit):
    t = _indexed_track("abc", "Bohemian Rhapsody", ["Queen"])
    track_lookup = [(t, "My Playlist")]
    indexed = [t]
    match, playlists = audit.find_match(
        file_artist="Queen", file_title="Bohemian Rhapsody",
        track_lookup=track_lookup, indexed=indexed,
    )
    assert match is t
    assert playlists == ["My Playlist"]


def test_find_match_artist_in_title_field(audit):
    # spotdl sometimes writes "Track Name" with artist baked into the title.
    # We should still match if the artist appears anywhere.
    t = _indexed_track("abc", "Bohemian Rhapsody", ["Queen"])
    match, _ = audit.find_match(
        file_artist="", file_title="Bohemian Rhapsody Queen",
        track_lookup=[(t, "P")], indexed=[t],
    )
    assert match is t


def test_find_match_rejects_wrong_artist(audit):
    t = _indexed_track("abc", "Bohemian Rhapsody", ["Queen"])
    match, _ = audit.find_match(
        file_artist="Beatles", file_title="Bohemian Rhapsody",
        track_lookup=[(t, "P")], indexed=[t],
    )
    assert match is None


def test_find_match_rejects_low_fuzzy_title(audit):
    # Artist matches but title is way off — should fail the 0.80 threshold
    t = _indexed_track("abc", "Bohemian Rhapsody", ["Queen"])
    match, _ = audit.find_match(
        file_artist="Queen", file_title="Killer Queen",
        track_lookup=[(t, "P")], indexed=[t],
    )
    assert match is None


def test_find_match_substring_boost(audit):
    # File title is the canonical title PLUS extra annotation ("official audio")
    # — the substring boost should accept this.
    t = _indexed_track("abc", "Tidecaller", ["Some Artist"])
    match, _ = audit.find_match(
        file_artist="Some Artist", file_title="Tidecaller [official audio]",
        track_lookup=[(t, "P")], indexed=[t],
    )
    assert match is t


def test_find_match_returns_all_playlists(audit):
    # The same track can appear in multiple playlists — find_match should
    # return all of them
    t = _indexed_track("abc", "Bohemian Rhapsody", ["Queen"])
    track_lookup = [(t, "P1"), (t, "P2"), (t, "P3")]
    match, playlists = audit.find_match(
        file_artist="Queen", file_title="Bohemian Rhapsody",
        track_lookup=track_lookup, indexed=[t],
    )
    assert match is t
    assert sorted(playlists) == ["P1", "P2", "P3"]
