"""Texas Hold'em hand evaluator (best 5 of 5-7 cards).

Returns a comparable integer: higher is better, equal means a tie.
Validated in tests against the published 5-card category frequencies
(e.g. 40 straight flushes, 624 quads ... out of 2,598,960).
"""
from __future__ import annotations

from collections import Counter

from ..cards import Card

CATEGORIES = [
    "High card", "One pair", "Two pair", "Three of a kind", "Straight",
    "Flush", "Full house", "Four of a kind", "Straight flush",
]


def _straight_high(ranks: set[int]) -> int:
    """Highest straight top card among ranks (2..14), 0 if none. Wheel = 5."""
    rs = set(ranks)
    if 14 in rs:
        rs.add(1)
    for top in range(14, 4, -1):
        if all(r in rs for r in range(top - 4, top + 1)):
            return top
    return 0


def _score(cat: int, kickers: list[int]) -> int:
    s = cat
    for i in range(5):
        s = s * 16 + (kickers[i] if i < len(kickers) else 0)
    return s


def evaluate(cards: list[Card]) -> int:
    if not 5 <= len(cards) <= 7:
        raise ValueError("need 5-7 cards")
    ranks = [c.rank_value for c in cards]
    by_suit: dict[str, list[int]] = {}
    for c in cards:
        by_suit.setdefault(c.suit, []).append(c.rank_value)
    flush_ranks = next((rs for rs in by_suit.values() if len(rs) >= 5), None)
    if flush_ranks:
        sf = _straight_high(set(flush_ranks))
        if sf:
            return _score(8, [sf])
    counts = Counter(ranks)
    # sort by (count, rank) descending
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    if groups[0][1] == 4:
        quad = groups[0][0]
        kicker = max(r for r in ranks if r != quad)
        return _score(7, [quad, kicker])
    trips = sorted([r for r, n in counts.items() if n >= 3], reverse=True)
    pairs = sorted([r for r, n in counts.items() if n == 2], reverse=True)
    if trips and (len(trips) > 1 or pairs):
        t = trips[0]
        p = max(([trips[1]] if len(trips) > 1 else []) + pairs)
        return _score(6, [t, p])
    if flush_ranks:
        return _score(5, sorted(flush_ranks, reverse=True)[:5])
    st = _straight_high(set(ranks))
    if st:
        return _score(4, [st])
    if trips:
        t = trips[0]
        kick = sorted((r for r in ranks if r != t), reverse=True)[:2]
        return _score(3, [t] + kick)
    if len(pairs) >= 2:
        p1, p2 = pairs[0], pairs[1]
        kick = max(r for r in ranks if r != p1 and r != p2)
        return _score(2, [p1, p2, kick])
    if pairs:
        p = pairs[0]
        kick = sorted((r for r in ranks if r != p), reverse=True)[:3]
        return _score(1, [p] + kick)
    return _score(0, sorted(ranks, reverse=True)[:5])


def category(score: int) -> str:
    return CATEGORIES[score // (16 ** 5)]
