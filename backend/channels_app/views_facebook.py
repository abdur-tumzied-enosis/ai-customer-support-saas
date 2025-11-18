import hashlib
import hmac
import json
from typing import Any, Dict

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .service.facebook_messing import handle_facebook_message, handle_facebook_webhook_verification

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


@require_http_methods(["GET", "POST"])
@csrf_exempt
def facebook_webhook(request: HttpRequest) -> HttpResponse:
	logger.info(f"Facebook webhook received: {request.method} {request.path}")

	if request.method == "GET":
		return handle_facebook_webhook_verification(request)

	# POST
	logger.info(f"Facebook webhook POST received: {request.body}")
	signature = request.headers.get("X-Hub-Signature-256", "")
	app_secret = settings.META_APP_SECRET
	if not _verify_signature(signature, request.body, app_secret):
		return HttpResponse(status=403)

	try:
		payload: Dict[str, Any] = json.loads(request.body.decode("utf-8"))
	except Exception:
		return HttpResponse(status=400)

	# Handle message events
	logger.info(f"Facebook webhook payload: {json.dumps(payload, indent=4)}")
	handle_facebook_message(payload)

	return JsonResponse({"status": "ok"})
