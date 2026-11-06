import json
import logging
from functools import partial

from django.db import transaction
from django.utils import timezone

from .http import OutboundError, safe_request
from .models import OrderActionRun
from .service import OrderStatusError, set_status
from .tasks import run_action_run
from .templates import TemplateError, build_context, render

logger = logging.getLogger(__name__)

EXCERPT_CHARS = 500
REDACTED = "***"
SENDABLE = ("new", "confirmed", "shipped", "delivered")  # never a draft, cancelled or abandoned order


def redact(text: str, headers: list[dict]) -> str:
	for h in headers:
		if h.get("value"):
			text = text.replace(h["value"], REDACTED)
	return text


def already_sent_orders(action, orders) -> list:
	done = set(
		OrderActionRun.objects.filter(action=action, status="succeeded", order__in=orders).values_list("order_id", flat=True)
	)
	return [o for o in orders if o.id in done]


def run_action_for_orders(action, orders, actor, force: bool = False) -> dict:
	"""Queue one run per order. Orders this action already sent are skipped unless force is set."""
	orders = list(orders)
	skip = set() if force else {o.id for o in already_sent_orders(action, orders)}
	queued, already_sent = [], []
	for order in orders:
		if order.id in skip:
			already_sent.append(order.number)
			continue
		run = OrderActionRun.objects.create(
			action=action, order=order, status="queued", actor_type=actor[0], actor_id=actor[1],
		)
		queued.append(run.id)
		transaction.on_commit(partial(run_action_run.delay, run.id))
	return {"queued": queued, "already_sent": already_sent}


def _dig(data, path: str):
	for part in path.split("."):
		data = data[int(part)] if isinstance(data, list) else data[part]
	return data


def _finish(run, status: str, headers: list[dict], http_status=None, excerpt: str = "", error: str = "") -> None:
	run.status = status
	run.http_status = http_status
	run.response_excerpt = redact(excerpt, headers)[:EXCERPT_CHARS]
	run.error = redact(error, headers)
	run.finished_at = timezone.now()
	run.save(update_fields=["status", "http_status", "response_excerpt", "error", "finished_at"])


def _apply_success(action, order, text: str) -> str:
	"""Store the mapped tracking number and move the order to the success status. Returns a note for the run."""
	note = ""
	path = action.response_mapping.get("tracking_number")
	if path:
		try:
			value = _dig(json.loads(text), path)
		except (ValueError, KeyError, IndexError, TypeError):
			value = None
		if value:
			order.tracking_number = str(value)[:255]
			order.save(update_fields=["tracking_number", "updated_at"])
		else:
			note = "The response had no tracking number at the mapped path"
	if action.success_status:
		try:
			set_status(order, action.success_status, ("system", action.name))
		except OrderStatusError as e:
			note = e.message
	return note


def execute_run(run_id: int) -> None:
	"""Send one order's request. Every failure is recorded on this run only; the caller never sees it raised."""
	run = OrderActionRun.objects.select_related("action", "order").get(pk=run_id)
	action, order, headers = run.action, run.order, run.action.headers
	run.status = "running"
	run.save(update_fields=["status"])
	try:
		context = build_context(order)
		url = render(action.url, context, "url")
		body = render(action.body_template, context, "json") if action.method == "POST" and action.body_template else None
		result = safe_request(action.method, url, {h["name"]: h["value"] for h in headers}, body)
	except TemplateError as e:
		return _finish(run, "failed", headers, error=e.message)
	except OutboundError as e:
		return _finish(run, "failed", headers, error=str(e))
	except Exception:  # one order must not stop the bulk run; the cause stays in the server log
		logger.exception("Action run %s failed unexpectedly", run_id)
		return _finish(run, "failed", headers, error="Unexpected error")
	if result.status not in action.success_codes:
		return _finish(run, "failed", headers, http_status=result.status, excerpt=result.text, error=f"HTTP {result.status}")
	note = _apply_success(action, order, result.text)
	_finish(run, "succeeded", headers, http_status=result.status, excerpt=result.text, error=note)
