"""Live screen overlay: a small always-on-top window showing the current analysis.

It reads the local service (http://127.0.0.1:8765/api/state) and only displays;
it never clicks or sends input. Keep it OUTSIDE the captured region, otherwise
it would be captured and confuse recognition; it warns when they overlap.

Usage:  python overlay.py [--url http://127.0.0.1:8765]
Drag to move · right-click for menu · double-click toggles compact mode.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ACTION = {"hit": "HIT", "stand": "STAND", "double": "DOUBLE", "split": "SPLIT", "surrender": "SURRENDER"}
SUIT = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}


def card(c: str) -> str:
    if c in (None, "", "empty"):
        return ""
    if c == "hidden":
        return "🂠"
    r = "10" if c[0] == "T" else c[0]
    return f"{r}{SUIT.get(c[-1], c[-1])}"


def pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def flags(state: dict | None, connected: bool) -> list[str]:
    if not connected or state is None:
        return ["DISCONNECTED"]
    s = state["status"]
    out = []
    if s.get("synthetic"):
        out.append("SYNTHETIC")
    if s["mode"] != "running":
        out.append(s["mode"].upper())
    if s.get("stale"):
        out.append("STALE")
    if s.get("source_error"):
        out.append("SOURCE LOST")
    if state["analysis"].get("status") == "suppressed":
        out.append("UNCERTAIN")
    return out


def overlay_lines(state: dict | None, connected: bool = True, now: float | None = None) -> dict:
    """Return {'headline', 'tone', 'lines', 'flags'}. Pure function (tested without a display)."""
    now = now or time.time()
    fl = flags(state, connected)
    if not connected or state is None:
        return {"headline": "No connection to the local service", "tone": "bad", "lines": ["Start backend/run.py"], "flags": fl}
    a = state["analysis"]
    lines: list[str] = []
    if fl and ("STALE" in fl or "SOURCE LOST" in fl):
        tone = "bad"
    else:
        tone = "warn" if a.get("status") == "suppressed" else "ok"
    age = now - a["computed_at"] if a.get("computed_at") else None
    footer = f"state v{a.get('state_version', 0)}" + (f" · analysis {age:.0f}s old" if age is not None else "")

    if a.get("status") == "suppressed":
        head = "No recommendation: info uncertain"
        lines += [f"• {r}" for r in a.get("suppressed_reasons", [])[:3]]
    elif a.get("game") == "blackjack" and a.get("status") == "decision":
        d, r, ev = a["decision"], a["recommendation"], a["ev"]["actions"]
        head = ACTION.get(r["action"], r["action"].upper())
        lines.append(f"{' '.join(card(c) for c in d['cards'])} = {'soft ' if d['soft'] else ''}{d['total']}  vs  {card(d['dealer_upcard'])}")
        top = sorted(ev.items(), key=lambda kv: -kv[1])[:3]
        lines.append("EV  " + "   ".join(f"{k} {v:+.3f}" for k, v in top))
        lines.append("chart" if r["source"] == "chart" else "EV model")
    elif a.get("game") == "holdem" and a.get("status") == "analysis":
        e = a["equity"]
        head = f"Equity {pct(e['equity'])}"
        ci = "exact" if e["method"] == "exact" else f"±{pct(1.96 * e['std_error'])}"
        hero = " ".join(card(c) for c in a["inputs"]["hero"])
        board = " ".join(card(c) for c in a["inputs"]["board"]) or "—"
        lines.append(f"{hero}  |  {board}  ({a.get('street')})")
        lines.append(f"{a['hand_strength']['made_hand']} · {ci} · vs {len(e['opponents'])} (your ranges)")
        if a.get("pot_odds"):
            po = a["pot_odds"]
            lines.append(f"Call {po['to_call']:g} into {po['pot_before_call']:g} → need {pct(po['break_even_equity'])}")
    else:
        head = a.get("message") or "Waiting…"
    ins = a.get("insurance")
    if ins:
        lines.append(f"Insurance: P(ten) {pct(ins['p_hole_ten'])} → {ins['verdict']}")
        if ins.get("even_money"):
            lines.append(f"Even money: {ins['even_money']['better']}")
    for w in a.get("warnings", [])[:1]:
        lines.append(f"⚠ {w[:70]}")
    lines.append(footer)
    return {"headline": head, "tone": tone, "lines": lines, "flags": fl}


def rects_overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


# ----------------------------------------------------------------------------- window

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    if sys.platform == "win32":  # physical pixels, same coordinates as the capture
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    import tkinter as tk

    cfg_path = Path(__file__).resolve().parent / "data" / "overlay.json"
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception:
        cfg = {"x": 40, "y": 40, "compact": False, "alpha": 0.88}

    root = tk.Tk()
    root.title("Table Assistant overlay")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", cfg.get("alpha", 0.88))
    root.geometry(f"+{cfg['x']}+{cfg['y']}")
    import tkinter.font as tkfont
    installed = set(tkfont.families())
    family = next((f for f in ("Segoe UI", "DejaVu Sans", "Noto Sans", "Helvetica", "Arial") if f in installed), "TkDefaultFont")
    bg = "#10141d"
    frame = tk.Frame(root, bg=bg, padx=12, pady=8, highlightthickness=2, highlightbackground="#2e6cb5")
    frame.pack()
    flag_lbl = tk.Label(frame, bg=bg, fg="#ffb547", font=(family, 9, "bold"), anchor="w")
    flag_lbl.pack(fill="x")
    head = tk.Label(frame, bg=bg, fg="#3ddc97", font=(family, 20, "bold"), anchor="w")
    head.pack(fill="x")
    body = tk.Label(frame, bg=bg, fg="#e6e9f0", font=(family, 10), justify="left", anchor="w")
    body.pack(fill="x")
    tone_color = {"ok": "#3ddc97", "warn": "#ffb547", "bad": "#ff5d6c"}

    drag = {"x": 0, "y": 0}

    def start(e):
        drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def move(e):
        root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

    def save_pos(_=None):
        cfg.update(x=root.winfo_x(), y=root.winfo_y())
        try:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(cfg))
        except OSError:
            pass

    def toggle(_=None):
        cfg["compact"] = not cfg.get("compact")

    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="Compact / full", command=toggle)
    menu.add_command(label="More transparent", command=lambda: (cfg.update(alpha=max(0.4, cfg["alpha"] - 0.1)), root.attributes("-alpha", cfg["alpha"])))
    menu.add_command(label="Less transparent", command=lambda: (cfg.update(alpha=min(1.0, cfg["alpha"] + 0.1)), root.attributes("-alpha", cfg["alpha"])))
    menu.add_separator()
    menu.add_command(label="Close overlay", command=lambda: (save_pos(), root.destroy()))
    for w in (frame, flag_lbl, head, body):
        w.bind("<ButtonPress-1>", start)
        w.bind("<B1-Motion>", move)
        w.bind("<ButtonRelease-1>", save_pos)
        w.bind("<Double-Button-1>", toggle)
        w.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def tick():
        try:
            with urllib.request.urlopen(args.url + "/api/state", timeout=1.5) as r:
                state, ok = json.loads(r.read()), True
        except Exception:
            state, ok = None, False
        view = overlay_lines(state, ok)
        fl = list(view["flags"])
        cap = (state or {}).get("status", {}).get("capture_rect")
        if cap and rects_overlap((root.winfo_x(), root.winfo_y(), root.winfo_width(), root.winfo_height()), cap):
            fl.insert(0, "OVERLAPS CAPTURE — MOVE ME")
        flag_lbl.config(text="  ".join(fl))
        head.config(text=view["headline"], fg=tone_color[view["tone"]])
        body.config(text="" if cfg.get("compact") else "\n".join(view["lines"]))
        root.after(300, tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
