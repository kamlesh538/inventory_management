"""Checks for Indian identifiers, so typos are caught at entry rather than on a printed invoice."""

import re

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_SHAPE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _GSTIN_CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str) -> bool:
    gstin = gstin.strip().upper()
    return bool(_GSTIN_SHAPE.match(gstin)) and gstin_check_char(gstin[:14]) == gstin[14]


def is_valid_mobile(phone: str) -> bool:
    """10-digit Indian mobile number, optionally prefixed with +91 / 91 / 0."""
    digits = re.sub(r"[\s-]", "", phone)
    digits = re.sub(r"^(\+91|91|0)(?=\d{10}$)", "", digits)
    return bool(re.fullmatch(r"[6-9]\d{9}", digits))


def is_valid_pincode(pincode: str) -> bool:
    return bool(re.fullmatch(r"[1-9]\d{5}", pincode.strip()))
