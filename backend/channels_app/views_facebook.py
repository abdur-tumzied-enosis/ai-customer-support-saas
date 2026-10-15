import hashlib
import hmac
import json
from typing import Any, Dict

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import ChannelCredential
from .service.facebook_messing import handle_facebook_message, handle_facebook_webhook_verification
from .service.whatsapp_messaging import handle_whatsapp_message

import logging
logger = logging.getLogger(__name__)


def _verify_signature(signature_header: str, payload: bytes, app_secret: str) -> bool:
	try:
		algo, signature = signature_header.split("=")
		if algo != "sha256":
			return False
		expected = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
		return hmac.compare_digest(expected, signature)
	except Exception:
		return False


def _app_secrets(payload: Dict[str, Any]) -> list:
	"""The platform Meta app's secret, plus the secret of any WhatsApp number on its own Meta app.
	The payload is untrusted until one of these signatures matches."""
	secrets = [settings.META_APP_SECRET]
	if payload.get("object") == "whatsapp_business_account":
		ids = {
			change.get("value", {}).get("metadata", {}).get("phone_number_id")
			for entry in payload.get("entry", []) for change in entry.get("changes", [])
		}
		for cred in ChannelCredential.objects.filter(channel="whatsapp", is_active=True, credentials__phone_number_id__in=ids - {None}):
			secrets.append(cred.credentials.get("app_secret"))
	return [s for s in secrets if s]  # an empty key would let anyone sign


@require_http_methods(["GET", "POST"])
@csrf_exempt
def facebook_webhook(request: HttpRequest) -> HttpResponse:
	logger.info(f"Facebook webhook received: {request.method} {request.path}")

	if request.method == "GET":
		return handle_facebook_webhook_verification(request)

	# POST
	logger.info(f"Facebook webhook POST received: {request.body}")
	try:
		payload: Dict[str, Any] = json.loads(request.body.decode("utf-8"))
	except Exception:
		return HttpResponse(status=400)

	signature = request.headers.get("X-Hub-Signature-256", "")
	if not any(_verify_signature(signature, request.body, secret) for secret in _app_secrets(payload)):
		return HttpResponse(status=403)

	# Handle message events
	logger.info(f"Facebook webhook payload: {json.dumps(payload, indent=4)}")
	if payload.get("object") == "whatsapp_business_account":
		handle_whatsapp_message(payload)
	else:
		handle_facebook_message(payload)

	return JsonResponse({"status": "ok"})
