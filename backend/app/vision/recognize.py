"""Local recognition: OpenCV glyph template matching (no network, no OCR binary).

Templates come from two sources:
  * built-in templates rendered with the synthetic table's font/suit shapes;
  * table-specific templates saved from the calibration UI ("label this crop"),
    stored under layouts/<layout>/templates/.

Confidence is a template-match score in [0, 1] combined with a margin over the
runner-up; it measures recognition reliability only.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..cards import RANKS, RED_SUITS, SUITS, Card
from ..schemas import Layout, Observation, RegionDef
from . import synth

GLYPH = (24, 32)  # w, h for rank/digit glyphs
SUIT_GLYPH = (28, 28)


def _crop(img: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    H, W = img.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return np.zeros((1, 1, 3), np.uint8)
    return img[y0:y1, x0:x1]


def _ink_mask_card(patch: np.ndarray) -> np.ndarray:
    """Ink on a light card: dark pixels or strongly red pixels."""
    b, g, r = [patch[..., i].astype(int) for i in range(3)]
    gray = (b + g + r) // 3
    dark = gray < 120
    red = (r > 120) & (r - g > 70) & (r - b > 50)
    return ((dark | red) * 255).astype(np.uint8)


def _normalize_glyph(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray | None:
    ys, xs = np.nonzero(mask > 0)
    if len(xs) < 8:
        return None
    g = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return cv2.resize(g, size, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0


def _similarity(a: np.ndarray, b: np.ndarray) -> float:
    a0, b0 = a - a.mean(), b - b.mean()
    den = np.sqrt((a0 * a0).sum() * (b0 * b0).sum())
    if den == 0:
        return 0.0
    return float((a0 * b0).sum() / den)


def _red_ratio(patch: np.ndarray, mask: np.ndarray) -> float:
    b, g, r = [patch[..., i].astype(int) for i in range(3)]
    red = (r > 120) & (r - g > 70) & (r - b > 50)
    ink = mask > 0
    return float(red[ink].mean()) if ink.any() else 0.0


class Recognizer:
    def __init__(self, layout: Layout, template_dir: Path | None = None):
        self.layout = layout
        self.template_dir = template_dir
        self.rank_t: dict[str, list[np.ndarray]] = {}
        self.suit_t: dict[str, list[np.ndarray]] = {}
        self.digit_t: dict[str, list[np.ndarray]] = {}
        self._builtin_templates()
        self._load_custom()

    # ---------------------------------------------------------------- templates
    def _builtin_templates(self):
        for rank in RANKS:
            for suit in ("s", "h"):
                face = synth.draw_card_face(Card(rank, suit))
                g = self._rank_glyph(face)
                if g is not None:
                    self.rank_t.setdefault(rank, []).append(g)
        for suit in SUITS:
            face = synth.draw_card_face(Card("2", suit))
            g = self._suit_glyph(face)
            if g is not None:
                self.suit_t.setdefault(suit, []).append(g)
        for d in "0123456789":
            img = np.zeros((40, 40, 3), np.uint8)
            cv2.putText(img, d, (6, 32), synth.FONT, 0.8, (235, 235, 235), 2, cv2.LINE_AA)
            g = _normalize_glyph((cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) > 120).astype(np.uint8) * 255, GLYPH)
            self.digit_t.setdefault(d, []).append(g)

    def _load_custom(self):
        if not self.template_dir or not self.template_dir.exists():
            return
        for p in self.template_dir.glob("*.png"):
            kind, _, label = p.stem.partition("_")
            label = label.split("-")[0]
            g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if g is None:
                continue
            g = g.astype(np.float32) / 255.0
            {"rank": self.rank_t, "suit": self.suit_t, "digit": self.digit_t}.get(kind, {}).setdefault(label, []).append(g)

    def save_card_template(self, card_crop: np.ndarray, card: Card) -> list[str]:
        """Store rank and suit glyphs from a user-labelled card crop."""
        assert self.template_dir is not None
        self.template_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for kind, glyph, label, store in (("rank", self._rank_glyph(card_crop), card.rank, self.rank_t),
                                          ("suit", self._suit_glyph(card_crop), card.suit, self.suit_t)):
            if glyph is None:
                continue
            n = len(list(self.template_dir.glob(f"{kind}_{label}-*.png")))
            path = self.template_dir / f"{kind}_{label}-{n}.png"
            cv2.imwrite(str(path), (glyph * 255).astype(np.uint8))
            store.setdefault(label, []).append(glyph)
            saved.append(path.name)
        return saved

    def save_number_template(self, crop: np.ndarray, region: RegionDef, text: str) -> list[str]:
        assert self.template_dir is not None
        self.template_dir.mkdir(parents=True, exist_ok=True)
        glyphs = self._digit_glyphs(crop, region)
        digits = [c for c in text if c.isdigit()]
        if len(glyphs) != len(digits):
            raise ValueError(f"found {len(glyphs)} glyphs but label has {len(digits)} digits")
        saved = []
        for g, d in zip(glyphs, digits):
            n = len(list(self.template_dir.glob(f"digit_{d}-*.png")))
            path = self.template_dir / f"digit_{d}-{n}.png"
            cv2.imwrite(str(path), (g * 255).astype(np.uint8))
            self.digit_t.setdefault(d, []).append(g)
            saved.append(path.name)
        return saved

    # ---------------------------------------------------------------- glyphs
    def _sub(self, card: np.ndarray, box) -> np.ndarray:
        h, w = card.shape[:2]
        fx, fy, fw, fh = box
        return card[int(fy * h):int((fy + fh) * h), int(fx * w):int((fx + fw) * w)]

    def _rank_glyph(self, card: np.ndarray):
        sub = self._sub(card, self.layout.rank_box)
        return _normalize_glyph(_ink_mask_card(sub), GLYPH)

    def _suit_glyph(self, card: np.ndarray):
        sub = self._sub(card, self.layout.suit_box)
        return _normalize_glyph(_ink_mask_card(sub), SUIT_GLYPH)

    def _digit_glyphs(self, crop: np.ndarray, region: RegionDef) -> list[np.ndarray]:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        mask = (gray > 150) if region.polarity == "light" else (gray < 100)
        mask = mask.astype(np.uint8) * 255
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        comps = [stats[i] for i in range(1, n) if stats[i][cv2.CC_STAT_AREA] >= 12]
        if not comps:
            return []
        max_h = max(c[cv2.CC_STAT_HEIGHT] for c in comps)
        comps = [c for c in comps if c[cv2.CC_STAT_HEIGHT] >= 0.6 * max_h]  # drop commas/dots/noise
        comps.sort(key=lambda c: c[cv2.CC_STAT_LEFT])
        out = []
        for c in comps:
            x, y, w, h = c[0], c[1], c[2], c[3]
            g = _normalize_glyph(mask[y:y + h, x:x + w], GLYPH)
            if g is not None:
                out.append(g)
        return out

    @staticmethod
    def _best(glyph, templates: dict[str, list[np.ndarray]], allowed=None) -> tuple[str | None, float, float]:
        scores = []
        for label, ts in templates.items():
            if allowed is not None and label not in allowed:
                continue
            scores.append((max(_similarity(glyph, t) for t in ts), label))
        if not scores:
            return None, 0.0, 0.0
        scores.sort(reverse=True)
        best, label = scores[0]
        runner = scores[1][0] if len(scores) > 1 else 0.0
        return label, max(best, 0.0), best - runner

    # ---------------------------------------------------------------- regions
    def card(self, crop: np.ndarray) -> tuple[str | None, float, str]:
        b, g, r = [crop[..., i].astype(int) for i in range(3)]
        white = ((b > 190) & (g > 190) & (r > 190)).mean()
        blue = ((b > 110) & (b - r > 60)).mean()
        if white > 0.45:
            rank, rs, rmargin = self._best(self._rank_glyph(crop), self.rank_t) if self._rank_glyph(crop) is not None else (None, 0, 0)
            sg = self._suit_glyph(crop)
            if rank is None or sg is None:
                return None, 0.2, "card face but glyphs unreadable"
            red = _red_ratio(self._sub(crop, self.layout.suit_box), _ink_mask_card(self._sub(crop, self.layout.suit_box)))
            allowed = RED_SUITS if red > 0.5 else set(SUITS) - RED_SUITS
            suit, ss, smargin = self._best(sg, self.suit_t, allowed)
            conf = min(rs, ss) * min(1.0, 0.5 + 4 * min(rmargin, smargin))
            conf *= min(1.0, white / 0.6)
            return f"{rank}{suit}", float(np.clip(conf, 0, 1)), f"rank {rs:.2f}/+{rmargin:.2f} suit {ss:.2f}/+{smargin:.2f}"
        if blue > 0.4:
            return "hidden", float(min(1.0, blue / 0.6)), "card back"
        if white < 0.05 and blue < 0.05:
            return "empty", float(1.0 - (white + blue) * 10), "no card"
        return None, 0.3, f"ambiguous (white {white:.2f}, back {blue:.2f})"

    def number(self, crop: np.ndarray, region: RegionDef) -> tuple[str | None, float, str]:
        glyphs = self._digit_glyphs(crop, region)
        if not glyphs:
            return "empty", 0.9, "no digits"  # confidently blank
        digits, confs = [], []
        for g in glyphs:
            d, s, m = self._best(g, self.digit_t)
            digits.append(d)
            confs.append(s * min(1.0, 0.5 + 4 * m))
        return "".join(digits), float(np.clip(min(confs), 0, 1)), f"{len(digits)} digits"

    def indicator(self, crop: np.ndarray, region: RegionDef) -> tuple[str, float, str]:
        mean = float(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).mean())
        on = mean > region.threshold
        conf = min(1.0, abs(mean - region.threshold) / 40)
        return ("on" if on else "off"), conf, f"brightness {mean:.0f}"

    def observe(self, frame: np.ndarray, frame_id: int, captured_at: float) -> list[Observation]:
        obs = []
        for reg in self.layout.regions:
            r = reg.rect
            crop = _crop(frame, r.x, r.y, r.w, r.h)
            if reg.kind == "card":
                v, c, d = self.card(crop)
            elif reg.kind == "number":
                v, c, d = self.number(crop, reg)
            else:
                v, c, d = self.indicator(crop, reg)
            obs.append(Observation(region_id=reg.id, kind=reg.kind, value=v, confidence=round(c, 3),
                                   captured_at=captured_at, frame_id=frame_id, detail=d))
        return obs
