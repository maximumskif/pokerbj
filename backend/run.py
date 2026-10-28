"""Launch the local service (API + built dashboard) on 127.0.0.1 only."""
import os
import subprocess
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    if not (ROOT / "replays" / "synthetic_blackjack" / "replay.json").exists():
        print("Generating synthetic demo replays (first run)...")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_replays.py")], check=True)
    port = int(os.environ.get("TABLE_ASSISTANT_PORT", "8765"))
    print(f"Dashboard: http://127.0.0.1:{port}  (localhost only)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info", app_dir=str(ROOT))
