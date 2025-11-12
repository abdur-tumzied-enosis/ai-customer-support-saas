from django.urls import path
from .views import DocumentUploadView, DocumentListView, DocumentDeleteView

urlpatterns = [
	path("documents/upload/", DocumentUploadView.as_view(), name="document-upload"),
	path("documents/", DocumentListView.as_view(), name="document-list"),
	path("documents/<uuid:document_id>/", DocumentDeleteView.as_view(), name="document-delete"),
]


