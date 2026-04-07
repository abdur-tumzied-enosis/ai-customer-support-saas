from celery import shared_task
from django.db import transaction
import uuid

from knowledge.models import KnowledgeBase, CustomInstruction
from .models import Message, ChannelCredential
from .facebook_client import send_text_message
from .service.message_service import create_outbound_message
from agents.rag import generate_reply, Context, ToolDeps, ChatMessage
from agents.vector_store import get_retriever
from agents.web_crawl_retriever import get_web_crawl_retriever

import logging
logger = logging.getLogger(__name__)


def get_effective_instructions(project_id: str, knowledge_base_id: str = None) -> list:
	"""
	Get effective custom instructions for a project/knowledge base.
	Returns KB-level instructions if they exist, otherwise project-level.
	
	Args:
		project_id: Project UUID
		knowledge_base_id: Optional Knowledge Base UUID
	
	Returns:
		List of instruction dicts with 'label' and 'message' keys
	"""
	# Try KB-level instructions first
	if knowledge_base_id:
		kb_instructions = CustomInstruction.objects.filter(
			project_id=project_id,
			knowledge_base_id=knowledge_base_id
		).order_by('-created_at')
		
		if kb_instructions.exists():
			return [{'label': inst.label, 'message': inst.message} for inst in kb_instructions]
	
	# Fall back to project-level instructions
	project_instructions = CustomInstruction.objects.filter(
		project_id=project_id,
		knowledge_base__isnull=True
	).order_by('-created_at')
	
	return [{'label': inst.label, 'message': inst.message} for inst in project_instructions]


def format_message_with_context(message: Message) -> str:
	"""
	Format a message with contextual information (attachments, replies).
	
	Args:
		message: The Message instance to format
		
	Returns:
		Formatted message text with context annotations
	"""
	content = message.text
	
	# Add context about attachments if present
	if message.has_attachments:
		att_types = [att.get("type", "unknown") for att in message.attachment_info]
		content = f"{content} [Message includes attachments: {', '.join(att_types)}]"
	
	# Add context about replies - specify which message it's replying to
	if message.is_reply and message.reply_to_message:
		reply_text = message.reply_to_message.text[:100]  # First 100 chars of replied message
		if not reply_text:
			reply_text = "[previous message]"
		content = f"{content} [Replying to: \"{reply_text}\"]"
	elif message.is_reply:
		content = f"{content} [This was a reply to a previous message]"
	
	return content


@shared_task
def handle_incoming_message(message_id: str, knowledge_base_id: str) -> None:
	"""Handle incoming message from external channel (e.g., Facebook)."""
	with transaction.atomic():
		msg = Message.objects.select_related("project", "contact", "reply_to_message", "conversation").get(id=message_id)
		knowledge_base = KnowledgeBase.objects.get(id=knowledge_base_id)
		project = msg.project
		contact = msg.contact
		conversation = msg.conversation
		
		# Build conversation history from conversation messages if available, otherwise from contact messages
		if conversation:
			prev_qs = (
				Message.objects.filter(conversation=conversation)
				.select_related("reply_to_message")
				.order_by("-created_at")
			)
		else:
			prev_qs = (
				Message.objects.filter(contact=contact)
				.select_related("reply_to_message")
				.order_by("-created_at")
			)
		
		# Convert the queryset to a list and reverse it so that the oldest message comes first (chronological order)
		prev = list(prev_qs)
		
		# Build history with enhanced context
		history = []
		for m in prev:
			content = format_message_with_context(m)
			history.append(ChatMessage(
				role=("user" if m.direction == "in" else "assistant"),
				content=content
			))
		
		# Prepare current message text with context
		current_message_text = format_message_with_context(msg)
		
		# Get effective custom instructions for this project/KB
		custom_instructions = get_effective_instructions(str(project.id), str(knowledge_base.id))
		logger.info(f"Using {len(custom_instructions)} custom instructions")
		
		# Generate reply via RAG
		context = Context(
			knowledge_base_id=str(knowledge_base.id),
			vector_collection=KnowledgeBase.get_vector_collection_name(project, knowledge_base.name),
			custom_instructions=custom_instructions
		)
		deps = ToolDeps(
			retriever_factory=get_retriever,
			retrieved_factory_for_crawler_url_context=lambda: get_web_crawl_retriever(str(knowledge_base.id), k=5)
		)

		logger.info(f"Generating reply for message {msg.id} with text: {current_message_text}")
		logger.info(f"History: {history[:5]}")

		reply_text = generate_reply(current_message_text, context, deps, history=history[-10:], project_id=str(project.id))
		logger.info(f"Reply text: {reply_text}")
		agent_reply_uuid = msg.agent_reply_id
		# Persist outbound message record using service
		out_msg = create_outbound_message(
			project=project,
			contact=contact,
			text=reply_text,
			external_message_id="",  # Will be updated after sending
			meta={},
			conversation=conversation,
			id= agent_reply_uuid or None
		)

	# Send via Facebook
	cred = ChannelCredential.objects.get(project=project, channel="facebook")
	response = send_text_message(
		page_access_token=cred.page_access_token,
		recipient_psid=contact.external_id,
		text=reply_text
	)
	
	# Update message with Facebook message_id if successful
	if response.get("message_id"):
		out_msg.external_message_id = response["message_id"]
		out_msg.meta = response  # Store full response in meta
		out_msg.save(update_fields=["external_message_id", "meta"])
		logger.info(f"Updated outbound message {out_msg.id} with Facebook message_id: {response['message_id']}")
	else:
		logger.warning(f"Failed to get message_id from Facebook API response for message {out_msg.id}")


@shared_task
def handle_ui_message(message_id: str) -> None:
	"""Handle UI message and generate agent reply."""
	try:
		with transaction.atomic():
			msg = Message.objects.select_related(
				"project", "contact", "conversation", "conversation__knowledge_base"
			).get(id=message_id)
			
			# Update status to processing
			msg.status = "processing"
			msg.save(update_fields=["status"])
			
			conversation = msg.conversation
			if not conversation:
				raise ValueError(f"Message {message_id} has no conversation")
			
			project = msg.project
			contact = msg.contact
			knowledge_base = conversation.knowledge_base
			
			# Build conversation history from conversation messages
			prev_qs = (
				Message.objects.filter(conversation=conversation)
				.exclude(id=msg.id)  # Exclude current message
				.select_related("reply_to_message")
				.order_by("-created_at")
			)
			
			# Convert to list and reverse for chronological order
			prev = list(prev_qs)
			prev.reverse()
			
			# Build history with enhanced context
			history = []
			for m in prev:
				content = format_message_with_context(m)
				history.append(ChatMessage(
					role=("user" if m.direction == "in" else "assistant"),
					content=content
				))
			
			# Prepare current message text with context
			current_message_text = format_message_with_context(msg)
			
			# Get effective custom instructions for this project/KB
			custom_instructions = get_effective_instructions(str(project.id), str(knowledge_base.id))
			logger.info(f"Using {len(custom_instructions)} custom instructions")
			
			# Generate reply via RAG
			context = Context(
				knowledge_base_id=str(knowledge_base.id),
				vector_collection=KnowledgeBase.get_vector_collection_name(project, knowledge_base.name),
				custom_instructions=custom_instructions
			)
			deps = ToolDeps(
				retriever_factory=get_retriever,
				retrieved_factory_for_crawler_url_context=lambda: get_web_crawl_retriever(str(knowledge_base.id), k=5)
			)

			logger.info(f"Generating reply for UI message {msg.id} with text: {current_message_text}")
			logger.info(f"History: {history[:5]}")

			reply_text = generate_reply(current_message_text, context, deps, history=history[-10:], project_id=str(project.id))
			logger.info(f"Reply text: {reply_text}")
			
			# Get the pre-generated agent_reply_id from user message
			agent_reply_uuid = msg.agent_reply_id
			if not agent_reply_uuid:
				raise ValueError(f"User message {msg.id} does not have agent_reply_id set")
			
			# Persist outbound message record using service with the pre-generated UUID
			out_msg = create_outbound_message(
				project=project,
				contact=contact,
				text=reply_text,
				external_message_id="",
				meta={},
				conversation=conversation,
				id=agent_reply_uuid
			)
			
			# Update user message status to completed
			msg.status = "completed"
			msg.save(update_fields=["status"])
			
			logger.info(f"UI message {msg.id} completed, agent reply {out_msg.id} created")
			
	except Exception as e:
		logger.error(f"Error processing UI message {message_id}: {e}", exc_info=True)
		# Update message status to failed
		try:
			with transaction.atomic():
				msg = Message.objects.get(id=message_id)
				msg.status = "failed"
				msg.save(update_fields=["status"])
		except Exception:
			pass
		raise


