"""Orchestrates capture -> recognition -> stabilization -> tracking -> analysis -> explanation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field

from .analysis import PokerSettings, analyze_blackjack, analyze_holdem
from .capture import ReplaySource, ScreenSource, SourceError, frame_change, frame_signature
from .claude_client import ClaudeService
from .engines.bj_rules import BlackjackRules
from .schemas import Layout, ManualCorrection, Observation, TimelineEvent
from .storage import Storage
from .tracking.blackjack_tracker import BlackjackTracker
from .tracking.poker_tracker import PokerTracker
from .tracking.stabilizer import Stabilizer
from .vision.recognize import Recognizer

log = logging.getLogger("service")


def _strip_confidence(x):
    """State identity ignores recognition-confidence jitter."""
    if isinstance(x, dict):
        return {k: _strip_confidence(v) for k, v in x.items() if k != "confidence"}
    if isinstance(x, list):
        return [_strip_confidence(v) for v in x]
    return x


class CaptureSettings(BaseModel):
    interval_s: float = Field(0.5, ge=0.05, le=10)
    change_threshold: float = Field(0.0005, ge=0, le=0.5, description="fraction of changed pixels that counts as a new frame")
    stable_frames: int = Field(3, ge=1, le=10)
    min_confidence: float = Field(0.6, ge=0.1, le=0.99)
    uncertain_after: int = Field(6, ge=2, le=50)
    stale_after_s: float = Field(5.0, ge=0.5, le=120)
    gap_break_s: float = Field(10.0, ge=1, le=600, description="observation gap that invalidates shoe history")
    replay_speed: float = Field(1.0, ge=0.1, le=20)


class PrivacySettings(BaseModel):
    retain_screenshots: bool = False
    record_history: bool = True


class Settings(BaseModel):
    game: Literal["blackjack", "holdem"] = "blackjack"
    layout: str = "synthetic_blackjack"
    rules: BlackjackRules = Field(default_factory=BlackjackRules)
    poker: PokerSettings = Field(default_factory=PokerSettings)
    capture: CaptureSettings = Field(default_factory=CaptureSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    acknowledged_platform_rules: bool = False


class AppService:
    def __init__(self, root: Path, data_dir: Path):
        self.root = root
        self.data_dir = data_dir
        self.builtin_layouts = root / "layouts"
        self.user_layouts = data_dir / "layouts"
        self.user_layouts.mkdir(parents=True, exist_ok=True)
        self.replays_dir = root / "replays"
        self.storage = Storage(data_dir)
        self.claude = ClaudeService()
        self.settings = self._load_settings()
        self.mode: Literal["stopped", "running", "paused"] = "stopped"
        self.source = None
        self.source_info: dict = {"type": None}
        self.source_error: str | None = None
        self.session_id: int | None = None
        self.timeline: deque[dict] = deque(maxlen=300)
        self.clients: set = set()
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self.analysis_lock = asyncio.Lock()
        self._reset_pipeline()

    # ----------------------------------------------------------------- setup
    def _settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def _load_settings(self) -> Settings:
        p = self._settings_path()
        if p.exists():
            try:
                return Settings.model_validate_json(p.read_text())
            except Exception:
                log.warning("settings.json invalid; using defaults")
        return Settings()

    def save_settings(self) -> None:
        self._settings_path().write_text(self.settings.model_dump_json(indent=2))

    def layout_path(self, name: str) -> Path | None:
        for d in (self.user_layouts, self.builtin_layouts):
            p = d / f"{name}.json"
            if p.exists():
                return p
        return None

    def load_layout(self, name: str) -> Layout:
        p = self.layout_path(name)
        if not p:
            raise FileNotFoundError(f"layout {name!r} not found")
        return Layout.model_validate_json(p.read_text())

    def list_layouts(self) -> list[dict]:
        out = {}
        for d, kind in ((self.builtin_layouts, "builtin"), (self.user_layouts, "user")):
            for p in sorted(d.glob("*.json")):
                try:
                    lay = Layout.model_validate_json(p.read_text())
                    out[lay.name] = {"name": lay.name, "game": lay.game, "kind": kind, "synthetic": lay.synthetic,
                                     "regions": len(lay.regions)}
                except Exception:
                    continue
        return list(out.values())

    def save_layout(self, layout: Layout) -> None:
        (self.user_layouts / f"{layout.name}.json").write_text(layout.model_dump_json(indent=2))

    def _reset_pipeline(self) -> None:
        s = self.settings
        try:
            self.layout = self.load_layout(s.layout)
        except FileNotFoundError:
            self.layout = self.load_layout("synthetic_blackjack" if s.game == "blackjack" else "synthetic_holdem")
        if self.layout.game != s.game:
            s.game = self.layout.game
        self.recognizer = Recognizer(self.layout, self.user_layouts / self.layout.name / "templates")
        self.stabilizer = Stabilizer(s.capture.stable_frames, s.capture.min_confidence, s.capture.uncertain_after)
        if self.layout.game == "blackjack":
            self.tracker = BlackjackTracker(self.layout, s.rules, self._emit)
        else:
            self.tracker = PokerTracker(self.layout, self._emit)
        self.version = 0
        self._state_key = None
        self.frame_id = 0
        self.last_frame: np.ndarray | None = None
        self.last_sig = None
        self.last_capture_at: float | None = None  # source timestamp (replay time or wall clock)
        self.last_capture_wall: float | None = None
        self.last_processed_wall: float | None = None
        self.frames_processed = 0
        self.frames_skipped_duplicate = 0
        self.last_observations: list[Observation] = []
        self.game_snapshot: dict = {}
        self.analysis: dict = {"status": "idle", "state_version": 0, "message": "Not started."}
        self.explanation: dict | None = None

    # ----------------------------------------------------------------- events
    def _emit(self, ev: TimelineEvent) -> None:
        ev.state_version = self.version
        d = ev.model_dump()
        if self.last_capture_at is not None:
            d["capture_t"] = self.last_capture_at
        self.timeline.append(d)
        if self.session_id and self.settings.privacy.record_history:
            self.storage.log_event(self.session_id, ev.t, ev.kind, ev.message, ev.state_version, ev.data)

    def event(self, kind: str, msg: str, **data) -> None:
        self._emit(TimelineEvent(kind=kind, message=msg, data=data))

    # ----------------------------------------------------------------- sources
    def list_replays(self) -> list[dict]:
        out = []
        for base, kind in ((self.replays_dir, "bundled"),):
            if base.exists():
                for p in sorted(base.iterdir()):
                    if (p / "replay.json").exists():
                        m = json.loads((p / "replay.json").read_text())
                        out.append({"name": p.name, "kind": kind, "game": m.get("game"), "frames": len(m["frames"]),
                                    "synthetic": bool(m.get("synthetic")), "description": m.get("description", ""),
                                    "layout": m.get("layout")})
        for p in self.storage.capture_sessions():
            m = json.loads((p / "replay.json").read_text())
            out.append({"name": f"captures/{p.name}", "kind": "recorded", "game": m.get("game"), "frames": len(m["frames"]),
                        "synthetic": False, "description": m.get("description", ""), "layout": m.get("layout")})
        return out

    def set_source(self, spec: dict) -> None:
        self.stop()
        if spec["type"] == "replay":
            name = spec["name"]
            folder = (self.storage.captures_dir / name.split("/", 1)[1]) if name.startswith("captures/") else (self.replays_dir / name)
            src = ReplaySource(folder)
            lay = src.manifest.get("layout")
            if lay:
                self.settings.layout = lay
                self.settings.game = src.manifest.get("game", self.settings.game)
            self.source = src
            self.source_info = {"type": "replay", "name": name, "synthetic": src.synthetic,
                                "frames": len(src.frames), "description": src.manifest.get("description", "")}
        elif spec["type"] == "screen":
            self.source = ScreenSource(spec["target"])
            self.source_info = {"type": "screen", "target": spec["target"], "synthetic": False}
        else:
            raise ValueError("unknown source type")
        self.source_error = None
        self._reset_pipeline()
        self.timeline.clear()  # timeline is per source; persisted history lives in SQLite
        self.event("source", f"Source selected: {self.source_info.get('name') or self.source.label}")

    # ----------------------------------------------------------------- control
    def start(self) -> None:
        if self.source is None:
            raise ValueError("select a source first")
        if self.mode == "stopped":
            self.session_id = self.storage.start_session(self.layout.game, json.dumps(self.source_info, default=str),
                                                         bool(self.source_info.get("synthetic")))
            self.event("control", "Monitoring started")
        elif self.mode == "paused":
            self.event("control", "Monitoring resumed")
        self.mode = "running"
        self._wake.set()

    def pause(self) -> None:
        if self.mode == "running":
            self.mode = "paused"
            self.event("control", "Monitoring paused")

    def stop(self) -> None:
        if self.mode != "stopped":
            self.event("control", "Monitoring stopped")
        self.mode = "stopped"
        self.session_id = None
        if isinstance(self.source, ReplaySource):
            self.source.restart()
        self._reset_pipeline()

    async def run_loop(self) -> None:
        while True:
            try:
                if self.mode == "running" and self.source is not None:
                    await self.capture_once()
                    await self.broadcast()
                    interval = self.settings.capture.interval_s
                    if isinstance(self.source, ReplaySource):
                        interval /= self.settings.capture.replay_speed
                    await asyncio.sleep(interval)
                else:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        await self.broadcast()  # keeps stale/age indicators fresh
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("capture loop error")
                await asyncio.sleep(1)

    async def capture_once(self) -> None:
        try:
            frame, src_t = await asyncio.to_thread(self.source.grab)
        except SourceError as e:
            if isinstance(self.source, ReplaySource) and self.source.finished:
                self.event("control", "Replay finished")
                self.mode = "paused"
                return
            if self.source_error is None:
                self.event("error", f"Capture source disconnected: {e}")
            self.source_error = str(e)
            return
        now = time.time()
        t = src_t if src_t is not None else now
        if self.source_error is not None:
            self.event("source", "Capture source reconnected")
            self.source_error = None
        if self.layout.width != frame.shape[1] or self.layout.height != frame.shape[0]:
            # a different table size: regions would be misplaced
            if not getattr(self, "_size_warned", False):
                self.event("warning", f"Captured frame is {frame.shape[1]}x{frame.shape[0]} but layout '{self.layout.name}' "
                                      f"expects {self.layout.width}x{self.layout.height}; recalibrate or select the matching window")
                self._size_warned = True
        else:
            self._size_warned = False
        await self.process_frame(frame, t, now)

    async def process_frame(self, frame: np.ndarray, t: float, now: float | None = None) -> None:
        now = now or time.time()
        gap = (t - self.last_capture_at) if self.last_capture_at is not None else 0.0
        self.last_frame = frame
        self.last_capture_wall = now
        sig = frame_signature(frame)
        change = frame_change(self.last_sig, sig)
        prev_t = self.last_capture_at
        self.last_capture_at = t
        if prev_t is not None and gap > self.settings.capture.gap_break_s and isinstance(self.tracker, BlackjackTracker):
            self.tracker.break_continuity(f"no observation for {gap:.0f}s; hands may have been dealt unseen")
            self.event("warning", f"Observation gap of {gap:.0f}s")
        if change < 0 or (change < self.settings.capture.change_threshold and self.stabilizer.settled()):
            self.frames_skipped_duplicate += 1
            return
        self.last_sig = sig
        self.frame_id += 1
        obs = await asyncio.to_thread(self.recognizer.observe, frame, self.frame_id, t)
        self.last_observations = obs
        self.stabilizer.update(obs)
        self.tracker.update(self.stabilizer.states)
        self.frames_processed += 1
        self.last_processed_wall = now
        if self.session_id and self.settings.privacy.retain_screenshots:
            await asyncio.to_thread(self.storage.save_capture, self.session_id, frame, t, self.layout.game, self.layout.name)
        await self.refresh_state()

    async def refresh_state(self) -> None:
        snap = self.tracker.snapshot(self.stabilizer.states)
        self.game_snapshot = snap
        key = hashlib.sha256(json.dumps(_strip_confidence(snap), sort_keys=True, default=str).encode()).hexdigest()
        if key == self._state_key:
            return
        self._state_key = key
        self.version += 1
        await self.run_analysis()

    async def settings_changed(self) -> None:
        """Rules/ranges changed: same observations, new calculation -> new state version."""
        if isinstance(self.tracker, BlackjackTracker):
            self.tracker.rules = self.settings.rules
        self.version += 1
        self._state_key = None
        if self.game_snapshot:
            await self.refresh_state()

    async def run_analysis(self) -> None:
        version = self.version
        snap = self.game_snapshot
        if self.layout.game == "blackjack":
            result = analyze_blackjack(snap, self.settings.rules, version)
        else:
            result = await asyncio.to_thread(analyze_holdem, snap, self.settings.poker, version)
        if version != self.version:
            self.event("discard", f"Analysis for state v{version} discarded: state changed to v{self.version} meanwhile")
            return
        self.analysis = result
        if self.explanation and self.explanation.get("state_version") != version:
            self.explanation = None
        if result.get("facts") and self.claude.auto_explain and self.claude.has_key:
            asyncio.create_task(self.request_explanation(force=False))

    def rules_numbers(self) -> list[float]:
        r = self.settings.rules
        return [r.decks, r.blackjack_payout, 3, 17, 21, 52, 13]

    async def request_explanation(self, force: bool) -> dict:
        analysis = self.analysis
        version = analysis.get("state_version", 0)
        self.explanation = {"status": "pending", "state_version": version}
        await self.broadcast()
        res = await self.claude.explain(analysis, self.rules_numbers(), force=force)
        if res.get("state_version") != self.version:
            self.event("discard", f"Claude response for state v{res.get('state_version')} discarded (current v{self.version})")
            if self.explanation and self.explanation.get("state_version") == version:
                self.explanation = None
            await self.broadcast()
            return {"status": "discarded_stale", "state_version": res.get("state_version")}
        if res["status"] == "rejected":
            self.event("claude", f"Claude output rejected: {res['reason']}")
        self.explanation = res
        await self.broadcast()
        return res

    # ----------------------------------------------------------------- corrections
    async def correct(self, c: ManualCorrection) -> None:
        reg = self.layout.region(c.region_id)
        if not reg:
            raise ValueError(f"unknown region {c.region_id}")
        if c.value is not None:
            Observation(region_id=reg.id, kind=reg.kind, value=c.value, confidence=1, captured_at=0, frame_id=0)  # validates
        self.stabilizer.set_override(reg.id, c.value, reg.kind, time.time())
        self.event("correction", f"Manual correction: {reg.label} = {c.value}" if c.value is not None
                   else f"Manual correction cleared: {reg.label}")
        self.tracker.update(self.stabilizer.states)
        await self.refresh_state()
        await self.broadcast()

    def new_shoe(self) -> None:
        if isinstance(self.tracker, BlackjackTracker):
            self.tracker.new_shoe("user marked a fresh shoe")

    def region_crop(self, region_id: str) -> np.ndarray | None:
        reg = self.layout.region(region_id)
        if reg is None or self.last_frame is None:
            return None
        r = reg.rect
        return self.last_frame[r.y:r.y + r.h, r.x:r.x + r.w]

    # ----------------------------------------------------------------- snapshot
    def status(self) -> dict:
        now = time.time()
        stale = bool(self.mode == "running" and self.last_capture_wall and now - self.last_capture_wall > self.settings.capture.stale_after_s)
        return {
            "mode": self.mode,
            "source": self.source_info,
            "source_error": self.source_error,
            "synthetic": bool(self.source_info.get("synthetic")) or self.layout.synthetic,
            "layout": {"name": self.layout.name, "game": self.layout.game, "width": self.layout.width, "height": self.layout.height,
                       "synthetic": self.layout.synthetic},
            "frame_id": self.frame_id,
            "frames_processed": self.frames_processed,
            "frames_skipped_duplicate": self.frames_skipped_duplicate,
            "last_capture_t": self.last_capture_at,
            "last_capture_wall": self.last_capture_wall,
            "last_processed_wall": self.last_processed_wall,
            "replay_position": (self.source.index, len(self.source.frames)) if isinstance(self.source, ReplaySource) else None,
            "stale": stale,
            "capture_rect": getattr(self.source, "last_rect", None),
            "server_time": now,
            "state_version": self.version,
        }

    def full_state(self) -> dict:
        regions = {rid: st.model_dump() for rid, st in self.stabilizer.states.items()}
        return {
            "status": self.status(),
            "game": self.game_snapshot,
            "analysis": self.analysis,
            "explanation": self.explanation,
            "regions": regions,
            "timeline": list(self.timeline)[-120:],
            "claude": self.claude.status(),
        }

    async def broadcast(self) -> None:
        if not self.clients:
            return
        msg = json.dumps(self.full_state(), default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def preview_jpeg(self) -> bytes | None:
        if self.last_frame is None:
            return None
        ok, enc = cv2.imencode(".jpg", self.last_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return enc.tobytes() if ok else None
