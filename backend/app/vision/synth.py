"""Synthetic table renderer.

Used for (1) the clearly-labelled synthetic replay sessions and (2) seeding the
local recognizer with glyph templates. Real tables are supported by drawing
regions in the calibration UI and labelling a few crops, which saves
table-specific templates.
"""
from __future__ import annotations

import cv2
import numpy as np

from ..cards import RED_SUITS, Card
from ..schemas import Layout, Rect, RegionDef

W, H = 960, 600
CARD_W, CARD_H = 64, 90
FELT = (48, 96, 38)  # BGR
FONT = cv2.FONT_HERSHEY_SIMPLEX


# --------------------------------------------------------------------- layouts

def _r(rid, kind, x, y, w, h, label="", **kw) -> RegionDef:
    return RegionDef(id=rid, kind=kind, rect=Rect(x=x, y=y, w=w, h=h), label=label or rid, **kw)


def blackjack_layout() -> Layout:
    regs = [_r(f"dealer_{i}", "card", 300 + i * 72, 70, CARD_W, CARD_H, f"Dealer card {i + 1}") for i in range(6)]
    for h, x0 in ((0, 110), (1, 520)):
        regs += [_r(f"p{h}_{i}", "card", x0 + i * 70, 300, CARD_W, CARD_H, f"Hand {h + 1} card {i + 1}") for i in range(5)]
        regs.append(_r(f"bet{h}", "number", x0, 410, 130, 34, f"Hand {h + 1} bet"))
        regs.append(_r(f"active{h}", "indicator", x0, 396, 340, 8, f"Hand {h + 1} active marker"))
    regs.append(_r("bankroll", "number", 20, 548, 180, 34, "Bankroll"))
    for i, a in enumerate(("hit", "stand", "double", "split", "surrender")):
        regs.append(_r(f"btn_{a}", "indicator", 300 + i * 128, 540, 118, 44, f"{a.title()} button"))
    regs.append(_r("shuffle", "indicator", 780, 14, 160, 30, "Shuffle notice"))
    return Layout(name="synthetic_blackjack", game="blackjack", width=W, height=H, regions=regs, synthetic=True)


HOLDEM_SEATS = [(60, 200), (230, 40), (650, 40), (820, 200), (650, 330)]  # 5 opponents (x, y)


def holdem_layout() -> Layout:
    regs = [_r(f"hero_{i}", "card", 400 + i * 70, 400, CARD_W, CARD_H, f"Hero card {i + 1}") for i in range(2)]
    regs += [_r(f"board_{i}", "card", 300 + i * 72, 200, CARD_W, CARD_H, f"Board card {i + 1}") for i in range(5)]
    regs.append(_r("pot", "number", 420, 150, 130, 34, "Pot"))
    regs.append(_r("hero_stack", "number", 400, 500, 130, 34, "Hero stack"))
    regs.append(_r("hero_bet", "number", 400, 355, 110, 34, "Hero bet"))
    regs.append(_r("hero_dealer", "indicator", 545, 410, 26, 26, "Hero dealer button"))
    for i, (x, y) in enumerate(HOLDEM_SEATS, start=1):
        regs.append(_r(f"seat{i}_cards", "indicator", x, y, 70, 22, f"Seat {i} has cards"))
        regs.append(_r(f"seat{i}_stack", "number", x, y + 28, 110, 30, f"Seat {i} stack"))
        regs.append(_r(f"seat{i}_bet", "number", x, y + 64, 110, 30, f"Seat {i} bet"))
        regs.append(_r(f"seat{i}_dealer", "indicator", x + 80, y, 22, 22, f"Seat {i} dealer button"))
    for i, a in enumerate(("fold", "check", "call", "raise")):
        regs.append(_r(f"btn_{a}", "indicator", 600 + i * 88, 540, 82, 44, f"{a.title()} button"))
    regs.append(_r("to_call", "number", 600 + 2 * 88, 500, 82, 34, "Amount to call"))
    return Layout(name="synthetic_holdem", game="holdem", width=W, height=H, regions=regs, synthetic=True)


# --------------------------------------------------------------------- drawing

def _suit_shape(img, cx, cy, s, suit, color):
    if suit == "d":
        pts = np.array([[cx, cy - s], [cx + int(s * 0.7), cy], [cx, cy + s], [cx - int(s * 0.7), cy]])
        cv2.fillPoly(img, [pts], color, cv2.LINE_AA)
    elif suit == "h":
        r = int(s * 0.5)
        cv2.circle(img, (cx - r, cy - r // 2), r, color, -1, cv2.LINE_AA)
        cv2.circle(img, (cx + r, cy - r // 2), r, color, -1, cv2.LINE_AA)
        pts = np.array([[cx - 2 * r, cy - r // 3], [cx + 2 * r, cy - r // 3], [cx, cy + s]])
        cv2.fillPoly(img, [pts], color, cv2.LINE_AA)
    elif suit == "s":
        r = int(s * 0.5)
        cv2.circle(img, (cx - r, cy + r // 3), r, color, -1, cv2.LINE_AA)
        cv2.circle(img, (cx + r, cy + r // 3), r, color, -1, cv2.LINE_AA)
        pts = np.array([[cx - 2 * r, cy + r // 6], [cx + 2 * r, cy + r // 6], [cx, cy - s]])
        cv2.fillPoly(img, [pts], color, cv2.LINE_AA)
        cv2.fillPoly(img, [np.array([[cx, cy], [cx + r // 2 + 2, cy + s], [cx - r // 2 - 2, cy + s]])], color, cv2.LINE_AA)
    elif suit == "c":
        r = int(s * 0.42)
        cv2.circle(img, (cx, cy - r), r, color, -1, cv2.LINE_AA)
        cv2.circle(img, (cx - r - 1, cy + r // 2), r, color, -1, cv2.LINE_AA)
        cv2.circle(img, (cx + r + 1, cy + r // 2), r, color, -1, cv2.LINE_AA)
        cv2.fillPoly(img, [np.array([[cx, cy], [cx + r // 2 + 2, cy + s], [cx - r // 2 - 2, cy + s]])], color, cv2.LINE_AA)


def rank_text(rank: str) -> str:
    return "10" if rank == "T" else rank


def draw_card_face(card: Card) -> np.ndarray:
    img = np.full((CARD_H, CARD_W, 3), 245, np.uint8)
    cv2.rectangle(img, (0, 0), (CARD_W - 1, CARD_H - 1), (90, 90, 90), 1)
    color = (30, 30, 200) if card.suit in RED_SUITS else (25, 25, 25)
    txt = rank_text(card.rank)
    scale = 0.8 if len(txt) == 1 else 0.68
    cv2.putText(img, txt, (4, 26), FONT, scale, color, 2, cv2.LINE_AA)
    _suit_shape(img, CARD_W // 2, 62, 14, card.suit, color)
    return img


def draw_card_back() -> np.ndarray:
    img = np.full((CARD_H, CARD_W, 3), (150, 60, 30), np.uint8)
    for k in range(-CARD_H, CARD_W, 8):
        cv2.line(img, (k, 0), (k + CARD_H, CARD_H), (185, 95, 50), 2)
    cv2.rectangle(img, (0, 0), (CARD_W - 1, CARD_H - 1), (240, 240, 240), 2)
    return img


def blank_table(label: str) -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = FELT
    cv2.ellipse(img, (W // 2, 40), (520, 520), 0, 0, 180, (40, 80, 30), 3)
    cv2.putText(img, label, (12, 24), FONT, 0.55, (0, 215, 255), 1, cv2.LINE_AA)
    return img


def paste(img: np.ndarray, patch: np.ndarray, rect: Rect, dx: int = 0, dy: int = 0) -> None:
    x, y = rect.x + dx, rect.y + dy
    h, w = patch.shape[:2]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, img.shape[1]), min(y + h, img.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    img[y0:y1, x0:x1] = patch[y0 - y:y1 - y, x0 - x:x1 - x]


def draw_card(img, rect: Rect, value: str | None, dx=0, dy=0, blur=0, occlude=False):
    if value in (None, "empty"):
        return
    patch = draw_card_back() if value == "hidden" else draw_card_face(Card.parse(value))
    if blur:
        patch = cv2.GaussianBlur(patch, (0, 0), blur)
    paste(img, patch, rect, dx, dy)
    if occlude:  # e.g. a mouse cursor / chip animation over the card corner
        cv2.circle(img, (rect.x + 18, rect.y + 20), 20, (60, 60, 60), -1, cv2.LINE_AA)


def draw_number(img, rect: Rect, value: int | None, prefix_box=True):
    if prefix_box:
        cv2.rectangle(img, (rect.x, rect.y), (rect.x + rect.w, rect.y + rect.h), (20, 30, 20), -1)
    if value is None:
        return
    cv2.putText(img, str(value), (rect.x + 6, rect.y + rect.h - 8), FONT, 0.8, (235, 235, 235), 2, cv2.LINE_AA)


def draw_indicator(img, rect: Rect, on: bool, text: str = ""):
    color = (60, 200, 240) if on else (30, 45, 30)
    cv2.rectangle(img, (rect.x, rect.y), (rect.x + rect.w, rect.y + rect.h), color, -1)
    if text and rect.h >= 20:
        tc = (20, 20, 20) if on else (80, 100, 80)
        cv2.putText(img, text, (rect.x + 6, rect.y + rect.h // 2 + 6), FONT, 0.5, tc, 1, cv2.LINE_AA)


def degrade(img: np.ndarray, rng: np.random.Generator, noise=5.0, jpeg=75) -> np.ndarray:
    """Simulate capture noise and compression so recognition is not trivially exact."""
    noisy = img.astype(np.float32) + rng.normal(0, noise, img.shape)
    noisy = np.clip(noisy, 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode(".jpg", noisy, [cv2.IMWRITE_JPEG_QUALITY, jpeg])
    return cv2.imdecode(enc, cv2.IMREAD_COLOR)
