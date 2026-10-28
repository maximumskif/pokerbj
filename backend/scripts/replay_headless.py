"""Run a replay through the full pipeline without the UI and print what the dashboard would show.

Usage: python scripts/replay_headless.py [synthetic_blackjack|synthetic_holdem]
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.service import AppService  # noqa: E402


def summarize(a: dict) -> str:
    if a["game"] == "blackjack":
        if a["status"] == "decision":
            d, r, ev = a["decision"], a["recommendation"], a["ev"]
            evs = ", ".join(f"{k} {v:+.3f}" for k, v in ev["actions"].items())
            return (f"DECISION {' '.join(d['cards'])} ({'soft ' if d['soft'] else ''}{d['total']}) vs {d['dealer_upcard']}: "
                    f"{r['action'].upper()} [{r['source']}] | EV {evs} | model={ev['model']}")
        if a["status"] == "suppressed":
            return "SUPPRESSED: " + "; ".join(a["suppressed_reasons"])
        return a["status"]
    if a["status"] == "analysis":
        e = a["equity"]
        po = a.get("pot_odds")
        s = (f"{a['street']} {' '.join(a['inputs']['hero'])} | {' '.join(a['inputs']['board']) or '-'} | "
             f"{a['hand_strength']['made_hand']} | equity {e['equity']:.3f} ±{1.96 * e['std_error']:.3f} "
             f"({e['method']}, n={e['samples']}) vs {len(e['opponents'])} opp")
        if po:
            s += f" | call {po['to_call']} into {po['pot_before_call']} -> break-even {po['break_even_equity']:.3f}"
        return s
    if a["status"] == "suppressed":
        return "SUPPRESSED: " + "; ".join(a["suppressed_reasons"])
    return a["status"]


async def main(name: str):
    with tempfile.TemporaryDirectory() as tmp:
        svc = AppService(ROOT, Path(tmp))
        svc.settings.poker.mc_samples = 6000
        svc.set_source({"type": "replay", "name": name})
        svc.start()
        last = None
        seen_events = 0
        while svc.mode == "running":
            await svc.capture_once()
            tl = list(svc.timeline)
            for ev in tl[seen_events:]:
                print(f"  [{ev.get('capture_t', 0):6.1f}s] {ev['kind']:>10}: {ev['message']}")
            seen_events = len(tl)
            line = summarize(svc.analysis)
            if line != last and svc.analysis["status"] not in ("idle", "observing"):
                print(f"v{svc.version:<4} {line}")
                last = line
        st = svc.status()
        print(f"\nframes processed {st['frames_processed']}, duplicates skipped {st['frames_skipped_duplicate']}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "synthetic_blackjack"))
