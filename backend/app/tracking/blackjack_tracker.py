"""Blackjack hand lifecycle and shoe-history tracking."""
from __future__ import annotations

import time
from collections import Counter
from typing import Callable

from ..cards import Card
from ..engines.bj_rules import BlackjackRules
from ..schemas import Layout, RegionState, TimelineEvent

ACTIONS = ("hit", "stand", "double", "split", "surrender")


class BlackjackTracker:
    def __init__(self, layout: Layout, rules: BlackjackRules, emit: Callable[[TimelineEvent], None]):
        self.layout = layout
        self.rules = rules
        self.emit = emit
        self.card_regions = [r.id for r in layout.regions if r.kind == "card"]
        self.hand_id = 0
        self.in_hand = False
        self.counted_max: dict[str, int] = {}  # card -> max simultaneous copies seen this hand
        self.slot_last: dict[str, str] = {}  # region -> last committed face value
        self._pending_corrections: list[tuple[str, str, str]] = []
        self.seen: dict[int, int] = {v: 0 for v in range(1, 11)}
        self.cards_seen = 0
        self.shoe_known = False
        self.shoe_reason = "shoe start not observed (no shuffle seen since monitoring began)"
        self._shuffle_on = False
        self.hands_this_shoe = 0

    # ------------------------------------------------------------- shoe state
    def _event(self, kind: str, msg: str, **data):
        self.emit(TimelineEvent(t=time.time(), kind=kind, message=msg, data=data))

    def new_shoe(self, reason: str) -> None:
        self.seen = {v: 0 for v in range(1, 11)}
        self.cards_seen = 0
        self.hands_this_shoe = 0
        if self.in_hand:
            # cards already dealt this hand belong to the new shoe count only if the
            # shuffle happened before they were dealt; we cannot know, so stay unknown.
            self.shoe_known = False
            self.shoe_reason = f"{reason}, but mid-hand; waiting for the next shuffle"
        else:
            self.shoe_known = True
            self.shoe_reason = reason
        self._event("shoe", f"New shoe: {reason}")

    def break_continuity(self, reason: str) -> None:
        if self.shoe_known:
            self._event("warning", f"Shoe history now incomplete: {reason}")
        self.shoe_known = False
        self.shoe_reason = reason

    def remaining_counts(self) -> dict[int, int] | None:
        if self.rules.continuous_shuffle:
            return None
        if not self.shoe_known:
            return None
        full = {v: 4 * self.rules.decks for v in range(1, 10)} | {10: 16 * self.rules.decks}
        rem = {v: full[v] - self.seen[v] for v in full}
        return rem

    # ------------------------------------------------------------- frame update
    def update(self, states: dict[str, RegionState]) -> None:
        shuffle = states.get("shuffle")
        shuffle_on = bool(shuffle and shuffle.status in ("stable", "manual") and shuffle.value == "on")
        if shuffle_on and not self._shuffle_on:
            self.new_shoe("shuffle notice observed on screen")
        self._shuffle_on = shuffle_on

        cards = {rid: states.get(rid) for rid in self.card_regions}
        settled = {rid: s for rid, s in cards.items() if s and s.status in ("stable", "manual")}
        present = {rid for rid, s in settled.items() if s.value not in (None, "empty")}
        all_empty = all(s and s.status in ("stable", "manual") and s.value == "empty" for s in cards.values())

        if not self.in_hand and present:
            self.in_hand = True
            self.hand_id += 1
            self.hands_this_shoe += 1
            self.counted_max, self.slot_last = {}, {}
            self._event("hand", f"Hand #{self.hand_id} started")
        if self.in_hand and all_empty:
            self.in_hand = False
            self._event("hand", f"Hand #{self.hand_id} ended; table cleared", cards=dict(self.counted_max))
            self.counted_max, self.slot_last = {}, {}
            return

        if not self.in_hand:
            return
        self._count_visible(settled)
        self._validate_counts()

    def _count_visible(self, settled: dict[str, RegionState]) -> None:
        """Count each physical card once per hand.

        Per card identity we count the maximum number of copies visible at the
        same time during the hand, so cards moving between slots (splits,
        animations) are not double-counted. A slot whose committed value changes
        directly from X to Y (no empty in between) is a correction: X is
        uncounted if fewer copies of X remain visible than were counted.
        """
        visible: Counter[str] = Counter()
        for rid, st in settled.items():
            v = st.value
            prev = self.slot_last.get(rid)
            if v in (None, "empty"):
                self.slot_last.pop(rid, None)
                continue
            if v == "hidden":
                continue
            visible[v] += 1
            if prev is not None and prev != v:
                self._pending_corrections.append((rid, prev, v))
            self.slot_last[rid] = v
        for rid, old, new in self._pending_corrections:
            if visible[old] < self.counted_max.get(old, 0):
                self.counted_max[old] -= 1
                self.seen[Card.parse(old).bj_value] -= 1
                self.cards_seen -= 1
                self._event("correction", f"{rid}: {old} -> {new}; shoe count adjusted (each card counted once)")
        self._pending_corrections = []
        for card, n in visible.items():
            extra = n - self.counted_max.get(card, 0)
            if extra > 0:
                self.counted_max[card] = n
                self.seen[Card.parse(card).bj_value] += extra
                self.cards_seen += extra

    def _validate_counts(self) -> None:
        if not self.shoe_known:
            return
        for v, n in self.seen.items():
            cap = (16 if v == 10 else 4) * self.rules.decks
            if n > cap:
                name = "ten-value cards" if v == 10 else ("aces" if v == 1 else f"{v}s")
                self.break_continuity(f"more {name} seen ({n}) than a {self.rules.decks}-deck shoe holds; misread, wrong deck setting, or missed shuffle")
                return

    # ------------------------------------------------------------- snapshot
    def snapshot(self, states: dict[str, RegionState]) -> dict:
        def val(rid):
            s = states.get(rid)
            return s

        def cards_of(prefix: str):
            out = []
            for rid in sorted((r for r in self.card_regions if r.startswith(prefix)), key=lambda x: int(x.rsplit("_", 1)[1])):
                s = val(rid)
                out.append({"region": rid, "value": s.value if s else None, "status": s.status if s else "pending",
                            "confidence": s.confidence if s else 0.0, "source": s.source if s else "local",
                    "raw": s.last_raw if s else None})
            return out

        def number(rid):
            s = val(rid)
            if not s:
                return {"region": rid, "value": None, "status": "pending", "confidence": 0.0}
            v = int(s.value) if s.value not in (None, "") and s.value.isdigit() else None
            return {"region": rid, "value": v, "status": s.status, "confidence": s.confidence, "source": s.source}

        def on(rid):
            s = val(rid)
            return bool(s and s.status in ("stable", "manual") and s.value == "on")

        hands = []
        for h in (0, 1):
            cs = [c for c in cards_of(f"p{h}_")]
            hands.append({"index": h, "cards": cs, "bet": number(f"bet{h}"), "active": on(f"active{h}")})
        rem = self.remaining_counts()
        if self.rules.continuous_shuffle:
            comp = {"mode": "csm", "label": "Continuous shuffler: every hand is dealt from a full shoe; card history is irrelevant"}
        elif rem is not None:
            hi_lo = sum(self.seen[v] for v in range(2, 7)) - self.seen[10] - self.seen[1]
            decks_left = max(sum(rem.values()) / 52, 0.25)
            comp = {"mode": "known", "label": "Remaining-shoe composition (history observed since shuffle)",
                    "remaining": rem, "cards_seen": self.cards_seen, "hi_lo_running": hi_lo,
                    "hi_lo_true": round(hi_lo / decks_left, 2), "reason": self.shoe_reason}
        else:
            comp = {"mode": "unknown", "label": "Shoe composition unknown; using infinite-deck model", "reason": self.shoe_reason}
        return {
            "hand_id": self.hand_id if self.in_hand else None,
            "in_hand": self.in_hand,
            "dealer": cards_of("dealer_"),
            "hands": hands,
            "bankroll": number("bankroll"),
            "buttons": {a: on(f"btn_{a}") for a in ACTIONS},
            "composition": comp,
        }
