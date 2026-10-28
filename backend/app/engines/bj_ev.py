"""Blackjack expected-value engine.

Model: each drawn card has value v in 1..10 with fixed probability p[v]
(infinite-deck model, or probabilities frozen at a known remaining-shoe
composition). Player play after the current decision is total-dependent and
optimal under that model. Units: expected net result in initial-wager units
(+1 = win one original bet). Values account for the extra stake on doubles and
splits.

Peek games: values are conditional on the dealer having checked and NOT having
blackjack. No-hole-card games: values are unconditional and include losing to a
dealer blackjack (all stakes, or original bet only with OBO).

Split EV = 2 x EV(one hand starting from the split card), which is exact under
the model by linearity of expectation, but ignores resplits (a lower bound when
resplitting is allowed).
"""
from __future__ import annotations

from functools import lru_cache

from .bj_rules import BlackjackRules, hand_total

INFINITE_DECK = tuple([1 / 13] * 9 + [4 / 13])  # index 0 -> ace ... index 9 -> ten-value
OUTCOMES = (17, 18, 19, 20, 21, "bust")


def probs_from_counts(counts: dict[int, int]) -> tuple[float, ...]:
    n = sum(counts.values())
    if n <= 0:
        raise ValueError("empty shoe")
    return tuple(counts.get(v, 0) / n for v in range(1, 11))


@lru_cache(maxsize=4096)
def _dealer_from(hard: int, has_ace: bool, p: tuple[float, ...], h17: bool) -> tuple[float, ...]:
    best = hard + 10 if has_ace and hard + 10 <= 21 else hard
    soft = has_ace and hard + 10 <= 21
    if best > 21:
        return (0, 0, 0, 0, 0, 1.0)
    if best >= 17 and not (best == 17 and soft and h17):
        out = [0.0] * 6
        out[best - 17] = 1.0
        return tuple(out)
    acc = [0.0] * 6
    for v in range(1, 11):
        pv = p[v - 1]
        if pv == 0:
            continue
        sub = _dealer_from(hard + v, has_ace or v == 1, p, h17)
        for i in range(6):
            acc[i] += pv * sub[i]
    return tuple(acc)


def dealer_distribution(up: int, p: tuple[float, ...], h17: bool, condition_no_bj: bool) -> dict:
    """Final dealer totals given the upcard. Returns dict with outcomes plus p_blackjack."""
    hole = list(p)
    p_bj = 0.0
    if up == 1:
        p_bj = p[9]
    elif up == 10:
        p_bj = p[0]
    acc = [0.0] * 6
    norm = 1.0 - p_bj if condition_no_bj else 1.0
    for v in range(1, 11):
        pv = hole[v - 1]
        if pv == 0:
            continue
        if (up == 1 and v == 10) or (up == 10 and v == 1):
            continue  # natural; excluded here and reported separately
        sub = _dealer_from(up + v, up == 1 or v == 1, p, h17)
        for i in range(6):
            acc[i] += pv * sub[i] / norm
    dist = dict(zip(OUTCOMES, acc))
    dist["blackjack"] = 0.0 if condition_no_bj else p_bj
    dist["p_blackjack_prior"] = p_bj
    return dist


class EVModel:
    def __init__(self, up: int, p: tuple[float, ...], rules: BlackjackRules):
        self.up = up
        self.p = p
        self.rules = rules
        self.dist = dealer_distribution(up, p, rules.dealer_hits_soft_17, condition_no_bj=True)
        self.p_bj = self.dist["p_blackjack_prior"]
        self._hit_cache: dict[tuple[int, bool], float] = {}

    # --- values conditional on no dealer blackjack ---
    def stand(self, total: int) -> float:
        if total > 21:
            return -1.0
        d = self.dist
        win = d["bust"] + sum(d[t] for t in (17, 18, 19, 20, 21) if t < total)
        lose = sum(d[t] for t in (17, 18, 19, 20, 21) if t > total)
        return win - lose

    def _value(self, hard: int, ace: bool) -> float:
        if hard > 21:
            return -1.0
        best = hard + 10 if ace and hard + 10 <= 21 else hard
        return max(self.stand(best), self.hit(hard, ace)) if best < 21 else self.stand(best)

    def hit(self, hard: int, ace: bool) -> float:
        key = (hard, ace)
        if key in self._hit_cache:
            return self._hit_cache[key]
        ev = 0.0
        for v in range(1, 11):
            pv = self.p[v - 1]
            if pv:
                ev += pv * self._value(hard + v, ace or v == 1)
        self._hit_cache[key] = ev
        return ev

    def double(self, hard: int, ace: bool) -> float:
        ev = 0.0
        for v in range(1, 11):
            pv = self.p[v - 1]
            if pv:
                h2 = hard + v
                a2 = ace or v == 1
                best = h2 + 10 if a2 and h2 + 10 <= 21 else h2
                ev += pv * 2 * self.stand(best)
        return ev

    def split(self, card: int) -> float:
        r = self.rules
        ev_hand = 0.0
        for v in range(1, 11):
            pv = self.p[v - 1]
            if not pv:
                continue
            vals = [card, v]
            hard = card + v
            ace = card == 1 or v == 1
            total, soft = hand_total(vals)
            if card == 1 and not r.hit_split_aces:
                ev_hand += pv * self.stand(total)
                continue
            options = [self.stand(total)]
            if total < 21:
                options.append(self.hit(hard, ace))
            if r.double_after_split and _double_allowed(vals, r):
                options.append(self.double(hard, ace))
            ev_hand += pv * max(options)
        return 2 * ev_hand

    # --- public ---
    def evaluate(self, values: list[int], legal: dict[str, str]) -> dict:
        """EV per legal action for the given hand. Keys only for computed actions."""
        hard = sum(values)
        ace = 1 in values
        total, _ = hand_total(values)
        cond: dict[str, float] = {}
        if legal.get("stand") == "legal":
            cond["stand"] = self.stand(total)
        if legal.get("hit") == "legal":
            cond["hit"] = self.hit(hard, ace)
        if legal.get("double") == "legal":
            cond["double"] = self.double(hard, ace)
        if legal.get("split") == "legal":
            cond["split"] = self.split(values[0])
        if legal.get("surrender") == "legal":
            cond["surrender"] = -0.5

        if self.rules.dealer_peeks or self.p_bj == 0:
            basis = "conditional on dealer having no blackjack" if self.p_bj else "dealer cannot have blackjack with this upcard"
            return {"ev": cond, "basis": basis}
        # ENHC: unconditional, including loss of stakes to a dealer natural
        obo = self.rules.enhc_original_bets_only
        loss = {"stand": 1, "hit": 1, "double": 1 if obo else 2, "split": 1 if obo else 2, "surrender": 1}
        unc = {a: self.p_bj * -loss[a] + (1 - self.p_bj) * ev for a, ev in cond.items()}
        return {"ev": unc, "basis": "unconditional (no-hole-card game; includes dealer blackjack)"}


def _double_allowed(values: list[int], r: BlackjackRules) -> bool:
    if r.double_on == "any_two":
        return True
    total, soft = hand_total(values)
    allowed = {9, 10, 11} if r.double_on == "9_11" else {10, 11}
    return not soft and sum(values) in allowed
