import uuid
import os
from enum import Enum
from django.db import models
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from accounts.models import Project


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


def document_upload_path(instance, filename):
    """
    Generate upload path for documents
    Format: documents/project_slug/kb_slug/filename
    """
    project_slug = instance.kb.project.slug
    # Use KB name as slug (slugify if needed, but for now use name directly)
    kb_slug = instance.kb.name.lower().replace(' ', '-')
    return f"documents/{project_slug}/{kb_slug}/{filename}"


class KnowledgeBase(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="knowledge_bases")
	name = models.CharField(max_length=255)
	vector_collection = models.CharField(max_length=255, unique=True)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self) -> str:
		return f"{self.project.name} - {self.name}"

	@classmethod
	def get_vector_collection_name(cls, project, kb_name: str) -> str:
		"""
		Generate a vector collection name for a knowledge base.
		
		Args:
			project: Project instance
			kb_name: Knowledge base name
			
		Returns:
			str: Vector collection name in format: kb_{project_slug}_{kb_name}_{project_id_without_dashes}
		"""
		project_id_str = str(project.id).replace('-', '')
		return f"kb_{project.slug}_{kb_name}_{project_id_str}"


class Document(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	SOURCE_CHOICES = [("upload", "Upload"), ("url", "URL")]
	kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="documents")
	source_type = models.CharField(max_length=32, choices=SOURCE_CHOICES, default="upload")

	# File upload field
	file = models.FileField(upload_to=document_upload_path, null=True, blank=True)
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
		# Delete vectors from Qdrant if they exist
		if self.vector_chunk_ids and self.kb:
			try:
				from agents.vector_store import delete_vectors_from_collection
				delete_vectors_from_collection(
					collection_name=self.kb.vector_collection,
					vector_ids=self.vector_chunk_ids
				)
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
	- If knowledge_base is null: project-level instruction (default for all KBs)
	- If knowledge_base is set: KB-specific instruction (overrides project-level)
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


