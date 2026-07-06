"""Regression tests for update_index()'s disk↔index reconcile.

Production incident (2026-07-06): the index entry for a track pointed at an
M4A twin; dedup quarantined that M4A (keeper = the FLAC, same track ID,
whose index entry had been overwritten earlier). The old update_index()
pruned the dead M4A entry but only ever indexed freshly-moved files — so
the surviving FLAC stayed unindexed forever and the track silently vanished
from every M3U (the library index collapsed from 3,872 to 319 entries).

update_index() must therefore reconcile: prune dead entries, then index any
on-disk file whose path is not an index value — resolving track-ID
collisions through the keeper rule.

These tests monkeypatch the module's LIB and url_tag_id so no ffprobe or
real library is needed.
"""


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def _setup(migrate, monkeypatch, tmp_path, tags):
    """Point migrate at a tmp library; url_tag_id resolves via `tags`
    (a {filename: track_id} dict keyed on the path's name)."""
    monkeypatch.setattr(migrate, "LIB", tmp_path)
    monkeypatch.setattr(migrate, "url_tag_id", lambda p: tags.get(p.name))


def test_reconcile_reindexes_orphaned_keeper(migrate, monkeypatch, tmp_path):
    # The production incident: index points at a quarantined (gone) M4A,
    # the keeper FLAC is on disk but unindexed.
    _touch(tmp_path, "Opeth/Damnation/Windowpane.flac")
    _setup(migrate, monkeypatch, tmp_path, {"Windowpane.flac": "TRACK1"})

    index = {"TRACK1": "Opeth/Damnation/Windowpane.m4a"}  # dead path
    migrate.update_index(index, moved_new=[])

    assert index == {"TRACK1": "Opeth/Damnation/Windowpane.flac"}


def test_reconcile_quarantines_lower_ranked_twin(migrate, monkeypatch, tmp_path):
    # Both twins on disk, index knows only the M4A → reconcile finds the
    # FLAC, keeper rule fires, M4A moves to _dedup_quarantine/.
    _touch(tmp_path, "A/B/track.flac")
    m4a = _touch(tmp_path, "A/B/track.m4a")
    _setup(migrate, monkeypatch, tmp_path,
           {"track.flac": "T", "track.m4a": "T"})

    index = {"T": "A/B/track.m4a"}
    migrate.update_index(index, moved_new=[])

    assert index["T"] == "A/B/track.flac"
    assert not m4a.exists()
    assert (tmp_path / "_dedup_quarantine").exists()


def test_reconcile_skips_quarantine_dirs(migrate, monkeypatch, tmp_path):
    # Files already in quarantine must never be resurrected into the index.
    _touch(tmp_path, "_dedup_quarantine/A⁄B⁄track.m4a")
    _setup(migrate, monkeypatch, tmp_path, {"A⁄B⁄track.m4a": "T"})

    index = {}
    migrate.update_index(index, moved_new=[])

    assert index == {}


def test_reconcile_prunes_dead_entries(migrate, monkeypatch, tmp_path):
    _setup(migrate, monkeypatch, tmp_path, {})
    index = {"GONE": "no/such/file.flac"}
    migrate.update_index(index, moved_new=[])
    assert index == {}


def test_reconcile_untagged_file_ignored(migrate, monkeypatch, tmp_path):
    # A file with no URL tag can't be indexed — must not crash or loop.
    _touch(tmp_path, "X/Y/untagged.m4a")
    _setup(migrate, monkeypatch, tmp_path, {})
    index = {}
    migrate.update_index(index, moved_new=[])
    assert index == {}
