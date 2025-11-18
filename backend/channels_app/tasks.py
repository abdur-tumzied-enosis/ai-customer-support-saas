from celery import shared_task
from django.db import transaction

from knowledge.models import KnowledgeBase
from .models import Message, ChannelCredential
from .facebook_client import send_text_message
from .service.message_service import create_outbound_message
from agents.rag import generate_reply, Context, ToolDeps, ChatMessage
from agents.vector_store import get_retriever

import logging
logger = logging.getLogger(__name__)


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
def handle_incoming_message(message_id: int, knowledge_base_id: str) -> None:
	with transaction.atomic():
		msg = Message.objects.select_related("project", "contact", "reply_to_message").get(id=message_id)
		knowledge_base = KnowledgeBase.objects.get(id=knowledge_base_id)
		project = msg.project
		contact = msg.contact
		# Build short conversation history (role-mapped) excluding current message (limit last 20)
		# Include metadata for better context understanding
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
		
		# Generate reply via RAG
		context = Context(vector_collection=KnowledgeBase.get_vector_collection_name(project, knowledge_base.name))
		deps = ToolDeps(retriever_factory=get_retriever)

		logger.info(f"Generating reply for message {msg.id} with text: {current_message_text}")
		logger.info(f"History: {history[:5]}")

		reply_text = generate_reply(current_message_text, context, deps, history=history[:5])
		logger.info(f"Reply text: {reply_text}")
		# Persist outbound message record using service
		out_msg = create_outbound_message(
			project=project,
			contact=contact,
			text=reply_text,
			external_message_id="",  # Will be updated after sending
			meta={}
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


