from django.urls import path
from .views import (
	DocumentUploadView, DocumentListView, DocumentDeleteView,
	CustomInstructionListCreateView, CustomInstructionDetailView, CustomInstructionResolveView
)

urlpatterns = [
	# Document endpoints
	path("documents/upload/", DocumentUploadView.as_view(), name="document-upload"),
	path("documents/", DocumentListView.as_view(), name="document-list"),
	path("documents/<uuid:document_id>/", DocumentDeleteView.as_view(), name="document-delete"),
	
	# Custom instruction endpoints
	path("instructions/", CustomInstructionListCreateView.as_view(), name="instruction-list-create"),
	path("instructions/resolve/", CustomInstructionResolveView.as_view(), name="instruction-resolve"),
	path("instructions/<uuid:instruction_id>/", CustomInstructionDetailView.as_view(), name="instruction-detail"),
]


