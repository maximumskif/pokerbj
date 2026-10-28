"""State-transition and failure-case tests: misreads, stale/duplicate frames, new hands,
shoe resets, incomplete history, manual correction, and replay end-to-end."""
import asyncio
import time
from pathlib import Path

import numpy as np
import pytest

from app.analysis import analyze_blackjack
from app.engines.bj_rules import BlackjackRules
from app.schemas import Observation
from app.service import AppService
from app.tracking.blackjack_tracker import BlackjackTracker
from app.tracking.poker_tracker import PokerTracker
from app.tracking.stabilizer import Stabilizer
from app.vision import synth

ROOT = Path(__file__).resolve().parent.parent
LAYOUT = synth.blackjack_layout()


def obs(region, value, conf=0.95, kind="card", t=0.0):
    return Observation(region_id=region, kind=kind, value=value, confidence=conf, captured_at=t, frame_id=0)


class Table:
    """Drive stabilizer + tracker with logical frames (region -> value)."""

    def __init__(self, rules=None):
        self.stab = Stabilizer(required_frames=3, min_confidence=0.6, uncertain_after=4)
        self.events = []
        self.tr = BlackjackTracker(LAYOUT, rules or BlackjackRules(), self.events.append)
        self.base = {r.id: ("empty" if r.kind in ("card", "number") else "off") for r in LAYOUT.regions}

    def frame(self, n=1, conf=None, **vals):
        for _ in range(n):
            frame = []
            for r in LAYOUT.regions:
                v = vals.get(r.id, self.base[r.id])
                c = (conf or {}).get(r.id, 0.95)
                frame.append(obs(r.id, v, c, r.kind))
            self.stab.update(frame)
            self.tr.update(self.stab.states)

    def snap(self):
        return self.tr.snapshot(self.stab.states)


def test_single_frame_misread_is_ignored():
    t = Table()
    t.frame(3)
    t.frame(3, p0_0="Tc")
    t.frame(1, p0_0="Jc")  # one-frame misread
    t.frame(2, p0_0="Tc")
    assert t.stab.states["p0_0"].value == "Tc"
    assert t.tr.seen[10] == 1


def test_low_confidence_never_commits_and_becomes_uncertain():
    t = Table()
    t.frame(3)
    t.frame(6, p0_0="7c", conf={"p0_0": 0.3})
    st = t.stab.states["p0_0"]
    assert st.value == "empty" and st.status == "uncertain"
    assert t.tr.cards_seen == 0


def test_cards_counted_once_across_frames_and_moves():
    t = Table()
    t.frame(4, shuffle="on")
    t.frame(3)
    deal = dict(p0_0="8h", p0_1="8c", dealer_0="7s", dealer_1="hidden")
    t.frame(10, **deal)
    assert t.tr.cards_seen == 3  # hole card not counted while hidden
    # split: 8c moves to the second hand, in either order
    t.frame(5, p0_0="8h", p1_0="8c", dealer_0="7s", dealer_1="hidden")
    assert t.tr.cards_seen == 3
    t.frame(5, p0_0="8h", p0_1="3d", p1_0="8c", dealer_0="7s", dealer_1="hidden")
    assert t.tr.cards_seen == 4
    assert not [e for e in t.events if e.kind == "correction"]
    t.frame(5, p0_0="8h", p0_1="3d", p1_0="8c", dealer_0="7s", dealer_1="Qd")
    assert t.tr.cards_seen == 5
    assert t.tr.seen == {1: 0, 2: 0, 3: 1, 4: 0, 5: 0, 6: 0, 7: 1, 8: 2, 9: 0, 10: 1}


def test_manual_correction_adjusts_count():
    t = Table()
    t.frame(4, shuffle="on")
    t.frame(3)
    t.frame(4, p0_0="9d", p0_1="4c", dealer_0="5s")
    assert t.tr.seen[4] == 1
    t.stab.set_override("p0_1", "5c", "card", time.time())  # user says it was the 5c
    t.tr.update(t.stab.states)
    assert t.tr.seen[4] == 0 and t.tr.seen[5] == 2
    assert any(e.kind == "correction" for e in t.events)


def test_new_hand_detection_and_reset():
    t = Table()
    t.frame(3)
    t.frame(4, p0_0="Tc", dealer_0="6d")
    assert t.tr.in_hand and t.tr.hand_id == 1
    t.frame(4)
    assert not t.tr.in_hand
    t.frame(4, p0_0="2c", dealer_0="3d")
    assert t.tr.hand_id == 2


def test_shoe_unknown_until_shuffle_and_after_gap():
    t = Table()
    t.frame(3)
    assert t.snap()["composition"]["mode"] == "unknown"  # started mid-shoe
    t.frame(4, shuffle="on")
    t.frame(3)
    assert t.snap()["composition"]["mode"] == "known"
    t.tr.break_continuity("gap")
    assert t.snap()["composition"]["mode"] == "unknown"
    t.frame(4, shuffle="on")
    t.frame(2)
    assert t.snap()["composition"]["mode"] == "known"
    assert t.snap()["composition"]["cards_seen"] == 0


def test_impossible_counts_invalidate_shoe():
    t = Table(BlackjackRules(decks=1))
    t.frame(4, shuffle="on")
    t.frame(3)
    for hand in range(3):  # 6 sevens seen in a 1-deck shoe -> inconsistent
        t.frame(4, p0_0="7c", p0_1="7d")
        t.frame(4)
    assert t.snap()["composition"]["mode"] == "unknown"
    assert "more 7s" in t.snap()["composition"]["reason"]


def test_csm_never_uses_history():
    t = Table(BlackjackRules(continuous_shuffle=True))
    t.frame(4, shuffle="on")
    assert t.snap()["composition"]["mode"] == "csm"


def test_analysis_suppressed_on_uncertain_or_changing_cards():
    t = Table()
    t.frame(3)
    t.frame(4, p0_0="Tc", p0_1="6s", dealer_0="Kd", dealer_1="hidden", btn_hit="on", btn_stand="on")
    a = analyze_blackjack(t.snap(), BlackjackRules(), 1)
    assert a["status"] == "decision" and a["recommendation"]["action"] == "hit"  # surrender button not lit
    # a third card arrives but is not yet stable
    t.frame(1, p0_0="Tc", p0_1="6s", p0_2="5d", dealer_0="Kd", dealer_1="hidden", btn_hit="on", btn_stand="on")
    a = analyze_blackjack(t.snap(), BlackjackRules(), 2)
    assert a["status"] == "suppressed" and "changing" in a["suppressed_reasons"][0]
    # dealer upcard unreadable -> suppressed
    t2 = Table()
    t2.frame(3)
    t2.frame(8, p0_0="Tc", p0_1="6s", dealer_0="Kd", btn_hit="on", conf={"dealer_0": 0.2})
    a = analyze_blackjack(t2.snap(), BlackjackRules(), 1)
    assert a["status"] == "suppressed"


def test_button_rule_mismatch_warns():
    t = Table()
    t.frame(3)
    t.frame(4, p0_0="Tc", p0_1="5s", p0_2="2d", dealer_0="9d", btn_hit="on", btn_stand="on", btn_double="on")
    a = analyze_blackjack(t.snap(), BlackjackRules(), 1)
    assert any("double" in w and "lit" in w for w in a["warnings"])
    assert a["recommendation"]["action"] in ("hit", "stand")


def test_poker_duplicate_cards_rejected():
    problems = PokerTracker.validate_cards(["Qc", "Qd"], ["Qd", "8s", "2c", None, None])
    assert any("duplicate" in p for p in problems)
    assert any("board" in p for p in PokerTracker.validate_cards(["Ah", "Kh"], ["2c", "3d", None, None, None]))


def test_poker_position():
    seats = [{"seat": i, "stack": {"value": 100}, "has_cards": True, "dealer": i == 3} for i in range(1, 6)]
    # seats are numbered clockwise from hero: BTN seat 3 -> SB 4, BB 5, hero UTG
    assert PokerTracker.position(seats, False)["label"] == "UTG"
    seats = [dict(s, dealer=s["seat"] == 1) for s in seats]
    assert PokerTracker.position(seats, False)["label"] == "CO"
    assert PokerTracker.position(seats, True)["label"] == "BTN"


def test_duplicate_frames_skipped_and_stale_flag():
    async def run():
        import tempfile
        svc = AppService(ROOT, Path(tempfile.mkdtemp()))
        img = synth.blank_table("t")
        await svc.process_frame(img, 0.0)
        for i in range(3):
            await svc.process_frame(img.copy(), 0.5 * (i + 1))  # bit-identical
        assert svc.frames_skipped_duplicate == 3
        assert svc.frames_processed == 1
        svc.mode = "running"
        svc.last_capture_wall = time.time() - 60
        assert svc.status()["stale"]
    asyncio.run(run())


@pytest.mark.slow
def test_replay_end_to_end():
    """The synthetic replay produces the expected decisions with no API key."""
    async def run():
        import tempfile
        svc = AppService(ROOT, Path(tempfile.mkdtemp()))
        svc.set_source({"type": "replay", "name": "synthetic_blackjack"})
        svc.start()
        decisions = []
        while svc.mode == "running":
            await svc.capture_once()
            a = svc.analysis
            if a["status"] == "decision":
                key = (tuple(a["decision"]["cards"]), a["decision"]["dealer_upcard"], a["recommendation"]["action"], a["ev"]["model"])
                if not decisions or decisions[-1] != key:
                    decisions.append(key)
        return decisions, svc
    if not (ROOT / "replays" / "synthetic_blackjack" / "replay.json").exists():
        pytest.skip("run scripts/make_replays.py first")
    decisions, svc = asyncio.run(run())
    actions = [(" ".join(c), up, act) for c, up, act, _ in decisions]
    assert actions == [
        ("Tc 6s", "Kd", "surrender"),
        ("6d 5h", "6c", "double"),
        ("8h 8c", "7s", "split"),
        ("8h 3d", "7s", "double"),
        ("8c Ks", "7s", "stand"),
        ("Ad 7c", "9h", "hit"),
        ("Ad 7c 2s", "9h", "stand"),
        ("Ts 2c", "Ah", "hit"),
        ("9h 9c", "7c", "stand"),
    ]
    models = [m for *_, m in decisions]
    assert models[-2] == "infinite_deck"  # after the 40 s gap
    assert models[-1] == "composition"  # after the second shuffle
    assert any("uncertain" in e["message"] or e["kind"] == "warning" for e in svc.timeline)
