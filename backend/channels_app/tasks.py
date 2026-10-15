from celery import shared_task
from django.db.models import Q
from typing import Optional

from knowledge.models import KnowledgeBase, CustomInstruction
from . import memory  # also registers the memory Celery tasks
from .models import Message, ChannelCredential
from .facebook_client import send_text_message
from .service.message_service import create_outbound_message
from agents.rag import generate_reply, Context, ToolDeps, ChatMessage
from agents.vector_store import get_retriever
from agents.web_crawl_retriever import get_web_crawl_retriever
from agents.api_tool import collect_api_domains
from usage.utils import estimate_tokens

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


HISTORY_MAX_MESSAGES = 50
HISTORY_TOKEN_BUDGET = 3000  # estimated tokens of history sent with each reply
FALLBACK_REPLY = "Sorry, I'm having trouble answering right now. Please try again in a moment."


def build_history(msg: Message, since=None) -> list:
	"""Recent messages before `msg` that fit HISTORY_TOKEN_BUDGET (at least one), oldest first.
	`since`: skip messages up to this time (already covered by episode summaries).
	Includes the contact's older messages saved before Facebook chats had conversations."""
	if msg.conversation_id:
		qs = Message.objects.filter(
			Q(conversation_id=msg.conversation_id) | Q(contact_id=msg.contact_id, conversation__isnull=True)
		)
	else:
		qs = Message.objects.filter(contact=msg.contact)
	recent = list(
		qs.exclude(id=msg.id)
		.exclude(direction="out", status="failed")  # never delivered, so the customer never saw it
		.filter(created_at__lte=msg.created_at, **({"created_at__gt": since} if since else {}))
		.select_related("reply_to_message")
		.order_by("-created_at")[:HISTORY_MAX_MESSAGES]
	)
	history, used = [], 0
	for m in recent:  # newest first
		content = format_message_with_context(m)
		used += estimate_tokens(content)
		if history and used > HISTORY_TOKEN_BUDGET:
			break
		history.append(ChatMessage(role=("user" if m.direction == "in" else "assistant"), content=content))
	history.reverse()
	return history


def build_reply_context(project, knowledge_base: KnowledgeBase, conversation=None) -> tuple[Context, ToolDeps]:
	"""Agent setup for one reply: instructions, tools, and the customer's memory when there is a conversation."""
	custom_instructions = get_effective_instructions(str(project.id), str(knowledge_base.id))
	logger.info(f"Using {len(custom_instructions)} custom instructions")
	context = Context(
		knowledge_base_id=str(knowledge_base.id),
		vector_collection=KnowledgeBase.get_vector_collection_name(project, knowledge_base.name),
		custom_instructions=custom_instructions,
		api_domains=collect_api_domains(custom_instructions),
		memory_prompt=memory.memory_prompt(conversation) if conversation else "",
	)
	deps = ToolDeps(
		retriever_factory=get_retriever,
		retrieved_factory_for_crawler_url_context=lambda: get_web_crawl_retriever(str(knowledge_base.id), k=10)
	)
	return context, deps


def generate_agent_reply(msg: Message, knowledge_base: KnowledgeBase, question: Optional[str] = None) -> tuple[str, Context]:
	"""Run the RAG agent with history before `msg`. `question` defaults to `msg` formatted.
	Returns (reply text, Context with api_call_log/handoff_reason). DB reads only; call outside any transaction."""
	project = msg.project
	history = build_history(msg, since=memory.history_start(msg.conversation) if msg.conversation_id else None)
	current_message_text = question or format_message_with_context(msg)
	context, deps = build_reply_context(project, knowledge_base, msg.conversation if msg.conversation_id else None)

	logger.info(f"Generating reply for message {msg.id} with text: {current_message_text}")
	logger.info(f"History ({len(history)} msgs): {history[-3:]}")

	try:
		reply_text = generate_reply(current_message_text, context, deps, history=history, project_id=str(project.id))
	except Exception as exc:
		setattr(exc, "api_calls", context.api_call_log)  # lets the caller avoid re-sending POSTs on retry
		setattr(exc, "trace", context.trace)
		raise
	logger.info(f"Reply text: {reply_text}")
	return reply_text, context


def get_facebook_credential(msg: Message):
	"""Find the Facebook page credential that received `msg` (page id is the webhook recipient)."""
	creds = ChannelCredential.objects.filter(project=msg.project, channel="facebook", is_active=True)
	page_id = (msg.meta or {}).get("recipient", {}).get("id")
	return (page_id and creds.filter(credentials__page_id=page_id).first()) or creds.first()


@shared_task
def handle_incoming_message(message_id: str, knowledge_base_id: str) -> None:
	"""Legacy entry point for messages queued before channels_app.reply existed."""
	from .reply import process_reply
	from .service.conversation_service import ensure_contact_conversation

	msg = Message.objects.select_related("contact", "project").get(id=message_id)
	if not msg.conversation_id:
		msg.conversation = ensure_contact_conversation(msg.contact, KnowledgeBase.objects.get(id=knowledge_base_id))
		msg.save(update_fields=["conversation"])
	process_reply.delay(str(msg.conversation_id), str(msg.id))


@shared_task
def handle_ui_message(message_id: str) -> None:
	"""Legacy entry point for messages queued before channels_app.reply existed."""
	from .reply import process_reply

	msg = Message.objects.get(id=message_id)
	process_reply.delay(str(msg.conversation_id), str(msg.id))
