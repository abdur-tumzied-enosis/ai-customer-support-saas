from dataclasses import dataclass
from decimal import Decimal

from knowledge.catalog import effective_price


@dataclass
class Quote:
	price: Decimal | None
	available: bool


# shortcut: catalog prices only, no live lookup, until Plan C replaces this body with the live lookup
def quotes_for(products, fresh: bool = False) -> dict[str, Quote]:
	return {str(p.id): Quote(price=effective_price(p), available=p.available) for p in products}
