from celery import shared_task
from django.db import transaction

from knowledge.models import KnowledgeBase
from .models import Message, ChannelCredential
from .facebook_client import send_text_message
from agents.rag import generate_reply, Context, ToolDeps, ChatMessage
from agents.vector_store import get_retriever


@shared_task
def handle_incoming_message(message_id: int, knowledge_base_id: str) -> None:
	with transaction.atomic():
		msg = Message.objects.select_related("project", "contact").get(id=message_id)
		knowledge_base = KnowledgeBase.objects.get(id=knowledge_base_id)
		project = msg.project
		contact = msg.contact
		# Build short conversation history (role-mapped) excluding current message (limit last 20)
		prev_qs = (
			Message.objects.filter(contact=contact, id__lt=msg.id)
			.order_by("-created_at")
			.values("direction", "text")[:20]
		)
		# Convert the previously retrieved queryset into a list
		# and reverse it so that the oldest message comes first (chronological order)
		prev = list(prev_qs)[::-1]
		history = [ChatMessage(role=("user" if m["direction"] == "in" else "assistant"), content=m["text"]) for m in prev]
		# Generate reply via RAG
		context = Context(vector_collection=KnowledgeBase.get_vector_collection_name(project, knowledge_base.name))
		deps = ToolDeps(retriever_factory=get_retriever)

		
		reply_text = generate_reply(msg.text, context, deps, history=history)
		# Persist outbound message record
		out_msg = Message.objects.create(
			project=project, contact=contact, direction="out", text=reply_text, meta={}
		)

	# Send via Facebook
	cred = ChannelCredential.objects.get(project=project,  channel="facebook")
	send_text_message(page_access_token=cred.page_access_token, recipient_psid=contact.external_id, text=reply_text)


