import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction

from accounts.models import ProjectUser
from accounts.tasks import queue_email

from .models import Product, ProductVariant
from .price import BENGALI_DIGITS, parse_price


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


def save_variants(product, items: list[dict]) -> None:
	"""Replace the product's whole variant set with cleaned `items`, then recompute and save its status."""
	with transaction.atomic():
		product.variants.all().delete()
		ProductVariant.objects.bulk_create([
			ProductVariant(
				product=product, external_id=i.get("external_id"), sku=i.get("sku", ""), label=i["label"],
				attributes=i.get("attributes") or {}, price=i.get("price"), available=i.get("available", True),
			)
			for i in items
		])
		getattr(product, "_prefetched_objects_cache", {}).pop("variants", None)  # drop any stale prefetch
		product.status = compute_status(product, product.kb.product_fields.all())
		product.save()


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


@dataclass
class SourceRow:
	"""One source row. None means "this source does not supply the field": a sync leaves it alone."""
	external_id: str | None = None
	sku: str = ""
	name: str = ""
	price_text: str | None = None
	available: bool | None = None
	description: str | None = None
	product_url: str | None = None
	custom: dict = field(default_factory=dict)
	variant_label: str = ""
	attributes: dict = field(default_factory=dict)


def notify_owners_price_cleared(product) -> None:
	admins = ProjectUser.objects.filter(project=product.kb.project, role="admin").select_related("user")
	body = (f'The price of "{product.name}" was removed by its product source, so it is hidden from customers '
		f"until you add a price.\n\nAdd a price: {settings.SITE_URL}/knowledge")
	for pu in admins:
		if pu.user.email:
			queue_email(pu.user.email, f"Price removed: {product.name}", body)


def apply_source_row(kb, source, row: SourceRow, sync_started_at, fields=None) -> str:
	"""The one place sync rules live (CSV, Sheet and candidate import call this).
	Returns created, updated, unchanged or rejected. Never locks fields: only apply_owner_edit does."""
	if not (row.name or "").strip():
		return "rejected"
	fields = list(kb.product_fields.all()) if fields is None else fields
	price = parse_price(row.price_text) if row.price_text is not None else None
	product, _ = match_product(kb, row.external_id, row.sku, row.name, row.attributes)
	if product is None:
		new = Product(
			kb=kb, source=source.kind, source_ref=source, external_id=row.external_id or None, sku=row.sku or "",
			name=row.name.strip()[:255], description=row.description or "", price=price,
			available=True if row.available is None else row.available, product_url=row.product_url or "",
			custom=row.custom or {}, last_seen_at=sync_started_at,
		)
		new.status = compute_status(new, fields)
		try:
			with transaction.atomic():
				new.save()
		except IntegrityError:
			return "rejected"
		if row.attributes:  # a variant carries the attributes so the next sync matches this row by name + attributes
			ProductVariant.objects.create(product=new, label=row.variant_label, attributes=row.attributes)
		return "created"

	locked = set(product.locked_fields or [])

	def snapshot():
		return (product.name, product.description, product.price, product.available, product.product_url,
			product.custom, product.status)

	before = snapshot()
	had_price = product.price is not None
	if "name" not in locked:
		product.name = row.name.strip()[:255]
	if row.description is not None and "description" not in locked:
		product.description = row.description
	if row.price_text is not None and "price" not in locked:
		product.price = price
	if row.available is not None and "available" not in locked:
		product.available = row.available
	if row.product_url and "product_url" not in locked:
		product.product_url = row.product_url
	if row.custom and "custom" not in locked:
		product.custom = {**(product.custom or {}), **row.custom}
	rebound = product.source_ref_id != source.id
	product.source_ref = source
	if product.status == "hidden" and "status" not in locked:
		product.status = "needs_info"  # back in the source after a removal: let compute_status decide
	if "status" not in locked or product.price is None:  # a locked status holds, except active needs a price (DB constraint)
		product.status = compute_status(product, fields)
	cleared = had_price and product.price is None
	changed = before != snapshot()
	product.last_seen_at = sync_started_at
	if changed or rebound:
		product.save()
	else:
		Product.objects.filter(pk=product.pk).update(last_seen_at=sync_started_at)
	if cleared:
		notify_owners_price_cleared(product)
	return "updated" if changed else "unchanged"


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
