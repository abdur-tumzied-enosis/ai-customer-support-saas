"""
Review queue for agent-drafted memory (MemorySuggestion): list, approve (optionally edited) or reject.
Approving knowledge adds a Q&A text document to the KB and ingests it; approving an instruction adds a CustomInstruction.
"""
import hashlib

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ProjectUser
from .models import CustomInstruction, Document, IngestionJob, IngestionJobStatus, MemorySuggestion, document_storage_path
from .tasks import run_ingestion_job


def _dict(s: MemorySuggestion) -> dict:
	return {
		"id": str(s.id),
		"kind": s.kind,
		"title": s.title,
		"body": s.body,
		"rationale": s.rationale,
		"status": s.status,
		"knowledge_base_id": str(s.knowledge_base_id),
		"source_conversation_id": str(s.source_conversation_id) if s.source_conversation_id else None,
		"created_at": s.created_at.isoformat(),
	}


def _error(code: str, message: str, http_status: int) -> Response:
	return Response({"success": False, "error": {"code": code, "message": message}}, status=http_status)


def _add_knowledge_document(s: MemorySuggestion) -> str:
	kb = s.knowledge_base
	content = f"Q: {s.title}\nA: {s.body}\n".encode()
	doc = Document.objects.create(kb=kb, source_type="upload", filename=f"faq-{str(s.id)[:8]}.txt",
		checksum=hashlib.sha256(content).hexdigest())
	doc.file = default_storage.save(document_storage_path(kb, f"{doc.id}_{doc.filename}"), ContentFile(content))
	doc.file_size = len(content)
	doc.save()
	job = IngestionJob.objects.create(kb=kb, document=doc, status=IngestionJobStatus.PENDING.value,
		celery_task_kwargs={"task": "run_ingestion_job", "kwargs": {"job_id": None}})
	job.celery_task_kwargs["kwargs"]["job_id"] = str(job.id)
	job.save(update_fields=["celery_task_kwargs"])
	job_id = str(job.id)
	transaction.on_commit(lambda: run_ingestion_job.delay(job_id))  # recover_stuck_jobs re-dispatches if this is lost
	return str(doc.id)


def _add_instruction(s: MemorySuggestion) -> str:
	# Shared: every KB uses shared instructions, and the user can see and edit them on the knowledge page
	inst = CustomInstruction.objects.create(project_id=s.project_id, label=s.title[:255], message=s.body)
	return str(inst.id)


class SuggestionListView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project_id = request.query_params.get("project_id")
		if not project_id or not ProjectUser.objects.filter(project_id=project_id, user=request.user).exists():
			return _error("access_denied", "No access to this project", status.HTTP_403_FORBIDDEN)
		qs = MemorySuggestion.objects.filter(project_id=project_id, status=request.query_params.get("status", "pending"))
		return Response({"success": True, "data": {"suggestions": [_dict(s) for s in qs[:200]]}})


class SuggestionDecisionView(APIView):
	"""POST .../approve/ {title?, body?} or .../reject/"""
	permission_classes = [IsAuthenticated]

	def post(self, request, suggestion_id, decision):
		if decision not in ("approve", "reject"):
			return _error("not_found", "Unknown action", status.HTTP_404_NOT_FOUND)
		with transaction.atomic():
			s = MemorySuggestion.objects.select_for_update(of=("self",)).select_related("knowledge_base__project").filter(
				id=suggestion_id, project__members__user=request.user, project__members__role="admin"
			).first()
			if not s:
				return _error("not_found", "Suggestion not found", status.HTTP_404_NOT_FOUND)
			if s.status != "pending":
				return _error("already_decided", f"Suggestion was already {s.status}", status.HTTP_409_CONFLICT)
			if decision == "approve":
				s.title = (request.data.get("title") or s.title).strip()[:500]
				s.body = (request.data.get("body") or s.body).strip()
				if not s.title or not s.body:
					return _error("invalid", "Title and body are required", status.HTTP_400_BAD_REQUEST)
				s.result_id = _add_knowledge_document(s) if s.kind == "knowledge" else _add_instruction(s)
			s.status = "approved" if decision == "approve" else "rejected"
			s.decided_by, s.decided_at = request.user, timezone.now()
			s.save()
		return Response({"success": True, "data": {"suggestion": _dict(s)}})
