from typing import Optional, Dict, Any, List
import uuid

from accounts.models import Project
from channels_app.models import Message, CustomerContact, Conversation

import logging
logger = logging.getLogger(__name__)


def create_inbound_message(
	project: Project,
	contact: CustomerContact,
	text: str,
	external_message_id: str = "",
	timestamp: Optional[int] = None,
	reply_to_message: Optional[Message] = None,
	is_reply: bool = False,
	has_attachments: bool = False,
	attachment_info: List[Dict[str, Any]] = None,
	meta: Dict[str, Any] = None,
	conversation: Optional[Conversation] = None,
	status: str = "completed"
) -> Message:
	"""
	Create an inbound message record.
	
	Args:
		project: The project this message belongs to
		contact: The customer contact who sent the message
		text: The message text content
		external_message_id: External platform message ID (e.g., Facebook mid)
		timestamp: External platform timestamp
		reply_to_message: Reference to the message this is replying to (if applicable)
		is_reply: Whether this message is a reply
		has_attachments: Whether the message has attachments
		attachment_info: List of attachment metadata dictionaries
		meta: Full raw metadata from webhook/event
		conversation: Optional conversation this message belongs to
		status: Message status (default: "completed" for external messages, "pending" for UI)
		
	Returns:
		Created Message instance
	"""
	if attachment_info is None:
		attachment_info = []
	if meta is None:
		meta = {}
	
	# If conversation is not provided but contact has one, use it
	if conversation is None and contact.conversation:
		conversation = contact.conversation
	
	msg = Message.objects.create(
		project=project,
		contact=contact,
		conversation=conversation,
		direction="in",
		status=status,
		text=text,
		external_message_id=external_message_id,
		timestamp=timestamp,
		reply_to_message=reply_to_message,
		is_reply=is_reply,
		has_attachments=has_attachments,
		attachment_info=attachment_info,
		meta=meta,
	)
	
	logger.info(
		f"Inbound message created: id={msg.id}, text={text[:50] if text else 'N/A'}, "
		f"has_attachments={has_attachments}, is_reply={is_reply}, status={status}"
	)
	
	return msg


def create_ui_message(
	conversation: Conversation,
	text: str,
	attachment_info: list | None = None,
	meta: dict | None = None,
) -> Message:
	"""
	Create a message for UI conversations.
	
	Args:
		conversation: The conversation this message belongs to
		text: The message text content
		
	Returns:
		Created Message instance with status="pending" and agent_reply_id pre-generated
	"""
	if not conversation.contact:
		raise ValueError("Conversation must have a contact")
	
	# Generate UUID for future agent reply message
	agent_reply_uuid = uuid.uuid4()
	
	msg = Message.objects.create(
		project=conversation.project,
		contact=conversation.contact,
		conversation=conversation,
		direction="in",
		status="pending",
		text=text,
		external_message_id="",
		timestamp=None,
		reply_to_message=None,
		agent_reply_id=agent_reply_uuid,
		is_reply=False,
		has_attachments=bool(attachment_info),
		attachment_info=attachment_info or [],
		meta=meta or {},
	)
	
	logger.info(f"UI message created: id={msg.id}, conversation={conversation.id}, agent_reply_id={agent_reply_uuid}, text={text[:50]}")
	
	return msg


def create_outbound_message(
	project: Project,
	contact: CustomerContact,
	text: str,
	external_message_id: str = "",
	meta: Dict[str, Any] = None,
	conversation: Optional[Conversation] = None,
	id: Optional[uuid.UUID] = None,
	sender: str = "bot",
) -> Message:
	"""
	Create an outbound message record.
	
	Args:
		project: The project this message belongs to
		contact: The customer contact receiving the message
		text: The message text content
		external_message_id: External platform message ID (if available)
		meta: Additional metadata dictionary
		conversation: Optional conversation this message belongs to
		id: Optional UUID to use as message ID (for linking with agent_reply_id)
		sender: "bot" (default) or "staff"
		
	Returns:
		Created Message instance
	"""
	if meta is None:
		meta = {}
	
	# If conversation is not provided but contact has one, use it
	if conversation is None and contact.conversation:
		conversation = contact.conversation
	
	# Create message with optional ID
	create_kwargs = {
		"project": project,
		"contact": contact,
		"conversation": conversation,
		"direction": "out",
		"status": "completed",
		"text": text,
		"external_message_id": external_message_id,
		"timestamp": None,
		"reply_to_message": None,
		"agent_reply_id": None,  # Outbound messages don't have agent_reply_id
		"is_reply": False,
		"has_attachments": False,
		"attachment_info": [],
		"meta": meta,
		"sender": sender,
	}
	
	if id:
		create_kwargs["id"] = id
	
	msg = Message.objects.create(**create_kwargs)
	
	logger.info(f"Outbound message created: id={msg.id}, text={text[:50] if text else 'N/A'}")
	
	return msg

