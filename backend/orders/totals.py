from collections import namedtuple
from decimal import ROUND_HALF_UP, Decimal

MAX_LINE_QTY = 100
Totals = namedtuple("Totals", ["subtotal", "total"])
_CENT = Decimal("0.01")


def _money(value) -> Decimal:
	return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def compute_totals(lines: list[tuple[Decimal, int]], zone_charge: Decimal) -> Totals:
	"""Server-side totals: lines are (unit_price, quantity). Total is subtotal plus the zone charge."""
	subtotal = _money(sum((_money(price) * qty for price, qty in lines), Decimal("0")))
	return Totals(subtotal=subtotal, total=_money(subtotal + _money(zone_charge)))


def collect_amount(total: Decimal, advance_paid: Decimal) -> Decimal:
	return _money(Decimal(total) - Decimal(advance_paid))
