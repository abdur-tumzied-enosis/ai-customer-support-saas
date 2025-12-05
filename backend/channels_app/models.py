"""
Channels App Models
===================

This module defines the core models for managing messaging channels, customer contacts,
conversations, and messages within the Auto Magic Pro platform.

Model Hierarchy:
    Project (from accounts)
        └── ChannelCredential (API credentials for external platforms)
        └── CustomerContact (represents a customer from any platform)
            └── Conversation (a chat thread with a customer)
                └── Message (individual messages in a conversation)
"""

import uuid
from django.db import models
from django.core.exceptions import ValidationError
from accounts.models import Project


class ChannelCredential(models.Model):
	"""
	Stores API credentials and configuration for external messaging platforms.
	
	Purpose:
	    - Securely store authentication tokens for platforms like Facebook Messenger
	    - Link a messaging channel to a specific project and knowledge base
	    - Enable webhook verification and API communication with external platforms
	
	Data Stored:
	    - project: The project this channel belongs to
	    - knowledge_base: Optional KB to use for AI responses on this channel
	    - channel: Platform type (currently "facebook", extensible to others)
	    - page_id: Facebook Page ID that receives messages
	    - page_access_token: Long-lived token for sending messages via Graph API
	    - meta_app_id: Facebook App ID for the integration
	    - meta_app_secret: App secret for signature verification
	    - verify_token: Custom token used for webhook URL verification
	
	Usage:
	    - Retrieved when processing incoming webhooks to validate signatures
	    - Used when sending outbound messages to customers
	    - Configured via the Channels page in the dashboard
	"""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	CHANNEL_CHOICES = [
		("facebook", "Facebook"),
	]
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="channel_credentials")
	knowledge_base = models.ForeignKey(
		"knowledge.KnowledgeBase",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="credentials"
	)
	channel = models.CharField(max_length=32, choices=CHANNEL_CHOICES, default="facebook")
	# Facebook specific fields
	page_id = models.CharField(max_length=128, blank=True, default="")
	page_access_token = models.TextField(blank=True, default="")
	meta_app_id = models.CharField(max_length=128, blank=True, default="")
	meta_app_secret = models.CharField(max_length=256, blank=True, default="")
	verify_token = models.CharField(max_length=128, blank=True, default="")
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		indexes = [models.Index(fields=["channel", "page_id"])]

	def __str__(self):
		return f"{self.project.name} - {self.channel} ({self.page_id or 'not configured'})"


class CustomerContact(models.Model):
	"""
	Represents a customer who has interacted with the system via any channel.
	
	Purpose:
	    - Uniquely identify customers across different messaging platforms
	    - Link all messages and conversations from the same customer together
	    - Support both external platform users (Facebook) and internal UI users
	
	Data Stored:
	    - project: The project this contact belongs to
	    - platform: Source platform ("facebook" for Messenger, "self" for dashboard UI)
	    - external_id: Platform-specific user identifier
	        - For Facebook: Page-Scoped ID (PSID) - unique per user per page
	        - For UI: Generated UUID for dashboard test conversations
	    - conversation: One-to-one link to the contact's active conversation
	
	Usage:
	    - Created automatically when a new user sends their first message
	    - Used to lookup existing conversations for returning customers
	    - Enables customer history tracking across multiple interactions
	
	Constraints:
	    - Unique together: (project, platform, external_id) - one contact per user per project
	"""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="contacts")
	platform = models.CharField(max_length=32, default="facebook")
	external_id = models.CharField(max_length=128)
	conversation = models.OneToOneField(
		"Conversation",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="contact_link"
	)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ("project", "platform", "external_id")
		indexes = [models.Index(fields=["platform", "external_id"])]

	def __str__(self):
		return f"{self.platform}:{self.external_id} ({self.project.name})"


class Conversation(models.Model):
	"""
	Represents a chat thread/conversation between a customer and the AI agent.
	
	Purpose:
	    - Group related messages into a single conversation thread
	    - Associate conversations with a specific knowledge base for context
	    - Support both external (Facebook) and internal (dashboard) conversations
	
	Data Stored:
	    - project: The project this conversation belongs to
	    - contact: The customer contact (null for some UI-initiated conversations)
	    - name: Optional user-provided name for the conversation (UI only)
	    - knowledge_base: The KB used for RAG-based AI responses in this conversation
	
	Usage:
	    - External platforms: One conversation per contact (ongoing chat history)
	    - Dashboard UI: Multiple named conversations for testing different scenarios
	    - Messages are retrieved by conversation for chat history display
	    - Knowledge base determines what documents/context the AI can access
	
	Note:
	    - External contacts (Facebook) have one conversation per contact
	    - UI contacts (platform="self") can have multiple conversations for testing
	"""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="conversations")
	contact = models.ForeignKey(
		"CustomerContact",
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name="conversations"
	)
	name = models.CharField(max_length=255, blank=True, default="")
	knowledge_base = models.ForeignKey(
		"knowledge.KnowledgeBase",
		on_delete=models.PROTECT,
		related_name="conversations"
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		indexes = [
			models.Index(fields=["project", "created_at"]),
			models.Index(fields=["contact"]),
		]

	def __str__(self):
		return f"{self.project.name} - {self.name or 'Unnamed'}"


class Message(models.Model):
	"""
	Represents a single message within a conversation.
	
	Purpose:
	    - Store all inbound (customer) and outbound (AI agent) messages
	    - Track message processing status for async AI response generation
	    - Preserve metadata from external platforms for debugging/auditing
	
	Data Stored:
	    - project: The project this message belongs to
	    - contact: The customer who sent/received this message
	    - conversation: The conversation thread this message is part of
	    - direction: "in" (customer to agent) or "out" (agent to customer)
	    - status: Processing state for inbound messages
	        - "pending": Received, waiting for AI processing
	        - "processing": AI is generating a response
	        - "completed": Response sent successfully
	        - "failed": Error occurred during processing
	    - text: The actual message content
	    - external_message_id: Platform's message ID (e.g., Facebook "mid")
	    - timestamp: Platform's timestamp (Facebook uses milliseconds)
	    - reply_to_message: Self-referential FK for threaded replies
	    - agent_reply_id: Links inbound message to its AI-generated reply
	    - is_reply: Flag indicating if this is an AI response
	    - has_attachments: Whether message includes files/images
	    - attachment_info: JSON array of attachment metadata [{type, url, ...}]
	    - meta: Full raw webhook payload for debugging
	
	Usage:
	    - Inbound: Created when webhook receives customer message, triggers AI task
	    - Outbound: Created when AI generates response, sent to customer
	    - Status tracking enables async processing with Celery tasks
	    - Chat history retrieved ordered by created_at for UI display
	
	Message Flow:
	    1. Customer sends message → Webhook creates Message(direction="in", status="pending")
	    2. Celery task picks up → status="processing" → AI generates response
	    3. Response created → Message(direction="out", is_reply=True)
	    4. Original message updated → status="completed", agent_reply_id set
	"""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	DIRECTION_CHOICES = [("in", "Inbound"), ("out", "Outbound")]
	STATUS_CHOICES = [
		("pending", "Pending"),
		("processing", "Processing"),
		("completed", "Completed"),
		("failed", "Failed"),
	]
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="messages")
	contact = models.ForeignKey(CustomerContact, on_delete=models.CASCADE, related_name="messages")
	conversation = models.ForeignKey(
		Conversation,
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name="messages"
	)
	direction = models.CharField(max_length=8, choices=DIRECTION_CHOICES)
	status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="completed")
	text = models.TextField(blank=True, default="")
	# External platform metadata
	external_message_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
	timestamp = models.BigIntegerField(null=True, blank=True)
	# Reply tracking
	reply_to_message = models.ForeignKey(
		"self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
	)
	agent_reply_id = models.UUIDField(null=True, blank=True)
	is_reply = models.BooleanField(default=False)
	# Attachment handling
	has_attachments = models.BooleanField(default=False)
	attachment_info = models.JSONField(default=list, blank=True)
	# Raw webhook data
	meta = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		indexes = [
			models.Index(fields=["project", "created_at"]),
			models.Index(fields=["contact", "created_at"]),
			models.Index(fields=["conversation", "created_at"]),
			models.Index(fields=["status"]),
			models.Index(fields=["external_message_id"]),
		]

	def __str__(self):
		direction_label = "→" if self.direction == "out" else "←"
		text_str = str(self.text) if self.text else ""
		return f"{direction_label} {text_str[:50]}..." if len(text_str) > 50 else f"{direction_label} {text_str}"


