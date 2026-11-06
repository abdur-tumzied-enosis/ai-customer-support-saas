import json
import logging
from functools import partial

from django.db import transaction
from django.utils import timezone

from .http import OutboundError, safe_request
from .models import Order, OrderActionRun
from .service import OrderStatusError, set_status
from .tasks import run_action_run
from .templates import TemplateError, build_context, dig, render

logger = logging.getLogger(__name__)

EXCERPT_CHARS = 500
REDACTED = "***"
SENT_STATUSES = ("queued", "running", "succeeded")  # a send that is pending or done counts as sent
SENDABLE = ("new", "confirmed", "shipped", "delivered")  # never a draft, cancelled or abandoned order


def redact(text: str, headers: list[dict]) -> str:
	for h in headers:
		if h.get("value"):
			text = text.replace(h["value"], REDACTED)
	return text


def already_sent_orders(action, orders) -> list:
	done = set(
		OrderActionRun.objects.filter(action=action, status__in=SENT_STATUSES, order__in=orders).values_list("order_id", flat=True)
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
			value = dig(json.loads(text), path)
		except (ValueError, KeyError, IndexError, TypeError):
			value = None
		if value:
			order.tracking_number = str(value)[:255]
			order.save(update_fields=["tracking_number", "updated_at"])
		else:
			note = "The response had no tracking number at the mapped path"
	if action.success_status:
		with transaction.atomic():  # the courier call took seconds: re-read before changing the status
			locked = Order.objects.select_for_update().get(pk=order.pk)
			if locked.status in ("cancelled", "abandoned"):
				return note or f"Order was {locked.status} meanwhile; its status was not changed"
			if locked.status == action.success_status:
				return note
			try:
				set_status(locked, action.success_status, ("system", action.name))
			except OrderStatusError as e:
				note = e.message
	return note


def execute_run(run_id: int) -> None:
	"""Send one order's request. Every failure is recorded on this run only; the caller never sees it raised."""
	run = OrderActionRun.objects.select_related("action", "order").get(pk=run_id)
	action, order, headers = run.action, run.order, run.action.headers
	if OrderActionRun.objects.filter(pk=run_id, status="queued").update(status="running") == 0:
		return  # already claimed by another delivery of this task, or finished
	run.status = "running"
	try:
		context = build_context(order)
		url = render(action.url, context, "url")
		body = render(action.body_template, context, "json") if action.method == "POST" and action.body_template else None
		result = safe_request(action.method, url, {h["name"]: h["value"] for h in headers}, body)
	except TemplateError as e:
		return _finish(run, "failed", headers, error=e.message)
	except OutboundError as e:
		return _finish(run, "failed", headers, error=str(e))
	except Exception as e:  # one order must not stop the bulk run
		logger.error("Action run %s failed unexpectedly: %s", run_id, type(e).__name__)  # no message or traceback: either may echo a secret
		return _finish(run, "failed", headers, error="Unexpected error")
	if result.status not in action.success_codes:
		return _finish(run, "failed", headers, http_status=result.status, excerpt=result.text, error=f"HTTP {result.status}")
	note = _apply_success(action, order, result.text)
	_finish(run, "succeeded", headers, http_status=result.status, excerpt=result.text, error=note)
