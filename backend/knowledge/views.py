import json
import hashlib
import os
from django.http import JsonResponse, HttpRequest
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.forms.models import model_to_dict
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import serializers
from .models import KnowledgeBase, Document, IngestionJob, IngestionJobStatus, DocumentFileType, MAX_DOCUMENT_FILE_SIZE, CustomInstruction, InstructionTemplate
from .tasks import run_ingestion_job, crawl_website
from .api_tool_config import ApiConfigError, apply_api_config, public_api_domains


class CustomInstructionSerializer(serializers.ModelSerializer):
    """Serializer for CustomInstruction model"""
    knowledge_base_id = serializers.UUIDField(required=False, allow_null=True)
    knowledge_base_name = serializers.CharField(source='knowledge_base.name', read_only=True)
    
    class Meta:
        model = CustomInstruction
        fields = ['id', 'project', 'knowledge_base', 'knowledge_base_id', 'knowledge_base_name', 'label', 'message', 'created_at', 'updated_at']
        read_only_fields = ['id', 'project', 'knowledge_base', 'created_at', 'updated_at']

User = get_user_model()


class KnowledgeBaseListView(APIView):
	"""List all knowledge bases for a project"""
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project_id = request.query_params.get('project_id')
		if not project_id:
			return Response(
				{"success": False, "error": {"code": "missing_project", "message": "project_id is required"}},
				status=status.HTTP_400_BAD_REQUEST
			)

		# Get knowledge bases for this project
		knowledge_bases = KnowledgeBase.objects.filter(project_id=project_id).order_by('name')
		
		kb_data = []
		for kb in knowledge_bases:
			kb_data.append({
				"id": str(kb.id),
				"name": kb.name,
				"created_at": kb.created_at.isoformat(),
			})

		return Response({
			"success": True,
			"data": {
				"knowledge_bases": kb_data,
				"count": len(kb_data)
			}
		}, status=status.HTTP_200_OK)


class InstructionTemplateListView(APIView):
	"""List all active instruction templates"""
	permission_classes = [IsAuthenticated]

	def get(self, request):
		# Get active templates, optionally filter by category
		category = request.query_params.get('category')
		
		templates = InstructionTemplate.objects.filter(is_active=True)
		if category:
			templates = templates.filter(category=category)
		
		templates = templates.order_by('-usage_count', 'label')
		
		templates_data = []
		for template in templates:
			templates_data.append({
				"id": str(template.id),
				"label": template.label,
				"message": template.message,
				"description": template.description,
				"category": template.category,
				"usage_count": template.usage_count,
			})

		return Response({
			"success": True,
			"data": {
				"templates": templates_data,
				"count": len(templates_data)
			}
		}, status=status.HTTP_200_OK)


class DocumentUploadView(APIView):
    """
    API endpoint for uploading documents to knowledge base
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        try:
            # Get project from user's projects
            project_id = request.data.get('project_id')
            if not project_id:
                return Response(
                    {"success": False, "error": {"code": "missing_project", "message": "project_id is required"}},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Verify user has access to this project
            try:
                from accounts.models import ProjectUser
                project_user = ProjectUser.objects.get(
                    project_id=project_id,
                    user=request.user,
                    role='admin'
                )
                project = project_user.project
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No access to this project"}},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Get or create knowledge base
            kb_name = request.data.get('kb_name', 'default')
            kb, created = KnowledgeBase.objects.get_or_create(
                project=project,
                name=kb_name
            )

            # Handle file upload
            if 'file' not in request.FILES:
                return Response(
                    {"success": False, "error": {"code": "no_file", "message": "No file provided"}},
                    status=status.HTTP_400_BAD_REQUEST
                )

            uploaded_file = request.FILES['file']

            # Validate file type using enum
            file_extension = os.path.splitext(uploaded_file.name)[1].lower()
            if not DocumentFileType.is_supported(file_extension, uploaded_file.content_type):
                supported_types = ", ".join([ft.value.upper() for ft in DocumentFileType])
                return Response(
                    {"success": False, "error": {"code": "invalid_file_type",
                                               "message": f"File type {uploaded_file.content_type} not allowed. Only {supported_types} files are supported."}},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Validate file size using constant
            if uploaded_file.size > MAX_DOCUMENT_FILE_SIZE:
                max_size_mb = MAX_DOCUMENT_FILE_SIZE / (1024 * 1024)
                return Response(
                    {"success": False, "error": {"code": "file_too_large", 
                                               "message": f"File size exceeds {max_size_mb}MB limit"}},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Read file content for checksum calculation
            file_content = uploaded_file.read()

            # Calculate checksum
            checksum = hashlib.sha256(file_content).hexdigest()

            # Check for duplicate file in the same knowledge base
            existing_doc = Document.objects.filter(
                kb=kb,
                checksum=checksum,
                status__in=['completed', 'processing']
            ).first()

            if existing_doc:
                return Response(
                    {"success": False, "error": {"code": "duplicate_file",
                                               "message": f"This file '{existing_doc.filename}' has already been uploaded to this knowledge base"}},
                    status=status.HTTP_409_CONFLICT
                )

            # Create document record
            # Truncate filename if it's too long (database may have 100 char constraint)
            filename = uploaded_file.name
            if len(filename) > 100:
                # Keep extension and truncate the base name
                name, ext = os.path.splitext(filename)
                max_base_length = 100 - len(ext)
                if max_base_length < 1:
                    # If extension itself is too long, just truncate everything
                    filename = filename[:100]
                else:
                    filename = name[:max_base_length] + ext
            
            document = Document.objects.create(
                kb=kb,
                source_type='upload',
                filename=filename,
                file_size=uploaded_file.size,
                checksum=checksum,
                status='processing'
            )

            # Save file to storage
            # Use KB name as slug (slugify if needed, but for now use name directly)
            kb_slug = kb.name.lower().replace(' ', '-')
            # Calculate max filename length to keep total path under 100 chars
            # Path structure: "documents/" (10) + project_slug (32) + "/" (1) + kb_slug + "/" (1) + doc_id (36) + "_" (1) + filename
            # Base path length: 10 + 32 + 1 + len(kb_slug) + 1 + 36 + 1 = 81 + len(kb_slug)
            base_path_length = 81 + len(kb_slug)
            max_filename_length = max(10, 100 - base_path_length)  # Ensure at least 10 chars for filename
            
            # Further truncate filename if needed to fit in path
            if len(filename) > max_filename_length:
                name, ext = os.path.splitext(filename)
                max_base_length = max_filename_length - len(ext)
                if max_base_length < 1:
                    filename = filename[:max_filename_length]
                else:
                    filename = name[:max_base_length] + ext
            
            file_path = f"documents/{project.slug}/{kb_slug}/{document.id}_{filename}"
            saved_file = default_storage.save(file_path, ContentFile(file_content))
            document.file = saved_file
            document.save()

            # Create ingestion job
            ingestion_job = IngestionJob.objects.create(
                kb=kb,
                document=document,
                status=IngestionJobStatus.PENDING.value
            )

            # Store task kwargs for recovery re-dispatch
            ingestion_job.celery_task_kwargs = {
                'task': 'run_ingestion_job',
                'kwargs': {'job_id': str(ingestion_job.id)},
            }
            ingestion_job.save(update_fields=['celery_task_kwargs'])

            # Start background processing
            try:
                run_ingestion_job.delay(str(ingestion_job.id))
            except Exception as e:
                # If Celery is not running, mark as failed
                ingestion_job.status = IngestionJobStatus.FAILED.value
                ingestion_job.error = f"Could not start processing: {str(e)}"
                ingestion_job.save()

            return Response({
                "success": True,
                "message": "Document uploaded successfully and processing started",
                "data": {
                    "document": {
                        "id": str(document.id),
                        "filename": document.filename,
                        "file_size": document.file_size,
                        "status": document.status,
                        "created_at": document.created_at.isoformat()
                    },
                    "kb": {
                        "id": str(kb.id),
                        "name": kb.name,
                        "project_id": str(project.id),
                        "project_name": project.name
                    },
                    "ingestion_job": {
                        "id": str(ingestion_job.id),
                        "status": ingestion_job.status
                    }
                }
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            return Response(
                {"success": False, "error": {"code": "upload_failed", "message": str(e), "details": error_details}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DocumentListView(APIView):
    """
    API endpoint for listing documents
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            project_id = request.query_params.get('project_id')
            kb_id = request.query_params.get('kb_id')
            status_filter = request.query_params.get('status')

            # Base queryset with related data
            qs = Document.objects.select_related('kb', 'kb__project')

            # Filter by project access
            if project_id:
                try:
                    from accounts.models import ProjectUser
                    ProjectUser.objects.get(
                        project_id=project_id,
                        user=request.user
                    )
                    qs = qs.filter(kb__project_id=project_id)
                except ProjectUser.DoesNotExist:
                    return Response(
                        {"success": False, "error": {"code": "access_denied", "message": "No access to this project"}},
                        status=status.HTTP_403_FORBIDDEN
                    )
            else:
                # If no project specified, show documents from all user's projects
                from accounts.models import ProjectUser
                user_project_ids = ProjectUser.objects.filter(user=request.user).values_list('project_id', flat=True)
                qs = qs.filter(kb__project_id__in=user_project_ids)

            # Additional filters
            if kb_id:
                qs = qs.filter(kb_id=kb_id)
            if status_filter:
                qs = qs.filter(status=status_filter)

            # Get documents with ingestion job info
            documents = []
            for doc in qs.order_by("-created_at")[:100]:
                # Get latest ingestion job
                latest_job = doc.ingestion_jobs.order_by('-created_at').first()
                
                # Count child pages for URL sources
                children_count = 0
                if doc.source_type == 'url' and doc.crawl_depth == 0:
                    children_count = doc.child_pages.count()

                documents.append({
                    "id": str(doc.id),
                    "filename": doc.filename,
                    "file_size": doc.file_size,
                    "status": doc.status,
                    "error_message": doc.error_message,
                    "checksum": doc.checksum,
                    "created_at": doc.created_at.isoformat(),
                    "source_type": doc.source_type,
                    "page_url": doc.page_url,
                    "page_title": doc.page_title,
                    "crawl_depth": doc.crawl_depth,
                    "parent_crawl_id": str(doc.parent_crawl_id.id) if doc.parent_crawl_id else None,
                    "children_count": children_count,
                    "ingestion_job": {
                        "id": str(latest_job.id) if latest_job else None,
                        "status": latest_job.status if latest_job else None,
                        "progress": latest_job.progress if latest_job else 0,
                        "error": latest_job.error if latest_job else None,
                        "created_at": latest_job.created_at.isoformat() if latest_job else None,
                        "completed_at": latest_job.completed_at.isoformat() if latest_job and latest_job.completed_at else None,
                    } if latest_job else None
                })

            return Response({
                "success": True,
                "data": {
                    "documents": documents,
                    "count": len(documents)
                }
            }, status=status.HTTP_200_OK)

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            return Response(
                {"success": False, "error": {"code": "list_failed", "message": str(e), "details": error_details}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DocumentDeleteView(APIView):
    """
    API endpoint for deleting documents
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, document_id):
        try:
            # Get document and verify access
            document = Document.objects.select_related('kb', 'kb__project').get(id=document_id)

            # Verify user has access to the project
            from accounts.models import ProjectUser
            try:
                ProjectUser.objects.get(
                    project=document.kb.project,
                    user=request.user,
                    role='admin'
                )
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No access to this document"}},
                    status=status.HTTP_403_FORBIDDEN
                )

            # For URL documents, delete vectors from the web collection
            if document.source_type == 'url' and document.vector_chunk_ids:
                try:
                    from agents.vector_store import delete_vectors_from_collection
                    web_collection = document.kb.vector_collection + "_web"
                    delete_vectors_from_collection(web_collection, document.vector_chunk_ids)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Failed to delete vectors for document {document.id}: {e}")

            # Delete the document (this will also delete the file via the model's delete method)
            document.delete()

            return Response({
                "success": True,
                "message": f"Document '{document.filename}' deleted successfully"
            }, status=status.HTTP_200_OK)

        except Document.DoesNotExist:
            return Response(
                {"success": False, "error": {"code": "not_found", "message": "Document not found"}},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            return Response(
                {"success": False, "error": {"code": "delete_failed", "message": str(e), "details": error_details}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# Legacy view for backward compatibility
@method_decorator(csrf_exempt, name="dispatch")
class DocumentsView(View):
    def get(self, request: HttpRequest):
        kb_id = request.GET.get("kb_id")
        project_id = request.GET.get("project_id")

        qs = Document.objects.select_related('kb', 'kb__project')

        if kb_id:
            qs = qs.filter(kb_id=kb_id)
        if project_id:
            qs = qs.filter(kb__project_id=project_id)

        documents = []
        for doc in qs.order_by("-created_at")[:100]:
            documents.append({
                "id": str(doc.id),
                "filename": doc.filename,
                "file_size": doc.file_size,
                "status": doc.status,
                "created_at": doc.created_at.isoformat(),
                "kb": {
                    "id": str(doc.kb.id),
                    "name": doc.kb.name,
                    "project": {
                        "id": str(doc.kb.project.id),
                        "name": doc.kb.project.name
                    }
                }
            })

        return JsonResponse({"results": documents})

    def post(self, request: HttpRequest):
        # This method is kept for backward compatibility
        # The new DocumentUploadView should be used instead
        return JsonResponse({"error": "Use the API endpoint for document uploads"}, status=400)


class InstructionTemplateUseView(APIView):
	"""Increment usage count for a template"""
	permission_classes = [IsAuthenticated]

	def post(self, request, template_id):
		try:
			template = InstructionTemplate.objects.get(id=template_id)
			template.usage_count += 1
			template.save(update_fields=['usage_count'])
			
			return Response({
				"success": True,
				"message": "Template usage recorded"
			}, status=status.HTTP_200_OK)
		except InstructionTemplate.DoesNotExist:
			return Response(
				{"success": False, "error": {"code": "not_found", "message": "Template not found"}},
				status=status.HTTP_404_NOT_FOUND
			)


def _instruction_to_dict(inst: CustomInstruction) -> dict:
    return {
        "id": str(inst.id),
        "label": inst.label,
        "message": inst.message,
        "knowledge_base_id": str(inst.knowledge_base_id) if inst.knowledge_base_id else None,
        "knowledge_base_name": inst.knowledge_base.name if inst.knowledge_base else None,
        "api_tool_enabled": inst.api_tool_enabled,
        "api_domains": public_api_domains(inst.api_domains),
        "created_at": inst.created_at.isoformat(),
        "updated_at": inst.updated_at.isoformat(),
    }


def _api_config_error(e: ApiConfigError) -> Response:
    return Response(
        {"success": False, "error": {"code": "invalid_api_config", "message": str(e)}},
        status=status.HTTP_400_BAD_REQUEST
    )


class CustomInstructionListCreateView(APIView):
    """
    API endpoint for listing and creating custom instructions.
    
    GET: List instructions for a project (optionally filtered by knowledge_base_id)
    POST: Create a new instruction
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            project_id = request.query_params.get('project_id')
            kb_id = request.query_params.get('knowledge_base_id')
            
            if not project_id:
                return Response(
                    {"success": False, "error": {"code": "missing_project", "message": "project_id is required"}},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Verify user has access to this project
            from accounts.models import ProjectUser
            try:
                ProjectUser.objects.get(project_id=project_id, user=request.user)
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No access to this project"}},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Build queryset
            qs = CustomInstruction.objects.filter(project_id=project_id).select_related('knowledge_base')
            
            if kb_id:
                # If kb_id specified, get KB-level instructions only
                qs = qs.filter(knowledge_base_id=kb_id)
            elif kb_id == '':
                # If kb_id is empty string, get project-level instructions only
                qs = qs.filter(knowledge_base__isnull=True)
            # If kb_id not provided at all, return all instructions for the project
            
            instructions = [_instruction_to_dict(inst) for inst in qs.order_by('-created_at')]
            
            return Response({
                "success": True,
                "data": {
                    "instructions": instructions,
                    "count": len(instructions)
                }
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            import traceback
            return Response(
                {"success": False, "error": {"code": "list_failed", "message": str(e), "details": traceback.format_exc()}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def post(self, request):
        try:
            project_id = request.data.get('project_id')
            kb_id = request.data.get('knowledge_base_id')
            label = request.data.get('label', '').strip()
            message = request.data.get('message', '').strip()
            
            if not project_id:
                return Response(
                    {"success": False, "error": {"code": "missing_project", "message": "project_id is required"}},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not label or not message:
                return Response(
                    {"success": False, "error": {"code": "missing_fields", "message": "label and message are required"}},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Verify user has admin access to this project
            from accounts.models import ProjectUser, Project
            try:
                project_user = ProjectUser.objects.get(
                    project_id=project_id,
                    user=request.user,
                    role='admin'
                )
                project = project_user.project
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No admin access to this project"}},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Validate knowledge_base if provided
            knowledge_base = None
            if kb_id:
                try:
                    knowledge_base = KnowledgeBase.objects.get(id=kb_id, project=project)
                except KnowledgeBase.DoesNotExist:
                    return Response(
                        {"success": False, "error": {"code": "invalid_kb", "message": "Knowledge base not found or doesn't belong to this project"}},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            try:
                api_tool_enabled, api_domains = apply_api_config(request.data)
            except ApiConfigError as e:
                return _api_config_error(e)

            # Create instruction
            instruction = CustomInstruction.objects.create(
                project=project,
                knowledge_base=knowledge_base,
                label=label,
                message=message,
                api_tool_enabled=api_tool_enabled,
                api_domains=api_domains,
            )

            return Response({
                "success": True,
                "message": "Instruction created successfully",
                "data": {
                    "instruction": _instruction_to_dict(instruction)
                }
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            import traceback
            return Response(
                {"success": False, "error": {"code": "create_failed", "message": str(e), "details": traceback.format_exc()}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CustomInstructionDetailView(APIView):
    """
    API endpoint for retrieving, updating, and deleting a specific custom instruction.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, instruction_id):
        try:
            instruction = CustomInstruction.objects.select_related('knowledge_base', 'project').get(id=instruction_id)
            
            # Verify access
            from accounts.models import ProjectUser
            try:
                ProjectUser.objects.get(project=instruction.project, user=request.user)
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No access to this instruction"}},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            return Response({
                "success": True,
                "data": {
                    "instruction": _instruction_to_dict(instruction)
                }
            }, status=status.HTTP_200_OK)
            
        except CustomInstruction.DoesNotExist:
            return Response(
                {"success": False, "error": {"code": "not_found", "message": "Instruction not found"}},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            import traceback
            return Response(
                {"success": False, "error": {"code": "get_failed", "message": str(e), "details": traceback.format_exc()}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def patch(self, request, instruction_id):
        try:
            instruction = CustomInstruction.objects.select_related('knowledge_base', 'project').get(id=instruction_id)
            
            # Verify admin access
            from accounts.models import ProjectUser
            try:
                ProjectUser.objects.get(project=instruction.project, user=request.user, role='admin')
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No admin access to this instruction"}},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Update fields
            if 'label' in request.data:
                instruction.label = request.data['label'].strip()
            if 'message' in request.data:
                instruction.message = request.data['message'].strip()
            try:
                instruction.api_tool_enabled, instruction.api_domains = apply_api_config(
                    request.data, instruction.api_tool_enabled, instruction.api_domains
                )
            except ApiConfigError as e:
                return _api_config_error(e)

            instruction.save()
            
            return Response({
                "success": True,
                "message": "Instruction updated successfully",
                "data": {
                    "instruction": _instruction_to_dict(instruction)
                }
            }, status=status.HTTP_200_OK)
            
        except CustomInstruction.DoesNotExist:
            return Response(
                {"success": False, "error": {"code": "not_found", "message": "Instruction not found"}},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            import traceback
            return Response(
                {"success": False, "error": {"code": "update_failed", "message": str(e), "details": traceback.format_exc()}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def delete(self, request, instruction_id):
        try:
            instruction = CustomInstruction.objects.select_related('project').get(id=instruction_id)
            
            # Verify admin access
            from accounts.models import ProjectUser
            try:
                ProjectUser.objects.get(project=instruction.project, user=request.user, role='admin')
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No admin access to this instruction"}},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            label = instruction.label
            instruction.delete()
            
            return Response({
                "success": True,
                "message": f"Instruction '{label}' deleted successfully"
            }, status=status.HTTP_200_OK)
            
        except CustomInstruction.DoesNotExist:
            return Response(
                {"success": False, "error": {"code": "not_found", "message": "Instruction not found"}},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            import traceback
            return Response(
                {"success": False, "error": {"code": "delete_failed", "message": str(e), "details": traceback.format_exc()}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CustomInstructionResolveView(APIView):
    """
    API endpoint to get the effective instructions for a knowledge base.
    Returns KB-level instructions if they exist, otherwise project-level.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            project_id = request.query_params.get('project_id')
            kb_id = request.query_params.get('knowledge_base_id')
            
            if not project_id:
                return Response(
                    {"success": False, "error": {"code": "missing_project", "message": "project_id is required"}},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Verify user has access to this project
            from accounts.models import ProjectUser
            try:
                ProjectUser.objects.get(project_id=project_id, user=request.user)
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No access to this project"}},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Try to get KB-level instructions first if kb_id provided
            instructions = []
            source = "project"
            
            if kb_id:
                kb_instructions = CustomInstruction.objects.filter(
                    project_id=project_id,
                    knowledge_base_id=kb_id
                ).select_related('knowledge_base')
                
                if kb_instructions.exists():
                    instructions = list(kb_instructions.order_by('-created_at'))
                    source = "knowledge_base"
            
            # Fall back to project-level if no KB instructions
            if not instructions:
                instructions = list(CustomInstruction.objects.filter(
                    project_id=project_id,
                    knowledge_base__isnull=True
                ).order_by('-created_at'))
                source = "project"
            
            result = [_instruction_to_dict(inst) for inst in instructions]
            
            return Response({
                "success": True,
                "data": {
                    "instructions": result,
                    "count": len(result),
                    "source": source  # "project" or "knowledge_base"
                }
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            import traceback
            return Response(
                {"success": False, "error": {"code": "resolve_failed", "message": str(e), "details": traceback.format_exc()}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WebCrawlSubmitView(APIView):
    """
    API endpoint for submitting a URL to crawl
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            from urllib.parse import urlparse
            from accounts.models import ProjectUser
            
            # Get parameters
            url = request.data.get('url', '').strip()
            depth = request.data.get('depth', 1)
            project_id = request.data.get('project_id')
            kb_name = request.data.get('kb_name', 'default')
            search_query = request.data.get('search_query', '').strip()[:500]
            
            # Validate inputs
            if not url:
                return Response(
                    {"success": False, "error": {"code": "missing_url", "message": "URL is required"}},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not project_id:
                return Response(
                    {"success": False, "error": {"code": "missing_project", "message": "project_id is required"}},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate URL format
            try:
                parsed_url = urlparse(url)
                if not parsed_url.scheme or not parsed_url.netloc:
                    raise ValueError("Invalid URL format")
            except Exception:
                return Response(
                    {"success": False, "error": {"code": "invalid_url", "message": "Invalid URL format. Please provide a valid URL with http:// or https://"}},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate depth
            try:
                depth = int(depth)
                if depth < 1 or depth > 3:
                    raise ValueError()
            except (ValueError, TypeError):
                return Response(
                    {"success": False, "error": {"code": "invalid_depth", "message": "Depth must be between 1 and 3"}},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Verify user has admin access to this project
            try:
                project_user = ProjectUser.objects.get(
                    project_id=project_id,
                    user=request.user,
                    role='admin'
                )
                project = project_user.project
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No admin access to this project"}},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Get or create knowledge base
            kb, created = KnowledgeBase.objects.get_or_create(
                project=project,
                name=kb_name
            )
            
            # Create parent document record
            parent_doc = Document.objects.create(
                kb=kb,
                source_type='url',
                uri=url,
                page_url=url,
                crawl_depth=0,
                parent_crawl_id=None,
                status='processing',
                search_query=search_query,
            )
            
            # Create ingestion job for tracking
            ingestion_job = IngestionJob.objects.create(
                kb=kb,
                document=parent_doc,
                status=IngestionJobStatus.PENDING.value
            )
            
            # Store task kwargs for recovery re-dispatch
            ingestion_job.celery_task_kwargs = {
                'task': 'crawl_website',
                'kwargs': {
                    'url': url,
                    'depth': depth,
                    'kb_id': str(kb.id),
                    'parent_doc_id': str(parent_doc.id),
                    'job_id': str(ingestion_job.id),
                },
            }
            ingestion_job.save(update_fields=['celery_task_kwargs'])

            # Start background crawling
            try:
                crawl_website.delay(url, depth, str(kb.id), str(parent_doc.id), str(ingestion_job.id))
            except Exception as e:
                # If Celery is not running, mark as failed
                ingestion_job.status = IngestionJobStatus.FAILED.value
                ingestion_job.error = f"Could not start crawl: {str(e)}"
                ingestion_job.save()
                
                parent_doc.status = 'failed'
                parent_doc.error_message = str(e)
                parent_doc.save()
            
            return Response({
                "success": True,
                "message": "Web crawl started successfully",
                "data": {
                    "document": {
                        "id": str(parent_doc.id),
                        "url": url,
                        "depth": depth,
                        "status": parent_doc.status,
                        "created_at": parent_doc.created_at.isoformat()
                    },
                    "kb": {
                        "id": str(kb.id),
                        "name": kb.name,
                        "project_id": str(project.id),
                        "project_name": project.name
                    },
                    "ingestion_job": {
                        "id": str(ingestion_job.id),
                        "status": ingestion_job.status
                    }
                }
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            return Response(
                {"success": False, "error": {"code": "crawl_submit_failed", "message": str(e), "details": error_details}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DocumentMarkdownView(APIView):
    """
    API endpoint to retrieve markdown content for a document
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        try:
            from accounts.models import ProjectUser
            
            # Get document and verify access
            document = Document.objects.select_related('kb', 'kb__project').get(id=document_id)
            
            # Verify user has access to the project
            try:
                ProjectUser.objects.get(
                    project=document.kb.project,
                    user=request.user
                )
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No access to this document"}},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            return Response({
                "success": True,
                "data": {
                    "document": {
                        "id": str(document.id),
                        "page_url": document.page_url,
                        "page_title": document.page_title,
                        "markdown_content": document.markdown_content,
                        "images_metadata": document.images_metadata,
                        "created_at": document.created_at.isoformat()
                    }
                }
            }, status=status.HTTP_200_OK)
            
        except Document.DoesNotExist:
            return Response(
                {"success": False, "error": {"code": "not_found", "message": "Document not found"}},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            import traceback
            return Response(
                {"success": False, "error": {"code": "get_markdown_failed", "message": str(e), "details": traceback.format_exc()}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DocumentChildrenView(APIView):
    """
    API endpoint to retrieve child pages for a parent crawl document
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, parent_id):
        try:
            from accounts.models import ProjectUser
            
            # Get parent document and verify access
            parent_doc = Document.objects.select_related('kb', 'kb__project').get(id=parent_id)
            
            # Verify user has access to the project
            try:
                ProjectUser.objects.get(
                    project=parent_doc.kb.project,
                    user=request.user
                )
            except ProjectUser.DoesNotExist:
                return Response(
                    {"success": False, "error": {"code": "access_denied", "message": "No access to this document"}},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Get child pages
            children = parent_doc.child_pages.all().order_by('page_url')
            
            children_data = []
            for child in children:
                children_data.append({
                    "id": str(child.id),
                    "page_url": child.page_url,
                    "page_title": child.page_title,
                    "status": child.status,
                    "crawl_depth": child.crawl_depth,
                    "created_at": child.created_at.isoformat()
                })
            
            return Response({
                "success": True,
                "data": {
                    "parent_id": str(parent_doc.id),
                    "children": children_data,
                    "count": len(children_data)
                }
            }, status=status.HTTP_200_OK)
            
        except Document.DoesNotExist:
            return Response(
                {"success": False, "error": {"code": "not_found", "message": "Parent document not found"}},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            import traceback
            return Response(
                {"success": False, "error": {"code": "get_children_failed", "message": str(e), "details": traceback.format_exc()}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )