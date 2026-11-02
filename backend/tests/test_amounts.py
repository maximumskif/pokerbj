"""Amount parsing (dots/commas/currency/k-M) and recognition of rendered amounts."""
import cv2
import numpy as np
import pytest

from app.amounts import amount_value, parse_amount
from app.schemas import Rect, RegionDef
from app.vision import synth
from app.vision.recognize import Recognizer


@pytest.mark.parametrize("text,expected", [
    ("125", "125"), ("007", "7"), ("1,250", "1250"), ("1.250", "1250"), ("12.50", "12.5"), ("12,50", "12.5"),
    ("1,250.50", "1250.5"), ("1.250,50", "1250.5"), ("1,000,000", "1000000"), ("$1,250.50", "1250.5"),
    ("€12,50", "12.5"), ("12,50 €", "12.5"), ("1.5k", "1500"), ("1.250k", "1250"), ("2M", "2000000"),
    ("100.00", "100"), ("0.5", "0.5"), ("1_250_50", "1250.5"),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["", "$", "k", "12$34", "1.250.50", "1,250.5,0", "0,125", "12.5000", "1,25,000",
                                  "12.", ".5", "1..2", "abc", "1,2k3"])
def test_parse_amount_rejects_ambiguous_or_malformed(text):
    assert parse_amount(text) is None


def test_amount_value():
    assert amount_value("1250") == 1250 and isinstance(amount_value("1250"), int)
    assert amount_value("12.5") == 12.5
    assert amount_value("empty") is None and amount_value(None) is None


REGION = RegionDef(id="pot", kind="number", rect=Rect(x=0, y=0, w=220, h=40))


def _render(text: str) -> np.ndarray:
    img = np.zeros((40, 220, 3), np.uint8)
    cv2.putText(img, text, (6, 32), synth.FONT, 0.8, (235, 235, 235), 2, cv2.LINE_AA)
    return img


@pytest.fixture(scope="module")
def rec():
    return Recognizer(synth.holdem_layout())


@pytest.mark.parametrize("text,expected", [
    ("1250", "1250"), ("1,250", "1250"), ("12.50", "12.5"), ("$12.50", "12.5"), ("$1,250.50", "1250.5"),
    ("1.5k", "1500"), ("2M", "2000000"), ("0.25", "0.25"), ("1,000,000", "1000000"),
])
def test_rendered_amounts_recognized(rec, text, expected):
    value, conf, detail = rec.number(_render(text), REGION)
    assert value == expected, detail
    assert conf > 0.5, detail


def test_currency_sign_not_read_as_digit(rec):
    # before symbol templates, "$" matched its nearest digit and silently inflated the amount
    value, _, detail = rec.number(_render("$40"), REGION)
    assert value == "40", detail


def test_blank_region_is_empty(rec):
    assert rec.number(np.zeros((40, 220, 3), np.uint8), REGION)[0] == "empty"


def test_label_with_symbols_and_separators(rec, tmp_path):
    r = Recognizer(synth.holdem_layout(), template_dir=tmp_path)
    saved = r.save_number_template(_render("$1,250.50"), REGION, "$1,250.50")
    assert saved[0].startswith("sym_dollar-") and len(saved) == 7  # $ + 6 digits; separators not templated
    reloaded = Recognizer(synth.holdem_layout(), template_dir=tmp_path)
    assert len(reloaded.sym_t["$"]) == 2  # built-in + saved
    with pytest.raises(ValueError):
        r.save_number_template(_render("1250"), REGION, "12$50")
