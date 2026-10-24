"""GST maths for intra-state sales (CGST + SGST).

Prices are quoted inclusive of GST, so tax is worked backwards from the amount the
customer pays. CGST and SGST are always equal, and taxable + CGST + SGST always equals
the amount charged exactly; the taxable value absorbs any paisa of rounding.
"""

from dataclasses import dataclass

from jbhonda.core.money import div_round


@dataclass(frozen=True)
class LineTax:
    gross: int      # amount charged, incl. GST (paise)
    taxable: int
    cgst: int
    sgst: int


def split_inclusive(gross: int, gst_rate_bp: int) -> LineTax:
    """Split a GST-inclusive amount. gst_rate_bp: 1800 = 18%."""
    if gross < 0:
        raise ValueError("amount cannot be negative")
    if not 0 <= gst_rate_bp <= 10000:
        raise ValueError("GST rate must be between 0% and 100%")
    # Each half of the tax = gross * (rate / 2) / (1 + rate)
    half = div_round(gross * gst_rate_bp, 2 * (10000 + gst_rate_bp))
    return LineTax(gross=gross, taxable=gross - 2 * half, cgst=half, sgst=half)


def round_to_rupee(total: int) -> tuple[int, int]:
    """Return (grand_total, round_off) with grand_total a whole number of rupees."""
    grand = div_round(total, 100) * 100
    return grand, grand - total
