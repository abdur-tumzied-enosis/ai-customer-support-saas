import json
import logging
from decimal import Decimal, InvalidOperation

from django.core.cache import cache

from .http import OutboundError, safe_request
from .models import OrderSettings
from .templates import LIVE_PLACEHOLDERS, TemplateError, dig, render

logger = logging.getLogger(__name__)
CACHE_SECONDS = 300  # submit bypasses this cache (fresh=True)


def live_config(products) -> dict | None:
	"""The project's live lookup settings when switched on and complete, else None."""
	if not products:
		return None
	settings = OrderSettings.objects.filter(project_id=products[0].kb.project_id).first()
	config = settings.live_lookup if settings else {}
	if config.get("enabled") and config.get("url_template") and config.get("price_path"):
		return config
	return None


def _at(data, path: str):
	try:
		return dig(data, path)
	except (KeyError, IndexError, TypeError):
		return None


def _price(value) -> Decimal | None:
	try:
		price = Decimal(str(value))
	except (InvalidOperation, ValueError, TypeError):
		return None
	return price if price.is_finite() and price >= 0 else None


def _quote(data: dict):
	from .pricing import Quote  # pricing imports this module

	return Quote(price=None if data["price"] is None else Decimal(data["price"]), available=data["available"])


def lookup_quote(product, config: dict, fresh: bool, catalog):
	"""Price and availability from the client's API. Falls back to the catalog quote when the call fails."""
	if not (product.external_id or product.sku):
		return catalog
	cache_key = f"live-quote:{product.kb.project_id}:{product.id}"
	if not fresh:
		hit = cache.get(cache_key)
		if hit is not None:
			return _quote(hit)
	try:
		url = render(
			config["url_template"],
			{"product.external_id": product.external_id or "", "product.sku": product.sku},
			"url", LIVE_PLACEHOLDERS,
		)
		result = safe_request("GET", url, {h["name"]: h["value"] for h in config.get("headers") or []})
		if not result.ok:
			raise OutboundError(f"HTTP {result.status}")
		data = json.loads(result.text)
	except (TemplateError, OutboundError, ValueError) as e:
		logger.warning("Live lookup for product %s used the catalog instead: %s", product.id, type(e).__name__)
		return catalog
	price = _price(_at(data, config["price_path"]))  # no price in the answer: the product is not sellable
	path = config.get("available_path")
	available = price is not None and (bool(_at(data, path)) if path else catalog.available)
	stored = {"price": None if price is None else str(price), "available": available}
	cache.set(cache_key, stored, CACHE_SECONDS)
	return _quote(stored)
