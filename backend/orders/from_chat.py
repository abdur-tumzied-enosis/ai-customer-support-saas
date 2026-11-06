from pydantic import BaseModel, Field

from agents.llm import get_llm
from knowledge.catalog import effective_price
from knowledge.models import Product, ProductVariant
from usage.utils import llm_costs, track_token_usage

from .totals import MAX_LINE_QTY

FROM_CHAT_MAX_TOKENS = 2048
MESSAGE_LIMIT = 40
CATALOG_LIMIT = 200
SYSTEM = (
	"Read this chat between a customer and a business. Pick the products the customer wants, with quantities, "
	"using only the catalog ids given. Pick a variant id only when the customer named one. Take the customer's name, "
	"phone, address and zone only when the chat states them. Do not guess."
)


class PrefillItem(BaseModel):
	product_id: str
	variant_id: str | None = None
	quantity: int = 1


class PrefillCustomer(BaseModel):
	customer_name: str | None = None
	customer_phone: str | None = None
	customer_address: str | None = None
	zone_name: str | None = None
	customer_note: str | None = None


class Prefill(BaseModel):
	items: list[PrefillItem] = Field(default_factory=list, max_length=20)
	customer: PrefillCustomer = Field(default_factory=PrefillCustomer)


def _catalog_text(products, variants) -> str:
	lines = []
	for p in products:
		lines.append(f"{p.id} | {p.name} | key {p.external_id or p.sku or '-'} | price {effective_price(p) or 'none'}")
		lines += [f"  variant {v.id} | {v.label}" for v in variants if v.product_id == p.id]
	return "\n".join(lines)


def _ask(project_id: str, transcript: str, catalog: str) -> Prefill:
	"""One structured call, tracked under its own source."""
	llm = get_llm(role="memory", max_tokens=FROM_CHAT_MAX_TOKENS)
	out = llm.with_structured_output(Prefill, include_raw=True, method="function_calling").invoke(
		[("system", SYSTEM), ("human", f"Catalog:\n{catalog}\n\nChat:\n{transcript}")]
	)
	usage = getattr(out.get("raw"), "usage_metadata", None) or {}
	cost_in, cost_out = llm_costs([out.get("raw")])
	track_token_usage(project_id, "chat_in", usage.get("input_tokens", 0), {"source": "order_from_chat"}, cost_usd=cost_in)
	track_token_usage(project_id, "chat_out", usage.get("output_tokens", 0), {"source": "order_from_chat"}, cost_usd=cost_out)
	if out.get("parsed") is None:
		raise ValueError(f"order prefill returned no result: {out.get('parsing_error')}")
	return out["parsed"]


def prefill_from_conversation(conversation) -> dict:
	"""Suggested lines and customer details from a chat. Saves nothing."""
	products = []
	if conversation.knowledge_base_id:
		products = list(Product.objects.filter(kb_id=conversation.knowledge_base_id).exclude(status="hidden")[:CATALOG_LIMIT])
	by_id = {str(p.id): p for p in products}
	variants = list(ProductVariant.objects.filter(product__in=products))
	recent = list(conversation.messages.order_by("-created_at")[:MESSAGE_LIMIT])[::-1]
	transcript = "\n".join(f"{'customer' if m.direction == 'in' else 'business'}: {m.text}" for m in recent if m.text)
	result = _ask(str(conversation.project_id), transcript, _catalog_text(products, variants))

	items, needs_price = [], []
	for item in result.items:
		product = by_id.get(item.product_id)  # a product that is not in the catalog is dropped
		if product is None or not 1 <= item.quantity <= MAX_LINE_QTY:
			continue
		variant = next((v for v in variants if str(v.id) == item.variant_id and v.product_id == product.id), None)
		if effective_price(product, variant) is None:  # never added: the staff member sets its price first
			if not any(n["product_id"] == str(product.id) for n in needs_price):
				needs_price.append({"product_id": str(product.id), "name": product.name})
			continue
		items.append({
			"product_id": str(product.id), "name": product.name, "variant_id": str(variant.id) if variant else None,
			"quantity": item.quantity,
		})
	return {"items": items, "customer": result.customer.model_dump(exclude_none=True), "needs_price": needs_price}
