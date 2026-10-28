"""Download the official Stockfish binary for this platform into backend/engines/.

Source: https://github.com/official-stockfish/Stockfish/releases (GPLv3).
Alternative: install Stockfish yourself and set STOCKFISH_PATH in .env.
Usage: python scripts/get_stockfish.py
"""
from __future__ import annotations

import io
import json
import platform
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

DEST = Path(__file__).resolve().parent.parent / "engines"
API = "https://api.github.com/repos/official-stockfish/Stockfish/releases/latest"


def asset_name() -> str:
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "stockfish-windows-arm64-universal.zip" if arm else "stockfish-windows-x86-64-universal.zip"
    if sys.platform == "darwin":
        return "stockfish-macos-universal.tar.gz"
    return "stockfish-linux-arm64-universal.tar.gz" if arm else "stockfish-linux-x86-64-universal.tar.gz"


def main() -> None:
    with urllib.request.urlopen(API, timeout=30) as r:
        rel = json.load(r)
    name = asset_name()
    asset = next(a for a in rel["assets"] if a["name"] == name)
    print(f"Downloading {rel['tag_name']} {name} ...")
    with urllib.request.urlopen(asset["browser_download_url"], timeout=300) as r:
        data = r.read()
    DEST.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:  # extract into an empty directory, then copy only the binary
        if name.endswith(".zip"):
            zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
        else:
            tarfile.open(fileobj=io.BytesIO(data)).extractall(tmp, filter="data")
        candidates = [p for p in Path(tmp).rglob("stockfish*")
                      if p.is_file() and p.suffix in ("", ".exe") and p.stat().st_size > 1_000_000]
        if not candidates:
            raise SystemExit("no engine binary found in the archive")
        src = max(candidates, key=lambda p: p.stat().st_size)
        out = DEST / ("stockfish.exe" if sys.platform == "win32" else "stockfish")
        out.write_bytes(src.read_bytes())
        out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    (DEST / "VERSION").write_text(rel["tag_name"])
    print(f"Installed {out}")


if __name__ == "__main__":
    main()
