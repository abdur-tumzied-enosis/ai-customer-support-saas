"""Durable once-only execution of side-effecting agent tools.

A reply can be re-run after a crash or a task retry, and the model then calls its tools again. `once` records each
side-effecting call (agents.ToolExecution) so the second run gets the stored result instead of a second order or POST.
The key is the tool name plus its arguments: a model that regenerates the same call is caught, one that changes the
arguments is not (shortcut: nothing else identifies a regenerated call; plan steps would need persisting for that).
"""
import functools
import hashlib
import inspect
import json
import logging
from typing import Callable, Optional

from django.utils import timezone

from .models import ToolExecution

logger = logging.getLogger(__name__)

UNCONFIRMED = (
	"ERROR: this action was already attempted for this message and its outcome was not recorded, so it may have been done. "
	"Do not repeat it. Tell the customer a person will confirm it."
)


def once(context, fn: Callable[..., str], *, when: Optional[Callable[[dict], bool]] = None,
		unsure: Optional[Callable[[str], bool]] = None, replay: Optional[Callable[[str], None]] = None) -> Callable[..., str]:
	"""Wrap a side-effecting tool. when(args): only these calls are recorded (default all). unsure(result): the result
	means the effect may have happened anyway (a timed-out POST), so it is held as unknown, never retried.
	replay(result): restore what the live call sets on `context` when a stored result is returned instead.
	An unknown outcome hands the conversation to staff, the same way escalate_to_human does."""
	signature = inspect.signature(fn)

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		bound = signature.bind(*args, **kwargs)
		bound.apply_defaults()  # a default passed explicitly is the same call
		payload = dict(bound.arguments)
		if not context.message_id or (when and not when(payload)):  # no inbound message (evals, test chat): nothing to dedupe
			return fn(*args, **kwargs)
		key = hashlib.sha256(f"{fn.__name__}:{json.dumps(payload, sort_keys=True, default=str)}".encode()).hexdigest()
		row, created = ToolExecution.objects.get_or_create(
			message_id=context.message_id, operation_key=key,
			defaults={"tool_name": fn.__name__, "input_payload": json.loads(json.dumps(payload, default=str))},
		)
		if not created:
			if row.status == "completed":
				logger.info(f"[Tool: {fn.__name__}] already done for message {context.message_id}, reusing the result")
				context.api_call_log.append({"method": "POST", "url": f"replayed:{fn.__name__}"})  # reply.py: an action happened
				if replay:
					replay(row.result)
				return row.result
			# failed before doing anything: this run takes it over; otherwise a run started it and never recorded the outcome
			if not ToolExecution.objects.filter(pk=row.pk, status="failed").update(status="executing", error=""):
				ToolExecution.objects.filter(pk=row.pk, status="executing").update(status="unknown")
				logger.error(f"[Tool: {fn.__name__}] outcome unknown for message {context.message_id}, not repeating it")
				context.handoff_reason = context.handoff_reason or f"unconfirmed action: {fn.__name__}"
				return UNCONFIRMED
		try:
			result = fn(*args, **kwargs)
		except Exception as exc:
			ToolExecution.objects.filter(pk=row.pk).update(status="failed", error=repr(exc)[:2000])
			raise
		if unsure and unsure(result):
			ToolExecution.objects.filter(pk=row.pk).update(status="unknown", result=result)
			context.handoff_reason = context.handoff_reason or f"unconfirmed action: {fn.__name__}"
			return UNCONFIRMED
		ToolExecution.objects.filter(pk=row.pk).update(
			status="failed" if result.startswith("ERROR") else "completed", result=result, completed_at=timezone.now(),
		)
		return result

	return wrapper
