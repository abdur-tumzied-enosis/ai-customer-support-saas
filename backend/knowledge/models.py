import uuid
import os
from datetime import timedelta
from enum import Enum
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from accounts.models import Project
from agents.image_search import delete_images
from core.storage import delete_keys, thumb_key


class IngestionJobStatus(str, Enum):
	"""Enum for IngestionJob status values"""
	PENDING = "pending"
	PROCESSING = "processing"
	COMPLETED = "completed"
	FAILED = "failed"
	
	@classmethod
	def choices(cls):
		"""Return choices tuple for Django model field"""
		return [(status.value, status.value.replace('_', ' ').title()) for status in cls]


class DocumentFileType(str, Enum):
	"""Enum for supported document file types"""
	PDF = "pdf"
	TXT = "txt"
	MD = "md"
	
	@classmethod
	def extensions(cls):
		"""Return list of file extensions"""
		return [f".{ft.value}" for ft in cls]
	
	@classmethod
	def mime_types(cls):
		"""Return dict mapping file types to MIME types"""
		return {
			cls.PDF: ["application/pdf"],
			cls.TXT: ["text/plain"],
			cls.MD: ["text/markdown", "text/x-markdown"],
		}
	
	@classmethod
	def all_mime_types(cls):
		"""Return flat list of all supported MIME types"""
		return [mime for mimes in cls.mime_types().values() for mime in mimes]
	
	@classmethod
	def is_supported(cls, extension: str, mime_type: str | None = None) -> bool:
		"""Check if file extension and/or MIME type is supported"""
		ext = extension.lower() if extension.startswith('.') else f".{extension.lower()}"
		if ext in cls.extensions():
			return True
		if mime_type is not None and mime_type in cls.all_mime_types():
			return True
		return False


# Maximum file size in bytes (10MB)
MAX_DOCUMENT_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def document_storage_path(kb, filename: str) -> str:
	"""documents/<project slug>/<kb id>/<filename>. The KB id, not its name: survives renames, and any name is a safe path.
	Longest: 10 + 50 (slug) + 1 + 32 + 1 + 36 ("<doc id>_") + 100 (filename) = 230, inside Document.file's 255."""
	return f"documents/{kb.project.slug}/{kb.id.hex}/{filename}"


def document_upload_path(instance, filename):
	return document_storage_path(instance.kb, filename)


class KnowledgeBase(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="knowledge_bases")
	name = models.CharField(max_length=255)
	vector_collection = models.CharField(max_length=255, unique=True)
	# Fallback for uploads, crawls and test chats that name no KB. A flag, not the name "default", so it survives a rename.
	is_default = models.BooleanField(default=False)
	last_extraction = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(Lower("name"), "project", name="kb_unique_name_per_project"),
			models.UniqueConstraint(fields=["project"], condition=models.Q(is_default=True), name="kb_one_default_per_project"),
		]

	def __str__(self) -> str:
		return f"{self.project.name} - {self.name}"

	def save(self, *args, **kwargs):
		# Named by id, not name: a rename never touches Qdrant, and any name (spaces, Bengali) gives a valid collection name
		if not self.vector_collection:
			self.vector_collection = f"kb_{self.project.slug}_{self.id.hex}"
		super().save(*args, **kwargs)

	@classmethod
	def get_default(cls, project) -> "KnowledgeBase":
		# A pre-flag KB named "Default"/"DEFAULT" (the migration only flagged exact "default") would block creating "default"
		legacy = cls.objects.filter(project=project, name__iexact="default", is_default=False).first()
		if legacy and not cls.objects.filter(project=project, is_default=True).exists():
			legacy.is_default = True
			legacy.save(update_fields=["is_default"])
			return legacy
		kb, _ = cls.objects.get_or_create(project=project, is_default=True, defaults={"name": "default"})
		return kb


class Document(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	SOURCE_CHOICES = [("upload", "Upload"), ("url", "URL")]
	kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="documents")
	source_type = models.CharField(max_length=32, choices=SOURCE_CHOICES, default="upload")

	# File upload field
	file = models.FileField(upload_to=document_upload_path, max_length=255, null=True, blank=True)
	filename = models.CharField(max_length=255, blank=True, default="")
	file_size = models.PositiveBigIntegerField(default=0)

	# For URL sources
	uri = models.TextField(blank=True, default="")
	
	# Web crawling fields
	markdown_content = models.TextField(blank=True, default="", help_text="Markdown content from crawled page")
	images_metadata = models.JSONField(default=list, blank=True, help_text="Structured image data from crawled page")
	links_metadata = models.JSONField(default=dict, blank=True, help_text="Internal and external links from crawled page")
	parent_crawl_id = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='child_pages', help_text="Parent document for crawled child pages")
	crawl_depth = models.IntegerField(default=0, help_text="Depth level in crawl tree (0 for parent)")
	page_url = models.TextField(blank=True, default="", help_text="Display-friendly URL for crawled page")
	page_title = models.CharField(max_length=500, blank=True, default="", help_text="Page title from crawled content")
	search_query = models.CharField(max_length=500, blank=True, default="", help_text="Optional query used to filter and extract targeted content during crawl")

	status = models.CharField(max_length=32, default="pending")
	checksum = models.CharField(max_length=128, blank=True, default="")
	error_message = models.TextField(blank=True, default="")
	vector_chunk_ids = models.JSONField(default=list, blank=True, help_text="List of vector chunk IDs stored in Qdrant")
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self) -> str:
		return f"{self.filename or self.uri} ({self.status})"

	def save(self, *args, **kwargs):
		# Set filename and size from uploaded file
		if self.file and not self.filename:
			filename = os.path.basename(self.file.name)
			# Truncate filename to 100 chars to match database constraint
			if len(filename) > 100:
				name, ext = os.path.splitext(filename)
				max_base_length = 100 - len(ext)
				if max_base_length < 1:
					filename = filename[:100]
				else:
					filename = name[:max_base_length] + ext
			self.filename = filename
			self.file_size = self.file.size
			
			# Validate file size
			if self.file_size > MAX_DOCUMENT_FILE_SIZE:
				from django.core.exceptions import ValidationError
				raise ValidationError(
					f"File size ({self.file_size} bytes) exceeds maximum allowed size ({MAX_DOCUMENT_FILE_SIZE} bytes)"
				)
		
		# Always ensure filename doesn't exceed 100 chars (database constraint)
		if self.filename and len(self.filename) > 100:
			name, ext = os.path.splitext(self.filename)
			max_base_length = 100 - len(ext)
			if max_base_length < 1:
				self.filename = self.filename[:100]
			else:
				self.filename = name[:max_base_length] + ext
		
		super().save(*args, **kwargs)

	def delete(self, *args, **kwargs):
		# Delete vectors from Qdrant before the rows go. Crawled child pages are removed by the
		# cascade, which skips their delete(), so their vectors are collected here too.
		from agents.vector_store import delete_vectors_from_collection
		base = self.kb.vector_collection
		by_collection: dict[str, list[str]] = {}
		for doc in [self, *Document.objects.filter(parent_crawl_id=self)]:
			if doc.vector_chunk_ids:
				collection = base + "_web" if doc.source_type == "url" else base
				by_collection.setdefault(collection, []).extend(doc.vector_chunk_ids)
		for collection, vector_ids in by_collection.items():
			try:
				delete_vectors_from_collection(collection_name=collection, vector_ids=vector_ids)
			except Exception as e:
				# Log error but don't fail deletion
				import logging
				logger = logging.getLogger(__name__)
				logger.error(f"Failed to delete vectors for document {self.id}: {str(e)}")
		
		# Delete the actual file when document is deleted
		if self.file:
			if default_storage.exists(self.file.name):
				default_storage.delete(self.file.name)
		super().delete(*args, **kwargs)


MAX_IMAGES_PER_PRODUCT = 10
MAX_PRODUCT_IMAGE_BYTES = 10 * 1024 * 1024  # full-size stock/phone photos; customer uploads keep image_search.MAX_IMAGE_BYTES


def product_image_path(kb, image_id: str, ext: str) -> str:
	return f"products/{kb.project.slug}/{kb.id.hex}/{image_id}.{ext}"


class ProductSource(models.Model):
	KIND_CHOICES = [("csv", "CSV"), ("sheet", "Sheet"), ("api", "REST API")]
	SCHEDULE_CHOICES = [("hourly", "Hourly"), ("daily", "Daily")]
	SCHEDULE_INTERVALS = {"hourly": timedelta(hours=1), "daily": timedelta(days=1)}

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="product_sources")
	kind = models.CharField(max_length=16, choices=KIND_CHOICES)
	mapping = models.JSONField(default=dict)
	sheet_url = models.URLField(blank=True, default="")
	api_config = models.JSONField(default=dict, blank=True)  # REST API request, never holds the secret
	secret_ciphertext = models.TextField(blank=True, default="")  # the API key/token, Fernet-encrypted (product_api)
	last_synced_at = models.DateTimeField(null=True, blank=True)
	last_error = models.TextField(blank=True, default="")
	# Automatic sync (sheets only). Sheets synced hourly before this existed, so the migration turns this on for them.
	auto_sync_enabled = models.BooleanField(default=False)
	schedule = models.CharField(max_length=16, choices=SCHEDULE_CHOICES, default="hourly")
	next_sync_at = models.DateTimeField(null=True, blank=True)  # null = due now
	sync_failures = models.PositiveIntegerField(default=0)  # consecutive failed syncs; drives the retry backoff
	sync_locked_at = models.DateTimeField(null=True, blank=True)  # set while a sync runs: the overlap guard
	created_at = models.DateTimeField(auto_now_add=True)


class ProductField(models.Model):
	KIND_CHOICES = [("text", "Text"), ("number", "Number"), ("choice", "Choice")]

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="product_fields")
	key = models.SlugField(max_length=64)
	label = models.CharField(max_length=255)
	kind = models.CharField(max_length=16, choices=KIND_CHOICES, default="text")
	choices = models.JSONField(default=list, blank=True)
	required = models.BooleanField(default=False)
	show_to_agent = models.BooleanField(default=True)
	position = models.IntegerField(default=0)

	class Meta:
		constraints = [models.UniqueConstraint(fields=["kb", "key"], name="product_field_key_unique_per_kb")]


# ponytail: no reindex_vectors support for image collections; re-queue embed_product_image for all images if the embedding model changes.
class Product(models.Model):
	STATUS_CHOICES = [("needs_info", "Needs info"), ("active", "Active"), ("hidden", "Hidden")]
	SOURCE_CHOICES = [("manual", "Manual"), ("kb", "Knowledge base"), ("csv", "CSV"), ("sheet", "Sheet")]

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="products")
	name = models.CharField(max_length=255)
	description = models.TextField(blank=True, default="")
	external_id = models.CharField(max_length=255, null=True, blank=True)
	sku = models.CharField(max_length=128, blank=True, default="")
	price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
	available = models.BooleanField(default=True)
	status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="needs_info")
	product_url = models.TextField(blank=True, default="")
	source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default="manual")
	source_ref = models.ForeignKey(ProductSource, on_delete=models.SET_NULL, null=True, blank=True, related_name="products")
	custom = models.JSONField(default=dict, blank=True)
	locked_fields = models.JSONField(default=list, blank=True)
	last_seen_at = models.DateTimeField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		constraints = [
			models.CheckConstraint(check=~models.Q(status="active") | models.Q(price__isnull=False), name="product_active_requires_price"),
			models.UniqueConstraint(fields=["kb", "external_id"], condition=models.Q(external_id__isnull=False), name="product_external_id_unique_per_kb"),
		]

	def __str__(self) -> str:
		return self.name

	def delete(self, *args, **kwargs):
		# The cascade skips ProductImage.delete(), so remove points and files here
		images = list(self.images.all())
		if images:
			delete_images(self.kb.vector_collection, [str(i.id) for i in images])
		for image in images:
			image._delete_file()
		return super().delete(*args, **kwargs)


class ProductVariant(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
	external_id = models.CharField(max_length=255, null=True, blank=True)
	sku = models.CharField(max_length=128, blank=True, default="")
	label = models.CharField(max_length=255)
	attributes = models.JSONField(default=dict, blank=True)
	price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
	available = models.BooleanField(default=True)


class ProductImage(models.Model):
	# uploading: presigned, browser PUT not confirmed yet; pending: confirmed, waiting for resize + embed
	STATUS_CHOICES = [("uploading", "Uploading"), ("pending", "Pending"), ("ready", "Ready"), ("failed", "Failed")]

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
	file = models.FileField(max_length=255)
	status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
	error = models.CharField(max_length=255, blank=True, default="")
	source_url = models.TextField(blank=True, default="")  # the URL a source row gave; a sync skips URLs already attached
	created_at = models.DateTimeField(auto_now_add=True)

	def _delete_file(self):
		if self.file:
			delete_keys(self.file.name, thumb_key(self.file.name))

	def delete(self, *args, **kwargs):
		delete_images(self.product.kb.vector_collection, [str(self.id)])
		self._delete_file()
		return super().delete(*args, **kwargs)


class ProductCandidate(models.Model):
	STATUS_CHOICES = [("pending", "Pending"), ("imported", "Imported"), ("ignored", "Ignored")]

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="product_candidates")
	document = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="product_candidates")
	page_url = models.TextField(blank=True, default="")
	name = models.CharField(max_length=255)
	payload = models.JSONField(default=dict)
	confidence = models.FloatField(default=0)
	status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
	matched_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
	changes = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)


class IgnoredProductPage(models.Model):
	kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="ignored_product_pages")
	url = models.CharField(max_length=1000)

	class Meta:
		constraints = [models.UniqueConstraint(fields=["kb", "url"], name="ignored_product_page_unique_per_kb")]


class IngestionJob(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="ingestions")
	document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="ingestion_jobs", null=True, blank=True)
	status = models.CharField(
		max_length=32,
		choices=IngestionJobStatus.choices(),
		default=IngestionJobStatus.PENDING.value
	)
	error = models.TextField(blank=True, default="")
	progress = models.PositiveIntegerField(default=0)  # 0-100
	created_at = models.DateTimeField(auto_now_add=True)
	started_at = models.DateTimeField(null=True, blank=True)
	completed_at = models.DateTimeField(null=True, blank=True)
	celery_task_kwargs = models.JSONField(default=dict, blank=True, help_text="Stored task kwargs for recovery re-dispatch")

	class Meta:
		ordering = ['-created_at']

	def __str__(self) -> str:
		doc_name = self.document.filename if self.document else "KB"
		return f"{doc_name} - {self.status} ({self.progress}%)"


class CustomInstruction(models.Model):
	"""
	Custom instructions that define agent behavior.
	- If knowledge_base is null: shared instruction, used by every KB
	- If knowledge_base is set: used only by that KB, on top of the shared ones
	"""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="custom_instructions")
	knowledge_base = models.ForeignKey(
		KnowledgeBase,
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name="custom_instructions"
	)
	label = models.CharField(max_length=255)
	message = models.TextField()
	# Generic API tool (agents/api_tool.py). api_domains: [{"domain", "auth_header"?, "secret"?}]
	api_tool_enabled = models.BooleanField(default=False)
	api_domains = models.JSONField(default=list, blank=True)
	# general: in the general prompt. order: only in the order prompt. off: kept, used nowhere.
	SCOPE_CHOICES = [("general", "General"), ("order", "Orders"), ("off", "Off")]
	scope = models.CharField(max_length=16, choices=SCOPE_CHOICES, default="general")
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-created_at']
		indexes = [
			models.Index(fields=['project', 'knowledge_base']),
		]

	def __str__(self) -> str:
		scope = self.knowledge_base.name if self.knowledge_base else "Project"
		return f"{self.label} ({scope})"

	@classmethod
	def _for_kb(cls, project_id, knowledge_base_id=None):
		"""Every row that applies to a KB: shared, then the KB's own (last, so they win on conflicts)."""
		rule = models.Q(knowledge_base__isnull=True)
		if knowledge_base_id:
			rule |= models.Q(knowledge_base_id=knowledge_base_id)
		return cls.objects.filter(rule, project_id=project_id).order_by(
			models.F("knowledge_base_id").asc(nulls_first=True), "-created_at"
		)

	@classmethod
	def effective(cls, project_id, knowledge_base_id=None):
		"""General-prompt instructions for a KB."""
		return cls._for_kb(project_id, knowledge_base_id).filter(scope="general")

	@classmethod
	def effective_order_instructions(cls, project_id, knowledge_base_id=None):
		"""Order-prompt instructions for a KB."""
		return cls._for_kb(project_id, knowledge_base_id).filter(scope="order")

	@classmethod
	def visible(cls, project_id, knowledge_base_id=None):
		"""Every scope for a KB, for the settings UI (badges show Orders and Off)."""
		return cls._for_kb(project_id, knowledge_base_id)


class InstructionTemplate(models.Model):
	"""
	Predefined instruction templates that users can quickly add.
	These are system-wide templates to help users get started.
	"""
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	label = models.CharField(max_length=255, help_text="Template name/label")
	message = models.TextField(help_text="Template instruction content")
	description = models.TextField(blank=True, default="", help_text="Brief description of what this instruction does")
	category = models.CharField(
		max_length=50,
		default="general",
		help_text="Category for organizing templates (e.g., 'tone', 'behavior', 'formatting')"
	)
	is_active = models.BooleanField(default=True, help_text="Whether this template is available for use")
	usage_count = models.IntegerField(default=0, help_text="Number of times this template has been used")
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-usage_count', 'label']
		indexes = [
			models.Index(fields=['category', 'is_active']),
		]

	def __str__(self) -> str:
		return f"{self.label} ({self.category})"




class MemorySuggestion(models.Model):
	"""
	A lesson the agent drafted from real conversations, waiting for staff approval (docs/agent-memory-design.md).
	knowledge: Q&A from a staff answer → becomes a knowledge-base document when approved.
	instruction: behaviour rule from handoffs/staff replies → becomes a CustomInstruction when approved.
	Never applied without a person: drafts come from customer text and may be customer-specific.
	"""
	KIND_CHOICES = [("knowledge", "Knowledge"), ("instruction", "Instruction")]
	STATUS_CHOICES = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")]

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memory_suggestions")
	knowledge_base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="memory_suggestions")
	kind = models.CharField(max_length=16, choices=KIND_CHOICES)
	title = models.CharField(max_length=500)  # question, or instruction label
	body = models.TextField()  # answer, or instruction text
	rationale = models.TextField(blank=True, default="")
	source_conversation = models.ForeignKey(
		"channels_app.Conversation", on_delete=models.SET_NULL, null=True, blank=True, related_name="memory_suggestions"
	)
	source_message_id = models.UUIDField(null=True, blank=True)  # staff reply it was drafted from (dedupe)
	status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
	result_id = models.CharField(max_length=64, blank=True, default="")  # created Document / CustomInstruction id
	decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
	decided_at = models.DateTimeField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ["-created_at"]
		indexes = [models.Index(fields=["project", "status"])]

	def __str__(self) -> str:
		return f"{self.kind}: {self.title[:50]} ({self.status})"
