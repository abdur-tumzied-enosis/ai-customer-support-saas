from decimal import Decimal, InvalidOperation

from celery import chain, shared_task
from django.utils import timezone
from pydantic import BaseModel, Field

from agents.llm import get_llm
from knowledge.models import Document, ProductCandidate
from knowledge.product_extract import MAX_CHARS_PER_PAGE, MAX_PAGES_PER_RUN
from knowledge.tasks import extract_product_candidates
from usage.utils import llm_costs, track_token_usage

from .models import OrderSettings

SETUP_MAX_TOKENS = 2048
SYSTEM = (
	"You read a business's documents. Extract its delivery zones with their charges, and its payment methods. "
	"Use only what the text states. If a charge is not stated, use null. Do not guess."
)


class ExtractedZone(BaseModel):
	name: str
	charge: str | None = None


class ExtractedPayment(BaseModel):
	label: str


class OrderSettingsExtract(BaseModel):
	zones: list[ExtractedZone] = Field(default_factory=list, max_length=50)
	payment_methods: list[ExtractedPayment] = Field(default_factory=list, max_length=10)


def _text(kb_id) -> str:
	docs = Document.objects.filter(kb_id=kb_id).exclude(markdown_content="")[:MAX_PAGES_PER_RUN]
	return "\n\n".join(doc.markdown_content[:MAX_CHARS_PER_PAGE] for doc in docs)


def _charge(value) -> str | None:
	if value in (None, ""):
		return None
	try:
		amount = Decimal(str(value))
	except InvalidOperation:
		return None
	return str(amount) if amount.is_finite() and amount >= 0 else None


def _ask(project_id: str, text: str) -> OrderSettingsExtract:
	"""One structured call, shaped like channels_app.memory.ask_llm, tracked under its own source."""
	llm = get_llm(role="memory", max_tokens=SETUP_MAX_TOKENS)
	out = llm.with_structured_output(OrderSettingsExtract, include_raw=True, method="function_calling").invoke(
		[("system", SYSTEM), ("human", text)]
	)
	usage = getattr(out.get("raw"), "usage_metadata", None) or {}
	cost_in, cost_out = llm_costs([out.get("raw")])
	track_token_usage(project_id, "chat_in", usage.get("input_tokens", 0), {"source": "order_settings_extract"}, cost_usd=cost_in)
	track_token_usage(project_id, "chat_out", usage.get("output_tokens", 0), {"source": "order_settings_extract"}, cost_usd=cost_out)
	if out.get("parsed") is None:
		raise ValueError(f"setup model returned no settings: {out.get('parsing_error')}")
	return out["parsed"]


@shared_task
def extract_order_settings(project_id: str, kb_id: str) -> dict:
	settings, _ = OrderSettings.objects.get_or_create(project_id=project_id)
	text = _text(kb_id)
	extracted = _ask(project_id, text) if text else OrderSettingsExtract()
	# only fill what is empty: a client's edits are never overwritten
	if not settings.zones and extracted.zones:
		settings.zones = [{"name": z.name.strip(), "charge": _charge(z.charge)} for z in extracted.zones if z.name.strip()]
	if not settings.payment_methods and extracted.payment_methods:
		settings.payment_methods = [
			{"label": p.label.strip(), "default": i == 0} for i, p in enumerate(extracted.payment_methods) if p.label.strip()
		]
	not_found = []
	if not any(z.charge for z in extracted.zones):
		not_found.append("delivery charges")
	if not extracted.payment_methods:
		not_found.append("payment methods")
	settings.setup_report = {
		"found": {
			"zones": len(extracted.zones),
			"payment_methods": len(extracted.payment_methods),
			"product_candidates": ProductCandidate.objects.filter(kb_id=kb_id).count(),
		},
		"not_found": not_found,
	}
	settings.save()
	return settings.setup_report


@shared_task
def finish_setup(project_id: str) -> None:
	OrderSettings.objects.filter(project_id=project_id).update(setup_status="ready", updated_at=timezone.now())


def run_setup(project_id: str, kb_id: str) -> None:
	OrderSettings.objects.get_or_create(project_id=project_id)
	OrderSettings.objects.filter(project_id=project_id).update(setup_status="running", updated_at=timezone.now())
	chain(
		extract_product_candidates.si(kb_id),
		extract_order_settings.si(project_id, kb_id),
		finish_setup.si(project_id),
	).apply_async()
