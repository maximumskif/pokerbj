"""Card primitives shared by both games."""
from __future__ import annotations

import re
from dataclasses import dataclass

RANKS = "23456789TJQKA"
SUITS = "cdhs"
SUIT_NAMES = {"c": "clubs", "d": "diamonds", "h": "hearts", "s": "spades"}
RED_SUITS = {"d", "h"}

_CARD_RE = re.compile(r"^(10|[2-9TJQKA])([cdhs])$", re.IGNORECASE)


@dataclass(frozen=True, order=True)
class Card:
    rank: str  # one of RANKS
    suit: str  # one of SUITS

    def __post_init__(self) -> None:
        if self.rank not in RANKS or self.suit not in SUITS:
            raise ValueError(f"invalid card {self.rank}{self.suit}")

    @classmethod
    def parse(cls, text: str) -> "Card":
        m = _CARD_RE.match(text.strip())
        if not m:
            raise ValueError(f"cannot parse card {text!r}")
        rank = m.group(1).upper()
        rank = "T" if rank == "10" else rank
        return cls(rank, m.group(2).lower())

    @property
    def rank_value(self) -> int:
        """2..14 (ace high) for poker."""
        return RANKS.index(self.rank) + 2

    @property
    def bj_value(self) -> int:
        """1..10 for blackjack (ace = 1, faces = 10)."""
        if self.rank == "A":
            return 1
        if self.rank in "TJQK":
            return 10
        return int(self.rank)

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"


def full_deck() -> list[Card]:
    return [Card(r, s) for r in RANKS for s in SUITS]


def parse_cards(text: str) -> list[Card]:
    text = text.replace(",", " ").strip()
    if not text:
        return []
    # allow "AhKd" as well as "Ah Kd"
    if " " not in text and len(text) > 3:
        parts = re.findall(r"(10|[2-9TJQKAtjqka])([cdhsCDHS])", text)
        return [Card.parse(r + s) for r, s in parts]
    return [Card.parse(t) for t in text.split()]
