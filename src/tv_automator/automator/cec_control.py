"""HDMI-CEC controller — power the TV on/off via cec-client."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


class CECController:
    """Controls TV power via HDMI-CEC."""

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled
        self._available: bool | None = None  # lazily checked

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def is_available(self) -> bool:
        """Check if cec-client is installed and a CEC adapter is present."""
        if self._available is not None:
            return self._available
        try:
            proc = await asyncio.create_subprocess_exec(
                "cec-client", "--list-devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            self._available = proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError):
            self._available = False
        if not self._available:
            log.warning("CEC not available — cec-client not found or no adapter")
        return self._available

    async def power_on(self) -> bool:
        """Power on the TV (address 0 = TV)."""
        if not self._enabled:
            return False
        return await self._send_command("on 0", "TV power on")

    async def power_off(self) -> bool:
        """Put the TV in standby (address 0 = TV)."""
        if not self._enabled:
            return False
        return await self._send_command("standby 0", "TV standby")

    async def set_active_source(self) -> bool:
        """Set this device as the active HDMI source."""
        if not self._enabled:
            return False
        return await self._send_command("as", "Set active source")

    async def _send_command(self, command: str, description: str) -> bool:
        """Send a CEC command via cec-client."""
        try:
            proc = await asyncio.create_subprocess_shell(
                f'echo "{command}" | cec-client -s -d 1',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            ok = proc.returncode == 0
            if ok:
                log.info("CEC: %s", description)
            else:
                log.warning("CEC command failed: %s", description)
            return ok
        except asyncio.TimeoutError:
            log.warning("CEC command timed out: %s", description)
            return False
        except Exception:
            log.exception("CEC command error: %s", description)
            return False
