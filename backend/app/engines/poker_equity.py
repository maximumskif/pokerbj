"""Showdown equity vs user-configured opponent ranges.

Equity = expected share of the pot at showdown assuming every listed opponent
sees all remaining board cards (no folds, no further betting). Ties split the
share equally. This is NOT a betting strategy and NOT action EV.
"""
from __future__ import annotations

import itertools
import math
import random
import time
from dataclasses import dataclass

from ..cards import RANKS, SUITS, Card, full_deck, parse_cards
from .poker_eval import evaluate

Combo = tuple[Card, Card]


def _pair_combos(r: str) -> list[Combo]:
    return [(Card(r, a), Card(r, b)) for a, b in itertools.combinations(SUITS, 2)]


def _suited(r1: str, r2: str) -> list[Combo]:
    return [(Card(r1, s), Card(r2, s)) for s in SUITS]


def _offsuit(r1: str, r2: str) -> list[Combo]:
    return [(Card(r1, a), Card(r2, b)) for a in SUITS for b in SUITS if a != b]


def _token_combos(tok: str) -> list[Combo]:
    tok = tok.strip()
    if not tok:
        return []
    up = tok.upper()
    if up in ("RANDOM", "ANY", "100%", "XX"):
        return list(itertools.combinations(full_deck(), 2))
    # explicit combo, e.g. AhKh
    if len(tok) == 4 and tok[1].lower() in SUITS and tok[3].lower() in SUITS:
        c = parse_cards(tok)
        return [(c[0], c[1])]
    if "-" in tok:  # ranges like 77-99, A5s-A2s, KTo-K8o
        a, b = (t.strip() for t in tok.split("-"))
        a, b = a.upper().replace("S", "s").replace("O", "o"), b.upper().replace("S", "s").replace("O", "o")
        if len(a) == 2 and a[0] == a[1]:
            lo, hi = sorted([RANKS.index(a[0]), RANKS.index(b[0])])
            return [c for i in range(lo, hi + 1) for c in _pair_combos(RANKS[i])]
        if a[0] != b[0]:
            raise ValueError(f"range {tok!r} must share the first rank")
        suffix = a[2:] if len(a) > 2 else ""
        lo, hi = sorted([RANKS.index(a[1]), RANKS.index(b[1])])
        out: list[Combo] = []
        for i in range(lo, hi + 1):
            out += _token_combos(a[0] + RANKS[i] + suffix)
        return out
    plus = tok.endswith("+")
    core = tok[:-1] if plus else tok
    r1, r2 = core[0].upper(), core[1].upper()
    suffix = core[2:].lower()
    if r1 not in RANKS or r2 not in RANKS or suffix not in ("", "s", "o"):
        raise ValueError(f"cannot parse range token {tok!r}")
    if RANKS.index(r2) > RANKS.index(r1):
        r1, r2 = r2, r1
    if r1 == r2:
        if suffix:
            raise ValueError(f"pairs cannot be suited/offsuit: {tok!r}")
        if plus:
            return [c for i in range(RANKS.index(r1), len(RANKS)) for c in _pair_combos(RANKS[i])]
        return _pair_combos(r1)
    kickers = [r2]
    if plus:  # raise the kicker up to one below the top card
        kickers = [RANKS[i] for i in range(RANKS.index(r2), RANKS.index(r1))]
    out = []
    for k in kickers:
        if suffix in ("", "s"):
            out += _suited(r1, k)
        if suffix in ("", "o"):
            out += _offsuit(r1, k)
    return out


def parse_range(text: str) -> list[Combo]:
    seen: set[frozenset] = set()
    out: list[Combo] = []
    for tok in text.replace(";", ",").split(","):
        for c in _token_combos(tok):
            key = frozenset(c)
            if key not in seen:
                seen.add(key)
                out.append(c)
    if not out:
        raise ValueError("empty range")
    return out


@dataclass
class EquityResult:
    equity: float
    win: float
    tie: float
    samples: int
    method: str  # "exact" or "monte_carlo"
    std_error: float
    ci95: tuple[float, float]
    villain_equity: list[float]
    elapsed_ms: float
    note: str = ""

    def as_dict(self) -> dict:
        return self.__dict__ | {"ci95": list(self.ci95)}


def _showdown(hero: list[Card], villains: list[Combo], board: list[Card]) -> list[float]:
    """Pot share for [hero, v1, v2, ...]."""
    scores = [evaluate(hero + board)] + [evaluate(list(v) + board) for v in villains]
    best = max(scores)
    winners = [i for i, s in enumerate(scores) if s == best]
    share = 1.0 / len(winners)
    return [share if i in winners else 0.0 for i in range(len(scores))]


def equity(
    hero: list[Card],
    board: list[Card],
    villain_ranges: list[list[Combo]],
    samples: int = 20000,
    time_budget_s: float = 2.0,
    exact_limit: int = 150_000,
    seed: int | None = None,
) -> EquityResult:
    t0 = time.perf_counter()
    known = hero + board
    if len(set(known)) != len(known):
        raise ValueError("duplicate cards among hero and board")
    if len(hero) != 2 or len(board) > 5 or len(board) in (1, 2):
        raise ValueError("need 2 hero cards and a board of 0, 3, 4 or 5 cards")
    if not villain_ranges:
        raise ValueError("need at least one opponent")
    dead = set(known)
    ranges = [[c for c in r if c[0] not in dead and c[1] not in dead] for r in villain_ranges]
    if any(not r for r in ranges):
        raise ValueError("an opponent range has no combos left after card removal")
    need = 5 - len(board)
    rest = [c for c in full_deck() if c not in dead]

    n_players = len(ranges) + 1
    total_villain_combos = math.prod(len(r) for r in ranges)
    remaining_after = len(rest) - 2 * len(ranges)
    work = total_villain_combos * math.comb(max(remaining_after, 0), need)

    if work <= exact_limit:
        sums = [0.0] * n_players
        wins = ties = 0.0
        n = 0
        for vs in itertools.product(*ranges):
            used = [c for v in vs for c in v]
            if len(set(used)) != len(used):
                continue
            deck = [c for c in rest if c not in used]
            for runout in itertools.combinations(deck, need):
                sh = _showdown(hero, list(vs), board + list(runout))
                for i in range(n_players):
                    sums[i] += sh[i]
                if sh[0] == 1.0:
                    wins += 1
                elif sh[0] > 0:
                    ties += 1
                n += 1
        if n == 0:
            raise ValueError("no consistent card assignment for the given ranges")
        eq = sums[0] / n
        return EquityResult(eq, wins / n, ties / n, n, "exact", 0.0, (eq, eq),
                            [s / n for s in sums[1:]], (time.perf_counter() - t0) * 1000)

    rng = random.Random(seed)
    sums = [0.0] * n_players
    sq = 0.0
    wins = ties = 0.0
    n = 0
    rejects = 0
    deadline = t0 + time_budget_s
    while n < samples:
        if n % 256 == 0 and n > 1000 and time.perf_counter() > deadline:
            break
        vs = [r[rng.randrange(len(r))] for r in ranges]
        used = [c for v in vs for c in v]
        if len(set(used)) != len(used):
            rejects += 1
            if rejects > 50 * samples:
                raise ValueError("ranges conflict too often to sample")
            continue
        used_set = set(used)
        deck = [c for c in rest if c not in used_set]
        runout = rng.sample(deck, need)
        sh = _showdown(hero, vs, board + runout)
        for i in range(n_players):
            sums[i] += sh[i]
        sq += sh[0] * sh[0]
        if sh[0] == 1.0:
            wins += 1
        elif sh[0] > 0:
            ties += 1
        n += 1
    eq = sums[0] / n
    var = max(sq / n - eq * eq, 0.0)
    se = math.sqrt(var / n)
    note = "" if n >= samples else f"stopped at time budget ({n} of {samples} samples)"
    return EquityResult(eq, wins / n, ties / n, n, "monte_carlo", se,
                        (max(0.0, eq - 1.96 * se), min(1.0, eq + 1.96 * se)),
                        [s / n for s in sums[1:]], (time.perf_counter() - t0) * 1000, note)


def pot_odds(pot_before_call: float, to_call: float) -> dict:
    """Break-even showdown equity for a call (heads-up main pot, no rake, no future betting)."""
    if to_call <= 0:
        return {"to_call": 0, "break_even_equity": 0.0, "note": "nothing to call"}
    be = to_call / (pot_before_call + to_call)
    return {
        "pot_before_call": pot_before_call,
        "to_call": to_call,
        "pot_after_call": pot_before_call + to_call,
        "break_even_equity": be,
        "odds_ratio": f"{pot_before_call / to_call:.2f} : 1",
        "assumptions": [
            "pot_before_call includes all bets already made this street",
            "no rake",
            "no further betting on later streets (immediate pot odds only)",
            "single pot; side pots not modeled",
        ],
    }
