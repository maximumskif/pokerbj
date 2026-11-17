"""Start Screen Assistant: the local service on 127.0.0.1 plus the floating chat box."""
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent


def open_chatbox(url: str) -> None:
    for _ in range(60):  # wait for the server to answer
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    subprocess.Popen([sys.executable, str(ROOT / "chatbox.py"), "--url", url])


if __name__ == "__main__":
    port = int(os.environ.get("TABLE_ASSISTANT_PORT", "8765"))
    url = f"http://127.0.0.1:{port}"
    if "--no-box" not in sys.argv:
        threading.Thread(target=open_chatbox, args=(url,), daemon=True).start()
    print(f"Screen Assistant running. Bigger view: {url}  (localhost only)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info", app_dir=str(ROOT))
