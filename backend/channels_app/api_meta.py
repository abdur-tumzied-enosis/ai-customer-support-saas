from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from knowledge.models import KnowledgeBase
from . import meta_connect
from .api import _get_project
from .meta_status import GraphError


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
			return _error("no_default_knowledge_base",
				"Set a default knowledge base for this project before connecting Meta",
				status.HTTP_400_BAD_REQUEST)

		try:
			long_token = meta_connect.exchange_long_lived_token(access_token)
			meta_user_id = meta_connect.get_meta_user_id(long_token)
			pages = meta_connect.list_manageable_pages(long_token)
		except GraphError as exc:
			return _error("meta_error", str(exc), status.HTTP_502_BAD_GATEWAY)

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

		try:
			rows = meta_connect.connect_page(project, page, kb, session["meta_user_id"])
		except meta_connect.PageConflict:
			return _error("page_connected_elsewhere",
				"This Page is already connected to another project", status.HTTP_409_CONFLICT)
		except GraphError as exc:
			return _error("meta_error", str(exc), status.HTTP_502_BAD_GATEWAY)

		# Consume only after success so a failed connect can be retried with the same setup_id
		meta_connect.consume_setup_session(setup_id)
		return Response({"success": True, "data": {
			"connected": True, "channels": [_channel_dict(r) for r in rows]}})
