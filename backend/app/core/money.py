"""Money helpers. Money is always Decimal internally and a 2-dp string on the wire."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def to_money(value: Decimal | str | int) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid money amount: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"Invalid money amount: {value!r}")
    return amount


def to_paypal_value(value: Decimal | str | int) -> str:
    """Format an amount the way PayPal expects ("150.00")."""
    amount = to_money(value)
    if amount <= 0:
        raise ValueError(f"Amount must be positive, got {amount}")
    return f"{amount:.2f}"
