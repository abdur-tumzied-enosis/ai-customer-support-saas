import re
import unicodedata
from decimal import Decimal

from .models import Product
from .price import BENGALI_DIGITS

_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)


def normalise_name(text: str) -> str:
	text = unicodedata.normalize("NFKC", text or "").casefold().translate(BENGALI_DIGITS)
	return " ".join(_PUNCT.sub(" ", text).split())


def effective_price(product, variant=None) -> Decimal | None:
	if variant is not None and variant.price is not None:
		return variant.price
	return product.price


def compute_status(product, fields) -> str:
	"""The only place a product's status is decided."""
	if product.status == "hidden":
		return "hidden"
	if product.price is None:
		return "needs_info"
	custom = product.custom or {}
	if any(f.required and custom.get(f.key) in (None, "") for f in fields):
		return "needs_info"
	# a saved product exposes its variants; an unsaved one has none
	variants = product.variants.all() if not product._state.adding else []
	if any(v.available and effective_price(product, v) is None for v in variants):
		return "needs_info"
	return "active"


def sellable(product) -> bool:
	return product.status == "active" and product.available


def product_key(product) -> str:
	return product.external_id or product.sku or ""


def _attr_key(attributes: dict) -> tuple:
	return tuple(sorted((normalise_name(str(k)), normalise_name(str(v))) for k, v in (attributes or {}).items()))


def match_product(kb, external_id, sku, name, attributes: dict) -> tuple[Product | None, str]:
	products = Product.objects.filter(kb=kb)
	if external_id:
		found = products.filter(external_id=external_id).first()
		if found:
			return found, "external_id"
	if sku:
		found = products.filter(sku=sku).first()
		if found:
			return found, "sku"
	wanted_name = normalise_name(name)
	if not wanted_name:
		return None, ""
	wanted_attrs = _attr_key(attributes)
	for candidate in products.prefetch_related("variants"):
		if normalise_name(candidate.name) != wanted_name:
			continue
		if not wanted_attrs or any(_attr_key(v.attributes) == wanted_attrs for v in candidate.variants.all()):
			return candidate, "name"
	return None, ""


def apply_owner_edit(product, changes: dict, fields) -> None:
	locked = list(product.locked_fields or [])
	for key, value in changes.items():
		setattr(product, key, value)
		if key not in locked:
			locked.append(key)
	product.locked_fields = locked
	if "status" not in changes:
		product.status = compute_status(product, fields)
