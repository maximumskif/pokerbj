"""Poker evaluator and equity validated against independent references.

* 5-card category counts: standard combinatorics (out of C(52,5) = 2,598,960).
* Exact equities below are hand-counted outs, written out in each test.
* AA vs KK preflop: widely published ~82% equity for the aces.
"""
import itertools

import pytest

from app.cards import Card, full_deck, parse_cards
from app.engines.poker_equity import equity, parse_range, pot_odds
from app.engines.poker_eval import category, evaluate

C = parse_cards


def test_basic_ordering():
    assert evaluate(C("Ah Kh Qh Jh Th")) > evaluate(C("9s 9h 9d 9c 2h"))
    assert evaluate(C("As 2d 3c 4h 5s")) < evaluate(C("2d 3c 4h 5s 6s"))  # wheel is lowest straight
    assert category(evaluate(C("As 2d 3c 4h 5s"))) == "Straight"
    assert evaluate(C("Ah Ad Kc Ks 2h")) > evaluate(C("Ah Ad Qc Qs Kh"))
    assert evaluate(C("Ah Ad Kc Ks 3h")) > evaluate(C("Ah Ad Kc Ks 2h"))  # kicker
    # 7-card: two trips -> full house uses the higher trips
    assert category(evaluate(C("9s 9h 9d 5c 5h 5d 2s"))) == "Full house"
    # flush with 6 suited cards picks the top five
    assert evaluate(C("Ah Kh 9h 7h 4h 2h 3c")) == evaluate(C("Ah Kh 9h 7h 4h"))


@pytest.mark.slow
def test_five_card_category_counts():
    expected = {"Straight flush": 40, "Four of a kind": 624, "Full house": 3744, "Flush": 5108,
                "Straight": 10200, "Three of a kind": 54912, "Two pair": 123552,
                "One pair": 1098240, "High card": 1302540}
    counts: dict[str, int] = {}
    distinct = set()
    for hand in itertools.combinations(full_deck(), 5):
        s = evaluate(list(hand))
        distinct.add(s)
        cat = category(s)
        counts[cat] = counts.get(cat, 0) + 1
    assert counts == expected
    assert len(distinct) == 7462  # known number of distinct 5-card hand ranks


def test_exact_turn_flush_draw_vs_set():
    # Hero AhKh on 2h 7h 9c Qs vs QcQd (set of queens). 44 river cards.
    # Hero wins only with a heart that doesn't pair the board / give quads:
    # remaining hearts 3,4,5,6,8,9,T,J,Q (9); 9h (boat) and Qh (quads) lose -> 7/44.
    r = equity(C("Ah Kh"), C("2h 7h 9c Qs"), [parse_range("QcQd")])
    assert r.method == "exact"
    assert r.samples == 44
    assert r.equity == pytest.approx(7 / 44)
    assert r.tie == 0


def test_board_plays_three_way_tie():
    r = equity(C("2c 3d"), C("As Ks Qs Js Ts"), [parse_range("4c5d"), parse_range("6h7h")])
    assert r.equity == pytest.approx(1 / 3)
    assert r.tie == 1.0
    assert r.villain_equity == pytest.approx([1 / 3, 1 / 3])


def test_split_pot_two_of_three():
    # River: hero and villain1 both have the nut straight; villain2 has two pair.
    r = equity(C("Ah Td"), C("Kc Qd Js 4h 4c"), [parse_range("AcTs"), parse_range("KdQh")])
    assert r.equity == pytest.approx(0.5)
    assert r.villain_equity == pytest.approx([0.5, 0.0])


def test_exact_three_way_turn():
    # Board 2c 7d 9h Kc; hero AsAd, v1 KhKd (set), v2 QsJs. 42 rivers.
    # Hero wins with Ac/Ah (2); v2 wins with any ten (4, straight 9-K); v1 the other 36.
    r = equity(C("As Ad"), C("2c 7d 9h Kc"), [parse_range("KhKd"), parse_range("QsJs")])
    assert r.samples == 42
    assert r.equity == pytest.approx(2 / 42)
    assert r.villain_equity == pytest.approx([36 / 42, 4 / 42])


def test_aa_vs_kk_preflop_monte_carlo():
    r = equity(C("Ah As"), [], [parse_range("KdKc")], samples=30000, time_budget_s=60, seed=1)
    assert r.method == "monte_carlo"
    assert 0.80 < r.equity < 0.84
    assert r.ci95[0] < r.equity < r.ci95[1]
    assert r.std_error < 0.003


def test_card_removal_in_ranges():
    hero = C("Ah Ad")
    r = parse_range("AA")
    assert len(r) == 6
    res = equity(hero, C("2c 7d 9h Kc 3s"), [r])
    # only one AA combo (AcAs) remains -> chop
    assert res.samples == 1 and res.equity == pytest.approx(0.5)


def test_range_parser():
    assert len(parse_range("TT+")) == 30
    assert len(parse_range("AKs")) == 4
    assert len(parse_range("AKo")) == 12
    assert len(parse_range("AK")) == 16
    assert len(parse_range("A2s+")) == 48
    assert len(parse_range("77-99")) == 18
    assert len(parse_range("A5s-A2s")) == 16
    assert len(parse_range("random")) == 1326
    assert len(parse_range("QQ+, AK, QQ")) == 18 + 16
    with pytest.raises(ValueError):
        parse_range("ZZ")


def test_duplicate_cards_rejected():
    with pytest.raises(ValueError):
        equity(C("Ah Kh"), C("Ah 2c 3d"), [parse_range("random")])


def test_pot_odds():
    po = pot_odds(100, 50)
    assert po["break_even_equity"] == pytest.approx(50 / 150)
    assert pot_odds(100, 0)["break_even_equity"] == 0
