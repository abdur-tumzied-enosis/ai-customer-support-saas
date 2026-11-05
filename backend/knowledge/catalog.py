import unicodedata
from decimal import Decimal

from .models import Product
from .price import BENGALI_DIGITS


def normalise_name(text: str) -> str:
	text = unicodedata.normalize("NFKC", text or "").casefold().translate(BENGALI_DIGITS)
	text = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in text)
	return " ".join(text.split())


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


def image_dict(img) -> dict:
	url = f"/api/kb/products/images/{img.id}/file/"
	return {"id": str(img.id), "status": img.status, "url": url, "thumb_url": f"{url}?size=thumb"}


def _variant_dict(v) -> dict:
	return {"id": str(v.id), "external_id": v.external_id, "sku": v.sku, "label": v.label, "attributes": v.attributes,
		"price": None if v.price is None else str(v.price), "available": v.available}


def product_dict(p) -> dict:
	return {
		"id": str(p.id),
		"kb_id": str(p.kb_id),
		"external_id": p.external_id,
		"sku": p.sku,
		"name": p.name,
		"description": p.description,
		"price": None if p.price is None else str(p.price),
		"currency": p.kb.project.currency,
		"available": p.available,
		"status": p.status,
		"product_url": p.product_url,
		"source": p.source,
		"custom": p.custom,
		"locked_fields": p.locked_fields,
		"variants": [_variant_dict(v) for v in sorted(p.variants.all(), key=lambda v: (v.label, v.id))],
		"images": [image_dict(i) for i in sorted(p.images.all(), key=lambda i: (i.created_at, i.id))],
		"created_at": p.created_at.isoformat(),
	}
