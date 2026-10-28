"""Blackjack math validated against independent references.

References:
 * Wizard of Odds, "Blackjack Appendix 2 / dealer probabilities" and
   "infinite deck" EV tables (S17, dealer peeks). Values quoted below to 4 dp.
 * An independent Monte Carlo simulator written here (different code path:
   explicit card draws, no recursion) for stand/double EVs.
"""
import random

import pytest

from app.engines.bj_ev import INFINITE_DECK, EVModel, dealer_distribution, probs_from_counts
from app.engines.bj_rules import BlackjackRules, HandContext, hand_total, legal_actions
from app.engines.bj_strategy import HARD, PAIRS, SOFT, UPCARDS, chart_supported, recommend

# Published infinite-deck S17 dealer bust probabilities (not conditioned on peek).
PUBLISHED_BUST_S17 = {2: 0.3536, 3: 0.3739, 4: 0.3945, 5: 0.4164, 6: 0.4232,
                      7: 0.2623, 8: 0.2447, 9: 0.2284, 10: 0.2121, 1: 0.1153}


@pytest.mark.parametrize("up,expected", PUBLISHED_BUST_S17.items())
def test_dealer_bust_matches_published(up, expected):
    d = dealer_distribution(up, INFINITE_DECK, h17=False, condition_no_bj=False)
    assert d["bust"] == pytest.approx(expected, abs=0.0006)


def test_dealer_distribution_sums_to_one():
    for h17 in (False, True):
        for up in range(1, 11):
            for cond in (False, True):
                d = dealer_distribution(up, INFINITE_DECK, h17, cond)
                s = sum(d[k] for k in (17, 18, 19, 20, 21, "bust")) + d["blackjack"]
                assert s == pytest.approx(1.0, abs=1e-12)


def test_h17_increases_bust_vs_6():
    s17 = dealer_distribution(6, INFINITE_DECK, False, False)["bust"]
    h17 = dealer_distribution(6, INFINITE_DECK, True, False)["bust"]
    assert h17 > s17


def test_published_player_evs():
    r = BlackjackRules()
    m10 = EVModel(10, INFINITE_DECK, r)
    # Famous close call: hard 16 vs 10, hit slightly better than stand.
    assert m10.stand(16) == pytest.approx(-0.5404, abs=0.0005)
    assert m10.hit(16, False) == pytest.approx(-0.5398, abs=0.0005)
    assert m10.stand(20) == pytest.approx(0.5545, abs=0.0005)
    m6 = EVModel(6, INFINITE_DECK, r)
    assert m6.double(11, False) == pytest.approx(0.6674, abs=0.0005)
    # doubling exactly doubles the single-card stake: double EV = 2 * one-card-then-stand EV
    one_card = sum(INFINITE_DECK[v - 1] * m6.stand(hand_total([11, v])[0] if 11 + v <= 21 else 22) for v in range(1, 11))
    assert m6.double(11, False) == pytest.approx(2 * one_card)


# ---- independent simulator -------------------------------------------------

def _draw(rng):
    v = rng.randint(1, 13)
    return min(v, 10)


def _sim_dealer(up, rng, h17):
    """Returns final total (22 = bust) conditioned on no natural."""
    while True:
        cards = [up, _draw(rng)]
        if sorted(cards) == [1, 10]:
            continue  # condition on dealer having checked and not having blackjack
        while True:
            t = sum(cards)
            soft = 1 in cards and t + 10 <= 21
            best = t + 10 if soft else t
            if best > 21:
                return 22
            if best > 17 or (best == 17 and not (soft and h17)):
                return best
            cards.append(_draw(rng))


def _settle(player, dealer):
    if player > 21:
        return -1
    if dealer > 21 or player > dealer:
        return 1
    return 0 if player == dealer else -1


def test_independent_simulation_agrees():
    rng = random.Random(7)
    n = 200_000
    r = BlackjackRules()
    # stand on 16 vs 10
    sim = sum(_settle(16, _sim_dealer(10, rng, False)) for _ in range(n)) / n
    assert sim == pytest.approx(EVModel(10, INFINITE_DECK, r).stand(16), abs=4 * (1 / n) ** 0.5)
    # double 11 vs 6
    tot = 0
    for _ in range(n):
        c = _draw(rng)
        p = 11 + c if c != 1 else 12  # ace counts 1 with 11 (21 would need 10)
        tot += 2 * _settle(p, _sim_dealer(6, rng, False))
    sim = tot / n
    assert sim == pytest.approx(EVModel(6, INFINITE_DECK, r).double(11, False), abs=4 * 2 * (1 / n) ** 0.5)


# ---- strategy chart vs computed EVs ---------------------------------------

def _best(ev):
    return max(ev, key=ev.get)


def test_chart_cells_agree_with_ev_engine():
    """Every hard/soft cell of the 4-8 deck S17 chart should match the infinite-deck
    EV-maximising action, except cells documented as composition-dependent."""
    r = BlackjackRules(decks=6, surrender="late", double_after_split=True)
    disagreements = []
    for up in UPCARDS:
        m = EVModel(up, INFINITE_DECK, r)
        for total in range(8, 18):
            # build a non-pair two-card hand with this hard total
            a = 2 if total - 2 != 2 else 3
            vals = [a, total - a]
            ctx = HandContext(values=vals, ranks=[str(v) for v in vals])
            rec = recommend(ctx, up, r)
            ev = m.evaluate(vals, rec["legal"])["ev"]
            if _best(ev) != rec["action"] and abs(ev[_best(ev)] - ev[rec["action"]]) > 1e-9:
                disagreements.append(("hard", total, up, rec["action"], _best(ev), round(ev[_best(ev)] - ev[rec["action"]], 4)))
        for total in range(13, 21):
            vals = [1, total - 11]
            ctx = HandContext(values=vals, ranks=["A", str(total - 11)])
            rec = recommend(ctx, up, r)
            ev = m.evaluate(vals, rec["legal"])["ev"]
            if _best(ev) != rec["action"] and abs(ev[_best(ev)] - ev[rec["action"]]) > 1e-9:
                disagreements.append(("soft", total, up, rec["action"], _best(ev), round(ev[_best(ev)] - ev[rec["action"]], 4)))
    # The 4-8 deck chart doubles A4 vs 4 and A2 vs 5; under the infinite-deck
    # model hitting is marginally better. Pin the exact set so changes are noticed.
    assert {d[:3] for d in disagreements} == {("soft", 15, 4), ("soft", 13, 5)}, disagreements
    assert all(abs(d[-1]) < 0.01 for d in disagreements), disagreements


def test_pair_chart_vs_ev():
    r = BlackjackRules(decks=6, surrender="none")
    bad = []
    for up in UPCARDS:
        m = EVModel(up, INFINITE_DECK, r)
        for v in (1, 2, 3, 4, 6, 7, 8, 9, 10):
            vals = [v, v]
            ranks = ["A" if v == 1 else ("K" if v == 10 else str(v))] * 2
            ctx = HandContext(values=vals, ranks=ranks)
            rec = recommend(ctx, up, r)
            ev = m.evaluate(vals, rec["legal"])["ev"]
            if _best(ev) != rec["action"] and ev[_best(ev)] - ev[rec["action"]] > 0.01:
                bad.append((v, up, rec["action"], _best(ev), ev))
    assert not bad, bad


# ---- legality -------------------------------------------------------------

def test_illegal_actions_explained():
    r = BlackjackRules(double_on="10_11", double_after_split=False, surrender="none", max_split_hands=2)
    la = legal_actions(HandContext(values=[5, 4], ranks=["5", "4"]), r)
    assert la["double"].startswith("doubling restricted")
    assert la["surrender"] == "surrender not offered"
    la = legal_actions(HandContext(values=[8, 8], ranks=["8", "8"], num_hands=2, from_split=True), r)
    assert la["split"] == "maximum of 2 hands"
    assert la["double"] == "no double after split"
    la = legal_actions(HandContext(values=[5, 4, 2], ranks=["5", "4", "2"]), r)
    assert la["double"] == "only on the first two cards"
    la = legal_actions(HandContext(values=[1, 5], ranks=["A", "5"], from_split=True, split_aces=True), BlackjackRules())
    assert la["hit"] == "split aces receive one card only"
    la = legal_actions(HandContext(values=[10, 9, 5], ranks=["K", "9", "5"]), r)
    assert all(v == "hand is bust" for v in la.values())


def test_recommendation_never_illegal():
    for rules in (BlackjackRules(), BlackjackRules(double_on="10_11", surrender="none", double_after_split=False),
                  BlackjackRules(dealer_hits_soft_17=True)):
        for up in UPCARDS:
            for a in range(1, 11):
                for b in range(1, 11):
                    ctx = HandContext(values=[a, b], ranks=[str(a), str(b)])
                    rec = recommend(ctx, up, rules)
                    assert rec["legal"][rec["action"]] == "legal", (a, b, up, rec)


def test_h17_deviations_and_surrender_fallback():
    h17 = BlackjackRules(dealer_hits_soft_17=True)
    assert recommend(HandContext(values=[6, 5], ranks=["6", "5"]), 1, h17)["action"] == "double"
    assert recommend(HandContext(values=[6, 5], ranks=["6", "5"]), 1, BlackjackRules())["action"] == "hit"
    assert recommend(HandContext(values=[10, 6], ranks=["K", "6"]), 10, BlackjackRules())["action"] == "surrender"
    no_sur = BlackjackRules(surrender="none")
    assert recommend(HandContext(values=[10, 6], ranks=["K", "6"]), 10, no_sur)["action"] == "hit"
    # 3-card 16 cannot surrender
    assert recommend(HandContext(values=[10, 4, 2], ranks=["K", "4", "2"]), 10, BlackjackRules())["action"] == "hit"


def test_unsupported_chart_configs_flagged():
    assert not chart_supported(BlackjackRules(decks=1))[0]
    assert not chart_supported(BlackjackRules(dealer_peeks=False))[0]
    assert chart_supported(BlackjackRules(decks=8))[0]


def test_enhc_double_loses_extra_stake_to_dealer_bj():
    peek = EVModel(10, INFINITE_DECK, BlackjackRules())
    enhc = EVModel(10, INFINITE_DECK, BlackjackRules(dealer_peeks=False))
    obo = EVModel(10, INFINITE_DECK, BlackjackRules(dealer_peeks=False, enhc_original_bets_only=True))
    legal = {"double": "legal", "stand": "legal"}
    e_peek = peek.evaluate([6, 5], legal)["ev"]["double"]
    e_enhc = enhc.evaluate([6, 5], legal)["ev"]["double"]
    e_obo = obo.evaluate([6, 5], legal)["ev"]["double"]
    p_bj = 1 / 13
    assert e_enhc == pytest.approx(p_bj * -2 + (1 - p_bj) * e_peek)
    assert e_obo == pytest.approx(p_bj * -1 + (1 - p_bj) * e_peek)


def test_composition_probabilities():
    counts = {v: 24 for v in range(1, 10)} | {10: 96}
    assert probs_from_counts(counts) == pytest.approx(INFINITE_DECK)
    rich = dict(counts)
    rich[10] += 40  # ten-rich shoe makes dealer 6 bust more often
    d_rich = dealer_distribution(6, probs_from_counts(rich), False, False)["bust"]
    assert d_rich > dealer_distribution(6, INFINITE_DECK, False, False)["bust"]
