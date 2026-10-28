"""Blackjack rule configuration and legal-action logic."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BlackjackRules(BaseModel):
    decks: int = Field(6, ge=1, le=8)
    dealer_hits_soft_17: bool = False
    blackjack_payout: float = Field(1.5, description="3:2 = 1.5, 6:5 = 1.2")
    double_on: Literal["any_two", "9_11", "10_11"] = "any_two"
    double_after_split: bool = True
    surrender: Literal["none", "late"] = "late"
    max_split_hands: int = Field(4, ge=1, le=4)
    resplit_aces: bool = False
    hit_split_aces: bool = False
    split_tens_by_value: bool = True  # e.g. K+Q may be split
    dealer_peeks: bool = True  # False = European no-hole-card (ENHC)
    enhc_original_bets_only: bool = False  # ENHC: lose only original bet to dealer BJ
    continuous_shuffle: bool = False

    def describe(self) -> str:
        parts = [
            f"{self.decks} deck{'s' if self.decks > 1 else ''}",
            "H17" if self.dealer_hits_soft_17 else "S17",
            f"BJ pays {self.blackjack_payout:g}:1",
            {"any_two": "double any two", "9_11": "double 9-11", "10_11": "double 10-11"}[self.double_on],
            "DAS" if self.double_after_split else "no DAS",
            "late surrender" if self.surrender == "late" else "no surrender",
            f"split to {self.max_split_hands}",
            "RSA" if self.resplit_aces else "no RSA",
            "peek" if self.dealer_peeks else ("ENHC (OBO)" if self.enhc_original_bets_only else "ENHC"),
            "CSM" if self.continuous_shuffle else "shoe",
        ]
        return ", ".join(parts)


def hand_total(values: list[int]) -> tuple[int, bool]:
    """Return (best total, is_soft) for blackjack card values (ace=1)."""
    total = sum(values)
    if 1 in values and total + 10 <= 21:
        return total + 10, True
    return total, False


def is_blackjack(values: list[int], from_split: bool = False) -> bool:
    return not from_split and len(values) == 2 and sorted(values) == [1, 10]


class HandContext(BaseModel):
    """Facts about the hand needed to decide legality."""
    values: list[int]
    ranks: list[str]  # raw ranks, to decide split eligibility of 10-value cards
    num_hands: int = 1  # hands currently in play (after splits)
    from_split: bool = False
    split_aces: bool = False


def legal_actions(ctx: HandContext, rules: BlackjackRules) -> dict[str, str]:
    """Map action -> 'legal' or a reason it is not legal. Auditable plain strings."""
    out: dict[str, str] = {}
    total, _ = hand_total(ctx.values)
    two = len(ctx.values) == 2
    if total > 21:
        return {a: "hand is bust" for a in ("hit", "stand", "double", "split", "surrender")}
    if ctx.split_aces and not rules.hit_split_aces and len(ctx.values) >= 2:
        out["hit"] = "split aces receive one card only"
    elif total == 21:
        out["hit"] = "total is 21"
    else:
        out["hit"] = "legal"
    out["stand"] = "legal"

    # double
    if not two:
        out["double"] = "only on the first two cards"
    elif ctx.split_aces and not rules.hit_split_aces:
        out["double"] = "split aces receive one card only"
    elif ctx.from_split and not rules.double_after_split:
        out["double"] = "no double after split"
    else:
        hard_total = sum(ctx.values)
        _, soft = hand_total(ctx.values)
        if rules.double_on == "any_two":
            out["double"] = "legal"
        else:
            allowed = {9, 10, 11} if rules.double_on == "9_11" else {10, 11}
            out["double"] = "legal" if (not soft and hard_total in allowed) else f"doubling restricted to hard {rules.double_on.replace('_', '-')}"

    # split
    if not two:
        out["split"] = "only on two cards"
    else:
        same = ctx.ranks[0] == ctx.ranks[1] or (
            rules.split_tens_by_value and ctx.values[0] == ctx.values[1] == 10
        )
        if not same:
            out["split"] = "cards are not a pair"
        elif ctx.num_hands >= rules.max_split_hands:
            out["split"] = f"maximum of {rules.max_split_hands} hands"
        elif ctx.split_aces and not rules.resplit_aces:
            out["split"] = "resplitting aces not allowed"
        else:
            out["split"] = "legal"

    # surrender
    if rules.surrender == "none":
        out["surrender"] = "surrender not offered"
    elif not two or ctx.from_split:
        out["surrender"] = "only on the original two-card hand"
    else:
        out["surrender"] = "legal"
    return out
