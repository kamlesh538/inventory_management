"""Money is handled as integer paise everywhere; these helpers convert at the edges."""

from decimal import Decimal, InvalidOperation

_ONES = (
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
    "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
    "Eighteen", "Nineteen",
)
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def div_round(numerator: int, denominator: int) -> int:
    """Integer division rounding half up (for non-negative values), e.g. 0.5 paise -> 1."""
    if numerator < 0 or denominator <= 0:
        raise ValueError("div_round expects a non-negative numerator and positive denominator")
    return (2 * numerator + denominator) // (2 * denominator)


def to_paise(value: str | int | Decimal) -> int:
    """Parse rupees as typed by a user ("85,000", "₹350.50", 120) into paise."""
    text = str(value).replace(",", "").replace("₹", "").strip()
    try:
        rupees = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"'{value}' is not a valid amount") from None
    if not rupees.is_finite():
        raise ValueError(f"'{value}' is not a valid amount")
    paise = rupees * 100
    if paise != paise.to_integral_value():
        raise ValueError(f"'{value}' has more than 2 decimal places")
    return int(paise)


def format_inr(paise: int, symbol: bool = True) -> str:
    """Format with Indian digit grouping: 12345678 -> '₹1,23,456.78'."""
    sign = "-" if paise < 0 else ""
    rupees, p = divmod(abs(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"{sign}{'₹' if symbol else ''}{digits}.{p:02d}"


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return f"{_TENS[n // 10]} {_ONES[n % 10]}".strip()


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def _number_in_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    crores, n = divmod(n, 10_000_000)
    lakhs, n = divmod(n, 100_000)
    thousands, n = divmod(n, 1_000)
    if crores:
        parts.append(f"{_number_in_words(crores)} Crore")
    if lakhs:
        parts.append(f"{_below_hundred(lakhs)} Lakh")
    if thousands:
        parts.append(f"{_below_hundred(thousands)} Thousand")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(paise: int) -> str:
    """For invoices: 8500050 -> 'Rupees Eighty Five Thousand and Fifty Paise Only'."""
    if paise < 0:
        raise ValueError("amount_in_words expects a non-negative amount")
    rupees, p = divmod(paise, 100)
    text = f"Rupees {_number_in_words(rupees)}"
    if p:
        text += f" and {_below_hundred(p)} Paise"
    return text + " Only"
