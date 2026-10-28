"""FastAPI app. Binds to localhost only (see run.py). API keys never leave the backend."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .capture import list_monitors, list_windows
from .cards import Card
from .schemas import Layout, ManualCorrection
from .service import AppService, Settings

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT / ".env")


class _RedactKeys(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key and key in str(record.getMessage()):
            record.msg = str(record.getMessage()).replace(key, "[redacted]")
            record.args = ()
        return True


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for h in logging.getLogger().handlers:
    h.addFilter(_RedactKeys())
logging.getLogger("httpx").setLevel(logging.WARNING)  # avoid logging request details

DATA_DIR = Path(os.environ.get("TABLE_ASSISTANT_DATA", ROOT / "data"))
service = AppService(ROOT, DATA_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(service.run_loop())
    yield
    task.cancel()


app = FastAPI(title="Table Assistant", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- state
@app.get("/api/health")
def health():
    return {"ok": True, "claude_configured": service.claude.has_key}


@app.get("/api/state")
def state():
    return service.full_state()


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    service.clients.add(websocket)
    await service.broadcast()
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        service.clients.discard(websocket)


@app.get("/api/preview.jpg")
def preview():
    data = service.preview_jpeg()
    if data is None:
        raise HTTPException(404, "no frame captured yet")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/region/{region_id}.png")
def region_png(region_id: str):
    crop = service.region_crop(region_id)
    if crop is None or crop.size == 0:
        raise HTTPException(404, "no crop available")
    ok, enc = cv2.imencode(".png", crop)
    return Response(enc.tobytes(), media_type="image/png", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- sources & control
@app.get("/api/sources")
def sources():
    return {"replays": service.list_replays(), "windows": list_windows(), "monitors": list_monitors(),
            "screen_capture_available": bool(list_monitors())}


class SourceSpec(BaseModel):
    type: str
    name: str | None = None
    target: dict | None = None


@app.post("/api/source")
async def set_source(spec: SourceSpec):
    try:
        service.set_source(spec.model_dump())
    except Exception as e:
        raise HTTPException(400, str(e))
    await service.broadcast()
    return service.status()


class Control(BaseModel):
    action: str


@app.post("/api/control")
async def control(c: Control):
    try:
        if c.action == "start":
            service.start()
        elif c.action == "pause":
            service.pause()
        elif c.action == "stop":
            service.stop()
        elif c.action in ("capture", "step"):
            if service.source is None:
                raise ValueError("select a source first")
            if service.mode == "stopped":
                service.start()
                service.pause()
            await service.capture_once()
        else:
            raise ValueError(f"unknown action {c.action}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await service.broadcast()
    return service.status()


# ---------------------------------------------------------------- settings & layouts
@app.get("/api/settings")
def get_settings():
    return service.settings


@app.put("/api/settings")
async def put_settings(s: Settings):
    old = service.settings
    pipeline_change = (s.layout != old.layout or s.game != old.game or s.capture.stable_frames != old.capture.stable_frames
                       or s.capture.min_confidence != old.capture.min_confidence
                       or s.capture.uncertain_after != old.capture.uncertain_after)
    service.settings = s
    service.save_settings()
    if pipeline_change:
        service.stop()
        service.event("settings", "Layout/recognition settings changed; pipeline reset (shoe history cleared)")
    else:
        service.event("settings", "Settings updated")
        await service.settings_changed()
    await service.broadcast()
    return service.settings


class ClaudeSettings(BaseModel):
    enabled: bool | None = None
    auto_explain: bool | None = None
    vision_fallback: bool | None = None
    max_requests: int | None = None
    min_interval_s: float | None = None


@app.put("/api/claude")
async def claude_settings(cs: ClaudeSettings):
    c = service.claude
    if cs.enabled is not None:
        c.enabled = cs.enabled
    if cs.auto_explain is not None:
        c.auto_explain = cs.auto_explain
    if cs.vision_fallback is not None:
        c.vision_enabled = cs.vision_fallback
    if cs.max_requests is not None:
        c.max_requests = max(0, cs.max_requests)
    if cs.min_interval_s is not None:
        c.min_interval_s = max(1.0, cs.min_interval_s)
    await service.broadcast()
    return c.status()


@app.get("/api/layouts")
def layouts():
    return service.list_layouts()


@app.get("/api/layouts/{name}")
def get_layout(name: str):
    try:
        return service.load_layout(name)
    except FileNotFoundError:
        raise HTTPException(404, "layout not found")


@app.put("/api/layouts/{name}")
async def put_layout(name: str, layout: Layout):
    if layout.name != name:
        raise HTTPException(400, "name mismatch")
    if layout.synthetic:
        raise HTTPException(400, "save a copy under a new name; synthetic layouts are read-only")
    service.save_layout(layout)
    if service.settings.layout == name:
        service.stop()
    return {"saved": name}


class TemplateLabel(BaseModel):
    region_id: str
    value: str


@app.post("/api/calibrate/template")
def save_template(t: TemplateLabel):
    crop = service.region_crop(t.region_id)
    reg = service.layout.region(t.region_id)
    if crop is None or reg is None:
        raise HTTPException(400, "no frame/region available")
    if service.layout.synthetic:
        raise HTTPException(400, "templates are saved per user layout; copy the layout first")
    try:
        if reg.kind == "card":
            saved = service.recognizer.save_card_template(crop, Card.parse(t.value))
        elif reg.kind == "number":
            saved = service.recognizer.save_number_template(crop, reg, t.value)
        else:
            raise ValueError("indicators use a brightness threshold, not templates")
    except ValueError as e:
        raise HTTPException(400, str(e))
    service.event("calibration", f"Saved template(s) {saved} from {reg.label}")
    return {"saved": saved}


# ---------------------------------------------------------------- corrections & analysis
@app.post("/api/correction")
async def correction(c: ManualCorrection):
    try:
        await service.correct(c)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "state_version": service.version}


@app.post("/api/shoe/new")
async def new_shoe():
    service.new_shoe()
    await service.refresh_state()
    await service.broadcast()
    return {"ok": True}


@app.post("/api/explain")
async def explain():
    return await service.request_explanation(force=True)


class VisionReq(BaseModel):
    region_id: str


@app.post("/api/vision/suggest")
async def vision(v: VisionReq):
    crop = service.region_crop(v.region_id)
    reg = service.layout.region(v.region_id)
    if crop is None or reg is None or reg.kind != "card":
        raise HTTPException(400, "only card regions with a captured frame are supported")
    ok, enc = cv2.imencode(".png", crop)  # only this small crop is sent, never the full screen
    res = await service.claude.suggest_card(enc.tobytes(), reg.label)
    service.event("claude", f"Image fallback for {reg.label}: {res.get('suggestion') or res.get('reason')}")
    return res


# ---------------------------------------------------------------- history & privacy
@app.get("/api/history")
def history():
    return {"sessions": service.storage.sessions()}


@app.get("/api/history/{session_id}")
def history_events(session_id: int):
    return {"events": service.storage.events(session_id)}


@app.delete("/api/history")
def delete_history():
    service.storage.delete_history()
    service.timeline.clear()
    return {"deleted": True}


@app.delete("/api/captures")
def delete_captures():
    return {"deleted_frames": service.storage.delete_captures()}


# ---------------------------------------------------------------- static frontend
DIST = ROOT.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(DIST / "index.html")
