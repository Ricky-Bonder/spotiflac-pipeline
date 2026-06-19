"""Tests for dedup-tracks.py's keeper-selection priority logic.

When the same track exists in multiple sources, we keep the best copy and
quarantine the rest. The priority rule is:

  1. verified_good (file duration matches Spotify's reported duration) wins
     over not-verified
  2. format rank: flac > mp3 > m4a == ogg == opus == aac
  3. bitrate: higher wins
  4. size: larger wins (last-resort tiebreak)

This file pins the rule so future refactors don't silently re-order it.
"""


def _file(rel, *, verified_good=False, ext="mp3", bitrate=128000, size=1000):
    """Build a minimal file-dict shaped like dedup-tracks.py's inventory rows."""
    return {
        "rel": rel,
        "ext": ext,
        "format_rank": {"flac": 3, "mp3": 2, "m4a": 1, "opus": 1, "ogg": 1, "aac": 1}.get(ext, 0),
        "bitrate": bitrate,
        "size": size,
        "verified_good": verified_good,
    }


def _keeper(dedup, files):
    """Sort by keeper_sort_key and return the winner."""
    return sorted(files, key=dedup.keeper_sort_key)[0]


def test_verified_good_beats_unverified_higher_format(dedup):
    # A verified MP3 should beat an unverified FLAC — verified is rule #1
    flac_unverified = _file("a.flac", ext="flac", verified_good=False)
    mp3_verified    = _file("b.mp3", ext="mp3",  verified_good=True)
    assert _keeper(dedup, [flac_unverified, mp3_verified]) is mp3_verified


def test_flac_beats_mp3_when_both_unverified(dedup):
    flac = _file("a.flac", ext="flac")
    mp3  = _file("b.mp3",  ext="mp3")
    assert _keeper(dedup, [flac, mp3]) is flac


def test_flac_beats_mp3_when_both_verified(dedup):
    flac = _file("a.flac", ext="flac", verified_good=True)
    mp3  = _file("b.mp3",  ext="mp3",  verified_good=True)
    assert _keeper(dedup, [flac, mp3]) is flac


def test_mp3_beats_m4a(dedup):
    mp3 = _file("a.mp3", ext="mp3")
    m4a = _file("b.m4a", ext="m4a")
    assert _keeper(dedup, [mp3, m4a]) is mp3


def test_bitrate_breaks_tie_same_format(dedup):
    lo = _file("low.mp3",  ext="mp3", bitrate=128000)
    hi = _file("high.mp3", ext="mp3", bitrate=320000)
    assert _keeper(dedup, [lo, hi]) is hi


def test_size_breaks_tie_same_format_same_bitrate(dedup):
    small = _file("a.mp3", ext="mp3", bitrate=192000, size=1000)
    large = _file("b.mp3", ext="mp3", bitrate=192000, size=2000)
    assert _keeper(dedup, [small, large]) is large


def test_format_beats_bitrate(dedup):
    # A low-bitrate FLAC still beats a high-bitrate MP3
    flac_lo = _file("a.flac", ext="flac", bitrate=320000)
    mp3_hi  = _file("b.mp3",  ext="mp3",  bitrate=320000)
    assert _keeper(dedup, [flac_lo, mp3_hi]) is flac_lo


def test_verified_always_wins_over_bigger_better_unverified(dedup):
    # The whole point of rule #1 — verified-good is sacred.
    # A 128kbps verified M4A still beats an unverified 320kbps FLAC.
    unverified_huge_flac = _file("a.flac", ext="flac", verified_good=False,
                                  bitrate=1000000, size=100_000_000)
    verified_small_m4a   = _file("b.m4a", ext="m4a", verified_good=True,
                                  bitrate=128000, size=1000)
    assert _keeper(dedup, [unverified_huge_flac, verified_small_m4a]) is verified_small_m4a


def test_alternative_format_ranks(dedup):
    # m4a, opus, ogg, aac all rank equally at 1 — bitrate decides between them
    m4a_lo = _file("a.m4a",  ext="m4a",  bitrate=128000)
    opus_hi = _file("b.opus", ext="opus", bitrate=192000)
    assert _keeper(dedup, [m4a_lo, opus_hi]) is opus_hi


def test_norm_shared_with_audit(dedup):
    # dedup-tracks.py has its own norm() that should behave like audit's
    assert dedup.norm("Hello World") == "hello world"
    assert dedup.norm("Track (feat. X)") == "track"
    assert dedup.norm(None) == ""
