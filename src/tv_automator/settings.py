"""User-configurable runtime settings for TV-Automator.

All app behavior (favorites, overlays, credentials, etc.) is stored as flat JSON
in $DATA_DIR/settings.json and managed through the web UI.

Credentials can also be set via environment variables, which take precedence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class AppSettings:
    DEFAULTS: dict[str, Any] = {
        # MLB
        "favorite_teams": [],
        "auto_start": False,
        "default_feed": "HOME",
        "mlb_username": "",
        # Scheduler
        "poll_interval": 60,
        "pre_game_minutes": 5,
        # CEC
        "cec_enabled": False,
        "cec_power_off_on_stop": True,
        # Overlay
        "strike_zone_enabled": True,
        "strike_zone_size": "medium",
        "batter_intel_enabled": True,
        "between_innings_enabled": True,
        "overlay_delay": 2,
        # Screensaver
        "screensaver_music_size": "medium",
        "screensaver_schedule_scale": 100,
        # YouTube
        "suggested_channels": {
            "UCsBjURrPoezykLs9EqgamOA": "Fireship",
            "UCYO_jab_esuFRV4b17AJtAw": "3Blue1Brown",
            "UCBJycsmduvYEL83R_U4JriQ": "MKBHD",
            "UCKelCK4ZaO6HeEI1KQjqzWA": "AI Daily Brief",
        },
        # Navidrome
        "navidrome_server_url": "",
        "navidrome_username": "",
    }

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "settings.json"
        self._data: dict[str, Any] = {
            k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
            for k, v in self.DEFAULTS.items()
        }
        self._load()

    # ── Persistence ─────────────────────────────────────────────

    def _load(self) -> None:
        try:
            saved = json.loads(self._path.read_text())
            for k, v in saved.items():
                self._data[k] = v
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2))

    # ── Read / write ─────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        if default is None:
            default = self.DEFAULTS.get(key)
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, payload: dict[str, Any]) -> None:
        self._data.update(payload)

    def public_dict(self) -> dict[str, Any]:
        """Settings safe to expose to the frontend (passwords excluded)."""
        return {k: v for k, v in self._data.items() if k not in ("mlb_password", "navidrome_password")}

    # ── Convenience properties used by backend code ──────────────

    @property
    def favorite_teams(self) -> list[str]:
        return self._data.get("favorite_teams", [])

    @property
    def auto_start(self) -> bool:
        return bool(self._data.get("auto_start", False))

    @property
    def poll_interval(self) -> int:
        return int(self._data.get("poll_interval", 60))

    @property
    def mlb_credentials(self) -> tuple[str, str] | None:
        u = os.getenv("MLB_USERNAME") or self._data.get("mlb_username", "")
        p = os.getenv("MLB_PASSWORD") or self._data.get("mlb_password", "")
        return (u, p) if u and p else None

    @property
    def navidrome_credentials(self) -> tuple[str, str, str] | None:
        url = os.getenv("NAVIDROME_URL") or self._data.get("navidrome_server_url", "")
        user = os.getenv("NAVIDROME_USERNAME") or self._data.get("navidrome_username", "")
        pw = os.getenv("NAVIDROME_PASSWORD") or self._data.get("navidrome_password", "")
        return (url, user, pw) if url and user and pw else None
