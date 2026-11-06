import json
import re
from urllib.parse import quote

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")
ALLOWED_PLACEHOLDERS = {
	"order.number", "order.total", "order.subtotal", "order.zone_charge", "order.advance_paid",
	"order.collect_amount", "order.status", "order.tracking_number", "order.currency", "order.items",
	"customer.name", "customer.phone", "customer.address", "customer.note", "zone.name",
}


class TemplateError(Exception):
	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code, self.message = code, message


def build_context(order) -> dict:
	"""Placeholder values for one order. Money is sent as decimal strings, the same as the API returns it."""
	items = [
		{
			"name": i.name, "external_id": i.product_external_id, "sku": i.sku, "variant": i.variant_label,
			"quantity": i.quantity, "unit_price": str(i.unit_price), "product_url": i.product_url,
		}
		for i in order.items.all()
	]
	return {
		"order.number": order.number, "order.total": str(order.total), "order.subtotal": str(order.subtotal),
		"order.zone_charge": str(order.zone_charge), "order.advance_paid": str(order.advance_paid),
		"order.collect_amount": str(order.collect_amount), "order.status": order.status,
		"order.tracking_number": order.tracking_number, "order.currency": order.currency, "order.items": items,
		"customer.name": order.customer_name, "customer.phone": order.customer_phone,
		"customer.address": order.customer_address, "customer.note": order.customer_note,
		"zone.name": order.zone_name,
	}


LIVE_PLACEHOLDERS = {"product.external_id", "product.sku"}  # live price URLs only


def render(template: str, context: dict, mode: str, allowed=ALLOWED_PLACEHOLDERS) -> str:
	"""Fill placeholders. mode "json" JSON-encodes each value (the template supplies no quotes);
	mode "url" percent-encodes scalars. Plain string replacement is never used for user values."""
	def substitute(match):
		name = match.group(1)
		if name not in allowed:
			raise TemplateError("unknown_placeholder", f"Unknown placeholder {name}")
		if context.get(name) is None:
			raise TemplateError("missing_value", f"No value for {name}")
		value = context[name]
		if mode == "json":
			return json.dumps(value, ensure_ascii=False)
		if name == "order.items":
			raise TemplateError("items_in_url", "order.items cannot be used in a URL")
		return quote(str(value), safe="")

	return PLACEHOLDER.sub(substitute, template)


SAMPLE_CONTEXT = {
	"order.number": 1001, "order.total": "0.00", "order.subtotal": "0.00", "order.zone_charge": "0.00",
	"order.advance_paid": "0.00", "order.collect_amount": "0.00", "order.status": "new",
	"order.tracking_number": "", "order.currency": "BDT",
	"order.items": [{"name": "x", "external_id": "x", "sku": "x", "variant": "", "quantity": 1,
	                 "unit_price": "0.00", "product_url": ""}],
	"customer.name": "x", "customer.phone": "x", "customer.address": "x", "customer.note": "", "zone.name": "x",
}


def validate_json_template(template: str) -> None:
	rendered = render(template, SAMPLE_CONTEXT, "json")
	try:
		json.loads(rendered)
	except ValueError:
		raise TemplateError("invalid_body_template", "The body template does not produce valid JSON")


def dig(data, path: str):
	"""Value at a dotted path such as data.items.0.code. Raises KeyError, IndexError or TypeError when absent."""
	for part in path.split("."):
		data = data[int(part)] if isinstance(data, list) else data[part]
	return data
