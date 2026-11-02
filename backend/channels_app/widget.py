"""
Website chat widget (docs/widget-architecture.md): dashboard CRUD, the embed page and the visitor API.

A host page loads /widget/loader.js, which adds an iframe of /embed/<public key>/. The browser only renders that
iframe on the widget's allowed origins (CSP frame-ancestors). Inside it, visitors get a signed token from
/api/widget/<key>/session and talk through /api/widget/messages. Each visitor is a
CustomerContact(platform="widget") with one conversation, answered by the normal reply pipeline (channels_app.reply).
"""
import hashlib
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.http import FileResponse, Http404, HttpResponseNotFound
from django.shortcuts import render
from django.utils.dateparse import parse_datetime
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from knowledge.models import KnowledgeBase
from .api import _error, _get_project, _iso, _verify_project_access
from .media import PHOTO_ERROR, has_photo, is_own_upload, presign_chat_photo, uploaded_photo_ok
from .media_store import store_message_media
from .models import CustomerContact, Widget, normalize_origin
from .reply import schedule_reply
from .service.conversation_service import ensure_contact_conversation
from .service.message_service import create_inbound_message

TOKEN_SALT = "channels_app.widget"
TOKEN_MAX_AGE = 30 * 24 * 3600
MAX_MESSAGE_CHARS = 2000
MAX_ORIGINS = 20
MESSAGES_PAGE = 50
ASSETS = Path(__file__).parent / "widget_assets"
ASSET_TYPES = {"loader.js": "text/javascript", "app.js": "text/javascript", "app.css": "text/css"}
# Also the whitelist of what the public embed page gets: nothing else about the widget leaves the server
APPEARANCE_DEFAULTS = {
	"title": "Chat with us",
	"welcome_message": "Hi! How can we help you today?",
	"primary_color": "#2563eb",
	"position": "right",
	"privacy_url": "",  # the business's own policy (it is the controller); "" → ours
	"show_branding": True,
}
APPEARANCE_LIMITS = {"title": 60, "welcome_message": 500, "privacy_url": 500}
validate_url = URLValidator(schemes=["http", "https"])


# --- Dashboard (session auth, project members) ---------------------------------------------------------------

def _public_base(request) -> str:
	return settings.PUBLIC_BASE_URL.rstrip("/") if settings.PUBLIC_BASE_URL else request.build_absolute_uri("/").rstrip("/")


def _widget_dict(widget: Widget, request) -> dict:
	return {
		"id": str(widget.id),
		"name": widget.name,
		"public_key": widget.public_key,
		"knowledge_base": {"id": str(widget.knowledge_base.id), "name": widget.knowledge_base.name},
		"is_active": widget.is_active,
		"image_search_enabled": widget.image_search_enabled,
		"allowed_origins": widget.allowed_origins,
		"appearance": {**APPEARANCE_DEFAULTS, **widget.appearance},
		"snippet": f'<script src="{_public_base(request)}/widget/loader.js" data-widget-key="{widget.public_key}" async></script>',
		"created_at": _iso(widget.created_at),
		"updated_at": _iso(widget.updated_at),
	}


def _clean_appearance(data) -> dict:
	if not isinstance(data, dict):
		raise ValidationError({"appearance": "Must be an object"})
	clean = {}
	for field, value in data.items():
		if field not in APPEARANCE_DEFAULTS:
			continue
		if field == "show_branding":
			if not isinstance(value, bool):
				raise ValidationError({"appearance": "show_branding must be true or false"})
			clean[field] = value
			continue
		value = str(value or "").strip()
		if field == "privacy_url" and value:
			try:
				validate_url(value)
			except DjangoValidationError:
				raise ValidationError({"appearance": "privacy_url must be an http(s) URL"})
		if field == "primary_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
			raise ValidationError({"appearance": "primary_color must look like #2563eb"})
		if field == "position" and value not in ("left", "right"):
			raise ValidationError({"appearance": "position must be left or right"})
		if len(value) > APPEARANCE_LIMITS.get(field, 50):
			raise ValidationError({"appearance": f"{field} is longer than {APPEARANCE_LIMITS[field]} characters"})
		clean[field] = value
	return clean


def _apply(widget: Widget, data, project) -> None:
	"""Validate and copy editable fields from request data onto widget (not saved)."""
	if "name" in data:
		widget.name = str(data["name"] or "").strip()[:255]
	if not widget.name:
		raise ValidationError({"name": "This field is required"})
	if "knowledge_base_id" in data:
		try:
			widget.knowledge_base = KnowledgeBase.objects.get(id=data["knowledge_base_id"], project=project)
		except (KnowledgeBase.DoesNotExist, DjangoValidationError, ValueError):  # unknown, other project's or malformed
			raise ValidationError({"knowledge_base_id": "Knowledge base not found"})
	if widget.knowledge_base_id is None:
		widget.knowledge_base = KnowledgeBase.get_default(project)
	if "is_active" in data:
		widget.is_active = bool(data["is_active"])
	if "image_search_enabled" in data:
		widget.image_search_enabled = bool(data["image_search_enabled"])
	if "allowed_origins" in data:
		origins = data["allowed_origins"]
		if not isinstance(origins, list) or len(origins) > MAX_ORIGINS:
			raise ValidationError({"allowed_origins": f"Must be a list of at most {MAX_ORIGINS} origins"})
		try:
			widget.allowed_origins = list(dict.fromkeys(normalize_origin(str(o)) for o in origins if str(o).strip()))
		except DjangoValidationError as exc:
			raise ValidationError({"allowed_origins": exc.messages[0]})
	if "appearance" in data:
		widget.appearance = {**widget.appearance, **_clean_appearance(data["appearance"])}


def _get_widget(request, widget_id) -> Widget:
	widget = Widget.objects.filter(id=widget_id).select_related("project", "knowledge_base").first()
	if not widget:
		raise Http404
	_verify_project_access(request.user, widget.project)
	return widget


class WidgetListCreateView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project = _get_project(request.user, request.query_params.get("project_slug", ""))
		widgets = Widget.objects.filter(project=project).select_related("knowledge_base").order_by("-created_at")
		return Response({"success": True, "data": {"widgets": [_widget_dict(w, request) for w in widgets]}})

	def post(self, request):
		project = _get_project(request.user, request.data.get("project_slug", ""))
		widget = Widget(project=project)
		_apply(widget, request.data, project)
		widget.save()
		return Response({"success": True, "data": {"widget": _widget_dict(widget, request)}}, status=status.HTTP_201_CREATED)


class WidgetDetailView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request, widget_id):
		return Response({"success": True, "data": {"widget": _widget_dict(_get_widget(request, widget_id), request)}})

	def patch(self, request, widget_id):
		widget = _get_widget(request, widget_id)
		_apply(widget, request.data, widget.project)
		widget.save()
		return Response({"success": True, "data": {"widget": _widget_dict(widget, request)}})

	def delete(self, request, widget_id):
		_get_widget(request, widget_id).delete()  # visitor conversations stay in the inbox
		return Response({"success": True, "data": {}})  # JSON body: the dashboard client always parses one


# --- Public: assets and the embed page -----------------------------------------------------------------------

def asset(request, name):
	if name not in ASSET_TYPES:
		return HttpResponseNotFound()  # not raise: core.middleware turns a raised Http404 into a 500
	response = FileResponse(open(ASSETS / name, "rb"), content_type=ASSET_TYPES[name])
	response["Cache-Control"] = "public, max-age=300"  # short: updates reach every site without reinstalling
	response["X-Content-Type-Options"] = "nosniff"
	return response


@xframe_options_exempt  # framing is governed by the CSP frame-ancestors below instead
def embed(request, key):
	widget = Widget.objects.filter(public_key=key, is_active=True).first()
	if not widget:
		return HttpResponseNotFound()
	config = {
		"key": widget.public_key,
		**APPEARANCE_DEFAULTS,
		**widget.appearance,
		"site_url": settings.SITE_URL,  # "Powered by" and the fallback privacy link
		"product_name": settings.PRODUCT_NAME,
		"image_upload": _image_upload(widget),
	}
	response = render(request, "widget/embed.html", {"config": config})
	ancestors = " ".join(widget.allowed_origins) or "'none'"
	# Visitor photos are PUT straight to R2 (presigned, path-style URLs on the account endpoint)
	connect = f"'self' {settings.R2_ENDPOINT_URL}".strip()
	response["Content-Security-Policy"] = (
		f"default-src 'none'; script-src 'self'; style-src 'self'; connect-src {connect}; img-src 'self' data:; "
		f"base-uri 'none'; form-action 'none'; frame-ancestors {ancestors}"
	)
	response["Cache-Control"] = "no-store"  # config and allowed origins apply on the next page load
	return response


# --- Public: visitor API (signed bearer token, no cookies) ---------------------------------------------------

def issue_token(widget: Widget, visitor: str) -> str:
	# The key is inside, so rotating it (or disabling the widget) revokes every visitor token
	return signing.dumps({"w": str(widget.id), "k": widget.public_key, "v": visitor}, salt=TOKEN_SALT)


class WidgetTokenAuthentication(BaseAuthentication):
	"""request.auth = (widget, visitor id). Invalid, expired or revoked tokens → 401."""

	def authenticate(self, request):
		header = request.headers.get("Authorization", "")
		if not header.startswith("Bearer "):
			return None
		try:
			data = signing.loads(header[7:], salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
		except signing.BadSignature:  # includes SignatureExpired
			raise AuthenticationFailed("Invalid or expired session")
		widget = Widget.objects.filter(id=data["w"], public_key=data["k"], is_active=True).select_related(
			"project", "knowledge_base"
		).first()
		if not widget:
			raise AuthenticationFailed("Invalid or expired session")
		return AnonymousUser(), (widget, data["v"])

	def authenticate_header(self, request):
		return "Bearer"  # makes DRF answer 401, not 403


class HasWidgetToken(BasePermission):
	def has_permission(self, request, view):
		return request.auth is not None


class _Throttle(SimpleRateThrottle):
	def get_rate(self):
		return settings.WIDGET_THROTTLE_RATES[self.scope]


class SessionThrottle(_Throttle):
	scope = "session"  # new visitors per IP

	def get_cache_key(self, request, view):
		return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class VisitorThrottle(_Throttle):
	scope = "visitor_messages"

	def get_cache_key(self, request, view):
		return self.cache_format % {"scope": self.scope, "ident": request.auth[1]}


class WidgetThrottle(_Throttle):
	scope = "widget_messages"  # all visitors of one widget: caps LLM spend when tokens are farmed

	def get_cache_key(self, request, view):
		return self.cache_format % {"scope": self.scope, "ident": request.auth[0].id}


class PollThrottle(VisitorThrottle):
	scope = "visitor_polls"


def _visitor_message(msg) -> dict:
	# Explicit fields: meta holds traces and API call logs that must not reach the browser
	return {
		"id": str(msg.id),
		"sender": msg.sender,  # customer / bot / staff
		"text": msg.text,
		"client_id": (msg.meta or {}).get("client_id") if msg.direction == "in" else None,
		"has_photo": has_photo(msg),
		"created_at": _iso(msg.created_at),
	}


def _external_id(widget: Widget, visitor: str) -> str:
	return f"{widget.id}:{visitor}"


def _image_upload(widget: Widget) -> bool:
	return bool(widget.project.image_search_on and widget.image_search_enabled)


class WidgetSessionView(APIView):
	authentication_classes = []
	permission_classes = [AllowAny]
	throttle_classes = [SessionThrottle]

	def post(self, request, key):
		widget = Widget.objects.filter(public_key=key, is_active=True).first()
		if not widget:
			return _error("not_found", "Widget not found", status.HTTP_404_NOT_FOUND)
		# Nothing is stored until the visitor's first message
		return Response({"success": True, "data": {
			"token": issue_token(widget, uuid.uuid4().hex),
			"image_upload": _image_upload(widget),
		}}, status=status.HTTP_201_CREATED)


class WidgetMessagesView(APIView):
	authentication_classes = [WidgetTokenAuthentication]
	permission_classes = [HasWidgetToken]

	def get_throttles(self):
		return [VisitorThrottle(), WidgetThrottle()] if self.request.method == "POST" else [PollThrottle()]

	def get(self, request):
		"""Visitor's messages, newest MESSAGES_PAGE, or only those after ?after=<created_at>. Bot and staff alike."""
		widget, visitor = request.auth
		contact = CustomerContact.objects.filter(
			project=widget.project, platform="widget", external_id=_external_id(widget, visitor)
		).select_related("conversation").first()
		conversation = contact and contact.conversation
		if not conversation:
			return Response({"success": True, "data": {"messages": [], "pending": False}})
		messages = conversation.messages.all()
		try:
			after = parse_datetime(request.query_params.get("after", ""))
		except ValueError:
			after = None
		if after:
			messages = messages.filter(created_at__gt=after)
		page = list(messages.order_by("-created_at")[:MESSAGES_PAGE])[::-1]
		pending = conversation.messages.filter(direction="in", status="pending").exists()
		return Response({"success": True, "data": {"messages": [_visitor_message(m) for m in page], "pending": pending}})

	def post(self, request):
		widget, visitor = request.auth
		text = str(request.data.get("text") or "").strip()
		if not text:
			return _error("empty_message", "Message is empty", status.HTTP_400_BAD_REQUEST)
		if len(text) > MAX_MESSAGE_CHARS:
			return _error("message_too_long", f"Messages are limited to {MAX_MESSAGE_CHARS} characters", status.HTTP_400_BAD_REQUEST)
		client_id = str(request.data.get("client_id") or "")[:64]

		label = f"Visitor {visitor[:6]}"
		contact, _ = CustomerContact.objects.get_or_create(
			project=widget.project, platform="widget", external_id=_external_id(widget, visitor),
			defaults={"display_name": label},
		)
		conversation = ensure_contact_conversation(contact, widget.knowledge_base, name=label)
		# A retried send (lost response) must not ask the bot twice
		if client_id:
			existing = conversation.messages.filter(direction="in", meta__client_id=client_id).first()
			if existing:
				return Response({"success": True, "data": {"message": _visitor_message(existing)}})
		msg = create_inbound_message(
			project=widget.project, contact=contact, text=text, conversation=conversation,
			meta={"client_id": client_id} if client_id else {}, status="pending",
		)
		schedule_reply(msg)
		return Response({"success": True, "data": {"message": _visitor_message(msg)}}, status=status.HTTP_201_CREATED)


def _photo_prefix(widget: Widget, visitor: str) -> str:
	"""Where this visitor's photo uploads go: confirm only accepts keys under it."""
	return f"tmp/widget/{widget.id}/{hashlib.sha256(visitor.encode()).hexdigest()[:16]}/"


class WidgetPhotoPresignView(WidgetMessagesView):
	"""Step 1 of a visitor photo: a presigned PUT straight to R2 (the photo never passes through Django)."""
	http_method_names = ["post", "options"]

	def post(self, request):
		widget, visitor = request.auth
		if not _image_upload(widget):
			return _error("photo_disabled", "Photo upload is not available", status.HTTP_403_FORBIDDEN)
		upload = presign_chat_photo(_photo_prefix(widget, visitor), request.data.get("content_type"), request.data.get("size"))
		if not upload:
			return _error("invalid_image", PHOTO_ERROR, status.HTTP_400_BAD_REQUEST)
		return Response({"success": True, "data": upload})


class WidgetPhotoView(WidgetMessagesView):
	"""Step 2: the visitor's photo (by its uploaded key) plus optional text. Same auth, throttles and client_id dedupe
	as the text send; the photo is moved into R2 storage and resized before the reply runs."""
	http_method_names = ["post", "options"]

	def post(self, request):
		widget, visitor = request.auth
		if not _image_upload(widget):
			return _error("photo_disabled", "Photo upload is not available", status.HTTP_403_FORBIDDEN)
		text = str(request.data.get("text") or "").strip()
		if len(text) > MAX_MESSAGE_CHARS:
			return _error("message_too_long", f"Messages are limited to {MAX_MESSAGE_CHARS} characters", status.HTTP_400_BAD_REQUEST)
		key = request.data.get("key")
		if not is_own_upload(_photo_prefix(widget, visitor), key):
			return _error("invalid_upload", "Upload the photo first", status.HTTP_400_BAD_REQUEST)
		client_id = str(request.data.get("client_id") or "")[:64]
		label = f"Visitor {visitor[:6]}"
		contact, _ = CustomerContact.objects.get_or_create(
			project=widget.project, platform="widget", external_id=_external_id(widget, visitor),
			defaults={"display_name": label},
		)
		conversation = ensure_contact_conversation(contact, widget.knowledge_base, name=label)
		if client_id:
			existing = conversation.messages.filter(direction="in", meta__client_id=client_id).first()
			if existing:
				return Response({"success": True, "data": {"message": _visitor_message(existing)}})
		# After the dedupe: on a retry the worker may already have moved the upload out of tmp/
		if not uploaded_photo_ok(key):
			return _error("invalid_image", PHOTO_ERROR, status.HTTP_400_BAD_REQUEST)
		msg = create_inbound_message(
			project=widget.project, contact=contact, text=text, conversation=conversation,
			has_attachments=True, attachment_info=[{"type": "image", "path": key}],
			meta={"photo_reply": True, **({"client_id": client_id} if client_id else {})}, status="pending",
		)
		store_message_media.delay(str(msg.id), True)  # moves + resizes the photo, then schedules the reply
		return Response({"success": True, "data": {"message": _visitor_message(msg)}}, status=status.HTTP_201_CREATED)
