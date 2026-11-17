"""Long-term memory for the Screen Assistant ("second brain"), stored locally in SQLite.

- observations: one-line notes of what was on screen, written in the background
- facts: things the user asked it to remember about them (goals, projects, preferences)
- chat: the conversation, so it survives restarts
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'._-]{2,}")
_STOP = set("""the and for you are was what that this with have has had from your but not can did does how why when
where who which about into out then than them they their there here just like get got its it's i'm i've also any
some all one two now see seen saw earlier before today yesterday ago thing things tell show give me my our""".split())
TIME_WORDS = re.compile(r"\b(today|earlier|before|yesterday|this morning|tonight|ago|last (hour|time|night)|so far|recap|summar)", re.I)


def keywords(text: str) -> list[str]:
    return [w for w in (m.lower() for m in _WORD.findall(text)) if w not in _STOP][:12]


def stamp(t: float, with_day: bool = False) -> str:
    d = datetime.fromtimestamp(t)
    return d.strftime("%a %d %b %H:%M") if with_day else d.strftime("%H:%M")


class Brain:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path) if path else ":memory:"
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, t REAL NOT NULL, text TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS obs_t ON observations(t);
                CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, t REAL NOT NULL, text TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat (id INTEGER PRIMARY KEY, t REAL NOT NULL, role TEXT NOT NULL,
                                                 text TEXT NOT NULL, watch INTEGER NOT NULL DEFAULT 0);
            """)
            try:
                self.db.executescript("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS obs_fts USING fts5(text, content='observations', content_rowid='id');
                    CREATE TRIGGER IF NOT EXISTS obs_ai AFTER INSERT ON observations BEGIN
                        INSERT INTO obs_fts(rowid, text) VALUES (new.id, new.text); END;
                """)
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False
            self.db.commit()

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self.lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
            self.db.commit()
            return rows

    # ------------------------------------------------------------ observations
    def add_observation(self, text: str, t: float | None = None) -> None:
        text = " ".join(text.split())[:400]
        if text:
            self._q("INSERT INTO observations(t, text) VALUES (?, ?)", (t or time.time(), text))

    def last_observation(self) -> tuple[float, str] | None:
        rows = self._q("SELECT t, text FROM observations ORDER BY t DESC LIMIT 1")
        return rows[0] if rows else None

    def recent_observations(self, limit: int = 20, since_s: float = 3 * 3600) -> list[tuple[float, str]]:
        rows = self._q("SELECT t, text FROM observations WHERE t >= ? ORDER BY t DESC LIMIT ?",
                       (time.time() - since_s, limit))
        return rows[::-1]

    def today_timeline(self, limit: int = 60) -> list[tuple[float, str]]:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        rows = self._q("SELECT t, text FROM observations WHERE t >= ? ORDER BY t", (start,))
        if len(rows) > limit:  # evenly sample the day
            step = len(rows) / limit
            rows = [rows[int(i * step)] for i in range(limit)]
        return rows

    def search(self, query: str, limit: int = 8, exclude_after: float | None = None) -> list[tuple[float, str]]:
        words = keywords(query)
        if not words:
            return []
        cutoff = exclude_after or time.time() + 1
        if self.fts:
            match = " OR ".join(f'"{w}"' for w in words)
            try:
                rows = self._q("SELECT o.t, o.text FROM obs_fts f JOIN observations o ON o.id = f.rowid "
                               "WHERE obs_fts MATCH ? AND o.t < ? ORDER BY rank LIMIT ?", (match, cutoff, limit))
                return sorted(rows)
            except sqlite3.OperationalError:
                pass
        like = " OR ".join("text LIKE ?" for _ in words)
        rows = self._q(f"SELECT t, text FROM observations WHERE ({like}) AND t < ? ORDER BY t DESC LIMIT ?",
                       (*[f"%{w}%" for w in words], cutoff, limit))
        return sorted(rows)

    # ------------------------------------------------------------ facts
    def add_fact(self, text: str) -> bool:
        text = " ".join(text.split())[:300]
        if not text or text.lower() in {f.lower() for _, f in self.facts()}:
            return False
        self._q("INSERT INTO facts(t, text) VALUES (?, ?)", (time.time(), text))
        return True

    def facts(self) -> list[tuple[int, str]]:
        return self._q("SELECT id, text FROM facts ORDER BY id")

    def forget_fact(self, fact_id: int) -> bool:
        before = len(self.facts())
        self._q("DELETE FROM facts WHERE id = ?", (fact_id,))
        return len(self.facts()) < before

    # ------------------------------------------------------------ chat
    def add_chat(self, entry: dict) -> None:
        self._q("INSERT INTO chat(t, role, text, watch) VALUES (?, ?, ?, ?)",
                (entry["t"], entry["role"], entry["text"], int(bool(entry.get("watch")))))

    def recent_chat(self, limit: int = 40) -> list[dict]:
        rows = self._q("SELECT t, role, text, watch FROM chat ORDER BY id DESC LIMIT ?", (limit,))
        return [{"t": t, "role": r, "text": x, **({"watch": True} if w else {})} for t, r, x, w in rows[::-1]]

    def clear_chat(self) -> None:
        self._q("DELETE FROM chat")

    def stats(self) -> dict:
        (n_obs,), = self._q("SELECT COUNT(*) FROM observations")
        return {"observations": n_obs, "facts": len(self.facts())}
