import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, Field

from agents.llm import get_llm
from knowledge.models import Document, KnowledgeBase, ProductCandidate
from knowledge.product_extract import MAX_CHARS_PER_PAGE, MAX_PAGES_PER_RUN, extract_candidates
from usage.utils import llm_costs, track_token_usage

from .models import OrderSettings

logger = logging.getLogger(__name__)
SETUP_MAX_TOKENS = 2048
SYSTEM = (
	"You read a business's documents. Extract its delivery zones with their charges, and its payment methods. "
	"Use only what the text states. If a charge is not stated, use null. Do not guess."
)
STEPS = [
	("content", "Check your website and documents"),
	("products", "Find your products"),
	("delivery", "Find delivery and payment details"),
]
STEP_KEYS = [key for key, _ in STEPS]
STALE_AFTER = timedelta(minutes=5)  # a running step with no heartbeat this long was interrupted
USER_ERROR = "Something went wrong while reading your website. Try again."


class SetupBusy(Exception):
	"""Another run holds a step with a fresh heartbeat."""


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
	# named fields only: the step job's own columns are written by _mutate_steps, never from here
	settings.save(update_fields=["zones", "payment_methods", "setup_report", "updated_at"])
	return settings.setup_report


def _fresh_steps() -> list[dict]:
	return [
		{"key": key, "label": label, "status": "pending", "detail": "", "error_code": "", "error": "",
		 "started_at": None, "finished_at": None, "heartbeat_at": None}
		for key, label in STEPS
	]


def _now_iso() -> str:
	return timezone.now().isoformat()


def _is_stale(step: dict) -> bool:
	beat = step.get("heartbeat_at")
	return not beat or timezone.now() - datetime.fromisoformat(beat) > STALE_AFTER


def _derive_status(steps: list[dict]) -> str:
	statuses = {s["status"] for s in steps}
	if "failed" in statuses:
		return "failed"
	if "needs_input" in statuses:
		return "needs_input"
	if "running" in statuses:
		return "running"
	if statuses == {"done"}:
		return "ready"
	if statuses == {"pending"}:
		return "not_started"
	return "running"  # some done, the rest queued for the next step


def _mutate_steps(project_id: str, change) -> list[dict]:
	"""Apply change(steps) under a row lock, then save the steps and their derived status together."""
	with transaction.atomic():
		settings, _ = OrderSettings.objects.get_or_create(project_id=project_id)
		row = OrderSettings.objects.select_for_update().get(pk=settings.pk)
		steps = change(row.setup_steps or _fresh_steps())
		row.setup_steps = steps
		row.setup_status = _derive_status(steps)
		row.save(update_fields=["setup_steps", "setup_status", "updated_at"])
	return steps


def _set_step(project_id: str, key: str, **fields) -> None:
	def change(steps):
		for step in steps:
			if step["key"] == key:
				step.update(fields)
		return steps

	_mutate_steps(project_id, change)


def setup_state(settings: OrderSettings) -> dict:
	"""What the UI shows. A running step with a stale heartbeat reads as failed, but is not written back."""
	steps = []
	for step in settings.setup_steps or _fresh_steps():
		if step["status"] == "running" and _is_stale(step):
			step = {**step, "status": "failed", "error_code": "interrupted", "error": "Setup was interrupted. Try again."}
		steps.append(step)
	return {"status": _derive_status(steps), "steps": steps, "report": settings.setup_report}


def _content(project_id: str, kb_id: str, beat) -> tuple[str, str, str]:
	count = Document.objects.filter(kb_id=kb_id).exclude(markdown_content="").count()
	if not count:
		return "needs_input", "Add a website or document with text first.", "no_content"
	return "done", f"Read {count} pages and documents", ""


def _products(project_id: str, kb_id: str, beat) -> tuple[str, str, str]:
	ProductCandidate.objects.filter(kb_id=kb_id, status="pending").delete()  # a retry must not double them
	count = extract_candidates(KnowledgeBase.objects.get(pk=kb_id), on_page=lambda n: beat(f"Reading page {n}"))
	kb = KnowledgeBase.objects.get(pk=kb_id)
	message = f"Found {count} products to review"
	if kb.last_extraction.get("status", "").startswith("stopped"):
		message += f", {kb.last_extraction['skipped']} skipped: out of credits"
	return "done", message, ""


def _delivery(project_id: str, kb_id: str, beat) -> tuple[str, str, str]:
	found = extract_order_settings(project_id, kb_id)["found"]
	payments = f"{found['payment_methods']} payment methods" if found["payment_methods"] else "no payment methods"
	return "done", f"Found {found['zones']} delivery zones, {payments}", ""


STEP_FUNCS = {"content": _content, "products": _products, "delivery": _delivery}


def _dispatch(project_id: str, kb_id: str, key: str) -> None:
	run_step.apply_async(args=[project_id, kb_id, key])


def _run_step(project_id: str, kb_id: str, key: str) -> None:
	now = _now_iso()
	_set_step(project_id, key, status="running", detail="", error_code="", error="",
	          started_at=now, finished_at=None, heartbeat_at=now)
	try:
		status, message, code = STEP_FUNCS[key](project_id, kb_id, lambda text: _set_step(project_id, key, heartbeat_at=_now_iso(), detail=text))
	except Exception:
		logger.exception(f"Order setup step {key} failed for project {project_id}")
		_set_step(project_id, key, status="failed", error_code="step_error", error=USER_ERROR, finished_at=_now_iso())
		return
	if status == "done":
		_set_step(project_id, key, status="done", detail=message, error_code="", error="", finished_at=_now_iso())
		following = STEP_KEYS[STEP_KEYS.index(key) + 1:]
		if following:
			_dispatch(project_id, kb_id, following[0])
		return
	_set_step(project_id, key, status=status, detail="", error_code=code, error=message, finished_at=_now_iso())


@shared_task
def run_step(project_id: str, kb_id: str, key: str) -> None:
	_run_step(project_id, kb_id, key)


def run_setup(project_id: str, kb_id: str, mode: str = "resume") -> None:
	"""resume: start at the first step not done. restart: reset every step. Never touches user work."""
	def change(steps):
		if any(s["status"] == "running" and not _is_stale(s) for s in steps):
			raise SetupBusy()
		if mode == "restart":
			steps = _fresh_steps()
		for step in steps:
			if step["status"] != "done":
				step.update(status="pending", detail="", error_code="", error="", heartbeat_at=None)
		# claim the first step here, under the lock: a poll between now and the worker never sees "not_started"
		first_open = next((s for s in steps if s["status"] == "pending"), None)
		if first_open:
			first_open.update(status="running", started_at=_now_iso(), heartbeat_at=_now_iso())
		return steps

	steps = _mutate_steps(project_id, change)
	first = next((s["key"] for s in steps if s["status"] == "running"), None)
	if first:
		_dispatch(project_id, kb_id, first)
