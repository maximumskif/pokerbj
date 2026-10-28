"""Generate the SYNTHETIC demo layouts and replay sessions.

Everything produced here is fabricated demo data, watermarked "SYNTHETIC DEMO"
on every frame and flagged synthetic=true in replay.json.

Usage:  python scripts/make_replays.py
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.vision import synth  # noqa: E402

FPS_DT = 0.5
WATERMARK = "SYNTHETIC DEMO DATA - not a real game"


@dataclass
class Writer:
    folder: Path
    game: str
    layout: str
    description: str
    t: float = 0.0
    frames: list = field(default_factory=list)
    rng: np.random.Generator = field(default_factory=lambda: np.random.default_rng(42))
    notes: list = field(default_factory=list)

    def __post_init__(self):
        if self.folder.exists():
            shutil.rmtree(self.folder)
        self.folder.mkdir(parents=True)

    def add(self, img: np.ndarray, n: int = 1, duplicate: bool = False):
        """Write n frames. duplicate=True reuses the exact same encoded image (bit-identical)."""
        enc = synth.degrade(img, self.rng)
        for i in range(n):
            if not duplicate and i > 0:
                enc = synth.degrade(img, self.rng)
            name = f"frame_{len(self.frames):05d}.jpg"
            cv2.imwrite(str(self.folder / name), enc, [cv2.IMWRITE_JPEG_QUALITY, 92])
            self.frames.append({"file": name, "t": round(self.t, 3)})
            self.t += FPS_DT

    def gap(self, seconds: float, why: str):
        self.notes.append(f"t={self.t:.1f}s: {why}")
        self.t += seconds

    def note(self, msg: str):
        self.notes.append(f"t={self.t:.1f}s (frame {len(self.frames)}): {msg}")

    def finish(self):
        man = {"game": self.game, "layout": self.layout, "synthetic": True, "description": self.description,
               "notes": self.notes, "frames": self.frames}
        (self.folder / "replay.json").write_text(json.dumps(man, indent=1))
        print(f"{self.folder.name}: {len(self.frames)} frames")


# ------------------------------------------------------------------ blackjack

class BJScene:
    def __init__(self, layout):
        self.lay = layout
        self.dealer: list[str] = []
        self.hands: list[list[str]] = [[], []]
        self.bets = [None, None]
        self.bankroll = 1000
        self.buttons: set[str] = set()
        self.active: int | None = None
        self.shuffle = False

    def render(self, anim: str | None = None, occlude: str | None = None, smudge: str | None = None):
        img = synth.blank_table(WATERMARK)
        R = lambda rid: self.lay.region(rid).rect  # noqa: E731
        for i, c in enumerate(self.dealer):
            rid = f"dealer_{i}"
            self._card(img, rid, c, anim, occlude, smudge)
        for h in (0, 1):
            for i, c in enumerate(self.hands[h]):
                self._card(img, f"p{h}_{i}", c, anim, occlude, smudge)
            synth.draw_number(img, R(f"bet{h}"), self.bets[h])
            synth.draw_indicator(img, R(f"active{h}"), self.active == h)
        synth.draw_number(img, R("bankroll"), self.bankroll)
        for a in ("hit", "stand", "double", "split", "surrender"):
            synth.draw_indicator(img, R(f"btn_{a}"), a in self.buttons, a.upper())
        synth.draw_indicator(img, R("shuffle"), self.shuffle, "SHUFFLING" if self.shuffle else "")
        return img

    def _card(self, img, rid, c, anim, occlude, smudge):
        rect = self.lay.region(rid).rect
        if rid == anim:
            synth.draw_card(img, rect, c, dx=-30, dy=-25, blur=2.5)
        elif rid == smudge:
            synth.draw_card(img, rect, c, blur=4.5)
        else:
            synth.draw_card(img, rect, c, occlude=(rid == occlude))


def make_blackjack(out: Path):
    lay = synth.blackjack_layout()
    w = Writer(out, "blackjack", lay.name,
               "SYNTHETIC: 7 scripted 6-deck S17 hands. Includes deal animations, an occluded card, duplicate frames, "
               "a persistently unreadable card (needs manual correction), a 40 s observation gap and two shuffles.")
    s = BJScene(lay)

    def deal(target: str, card: str, hold: int = 3):
        if target == "d":
            s.dealer.append(card)
            rid = f"dealer_{len(s.dealer) - 1}"
        else:
            h = int(target)
            s.hands[h].append(card)
            rid = f"p{h}_{len(s.hands[h]) - 1}"
        w.add(s.render(anim=rid))
        w.add(s.render(), hold)
        return rid

    def decide(buttons: set[str], active: int = 0, hold: int = 5, **fx):
        s.buttons, s.active = buttons, active
        w.add(s.render(**fx), hold)
        s.buttons = set()

    def clear(hold: int = 3):
        s.dealer, s.hands, s.bets, s.active = [], [[], []], [None, None], None
        w.add(s.render(), 1)
        w.add(s.render(), hold, duplicate=True)

    def reveal(card: str):
        s.dealer[1] = card
        w.add(s.render(), 3)

    def shuffle():
        s.shuffle = True
        w.note("shuffle notice shown")
        w.add(s.render(), 4)
        s.shuffle = False
        w.add(s.render(), 3, duplicate=True)

    shuffle()
    # Hand 1: hard 16 vs 10 -> chart says surrender (late surrender allowed)
    s.bets[0] = 25; w.add(s.render(), 2)
    deal("0", "Tc"); deal("d", "Kd"); deal("0", "6s"); deal("d", "hidden")
    w.note("hand 1 decision: 16 vs K")
    decide({"hit", "stand", "double", "surrender"})
    s.bankroll -= 13; reveal("7h"); clear()

    # Hand 2: 11 vs 6 -> double
    s.bets[0] = 25; w.add(s.render(), 2)
    deal("0", "6d"); deal("d", "6c"); deal("0", "5h"); deal("d", "hidden")
    w.note("hand 2 decision: 11 vs 6")
    decide({"hit", "stand", "double", "surrender"})
    s.bets[0] = 50; w.add(s.render(), 2)
    deal("0", "9s"); reveal("Tc"); deal("d", "8d")  # dealer 24 bust
    s.bankroll += 50; clear()

    # Hand 3: 8,8 vs 7 -> split; first split hand 8+3 = 11 -> double (DAS)
    s.bets[0] = 25; w.add(s.render(), 2)
    deal("0", "8h"); deal("d", "7s"); deal("0", "8c"); deal("d", "hidden")
    w.note("hand 3 decision: 8,8 vs 7")
    decide({"hit", "stand", "double", "split", "surrender"}, occlude="p0_1", hold=1)  # cursor over a card
    decide({"hit", "stand", "double", "split", "surrender"}, hold=5)
    s.hands = [["8h"], ["8c"]]; s.bets = [25, 25]
    w.add(s.render(anim="p1_0")); w.add(s.render(), 3)
    deal("0", "3d")
    w.note("hand 3 split hand 1: 11 vs 7")
    decide({"hit", "stand", "double"}, active=0)
    s.bets[0] = 50; w.add(s.render(), 1)
    deal("0", "Th")
    deal("1", "Ks")
    w.note("hand 3 split hand 2: 18 vs 7")
    decide({"hit", "stand"}, active=1)
    reveal("Qd")  # dealer 17
    s.bankroll += 75; clear()

    # Hand 4: soft 18 vs 9 -> hit
    s.bets[0] = 25; w.add(s.render(), 2)
    deal("0", "Ad"); deal("d", "9h"); deal("0", "7c"); deal("d", "hidden")
    w.note("hand 4 decision: A,7 vs 9")
    decide({"hit", "stand", "double", "surrender"})
    deal("0", "2s")
    w.note("hand 4 decision: soft 20 (3 cards) vs 9")
    decide({"hit", "stand"})
    reveal("Jc"); s.bankroll += 25; clear()

    # Hand 5: one player card is persistently blurred (unreadable) -> uncertain, suppressed
    s.bets[0] = 25; w.add(s.render(), 2)
    deal("0", "9d"); deal("d", "5s")
    s.hands[0].append("4c"); w.add(s.render(smudge="p0_1"), 3)
    s.dealer.append("hidden"); w.add(s.render(smudge="p0_1"), 2)
    w.note("hand 5: second player card unreadable (blurred) - use manual correction (it is 4c)")
    decide({"hit", "stand", "double", "surrender"}, smudge="p0_1", hold=10)
    reveal("Td"); s.bankroll -= 25
    clear()

    w.gap(40, "40 s observation gap (e.g. capture paused / disconnected) -> shoe history invalid")
    # Hand 6 after gap: composition must be unknown
    s.bets[0] = 25; w.add(s.render(), 2)
    deal("0", "Ts"); deal("d", "Ah"); deal("0", "2c"); deal("d", "hidden")
    w.note("hand 6 decision: 12 vs A, shoe composition unknown after the gap")
    decide({"hit", "stand", "double", "surrender"})
    deal("0", "Kc")  # bust
    s.bankroll -= 25; reveal("6d"); clear()

    shuffle()
    # Hand 7: fresh shoe, composition known again; 9,9 vs 7 -> stand
    s.bets[0] = 25; w.add(s.render(), 2)
    deal("0", "9h"); deal("d", "7c"); deal("0", "9c"); deal("d", "hidden")
    w.note("hand 7 decision: 9,9 vs 7 (fresh shoe)")
    decide({"hit", "stand", "double", "split", "surrender"})
    reveal("Kh"); s.bankroll += 25; clear(4)
    w.finish()
    return lay


# ------------------------------------------------------------------ hold'em

class HEScene:
    def __init__(self, layout):
        self.lay = layout
        self.hero: list[str] = []
        self.board: list[str] = []
        self.pot = 0
        self.hero_stack = 1000
        self.hero_bet = None
        self.dealer_seat = 0  # 0 = hero
        self.seats = {i: {"cards": False, "stack": 1000, "bet": None} for i in range(1, 6)}
        self.buttons: set[str] = set()
        self.to_call = None

    def render(self, anim=None):
        img = synth.blank_table(WATERMARK)
        R = lambda rid: self.lay.region(rid).rect  # noqa: E731
        for i, c in enumerate(self.hero):
            rid = f"hero_{i}"
            synth.draw_card(img, R(rid), c, **({"dx": -30, "dy": -25, "blur": 2.5} if rid == anim else {}))
        for i, c in enumerate(self.board):
            rid = f"board_{i}"
            synth.draw_card(img, R(rid), c, **({"dx": -30, "dy": -25, "blur": 2.5} if rid == anim else {}))
        synth.draw_number(img, R("pot"), self.pot)
        synth.draw_number(img, R("hero_stack"), self.hero_stack)
        synth.draw_number(img, R("hero_bet"), self.hero_bet)
        synth.draw_indicator(img, R("hero_dealer"), self.dealer_seat == 0, "D" if self.dealer_seat == 0 else "")
        for i, st in self.seats.items():
            synth.draw_indicator(img, R(f"seat{i}_cards"), st["cards"], "CARDS" if st["cards"] else "")
            synth.draw_number(img, R(f"seat{i}_stack"), st["stack"])
            synth.draw_number(img, R(f"seat{i}_bet"), st["bet"])
            synth.draw_indicator(img, R(f"seat{i}_dealer"), self.dealer_seat == i)
        for a in ("fold", "check", "call", "raise"):
            synth.draw_indicator(img, R(f"btn_{a}"), a in self.buttons, a.upper())
        synth.draw_number(img, R("to_call"), self.to_call)
        return img


def make_holdem(out: Path):
    lay = synth.holdem_layout()
    w = Writer(out, "holdem", lay.name,
               "SYNTHETIC: two scripted 6-max hands. Hand 1 goes to the river; hand 2 contains an injected "
               "duplicate-card misread (board shows hero's card) to demonstrate validation and suppression.")
    s = HEScene(lay)

    def bet(seat, amt):
        st = s.seats[seat]
        add = amt - (st["bet"] or 0)
        st["stack"] -= add
        st["bet"] = amt

    def collect():
        for st in s.seats.values():
            s.pot += st["bet"] or 0
            st["bet"] = None
        s.pot += s.hero_bet or 0
        s.hero_bet = None

    def to_act(call):
        s.buttons = {"fold", "call", "raise"} if call else {"check", "raise"}
        s.to_call = call or None
        w.add(s.render(), 6)
        s.buttons, s.to_call = set(), None

    # Hand 1: hero on the button with AhKh
    for st in s.seats.values():
        st["cards"] = True
    bet(1, 10); bet(2, 20)
    w.add(s.render(), 3)
    s.hero.append("Ah"); w.add(s.render(anim="hero_0")); w.add(s.render(), 2)
    s.hero.append("Kh"); w.add(s.render(anim="hero_1")); w.add(s.render(), 3)
    bet(3, 60); w.add(s.render(), 3)
    s.seats[4]["cards"] = False; w.add(s.render(), 3)
    s.seats[5]["cards"] = False; w.add(s.render(), 3)
    w.note("hand 1 preflop decision: AhKh on the button facing a raise to 60")
    to_act(60)
    s.hero_bet = 60; s.hero_stack -= 60; w.add(s.render(), 3)
    s.seats[1]["cards"] = False; w.add(s.render(), 3)
    bet(2, 60); w.add(s.render(), 3)
    collect(); w.add(s.render(), 3)
    for c in ("2h", "7h", "9c"):
        s.board.append(c); w.add(s.render(anim=f"board_{len(s.board) - 1}")); w.add(s.render(), 3)
    bet(3, 100); w.add(s.render(), 3)
    s.seats[2]["cards"] = False; w.add(s.render(), 3)
    w.note("hand 1 flop decision: nut flush draw facing 100 into 190")
    to_act(100)
    s.hero_bet = 100; s.hero_stack -= 100; w.add(s.render(), 3)
    collect(); w.add(s.render(), 3)
    s.board.append("Qs"); w.add(s.render(anim="board_3")); w.add(s.render(), 3)
    bet(3, 200); w.add(s.render(), 3)
    w.note("hand 1 turn decision: facing 200 into 390")
    to_act(200)
    s.hero_bet = 200; s.hero_stack -= 200; w.add(s.render(), 3)
    collect(); w.add(s.render(), 3)
    s.board.append("3d"); w.add(s.render(anim="board_4")); w.add(s.render(), 3)
    w.note("hand 1 river: hero missed; checks")
    to_act(0)
    bet(3, 300); w.add(s.render(), 3)
    to_act(300)
    s.hero, s.board = [], []; s.seats[3]["cards"] = False
    collect(); s.pot = 0
    w.add(s.render(), 4, duplicate=True)

    # Hand 2: button moves to seat 1; injected duplicate-card misread on the flop
    s.dealer_seat = 1
    for st in s.seats.values():
        st["cards"] = True
    bet(2, 10); bet(3, 20); w.add(s.render(), 3)
    s.hero = ["Qc", "Qd"]; w.add(s.render(), 4)
    bet(4, 50); w.add(s.render(), 3)
    for i in (5, 2):
        s.seats[i]["cards"] = False; w.add(s.render(), 2)
    w.note("hand 2 preflop decision: QcQd facing a raise to 50")
    to_act(50)
    s.hero_bet = 50; s.hero_stack -= 50; bet(3, 50); w.add(s.render(), 3)
    collect(); w.add(s.render(), 2)
    s.board = ["Qd", "8s", "2c"]
    w.note("INJECTED MISREAD: board shows Qd, which is also a hero card (impossible)")
    w.add(s.render(), 6)
    s.board = ["Qh", "8s", "2c"]
    w.note("misread resolved: board is Qh 8s 2c")
    w.add(s.render(), 3)
    bet(4, 80); w.add(s.render(), 3)
    to_act(80)
    s.hero = []; s.board = []
    for st in s.seats.values():
        st["cards"] = False
    collect(); s.pot = 0
    w.add(s.render(), 4, duplicate=True)
    w.finish()
    return lay


def main():
    layouts = ROOT / "layouts"
    layouts.mkdir(exist_ok=True)
    replays = ROOT / "replays"
    bj = make_blackjack(replays / "synthetic_blackjack")
    he = make_holdem(replays / "synthetic_holdem")
    for lay in (bj, he):
        (layouts / f"{lay.name}.json").write_text(lay.model_dump_json(indent=1))
    print("layouts written")


if __name__ == "__main__":
    main()
