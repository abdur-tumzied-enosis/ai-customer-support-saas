import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from knowledge.models import KnowledgeBase
from . import meta_connect
from .api import _get_project
from .meta_status import GraphError
from .models import ChannelCredential

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int):
	return Response({"success": False, "error": {"code": code, "message": message}}, status=http_status)


def _channel_dict(row) -> dict:
	return {
		"id": str(row.id),
		"name": row.name,
		"channel": row.channel,
		"is_active": row.is_active,
		"is_configured": row.is_configured(),
	}


class MetaTokenView(APIView):
	"""Exchange a short-lived Meta login token and connect the only Page, or open a Page picker."""
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project_slug = request.data.get("project_slug")
		access_token = request.data.get("access_token")
		if not project_slug or not access_token:
			return _error("missing_fields", "project_slug and access_token are required", status.HTTP_400_BAD_REQUEST)

		project = _get_project(request.user, project_slug)
		kb = KnowledgeBase.objects.filter(project=project, is_default=True).first()
		if kb is None:
			logger.warning("Meta token: project=%s has no default knowledge base", project.id)
			return _error("no_default_knowledge_base",
				"Set a default knowledge base for this project before connecting Meta",
				status.HTTP_400_BAD_REQUEST)

		logger.info("Meta token: exchanging login token with app_id=%s project=%s", settings.META_APP_ID, project.id)
		try:
			long_token = meta_connect.exchange_long_lived_token(access_token)
			meta_user_id = meta_connect.get_meta_user_id(long_token)
			pages = meta_connect.list_manageable_pages(long_token)
		except GraphError as exc:
			logger.warning("Meta token: stopped at Graph call, project=%s, reason=%s", project.id, exc)
			return _error("meta_error", str(exc), status.HTTP_502_BAD_GATEWAY)

		logger.info("Meta token: user=%s manageable_pages=%d", meta_user_id, len(pages))
		if not pages:
			return _error("no_pages", "No Facebook Pages you manage were found", status.HTTP_400_BAD_REQUEST)

		if len(pages) == 1:
			try:
				rows = meta_connect.connect_page(project, pages[0], kb, meta_user_id)
			except meta_connect.PageConflict:
				return _error("page_connected_elsewhere",
					"This Page is already connected to another project", status.HTTP_409_CONFLICT)
			except GraphError as exc:
				return _error("meta_error", str(exc), status.HTTP_502_BAD_GATEWAY)
			return Response({"success": True, "data": {
				"connected": True, "channels": [_channel_dict(r) for r in rows]}})

		setup_id = meta_connect.create_setup_session(str(request.user.id), str(project.id),
			meta_user_id, long_token, pages)
		return Response({"success": True, "data": {
			"connected": False,
			"setup_id": setup_id,
			"pages": [{"id": p["id"], "name": p["name"], "instagram_username": p["instagram_username"]}
				for p in pages],
		}})


class MetaConnectView(APIView):
	"""Connect the Page the customer picked from a setup session created by MetaTokenView."""
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project_slug = request.data.get("project_slug")
		setup_id = request.data.get("setup_id")
		page_id = request.data.get("page_id")
		if not project_slug or not setup_id or not page_id:
			return _error("missing_fields", "project_slug, setup_id and page_id are required",
				status.HTTP_400_BAD_REQUEST)

		project = _get_project(request.user, project_slug)
		session = meta_connect.read_setup_session(setup_id)
		if (session is None or session["user_id"] != str(request.user.id)
				or session["project_id"] != str(project.id)):
			return _error("invalid_setup", "This Meta setup has expired. Start the connection again.",
				status.HTTP_400_BAD_REQUEST)

		page = next((p for p in session["pages"] if p["id"] == page_id), None)
		if page is None:
			return _error("invalid_page", "That Page is not part of this setup", status.HTTP_400_BAD_REQUEST)

		kb = KnowledgeBase.objects.filter(project=project, is_default=True).first()
		if kb is None:
			return _error("no_default_knowledge_base",
				"Set a default knowledge base for this project before connecting Meta",
				status.HTTP_400_BAD_REQUEST)

		# Claim before connecting so a parallel submit of the same setup cannot create duplicate rows
		if not meta_connect.claim_setup_session(setup_id):
			return _error("invalid_setup", "This Meta setup is already being connected. Start the connection again.",
				status.HTTP_400_BAD_REQUEST)
		try:
			rows = meta_connect.connect_page(project, page, kb, session["meta_user_id"])
		except meta_connect.PageConflict:
			meta_connect.release_setup_session(setup_id)
			logger.warning("Meta connect: page=%s already active in another project", page_id)
			return _error("page_connected_elsewhere",
				"This Page is already connected to another project", status.HTTP_409_CONFLICT)
		except GraphError as exc:
			meta_connect.release_setup_session(setup_id)
			logger.warning("Meta connect: page=%s stopped at Graph call, reason=%s", page_id, exc)
			return _error("meta_error", str(exc), status.HTTP_502_BAD_GATEWAY)
		except Exception:
			meta_connect.release_setup_session(setup_id)
			raise

		# Consume only after success; a failed connect keeps the session for a retry
		meta_connect.consume_setup_session(setup_id)
		return Response({"success": True, "data": {
			"connected": True, "channels": [_channel_dict(r) for r in rows]}})


class MetaDeauthorizeView(APIView):
	"""Meta calls this when a customer removes the app; the signed_request is the only credential."""
	permission_classes = [AllowAny]
	authentication_classes = []

	def post(self, request):
		payload = meta_connect.verify_signed_request(request.data.get("signed_request", ""), settings.META_APP_SECRET)
		user_id = str((payload or {}).get("user_id") or "")
		if not user_id:
			return _error("invalid_signature", "Invalid signed_request", status.HTTP_400_BAD_REQUEST)

		rows = ChannelCredential.objects.filter(is_active=True, credentials__connected_via="oauth",
			credentials__meta_user_id=user_id)
		for row in rows:
			row.is_active = False
			row.credentials["page_access_token"] = ""
			row.save(update_fields=["is_active", "credentials"])
		return Response({"success": True})
