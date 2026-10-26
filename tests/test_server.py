"""Offline tests for plexamp_now_playing: a fake Plex replaces HTTP."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).resolve().parent.parent / "server.py"
    spec = importlib.util.spec_from_file_location("pnp_server", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _track(player, state="playing", album="Kind of Blue", artist="Miles Davis"):
    return {
        "type": "track", "title": "So What", "parentTitle": album,
        "grandparentTitle": artist, "parentThumb": "/library/metadata/1/thumb/1",
        "Player": {"title": player, "product": "Plexamp", "state": state},
    }


ALBUMS = [{"title": f"Album {i}", "parentTitle": f"Artist {i}", "thumb": f"/t/{i}"} for i in range(50)]


@pytest.fixture
def plex(monkeypatch):
    srv = _load()
    state = {"sessions": []}

    def fake_get(base, token, path, params=None):
        params = params or {}
        if path == "/status/sessions":
            return {"Metadata": state["sessions"]}
        if path == "/library/sections":
            return {"Directory": [{"key": "3", "type": "artist", "title": "Music"},
                                  {"key": "1", "type": "movie", "title": "Films"}]}
        if path == "/library/sections/3/all":
            start, size = int(params["X-Plex-Container-Start"]), int(params["X-Plex-Container-Size"])
            return {"totalSize": len(ALBUMS), "Metadata": ALBUMS[start:start + size]}
        raise AssertionError(path)

    monkeypatch.setattr(srv, "_get", fake_get)
    monkeypatch.setattr(srv, "art_data_uri", lambda *a, **k: "data:image/jpeg;base64,AA==")
    srv._state = state
    return srv


SETTINGS = {"plex_url": "http://plex:32400", "plex_token": "x"}


def test_demo_without_settings():
    assert _load().fetch({}, {}, ctx={})["demo"] is True


def test_matches_named_player(plex):
    plex._state["sessions"] = [_track("Living Room iPhone", album="Wrong"), _track("caldera")]
    out = plex.fetch({"player": "caldera"}, SETTINGS, ctx={})
    assert out["mode"] == "playing" and out["album"] == "Kind of Blue"
    assert out["track"] == ""  # hidden by default


def test_show_track(plex):
    plex._state["sessions"] = [_track("caldera")]
    assert plex.fetch({"player": "caldera", "show_track": True}, SETTINGS, ctx={})["track"] == "So What"


def test_paused_kept_by_default_dropped_when_disabled(plex):
    plex._state["sessions"] = [_track("caldera", state="paused")]
    assert plex.fetch({"player": "caldera"}, SETTINGS, ctx={})["mode"] == "playing"
    out = plex.fetch({"player": "caldera", "include_paused": False}, SETTINGS, ctx={})
    assert out["mode"] == "shelf"


def test_other_player_falls_back_to_shelf(plex):
    plex._state["sessions"] = [_track("Office Mac")]
    out = plex.fetch({"player": "caldera"}, SETTINGS, ctx={})
    assert out["mode"] == "shelf" and out["album"].startswith("Album ")


def test_shelf_pick_is_stable_within_bucket(plex, monkeypatch):
    monkeypatch.setattr(plex, "_bucket", lambda hours, now=None: 42)
    picks = {plex.fetch({}, SETTINGS, ctx={})["album"] for _ in range(10)}
    assert len(picks) == 1
    monkeypatch.setattr(plex, "_bucket", lambda hours, now=None: 43)
    later = {plex.fetch({}, SETTINGS, ctx={})["album"] for _ in range(10)}
    assert len(later) == 1


def test_plex_error_is_surfaced(plex, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(plex, "_get", boom)
    assert "connection refused" in plex.fetch({}, SETTINGS, ctx={})["error"]


def test_wake_hint_at_track_end_while_playing(plex, monkeypatch):
    t = _track("caldera")
    t.update(duration=240_000, viewOffset=60_000)  # 3 min left
    plex._state["sessions"] = [t]
    monkeypatch.setattr(plex.time, "time", lambda: 1000.0)
    out = plex.fetch({"player": "caldera"}, SETTINGS, ctx={})
    assert out["next_change_at"] == 1000.0 + 180 + plex.TRACK_END_MARGIN_S
    assert "_track_left_s" not in out


def test_wake_hint_is_clamped(plex):
    item = {"mode": "playing", "state": "playing"}
    assert plex.next_change_at({**item, "_track_left_s": 2}, now=0) == plex.MIN_RECHECK_S
    assert plex.next_change_at({**item, "_track_left_s": 3600}, now=0) == plex.MAX_RECHECK_S
    assert plex.next_change_at(item, now=0) == plex.MAX_RECHECK_S  # no duration known


def test_no_wake_hint_when_paused_or_idle(plex):
    plex._state["sessions"] = [_track("caldera", state="paused")]
    assert "next_change_at" not in plex.fetch({"player": "caldera"}, SETTINGS, ctx={})
    plex._state["sessions"] = []
    assert "next_change_at" not in plex.fetch({}, SETTINGS, ctx={})
