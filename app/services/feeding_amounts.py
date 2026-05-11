from decimal import Decimal, ROUND_HALF_UP

FEED_AMOUNT_STEP = Decimal("0.1")


def round_feed_amount_kg(value: Decimal) -> Decimal:
    return value.quantize(FEED_AMOUNT_STEP, rounding=ROUND_HALF_UP)
