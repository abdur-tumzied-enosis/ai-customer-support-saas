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
from agents.api_tool import collect_api_domains

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
		List of instruction dicts with 'label', 'message', 'api_tool_enabled' and 'api_domains' keys
	"""
	# Try KB-level instructions first
	if knowledge_base_id:
		kb_instructions = CustomInstruction.objects.filter(
			project_id=project_id,
			knowledge_base_id=knowledge_base_id
		).order_by('-created_at')
		
		if kb_instructions.exists():
			return [{'label': inst.label, 'message': inst.message, 'api_tool_enabled': inst.api_tool_enabled, 'api_domains': inst.api_domains} for inst in kb_instructions]
	
	# Fall back to project-level instructions
	project_instructions = CustomInstruction.objects.filter(
		project_id=project_id,
		knowledge_base__isnull=True
	).order_by('-created_at')
	
	return [{'label': inst.label, 'message': inst.message, 'api_tool_enabled': inst.api_tool_enabled, 'api_domains': inst.api_domains} for inst in project_instructions]


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


HISTORY_LIMIT = 10
FALLBACK_REPLY = "Sorry, I'm having trouble answering right now. Please try again in a moment."


def build_history(msg: Message) -> list:
	"""Last HISTORY_LIMIT messages before `msg`, oldest first."""
	qs = (
		Message.objects.filter(conversation=msg.conversation) if msg.conversation
		else Message.objects.filter(contact=msg.contact)
	)
	recent = list(
		qs.exclude(id=msg.id)
		.filter(created_at__lte=msg.created_at)
		.select_related("reply_to_message")
		.order_by("-created_at")[:HISTORY_LIMIT]
	)
	recent.reverse()
	return [
		ChatMessage(role=("user" if m.direction == "in" else "assistant"), content=format_message_with_context(m))
		for m in recent
	]


def generate_agent_reply(msg: Message, knowledge_base: KnowledgeBase) -> tuple[str, list]:
	"""Run the RAG agent for `msg`. Returns (reply text, API call log). DB reads only; call outside any transaction."""
	project = msg.project
	history = build_history(msg)
	current_message_text = format_message_with_context(msg)

	custom_instructions = get_effective_instructions(str(project.id), str(knowledge_base.id))
	logger.info(f"Using {len(custom_instructions)} custom instructions")

	context = Context(
		knowledge_base_id=str(knowledge_base.id),
		vector_collection=KnowledgeBase.get_vector_collection_name(project, knowledge_base.name),
		custom_instructions=custom_instructions,
		api_domains=collect_api_domains(custom_instructions),
	)
	deps = ToolDeps(
		retriever_factory=get_retriever,
		retrieved_factory_for_crawler_url_context=lambda: get_web_crawl_retriever(str(knowledge_base.id), k=5)
	)

	logger.info(f"Generating reply for message {msg.id} with text: {current_message_text}")
	logger.info(f"History ({len(history)} msgs): {history[-3:]}")

	try:
		reply_text = generate_reply(current_message_text, context, deps, history=history, project_id=str(project.id))
	except Exception as exc:
		setattr(exc, "api_calls", context.api_call_log)  # lets the caller avoid re-sending POSTs on retry
		raise
	logger.info(f"Reply text: {reply_text}")
	return reply_text, context.api_call_log


def get_facebook_credential(msg: Message):
	"""Find the Facebook page credential that received `msg` (page id is the webhook recipient)."""
	creds = ChannelCredential.objects.filter(project=msg.project, channel="facebook", is_active=True)
	page_id = (msg.meta or {}).get("recipient", {}).get("id")
	return (page_id and creds.filter(credentials__page_id=page_id).first()) or creds.first()


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def handle_incoming_message(self, message_id: str, knowledge_base_id: str) -> None:
	"""Handle incoming message from external channel (e.g., Facebook)."""
	msg = Message.objects.select_related("project", "contact", "reply_to_message", "conversation").get(id=message_id)
	knowledge_base = KnowledgeBase.objects.get(id=knowledge_base_id)

	# LLM call runs outside any transaction so we don't hold a DB connection/locks for seconds
	api_calls: list = []
	try:
		reply_text, api_calls = generate_agent_reply(msg, knowledge_base)
	except Exception as exc:
		api_calls = getattr(exc, "api_calls", [])
		# A retry re-runs the whole agent; never risk sending a POST (order, booking, ticket) twice
		sent_post = any(c.get("method") == "POST" for c in api_calls)
		if not sent_post and self.request.retries < self.max_retries:
			logger.warning(f"Reply generation failed for {msg.id}, retrying: {exc}")
			raise self.retry(exc=exc)
		logger.error(f"Reply generation failed for {msg.id} after retries: {exc}", exc_info=True)
		reply_text = ""
	reply_text = reply_text or FALLBACK_REPLY

	out_msg = create_outbound_message(
		project=msg.project,
		contact=msg.contact,
		text=reply_text,
		external_message_id="",  # Will be updated after sending
		meta={"api_calls": api_calls} if api_calls else {},
		conversation=msg.conversation,
		id=msg.agent_reply_id or None
	)

	cred = get_facebook_credential(msg)
	if not cred:
		logger.error(f"No active Facebook credential for project {msg.project_id}; reply {out_msg.id} not sent")
		return
	response = send_text_message(
		page_access_token=cred.get_credentials().page_access_token,
		recipient_psid=msg.contact.external_id,
		text=reply_text
	)

	# Update message with Facebook message_id if successful
	if response.get("message_id"):
		out_msg.external_message_id = response["message_id"]
		out_msg.meta = {**out_msg.meta, **response}  # keep api_calls, add Facebook send response
		out_msg.save(update_fields=["external_message_id", "meta"])
		logger.info(f"Updated outbound message {out_msg.id} with Facebook message_id: {response['message_id']}")
	else:
		logger.warning(f"Failed to get message_id from Facebook API response for message {out_msg.id}")


@shared_task
def handle_ui_message(message_id: str) -> None:
	"""Handle UI message and generate agent reply."""
	try:
		msg = Message.objects.select_related(
			"project", "contact", "conversation", "conversation__knowledge_base", "reply_to_message"
		).get(id=message_id)
		if not msg.conversation:
			raise ValueError(f"Message {message_id} has no conversation")
		if not msg.agent_reply_id:
			raise ValueError(f"User message {msg.id} does not have agent_reply_id set")

		msg.status = "processing"
		msg.save(update_fields=["status"])

		# LLM call runs outside any transaction
		reply_text, api_calls = generate_agent_reply(msg, msg.conversation.knowledge_base)

		with transaction.atomic():
			out_msg = create_outbound_message(
				project=msg.project,
				contact=msg.contact,
				text=reply_text,
				external_message_id="",
				meta={"api_calls": api_calls} if api_calls else {},
				conversation=msg.conversation,
				id=msg.agent_reply_id
			)
			msg.status = "completed"
			msg.save(update_fields=["status"])

		logger.info(f"UI message {msg.id} completed, agent reply {out_msg.id} created")

	except Exception as e:
		logger.error(f"Error processing UI message {message_id}: {e}", exc_info=True)
		Message.objects.filter(id=message_id).update(status="failed")
		raise
