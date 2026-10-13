from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase
from .models import ChannelCredential, Conversation, Message, Platform
from .service.conversation_service import create_conversation, get_default_knowledge_base
from .service.message_service import create_ui_message
from datetime import timedelta
import requests
from django.conf import settings
from django.utils import timezone
from .handoff import conversation_event_data, end_handoff, note_staff_activity, start_handoff
from .models import ProjectWebhook
from .reply import deliver, schedule_reply
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


def _message_dict(msg: Message) -> dict:
	return {
		"id": str(msg.id),
		"direction": msg.direction,
		"sender": msg.sender,
		"status": msg.status,
		"text": msg.text,
		"created_at": msg.created_at.isoformat(),
		"agent_reply_id": str(msg.agent_reply_id) if msg.agent_reply_id else None,
	}


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
		).prefetch_related("messages").order_by("-updated_at")
		if request.query_params.get("needs_human") == "1":
			conversations = conversations.filter(handoff_at__gte=_paused_cutoff())

		conversations_data = []
		for conv in conversations:
			messages = conv.messages.all().order_by("created_at")
			last_message = messages.last() if messages.exists() else None
			message_count = messages.count()

			conversations_data.append({
				**_conversation_dict(conv),
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
			except KnowledgeBase.DoesNotExist:
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

		return Response({
			"success": True,
			"data": {
				"conversation": _conversation_dict(conversation)
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
		
		messages_data = [_message_dict(msg) for msg in messages]

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
			except KnowledgeBase.DoesNotExist:
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
		if conversation.channel == "facebook" and (
			not last_in or timezone.now() - last_in.created_at > timedelta(hours=24)
		):
			return _error(
				"outside_messaging_window",
				"Facebook only allows replies within 24 hours of the customer's last message. Reply from the Meta inbox instead.",
				status.HTTP_400_BAD_REQUEST,
			)

		msg = create_outbound_message(
			project=conversation.project, contact=conversation.contact, text=text,
			conversation=conversation, sender="staff",
		)
		note_staff_activity(conversation)
		delivered = deliver(conversation, msg, last_in)
		emit_event(conversation.project, "message.staff_reply", {
			**conversation_event_data(conversation),
			"message": {"id": str(msg.id), "text": msg.text, "created_at": msg.created_at.isoformat()},
		})
		if not delivered:
			return _error("send_failed", "Saved, but the message could not be delivered to the customer", status.HTTP_502_BAD_GATEWAY)
		return Response({"success": True, "data": {"message": _message_dict(msg)}}, status=status.HTTP_201_CREATED)


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
			code = post_event(hook, build_event(hook.project_id, "ping", {"message": "Test event from Auto Magic Pro"}))
		except WebhookUrlError as e:
			return _error("invalid_url", str(e), status.HTTP_400_BAD_REQUEST)
		except requests.RequestException as e:
			return _error("delivery_failed", f"Could not reach the URL ({type(e).__name__})", status.HTTP_502_BAD_GATEWAY)
		return Response({"success": True, "data": {"status": code}})
