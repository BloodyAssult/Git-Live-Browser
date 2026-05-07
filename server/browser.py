from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Download

from .downloads import unique_path, file_info, upload_to_release, list_downloads


class LiveBrowser:
    def __init__(self) -> None:
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.clients: Set[WebSocket] = set()
        self.frame_task: Optional[asyncio.Task] = None
        self.lock = asyncio.Lock()
        self.viewport = {"width": 1365, "height": 768}
        self.quality = 88
        self.frame_interval = 0.12
        self.last_url = "about:blank"
        self.downloads: List[Dict[str, Any]] = []
        self.started_at = time.time()

    async def start(self) -> None:
        if self.browser:
            return
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
            ],
        )
        self.context = await self.browser.new_context(
            viewport=self.viewport,
            device_scale_factor=1,
            accept_downloads=True,
            ignore_https_errors=True,
        )
        self.page = await self.context.new_page()
        self.page.on("download", lambda d: asyncio.create_task(self._handle_download(d)))
        await self.page.goto("about:blank")

    async def stop(self) -> None:
        if self.frame_task:
            self.frame_task.cancel()
            self.frame_task = None
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def add_client(self, ws: WebSocket) -> None:
        self.clients.add(ws)
        await self._send_json(ws, {"type": "hello", "viewport": self.viewport, "url": self.last_url})
        await self._send_json(ws, {"type": "downloads", "items": self.downloads or list_downloads()})
        if not self.frame_task or self.frame_task.done():
            self.frame_task = asyncio.create_task(self._frame_loop())

    def remove_client(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def _send_json(self, ws: WebSocket, payload: Dict[str, Any]) -> None:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def broadcast_json(self, payload: Dict[str, Any]) -> None:
        dead = []
        msg = json.dumps(payload, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove_client(ws)

    async def broadcast_frame(self, data: bytes) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove_client(ws)

    async def _frame_loop(self) -> None:
        while self.clients:
            try:
                await self.start()
                if self.page:
                    data = await self.page.screenshot(type="jpeg", quality=self.quality, full_page=False)
                    await self.broadcast_frame(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self.broadcast_json({"type": "status", "level": "warn", "message": f"frame error: {e}"})
                await asyncio.sleep(0.5)
            await asyncio.sleep(self.frame_interval)

    async def command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        await self.start()
        if not self.page:
            return {"ok": False, "error": "page not ready"}
        action = cmd.get("action")
        async with self.lock:
            try:
                if action == "goto":
                    url = (cmd.get("url") or "").strip()
                    if url and not url.startswith(("http://", "https://", "about:")):
                        url = "https://" + url
                    await self.broadcast_json({"type": "status", "level": "info", "message": f"Loading {url}"})
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await self.page.wait_for_timeout(700)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "click":
                    x = float(cmd.get("x_norm", 0.5)) * self.viewport["width"]
                    y = float(cmd.get("y_norm", 0.5)) * self.viewport["height"]
                    await self.page.mouse.click(x, y)
                    await self.page.wait_for_timeout(350)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "wheel":
                    dy = float(cmd.get("dy", 600))
                    await self.page.mouse.wheel(0, dy)
                    await self.page.wait_for_timeout(150)
                elif action == "type":
                    text = cmd.get("text", "")
                    await self.page.keyboard.type(text, delay=15)
                elif action == "key":
                    key = cmd.get("key", "Enter")
                    await self.page.keyboard.press(key)
                    await self.page.wait_for_timeout(300)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "back":
                    await self.page.go_back(wait_until="domcontentloaded", timeout=15000)
                    await self.page.wait_for_timeout(300)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "forward":
                    await self.page.go_forward(wait_until="domcontentloaded", timeout=15000)
                    await self.page.wait_for_timeout(300)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "reload":
                    await self.page.reload(wait_until="domcontentloaded", timeout=20000)
                    await self.page.wait_for_timeout(400)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "set_quality":
                    self.quality = max(40, min(100, int(cmd.get("quality", self.quality))))
                    interval_ms = int(cmd.get("interval_ms", int(self.frame_interval * 1000)))
                    self.frame_interval = max(0.05, min(1.0, interval_ms / 1000))
                    await self.broadcast_json({"type": "status", "level": "ok", "message": f"Quality {self.quality}, interval {int(self.frame_interval*1000)}ms"})
                elif action == "upload_downloads":
                    await self.upload_all_downloads()
                else:
                    return {"ok": False, "error": f"unknown action {action}"}
                return {"ok": True}
            except Exception as e:
                await self.broadcast_json({"type": "status", "level": "error", "message": str(e)})
                return {"ok": False, "error": str(e)}

    async def _handle_download(self, download: Download) -> None:
        try:
            suggested = download.suggested_filename or "download.bin"
            path = unique_path(suggested)
            await download.save_as(str(path))
            info = file_info(path, download.url)
            self.downloads.insert(0, info)
            await self.broadcast_json({"type": "downloads", "items": self.downloads})
            await self.broadcast_json({"type": "status", "level": "ok", "message": f"Downloaded: {path.name}"})
        except Exception as e:
            await self.broadcast_json({"type": "status", "level": "error", "message": f"download failed: {e}"})

    async def upload_all_downloads(self) -> None:
        for item in self.downloads:
            if item.get("uploaded"):
                continue
            path = Path(item["path"])
            if not path.exists():
                continue
            try:
                await self.broadcast_json({"type": "status", "level": "info", "message": f"Uploading {path.name} to release..."})
                result = await asyncio.to_thread(upload_to_release, path)
                item["release_url"] = result.get("browser_download_url")
                item["uploaded"] = True
                await self.broadcast_json({"type": "downloads", "items": self.downloads})
            except Exception as e:
                await self.broadcast_json({"type": "status", "level": "error", "message": f"release upload failed for {path.name}: {e}"})
