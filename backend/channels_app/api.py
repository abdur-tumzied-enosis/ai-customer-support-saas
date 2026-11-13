from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError
from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase
from .media import PHOTO_ERROR, UPLOAD_PREFIXES, MediaError, has_photo, is_own_upload, photo_keys, presign_chat_photo, uploaded_photo_ok
from .media_store import store_message_media, store_outbound_photo
from agents.image_search import NotAnImage
from .models import ChannelCredential, Conversation, CustomerContact, Message, Platform
from .service.conversation_service import create_conversation
from .tasks import photo_note_text
from .service.message_service import create_ui_message
from datetime import timedelta
import requests
from django.conf import settings
from django.db import transaction
from django.db.models import OuterRef, Subquery, Sum
from billing.models import ACTIONS, CreditTransaction
from core.storage import delete_keys, presign_get, thumb_key
from django.http import HttpResponseRedirect
from django.utils import timezone
from . import memory
from .handoff import conversation_event_data, end_handoff, note_staff_activity, start_handoff
from .models import ProjectWebhook
from .reply import answered_marker, deliver, schedule_memory_update, schedule_reply
from .service.message_service import create_outbound_message
from .webhooks import WebhookUrlError, build_event, emit_event, post_event, validate_webhook_url


class PlatformListView(APIView):
	"""List all available messaging platforms with their credential schemas."""
	permission_classes = [IsAuthenticated]

	def get(self, request):
		"""Get all active platforms with credential field schemas."""
		platforms = Platform.objects.filter(is_active=True).order_by('display_name')
		
		platforms_data = []
		for platform in platforms:
			platforms_data.append({
				"id": str(platform.id),
				"name": platform.name,
				"display_name": platform.display_name,
				"description": platform.description,
				"icon": platform.icon,
				"is_active": platform.is_active,
				"webhook_path": platform.webhook_path,
				"webhook_url": (
					settings.PUBLIC_BASE_URL.rstrip("/") + platform.webhook_path if settings.PUBLIC_BASE_URL
					else request.build_absolute_uri(platform.webhook_path)
				),
				"api_base_url": platform.api_base_url,
				"documentation_url": platform.documentation_url,
				"credential_schema": platform.credential_schema,
			})

		return Response({
			"success": True,
			"data": {
				"platforms": platforms_data,
				"count": len(platforms_data)
			}
		}, status=status.HTTP_200_OK)


class FacebookConfigView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project_slug = request.data.get("project_slug")
		if not project_slug:
			raise ValidationError({"project_slug": "This field is required"})

		try:
			project = Project.objects.get(slug=project_slug)
		except Project.DoesNotExist:
			return Response(
				{
					"success": False,
					"error": {
						"code": "not_found",
						"message": "Project not found"
					}
				},
				status=status.HTTP_404_NOT_FOUND
			)

		# Verify user has access to this project
		try:
			ProjectUser.objects.get(project=project, user=request.user)
		except ProjectUser.DoesNotExist:
			return Response(
				{
					"success": False,
					"error": {
						"code": "access_denied",
						"message": "You do not have access to this project"
					}
				},
				status=status.HTTP_403_FORBIDDEN
			)

		cred, _ = ChannelCredential.objects.get_or_create(project=project, channel="facebook")
		cred.page_id = request.data.get("page_id", cred.page_id)
		cred.page_access_token = request.data.get("page_access_token", cred.page_access_token)
		cred.meta_app_id = request.data.get("meta_app_id", cred.meta_app_id)
		cred.meta_app_secret = request.data.get("meta_app_secret", cred.meta_app_secret)
		cred.verify_token = request.data.get("verify_token", cred.verify_token)
		cred.save()

		return Response({
			"success": True,
			"message": "Facebook channel configured successfully",
			"data": {
				"status": "saved",
				"project": project.slug
			}
		}, status=status.HTTP_200_OK)


class ChannelCredentialsListView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project_slug = request.query_params.get("project_slug")
		if not project_slug:
			raise ValidationError({"project_slug": "This field is required"})

		try:
			project = Project.objects.get(slug=project_slug)
		except Project.DoesNotExist:
			return Response(
				{
					"success": False,
					"error": {
						"code": "not_found",
						"message": "Project not found"
					}
				},
				status=status.HTTP_404_NOT_FOUND
			)

		# Verify user has access to this project
		try:
			ProjectUser.objects.get(project=project, user=request.user)
		except ProjectUser.DoesNotExist:
			return Response(
				{
					"success": False,
					"error": {
						"code": "access_denied",
						"message": "You do not have access to this project"
					}
				},
				status=status.HTTP_403_FORBIDDEN
			)

		# Get all channel credentials for this project
		credentials = ChannelCredential.objects.filter(project=project)
		
		channels_data = []
		for cred in credentials:
			# Determine if channel is configured (has essential fields)
			is_configured = False
			if cred.channel == "facebook":
				is_configured = bool(cred.page_id and cred.page_access_token)
			
			channels_data.append({
				"id": str(cred.id),
				"channel": cred.channel,
				"is_configured": is_configured,
			})

		return Response({
			"success": True,
			"data": {
				"project": project.slug,
				"channels": channels_data
			}
		}, status=status.HTTP_200_OK)


def _verify_project_access(user, project: Project) -> None:
	"""Helper to verify user has access to project."""
	try:
		ProjectUser.objects.get(project=project, user=user)
	except ProjectUser.DoesNotExist:
		raise ValidationError({"error": "You do not have access to this project"})


def _get_project(user, project_slug: str) -> Project:
	"""Helper to get project and verify access."""
	project = Project.objects.filter(slug=project_slug).first()
	if not project:
		raise ValidationError({"error": "Project not found"})
	_verify_project_access(user, project)
	return project


def _iso(dt):
	return dt.isoformat() if dt else None


def _conversation_dict(conv: Conversation) -> dict:
	return {
		"id": str(conv.id),
		"name": conv.name or "Unnamed Conversation",
		"knowledge_base_id": str(conv.knowledge_base.id),
		"knowledge_base_name": conv.knowledge_base.name,
		"channel": conv.channel,
		"contact_display_name": conv.contact.display_name if conv.contact else "",
		"handoff_at": _iso(conv.handoff_at),
		"handoff_reason": conv.handoff_reason,
		"is_paused": conv.is_paused(),
		"resumes_at": _iso(conv.resumes_at()),
		"created_at": conv.created_at.isoformat(),
		"updated_at": conv.updated_at.isoformat(),
	}


def _credits_used(value) -> str:
	return str(-(value or 0))


def _message_dict(msg: Message, credits=()) -> dict:
	return {
		"id": str(msg.id),
		"direction": msg.direction,
		"sender": msg.sender,
		"status": msg.status,
		"text": msg.text,
		"created_at": msg.created_at.isoformat(),
		"agent_reply_id": str(msg.agent_reply_id) if msg.agent_reply_id else None,
		"has_photo": has_photo(msg),
		"photo_urls": [f"/api/channels/messages/{msg.id}/photo?index={i}" for i, _ in enumerate(photo_keys(msg))],
		"photo_matches": photo_note_text(msg.meta) if msg.direction == "in" else "",
		"credits": list(credits),
	}


def _message_credits(conversation) -> dict:
	"""{message id: [credit entries]} from one query: replies attach to the reply message, photo searches to the customer message."""
	out = {}
	rows = CreditTransaction.objects.filter(conversation=conversation, kind="charge", action__in=("reply", "photo_search"))
	for t in rows.order_by("created_at"):
		msg_id = t.result_message_id if t.action == "reply" else t.trigger_message_id
		if msg_id:
			out.setdefault(msg_id, []).append({"action": t.action, "label": ACTIONS[t.action][0], "credits": f"{-t.amount:.2f}"})
	return out


def _error(code: str, message: str, http_status: int) -> Response:
	return Response({"success": False, "error": {"code": code, "message": message}}, status=http_status)


def _paused_cutoff():
	return timezone.now() - timedelta(hours=settings.HANDOFF_AUTO_RESUME_HOURS)


def _get_conversation(request, conversation_id):
	conversation = Conversation.objects.filter(id=conversation_id).select_related(
		"knowledge_base", "contact", "project"
	).first()
	if conversation:
		_verify_project_access(request.user, conversation.project)
	return conversation


class ConversationListView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		"""List all conversations for a project."""
		project_slug = request.query_params.get("project_slug")
		if not project_slug:
			raise ValidationError({"project_slug": "This field is required"})

		project = _get_project(request.user, project_slug)

		conversations = Conversation.objects.filter(project=project).select_related(
			"knowledge_base", "contact"
		).prefetch_related("messages").annotate(credits_used=Subquery(
			CreditTransaction.objects.filter(conversation=OuterRef("pk"), kind="charge").values("conversation").annotate(s=Sum("amount")).values("s")
		)).order_by("-updated_at")
		if request.query_params.get("needs_human") == "1":
			conversations = conversations.filter(handoff_at__gte=_paused_cutoff())

		conversations_data = []
		for conv in conversations:
			messages = conv.messages.all().order_by("created_at")
			last_message = messages.last() if messages.exists() else None
			message_count = messages.count()

			conversations_data.append({
				**_conversation_dict(conv),
				"credits_used": _credits_used(conv.credits_used),
				"message_count": message_count,
				"last_message": {
					"id": str(last_message.id),
					"text": last_message.text[:100] if last_message else "",
					"created_at": last_message.created_at.isoformat() if last_message else None,
				} if last_message else None,
			})
		conversations_data.sort(key=lambda c: not c["is_paused"])  # paused first, stable within groups

		return Response({
			"success": True,
			"data": {
				"conversations": conversations_data,
				"needs_human_count": Conversation.objects.filter(project=project, handoff_at__gte=_paused_cutoff()).count(),
			}
		}, status=status.HTTP_200_OK)

	def post(self, request):
		"""Create a new conversation."""
		project_slug = request.data.get("project_slug")
		if not project_slug:
			raise ValidationError({"project_slug": "This field is required"})

		project = _get_project(request.user, project_slug)

		knowledge_base_id = request.data.get("knowledge_base_id")
		knowledge_base = None
		if knowledge_base_id:
			try:
				knowledge_base = KnowledgeBase.objects.get(id=knowledge_base_id, project=project)
			except (KnowledgeBase.DoesNotExist, DjangoValidationError):  # unknown or malformed id
				return Response({
					"success": False,
					"error": {
						"code": "not_found",
						"message": "Knowledge base not found"
					}
				}, status=status.HTTP_404_NOT_FOUND)

		name = request.data.get("name", "")

		try:
			conversation = create_conversation(
				project=project,
				knowledge_base=knowledge_base,
				name=name,
				user_id=str(request.user.id)
			)
		except ValueError as e:
			return Response({
				"success": False,
				"error": {
					"code": "validation_error",
					"message": str(e)
				}
			}, status=status.HTTP_400_BAD_REQUEST)

		return Response({
			"success": True,
			"data": {
				"conversation": _conversation_dict(conversation)
			}
		}, status=status.HTTP_201_CREATED)


class ConversationDetailView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request, conversation_id):
		"""Get conversation details without messages."""
		conversation = Conversation.objects.filter(
			id=conversation_id
		).select_related("knowledge_base", "contact", "project").first()
		
		if not conversation:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": "Conversation not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)
		
		_verify_project_access(request.user, conversation.project)
		used = CreditTransaction.objects.filter(conversation=conversation, kind="charge").aggregate(s=Sum("amount"))["s"]

		return Response({
			"success": True,
			"data": {
				"conversation": {**_conversation_dict(conversation), "credits_used": _credits_used(used)}
			}
		}, status=status.HTTP_200_OK)

	def patch(self, request, conversation_id):
		"""Update conversation (name only)."""
		conversation = Conversation.objects.filter(id=conversation_id).select_related("project", "knowledge_base").first()
		
		if not conversation:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": "Conversation not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)
		
		_verify_project_access(request.user, conversation.project)

		name = request.data.get("name")
		if name is not None:
			conversation.name = name
			conversation.save(update_fields=["name", "updated_at"])

		return Response({
			"success": True,
			"data": {
				"conversation": _conversation_dict(conversation)
			}
		}, status=status.HTTP_200_OK)

	def delete(self, request, conversation_id):
		"""Delete a conversation."""
		conversation = Conversation.objects.filter(id=conversation_id).select_related("project").first()
		
		if not conversation:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": "Conversation not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)
		
		_verify_project_access(request.user, conversation.project)

		conversation.delete()

		return Response({
			"success": True,
			"message": "Conversation deleted successfully"
		}, status=status.HTTP_200_OK)


class ConversationMessageListView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request, conversation_id):
		"""List messages in a conversation with pagination.
		
		Messages are ordered by created_at ascending (oldest first).
		For chat UI, we typically want the most recent messages first.
		- If no offset specified, returns the last 20 messages
		- If offset specified, returns messages starting from that offset
		"""
		conversation = Conversation.objects.filter(id=conversation_id).select_related("project").first()
		
		if not conversation:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": "Conversation not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)
		
		_verify_project_access(request.user, conversation.project)

		# Pagination parameters
		limit = int(request.query_params.get("limit", 20))
		offset_param = request.query_params.get("offset")
		
		# Ensure limit is reasonable (max 100)
		limit = min(limit, 100)
		limit = max(limit, 1)

		# Get total count
		total_count = conversation.messages.count()

		# Get paginated messages (ordered by created_at ascending for chat)
		messages_queryset = conversation.messages.all().order_by("created_at")
		
		# If no offset specified, get the last N messages (most recent)
		if offset_param is None:
			# Get the last 'limit' messages
			offset = max(0, total_count - limit)
			messages = messages_queryset[offset:]
		else:
			# Get messages starting from specified offset
			offset = int(offset_param)
			offset = max(0, offset)
			messages = messages_queryset[offset:offset + limit]
		
		messages = list(messages)
		credits = _message_credits(conversation)
		messages_data = [_message_dict(msg, credits.get(msg.id, ())) for msg in messages]

		# Calculate pagination info
		# has_previous = there are older messages (lower offset)
		has_previous = offset > 0
		# has_next = there are newer messages (higher offset)
		has_next = offset + len(messages_data) < total_count

		return Response({
			"success": True,
			"data": {
				"messages": messages_data,
				"pagination": {
					"count": total_count,
					"limit": limit,
					"offset": offset,
					"has_next": has_next,
					"has_previous": has_previous,
					"next_offset": offset + limit if has_next else None,
					"previous_offset": max(0, offset - limit) if has_previous else None,
				}
			}
		}, status=status.HTTP_200_OK)

	def post(self, request, conversation_id):
		"""Send a message in a conversation."""
		conversation = Conversation.objects.filter(
			id=conversation_id
		).select_related("knowledge_base", "project", "contact").first()
		
		if not conversation:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": "Conversation not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)
		
		_verify_project_access(request.user, conversation.project)
		if conversation.channel != "self":
			return _error("not_a_test_conversation", "Use staff reply for customer conversations", status.HTTP_400_BAD_REQUEST)

		text = request.data.get("text")
		if not text:
			raise ValidationError({"text": "This field is required"})

		# Create message with status="pending"
		msg = create_ui_message(conversation=conversation, text=text)

		# Trigger async agent reply task
		schedule_reply(msg)

		return Response({
			"success": True,
			"data": {
				"message": _message_dict(msg)
			}
		}, status=status.HTTP_201_CREATED)


def _test_photo_conversation(request, conversation_id):
	"""(conversation, None) when the user may send photos in this test conversation, else (None, error)."""
	conversation = Conversation.objects.filter(id=conversation_id).select_related("knowledge_base", "project", "contact").first()
	if not conversation or not ProjectUser.objects.filter(project=conversation.project, user=request.user).exists():
		return None, _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)  # other projects look the same as unknown ones
	if conversation.channel != "self":
		return None, _error("not_a_test_conversation", "Use staff reply for customer conversations", status.HTTP_400_BAD_REQUEST)
	if not conversation.project.image_search_on:
		return None, _error("feature_disabled", "Image search is not enabled", status.HTTP_404_NOT_FOUND)
	return conversation, None


def _chat_photo_prefix(conversation) -> str:
	return f"tmp/chat/{conversation.project.slug}/{conversation.id}/"


class ConversationPhotoPresignView(APIView):
	"""Step 1 of a test-chat photo: a presigned PUT straight to R2."""
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		conversation, err = _test_photo_conversation(request, conversation_id)
		if err:
			return err
		upload = presign_chat_photo(_chat_photo_prefix(conversation), request.data.get("content_type"), request.data.get("size"))
		if not upload:
			return _error("invalid_image", PHOTO_ERROR, status.HTTP_400_BAD_REQUEST)
		return Response({"success": True, "data": upload})


class ConversationPhotoView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		"""Step 2: send the uploaded photo (by key, and optional text) in a test conversation."""
		conversation, err = _test_photo_conversation(request, conversation_id)
		if err:
			return err
		key = request.data.get("key")
		if not is_own_upload(_chat_photo_prefix(conversation), key):
			return _error("invalid_upload", "Upload the photo first", status.HTTP_400_BAD_REQUEST)
		if not uploaded_photo_ok(key):
			return _error("invalid_image", PHOTO_ERROR, status.HTTP_400_BAD_REQUEST)
		msg = create_ui_message(
			conversation=conversation, text=str(request.data.get("text") or "").strip(),
			attachment_info=[{"type": "image", "path": key}], meta={"photo_reply": True},
		)
		store_message_media.delay(str(msg.id), True)  # moves + resizes the photo, then schedules the reply
		return Response({"success": True, "data": {"message": _message_dict(msg)}}, status=status.HTTP_201_CREATED)


class MessageStatusView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request, message_id):
		"""Get message status and agent reply if available."""
		message = Message.objects.filter(id=message_id).select_related("project", "conversation").first()
		
		if not message:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": "Message not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)
		
		_verify_project_access(request.user, message.project)

		agent_reply_id = None
		agent_reply = None
		if message.agent_reply_id:
			agent_reply_id = str(message.agent_reply_id)
			# Fetch the actual agent reply message using the UUID
			agent_reply_msg = Message.objects.filter(id=message.agent_reply_id).first()
			if agent_reply_msg:
				agent_reply = {
					"id": str(agent_reply_msg.id),
					"text": agent_reply_msg.text,
					"created_at": agent_reply_msg.created_at.isoformat(),
				}

		return Response({
			"success": True,
			"data": {
				"message": {
					"id": str(message.id),
					"status": message.status,
					"text": message.text,
					"created_at": message.created_at.isoformat(),
					"agent_reply_id": agent_reply_id,
					"agent_reply": agent_reply,
				}
			}
		}, status=status.HTTP_200_OK)


class MessagePhotoView(APIView):
	"""Redirects to a short-lived presigned R2 URL for one photo of a message: the browser fetches the bytes from
	R2, not through Django. `index` picks among the message's photos (a message may carry several)."""
	permission_classes = [IsAuthenticated]

	def get(self, request, message_id):
		message = Message.objects.filter(id=message_id).select_related("project").first()
		if not message or not ProjectUser.objects.filter(project=message.project, user=request.user).exists():
			return _error("not_found", "Message not found", status.HTTP_404_NOT_FOUND)  # other projects look unknown
		index = request.query_params.get("index")
		keys = photo_keys(message)
		if not index or not index.isdigit() or int(index) >= len(keys):
			return _error("not_found", "Photo not found", status.HTTP_404_NOT_FOUND)
		key = keys[int(index)]
		if not key.startswith(UPLOAD_PREFIXES):
			return _error("not_found", "Photo not found", status.HTTP_404_NOT_FOUND)  # never presign an arbitrary key
		key = thumb_key(key) if request.query_params.get("size") == "thumb" else key
		return HttpResponseRedirect(presign_get(key))


class ChannelListCreateView(APIView):
	"""List and create channel connections."""
	permission_classes = [IsAuthenticated]

	def get(self, request):
		"""List all channel connections for a project."""
		project_slug = request.query_params.get("project_slug")
		if not project_slug:
			raise ValidationError({"project_slug": "This field is required"})

		project = _get_project(request.user, project_slug)

		channels = ChannelCredential.objects.filter(project=project).select_related(
			"platform", "knowledge_base"
		).order_by("-created_at")

		channels_data = []
		for channel in channels:
			kb_data = None
			if channel.knowledge_base:
				kb_data = {
					"id": str(channel.knowledge_base.id),
					"name": channel.knowledge_base.name,
				}

			platform_data = None
			if channel.platform:
				platform_data = {
					"id": str(channel.platform.id),
					"name": channel.platform.name,
					"display_name": channel.platform.display_name,
					"icon": channel.platform.icon,
				}

			channels_data.append({
				"id": str(channel.id),
				"name": channel.name,
				"channel": channel.channel,
				"platform": platform_data,
				"knowledge_base": kb_data,
				"is_active": channel.is_active,
				"image_search_enabled": channel.image_search_enabled,
				"is_configured": channel.is_configured(),
				"created_at": channel.created_at.isoformat(),
				"updated_at": channel.updated_at.isoformat(),
			})

		return Response({
			"success": True,
			"data": {
				"channels": channels_data,
				"count": len(channels_data)
			}
		}, status=status.HTTP_200_OK)

	def post(self, request):
		"""Create a new channel connection."""
		project_slug = request.data.get("project_slug")
		if not project_slug:
			raise ValidationError({"project_slug": "This field is required"})

		project = _get_project(request.user, project_slug)

		# Required fields
		name = request.data.get("name")
		channel = request.data.get("channel")
		credentials = request.data.get("credentials", {})

		if not name:
			raise ValidationError({"name": "This field is required"})
		if not channel:
			raise ValidationError({"channel": "This field is required"})

		# Optional fields
		knowledge_base_id = request.data.get("knowledge_base_id")
		knowledge_base = None
		if knowledge_base_id:
			try:
				knowledge_base = KnowledgeBase.objects.get(id=knowledge_base_id, project=project)
			except (KnowledgeBase.DoesNotExist, DjangoValidationError):  # unknown or malformed id
				return Response({
					"success": False,
					"error": {
						"code": "not_found",
						"message": "Knowledge base not found"
					}
				}, status=status.HTTP_404_NOT_FOUND)

		# Get platform
		platform = Platform.objects.filter(name=channel).first()
		if not platform:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": f"Platform '{channel}' not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)

		# Create channel credential
		channel_cred = ChannelCredential.objects.create(
			project=project,
			platform=platform,
			name=name,
			channel=channel,
			knowledge_base=knowledge_base,
			credentials=credentials,
			is_active=request.data.get("is_active", True),
		)

		# Response data
		kb_data = None
		if channel_cred.knowledge_base:
			kb_data = {
				"id": str(channel_cred.knowledge_base.id),
				"name": channel_cred.knowledge_base.name,
			}

		platform_data = {
			"id": str(platform.id),
			"name": platform.name,
			"display_name": platform.display_name,
			"icon": platform.icon,
		}

		return Response({
			"success": True,
			"data": {
				"channel": {
					"id": str(channel_cred.id),
					"name": channel_cred.name,
					"channel": channel_cred.channel,
					"platform": platform_data,
					"knowledge_base": kb_data,
					"is_active": channel_cred.is_active,
					"image_search_enabled": channel_cred.image_search_enabled,
					"is_configured": channel_cred.is_configured(),
					"created_at": channel_cred.created_at.isoformat(),
					"updated_at": channel_cred.updated_at.isoformat(),
				}
			}
		}, status=status.HTTP_201_CREATED)


class ChannelDetailView(APIView):
	"""Get, update, or delete a specific channel connection."""
	permission_classes = [IsAuthenticated]

	def get(self, request, channel_id):
		"""Get channel details including credentials."""
		channel = ChannelCredential.objects.filter(
			id=channel_id
		).select_related("project", "platform", "knowledge_base").first()

		if not channel:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": "Channel not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)

		_verify_project_access(request.user, channel.project)

		kb_data = None
		if channel.knowledge_base:
			kb_data = {
				"id": str(channel.knowledge_base.id),
				"name": channel.knowledge_base.name,
			}

		platform_data = None
		if channel.platform:
			platform_data = {
				"id": str(channel.platform.id),
				"name": channel.platform.name,
				"display_name": channel.platform.display_name,
				"icon": channel.platform.icon,
			}

		return Response({
			"success": True,
			"data": {
				"channel": {
					"id": str(channel.id),
					"name": channel.name,
					"channel": channel.channel,
					"platform": platform_data,
					"knowledge_base": kb_data,
					"credentials": channel.credentials,
					"is_active": channel.is_active,
					"image_search_enabled": channel.image_search_enabled,
					"is_configured": channel.is_configured(),
					"created_at": channel.created_at.isoformat(),
					"updated_at": channel.updated_at.isoformat(),
				}
			}
		}, status=status.HTTP_200_OK)

	def patch(self, request, channel_id):
		"""Update a channel connection."""
		channel = ChannelCredential.objects.filter(
			id=channel_id
		).select_related("project", "platform", "knowledge_base").first()

		if not channel:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": "Channel not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)

		_verify_project_access(request.user, channel.project)

		# Update fields
		update_fields = []
		
		if "name" in request.data:
			channel.name = request.data["name"]
			update_fields.append("name")

		if "credentials" in request.data:
			channel.credentials = request.data["credentials"]
			update_fields.append("credentials")

		if "is_active" in request.data:
			channel.is_active = request.data["is_active"]
			update_fields.append("is_active")

		if "image_search_enabled" in request.data:
			channel.image_search_enabled = bool(request.data["image_search_enabled"])
			update_fields.append("image_search_enabled")

		if "knowledge_base_id" in request.data:
			kb_id = request.data["knowledge_base_id"]
			if kb_id:
				try:
					kb = KnowledgeBase.objects.get(id=kb_id, project=channel.project)
					channel.knowledge_base = kb
				except KnowledgeBase.DoesNotExist:
					return Response({
						"success": False,
						"error": {
							"code": "not_found",
							"message": "Knowledge base not found"
						}
					}, status=status.HTTP_404_NOT_FOUND)
			else:
				channel.knowledge_base = None
			update_fields.append("knowledge_base")

		if update_fields:
			update_fields.append("updated_at")
			channel.save(update_fields=update_fields)

		# Response data
		kb_data = None
		if channel.knowledge_base:
			kb_data = {
				"id": str(channel.knowledge_base.id),
				"name": channel.knowledge_base.name,
			}

		platform_data = None
		if channel.platform:
			platform_data = {
				"id": str(channel.platform.id),
				"name": channel.platform.name,
				"display_name": channel.platform.display_name,
				"icon": channel.platform.icon,
			}

		return Response({
			"success": True,
			"data": {
				"channel": {
					"id": str(channel.id),
					"name": channel.name,
					"channel": channel.channel,
					"platform": platform_data,
					"knowledge_base": kb_data,
					"is_active": channel.is_active,
					"image_search_enabled": channel.image_search_enabled,
					"is_configured": channel.is_configured(),
					"created_at": channel.created_at.isoformat(),
					"updated_at": channel.updated_at.isoformat(),
				}
			}
		}, status=status.HTTP_200_OK)

	def delete(self, request, channel_id):
		"""Delete a channel connection."""
		channel = ChannelCredential.objects.filter(
			id=channel_id
		).select_related("project").first()

		if not channel:
			return Response({
				"success": False,
				"error": {
					"code": "not_found",
					"message": "Channel not found"
				}
			}, status=status.HTTP_404_NOT_FOUND)

		_verify_project_access(request.user, channel.project)

		channel.delete()

		return Response({
			"success": True,
			"message": "Channel deleted successfully"
		}, status=status.HTTP_200_OK)




class ConversationHandoffView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		conversation = _get_conversation(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		start_handoff(conversation, (request.data.get("reason") or "").strip() or "paused by staff")
		return Response({"success": True, "data": {"conversation": _conversation_dict(conversation)}})


class ConversationResumeView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		conversation = _get_conversation(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		end_handoff(conversation)
		return Response({"success": True, "data": {"conversation": _conversation_dict(conversation)}})


def _window_error(conversation, last_in):
	"""The 24h free-form messaging window, shared by the text and photo staff replies: None when the send is
	allowed, else the refusal response."""
	if conversation.channel not in ("facebook", "instagram", "whatsapp"):
		return None
	if last_in and timezone.now() - last_in.created_at <= timedelta(hours=24):
		return None
	hint = "Message templates aren't supported yet." if conversation.channel == "whatsapp" else "Reply from the Meta inbox instead."
	return _error(
		"outside_messaging_window",
		f"{conversation.channel.capitalize()} only allows replies within 24 hours of the customer's last message. {hint}",
		status.HTTP_400_BAD_REQUEST,
	)


class ConversationStaffReplyView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		conversation = _get_conversation(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		text = (request.data.get("text") or "").strip()
		if not 1 <= len(text) <= 2000:
			return _error("invalid_text", "Reply must be 1-2000 characters", status.HTTP_400_BAD_REQUEST)

		last_in = conversation.messages.filter(direction="in").order_by("-created_at").first()
		refusal = _window_error(conversation, last_in)
		if refusal:
			return refusal

		msg = create_outbound_message(
			project=conversation.project, contact=conversation.contact, text=text,
			conversation=conversation, sender="staff", meta=answered_marker(last_in),
		)
		note_staff_activity(conversation)
		if not deliver(conversation, msg, last_in):
			# Kept for the record but excluded from history/turns: the customer never saw it
			msg.status = "failed"
			msg.save(update_fields=["status"])
			return _error("send_failed", "The message could not be delivered to the customer", status.HTTP_502_BAD_GATEWAY)
		emit_event(conversation.project, "message.staff_reply", {
			**conversation_event_data(conversation),
			"message": {"id": str(msg.id), "text": msg.text, "created_at": msg.created_at.isoformat()},
		})
		schedule_memory_update(conversation)
		msg_id = str(msg.id)
		transaction.on_commit(lambda: memory.draft_knowledge_suggestion.delay(msg_id))
		return Response({"success": True, "data": {"message": _message_dict(msg)}}, status=status.HTTP_201_CREATED)


class ConversationStaffPhotoPresignView(APIView):
	"""Step 1 of a staff photo: a presigned PUT straight to R2 (the bytes never pass through Django)."""
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		conversation = _get_conversation(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		upload = presign_chat_photo(_chat_photo_prefix(conversation), request.data.get("content_type"), request.data.get("size"))
		if not upload:
			return _error("invalid_image", PHOTO_ERROR, status.HTTP_400_BAD_REQUEST)
		return Response({"success": True, "data": upload})


class ConversationStaffPhotoView(APIView):
	"""Step 2: send the uploaded photo (by key, and optional text) to the customer as staff."""
	permission_classes = [IsAuthenticated]

	def post(self, request, conversation_id):
		conversation = _get_conversation(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		last_in = conversation.messages.filter(direction="in").order_by("-created_at").first()
		refusal = _window_error(conversation, last_in)
		if refusal:
			return refusal
		key = request.data.get("key")
		if not is_own_upload(_chat_photo_prefix(conversation), key):
			return _error("invalid_upload", "Upload the photo first", status.HTTP_400_BAD_REQUEST)
		if not uploaded_photo_ok(key):
			return _error("invalid_image", PHOTO_ERROR, status.HTTP_400_BAD_REQUEST)
		att = {"type": "image", "path": key}
		try:
			# Stored before the row exists, so a bad upload leaves no message behind, and the copy we send is
			# the stored one (JPEG: chat platforms reject the WebP a customer photo becomes).
			att["path"] = store_outbound_photo(conversation, att)
		except (MediaError, NotAnImage):
			return _error("invalid_image", PHOTO_ERROR, status.HTTP_400_BAD_REQUEST)

		msg = create_outbound_message(
			project=conversation.project, contact=conversation.contact,
			text=(request.data.get("text") or "").strip(),
			conversation=conversation, sender="staff", meta=answered_marker(last_in),
			attachment_info=[att],
		)
		if not deliver(conversation, msg, last_in):
			# "inflight" means a part may have reached the customer: hold the row and keep the stored photo,
			# since the customer may already have it. Otherwise the send definitely failed and the bytes go.
			unknown = "inflight" in msg.meta
			msg.status = "unknown" if unknown else "failed"
			msg.save(update_fields=["status"])
			if not unknown:
				delete_keys(att["path"], thumb_key(att["path"]))
				return _error("send_failed", "The message could not be delivered to the customer", status.HTTP_502_BAD_GATEWAY)
			return _error("send_unknown", "The photo may or may not have reached the customer", status.HTTP_502_BAD_GATEWAY)
		note_staff_activity(conversation)
		emit_event(conversation.project, "message.staff_reply", {
			**conversation_event_data(conversation),
			"message": {"id": str(msg.id), "text": msg.text, "created_at": msg.created_at.isoformat()},
		})
		schedule_memory_update(conversation)
		msg_id = str(msg.id)
		transaction.on_commit(lambda: memory.draft_knowledge_suggestion.delay(msg_id))
		return Response({"success": True, "data": {"message": _message_dict(msg)}}, status=status.HTTP_201_CREATED)


class ConversationMemoryView(APIView):
	"""What the bot remembers about this conversation's customer; staff can delete one fact (?key=) or all."""
	permission_classes = [IsAuthenticated]

	def _contact(self, request, conversation_id):
		conversation = _get_conversation(request, conversation_id)
		return conversation, conversation.contact if conversation else None

	def _response(self, conversation, contact):
		episodes = [
			{"id": str(e.id), "started_at": _iso(e.started_at), "ended_at": _iso(e.ended_at), "summary": e.summary}
			for e in conversation.episodes.exclude(summary="").order_by("-ended_at")[:20]
		]
		return Response({"success": True, "data": {"facts": contact.memory if contact else [], "episodes": episodes}})

	def get(self, request, conversation_id):
		conversation, contact = self._contact(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		return self._response(conversation, contact)

	def delete(self, request, conversation_id):
		conversation, contact = self._contact(request, conversation_id)
		if not conversation:
			return _error("not_found", "Conversation not found", status.HTTP_404_NOT_FOUND)
		key = request.query_params.get("key")
		if contact:
			with transaction.atomic():  # same row lock as update_contact_memory, so a running extraction can't undo this
				contact = CustomerContact.objects.select_for_update().get(id=contact.id)
				contact.memory = [f for f in contact.memory if key and f["key"] != key]
				contact.save(update_fields=["memory"])  # memory_through stays: deleted facts are not re-extracted
		if not key:
			memory.forget_conversation(conversation)
		return self._response(conversation, contact)


def _webhook_dict(hook: ProjectWebhook) -> dict:
	return {"url": hook.url, "is_active": hook.is_active, "secret": hook.secret}


def _project_webhook(request) -> ProjectWebhook:
	slug = request.query_params.get("project_slug") or request.data.get("project_slug")
	if not slug:
		raise ValidationError({"project_slug": "This field is required"})
	project = _get_project(request.user, slug)
	hook, _ = ProjectWebhook.objects.get_or_create(project=project)
	return hook


class ProjectWebhookView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		return Response({"success": True, "data": {"webhook": _webhook_dict(_project_webhook(request))}})

	def put(self, request):
		hook = _project_webhook(request)
		url = (request.data.get("url") or "").strip()
		is_active = request.data.get("is_active") is True
		try:
			if url:
				url = validate_webhook_url(url)
			if is_active and not url:
				raise WebhookUrlError("Add a URL before enabling the webhook")
		except WebhookUrlError as e:
			return _error("invalid_url", str(e), status.HTTP_400_BAD_REQUEST)
		hook.url, hook.is_active = url, is_active
		hook.save(update_fields=["url", "is_active", "updated_at"])
		return Response({"success": True, "data": {"webhook": _webhook_dict(hook)}})


class ProjectWebhookRotateView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		hook = _project_webhook(request)
		hook.secret = ProjectWebhook._meta.get_field("secret").default()
		hook.save(update_fields=["secret", "updated_at"])
		return Response({"success": True, "data": {"webhook": _webhook_dict(hook)}})


class ProjectWebhookTestView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		hook = _project_webhook(request)
		try:
			code = post_event(hook, build_event(hook.project_id, "ping", {"message": "Test event from TumarAgent"}))
		except WebhookUrlError as e:
			return _error("invalid_url", str(e), status.HTTP_400_BAD_REQUEST)
		except requests.RequestException:
			code = None
		# Only ok/failed: exact statuses or error types would make this a probe for what the URL reaches
		# ponytail: DNS rebinding can still redirect delivery between our check and requests' own lookup; pin the resolved IP if tenants are untrusted
		if code is None or not 200 <= code < 300:
			return _error("delivery_failed", "Your endpoint did not accept the test event (no 2xx response)", status.HTTP_502_BAD_GATEWAY)
		return Response({"success": True, "data": {"ok": True}})
