from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .browser import LiveBrowser
from .downloads import list_downloads

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

app = FastAPI(title="Git Live Browser")
live = LiveBrowser()

app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


class Command(BaseModel):
    action: str
    url: str | None = None
    text: str | None = None
    key: str | None = None
    x_norm: float | None = None
    y_norm: float | None = None
    dy: float | None = None
    quality: int | None = None
    interval_ms: int | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/status")
async def status() -> Dict[str, Any]:
    return {
        "ok": True,
        "url": live.last_url,
        "clients": len(live.clients),
        "quality": live.quality,
        "interval_ms": int(live.frame_interval * 1000),
        "downloads": len(live.downloads or list_downloads()),
    }


@app.get("/api/downloads")
async def downloads() -> Dict[str, Any]:
    return {"items": live.downloads or list_downloads()}


@app.post("/api/command")
async def command(cmd: Command) -> JSONResponse:
    result = await live.command(cmd.model_dump(exclude_none=True))
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await live.add_client(ws)
    try:
        while True:
            data = await ws.receive_json()
            await live.command(data)
    except WebSocketDisconnect:
        live.remove_client(ws)
    except Exception as e:
        live.remove_client(ws)
        try:
            await ws.close(code=1011, reason=str(e)[:120])
        except Exception:
            pass
