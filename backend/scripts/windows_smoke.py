"""Smoke test for the live-capture path on a real Windows desktop (used by CI).

Checks: Win32 window enumeration, monitor listing, a region capture through the
same ScreenSource the app uses, and that the Tk overlay window can be created.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.capture import ScreenSource, list_monitors, list_windows  # noqa: E402


def main() -> int:
    mons = list_monitors()
    print(f"monitors: {mons}")
    assert mons, "no monitors reported by mss"
    wins = list_windows()
    print(f"windows enumerated: {len(wins)} (first: {[w['title'] for w in wins[:3]]})")
    if sys.platform == "win32":
        assert isinstance(wins, list)
    src = ScreenSource({"kind": "region", "rect": [mons[0]["rect"][0], mons[0]["rect"][1], 320, 200]})
    frame, _ = src.grab()
    print(f"captured region frame: {frame.shape} dtype={frame.dtype}")
    assert frame.shape == (200, 320, 3)
    src.close()
    if wins:  # capture through a real window handle, as the app does
        w = wins[0]
        ws = ScreenSource({"kind": "window", "hwnd": w["hwnd"], "title": w["title"]})
        try:
            f2, _ = ws.grab()
            print(f"captured window '{w['title'][:40]}': {f2.shape}")
        except Exception as e:  # minimized windows are reported, not fatal
            print(f"window capture reported: {e}")
    import tkinter as tk
    root = tk.Tk()
    root.attributes("-topmost", True)
    root.update()
    print("tk overlay window created")
    root.destroy()
    print("WINDOWS SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
