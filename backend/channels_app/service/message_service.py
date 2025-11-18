from typing import Optional, Dict, Any, List

from accounts.models import Project
from channels_app.models import Message, CustomerContact

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
	meta: Dict[str, Any] = None
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
		
	Returns:
		Created Message instance
	"""
	if attachment_info is None:
		attachment_info = []
	if meta is None:
		meta = {}
	
	msg = Message.objects.create(
		project=project,
		contact=contact,
		direction="in",
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
		f"has_attachments={has_attachments}, is_reply={is_reply}"
	)
	
	return msg


def create_outbound_message(
	project: Project,
	contact: CustomerContact,
	text: str,
	external_message_id: str = "",
	meta: Dict[str, Any] = None
) -> Message:
	"""
	Create an outbound message record.
	
	Args:
		project: The project this message belongs to
		contact: The customer contact receiving the message
		text: The message text content
		external_message_id: External platform message ID (if available)
		meta: Additional metadata dictionary
		
	Returns:
		Created Message instance
	"""
	if meta is None:
		meta = {}
	
	msg = Message.objects.create(
		project=project,
		contact=contact,
		direction="out",
		text=text,
		external_message_id=external_message_id,
		timestamp=None,
		reply_to_message=None,
		is_reply=False,
		has_attachments=False,
		attachment_info=[],
		meta=meta,
	)
	
	logger.info(f"Outbound message created: id={msg.id}, text={text[:50] if text else 'N/A'}")
	
	return msg

