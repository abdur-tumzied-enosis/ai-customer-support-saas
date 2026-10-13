"""Signed outbound webhooks for project events (conversation.handoff, conversation.resumed, message.staff_reply)."""
import hashlib
import hmac
import json
import logging
import uuid
from typing import Optional

import requests
from celery import shared_task
from django.utils import timezone

from agents.api_tool import _is_public_host, _parse_target
from .models import ProjectWebhook

logger = logging.getLogger(__name__)

TIMEOUT = 10
RETRY_COUNTDOWNS = [10, 60, 300]


class WebhookUrlError(ValueError):
	pass


def validate_webhook_url(url: str) -> str:
	"""Same rules as the agent's call_api tool: http(s), public addresses only."""
	url = (url or "").strip()
	try:
		scheme, host = _parse_target(url)
	except ValueError as e:
		raise WebhookUrlError(str(e))
	if scheme not in ("http", "https") or not host:
		raise WebhookUrlError("URL must start with http:// or https://")
	if not _is_public_host(host):
		raise WebhookUrlError(f"{host} is not a public address")
	return url


def sign(secret: str, body: bytes) -> str:
	return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def build_event(project_id, event: str, data: dict) -> dict:
	return {
		"id": str(uuid.uuid4()),
		"event": event,
		"created_at": timezone.now().isoformat(),
		"project_id": str(project_id),
		"data": data,
	}


def post_event(hook: ProjectWebhook, payload: dict) -> int:
	"""POST one signed event; returns the HTTP status. Raises WebhookUrlError or requests.RequestException."""
	validate_webhook_url(hook.url)  # re-checked per delivery: DNS may have changed since save
	body = json.dumps(payload, separators=(",", ":")).encode()
	resp = requests.post(
		hook.url,
		data=body,
		timeout=TIMEOUT,
		allow_redirects=False,
		headers={
			"Content-Type": "application/json",
			"X-AMP-Event": payload["event"],
			"X-AMP-Delivery": payload["id"],
			"X-AMP-Signature": sign(hook.secret, body),
		},
	)
	return resp.status_code


def emit_event(project, event: str, data: dict) -> None:
	hook = ProjectWebhook.objects.filter(project=project, is_active=True).exclude(url="").first()
	if hook:
		deliver_webhook.delay(str(hook.id), build_event(project.id, event, data))


@shared_task(bind=True, max_retries=len(RETRY_COUNTDOWNS))
def deliver_webhook(self, hook_id: str, payload: dict) -> Optional[int]:
	hook = ProjectWebhook.objects.filter(id=hook_id).first()
	if not hook or not hook.is_active or not hook.url:
		return None
	try:
		status = post_event(hook, payload)
	except WebhookUrlError as e:
		logger.warning(f"Webhook {payload['event']} {payload['id']} skipped: {e}")
		return None
	except requests.RequestException as e:
		logger.warning(f"Webhook {payload['event']} {payload['id']} failed: {type(e).__name__}")
		status = None
	if status is not None and status < 500:
		if status >= 400:
			logger.warning(f"Webhook {payload['event']} {payload['id']} rejected with HTTP {status}")
		return status
	if self.request.retries < self.max_retries:
		raise self.retry(countdown=RETRY_COUNTDOWNS[self.request.retries])
	logger.error(f"Webhook {payload['event']} {payload['id']} failed after retries (last status {status})")
	return status
