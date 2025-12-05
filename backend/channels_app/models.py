"""
Channels App Models
===================

This module defines the core models for managing messaging channels, customer contacts,
conversations, and messages within the Auto Magic Pro platform.

Model Hierarchy:
    Project (from accounts)
        └── Platform (defines available messaging platforms and their credential schemas)
        └── ChannelCredential (API credentials for external platforms)
        └── CustomerContact (represents a customer from any platform)
            └── Conversation (a chat thread with a customer)
                └── Message (individual messages in a conversation)
"""

import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, Type, TypeVar
import json
from django.db import models
from django.core.exceptions import ValidationError
from accounts.models import Project


# =============================================================================
# Credential Dataclasses
# =============================================================================
# These dataclasses provide type-safe access to platform-specific credentials.
# They are used to validate and serialize/deserialize credential JSON data.

@dataclass
class BaseCredentials:
	"""
	Base class for all platform credentials.
	Provides common functionality for serialization/deserialization.
	"""
	
	def to_dict(self) -> Dict[str, Any]:
		"""Convert credentials to a dictionary for JSON storage."""
		return asdict(self)
	
	@classmethod
	def from_dict(cls, data: Dict[str, Any]) -> "BaseCredentials":
		"""Create credentials instance from dictionary."""
		# Filter out any extra keys not in the dataclass
		valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
		filtered_data = {k: v for k, v in data.items() if k in valid_keys}
		return cls(**filtered_data)
	
	def is_configured(self) -> bool:
		"""Check if all required credentials are provided."""
		raise NotImplementedError("Subclasses must implement is_configured()")
	
	@classmethod
	def get_required_fields(cls) -> list[dict]:
		"""
		Return list of field definitions for UI rendering.
		Each field dict contains: name, label, type, required, description
		"""
		raise NotImplementedError("Subclasses must implement get_required_fields()")


@dataclass
class FacebookCredentials(BaseCredentials):
	"""
	Credentials required for Facebook Messenger integration.
	
	Fields:
	    page_id: The Facebook Page ID that will receive messages
	    page_access_token: Long-lived Page Access Token from Facebook Developer Console
	    meta_app_id: The Facebook App ID from Meta Developer Dashboard
	    meta_app_secret: The App Secret for webhook signature verification
	    verify_token: Custom token for webhook URL verification (you create this)
	
	How to obtain:
	    1. Create a Facebook App at developers.facebook.com
	    2. Add Messenger product to your app
	    3. Connect your Facebook Page and generate a Page Access Token
	    4. Copy App ID and App Secret from App Settings
	    5. Create a custom verify_token for webhook setup
	"""
	page_id: str = ""
	page_access_token: str = ""
	meta_app_id: str = ""
	meta_app_secret: str = ""
	verify_token: str = ""
	
	def is_configured(self) -> bool:
		"""Check if all required Facebook credentials are provided."""
		return bool(
			self.page_id and 
			self.page_access_token and 
			self.meta_app_id and 
			self.meta_app_secret and 
			self.verify_token
		)
	
	@classmethod
	def get_required_fields(cls) -> list[dict]:
		return [
			{
				"name": "page_id",
				"label": "Page ID",
				"type": "text",
				"required": True,
				"description": "Your Facebook Page ID (found in Page Settings > Page Info)",
				"placeholder": "123456789012345"
			},
			{
				"name": "page_access_token",
				"label": "Page Access Token",
				"type": "password",
				"required": True,
				"description": "Long-lived Page Access Token from Meta Developer Console",
				"placeholder": "EAAxxxxxxx..."
			},
			{
				"name": "meta_app_id",
				"label": "App ID",
				"type": "text",
				"required": True,
				"description": "Facebook App ID from Meta Developer Dashboard",
				"placeholder": "1234567890123456"
			},
			{
				"name": "meta_app_secret",
				"label": "App Secret",
				"type": "password",
				"required": True,
				"description": "App Secret for webhook signature verification",
				"placeholder": "abcdef123456..."
			},
			{
				"name": "verify_token",
				"label": "Verify Token",
				"type": "text",
				"required": True,
				"description": "Custom token for webhook verification (create your own)",
				"placeholder": "my_custom_verify_token_123"
			},
		]


@dataclass
class WhatsAppCredentials(BaseCredentials):
	"""
	Credentials required for WhatsApp Business API integration.
	
	Fields:
	    phone_number_id: WhatsApp Business Phone Number ID
	    business_account_id: WhatsApp Business Account ID
	    access_token: Permanent access token for WhatsApp Cloud API
	    verify_token: Custom token for webhook URL verification
	    app_id: Meta App ID (same app can handle both FB and WhatsApp)
	    app_secret: Meta App Secret for webhook signature verification
	
	How to obtain:
	    1. Create a Meta Business Account and WhatsApp Business Account
	    2. Create a Meta App and add WhatsApp product
	    3. Register a phone number or use the test number
	    4. Generate a permanent access token (System User token recommended)
	    5. Get Phone Number ID and Business Account ID from WhatsApp Manager
	"""
	phone_number_id: str = ""
	business_account_id: str = ""
	access_token: str = ""
	verify_token: str = ""
	app_id: str = ""
	app_secret: str = ""
	
	def is_configured(self) -> bool:
		"""Check if all required WhatsApp credentials are provided."""
		return bool(
			self.phone_number_id and 
			self.business_account_id and 
			self.access_token and 
			self.verify_token and
			self.app_id and
			self.app_secret
		)
	
	@classmethod
	def get_required_fields(cls) -> list[dict]:
		return [
			{
				"name": "phone_number_id",
				"label": "Phone Number ID",
				"type": "text",
				"required": True,
				"description": "WhatsApp Business Phone Number ID from Meta Business Suite",
				"placeholder": "123456789012345"
			},
			{
				"name": "business_account_id",
				"label": "Business Account ID",
				"type": "text",
				"required": True,
				"description": "WhatsApp Business Account ID (WABA ID)",
				"placeholder": "123456789012345"
			},
			{
				"name": "access_token",
				"label": "Access Token",
				"type": "password",
				"required": True,
				"description": "Permanent access token for WhatsApp Cloud API",
				"placeholder": "EAAxxxxxxx..."
			},
			{
				"name": "app_id",
				"label": "App ID",
				"type": "text",
				"required": True,
				"description": "Meta App ID from Developer Dashboard",
				"placeholder": "1234567890123456"
			},
			{
				"name": "app_secret",
				"label": "App Secret",
				"type": "password",
				"required": True,
				"description": "App Secret for webhook signature verification",
				"placeholder": "abcdef123456..."
			},
			{
				"name": "verify_token",
				"label": "Verify Token",
				"type": "text",
				"required": True,
				"description": "Custom token for webhook verification (create your own)",
				"placeholder": "my_whatsapp_verify_token_123"
			},
		]


# Registry mapping platform names to their credential classes
PLATFORM_CREDENTIALS: Dict[str, Type[BaseCredentials]] = {
	"facebook": FacebookCredentials,
	"whatsapp": WhatsAppCredentials,
}


def get_credentials_class(platform: str) -> Type[BaseCredentials]:
	"""Get the credentials dataclass for a given platform."""
	if platform not in PLATFORM_CREDENTIALS:
		raise ValueError(f"Unknown platform: {platform}. Supported: {list(PLATFORM_CREDENTIALS.keys())}")
	return PLATFORM_CREDENTIALS[platform]


# =============================================================================
# Platform Model
# =============================================================================

class Platform(models.Model):
	"""
	Defines available messaging platforms and their configuration.
	
	Purpose:
	    - Centralized registry of supported messaging platforms
	    - Store platform metadata (name, icon, description, status)
	    - Enable/disable platforms dynamically without code changes
	    - Provide webhook URL patterns and API base URLs
	
	Data Stored:
	    - name: Unique platform identifier (e.g., "facebook", "whatsapp")
	    - display_name: Human-readable name for UI display
	    - description: Brief description of the platform
	    - icon: Icon identifier or URL for UI display
	    - is_active: Whether this platform is currently available
	    - webhook_path: URL path pattern for incoming webhooks
	    - api_base_url: Base URL for the platform's API
	    - documentation_url: Link to setup documentation
	    - credential_schema: JSON schema describing required credentials (auto-generated from dataclass)
	
	Usage:
	    - Query active platforms for UI display
	    - Get credential schema for dynamic form generation
	    - Route webhooks based on platform configuration
	
	Note:
	    The credential_schema is automatically populated from the corresponding
	    dataclass (FacebookCredentials, WhatsAppCredentials, etc.) using get_required_fields().
	"""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	
	PLATFORM_CHOICES = [
		("facebook", "Facebook Messenger"),
		("whatsapp", "WhatsApp Business"),
	]
	
	name = models.CharField(
		max_length=32, 
		choices=PLATFORM_CHOICES, 
		unique=True,
		help_text="Unique platform identifier"
	)
	display_name = models.CharField(
		max_length=64,
		help_text="Human-readable name for UI display"
	)
	description = models.TextField(
		blank=True, 
		default="",
		help_text="Brief description of the platform and its capabilities"
	)
	icon = models.CharField(
		max_length=128, 
		blank=True, 
		default="",
		help_text="Icon class name (e.g., 'facebook', 'whatsapp') or URL"
	)
	is_active = models.BooleanField(
		default=True,
		help_text="Whether this platform is available for new connections"
	)
	webhook_path = models.CharField(
		max_length=128, 
		blank=True, 
		default="",
		help_text="URL path for incoming webhooks (e.g., '/webhooks/facebook')"
	)
	api_base_url = models.URLField(
		blank=True, 
		default="",
		help_text="Base URL for the platform's messaging API"
	)
	documentation_url = models.URLField(
		blank=True, 
		default="",
		help_text="Link to platform setup documentation"
	)
	credential_schema = models.JSONField(
		default=list,
		blank=True,
		help_text="JSON schema of required credential fields (auto-populated from dataclass)"
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["display_name"]
		verbose_name = "Platform"
		verbose_name_plural = "Platforms"

	def __str__(self):
		status = "✓" if self.is_active else "✗"
		return f"{self.display_name} [{status}]"
	
	def save(self, *args, **kwargs):
		"""Auto-populate credential_schema from dataclass on save."""
		name_str = str(self.name)
		if name_str in PLATFORM_CREDENTIALS:
			cred_class = PLATFORM_CREDENTIALS[name_str]
			self.credential_schema = cred_class.get_required_fields()
		super().save(*args, **kwargs)
	
	def get_credentials_class(self) -> Type[BaseCredentials]:
		"""Get the dataclass for this platform's credentials."""
		return get_credentials_class(str(self.name))
	
	@classmethod
	def get_or_create_defaults(cls) -> list[str]:
		"""
		Create default platform entries if they don't exist.
		Call this during migrations or app initialization.
		
		Returns:
		    List of platform names that were created.
		"""
		defaults = [
			{
				"name": "facebook",
				"display_name": "Facebook Messenger",
				"description": "Connect your Facebook Page to receive and respond to Messenger conversations with AI-powered responses.",
				"icon": "facebook",
				"is_active": True,
				"webhook_path": "/webhooks/facebook/",
				"api_base_url": "https://graph.facebook.com/v18.0",
				"documentation_url": "https://developers.facebook.com/docs/messenger-platform",
			},
			{
				"name": "whatsapp",
				"display_name": "WhatsApp Business",
				"description": "Connect your WhatsApp Business account to handle customer conversations via the WhatsApp Cloud API.",
				"icon": "whatsapp",
				"is_active": False,  # Coming soon
				"webhook_path": "/webhooks/whatsapp/",
				"api_base_url": "https://graph.facebook.com/v18.0",
				"documentation_url": "https://developers.facebook.com/docs/whatsapp/cloud-api",
			},
		]
		
		created: list[str] = []
		for platform_data in defaults:
			platform, was_created = Platform.objects.get_or_create(
				name=platform_data["name"],
				defaults=platform_data
			)
			if was_created:
				created.append(str(platform.name))
		
		return created


class ChannelCredential(models.Model):
	"""
	Stores API credentials and configuration for external messaging platforms.
	
	Purpose:
	    - Securely store authentication tokens for platforms like Facebook Messenger
	    - Link a messaging channel to a specific project and knowledge base
	    - Enable webhook verification and API communication with external platforms
	
	Data Stored:
	    - project: The project this channel belongs to
	    - platform: Reference to the Platform model
	    - knowledge_base: Optional KB to use for AI responses on this channel
	    - channel: Platform type string (for quick access without join)
	    - credentials: JSON field storing platform-specific credentials
	    - is_active: Whether this channel connection is active
	    
	    Legacy fields (deprecated, use credentials JSON instead):
	    - page_id, page_access_token, meta_app_id, meta_app_secret, verify_token
	
	Usage:
	    - Retrieved when processing incoming webhooks to validate signatures
	    - Used when sending outbound messages to customers
	    - Configured via the Channels page in the dashboard
	    - Use get_credentials() method for type-safe credential access
	
	Example:
	    # Get typed credentials
	    cred = channel_credential.get_credentials()
	    if isinstance(cred, FacebookCredentials):
	        print(cred.page_access_token)
	"""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	CHANNEL_CHOICES = [
		("facebook", "Facebook"),
		("whatsapp", "WhatsApp"),
	]
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="channel_credentials")
	platform = models.ForeignKey(
		Platform,
		on_delete=models.PROTECT,
		null=True,
		blank=True,
		related_name="credentials",
		help_text="Reference to the platform configuration"
	)
	knowledge_base = models.ForeignKey(
		"knowledge.KnowledgeBase",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="kb_credentials"
	)
	channel = models.CharField(max_length=32, choices=CHANNEL_CHOICES, default="facebook")
	
	# JSON field for platform-specific credentials
	credentials = models.JSONField(
		default=dict,
		blank=True,
		help_text="Platform-specific credential data (structure varies by platform)"
	)
	
	is_active = models.BooleanField(
		default=True,
		help_text="Whether this channel connection is currently active"
	)
	
	# Legacy fields - kept for backward compatibility during migration
	# TODO: Remove after migrating existing data to credentials JSON
	page_id = models.CharField(max_length=128, blank=True, default="")
	page_access_token = models.TextField(blank=True, default="")
	meta_app_id = models.CharField(max_length=128, blank=True, default="")
	meta_app_secret = models.CharField(max_length=256, blank=True, default="")
	verify_token = models.CharField(max_length=128, blank=True, default="")
	
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		indexes = [
			models.Index(fields=["channel"]),
			models.Index(fields=["project", "channel"]),
		]
		verbose_name = "Channel Credential"
		verbose_name_plural = "Channel Credentials"

	def __str__(self):
		status = "✓" if self.is_configured() else "○"
		channel_display = dict(self.CHANNEL_CHOICES).get(str(self.channel), self.channel)
		return f"{self.project.name} - {channel_display} [{status}]"
	
	def get_credentials(self) -> BaseCredentials:
		"""
		Get typed credentials object for this channel.
		
		Returns:
		    Appropriate credentials dataclass instance (FacebookCredentials, WhatsAppCredentials, etc.)
		
		Example:
		    creds = channel.get_credentials()
		    if creds.is_configured():
		        # Safe to use credentials
		        token = creds.page_access_token  # Type-safe access
		"""
		channel_str = str(self.channel)
		cred_class = get_credentials_class(channel_str)
		
		# If credentials JSON is populated, use it
		if self.credentials:
			creds_dict: Dict[str, Any] = self.credentials if isinstance(self.credentials, dict) else {}
			return cred_class.from_dict(creds_dict)
		
		# Fallback to legacy fields for Facebook
		if channel_str == "facebook":
			return FacebookCredentials(
				page_id=str(self.page_id),
				page_access_token=str(self.page_access_token),
				meta_app_id=str(self.meta_app_id),
				meta_app_secret=str(self.meta_app_secret),
				verify_token=str(self.verify_token),
			)
		
		# Return empty credentials for other platforms
		return cred_class()
	
	def set_credentials(self, credentials: BaseCredentials) -> None:
		"""
		Set credentials from a dataclass instance.
		
		Args:
		    credentials: Credentials dataclass instance
		
		Example:
		    fb_creds = FacebookCredentials(
		        page_id="123",
		        page_access_token="token...",
		        ...
		    )
		    channel.set_credentials(fb_creds)
		    channel.save()
		"""
		self.credentials = credentials.to_dict()
	
	def is_configured(self) -> bool:
		"""Check if all required credentials are provided."""
		return self.get_credentials().is_configured()
	
	def migrate_legacy_credentials(self) -> bool:
		"""
		Migrate legacy field-based credentials to JSON format.
		Returns True if migration was performed.
		"""
		if self.channel == "facebook" and self.page_id and not self.credentials:
			self.credentials = {
				"page_id": self.page_id,
				"page_access_token": self.page_access_token,
				"meta_app_id": self.meta_app_id,
				"meta_app_secret": self.meta_app_secret,
				"verify_token": self.verify_token,
			}
			return True
		return False


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


