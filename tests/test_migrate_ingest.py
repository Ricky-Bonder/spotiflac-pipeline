"""Tests for migrate-to-flat.py's ingest-time keeper rule.

Re-downloading a playlist can produce the same Spotify track in a different
format than what's already in _library/ — e.g. the provider chain fell
through to YouTube (M4A) on one run and hit Deezer (FLAC) on another. Both
files carry the same TAG:URL, but their destination paths differ only by
extension, so the file-move pass keeps both. pick_keeper() resolves the
track-ID collision at index time: better format wins, the loser gets
quarantined. These tests pin that rule.
"""


def test_flac_beats_m4a_incumbent(migrate):
    # Existing M4A, new FLAC arrives → FLAC wins
    keeper, loser = migrate.pick_keeper("Opeth/Damnation/Windowpane.m4a",
                                        "Opeth/Damnation/Windowpane.flac")
    assert keeper.endswith(".flac")
    assert loser.endswith(".m4a")


def test_flac_incumbent_survives_m4a(migrate):
    # Existing FLAC, new M4A arrives → FLAC stays
    keeper, loser = migrate.pick_keeper("Opeth/Damnation/Windowpane.flac",
                                        "Opeth/Damnation/Windowpane.m4a")
    assert keeper.endswith(".flac")
    assert loser.endswith(".m4a")


def test_same_rank_keeps_incumbent(migrate):
    # Tie (m4a vs aac both rank 1) → incumbent stays, newcomer quarantined
    keeper, loser = migrate.pick_keeper("A/B/track.m4a", "A/B/track.aac")
    assert keeper == "A/B/track.m4a"
    assert loser == "A/B/track.aac"


def test_same_format_keeps_incumbent(migrate):
    keeper, loser = migrate.pick_keeper("A/B/old.flac", "A/B/new.flac")
    assert keeper == "A/B/old.flac"


def test_mp3_beats_m4a(migrate):
    keeper, loser = migrate.pick_keeper("A/B/track.m4a", "A/B/track.mp3")
    assert keeper.endswith(".mp3")


def test_rank_table_matches_dedup(migrate, dedup):
    """migrate's suffix ranks must agree with dedup-tracks.py's FORMAT_RANK —
    two disagreeing keeper rules would fight each other across runs."""
    for ext, rank in dedup.FORMAT_RANK.items():
        assert migrate._SUFFIX_RANK[f".{ext}"] == rank
