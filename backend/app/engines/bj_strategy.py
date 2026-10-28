"""Blackjack basic strategy chart for 4-8 deck games with dealer peek.

Source: the standard multi-deck basic strategy chart (S17 baseline with the
well-known H17 deviations), as published e.g. by Wizard of Odds
("4 to 8 decks" charts). The chart is a fixed, total-dependent policy: it is not
optimal for every composition, and it is only *claimed* for the configurations
listed in `chart_supported`. Tests cross-check every cell against the EV engine.

Codes: H hit, S stand, D double else hit, Ds double else stand, P split,
Ph split if DAS else hit, Rh/Rs/Rp surrender else hit/stand/split.
"""
from __future__ import annotations

from .bj_rules import BlackjackRules, HandContext, hand_total, legal_actions

UPCARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 1]  # column order; 1 = ace


def _row(s: str) -> list[str]:
    cells = s.split()
    assert len(cells) == 10, s
    return cells


HARD = {
    8: _row("H H H H H H H H H H"),
    9: _row("H D D D D H H H H H"),
    10: _row("D D D D D D D D H H"),
    11: _row("D D D D D D D D D H"),
    12: _row("H H S S S H H H H H"),
    13: _row("S S S S S H H H H H"),
    14: _row("S S S S S H H H H H"),
    15: _row("S S S S S H H H Rh H"),
    16: _row("S S S S S H H Rh Rh Rh"),
    17: _row("S S S S S S S S S S"),
}
SOFT = {
    13: _row("H H H D D H H H H H"),
    14: _row("H H H D D H H H H H"),
    15: _row("H H D D D H H H H H"),
    16: _row("H H D D D H H H H H"),
    17: _row("H D D D D H H H H H"),
    18: _row("S Ds Ds Ds Ds S S H H H"),
    19: _row("S S S S S S S S S S"),
    20: _row("S S S S S S S S S S"),
}
PAIRS = {  # keyed by blackjack value of one card
    2: _row("Ph Ph P P P P H H H H"),
    3: _row("Ph Ph P P P P H H H H"),
    4: _row("H H H Ph Ph H H H H H"),
    6: _row("Ph P P P P H H H H H"),
    7: _row("P P P P P P H H H H"),
    8: _row("P P P P P P P P P P"),
    9: _row("P P P P P S P P S S"),
    10: _row("S S S S S S S S S S"),
    1: _row("P P P P P P P P P P"),
}
# H17 deviations: (table, key, upcard) -> code
H17_CHANGES = {
    ("hard", 11, 1): "D",
    ("hard", 15, 1): "Rh",
    ("hard", 17, 1): "Rs",
    ("soft", 18, 2): "Ds",
    ("soft", 19, 6): "Ds",
    ("pair", 8, 1): "Rp",
}


def chart_supported(rules: BlackjackRules) -> tuple[bool, str]:
    if rules.decks < 4:
        return False, f"chart is for 4-8 decks; {rules.decks}-deck strategy differs in several cells"
    if not rules.dealer_peeks:
        return False, "chart assumes the dealer peeks for blackjack; no-hole-card games differ (e.g. 11 vs 10, pairs vs A/10)"
    return True, "4-8 decks, dealer peeks"


def chart_code(kind: str, key: int, up: int, rules: BlackjackRules) -> str:
    col = UPCARDS.index(up)
    table = {"hard": HARD, "soft": SOFT, "pair": PAIRS}[kind]
    if kind == "hard":
        key = min(max(key, 8), 17)
    code = table[key][col]
    if rules.dealer_hits_soft_17:
        code = H17_CHANGES.get((kind, key, up), code)
    return code


def recommend(ctx: HandContext, up: int, rules: BlackjackRules) -> dict:
    """Resolve the chart to a single legal action with an audit trail."""
    legal = legal_actions(ctx, rules)
    ok = {a for a, why in legal.items() if why == "legal"}
    total, soft = hand_total(ctx.values)
    trail: list[str] = []

    code = None
    if len(ctx.values) == 2 and "split" in ok:
        key = ctx.values[0]
        if key in PAIRS:
            code = chart_code("pair", key, up, rules)
            trail.append(f"pair of {key if key != 1 else 'A'}s vs {up if up != 1 else 'A'}: chart '{code}'")
            if code == "Ph" and not rules.double_after_split:
                trail.append("Ph without DAS -> do not split")
                code = None
            elif code in ("P", "Ph"):
                return {"action": "split", "code": code, "trail": trail, "legal": legal}
            elif code == "Rp":
                if "surrender" in ok:
                    return {"action": "surrender", "code": code, "trail": trail, "legal": legal}
                trail.append("surrender unavailable -> split")
                return {"action": "split", "code": code, "trail": trail, "legal": legal}
            else:
                code = None  # fall through to totals
    if soft and total <= 20 and total >= 13:
        code = chart_code("soft", total, up, rules)
        trail.append(f"soft {total} vs {up if up != 1 else 'A'}: chart '{code}'")
    elif soft and total == 12:  # A,A that could not be split
        code = "H"
        trail.append("soft 12 (unsplittable A,A): hit")
    elif total >= 21:
        code = "S"
        trail.append("21: stand")
    else:
        code = chart_code("hard", total, up, rules)
        trail.append(f"hard {total} vs {up if up != 1 else 'A'}: chart '{code}'")

    action = _resolve(code, ok, trail)
    return {"action": action, "code": code, "trail": trail, "legal": legal}


def _resolve(code: str, ok: set[str], trail: list[str]) -> str:
    if code == "H":
        return "hit" if "hit" in ok else "stand"
    if code == "S":
        return "stand"
    if code in ("D", "Ds"):
        if "double" in ok:
            return "double"
        fallback = "hit" if code == "D" else "stand"
        if fallback == "hit" and "hit" not in ok:
            fallback = "stand"
        trail.append(f"double not allowed -> {fallback}")
        return fallback
    if code in ("Rh", "Rs"):
        if "surrender" in ok:
            return "surrender"
        fallback = "hit" if code == "Rh" else "stand"
        trail.append(f"surrender not allowed -> {fallback}")
        return fallback
    raise ValueError(code)
