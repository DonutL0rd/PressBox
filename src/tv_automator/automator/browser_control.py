"""Browser controller — manages a Chrome instance on the display."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from playwright.async_api import async_playwright, Playwright, Browser, Page

if TYPE_CHECKING:
    from tv_automator.config import Config

log = logging.getLogger(__name__)


class BrowserController:
    """Launches and controls a Chrome window on the X11 display."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._fullscreened: bool = False

    async def start(self) -> None:
        log.info("Starting browser controller...")
        self._playwright = await async_playwright().start()

        res = self._config.display.get("resolution", "1920x1080")
        args = list(self._config.chrome_args) + [
            "--no-sandbox",
            "--disable-gpu-sandbox",
            "--start-fullscreen",
            f"--window-size={res.replace('x', ',')}",
            "--window-position=0,0",
            # ── Performance: prefer GPU decode, reduce CPU pressure ──
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--ignore-gpu-blocklist",
            "--enable-features=VaapiVideoDecoder,VaapiVideoEncoder",
            "--disable-features=UseChromeOSDirectVideoDecoder",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
        ]

        launch_kwargs = {
            "args": args,
            "ignore_default_args": ["--enable-automation"],
            "headless": False,
        }

        chrome_path = self._config.browser.get("chrome_path")
        if chrome_path:
            launch_kwargs["executable_path"] = chrome_path

        try:
            self._browser = await self._playwright.chromium.launch(
                channel="chrome", **launch_kwargs,
            )
            log.info("Launched Google Chrome")
        except Exception:
            log.warning("Chrome not found, falling back to Chromium")
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            log.info("Launched Chromium")

        # Monitor for unexpected disconnects
        self._browser.on("disconnected", lambda: log.error("Browser disconnected unexpectedly"))

        log.info("Browser controller ready")

    async def stop(self) -> None:
        log.info("Stopping browser controller...")
        for obj_name in ("_page", "_browser", "_playwright"):
            obj = getattr(self, obj_name, None)
            if obj is None:
                continue
            try:
                if hasattr(obj, "close"):
                    await obj.close()
                elif hasattr(obj, "stop"):
                    await obj.stop()
            except Exception:
                pass
            setattr(self, obj_name, None)
        log.info("Browser controller stopped")

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    @property
    def is_healthy(self) -> bool:
        """Check if Chrome is still connected and responsive."""
        if not self.is_running:
            return False
        # If we have a page, check it isn't closed
        if self._page and self._page.is_closed():
            return False
        return True

    async def restart(self) -> bool:
        """Tear down and relaunch Chrome. Returns True on success."""
        log.info("Restarting browser...")
        self._fullscreened = False
        await self.stop()
        try:
            await self.start()
            log.info("Browser restarted successfully")
            return True
        except Exception:
            log.exception("Browser restart failed")
            return False

    async def navigate(self, url: str) -> bool:
        """Navigate Chrome to a URL and bring the window to the foreground."""
        if not self.is_running:
            log.error("Browser not running — cannot navigate")
            return False

        try:
            page = await self._ensure_page()
            log.info("Navigating to: %s", url[:120])
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self._raise_window(page)
            return True
        except Exception:
            log.exception("Navigation failed")
            # Page may be broken — clear it so next navigate creates a fresh one
            self._page = None
            return False

    async def evaluate(self, expression: str):
        """Run a JavaScript expression in the current page and return the result."""
        page = self._page
        if not page or page.is_closed():
            return None
        try:
            return await page.evaluate(expression)
        except Exception:
            log.debug("evaluate failed: %s", expression[:100])
            return None

    async def stop_playback(self) -> None:
        if self._page and not self._page.is_closed():
            try:
                await self._page.goto("about:blank")
            except Exception:
                pass
            log.info("Playback stopped")

    @property
    def current_url(self) -> str | None:
        if self._page and not self._page.is_closed():
            url = self._page.url
            return url if url != "about:blank" else None
        return None

    # ── Internals ───────────────────────────────────────────────

    async def _ensure_page(self) -> Page:
        """Get the active page, creating one if needed."""
        if self._page and not self._page.is_closed():
            return self._page

        if not self._browser:
            raise RuntimeError("Browser not started")

        context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else await self._browser.new_context(no_viewport=True)
        )
        self._page = await context.new_page()
        self._page.on("console", lambda msg: log.info("BROWSER: [%s] %s", msg.type, msg.text))
        self._page.on("pageerror", lambda exc: log.error("BROWSER PAGE ERROR: %s", exc))
        return self._page

    async def _raise_window(self, page: Page) -> None:
        """Bring Chrome to the foreground and make it fullscreen."""
        try:
            await page.bring_to_front()
        except Exception:
            pass

        # CDP: set the OS window to fullscreen
        if not self._fullscreened:
            try:
                cdp = await page.context.new_cdp_session(page)
                result = await cdp.send("Browser.getWindowForTarget")
                window_id = result["windowId"]
                await cdp.send("Browser.setWindowBounds", {
                    "windowId": window_id,
                    "bounds": {"windowState": "fullscreen"},
                })
                self._fullscreened = True
                log.info("Set window to fullscreen via CDP")
            except Exception:
                log.exception("CDP fullscreen failed")

        # xdotool: raise Chrome to the top of the X11 window stack.
        # bring_to_front() only promotes the tab within Chrome; it does not
        # raise the Chrome window above the desktop in the window manager.
        await self._xdotool_raise()

        # JS Fullscreen API — Playwright bypasses the user-gesture requirement
        try:
            await page.evaluate(
                "document.fullscreenElement || document.documentElement.requestFullscreen().catch(()=>{})"
            )
        except Exception:
            pass

    async def _xdotool_raise(self) -> None:
        """Find the Chrome/Chromium window via xdotool and raise it."""
        env = {**os.environ}
        for cls in ("Google-chrome", "google-chrome", "Chromium", "chromium"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "xdotool", "search", "--class", cls,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=env,
                )
                try:
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                    continue
                wids = stdout.decode().strip().splitlines()
                if not wids:
                    continue
                wid = wids[-1]  # most recently opened window
                for action in ("windowraise", "windowactivate"):
                    p = await asyncio.create_subprocess_exec(
                        "xdotool", action, wid,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                        env=env,
                    )
                    await asyncio.wait_for(p.wait(), timeout=2)
                log.info("xdotool raised Chrome window (wid=%s, class=%s)", wid, cls)
                return
            except Exception:
                pass
        log.debug("xdotool: no Chrome window found to raise")

