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
import secrets
from datetime import timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, Type, TypeVar
import json
from django.conf import settings
from django.db import models
from django.utils import timezone
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


@dataclass
class InstagramCredentials(BaseCredentials):
	"""
	Credentials required for Instagram Messaging integration.
	
	Fields:
	    instagram_account_id: The Instagram Business Account ID
	    page_id: Connected Facebook Page ID
	    page_access_token: Long-lived Page Access Token from Facebook Developer Console
	    meta_app_id: The Facebook App ID from Meta Developer Dashboard
	    meta_app_secret: The App Secret for webhook signature verification
	    verify_token: Custom token for webhook URL verification (you create this)
	
	How to obtain:
	    1. Convert your Instagram account to a Business or Creator account
	    2. Connect it to a Facebook Page
	    3. Create a Facebook App at developers.facebook.com
	    4. Add Instagram product to your app
	    5. Generate a Page Access Token and grant Instagram permissions
	    6. Get Instagram Account ID from the Graph API or Instagram settings
	    7. Copy App ID and App Secret from App Settings
	    8. Create a custom verify_token for webhook setup
	
	Note: Instagram messaging uses the same infrastructure as Facebook Messenger,
	      so the credentials are similar.
	"""
	instagram_account_id: str = ""
	page_id: str = ""
	page_access_token: str = ""
	meta_app_id: str = ""
	meta_app_secret: str = ""
	verify_token: str = ""
	
	def is_configured(self) -> bool:
		"""Check if all required Instagram credentials are provided."""
		return bool(
			self.instagram_account_id and 
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
				"name": "instagram_account_id",
				"label": "Instagram Account ID",
				"type": "text",
				"required": True,
				"description": "Your Instagram Business Account ID (found in Instagram settings or Graph API)",
				"placeholder": "17841400000000000"
			},
			{
				"name": "page_id",
				"label": "Facebook Page ID",
				"type": "text",
				"required": True,
				"description": "Connected Facebook Page ID",
				"placeholder": "123456789012345"
			},
			{
				"name": "page_access_token",
				"label": "Page Access Token",
				"type": "password",
				"required": True,
				"description": "Long-lived Page Access Token with Instagram permissions",
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
				"placeholder": "my_instagram_verify_token_123"
			},
		]


# Registry mapping platform names to their credential classes
PLATFORM_CREDENTIALS: Dict[str, Type[BaseCredentials]] = {
	"facebook": FacebookCredentials,
	"whatsapp": WhatsAppCredentials,
	"instagram": InstagramCredentials,
}

META_CHANNELS = ("facebook", "instagram")


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
		("instagram", "Instagram Messaging"),
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
				"is_active": True,
				"webhook_path": "/webhooks/facebook/",  # one Meta callback URL, routed on payload "object"
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
		("instagram", "Instagram"),
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
	name = models.CharField(
		max_length=255,
		help_text="User-defined name for this channel connection"
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


def meta_credentials(account_id: str):
	"""Active Facebook/Instagram credentials for a webhook recipient: a page id or an Instagram account id.
	An Instagram connection includes its linked page, so it also answers that page's Messenger messages."""
	return ChannelCredential.objects.filter(channel__in=META_CHANNELS, is_active=True).filter(
		models.Q(credentials__page_id=account_id) | models.Q(credentials__instagram_account_id=account_id)
	).order_by("channel")  # a dedicated facebook credential wins over an instagram one sharing its page


def whatsapp_credentials(phone_number_id: str):
	"""Active WhatsApp credentials for a webhook's metadata.phone_number_id."""
	return ChannelCredential.objects.filter(channel="whatsapp", is_active=True, credentials__phone_number_id=phone_number_id)


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
	display_name = models.CharField(max_length=255, blank=True, default="")
	conversation = models.OneToOneField(
		"Conversation",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="contact_link"
	)
	# Long-term memory (channels_app.memory): [{key, value, source_msg_id, updated_at}], customer-reported
	memory = models.JSONField(default=list, blank=True)
	memory_through = models.DateTimeField(null=True, blank=True)  # newest message already read into memory
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
	# Human handoff: set => bot paused until resumed or HANDOFF_AUTO_RESUME_HOURS pass
	handoff_at = models.DateTimeField(null=True, blank=True)
	handoff_reason = models.CharField(max_length=255, blank=True, default="")
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		indexes = [
			models.Index(fields=["project", "created_at"]),
			models.Index(fields=["contact"]),
		]

	def __str__(self):
		return f"{self.project.name} - {self.name or 'Unnamed'}"

	@property
	def channel(self) -> str:
		return self.contact.platform if self.contact_id else "self"

	def resumes_at(self):
		return self.handoff_at + timedelta(hours=settings.HANDOFF_AUTO_RESUME_HOURS) if self.handoff_at else None

	def is_paused(self, now=None) -> bool:
		return self.handoff_at is not None and (now or timezone.now()) < self.resumes_at()


class ConversationEpisode(models.Model):
	"""Summary of one stretch of a conversation, cut at EPISODE_GAP_HOURS of silence (channels_app.memory).
	Replies see the latest summaries instead of the raw messages they cover."""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="episodes")
	started_at = models.DateTimeField()
	ended_at = models.DateTimeField()  # created_at of the last message covered
	summary = models.TextField()
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["ended_at"]
		indexes = [models.Index(fields=["conversation", "ended_at"])]


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
	SENDER_CHOICES = [("customer", "Customer"), ("bot", "Bot"), ("staff", "Staff")]
	sender = models.CharField(max_length=16, choices=SENDER_CHOICES, default="customer")
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


class WebhookEvent(models.Model):
	"""
	Tracks all incoming webhook requests from messaging platforms.
	
	Purpose:
	    - Complete audit trail of all webhook deliveries
	    - Debugging webhook issues and payload inspection
	    - Monitoring platform health and delivery patterns
	    - Security auditing (IP addresses, signature validation)
	    - Analytics on event types and processing performance
	    - Replay failed webhooks for error recovery
	
	Data Stored:
	    - channel: Which channel/platform this webhook was for
	    - event_type: Type of webhook event (message, delivery, read, etc.)
	    - method: HTTP method (usually POST)
	    - path: Webhook endpoint path that received the request
	    - headers: HTTP request headers (for signature verification debugging)
	    - payload: Full webhook payload from platform
	    - query_params: URL query parameters if any
	    - remote_ip: IP address of the webhook sender
	    - user_agent: User agent string from the request
	    - status: Processing status (received, processing, success, failed, ignored)
	    - status_code: HTTP response code returned to platform
	    - response_body: Response body sent back to platform
	    - error_message: Error details if processing failed
	    - processing_time_ms: Time taken to process the webhook (in milliseconds)
	    - signature_valid: Whether webhook signature was valid (security)
	    - message: Link to created Message if this webhook created one
	    - is_duplicate: Whether this appears to be a duplicate delivery
	
	Event Types by Platform:
	    Facebook:
	        - "messages": New message from customer
	        - "messaging_postbacks": User clicked button/quick reply
	        - "message_deliveries": Message delivered confirmation
	        - "message_reads": Message read confirmation
	        - "messaging_handovers": Handover protocol events
	    
	    WhatsApp:
	        - "message": New message from customer
	        - "message_status": Status updates (sent, delivered, read, failed)
	        - "message_template_status": Template approval status
	
	Status Values:
	    - "received": Webhook received, queued for processing
	    - "processing": Currently being processed by worker
	    - "success": Successfully processed
	    - "failed": Processing failed with error
	    - "ignored": Intentionally ignored (e.g., delivery receipts if not tracking)
	    - "duplicate": Detected as duplicate, skipped processing
	
	Usage:
	    # Record incoming webhook
	    webhook_event = WebhookEvent.objects.create(
	        channel=channel_credential,
	        event_type="messages",
	        method="POST",
	        path="/webhooks/facebook/abc-123",
	        headers=dict(request.headers),
	        payload=request.json(),
	        remote_ip=get_client_ip(request),
	        user_agent=request.headers.get("User-Agent"),
	        status="received",
	        signature_valid=True
	    )
	    
	    try:
	        # Process webhook...
	        start_time = time.time()
	        message = process_webhook(webhook_event)
	        processing_time = (time.time() - start_time) * 1000
	        
	        webhook_event.status = "success"
	        webhook_event.message = message
	        webhook_event.processing_time_ms = processing_time
	        webhook_event.status_code = 200
	        webhook_event.save()
	    except Exception as e:
	        webhook_event.status = "failed"
	        webhook_event.error_message = str(e)
	        webhook_event.status_code = 500
	        webhook_event.save()
	
	Monitoring & Analytics:
	    - Track webhook delivery latency from platforms
	    - Identify patterns in failed webhooks
	    - Detect duplicate deliveries from platform retries
	    - Audit security events (invalid signatures)
	    - Measure processing performance
	
	Retention:
	    - Keep recent events (30-90 days) for debugging
	    - Archive or aggregate older events
	    - Consider separate table for failed events that need attention
	"""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	
	# Relationship
	channel = models.ForeignKey(
		ChannelCredential,
		on_delete=models.CASCADE,
		related_name="webhook_events",
		help_text="The channel this webhook was received for"
	)
	
	# Event identification
	event_type = models.CharField(
		max_length=100,
		default="",
		blank=True,
		help_text="Type of webhook event (e.g., 'messages', 'message_status')"
	)
	
	# Request details
	method = models.CharField(
		max_length=10,
		default="POST",
		help_text="HTTP method (GET, POST, etc.)"
	)
	path = models.CharField(
		max_length=500,
		default="",
		help_text="Webhook endpoint path"
	)
	headers = models.JSONField(
		default=dict,
		blank=True,
		help_text="HTTP request headers"
	)
	payload = models.JSONField(
		default=dict,
		blank=True,
		help_text="Full webhook payload from platform"
	)
	query_params = models.JSONField(
		default=dict,
		blank=True,
		help_text="URL query parameters"
	)
	
	# Request metadata
	remote_ip = models.GenericIPAddressField(
		null=True,
		blank=True,
		help_text="IP address of the webhook sender"
	)
	user_agent = models.CharField(
		max_length=500,
		default="",
		blank=True,
		help_text="User agent string from request"
	)
	
	# Processing status
	STATUS_CHOICES = [
		("received", "Received"),
		("processing", "Processing"),
		("success", "Success"),
		("failed", "Failed"),
		("ignored", "Ignored"),
		("duplicate", "Duplicate"),
	]
	status = models.CharField(
		max_length=20,
		choices=STATUS_CHOICES,
		default="received",
		db_index=True,
		help_text="Processing status"
	)
	
	# Response details
	status_code = models.PositiveSmallIntegerField(
		null=True,
		blank=True,
		help_text="HTTP response code returned to platform"
	)
	response_body = models.JSONField(
		default=dict,
		blank=True,
		help_text="Response body sent back to platform"
	)
	
	# Error tracking
	error_message = models.TextField(
		blank=True,
		default="",
		help_text="Error details if processing failed"
	)
	
	# Performance metrics
	processing_time_ms = models.PositiveIntegerField(
		null=True,
		blank=True,
		help_text="Time taken to process webhook in milliseconds"
	)
	
	# Security & validation
	signature_valid = models.BooleanField(
		null=True,
		blank=True,
		help_text="Whether webhook signature was valid"
	)
	
	# Related data
	message = models.ForeignKey(
		Message,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="webhook_events",
		help_text="Message created from this webhook"
	)
	
	# Duplicate detection
	is_duplicate = models.BooleanField(
		default=False,
		help_text="Whether this is a duplicate delivery"
	)
	
	# Timestamps
	created_at = models.DateTimeField(
		auto_now_add=True,
		help_text="When webhook was received"
	)
	processed_at = models.DateTimeField(
		null=True,
		blank=True,
		help_text="When processing completed"
	)
	
	class Meta:
		ordering = ['-created_at']
		indexes = [
			models.Index(fields=['channel', 'created_at']),
			models.Index(fields=['channel', 'status', 'created_at']),
			models.Index(fields=['event_type', 'created_at']),
			models.Index(fields=['status', 'created_at']),
			models.Index(fields=['signature_valid', 'status']),
		]
	
	def __str__(self):
		return f"{self.channel.name} - {self.event_type or 'webhook'} ({self.status})"
	
	@property
	def is_processed(self) -> bool:
		"""Check if webhook has been processed (success or failed)."""
		return self.status in ["success", "failed", "ignored", "duplicate"]
	
	@property
	def is_failed(self) -> bool:
		"""Check if webhook processing failed."""
		return self.status == "failed"
	
	@property
	def platform_name(self) -> str:
		"""Get the platform name from the related channel."""
		return self.channel.platform.name if self.channel and self.channel.platform else "Unknown"




def _new_webhook_secret() -> str:
	return secrets.token_hex(32)


class ProjectWebhook(models.Model):
	"""Outbound webhook endpoint for a project (handoff/resume/staff-reply events, see channels_app/webhooks.py)."""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="webhook")
	url = models.URLField(blank=True, default="")
	secret = models.CharField(max_length=64, default=_new_webhook_secret)
	is_active = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"{self.project.name} webhook ({'on' if self.is_active else 'off'})"


def _new_widget_key() -> str:
	return "wgt_" + secrets.token_urlsafe(18)


def normalize_origin(value: str) -> str:
	"""'https://Shop.com/page' → 'https://shop.com'; also 'https://*.shop.com'. Raises ValidationError otherwise."""
	import re
	from urllib.parse import urlsplit
	parts = urlsplit(value.strip().lower())
	try:
		port = parts.port
	except ValueError:
		port = None
		parts = parts._replace(scheme="")  # invalid port: reject below
	# Strict host charset: the result goes into a CSP header, so ";" or spaces must never get through
	if (
		parts.scheme not in ("http", "https") or parts.username or parts.password
		or not re.fullmatch(r"(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)*", parts.hostname or "")
	):
		raise ValidationError(f"Not an origin: {value!r}")
	return f"{parts.scheme}://{parts.hostname}" + (f":{port}" if port else "")


class Widget(models.Model):
	"""Website chat widget (docs/widget-architecture.md). Visitors are CustomerContact(platform="widget",
	external_id="<widget id>:<visitor uuid>") and go through the normal reply pipeline."""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="widgets")
	name = models.CharField(max_length=255)
	public_key = models.CharField(max_length=64, unique=True, default=_new_widget_key)  # an identifier, not a secret
	knowledge_base = models.ForeignKey("knowledge.KnowledgeBase", on_delete=models.PROTECT, related_name="widgets")
	is_active = models.BooleanField(default=True)
	allowed_origins = models.JSONField(default=list, blank=True)  # normalized by normalize_origin; CSP frame-ancestors
	appearance = models.JSONField(default=dict, blank=True)  # see channels_app.widget.APPEARANCE_DEFAULTS
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"{self.project.name} widget {self.name}"
