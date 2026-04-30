"""Tests for AppSettings — load/save, defaults, credential handling."""

from __future__ import annotations

import json

import pytest

from tv_automator.settings import AppSettings


def test_defaults(tmp_path):
    s = AppSettings(tmp_path)
    assert s.get("favorite_teams") == []
    assert s.get("auto_start") is False
    assert s.get("poll_interval") == 60
    assert s.get("default_feed") == "HOME"
    assert s.get("cec_enabled") is False
    assert s.get("strike_zone_enabled") is True


def test_save_and_load_round_trip(tmp_path):
    s = AppSettings(tmp_path)
    s.set("favorite_teams", ["NYY", "LAD"])
    s.set("auto_start", True)
    s.set("poll_interval", 120)
    s.save()

    s2 = AppSettings(tmp_path)
    assert s2.get("favorite_teams") == ["NYY", "LAD"]
    assert s2.get("auto_start") is True
    assert s2.get("poll_interval") == 120


def test_missing_settings_file_does_not_crash(tmp_path):
    s = AppSettings(tmp_path / "nonexistent_subdir")
    assert s.get("poll_interval") == 60


def test_malformed_json_does_not_crash(tmp_path):
    (tmp_path / "settings.json").write_text("not valid json{{{")
    s = AppSettings(tmp_path)
    assert s.get("poll_interval") == 60


def test_public_dict_excludes_mlb_password(tmp_path):
    s = AppSettings(tmp_path)
    s.update({"mlb_password": "supersecret"})
    pub = s.public_dict()
    assert "mlb_password" not in pub


def test_public_dict_excludes_navidrome_password(tmp_path):
    s = AppSettings(tmp_path)
    s.update({"navidrome_password": "alsosecret"})
    pub = s.public_dict()
    assert "navidrome_password" not in pub


def test_public_dict_includes_non_sensitive_keys(tmp_path):
    s = AppSettings(tmp_path)
    pub = s.public_dict()
    assert "favorite_teams" in pub
    assert "poll_interval" in pub
    assert "auto_start" in pub


def test_update_merges_without_losing_other_keys(tmp_path):
    s = AppSettings(tmp_path)
    s.update({"poll_interval": 120, "auto_start": True})
    assert s.get("poll_interval") == 120
    assert s.get("auto_start") is True
    assert s.get("default_feed") == "HOME"  # untouched


def test_set_then_get(tmp_path):
    s = AppSettings(tmp_path)
    s.set("overlay_delay", 5.0)
    assert s.get("overlay_delay") == 5.0


def test_get_with_explicit_default_for_unknown_key(tmp_path):
    s = AppSettings(tmp_path)
    assert s.get("completely_unknown_key", "fallback") == "fallback"


def test_mlb_credentials_from_saved_settings(tmp_path):
    s = AppSettings(tmp_path)
    s.update({"mlb_username": "user@test.com", "mlb_password": "pw123"})
    creds = s.mlb_credentials
    assert creds == ("user@test.com", "pw123")


def test_mlb_credentials_none_when_both_missing(tmp_path):
    s = AppSettings(tmp_path)
    assert s.mlb_credentials is None


def test_mlb_credentials_none_when_only_username(tmp_path):
    s = AppSettings(tmp_path)
    s.update({"mlb_username": "user@test.com"})
    assert s.mlb_credentials is None


def test_mlb_credentials_env_overrides_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_USERNAME", "envuser@test.com")
    monkeypatch.setenv("MLB_PASSWORD", "envpass")
    s = AppSettings(tmp_path)
    s.update({"mlb_username": "saved@test.com", "mlb_password": "savedpass"})
    creds = s.mlb_credentials
    assert creds == ("envuser@test.com", "envpass")


def test_navidrome_credentials_all_present(tmp_path):
    s = AppSettings(tmp_path)
    s.update({
        "navidrome_server_url": "http://localhost:4533",
        "navidrome_username": "admin",
        "navidrome_password": "navipw",
    })
    creds = s.navidrome_credentials
    assert creds == ("http://localhost:4533", "admin", "navipw")


def test_navidrome_credentials_none_when_partial(tmp_path):
    s = AppSettings(tmp_path)
    s.update({
        "navidrome_server_url": "http://localhost:4533",
        "navidrome_username": "admin",
        # missing password
    })
    assert s.navidrome_credentials is None


def test_favorite_teams_property(tmp_path):
    s = AppSettings(tmp_path)
    s.set("favorite_teams", ["NYY", "LAD"])
    assert s.favorite_teams == ["NYY", "LAD"]


def test_auto_start_property(tmp_path):
    s = AppSettings(tmp_path)
    s.set("auto_start", True)
    assert s.auto_start is True


def test_poll_interval_property(tmp_path):
    s = AppSettings(tmp_path)
    s.set("poll_interval", 90)
    assert s.poll_interval == 90


def test_saved_json_is_readable(tmp_path):
    s = AppSettings(tmp_path)
    s.update({"favorite_teams": ["NYY"], "poll_interval": 45})
    s.save()
    raw = json.loads((tmp_path / "settings.json").read_text())
    assert raw["favorite_teams"] == ["NYY"]
    assert raw["poll_interval"] == 45
