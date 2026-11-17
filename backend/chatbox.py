"""Screen Assistant chat box: a small always-on-top window you talk to.

Talks to the local service (http://127.0.0.1:8765/api/assistant). Pick what it should
look at, hit ⚡ for a quick insight or type a question. The box hides itself from
screen capture (Windows 10 2004+), so it never shows up in what Claude sees.

Usage:  python chatbox.py [--url http://127.0.0.1:8765]
Overlay: a slim see-through bar in the bottom-right corner with the latest comment. Hover it to open the
full chat; move the mouse away and it shrinks back (Esc shrinks it right away).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request

BG, PANEL, LINE, TEXT, MUTED, ACCENT, WARN, BAD = "#0b0e14", "#131824", "#262e40", "#e6e9f0", "#8b95a7", "#5aa9ff", "#ffb547", "#ff5d6c"
TITLE = "Screen Assistant"
STRIP_W, STRIP_H = 560, 62


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 120):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read()).get("detail")
            except Exception:
                detail = None
            raise RuntimeError(detail or f"HTTP {e.code}") from None


def hide_from_capture(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


def target_options(sources: dict) -> list[tuple[str, dict]]:
    """(label, target) pairs for the picker; skips this box itself. Pure, tested."""
    out = []
    for m in sources.get("monitors", []):
        out.append((f"Whole monitor {m['index']}", {"kind": "region", "rect": m["rect"], "title": f"Monitor {m['index']}"}))
    for w in sources.get("windows", []):
        if w.get("title") and w["title"] != TITLE:
            out.append((w["title"][:60], {"kind": "window", "hwnd": w["hwnd"], "title": w["title"]}))
    return out


class ChatBox:
    def __init__(self, api: Api):
        import tkinter as tk
        self.tk = tk
        self.api = api
        self.root = root = tk.Tk()
        root.title(TITLE)
        root.attributes("-topmost", True)  # normal window (taskbar button, title bar) that stays on top
        root.configure(bg=LINE)
        root.minsize(300, 220)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"400x500+{(sw - 400) // 2}+{(sh - 500) // 2}")
        self.collapsed = False
        self.seen = None
        self.options: list[tuple[str, dict]] = []
        self.busy = False

        self.frame = frame = tk.Frame(root, bg=BG)
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        # compact overlay: a slim see-through bar in the bottom-right corner showing the latest comment
        self.strip = tk.Frame(root, bg=BG, cursor="hand2")
        tk.Label(self.strip, text="◆", bg=BG, fg=ACCENT, font=("Segoe UI", 12, "bold")).pack(side="left", padx=(10, 6))
        self.strip_text = tk.Label(self.strip, text="Watching your screen…", bg=BG, fg=TEXT, font=("Segoe UI", 10),
                                   justify="left", anchor="w", wraplength=STRIP_W - 50)
        self.strip_text.pack(side="left", fill="both", expand=True, pady=4)
        for w in (self.strip, *self.strip.winfo_children()):
            w.bind("<Enter>", lambda e: self.expand())
            w.bind("<Button-1>", lambda e: self.expand())
        self.compact = False
        self._outside_since = None

        bar = tk.Frame(frame, bg=PANEL)
        bar.pack(fill="x")
        tk.Label(bar, text="◆ Screen Assistant", bg=PANEL, fg=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=8, pady=4)

        self.body = body = tk.Frame(frame, bg=BG)
        body.pack(fill="both", expand=True)

        top = tk.Frame(body, bg=BG)
        top.pack(fill="x", padx=8, pady=(6, 2))
        self.target_var = tk.StringVar(value="Pick what it sees…")
        self.picker = tk.OptionMenu(top, self.target_var, "")
        self.picker.configure(bg=PANEL, fg=TEXT, activebackground=LINE, highlightthickness=0, bd=0, anchor="w", font=("Segoe UI", 9))
        self.picker["menu"].configure(bg=PANEL, fg=TEXT)
        self.picker.pack(side="left", fill="x", expand=True)
        self.picker.bind("<Button-1>", lambda e: self.load_sources(), add="+")
        self.watch_btn = tk.Button(top, text="💡 off", command=self.toggle_watch, bg=PANEL, fg=MUTED, bd=0, font=("Segoe UI", 9), width=6)
        self.watch_btn.pack(side="right", padx=(6, 0))

        self.log = tk.Text(body, bg=BG, fg=TEXT, bd=0, wrap="word", font=("Segoe UI", 10), padx=8, pady=6,
                           highlightthickness=0, state="disabled", cursor="arrow")
        self.log.tag_configure("user", foreground=ACCENT, spacing1=8)
        self.log.tag_configure("assistant", foreground=TEXT, spacing1=4, spacing3=4)
        self.log.tag_configure("watch", foreground=WARN, spacing1=4, spacing3=4)
        self.log.tag_configure("system", foreground=MUTED, font=("Segoe UI", 8), spacing1=4)
        self.log.tag_configure("error", foreground=BAD, font=("Segoe UI", 9))

        inp = tk.Frame(body, bg=PANEL)
        inp.pack(fill="x", side="bottom")
        tk.Label(inp, text="Ask anything · Enter = send · ⚡ quick look · 🧠 think it through · /facts /memory", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=(6, 0))
        row = tk.Frame(inp, bg=PANEL)
        row.pack(fill="x", padx=8, pady=(2, 8))
        self.entry = tk.Entry(row, bg="#1f2738", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 11),
                              highlightthickness=2, highlightbackground=LINE, highlightcolor=ACCENT)
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.status = tk.Label(inp, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 7), anchor="w")
        self.status.pack(fill="x", padx=8, pady=(0, 4), side="bottom")
        self.entry.bind("<Return>", lambda e: self.send())
        tk.Button(row, text="➤", command=self.send, bg=ACCENT, fg=BG, relief="flat",
                  font=("Segoe UI", 11, "bold"), width=3, activebackground=TEXT).pack(side="left", padx=(6, 0), fill="y")
        tk.Button(row, text="⚡", command=lambda: self.send(quick=True), bg=WARN, fg=BG, relief="flat",
                  font=("Segoe UI", 11, "bold"), width=3, activebackground=TEXT).pack(side="left", padx=(6, 0), fill="y")
        tk.Button(row, text="🧠", command=lambda: self.send(deep=True), bg="#c38bff", fg=BG, relief="flat",
                  font=("Segoe UI", 11, "bold"), width=3, activebackground=TEXT).pack(side="left", padx=(6, 0), fill="y")
        self.log.pack(fill="both", expand=True)
        # clicking the chat or typing anywhere in the box goes to the input
        self.log.bind("<Button-1>", lambda e: (self.entry.focus_set(), "break")[1])
        root.bind("<Key>", self._redirect_key)
        root.bind("<FocusIn>", lambda e: e.widget is root and self.entry.focus_set())

        root.bind("<Escape>", lambda e: self.collapse())
        self.collapse()
        self.load_sources()
        self.poll()
        self.root.after(500, self._auto_collapse)

    # ------------------------------------------------------------ overlay modes
    def _place(self, w: int, h: int):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{sw - w - 12}+{sh - h - 56}")  # bottom-right, above the taskbar

    def _hide_from_capture(self):
        self.root.update_idletasks()
        hide_from_capture(int(self.root.wm_frame(), 16) if self.root.wm_frame() else self.root.winfo_id())

    def collapse(self):
        """Slim bar mode: borderless, see-through, out of the way."""
        if self.compact or (self.entry.get().strip() and self.root.focus_get() is not None):
            return
        self.compact = True
        self.root.withdraw()
        self.frame.pack_forget()
        self.strip.pack(fill="both", expand=True)
        self.root.overrideredirect(True)
        self.root.minsize(1, 1)
        self._place(STRIP_W, STRIP_H)
        self.root.attributes("-alpha", 0.88)
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._hide_from_capture()

    def expand(self):
        """Full chat: a normal window (so typing works) in the same corner."""
        if not self.compact:
            return
        self.compact = False
        self._outside_since = None
        self.root.withdraw()
        self.strip.pack_forget()
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)
        self.root.overrideredirect(False)
        self.root.minsize(300, 220)
        self._place(420, 540)
        self.root.attributes("-alpha", 0.97)
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._hide_from_capture()
        self.root.lift()
        self.root.focus_force()
        self.entry.focus_set()
        self.seen = None  # redraw the chat

    def _auto_collapse(self):
        """Shrink back to the bar once the mouse has been away for a bit (unless you're typing or waiting)."""
        try:
            if not self.compact:
                x, y = self.root.winfo_pointerxy()
                rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
                inside = rx - 30 <= x <= rx + self.root.winfo_width() + 30 and ry - 40 <= y <= ry + self.root.winfo_height() + 30
                if inside or self.busy or self.entry.get().strip():
                    self._outside_since = None
                elif self._outside_since is None:
                    self._outside_since = time.time()
                elif time.time() - self._outside_since > 2.5:
                    self.collapse()
        finally:
            self.root.after(400, self._auto_collapse)

    # ------------------------------------------------------------ helpers
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _redirect_key(self, e):
        if self.root.focus_get() is self.entry or not e.char or not e.char.isprintable():
            return
        self.entry.focus_set()
        self.entry.insert("end", e.char)
        return "break"

    def toggle(self):
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.body.pack_forget()
            self.root.geometry(f"380x30+{self.root.winfo_x()}+{self.root.winfo_y()}")
        else:
            self.body.pack(fill="both", expand=True)
            self.root.geometry(f"380x460+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def bg(self, fn, done=None):
        def run():
            try:
                res, err = fn(), None
            except Exception as e:
                res, err = None, str(e)
            self.root.after(0, lambda: done(res, err) if done else None)
        threading.Thread(target=run, daemon=True).start()

    def say(self, text: str, tag: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    # ------------------------------------------------------------ actions
    def load_sources(self):
        def done(src, err):
            if err:
                return
            self.options = target_options(src)
            menu = self.picker["menu"]
            menu.delete(0, "end")
            for label, target in self.options:
                menu.add_command(label=label, command=lambda l=label, t=target: self.choose(l, t))
        self.bg(lambda: self.api.call("GET", "/api/sources", timeout=10), done)

    def choose(self, label, target):
        self.target_var.set(label)
        self.bg(lambda: self.api.call("POST", "/api/assistant/target", {"target": target}),
                lambda r, err: err and self.say(err, "error"))

    def send(self, quick=False, deep=False):
        text = "" if quick else self.entry.get().strip()
        if self.busy or (not quick and not deep and not text):
            return
        self.busy = True
        self.entry.delete(0, "end")
        self.say(("🧠 " if deep else "") + (text or "⚡ quick look"), "user")
        def done(r, err):
            self.busy = False
            if err:
                self.say(err, "error")
        self.bg(lambda: self.api.call("POST", "/api/assistant/ask", {"text": text, "deep": deep}), done)
        self.poll_now()

    def toggle_watch(self):
        on = self.watch_btn.cget("text").endswith("off")  # tips on/off (memory keeps running)
        self.bg(lambda: self.api.call("POST", "/api/assistant/watch", {"on": on}),
                lambda r, err: err and self.say(err, "error"))

    def poll_now(self):
        if getattr(self, "_poll_id", None):
            self.root.after_cancel(self._poll_id)
        self._poll_id = None
        self.root.after(30, self.poll)

    def poll(self):
        def done(st, err):
            if st:
                self.render(st)
            busy = bool(st and (st.get("busy") or any(m.get("streaming") for m in st["messages"][-2:])))
            self._poll_id = self.root.after(60 if busy or self.busy else 500, self.poll)
        self.bg(lambda: self.api.call("GET", "/api/assistant", timeout=5), done)

    def render(self, st: dict):
        self.watch_btn.configure(text="💡 on" if st["watching"] else "💡 off", fg=WARN if st["watching"] else MUTED)
        if st.get("target") and self.target_var.get().startswith("Pick"):
            self.target_var.set(st["target"].get("title", "")[:60])
        msgs = st["messages"]
        latest = next((m for m in reversed(msgs) if m["role"] == "assistant" and m.get("text")), None)
        if st.get("paused"):
            self.strip_text.configure(text="⏸ Paused: live chess game open")
        elif not st["watching"] and (latest is None or latest.get("watch")):
            self.strip_text.configure(text="Live commentary off · type \"live commentary\" or tap 💡 to turn it on")
        elif latest:
            t = " ".join(latest["text"].split())
            self.strip_text.configure(text=("💡 " if latest.get("watch") else "") + (t if len(t) <= 170 else t[:167] + "…"))
        lat = st.get("latency_ms")
        mem = st.get("memory") or {}
        info = f"{st.get('model')} · {mem.get('observations', 0)} memories · {mem.get('facts', 0)} facts" + (f" · first word {lat['first_token'] + lat.get('screenshot', 0)} ms, done {lat['total'] + lat.get('screenshot', 0)} ms" if lat else "")
        self.status.configure(text=info if st.get("configured") else "not ready: " + (st.get("last_error") or "see the hint above"))
        key = (len(msgs), msgs[-1]["text"] if msgs else "")
        if key == self.seen:
            return
        self.seen = key
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        if not msgs:
            hint = "Pick what it sees above, then hit ⚡ for a quick insight or type a question."
            if not st.get("configured"):
                hint = ("Free local model not ready. Install Ollama, then run:  ollama pull " + st.get("model", "qwen2.5vl:7b")
                        if st.get("provider") == "ollama" else "No ANTHROPIC_API_KEY in .env yet.")
            self.log.insert("end", hint + "\n", "system")
        for m in msgs:
            tag = "watch" if m.get("watch") else m["role"]
            text = "⚡ quick look" if m["role"] == "user" and m["text"].lstrip("🧠 ").startswith("Quick look:") else m["text"]
            if m.get("watch"):
                text = "💡 " + text
            if m.get("streaming") and not text:
                text = "…"
            self.log.insert("end", text + "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8765")
    ChatBox(Api(p.parse_args().url)).run()
