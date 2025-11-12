import uuid
from django.db import models
from accounts.models import Project


class UsageCounter(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="usage_counters")
	period_start = models.DateTimeField()
	period_end = models.DateTimeField()
	input_tokens = models.PositiveBigIntegerField(default=0)
	output_tokens = models.PositiveBigIntegerField(default=0)
	embedding_tokens = models.PositiveBigIntegerField(default=0)
	last_event_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		unique_together = ("project", "period_start", "period_end")


class UsageEvent(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	KIND_CHOICES = [
		("chat_in", "Chat Input"),
		("chat_out", "Chat Output"),
		("embed", "Embedding"),
	]
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="usage_events")
	kind = models.CharField(max_length=16, choices=KIND_CHOICES)
	tokens = models.PositiveIntegerField(default=0)
	metadata = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)


