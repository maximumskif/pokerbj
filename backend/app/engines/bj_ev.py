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

Split EV includes resplits: each split hand's second card is the pair card with
probability p[card]; then the player either resplits (while under the hand limit)
or plays the pair. Hands are independent under the model, so a small DP over
(one-card hands still to be dealt, hands in play) gives the exact optimal EV.
Ten-value cards resplit by value when split_tens_by_value, otherwise only the
same rank (taken as 1/4 of the ten-value probability).
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

    def _split_hand_values(self, card: int) -> dict[int, tuple[float, int]]:
        """Second card v -> (EV, stake) of a one-card split hand played on without splitting again."""
        r = self.rules
        out = {}
        for v in range(1, 11):
            if not self.p[v - 1]:
                continue
            vals = [card, v]
            hard, ace = card + v, card == 1 or v == 1
            total, _ = hand_total(vals)
            if card == 1 and not r.hit_split_aces:
                out[v] = (self.stand(total), 1)
                continue
            options = [(self.stand(total), 1)]
            if total < 21:
                options.append((self.hit(hard, ace), 1))
            if r.double_after_split and _double_allowed(vals, r):
                options.append((self.double(hard, ace), 2))
            out[v] = max(options, key=lambda o: o[0])
        return out

    def split_detail(self, card: int, num_hands: int = 1) -> dict:
        """Optimal split EV with resplits. num_hands = hands in play before this split.

        Returns ev, expected total stake (initial-wager units, incl. doubles) and expected hands."""
        r = self.rules
        hv = self._split_hand_values(card)
        p_pair = self.p[card - 1] / (4 if card == 10 and not r.split_tens_by_value else 1)
        can_resplit = card != 1 or r.resplit_aces
        # outcomes that never resplit: all second cards, minus the resplittable pair-card share
        base = [0.0, 0.0]
        for v, (ev, st) in hv.items():
            w = self.p[v - 1] - (p_pair if v == card else 0.0)
            base[0] += w * ev
            base[1] += w * st
        pair_ev, pair_st = hv.get(card, (0.0, 0))
        memo: dict[tuple[int, int], tuple[float, float, float]] = {}

        def value(k: int, h: int) -> tuple[float, float, float]:
            """(ev, stake, hands) of k one-card hands still to be dealt, h hands in play."""
            if k == 0:
                return (0.0, 0.0, 0.0)
            if (k, h) in memo:
                return memo[(k, h)]
            rest = value(k - 1, h)
            chosen = (pair_ev + rest[0], pair_st + rest[1], 1 + rest[2])
            if can_resplit and h < r.max_split_hands:
                rs = value(k + 1, h + 1)
                if rs[0] > chosen[0]:
                    chosen = rs
            q = 1.0 - p_pair
            res = (base[0] + q * rest[0] + p_pair * chosen[0],
                   base[1] + q * rest[1] + p_pair * chosen[1],
                   q * (1 + rest[2]) + p_pair * chosen[2])
            memo[(k, h)] = res
            return res

        ev, stake, hands = value(2, num_hands + 1)
        return {"ev": ev, "stake": stake, "hands": hands}

    def split(self, card: int, num_hands: int = 1) -> float:
        return self.split_detail(card, num_hands)["ev"]

    # --- public ---
    def evaluate(self, values: list[int], legal: dict[str, str], num_hands: int = 1) -> dict:
        """EV per legal action for the given hand. Keys only for computed actions.
        num_hands = hands currently in play (affects how many resplits remain)."""
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
        split = None
        if legal.get("split") == "legal":
            split = self.split_detail(values[0], num_hands)
            cond["split"] = split["ev"]
        if legal.get("surrender") == "legal":
            cond["surrender"] = -0.5

        if self.rules.dealer_peeks or self.p_bj == 0:
            basis = "conditional on dealer having no blackjack" if self.p_bj else "dealer cannot have blackjack with this upcard"
            return {"ev": cond, "basis": basis, "split": split}
        # ENHC: unconditional, including loss of stakes to a dealer natural
        obo = self.rules.enhc_original_bets_only
        # split: every stake on the table (resplit hands and doubles) is lost to the natural
        loss = {"stand": 1, "hit": 1, "double": 1 if obo else 2, "split": 1 if obo else (split or {}).get("stake", 2),
                "surrender": 1}
        unc = {a: self.p_bj * -loss[a] + (1 - self.p_bj) * ev for a, ev in cond.items()}
        return {"ev": unc, "basis": "unconditional (no-hole-card game; includes dealer blackjack)", "split": split}


def _double_allowed(values: list[int], r: BlackjackRules) -> bool:
    if r.double_on == "any_two":
        return True
    total, soft = hand_total(values)
    allowed = {9, 10, 11} if r.double_on == "9_11" else {10, 11}
    return not soft and sum(values) in allowed
