from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models

MODEL_CONFIG_CACHE_KEY = "agents:model_config"
ROLES = ("reply", "fallback", "memory", "transcription", "vision")


class ToolExecution(models.Model):
	"""One side-effecting agent tool call for one inbound message. A reply that is retried or recovered after a crash
	finds the row and reuses the stored result instead of running the tool again (agents.idempotency.once)."""
	STATUS_CHOICES = [("executing", "Executing"), ("completed", "Completed"), ("failed", "Failed"), ("unknown", "Unknown")]
	message_id = models.UUIDField()  # the inbound message the reply answers (channels_app.Message)
	operation_key = models.CharField(max_length=64)  # sha256 of tool name + arguments: same call, same key
	tool_name = models.CharField(max_length=100)
	status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="executing")
	input_payload = models.JSONField(default=dict)
	result = models.TextField(blank=True)
	error = models.TextField(blank=True)
	started_at = models.DateTimeField(auto_now_add=True)
	completed_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		constraints = [models.UniqueConstraint(fields=["message_id", "operation_key"], name="unique_tool_operation")]

	def __str__(self):
		return f"{self.tool_name} {self.status} ({self.message_id})"


class AIModel(models.Model):
	"""A model the admin can pick in ModelConfig. Prices price providers that don't report a charged amount (Google)."""
	KIND_CHOICES = [("text", "Text"), ("transcription", "Transcription"), ("embedding", "Embedding"), ("rerank", "Rerank")]
	PROVIDER_CHOICES = [("google", "Google"), ("openrouter", "OpenRouter")]
	kind = models.CharField(max_length=16, choices=KIND_CHOICES, default="text")
	provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES)
	name = models.CharField(max_length=200, help_text="Provider's model id, e.g. deepseek/deepseek-v4.1-flash or gemini-3.8-flash")
	input_price_per_mtok = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, help_text="USD per 1M input tokens")
	output_price_per_mtok = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, help_text="USD per 1M output tokens")
	image_price_per_mtok = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, help_text="USD per 1M image tokens")
	notes = models.TextField(blank=True)
	input_modalities = models.JSONField(
		default=list, blank=True,
		help_text="From OpenRouter architecture.input_modalities; Google text models are set on save.",
	)
	supports_vision = models.BooleanField(
		default=False, editable=False,
		help_text="Derived: input_modalities contains image. Read-only; not set by hand.",
	)

	class Meta:
		unique_together = ("provider", "name")
		ordering = ("kind", "provider", "name")
		verbose_name = "AI model"

	def __str__(self):
		return f"{self.name} ({self.get_provider_display()})"

	def save(self, *args, **kwargs):
		if self.provider == "google" and self.kind == "text":
			self.input_modalities = ["text", "image"]  # Gemini text models accept images; no provider list says so
		self.supports_vision = "image" in (self.input_modalities or [])
		super().save(*args, **kwargs)
		cache.delete(MODEL_CONFIG_CACHE_KEY)


def _role_model(kind):
	return models.ForeignKey(
		AIModel, null=True, blank=True, on_delete=models.PROTECT, related_name="+", limit_choices_to={"kind": kind},
		help_text="Empty → env settings",
	)


class ModelConfig(models.Model):
	"""The one global row picking which AIModel does each job. Unset roles fall back to env (LLM_PROVIDER, LLM_MODEL, ...)."""
	reply_model = _role_model("text")
	fallback_model = _role_model("text")
	memory_model = _role_model("text")
	transcription_model = _role_model("transcription")  # not read anywhere yet: audio messages come later
	vision_model = models.ForeignKey(
		AIModel, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
		limit_choices_to={"kind": "text", "supports_vision": True},
		help_text="Used on photo turns when the reply model cannot see images. Empty → photo turns are answered without the photo.",
	)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = verbose_name_plural = "Model config"

	def __str__(self):
		return "Model config"

	def clean(self):
		super().clean()
		if self.vision_model_id and not self.vision_model.supports_vision:
			raise ValidationError({"vision_model": "Pick a model whose input modalities include image."})

	def save(self, *args, **kwargs):
		self.pk = 1  # singleton: a second create() updates the one row
		kwargs.pop("force_insert", None)
		super().save(*args, **kwargs)
		cache.delete(MODEL_CONFIG_CACHE_KEY)

	def roles(self) -> dict:
		"""{role: (provider, name)} for the roles that are set."""
		return {r: (m.provider, m.name) for r in ROLES if (m := getattr(self, f"{r}_model"))}
