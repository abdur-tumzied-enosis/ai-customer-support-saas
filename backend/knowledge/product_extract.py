import logging
from decimal import Decimal

from pydantic import BaseModel, Field

from agents.llm import get_llm
from usage.utils import llm_costs, track_token_usage

from .catalog import match_product
from .models import Document, IgnoredProductPage, KnowledgeBase, ProductCandidate
from .price import parse_price

logger = logging.getLogger(__name__)

MAX_PAGES_PER_RUN = 50
MAX_CHARS_PER_PAGE = 24000
MAX_CANDIDATES_PER_PAGE = 30
MAX_VARIANTS = 20
EXTRACT_MAX_TOKENS = 4096
COMPARED_FIELDS = ("description", "sku", "external_id", "price")

SYSTEM = (
	"You extract products for sale from one web page or document. Return only real products a customer can buy; "
	"skip navigation, blog posts, policies and category lists. Copy values as written on the page. "
	"Give the price as the page's own text (price_text), never a computed number. "
	"image_srcs must be copied exactly from the image list provided. confidence is 0 to 1."
)


class ExtractedVariant(BaseModel):
	label: str
	sku: str = ""
	price_text: str | None = None
	attributes: dict[str, str] = Field(default_factory=dict)


class ExtractedProduct(BaseModel):
	name: str
	description: str = ""
	external_id: str | None = None
	sku: str = ""
	price_text: str | None = Field(default=None, description="price exactly as written on the page")
	variants: list[ExtractedVariant] = Field(default_factory=list)
	image_srcs: list[str] = Field(default_factory=list)
	custom: dict[str, str] = Field(default_factory=dict)
	confidence: float = 0.5


class ExtractionResult(BaseModel):
	products: list[ExtractedProduct] = Field(default_factory=list)


def _price_str(value: Decimal | None) -> str | None:
	return None if value is None else format(value.normalize(), "f")


def _ask(kb, doc, user: str) -> ExtractionResult:
	llm = get_llm(role="memory", max_tokens=EXTRACT_MAX_TOKENS)
	out = llm.with_structured_output(ExtractionResult, include_raw=True, method="function_calling").invoke(
		[("system", SYSTEM), ("human", user)]
	)
	raw = out.get("raw")
	usage = getattr(raw, "usage_metadata", None) or {}
	meta = {"source": "product_extract", "kb_id": str(kb.id), "document_id": str(doc.id),
		"model": (getattr(raw, "response_metadata", None) or {}).get("model_name", "")}
	cost_in, cost_out = llm_costs([raw])
	project_id = str(kb.project_id)
	track_token_usage(project_id, "chat_in", usage.get("input_tokens", 0), meta, cost_usd=cost_in)
	track_token_usage(project_id, "chat_out", usage.get("output_tokens", 0), meta, cost_usd=cost_out)
	if out.get("parsed") is None:
		raise ValueError(f"model returned no product list: {out.get('parsing_error')}")
	return out["parsed"]


def _changes(product, payload: dict) -> dict:
	locked = set(product.locked_fields or [])
	current = {"description": product.description, "sku": product.sku, "external_id": product.external_id,
		"price": _price_str(product.price)}
	changes = {}
	for field in COMPARED_FIELDS:
		new = payload[field]
		# an empty extracted value never overwrites; only a real, different value is a change
		if field in locked or new in (None, "") or new == current[field]:
			continue
		changes[field] = {"old": current[field], "new": new}
	return changes


def _cut(value, n: int):
	return value if value is None else str(value)[:n]


def _candidate(kb, doc, url: str, item: ExtractedProduct):
	srcs = {i.get("src") for i in doc.images_metadata or [] if isinstance(i, dict)}
	variants = [
		{"label": _cut(v.label, 255), "sku": _cut(v.sku, 128), "price": _price_str(parse_price(v.price_text)),
			"attributes": {_cut(k, 255): _cut(a, 255) for k, a in v.attributes.items()}}
		for v in item.variants[:MAX_VARIANTS]
	]
	payload = {
		"description": _cut(item.description, 2000), "external_id": _cut(item.external_id or None, 255),
		"sku": _cut(item.sku, 128), "price": _price_str(parse_price(item.price_text)), "variants": variants,
		"image_srcs": [s for s in item.image_srcs if s in srcs],
		"custom": {_cut(k, 255): _cut(v, 255) for k, v in item.custom.items()},
	}
	first_attrs = variants[0]["attributes"] if variants else {}
	product, matched_by = match_product(kb, payload["external_id"], payload["sku"], item.name, first_attrs)
	changes = {}
	if product:
		changes = _changes(product, payload)
		if not changes:
			return None
	payload["matched_by"] = matched_by  # the review shows "Matched by name" so the owner can check it
	name = item.name[:255]
	if ProductCandidate.objects.filter(kb=kb, page_url=url, name=name, status="pending").exists():
		return None
	return ProductCandidate(
		kb=kb, document=doc, page_url=url, name=name, payload=payload,
		confidence=min(1.0, max(0.0, float(item.confidence))), matched_product=product, changes=changes,
	)


def extract_candidates(kb: KnowledgeBase, document_ids: list[str] | None = None) -> int:
	"""Write review candidates from the KB's pages. Never creates or edits a Product."""
	ignored = list(IgnoredProductPage.objects.filter(kb=kb).values_list("url", flat=True))
	docs = Document.objects.filter(kb=kb).exclude(markdown_content="").order_by("created_at")
	if document_ids:
		docs = docs.filter(id__in=document_ids)
	if ignored:
		docs = docs.exclude(page_url__in=ignored).exclude(page_url="", uri__in=ignored)
	created = attempted = failed = 0
	for doc in docs[:MAX_PAGES_PER_RUN]:
		url = doc.page_url or doc.uri
		if url in ignored or not doc.markdown_content.strip():
			continue
		attempted += 1
		try:
			images = "\n".join(str(i.get("src")) for i in doc.images_metadata or [] if isinstance(i, dict) and i.get("src"))
			user = f"URL: {url}\nTitle: {doc.page_title}\n\nImages:\n{images}\n\nContent:\n{doc.markdown_content[:MAX_CHARS_PER_PAGE]}"
			result = _ask(kb, doc, user)
			rows = [c for c in (_candidate(kb, doc, url, p) for p in result.products[:MAX_CANDIDATES_PER_PAGE]) if c]
			ProductCandidate.objects.bulk_create(rows)
		except Exception:
			failed += 1
			logger.exception(f"Product extraction failed for {url!r}")
			continue
		created += len(rows)
	if attempted and failed == attempted:
		raise RuntimeError(f"product extraction: all {attempted} pages failed")
	logger.info(f"Product extraction: attempted={attempted} failed={failed} candidates={created}")
	return created
