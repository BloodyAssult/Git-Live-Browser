from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import WebSocket
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Download

from .downloads import unique_path, file_info, upload_to_release, list_downloads


class ClientState:
    """Single-writer queue for each websocket.

    Frames are lossy by design: if the client/browser/network is behind, old frames
    are dropped and the newest frame wins. JSON/status messages are preserved as
    much as possible, but may evict stale frames when the queue is full.
    """

    def __init__(self, ws: WebSocket, on_dead) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[tuple[str, str | bytes]] = asyncio.Queue(maxsize=6)
        self.on_dead = on_dead
        self.task = asyncio.create_task(self._writer())
        self.dropped_frames = 0

    async def _writer(self) -> None:
        try:
            while True:
                kind, payload = await self.queue.get()
                if kind == "bytes":
                    await self.ws.send_bytes(payload)  # type: ignore[arg-type]
                else:
                    await self.ws.send_text(payload)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            pass
        except Exception:
            self.on_dead(self.ws)

    def close(self) -> None:
        self.task.cancel()

    def _drop_one_frame_if_possible(self) -> bool:
        if self.queue.empty():
            return False
        items: list[tuple[str, str | bytes]] = []
        dropped = False
        while not self.queue.empty():
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not dropped and item[0] == "bytes":
                dropped = True
                self.dropped_frames += 1
                continue
            items.append(item)
        for item in items:
            try:
                self.queue.put_nowait(item)
            except asyncio.QueueFull:
                break
        return dropped

    def enqueue_frame(self, data: bytes) -> None:
        try:
            self.queue.put_nowait(("bytes", data))
        except asyncio.QueueFull:
            if self._drop_one_frame_if_possible():
                try:
                    self.queue.put_nowait(("bytes", data))
                    return
                except asyncio.QueueFull:
                    pass
            self.dropped_frames += 1

    def enqueue_json(self, payload: Dict[str, Any]) -> None:
        msg = json.dumps(payload, ensure_ascii=False)
        try:
            self.queue.put_nowait(("text", msg))
        except asyncio.QueueFull:
            self._drop_one_frame_if_possible()
            try:
                self.queue.put_nowait(("text", msg))
            except asyncio.QueueFull:
                # Last resort: drop the oldest message and insert this status/update.
                try:
                    self.queue.get_nowait()
                    self.queue.put_nowait(("text", msg))
                except Exception:
                    pass


class LiveBrowser:
    def __init__(self) -> None:
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.cdp: Any = None
        self.screencast_running = False
        self.clients: Dict[WebSocket, ClientState] = {}
        self.fallback_task: Optional[asyncio.Task] = None
        self.lock = asyncio.Lock()
        self.viewport = {"width": 1365, "height": 768}
        self.quality = 88
        self.frame_interval = 0.10  # fallback screenshot loop only
        self.every_nth_frame = 1
        self.last_url = "about:blank"
        self.downloads: List[Dict[str, Any]] = []
        self.started_at = time.time()
        self.stream_mode = "cdp-screencast"
        self.frame_count = 0
        self.last_frame_ts = 0.0
        self._last_stats_ts = 0.0

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
                "--autoplay-policy=no-user-gesture-required",
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

        # CDP screencast is Chromium-only, which is fine because we launch Chromium.
        # If this fails for any reason, we fall back to the old screenshot loop.
        try:
            self.cdp = await self.context.new_cdp_session(self.page)
            self.cdp.on("Page.screencastFrame", lambda p: asyncio.create_task(self._on_screencast_frame(p)))
            await self.cdp.send("Page.enable")
            self.stream_mode = "cdp-screencast"
        except Exception as e:
            self.cdp = None
            self.stream_mode = "screenshot-fallback"
            await self.broadcast_json({"type": "status", "level": "warn", "message": f"CDP unavailable; fallback screenshot loop: {e}"})

    async def stop(self) -> None:
        await self._stop_stream()
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
        self.cdp = None

    async def add_client(self, ws: WebSocket) -> None:
        self.clients[ws] = ClientState(ws, self.remove_client)
        self.clients[ws].enqueue_json({"type": "hello", "viewport": self.viewport, "url": self.last_url, "mode": self.stream_mode})
        self.clients[ws].enqueue_json({"type": "downloads", "items": self.downloads or list_downloads()})
        await self.start()
        await self._ensure_stream()

    def remove_client(self, ws: WebSocket) -> None:
        state = self.clients.pop(ws, None)
        if state:
            state.close()
        if not self.clients:
            # Stop pushing frames when nobody is watching. Do it asynchronously so the
            # websocket disconnect path stays simple.
            asyncio.create_task(self._stop_stream())

    async def _send_json(self, ws: WebSocket, payload: Dict[str, Any]) -> None:
        state = self.clients.get(ws)
        if state:
            state.enqueue_json(payload)

    async def broadcast_json(self, payload: Dict[str, Any]) -> None:
        for state in list(self.clients.values()):
            state.enqueue_json(payload)

    async def broadcast_frame(self, data: bytes) -> None:
        if not self.clients:
            return
        for state in list(self.clients.values()):
            state.enqueue_frame(data)

    def _compute_every_nth_frame(self) -> int:
        # CDP's everyNthFrame is a browser-side throttle. The UI calls it "Delay"
        # for simplicity. Low values keep it live; high values reduce CPU/bandwidth.
        ms = int(self.frame_interval * 1000)
        if ms <= 80:
            return 1
        if ms <= 150:
            return 2
        if ms <= 250:
            return 3
        return 4

    async def _ensure_stream(self) -> None:
        await self.start()
        if self.cdp:
            if not self.screencast_running:
                self.every_nth_frame = self._compute_every_nth_frame()
                await self.cdp.send("Page.startScreencast", {
                    "format": "jpeg",
                    "quality": int(self.quality),
                    "maxWidth": int(self.viewport["width"]),
                    "maxHeight": int(self.viewport["height"]),
                    "everyNthFrame": int(self.every_nth_frame),
                })
                self.screencast_running = True
                self.stream_mode = "cdp-screencast"
                await self.broadcast_json({"type": "status", "level": "ok", "message": f"CDP screencast live · q={self.quality} · nth={self.every_nth_frame}"})
        else:
            if not self.fallback_task or self.fallback_task.done():
                self.stream_mode = "screenshot-fallback"
                self.fallback_task = asyncio.create_task(self._fallback_frame_loop())

    async def _stop_stream(self) -> None:
        if self.fallback_task:
            self.fallback_task.cancel()
            self.fallback_task = None
        if self.cdp and self.screencast_running:
            try:
                await self.cdp.send("Page.stopScreencast")
            except Exception:
                pass
        self.screencast_running = False

    async def _restart_stream(self) -> None:
        await self._stop_stream()
        if self.clients:
            await self._ensure_stream()

    async def _on_screencast_frame(self, params: Dict[str, Any]) -> None:
        session_id = params.get("sessionId")
        cdp = self.cdp
        # Ack first. CDP will not keep sending smoothly if frames stay unacked.
        if cdp is not None and session_id is not None:
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                pass
        if not self.clients:
            return
        try:
            data = base64.b64decode(params.get("data", ""))
            self.frame_count += 1
            self.last_frame_ts = time.time()
            await self.broadcast_frame(data)
            now = time.time()
            if now - self._last_stats_ts > 3:
                self._last_stats_ts = now
                dropped = sum(c.dropped_frames for c in self.clients.values())
                await self.broadcast_json({
                    "type": "stream_stats",
                    "mode": self.stream_mode,
                    "quality": self.quality,
                    "nth": self.every_nth_frame,
                    "frames": self.frame_count,
                    "dropped": dropped,
                })
        except Exception as e:
            await self.broadcast_json({"type": "status", "level": "warn", "message": f"screencast frame error: {e}"})

    async def _fallback_frame_loop(self) -> None:
        while self.clients:
            try:
                await self.start()
                if self.page:
                    data = await self.page.screenshot(type="jpeg", quality=self.quality, full_page=False)
                    await self.broadcast_frame(data)
                    self.frame_count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self.broadcast_json({"type": "status", "level": "warn", "message": f"frame error: {e}"})
                await asyncio.sleep(0.5)
            await asyncio.sleep(self.frame_interval)

    async def command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        await self.start()
        await self._ensure_stream()
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
                    await self.page.wait_for_timeout(250)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "click":
                    x = float(cmd.get("x_norm", 0.5)) * self.viewport["width"]
                    y = float(cmd.get("y_norm", 0.5)) * self.viewport["height"]
                    await self.page.mouse.click(x, y)
                    await self.page.wait_for_timeout(120)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "wheel":
                    dy = float(cmd.get("dy", 600))
                    await self.page.mouse.wheel(0, dy)
                    await self.page.wait_for_timeout(40)
                elif action == "type":
                    text = cmd.get("text", "")
                    await self.page.keyboard.type(text, delay=5)
                elif action == "key":
                    key = cmd.get("key", "Enter")
                    await self.page.keyboard.press(key)
                    await self.page.wait_for_timeout(120)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "back":
                    await self.page.go_back(wait_until="domcontentloaded", timeout=15000)
                    await self.page.wait_for_timeout(150)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "forward":
                    await self.page.go_forward(wait_until="domcontentloaded", timeout=15000)
                    await self.page.wait_for_timeout(150)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "reload":
                    await self.page.reload(wait_until="domcontentloaded", timeout=20000)
                    await self.page.wait_for_timeout(150)
                    self.last_url = self.page.url
                    await self.broadcast_json({"type": "url", "url": self.last_url})
                elif action == "set_quality":
                    self.quality = max(40, min(100, int(cmd.get("quality", self.quality))))
                    interval_ms = int(cmd.get("interval_ms", int(self.frame_interval * 1000)))
                    self.frame_interval = max(0.04, min(1.0, interval_ms / 1000))
                    self.every_nth_frame = self._compute_every_nth_frame()
                    await self._restart_stream()
                    await self.broadcast_json({"type": "status", "level": "ok", "message": f"Quality {self.quality}, CDP nth={self.every_nth_frame}"})
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
