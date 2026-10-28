"""Local-only session history (SQLite) and opt-in screenshot retention."""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import cv2
import numpy as np


class Storage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.captures_dir = data_dir / "captures"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(data_dir / "history.db", check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY, started_at REAL, game TEXT, source TEXT, synthetic INTEGER);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, session_id INTEGER, t REAL, kind TEXT,
                                               message TEXT, state_version INTEGER, data TEXT);
        """)
        self.db.commit()
        self._manifests: dict[int, dict] = {}

    def start_session(self, game: str, source: str, synthetic: bool) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO sessions (started_at, game, source, synthetic) VALUES (?,?,?,?)",
                                  (time.time(), game, source, int(synthetic)))
            self.db.commit()
            return cur.lastrowid

    def log_event(self, session_id: int, t: float, kind: str, message: str, version: int, data: dict) -> None:
        with self._lock:
            self.db.execute("INSERT INTO events (session_id, t, kind, message, state_version, data) VALUES (?,?,?,?,?,?)",
                            (session_id, t, kind, message, version, json.dumps(data, default=str)))
            self.db.commit()

    def sessions(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("SELECT s.id, s.started_at, s.game, s.source, s.synthetic, COUNT(e.id) "
                               "FROM sessions s LEFT JOIN events e ON e.session_id = s.id "
                               "GROUP BY s.id ORDER BY s.id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "started_at": r[1], "game": r[2], "source": r[3], "synthetic": bool(r[4]), "events": r[5],
                 "captures": self._capture_count(r[0])} for r in rows]

    def events(self, session_id: int) -> list[dict]:
        rows = self.db.execute("SELECT t, kind, message, state_version, data FROM events WHERE session_id=? ORDER BY id",
                               (session_id,)).fetchall()
        return [{"t": r[0], "kind": r[1], "message": r[2], "state_version": r[3], "data": json.loads(r[4])} for r in rows]

    def delete_history(self) -> None:
        with self._lock:
            self.db.execute("DELETE FROM events")
            self.db.execute("DELETE FROM sessions")
            self.db.commit()
            self.db.execute("VACUUM")

    # ------------------------------------------------------------- captures
    def _capture_count(self, session_id: int) -> int:
        d = self.captures_dir / f"session_{session_id}"
        return len(list(d.glob("*.png"))) if d.exists() else 0

    def save_capture(self, session_id: int, frame: np.ndarray, t: float, game: str, layout: str) -> None:
        d = self.captures_dir / f"session_{session_id}"
        d.mkdir(parents=True, exist_ok=True)
        man = self._manifests.setdefault(session_id, {"game": game, "layout": layout, "synthetic": False,
                                                       "description": "Recorded capture session", "frames": []})
        name = f"frame_{len(man['frames']):05d}.png"
        cv2.imwrite(str(d / name), frame)
        man["frames"].append({"file": name, "t": t})
        (d / "replay.json").write_text(json.dumps(man))

    def delete_captures(self) -> int:
        n = 0
        if self.captures_dir.exists():
            n = len(list(self.captures_dir.rglob("*.png")))
            shutil.rmtree(self.captures_dir)
        self._manifests.clear()
        return n

    def capture_sessions(self) -> list[Path]:
        if not self.captures_dir.exists():
            return []
        return sorted(p for p in self.captures_dir.iterdir() if (p / "replay.json").exists())
