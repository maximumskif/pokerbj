"""Overlay text is derived only from the analysis; suppressed/stale states never show an action."""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.service import AppService  # noqa: E402
from overlay import overlay_lines, rects_overlap  # noqa: E402


def _state_at(replay, pred):
    async def run():
        svc = AppService(ROOT, Path(tempfile.mkdtemp()))
        svc.settings.poker.mc_samples = 2000
        svc.set_source({"type": "replay", "name": replay})
        svc.start()
        while svc.mode == "running":
            await svc.capture_once()
            if pred(svc.analysis):
                return svc.full_state()
        raise AssertionError("not reached")
    return asyncio.run(run())


def test_blackjack_decision_overlay():
    st = _state_at("synthetic_blackjack", lambda a: a["status"] == "decision")
    v = overlay_lines(st)
    assert v["headline"] == "SURRENDER" and v["tone"] == "ok"
    assert "SYNTHETIC" in v["flags"]
    assert any("16" in line for line in v["lines"])


def test_suppressed_overlay_shows_no_action():
    st = _state_at("synthetic_blackjack", lambda a: a["status"] == "suppressed" and any("uncertain" in r for r in a["suppressed_reasons"]))
    v = overlay_lines(st)
    assert v["headline"].startswith("No recommendation") and v["tone"] == "warn"
    assert not any(w in v["headline"] for w in ("HIT", "STAND", "DOUBLE"))


def test_holdem_overlay_and_stale_and_disconnected():
    st = _state_at("synthetic_holdem", lambda a: a["status"] == "analysis" and a.get("pot_odds") and a["street"] == "flop")
    v = overlay_lines(st)
    assert v["headline"].startswith("Equity ")
    assert any("need" in line for line in v["lines"])
    st["status"]["stale"] = True
    assert overlay_lines(st)["tone"] == "bad" and "STALE" in overlay_lines(st)["flags"]
    d = overlay_lines(None, connected=False)
    assert d["flags"] == ["DISCONNECTED"] and d["tone"] == "bad"


def test_overlap():
    assert rects_overlap((0, 0, 100, 100), (50, 50, 100, 100))
    assert not rects_overlap((0, 0, 100, 100), (100, 0, 50, 50))
