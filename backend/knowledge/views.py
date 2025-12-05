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
from .models import KnowledgeBase, Document, IngestionJob, IngestionJobStatus, DocumentFileType, MAX_DOCUMENT_FILE_SIZE, CustomInstruction
from .tasks import run_ingestion_job


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
                name=kb_name,
                defaults={'vector_collection': KnowledgeBase.get_vector_collection_name(project, kb_name)}
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

                documents.append({
                    "id": str(doc.id),
                    "filename": doc.filename,
                    "file_size": doc.file_size,
                    "status": doc.status,
                    "error_message": doc.error_message,
                    "checksum": doc.checksum,
                    "created_at": doc.created_at.isoformat(),
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
            
            instructions = []
            for inst in qs.order_by('-created_at'):
                instructions.append({
                    "id": str(inst.id),
                    "label": inst.label,
                    "message": inst.message,
                    "knowledge_base_id": str(inst.knowledge_base_id) if inst.knowledge_base_id else None,
                    "knowledge_base_name": inst.knowledge_base.name if inst.knowledge_base else None,
                    "created_at": inst.created_at.isoformat(),
                    "updated_at": inst.updated_at.isoformat(),
                })
            
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
            
            # Create instruction
            instruction = CustomInstruction.objects.create(
                project=project,
                knowledge_base=knowledge_base,
                label=label,
                message=message
            )
            
            return Response({
                "success": True,
                "message": "Instruction created successfully",
                "data": {
                    "instruction": {
                        "id": str(instruction.id),
                        "label": instruction.label,
                        "message": instruction.message,
                        "knowledge_base_id": str(instruction.knowledge_base_id) if instruction.knowledge_base_id else None,
                        "knowledge_base_name": instruction.knowledge_base.name if instruction.knowledge_base else None,
                        "created_at": instruction.created_at.isoformat(),
                        "updated_at": instruction.updated_at.isoformat(),
                    }
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
                    "instruction": {
                        "id": str(instruction.id),
                        "label": instruction.label,
                        "message": instruction.message,
                        "knowledge_base_id": str(instruction.knowledge_base_id) if instruction.knowledge_base_id else None,
                        "knowledge_base_name": instruction.knowledge_base.name if instruction.knowledge_base else None,
                        "created_at": instruction.created_at.isoformat(),
                        "updated_at": instruction.updated_at.isoformat(),
                    }
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
            
            instruction.save()
            
            return Response({
                "success": True,
                "message": "Instruction updated successfully",
                "data": {
                    "instruction": {
                        "id": str(instruction.id),
                        "label": instruction.label,
                        "message": instruction.message,
                        "knowledge_base_id": str(instruction.knowledge_base_id) if instruction.knowledge_base_id else None,
                        "knowledge_base_name": instruction.knowledge_base.name if instruction.knowledge_base else None,
                        "created_at": instruction.created_at.isoformat(),
                        "updated_at": instruction.updated_at.isoformat(),
                    }
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
            
            result = []
            for inst in instructions:
                result.append({
                    "id": str(inst.id),
                    "label": inst.label,
                    "message": inst.message,
                    "knowledge_base_id": str(inst.knowledge_base_id) if inst.knowledge_base_id else None,
                    "knowledge_base_name": inst.knowledge_base.name if inst.knowledge_base else None,
                    "created_at": inst.created_at.isoformat(),
                    "updated_at": inst.updated_at.isoformat(),
                })
            
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