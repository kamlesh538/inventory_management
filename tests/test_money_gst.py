from datetime import date

import pytest

from jbhonda.core.gst import round_to_rupee, split_inclusive
from jbhonda.core.money import amount_in_words, format_inr, to_paise
from jbhonda.core.numbering import financial_year


@pytest.mark.parametrize(
    ("text", "paise"),
    [("85000", 8500000), ("85,000", 8500000), ("₹350.50", 35050), ("0.05", 5), (120, 12000), (" 1,23,456.7 ", 12345670)],
)
def test_to_paise(text, paise):
    assert to_paise(text) == paise


@pytest.mark.parametrize("text", ["abc", "", "1.234", "nan", "inf"])
def test_to_paise_rejects_bad_input(text):
    with pytest.raises(ValueError):
        to_paise(text)


@pytest.mark.parametrize(
    ("paise", "text"),
    [(0, "₹0.00"), (35050, "₹350.50"), (8500000, "₹85,000.00"), (12345678, "₹1,23,456.78"),
     (1234567890, "₹1,23,45,678.90"), (-35050, "-₹350.50")],
)
def test_format_inr_uses_indian_grouping(paise, text):
    assert format_inr(paise) == text


@pytest.mark.parametrize(
    ("paise", "words"),
    [
        (8500000, "Rupees Eighty Five Thousand Only"),
        (8500050, "Rupees Eighty Five Thousand and Fifty Paise Only"),
        (12345600, "Rupees One Lakh Twenty Three Thousand Four Hundred Fifty Six Only"),
        (1_00_00_000_00, "Rupees One Crore Only"),
        (100, "Rupees One Only"),
        (0, "Rupees Zero Only"),
    ],
)
def test_amount_in_words(paise, words):
    assert amount_in_words(paise) == words


def test_split_inclusive_18_percent():
    # ₹85,000 incl. 18% -> taxable ₹72,033.90, CGST = SGST = ₹6,483.05
    tax = split_inclusive(8500000, 1800)
    assert (tax.taxable, tax.cgst, tax.sgst) == (7203390, 648305, 648305)


def test_split_inclusive_zero_rate():
    tax = split_inclusive(12345, 0)
    assert (tax.taxable, tax.cgst, tax.sgst) == (12345, 0, 0)


@pytest.mark.parametrize("rate", [0, 500, 1200, 1800, 2800, 4000])
def test_split_always_adds_up_and_matches_rate(rate):
    for gross in [*range(0, 2000), 99999, 8500000, 12345678]:
        tax = split_inclusive(gross, rate)
        assert tax.taxable + tax.cgst + tax.sgst == gross
        assert tax.cgst == tax.sgst
        # Tax charged is within a paisa of the exact figure for the taxable value.
        assert abs(2 * tax.cgst - tax.taxable * rate / 10000) <= 1.5


@pytest.mark.parametrize(
    ("total", "grand", "round_off"),
    [(8500000, 8500000, 0), (8500049, 8500000, -49), (8500050, 8500100, 50), (8500001, 8500000, -1)],
)
def test_round_to_rupee(total, grand, round_off):
    assert round_to_rupee(total) == (grand, round_off)


@pytest.mark.parametrize(
    ("on", "fy"),
    [(date(2026, 4, 1), "2026-27"), (date(2027, 3, 31), "2026-27"), (date(2026, 3, 31), "2025-26"),
     (date(2099, 12, 1), "2099-00")],
)
def test_financial_year(on, fy):
    assert financial_year(on) == fy
