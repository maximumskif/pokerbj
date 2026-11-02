"""Bet/stack/pot amounts: one parser for recognized text and typed corrections.

Canonical form is a plain decimal string without separators or symbols ("1250", "12.5").

Separators: a separator followed by exactly 3 digits is a thousands separator, one followed
by 1-2 digits at the end is the decimal point ("1,250" = 1250, "12.50" = 12.5, "1.250,5" = 1250.5).
This works for both "1,250.50" and "1.250,50" conventions without knowing the locale, and
lets recognition treat dots and commas alike (it cannot reliably tell them apart at small sizes);
recognized separators arrive as "_" (shape unknown).
Currency symbols are allowed at the start or end, a k/M multiplier at the end ("1.5k" = 1500).
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

CURRENCY = "$€£¥"
MULTIPLIERS = {"k": 1000, "K": 1000, "M": 1_000_000}
SEPARATORS = ".,_"


def parse_amount(text: str | None) -> str | None:
    """Canonical amount string, or None if the text isn't an unambiguous amount."""
    if text is None:
        return None
    s = re.sub(r"\s", "", text)
    s = s.strip(CURRENCY)
    mult = 1
    if s and s[-1] in MULTIPLIERS:
        mult = MULTIPLIERS[s[-1]]
        s = s[:-1].strip(CURRENCY)
    if not s or not re.fullmatch(r"\d[\d.,_]*", s) or s[-1] in SEPARATORS or re.search(r"[.,_]{2}", s):
        return None
    groups = re.split(r"[.,_]", s)
    seps = re.findall(r"[.,_]", s)
    decimal_sep = None
    if seps and (len(groups[-1]) in (1, 2) or (mult > 1 and len(groups[-1]) == 3 and len(seps) == 1)):
        decimal_sep = seps.pop()  # with a multiplier, "1.250k" means 1.25k
        frac = groups.pop()
    else:
        frac = ""
    if seps:  # remaining separators group thousands
        if len({c for c in seps if c != "_"}) > 1:
            return None
        if decimal_sep and decimal_sep != "_" and decimal_sep in seps:
            return None
        if not 1 <= len(groups[0]) <= 3 or groups[0] == "0" or any(len(g) != 3 for g in groups[1:]):
            return None
    try:
        d = Decimal("".join(groups) + ("." + frac if frac else "")) * mult
    except InvalidOperation:
        return None
    out = format(d.normalize(), "f")
    return out


def amount_value(canonical: str | None) -> int | float | None:
    """Number from a canonical amount string (int when whole)."""
    if canonical in (None, "", "empty"):
        return None
    try:
        d = Decimal(canonical)
    except InvalidOperation:
        return None
    return int(d) if d == d.to_integral_value() else float(d)
