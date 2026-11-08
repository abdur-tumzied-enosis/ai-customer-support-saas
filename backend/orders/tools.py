import json

from django.core.exceptions import ValidationError
from django.db.models import Q

from knowledge.catalog import effective_price, product_key
from knowledge.models import Product

from . import service
from .models import Order, OrderStatusHistory
from .pricing import quotes_for
from .service import OrderValidationError, apply_lines, get_or_create_draft, set_status, update_customer, validate_for_submit

MAX_MATCHES = 8


def _error(code: str, message: str) -> str:
	return json.dumps({"success": False, "error": {"code": code, "message": message}})


def _summary(order) -> str:
	lines = "; ".join(
		f"{i.quantity} x {i.name}" + (f" ({i.variant_label})" if i.variant_label else "") for i in order.items.all()
	)
	return f"{lines or 'no items'}; delivery {order.zone_charge}; total {order.total} {order.currency}"


def _draft_view(order, settings) -> str:
	problems = validate_for_submit(order, settings)
	return json.dumps({
		"order_number": order.number,
		"missing": [p["field"] for p in problems if p["code"] == "missing"],
		"problems": problems,
		"subtotal": str(order.subtotal),
		"zone_charge": str(order.zone_charge),
		"total": str(order.total),
		"summary": _summary(order),
	}, ensure_ascii=False)


def build_order_tools(conversation, settings, context) -> list:
	"""Order tools for one reply. Each returns a JSON string. Prices come only from the catalog."""
	actor = ("agent", str(conversation.id))

	def find_products(query: str = "", page: int = 1) -> str:
		"""Find products by name, product key, SKU or variant label, 8 per page. Leave query empty to list
		the whole catalog (e.g. "what do you have?"). Returns {"products", "page", "total", "has_more"}: each
		product has id, variants, price and availability; when has_more is true, call again with page + 1 to
		see more. Use the returned id when adding a product to the order.
		A product without a price is returned without one: never quote a price for it."""
		try:
			page = max(1, int(page))
		except (TypeError, ValueError):
			page = 1
		if conversation.knowledge_base_id is None:
			return json.dumps({"products": [], "page": page, "total": 0, "has_more": False})
		products = Product.objects.filter(kb_id=conversation.knowledge_base_id).exclude(status="hidden")
		query = (query or "").strip()
		if query:
			products = products.filter(
				Q(name__icontains=query) | Q(external_id__iexact=query) | Q(sku__iexact=query)
				| Q(variants__label__icontains=query)
			).distinct()
		products = products.order_by("name", "id")  # stable order so pages never overlap or skip
		total = products.count()
		start = (page - 1) * MAX_MATCHES
		found = list(products[start:start + MAX_MATCHES])
		quotes = quotes_for(found)
		out = []
		for product in found:
			if product.status == "needs_info":
				out.append({"id": str(product.id), "name": product.name, "status": "needs_info"})
				continue
			quote = quotes[str(product.id)]
			out.append({
				"id": str(product.id), "name": product.name, "key": product_key(product),
				"variants": [
					{"id": str(v.id), "label": v.label, "price": str(effective_price(product, v))}
					for v in product.variants.all() if v.available
				],
				"price": None if quote.price is None else str(quote.price),
				"available": quote.available,
			})
		has_more = start + len(found) < total
		return json.dumps({"products": out, "page": page, "total": total, "has_more": has_more}, ensure_ascii=False)

	def update_draft(items: list[dict] | None = None, customer: dict | None = None) -> str:
		"""Save products and customer details on this chat's draft order. items: [{"product_id", "variant_id"
		(optional), "quantity"}], quantity 1 to 100. customer: customer_name, customer_phone, customer_address,
		zone_name, customer_note, custom. Prices come from the catalog only; there is no price argument.
		Returns the totals and what is still missing. Call it again after each change."""
		order = get_or_create_draft(conversation, settings)
		try:
			if items is not None:
				apply_lines(order, items, settings)
			if customer is not None:
				update_customer(order, customer, settings)
		except OrderValidationError as e:
			return json.dumps({"order_number": None, "problems": e.problems}, ensure_ascii=False)
		except ValidationError as e:
			return json.dumps({"order_number": None, "problems": [{"code": "invalid", "message": " ".join(e.messages)}]})
		return _draft_view(order, settings)

	def submit_order() -> str:
		"""Submit this chat's draft order. Call it only after the customer has said yes to your read-back summary.
		Tell the customer the order is placed only when this returns an order number."""
		order = Order.objects.filter(conversation=conversation, status="draft").first()
		if order is None:
			return _error("no_draft", "There is no draft order to submit")
		try:
			order = service.submit_order(order, actor)
		except OrderValidationError as e:
			return json.dumps({"order_number": None, "problems": e.problems}, ensure_ascii=False)
		context.order_submitted = True
		# a retry after this point must not re-run the agent and submit again (channels_app/reply.py checks for POSTs)
		context.api_call_log.append({"method": "POST", "url": f"order:{order.number}"})
		return json.dumps({"order_number": order.number, "status": order.status})

	def change_order(order_number: int, items: list[dict] | None = None, customer: dict | None = None) -> str:
		"""Change a submitted order of this chat while it is still new. Pass only what changes: items or customer.
		Returns the updated totals."""
		order = Order.objects.filter(conversation=conversation, number=order_number).first()
		if order is None:
			return _error("not_found", "No order with that number in this chat")
		if order.status != "new":
			return _error("not_editable", "Only a new order can be changed")
		try:
			if items is not None:
				apply_lines(order, items, settings)
			if customer is not None:
				update_customer(order, customer, settings)
		except OrderValidationError as e:
			return json.dumps({"order_number": order.number, "problems": e.problems}, ensure_ascii=False)
		except ValidationError as e:
			return json.dumps({"order_number": order.number, "problems": [{"code": "invalid", "message": " ".join(e.messages)}]})
		OrderStatusHistory.objects.create(
			order=order, actor_type=actor[0], actor_id=actor[1], from_status="new", to_status="new",
		)  # records the edit; the status itself does not change
		return _draft_view(order, settings)

	def cancel_order(order_number: int | None = None) -> str:
		"""Cancel this chat's draft order (no number) or a new order of this chat by its number.
		Only a draft or new order can be cancelled."""
		if order_number is None:
			order = Order.objects.filter(conversation=conversation, status="draft").first()
		else:
			order = Order.objects.filter(conversation=conversation, number=order_number).first()
		if order is None:
			return _error("not_found", "No order to cancel in this chat")
		if order.status not in ("draft", "new"):
			return _error("not_cancellable", "Only a draft or new order can be cancelled")
		set_status(order, "cancelled", actor)
		return json.dumps({"order_number": order.number, "status": "cancelled"})

	return [find_products, update_draft, submit_order, change_order, cancel_order]
