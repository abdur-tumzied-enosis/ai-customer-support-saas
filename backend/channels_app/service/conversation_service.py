from typing import Optional
from django.db import transaction
from accounts.models import Project
from knowledge.models import KnowledgeBase
from channels_app.models import CustomerContact, Conversation

import logging
logger = logging.getLogger(__name__)


def get_or_create_self_contact(project: Project, user_id: Optional[str] = None) -> CustomerContact:
	"""
	Get or create a contact with platform="self" for UI conversations.
	
	Args:
		project: The project this contact belongs to
		user_id: Optional user ID to use as external_id. If not provided, generates one.
		
	Returns:
		CustomerContact instance with platform="self"
	"""
	if user_id is None:
		# Generate a unique external_id for this user/project combination
		user_id = f"user_{project.id}"
	
	contact, created = CustomerContact.objects.get_or_create(
		project=project,
		platform="self",
		external_id=user_id,
		defaults={}
	)
	if created:
		logger.info(f"Created self contact for project {project.slug}, user_id={user_id}")
	return contact


def create_conversation(
	project: Project,
	knowledge_base: Optional[KnowledgeBase] = None,
	name: Optional[str] = None,
	contact: Optional[CustomerContact] = None,
	user_id: Optional[str] = None
) -> Conversation:
	"""
	Create a new conversation.
	
	Args:
		project: The project this conversation belongs to
		knowledge_base: The knowledge base to use. If None, uses default KB.
		name: Optional name for the conversation
		contact: Optional contact. If None and creating UI conversation, creates self contact.
		user_id: User ID for creating self contact if contact is None
		
	Returns:
		Created Conversation instance
	"""
	if knowledge_base is None:
		knowledge_base = KnowledgeBase.get_default(project)
	
	# For UI conversations, create or get self contact
	if contact is None:
		contact = get_or_create_self_contact(project, user_id)
	
	# For UI conversations (platform="self"), allow multiple conversations per contact
	# For external platforms, enforce 1:1 relationship
	if contact.platform != "self":
		# Check if contact already has a conversation (1:1 relationship for external platforms)
		if contact.conversation:
			raise ValueError(f"Contact {contact.id} already has a conversation {contact.conversation.id}")
	
	conversation = Conversation.objects.create(
		project=project,
		contact=contact,
		name=name or "",
		knowledge_base=knowledge_base
	)
	
	# Only link contact to conversation for external platforms (1:1)
	# For UI conversations, contact can have multiple conversations
	if contact.platform != "self":
		contact.conversation = conversation
		contact.save(update_fields=["conversation"])
	
	logger.info(f"Created conversation {conversation.id} for project {project.slug}, contact={contact.id}, platform={contact.platform}")
	return conversation



def ensure_contact_conversation(contact: CustomerContact, knowledge_base: KnowledgeBase, name: str = "") -> Conversation:
	"""The single conversation of an external-platform contact, created on first use."""
	with transaction.atomic():
		contact = CustomerContact.objects.select_for_update().get(pk=contact.pk)  # two webhooks at once must not create two
		if contact.conversation_id:
			return contact.conversation
		return create_conversation(project=contact.project, knowledge_base=knowledge_base, name=name, contact=contact)
