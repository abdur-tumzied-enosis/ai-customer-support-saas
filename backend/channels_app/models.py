import uuid
from django.db import models
from django.core.exceptions import ValidationError
from accounts.models import Project


class ChannelCredential(models.Model):
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


class CustomerContact(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="contacts")
	platform = models.CharField(max_length=32, default="facebook")  # Can be "facebook", "self", etc.
	external_id = models.CharField(max_length=128)  # e.g., PSID or user ID for "self"
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


class Conversation(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="conversations")
	contact = models.ForeignKey(
		"CustomerContact",
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name="conversations"
	)  # Null for UI conversations
	name = models.CharField(max_length=255, blank=True, default="")  # User-provided name
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
		# Note: We don't enforce unique constraint here because UI contacts (platform="self")
		# should be able to have multiple conversations. The uniqueness check is handled
		# in application logic for external platform contacts only.

	def __str__(self):
		return f"{self.project.name} - {self.name or 'Unnamed'}"


class Message(models.Model):
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
	# Facebook message metadata
	external_message_id = models.CharField(max_length=255, blank=True, default="", db_index=True)  # Facebook message ID (mid)
	timestamp = models.BigIntegerField(null=True, blank=True)  # Facebook timestamp
	reply_to_message = models.ForeignKey(
		"self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
	)  # Reference to the message this is replying to
	agent_reply_id = models.UUIDField(null=True, blank=True)  # UUID of agent reply message
	is_reply = models.BooleanField(default=False)  # Whether this message is a reply
	has_attachments = models.BooleanField(default=False)  # Whether message has attachments
	attachment_info = models.JSONField(default=list, blank=True)  # List of attachment metadata
	# Full raw metadata from webhook
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


