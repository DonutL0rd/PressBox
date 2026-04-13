"""Configuration management for TV-Automator."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "default.yaml"


class Config:
    """Layered configuration: defaults → config file → environment variables."""

    def __init__(self, config_path: Path | None = None) -> None:
        load_dotenv()

        self._data: dict[str, Any] = {}

        # Layer 1: defaults
        if DEFAULT_CONFIG_PATH.exists():
            self._data = self._load_yaml(DEFAULT_CONFIG_PATH)

        # Layer 2: user config file (explicit path)
        if config_path and config_path.exists():
            user_cfg = self._load_yaml(config_path)
            self._deep_merge(self._data, user_cfg)

        # Layer 2b: auto-load persisted user config from data dir
        auto_user_cfg = self.data_dir / "config" / "user.yaml"
        if auto_user_cfg.exists():
            self._deep_merge(self._data, self._load_yaml(auto_user_cfg))

        # Layer 3: env overrides
        self._apply_env_overrides()

    # ── Public API ──────────────────────────────────────────────

    @property
    def data_dir(self) -> Path:
        return Path(self.get("data_dir", "/data"))

    @property
    def cookie_dir(self) -> Path:
        d = self.data_dir / "cookies"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def config_dir(self) -> Path:
        d = self.data_dir / "config"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def display(self) -> dict[str, Any]:
        return self.get("display", {})

    @property
    def browser(self) -> dict[str, Any]:
        return self.get("browser", {})

    @property
    def chrome_args(self) -> list[str]:
        return self.browser.get("args", [])

    @property
    def browser_timeout(self) -> int:
        return self.browser.get("timeout", 30)

    @property
    def cec(self) -> dict[str, Any]:
        return self.get("cec", {})

    @property
    def scheduler(self) -> dict[str, Any]:
        return self.get("scheduler", {})

    @property
    def poll_interval(self) -> int:
        return self.scheduler.get("poll_interval", 60)

    @property
    def favorite_teams(self) -> list[str]:
        mlb = self.get("providers", {}).get("mlb", {})
        return mlb.get("favorite_teams", [])

    @property
    def auto_start(self) -> bool:
        mlb = self.get("providers", {}).get("mlb", {})
        return mlb.get("auto_start", False)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    # ── Persistence ─────────────────────────────────────────────

    def save_user_config(self) -> None:
        """Save current configuration (minus defaults) to user config file."""
        user_config_path = self.config_dir / "user.yaml"
        with open(user_config_path, "w") as f:
            yaml.dump(self._data, f, default_flow_style=False)

    def update(self, key: str, value: Any) -> None:
        """Update a top-level config key."""
        self._data[key] = value

    def update_nested(self, *keys: str, value: Any) -> None:
        """Update a nested config key. E.g. update_nested('providers', 'mlb', 'auto_start', value=True)."""
        d = self._data
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value

    # ── Internals ───────────────────────────────────────────────

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        with open(path) as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                Config._deep_merge(base[k], v)
            else:
                base[k] = v

    @property
    def navidrome_config(self) -> dict[str, Any]:
        return self.get("navidrome", {})

    @property
    def navidrome_credentials(self) -> tuple[str, str, str] | None:
        """Return (server_url, username, password) or None if not configured."""
        cfg = self.navidrome_config
        url = os.getenv("NAVIDROME_URL") or cfg.get("server_url", "")
        user = os.getenv("NAVIDROME_USERNAME") or cfg.get("username", "")
        pw = os.getenv("NAVIDROME_PASSWORD") or cfg.get("password", "")
        return (url, user, pw) if url and user and pw else None

    @property
    def mlb_username(self) -> str | None:
        return os.getenv("MLB_USERNAME") or self.get("providers", {}).get("mlb", {}).get("username")

    @property
    def mlb_password(self) -> str | None:
        return os.getenv("MLB_PASSWORD") or self.get("providers", {}).get("mlb", {}).get("password")

    @property
    def mlb_credentials(self) -> tuple[str, str] | None:
        u, p = self.mlb_username, self.mlb_password
        return (u, p) if u and p else None

    def _apply_env_overrides(self) -> None:
        if d := os.getenv("DATA_DIR"):
            self._data["data_dir"] = d
        if d := os.getenv("DISPLAY"):
            self._data.setdefault("display", {})["display_env"] = d
        if d := os.getenv("CHROME_PATH"):
            self._data.setdefault("browser", {})["chrome_path"] = d
