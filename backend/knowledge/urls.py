from django.urls import path
from .suggestions import SuggestionDecisionView, SuggestionListView
from .views import (
	KnowledgeBaseListView,
	DocumentUploadView, DocumentListView, DocumentDeleteView,
	WebCrawlSubmitView, DocumentMarkdownView, DocumentChildrenView,
	InstructionTemplateListView, InstructionTemplateUseView,
	CustomInstructionListCreateView, CustomInstructionDetailView, CustomInstructionResolveView
)

urlpatterns = [
	# Knowledge base endpoints
	path("knowledge-bases/", KnowledgeBaseListView.as_view(), name="knowledge-base-list"),
	
	# Document endpoints
	path("documents/upload/", DocumentUploadView.as_view(), name="document-upload"),
	path("documents/", DocumentListView.as_view(), name="document-list"),
	path("documents/<uuid:document_id>/", DocumentDeleteView.as_view(), name="document-delete"),
	path("documents/<uuid:document_id>/markdown/", DocumentMarkdownView.as_view(), name="document-markdown"),
	path("documents/<uuid:parent_id>/children/", DocumentChildrenView.as_view(), name="document-children"),
	
	# Web crawl endpoints
	path("crawl/", WebCrawlSubmitView.as_view(), name="web-crawl-submit"),
	
	# Instruction template endpoints
	path("instruction-templates/", InstructionTemplateListView.as_view(), name="instruction-template-list"),
	path("instruction-templates/<uuid:template_id>/use/", InstructionTemplateUseView.as_view(), name="instruction-template-use"),
	
	# Custom instruction endpoints
	path("instructions/", CustomInstructionListCreateView.as_view(), name="instruction-list-create"),
	path("instructions/resolve/", CustomInstructionResolveView.as_view(), name="instruction-resolve"),
	path("instructions/<uuid:instruction_id>/", CustomInstructionDetailView.as_view(), name="instruction-detail"),

	# Agent memory review queue
	path("suggestions/", SuggestionListView.as_view(), name="suggestion-list"),
	path("suggestions/<uuid:suggestion_id>/<str:decision>/", SuggestionDecisionView.as_view(), name="suggestion-decision"),
]


