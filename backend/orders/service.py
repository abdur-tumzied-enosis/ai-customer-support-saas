from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from knowledge.models import Product, ProductVariant

from . import hooks
from .models import Order, OrderCounter, OrderItem, OrderSettings, OrderStatusHistory
from .phone import normalise_bd_phone
from .pricing import quotes_for
from .totals import MAX_LINE_QTY, collect_amount, compute_totals

CUSTOMER_FIELDS = {"customer_name", "customer_phone", "customer_address", "zone_name", "customer_note", "custom"}
PROBLEM_TEXT = {"needs_price": "has no price yet", "unavailable": "is not available"}


class OrderValidationError(Exception):
	def __init__(self, problems):
		super().__init__("order validation failed")
		self.problems = problems


def ordering_active(conversation):
	"""The settings when ordering runs for this conversation, else None."""
	settings = OrderSettings.objects.filter(project_id=conversation.project_id).first()
	if settings is None:
		return None
	if settings.enabled and conversation.channel in settings.channels:
		return settings
	# the owner's test chat always gets ordering while it is live, so they can try what customers see
	if (settings.enabled or settings.test_mode) and conversation.channel == "self":
		return settings
	return None


def get_or_create_draft(conversation, settings):
	for _ in range(2):  # the one-draft constraint can race; the second pass finds the winner's draft
		draft = Order.objects.filter(conversation=conversation, status="draft").first()
		if draft:
			return draft
		try:
			with transaction.atomic():
				return Order.objects.create(
					project=conversation.project, conversation=conversation, kb=conversation.knowledge_base,
					channel=conversation.channel, contact=conversation.contact,
					is_test=conversation.channel == "self", currency=conversation.project.currency,
				)
		except IntegrityError:
			continue
	raise IntegrityError("draft race not resolved")


def _unit_price(quote, variant):
	if variant is not None and variant.price is not None:
		return variant.price
	return quote.price


def _problem(product, variant, quote):
	if _unit_price(quote, variant) is None:
		return "needs_price"
	if product.status != "active" or not quote.available or (variant is not None and not variant.available):
		return "unavailable"
	return None


def _lookup(model, pk, **scope):
	try:
		return model.objects.filter(pk=pk, **scope).first()
	except (ValueError, ValidationError):  # a malformed id is simply not found
		return None


def _zone(settings, name):
	return next((z for z in settings.zones if z["name"] == name), None)


def _recalculate(order, settings):
	zone = _zone(settings, order.zone_name)
	charge = Decimal(zone["charge"]) if zone and zone["charge"] not in (None, "") else Decimal("0")
	totals = compute_totals([(i.unit_price, i.quantity) for i in order.items.all()], charge)
	order.zone_charge = charge
	order.subtotal = totals.subtotal
	order.total = totals.total
	order.collect_amount = collect_amount(totals.total, order.advance_paid)
	order.save(update_fields=["zone_charge", "subtotal", "total", "collect_amount", "updated_at"])


def apply_lines(order, items: list[dict], settings) -> None:
	"""Replace the order's lines. Prices come from the catalog; nothing is saved if any line is refused."""
	problems, rows = [], []
	for item in items:
		qty = item.get("quantity")
		if not (isinstance(qty, int) and not isinstance(qty, bool) and 1 <= qty <= MAX_LINE_QTY):
			problems.append({"code": "bad_quantity", "message": f"quantity must be 1 to {MAX_LINE_QTY}"})
			continue
		product = _lookup(Product, item.get("product_id"), kb=order.kb)
		if product is None:
			problems.append({"code": "unknown_product", "message": "product is not in this knowledge base"})
			continue
		variant = None
		if item.get("variant_id"):
			variant = _lookup(ProductVariant, item["variant_id"], product=product)
			if variant is None:
				problems.append({"code": "unknown_variant", "product_id": str(product.id), "message": "variant not found"})
				continue
		quote = quotes_for([product])[str(product.id)]
		code = _problem(product, variant, quote)
		if code:
			problems.append({"code": code, "product_id": str(product.id), "message": f"{product.name} {PROBLEM_TEXT[code]}"})
			continue
		unit = _unit_price(quote, variant)
		rows.append(OrderItem(
			order=order, product=product, variant=variant, product_external_id=product.external_id or "",
			sku=(variant.sku if variant and variant.sku else product.sku), name=product.name,
			variant_label=variant.label if variant else "", unit_price=unit, quantity=qty,
			product_url=product.product_url, line_total=unit * qty,
		))
	if problems:
		raise OrderValidationError(problems)
	with transaction.atomic():
		order.items.all().delete()
		OrderItem.objects.bulk_create(rows)
		_recalculate(order, settings)


def update_customer(order, data: dict, settings) -> None:
	unknown = set(data) - CUSTOMER_FIELDS
	if unknown:
		raise ValidationError(f"unknown field: {sorted(unknown)[0]}")
	for key, value in data.items():
		if key == "custom" and value is not None and not isinstance(value, dict):
			raise ValidationError("custom must be an object")
		if key != "custom" and value is not None and not isinstance(value, str):
			raise ValidationError(f"{key} must be text")
		if key == "customer_phone":
			phone = normalise_bd_phone(value)
			if value and phone is None:
				raise ValidationError("invalid phone number")
			order.customer_phone = phone or ""
		elif key == "zone_name":
			if value and _zone(settings, value) is None:
				raise ValidationError("unknown zone")
			order.zone_name = value or ""
		elif key == "custom":
			declared = {f["key"] for f in settings.form_fields.get("custom", [])}
			if set(value or {}) - declared:
				raise ValidationError("undeclared custom field")
			order.custom = dict(value or {})
		else:
			setattr(order, key, str(value or "").strip())
	order.save()
	_recalculate(order, settings)


def validate_for_submit(order, settings) -> list[dict]:
	problems = []
	for field, label in (("customer_name", "Name"), ("customer_phone", "Phone"),
	                     ("customer_address", "Address"), ("zone_name", "Zone")):
		if not getattr(order, field):
			problems.append({"code": "missing", "field": field, "message": f"{label} is required"})
	if order.zone_name:
		zone = _zone(settings, order.zone_name)
		if zone is None:
			problems.append({"code": "invalid_zone", "field": "zone_name", "message": "Zone is not offered"})
		elif zone["charge"] in (None, ""):
			problems.append({"code": "zone_charge_missing", "field": "zone_name", "message": "Delivery charge is not set"})
	for f in settings.form_fields.get("custom", []):
		if f.get("required") and not (order.custom or {}).get(f["key"]):
			problems.append({"code": "missing", "field": f"custom.{f['key']}", "message": f"{f.get('label', f['key'])} is required"})
	items = list(order.items.select_related("product", "variant"))
	if not items:
		problems.append({"code": "no_items", "message": "Add at least one product"})
	for item in items:
		if item.product is None:
			problems.append({"code": "unavailable", "message": "A product on this order was removed"})
			continue
		quote = quotes_for([item.product], fresh=True)[str(item.product.id)]
		if item.variant is None and item.variant_label:  # the variant was deleted: its link is null, its label remains
			problems.append({"code": "variant_removed", "product_id": str(item.product.id), "message": f"{item.product.name} ({item.variant_label}) is no longer sold"})
			continue
		code = _problem(item.product, item.variant, quote)
		if code:
			problems.append({"code": code, "product_id": str(item.product.id), "message": f"{item.product.name} {PROBLEM_TEXT[code]}"})
	return problems


def _reprice(order, settings):
	for item in order.items.select_related("product", "variant"):
		quote = quotes_for([item.product], fresh=True)[str(item.product.id)]
		item.unit_price = _unit_price(quote, item.variant)
		item.line_total = item.unit_price * item.quantity
		item.save(update_fields=["unit_price", "line_total"])
	_recalculate(order, settings)


def submit_order(order, actor):
	actor_type, actor_id = actor
	with transaction.atomic():
		locked = Order.objects.select_for_update().get(pk=order.pk)
		if locked.status == "new":  # a retry or double click: the first submit already numbered it
			return locked
		if locked.status != "draft":
			raise OrderValidationError([{"code": "not_draft", "message": "Only a draft can be submitted"}])
		settings = OrderSettings.objects.get(project_id=locked.project_id)
		problems = validate_for_submit(locked, settings)
		if problems:
			raise OrderValidationError(problems)
		_reprice(locked, settings)
		counter, _ = OrderCounter.objects.get_or_create(project_id=locked.project_id)
		counter = OrderCounter.objects.select_for_update().get(pk=counter.pk)
		counter.last_number += 1
		counter.save(update_fields=["last_number"])
		locked.number = counter.last_number
		locked.status = "new"
		locked.submitted_at = timezone.now()
		locked.save()
		OrderStatusHistory.objects.create(
			order=locked, actor_type=actor_type, actor_id=actor_id, from_status="draft", to_status="new",
		)
		transaction.on_commit(lambda: hooks.order_submitted.delay(str(locked.id)))
	return locked


class OrderStatusError(Exception):
	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code, self.message = code, message


def set_status(order, to_status, actor):
	if to_status == "draft":  # a draft exists only as the chat's open order; it is never set by hand
		raise OrderStatusError("invalid_status_change", "An order cannot be set back to draft")
	if order.number is None and to_status not in ("cancelled", "abandoned"):  # only submit_order numbers an order
		raise OrderStatusError("invalid_status_change", "Submit the draft before changing its status")
	actor_type, actor_id = actor
	with transaction.atomic():
		from_status = order.status
		order.status = to_status
		order.save(update_fields=["status", "updated_at"])
		OrderStatusHistory.objects.create(
			order=order, actor_type=actor_type, actor_id=actor_id, from_status=from_status, to_status=to_status,
		)
		transaction.on_commit(lambda: hooks.order_status_changed.delay(str(order.id), from_status, to_status))
	return order
