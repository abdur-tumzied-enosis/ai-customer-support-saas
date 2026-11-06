from dataclasses import dataclass
from decimal import Decimal
from time import monotonic

from knowledge.catalog import effective_price

LIVE_BUDGET_SECONDS = 8  # all live calls in one quote; products past this use the catalog


@dataclass
class Quote:
	price: Decimal | None
	available: bool


def quotes_for(products, fresh: bool = False) -> dict[str, Quote]:
	"""Price and availability per product id: the live lookup when the project has it on, else the catalog.
	fresh=True skips the live cache (submit always uses it)."""
	products = list(products)
	catalog = {str(p.id): Quote(price=effective_price(p), available=p.available) for p in products}
	from .live import live_config, lookup_quote  # live imports Quote from this module

	config = live_config(products)
	if config is None:
		return catalog
	deadline = monotonic() + LIVE_BUDGET_SECONDS
	quotes = {}
	for product in products:
		key = str(product.id)
		left = deadline - monotonic()
		quotes[key] = lookup_quote(product, config, fresh, catalog[key], min(10, left)) if left > 0 else catalog[key]
	return quotes
