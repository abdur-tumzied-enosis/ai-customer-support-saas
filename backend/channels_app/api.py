from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase
from .models import ChannelCredential, Conversation, Message
from .service.conversation_service import create_conversation, get_default_knowledge_base
from .service.message_service import create_ui_message
from .tasks import handle_ui_message


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

		conversations_data = []
		for conv in conversations:
			messages = conv.messages.all().order_by("created_at")
			last_message = messages.last() if messages.exists() else None
			message_count = messages.count()

			conversations_data.append({
				"id": str(conv.id),
				"name": conv.name or "Unnamed Conversation",
				"knowledge_base_id": str(conv.knowledge_base.id),
				"knowledge_base_name": conv.knowledge_base.name,
				"message_count": message_count,
				"last_message": {
					"id": str(last_message.id),
					"text": last_message.text[:100] if last_message else "",
					"created_at": last_message.created_at.isoformat() if last_message else None,
				} if last_message else None,
				"created_at": conv.created_at.isoformat(),
				"updated_at": conv.updated_at.isoformat(),
			})

		return Response({
			"success": True,
			"data": {
				"conversations": conversations_data
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
				"conversation": {
					"id": str(conversation.id),
					"name": conversation.name or "Unnamed Conversation",
					"knowledge_base_id": str(conversation.knowledge_base.id),
					"knowledge_base_name": conversation.knowledge_base.name,
					"created_at": conversation.created_at.isoformat(),
					"updated_at": conversation.updated_at.isoformat(),
				}
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
				"conversation": {
					"id": str(conversation.id),
					"name": conversation.name or "Unnamed Conversation",
					"knowledge_base_id": str(conversation.knowledge_base.id),
					"knowledge_base_name": conversation.knowledge_base.name,
					"created_at": conversation.created_at.isoformat(),
					"updated_at": conversation.updated_at.isoformat(),
				}
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
				"conversation": {
					"id": str(conversation.id),
					"name": conversation.name or "Unnamed Conversation",
					"knowledge_base_id": str(conversation.knowledge_base.id),
					"knowledge_base_name": conversation.knowledge_base.name,
					"created_at": conversation.created_at.isoformat(),
					"updated_at": conversation.updated_at.isoformat(),
				}
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
		
		messages_data = []
		for msg in messages:
			messages_data.append({
				"id": str(msg.id),
				"direction": msg.direction,
				"status": msg.status,
				"text": msg.text,
				"created_at": msg.created_at.isoformat(),
				"agent_reply_id": str(msg.agent_reply_id) if msg.agent_reply_id else None,
			})

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

		text = request.data.get("text")
		if not text:
			raise ValidationError({"text": "This field is required"})

		# Create message with status="pending"
		msg = create_ui_message(conversation=conversation, text=text)

		# Trigger async agent reply task
		handle_ui_message.delay(str(msg.id))

		return Response({
			"success": True,
			"data": {
				"message": {
					"id": str(msg.id),
					"direction": msg.direction,
					"status": msg.status,
					"text": msg.text,
					"created_at": msg.created_at.isoformat(),
				}
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


