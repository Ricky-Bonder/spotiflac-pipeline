"""Regression tests for spotify-diff.py's sanity guards (0.8.1).

Production incident: a GraphQL 504 fell back to the embed scrape, which
returned an empty page. That looked like a *complete* playlist with zero
tracks, so every track was treated as deleted — the M3U was emptied and
orphaned files were unlinked. The empty list was then saved as the new
snapshot, so when the tracks reappeared the next day they were taken for a
"first run" and never re-queued (Spotify track-ID relinks were lost the
same way).

These tests drive main() with a fake fetch_playlist() against a tmp state
dir — no network, no real library.
"""
import json

import pytest

A, B, C, D = ("a" * 22, "b" * 22, "c" * 22, "d" * 22)


def _ids(n, p):
    return [f"{p}{i:021d}" for i in range(n)]


@pytest.fixture
def run_diff(diff, monkeypatch, tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "pl").mkdir()
    monkeypatch.setattr(diff, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(diff, "DONE_LOG", tmp_path / "done.txt")
    monkeypatch.setattr(diff, "PLAYLIST_FILE", tmp_path / "playlists.txt")
    monkeypatch.setattr(diff, "LIBRARY", tmp_path / "lib")
    monkeypatch.setattr(diff, "PLAYLISTS_DIR", tmp_path / "pl")
    monkeypatch.setattr(diff, "send_telegram", lambda t: None)
    monkeypatch.setattr(diff.time, "sleep", lambda s: None)

    def run(state, fake):
        diff.PLAYLIST_FILE.write_text(
            "\n".join(f"https://open.spotify.com/playlist/{p}" for p in state))
        diff.STATE_FILE.write_text(json.dumps(state))
        diff.DONE_LOG.write_text("\n".join(state) + "\n")
        monkeypatch.setattr(diff, "fetch_playlist", lambda pid: dict(fake[pid]))
        diff.main()
        return (json.loads(diff.STATE_FILE.read_text()),
                set(diff.DONE_LOG.read_text().split()))
    return run


def _pl(name, ids, complete=True, total=None):
    return {"name": name, "total": len(ids) if total is None else total,
            "track_ids": ids, "complete": complete}


def test_empty_fetch_is_rejected(run_diff, capsys):
    state, done = run_diff(
        {A: _pl("Drum", _ids(30, "A"))},
        {A: _pl("Unknown Playlist", [])})
    assert len(state[A]["track_ids"]) == 30
    assert A in done
    assert "sanity guard" in capsys.readouterr().out


def test_mass_drop_is_rejected(run_diff):
    state, done = run_diff(
        {A: _pl("Drum", _ids(30, "A"))},
        {A: _pl("Drum", _ids(3, "A"))})
    assert len(state[A]["track_ids"]) == 30
    assert A in done


def test_empty_snapshot_recovery_requeues(run_diff):
    state, done = run_diff(
        {B: _pl("Emptied", [])},
        {B: _pl("Emptied", _ids(25, "B"))})
    assert B not in done
    assert len(state[B]["track_ids"]) == 25


def test_embed_fallback_keeps_full_list(run_diff):
    state, done = run_diff(
        {C: _pl("Big", _ids(300, "C"))},
        {C: _pl("Big", [], complete=False, total=301)})
    assert len(state[C]["track_ids"]) == 300
    assert C not in done  # count grew -> re-queued


def test_relink_is_requeued(run_diff):
    state, done = run_diff(
        {D: _pl("Normal", _ids(10, "D"))},
        {D: _pl("Normal", _ids(9, "D") + ["X" * 22])})
    assert D not in done
    assert "X" * 22 in state[D]["track_ids"]


def test_unchanged_stays_done(run_diff):
    state, done = run_diff(
        {D: _pl("Normal", _ids(10, "D"))},
        {D: _pl("Normal", _ids(10, "D"))})
    assert D in done
