import hashlib
import hmac
import json
from typing import Any, Dict

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from knowledge.models import KnowledgeBase

from .models import ChannelCredential, CustomerContact, Message
from accounts.models import Project
from .tasks import handle_incoming_message


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
	if request.method == "GET":
		mode = request.GET.get("hub.mode")
		verify_token = request.GET.get("hub.verify_token")
		challenge = request.GET.get("hub.challenge")
		if mode == "subscribe" and verify_token == settings.FB_VERIFY_TOKEN:
			return HttpResponse(challenge or "")
		return HttpResponse(status=403)

	# POST
	signature = request.headers.get("X-Hub-Signature-256", "")
	app_secret = settings.META_APP_SECRET
	if not _verify_signature(signature, request.body, app_secret):
		return HttpResponse(status=403)

	try:
		payload: Dict[str, Any] = json.loads(request.body.decode("utf-8"))
	except Exception:
		return HttpResponse(status=400)

	# Handle message events
	entry_list = payload.get("entry", [])
	for entry in entry_list:
		messaging = entry.get("messaging", [])
		for evt in messaging:
			sender_id = evt.get("sender", {}).get("id")
			recipient_id = evt.get("recipient", {}).get("id")
			message = evt.get("message", {})
			text = message.get("text", "")
			if not (sender_id and recipient_id and text):
				continue

			# Resolve business by page_id (recipient_id)
			try:
				cred = ChannelCredential.objects.get(channel="facebook", page_id=recipient_id)
				project: Project = cred.project
				knowledge_base: KnowledgeBase = cred.knowledge_base
			except ChannelCredential.DoesNotExist:
				continue

			contact, _ = CustomerContact.objects.get_or_create(
				project=project, platform="facebook", external_id=sender_id
			)
			msg = Message.objects.create(
				project=project,
				contact=contact,
				direction="in",
				text=text,
				meta=evt,
			)
			# Enqueue async handler (generate reply + send)
			handle_incoming_message.delay(msg.id, knowledge_base.id)

	return JsonResponse({"status": "ok"})


