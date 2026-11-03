"""Frame sources: a selected screen region / window, or a replay folder.

Only the selected rectangle is ever grabbed. On Windows, windows are listed via
the Win32 API (ctypes, no extra dependency) and their rectangle is re-read every
frame so moving the window keeps working.

A window source is locked to that window: its pixels are read from the window
itself (PrintWindow), so other windows on top of it - the browser, popups, the
overlay - are never read. Windows that cannot render themselves this way (some
GPU-drawn games return a black image) fall back to a screen grab, but only while
the window is fully uncovered; otherwise the frame is refused.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np


class SourceError(RuntimeError):
    pass


# --------------------------------------------------------------------- windows

def _win_api():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    return ctypes, wintypes, user32


def list_windows() -> list[dict]:
    if sys.platform != "win32":
        return []
    ctypes, wintypes, user32 = _win_api()
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w > 100 and h > 100:
            out.append({"hwnd": int(hwnd), "title": buf.value, "rect": [rect.left, rect.top, w, h]})
        return True

    user32.EnumWindows(cb, 0)
    return out


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    ctypes, wintypes, user32 = _win_api()
    if not user32.IsWindow(hwnd):
        raise SourceError("selected window no longer exists")
    if user32.IsIconic(hwnd):
        raise SourceError("selected window is minimized")
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _print_window(hwnd: int, w: int, h: int) -> np.ndarray | None:
    """The window's own pixels (BGR, h x w), or None if it cannot render itself."""
    ctypes, wintypes, user32 = _win_api()
    gdi32 = ctypes.windll.gdi32
    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(hdc_mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, hdc_mem, 2):  # PW_RENDERFULLCONTENT
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h  # negative height = top-down rows
        bi.biPlanes, bi.biBitCount = 1, 32
        buf = (ctypes.c_ubyte * (w * h * 4))()
        if gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bi), 0) != h:
            return None
        img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[..., :3].copy()
        if img.max() == 0:  # GPU-drawn windows often render as solid black
            return None
        return img
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_win)


def _uncovered(hwnd: int, rect: tuple[int, int, int, int]) -> bool:
    """True if a 5x5 grid of points across rect all belong to hwnd (nothing on top of it)."""
    ctypes, wintypes, user32 = _win_api()
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.restype = wintypes.HWND
    x, y, w, h = rect
    for i in range(5):
        for j in range(5):
            pt = wintypes.POINT(int(x + (i + 0.5) * w / 5), int(y + (j + 0.5) * h / 5))
            top = user32.WindowFromPoint(pt)
            if not top or int(user32.GetAncestor(top, 2) or 0) != int(hwnd):  # GA_ROOT
                return False
    return True


def list_monitors() -> list[dict]:
    try:
        import mss
        with mss.mss() as s:
            return [{"index": i, "rect": [m["left"], m["top"], m["width"], m["height"]]}
                    for i, m in enumerate(s.monitors) if i > 0]
    except Exception:
        return []


class ScreenSource:
    """target: {"kind": "region", "rect": [x,y,w,h]} or {"kind": "window", "hwnd": int, "title": str,
    "crop": [x,y,w,h] optional, relative to the window}."""
    synthetic = False

    def __init__(self, target: dict):
        import mss
        self.target = target
        self._mss = mss.mss()
        self.label = target.get("title") or f"region {target.get('rect')}"

    def rect(self) -> tuple[int, int, int, int]:
        if self.target["kind"] == "window":
            x, y, w, h = _window_rect(int(self.target["hwnd"]))
            crop = self.target.get("crop")
            if crop:
                cx, cy, cw, ch = crop
                return x + cx, y + cy, min(cw, w - cx), min(ch, h - cy)
            return x, y, w, h
        x, y, w, h = self.target["rect"]
        return int(x), int(y), int(w), int(h)

    def grab(self) -> tuple[np.ndarray, float | None]:
        x, y, w, h = self.rect()
        if w <= 0 or h <= 0:
            raise SourceError("empty capture rectangle")
        self.last_rect = (x, y, w, h)
        if self.target["kind"] == "window":
            return self._grab_window(x, y, w, h), None
        try:
            shot = self._mss.grab({"left": x, "top": y, "width": w, "height": h})
        except Exception as e:
            raise SourceError(f"screen capture failed: {e}") from e
        frame = np.asarray(shot)[..., :3].copy()  # BGRA -> BGR
        return frame, None

    def _grab_window(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        hwnd = int(self.target["hwnd"])
        wx, wy, ww, wh = _window_rect(hwnd)
        img = _print_window(hwnd, ww, wh)
        if img is not None:
            self.capture_method = "window"
            return img[y - wy:y - wy + h, x - wx:x - wx + w].copy()
        # this window can't render itself: read the screen, but only if nothing covers it
        if not _uncovered(hwnd, (x, y, w, h)):
            raise SourceError("game window is covered by another window")
        self.capture_method = "screen"
        try:
            shot = self._mss.grab({"left": x, "top": y, "width": w, "height": h})
        except Exception as e:
            raise SourceError(f"screen capture failed: {e}") from e
        return np.asarray(shot)[..., :3].copy()

    def close(self):
        self._mss.close()


class ReplaySource:
    def __init__(self, folder: Path):
        man_path = folder / "replay.json"
        if not man_path.exists():
            raise SourceError(f"{folder} has no replay.json")
        self.folder = folder
        self.manifest = json.loads(man_path.read_text())
        self.frames = self.manifest["frames"]
        self.synthetic = bool(self.manifest.get("synthetic"))
        self.label = folder.name
        self.index = 0

    @property
    def finished(self) -> bool:
        return self.index >= len(self.frames)

    def restart(self):
        self.index = 0

    def grab(self) -> tuple[np.ndarray, float | None]:
        if self.finished:
            raise SourceError("end of replay")
        f = self.frames[self.index]
        self.index += 1
        img = cv2.imread(str(self.folder / f["file"]), cv2.IMREAD_COLOR)
        if img is None:
            raise SourceError(f"cannot read {f['file']}")
        return img, float(f["t"])

    def close(self):
        pass


def frame_signature(frame: np.ndarray) -> np.ndarray:
    small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 100), interpolation=cv2.INTER_AREA)
    return small.astype(np.float32)


def frame_change(a: np.ndarray | None, b: np.ndarray) -> float:
    """Fraction of downscaled pixels that changed by more than 25 grey levels.

    Downscaling averages away sensor/compression noise, so a single card
    appearing (~1% of the area) is detected while noisy re-captures are not.
    Returns -1.0 for a bit-identical frame.
    """
    if a is None or a.shape != b.shape:
        return 1.0
    d = np.abs(a - b)
    if not d.any():
        return -1.0
    return float((d > 25).mean())
