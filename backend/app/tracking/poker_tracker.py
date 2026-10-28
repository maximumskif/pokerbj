"""Texas Hold'em hand lifecycle, street, positions and observed betting history."""
from __future__ import annotations

import re
import time
from typing import Callable

from ..cards import Card
from ..schemas import Layout, RegionState, TimelineEvent

STREETS = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}


def _ok(s: RegionState | None) -> bool:
    return bool(s and s.status in ("stable", "manual"))


class PokerTracker:
    def __init__(self, layout: Layout, emit: Callable[[TimelineEvent], None]):
        self.layout = layout
        self.emit = emit
        self.seats = sorted({int(m.group(1)) for r in layout.regions if (m := re.match(r"seat(\d+)_", r.id))})
        self.hand_id = 0
        self.in_hand = False
        self.street = None
        self.history: list[dict] = []
        self._bets: dict[str, int] = {}
        self._had_cards: set[int] = set()
        self._board_n = 0

    def _event(self, kind, msg, **data):
        self.emit(TimelineEvent(t=time.time(), kind=kind, message=msg, data=data))

    def _num(self, states, rid) -> int | None:
        s = states.get(rid)
        if _ok(s) and s.value and s.value.isdigit():
            return int(s.value)
        return None

    def update(self, states: dict[str, RegionState]) -> None:
        hero = [states.get(f"hero_{i}") for i in range(2)]
        hero_present = all(_ok(s) and s.value not in (None, "empty", "hidden") for s in hero)
        hero_empty = all(_ok(s) and s.value == "empty" for s in hero)
        board = [states.get(f"board_{i}") for i in range(5)]
        board_n = sum(1 for s in board if _ok(s) and s.value not in (None, "empty"))

        if not self.in_hand and hero_present:
            self.in_hand = True
            self.hand_id += 1
            self.history = []
            self._bets = {}
            self._had_cards = set()
            self.street = "preflop"
            self._board_n = board_n
            self._event("hand", f"Hold'em hand #{self.hand_id} started")
        elif self.in_hand and hero_empty:
            self.in_hand = False
            self._event("hand", f"Hold'em hand #{self.hand_id} ended")
            return
        if not self.in_hand:
            return

        if board_n < self._board_n:
            self._event("warning", "Board cards disappeared mid-hand; treating as a new hand / table change")
            self.in_hand = False
            return
        if board_n != self._board_n and board_n in STREETS:
            self.street = STREETS[board_n]
            self._bets = {}
            self.history.append({"street": self.street, "event": "street", "text": f"{self.street.title()} dealt"})
            self._event("street", f"{self.street.title()} dealt")
        self._board_n = board_n

        for i in self.seats:
            cards = states.get(f"seat{i}_cards")
            if _ok(cards) and cards.value == "on":
                self._had_cards.add(i)
            elif _ok(cards) and cards.value == "off" and i in self._had_cards:
                self._had_cards.discard(i)
                self.history.append({"street": self.street, "event": "fold", "seat": i, "text": f"Seat {i}: cards no longer visible (fold observed)"})
        for rid, who in [(f"seat{i}_bet", f"Seat {i}") for i in self.seats] + [("hero_bet", "Hero")]:
            amt = self._num(states, rid)
            if amt is None:
                continue
            prev = self._bets.get(rid, 0)
            if amt > prev:
                self.history.append({"street": self.street, "event": "bet", "who": who, "amount": amt,
                                     "text": f"{who}: bet observed {amt}" + (f" (was {prev})" if prev else "")})
                self._bets[rid] = amt

    def snapshot(self, states: dict[str, RegionState]) -> dict:
        def card(rid):
            s = states.get(rid)
            return {"region": rid, "value": s.value if s else None, "status": s.status if s else "pending",
                    "confidence": s.confidence if s else 0.0, "source": s.source if s else "local",
                    "raw": s.last_raw if s else None}

        def number(rid):
            s = states.get(rid)
            v = int(s.value) if s and s.value and s.value.isdigit() else None
            return {"region": rid, "value": v, "status": s.status if s else "pending", "confidence": s.confidence if s else 0.0,
                    "changing": bool(s and s.status != "manual" and s.last_raw != s.value)}

        def on(rid):
            s = states.get(rid)
            return bool(_ok(s) and s.value == "on")

        seats = []
        for i in self.seats:
            seats.append({"seat": i, "has_cards": on(f"seat{i}_cards"), "stack": number(f"seat{i}_stack"),
                          "bet": number(f"seat{i}_bet"), "dealer": on(f"seat{i}_dealer")})
        hero_cards = [card(f"hero_{i}") for i in range(2)]
        board = [card(f"board_{i}") for i in range(5)]
        return {
            "hand_id": self.hand_id if self.in_hand else None,
            "in_hand": self.in_hand,
            "street": self.street if self.in_hand else None,
            "hero": hero_cards,
            "board": board,
            "pot": number("pot"),
            "to_call": number("to_call"),
            "hero_stack": number("hero_stack"),
            "hero_bet": number("hero_bet"),
            "hero_dealer": on("hero_dealer"),
            "seats": seats,
            "buttons": {a: on(f"btn_{a}") for a in ("fold", "check", "call", "raise")},
            "history": list(self.history),
            "position": self.position(seats, on("hero_dealer")),
        }

    @staticmethod
    def position(seats: list[dict], hero_dealer: bool) -> dict:
        """Hero position from the dealer button. Seats are numbered clockwise after hero."""
        occupied = [0] + [s["seat"] for s in seats if s["stack"]["value"] is not None or s["has_cards"]]
        btn = 0 if hero_dealer else next((s["seat"] for s in seats if s["dealer"]), None)
        if btn is None or btn not in occupied:
            return {"label": None, "reason": "dealer button not detected"}
        n = len(occupied)
        order = occupied[occupied.index(btn) + 1:] + occupied[:occupied.index(btn) + 1]  # SB first, BTN last
        names_by_n = {
            2: ["BB", "BTN/SB"], 3: ["SB", "BB", "BTN"], 4: ["SB", "BB", "CO", "BTN"],
            5: ["SB", "BB", "UTG", "CO", "BTN"], 6: ["SB", "BB", "UTG", "HJ", "CO", "BTN"],
        }
        names = names_by_n.get(n)  # heads-up: the button posts the small blind
        if not names:
            return {"label": None, "reason": f"{n} occupied seats not supported"}
        return {"label": names[order.index(0)], "players": n, "button_seat": btn}

    @staticmethod
    def validate_cards(hero: list[str | None], board: list[str | None]) -> list[str]:
        problems = []
        known = [c for c in hero + board if c not in (None, "empty", "hidden")]
        if len(set(known)) != len(known):
            dups = sorted({c for c in known if known.count(c) > 1})
            problems.append(f"duplicate physical card(s) {', '.join(dups)} — impossible in one deck; a reading is wrong")
        n_board = sum(1 for c in board if c not in (None, "empty"))
        if n_board not in STREETS:
            problems.append(f"{n_board} board cards visible (expected 0, 3, 4 or 5); cards may still be animating")
        for c in known:
            Card.parse(c)
        return problems
