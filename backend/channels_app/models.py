import uuid
from django.db import models
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
	platform = models.CharField(max_length=32, default="facebook")
	external_id = models.CharField(max_length=128)  # e.g., PSID
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ("project", "platform", "external_id")
		indexes = [models.Index(fields=["platform", "external_id"])]


class Message(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	DIRECTION_CHOICES = [("in", "Inbound"), ("out", "Outbound")]
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="messages")
	contact = models.ForeignKey(CustomerContact, on_delete=models.CASCADE, related_name="messages")
	direction = models.CharField(max_length=8, choices=DIRECTION_CHOICES)
	text = models.TextField(blank=True, default="")
	meta = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		indexes = [models.Index(fields=["project", "created_at"])]


